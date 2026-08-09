# -*- coding: utf-8 -*-
"""
Agent C · 三层模型构建（canonical dataset，仅建模，回测由 Agent D 负责）
=====================================================================

基于 Agent B 的 canonical.parquet 构建三层模型，输出统一预测 CSV：
  code/data/predictions_rule.csv
  code/data/predictions_interpretable.csv
  code/data/predictions_catboost.csv
并在 code/data/model_notes.json 记录规则/口径/敏感性与基础指标。

预测目标（契约 §7）：Return = DA − RTPD（$ / MWh）。
  - 任务 A：方向分类  pred_direction ∈ {+1(SELL), -1(BUY), 0(观望)}
  - 任务 B：幅度回归  expected_return
  - 输出置信度 confidence ∈ [0,1] 与 P(Return>0)

三层模型：
  Model 0 · Rule Baseline（完全白盒，规则可复现）
  Model 1 · Interpretable Baseline（Logistic + Huber Regression）
  Model 2 · ML Challenger（CatBoost 分类 + LightGBM 回归，仅对照）

口径约定（与契约/feature_schema.json 一致）：
  - split 严格按 canonical 的 decision_date 时间切分（train / val / test），无随机
  - Shared Model + Node Feature（node/zone/hour 作特征），不按节点单独建模
  - ELCA 冷启动节点：与其他节点一起进特征训练，但评估单独报告
  - 只用 38 个 X 特征（全部 <= decision_cutoff 可得），*_next 天气特征禁用
  - 弱信号阈值 th = 2.0 $/MWh（虚拟报价交易成本代理），概率 margin = 0.05
"""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "code" / "data"

CANONICAL = DATA / "canonical.parquet"
OUT_RULE = DATA / "predictions_rule.csv"
OUT_INTERP = DATA / "predictions_interpretable.csv"
OUT_CAT = DATA / "predictions_catboost.csv"
OUT_NOTES = DATA / "model_notes.json"

TH = 2.0            # 弱信号阈值（$ / MWh）
PROB_MARGIN = 0.05  # 分类概率 margin（0.45 / 0.55 为观望带）

# ---- 特征清单（feature_schema.json 的 x_columns，全量 38，均决策时点可得）----
X_FEATURES = [
    "hour", "node", "zone", "dow", "month", "is_holiday", "solar_flag",
    "da_lag1", "rtpd_lag1", "spread_lag1",
    "da_lag2", "rtpd_lag2", "spread_lag2",
    "da_lag7", "rtpd_lag7", "spread_lag7",
    "spread_mean7", "spread_std7", "spread_mean14", "spread_std14",
    "spread_mean30", "spread_std30",
    "spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
    "da_day_mean_lag1", "rtpd_day_mean_lag1", "spread_day_mean_lag1",
    "load_actual_lag1", "load_actual_day_mean_lag1",
    "load_2da_forecast", "load_peak_flag",
    "t2m_lag1", "ssrd_lag1", "wind100_lag1",
    "peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1",
]

ID_COLS = ["node", "target_date", "hour", "split"]
OUT_COLS = [
    "node", "target_date", "hour", "split",
    "pred_direction", "expected_return", "confidence", "prob_return_positive",
    "actual_return", "actual_direction",
]

RNG = 42
np.random.seed(RNG)


def load_canonical():
    df = pd.read_parquet(CANONICAL)
    return df


# ===========================================================================
# 通用：决策 / 置信度
# ===========================================================================
def decision_from_prob_expected(prob, expected, th=TH, margin=PROB_MARGIN):
    """分类概率 + 期望幅度 → 三态 pred_direction。
    +1(SELL) : P(Return>0) >= 0.5+margin 且 expected > +th
    -1(BUY)  : P(Return>0) <= 0.5-margin 且 expected < -th
     0(观望) : 其余
    """
    d = np.zeros(len(prob), dtype=int)
    d[(prob >= 0.5 + margin) & (expected > th)] = 1
    d[(prob <= 0.5 - margin) & (expected < -th)] = -1
    return d


def confidence_from_prob_expected(prob, expected, th=TH):
    """置信度 = 0.5×概率离 0.5 的强度 + 0.5×幅度相对 4×th 的强度。"""
    conf = 0.5 * np.abs(prob - 0.5) / 0.5 + 0.5 * np.minimum(1.0, np.abs(expected) / (4.0 * th))
    return np.clip(conf, 0.0, 1.0)


def metrics_table(pred_df, label):
    """test 上的基础指标。pred_df 含 OUT_COLS，split 已过滤为 test。"""
    test = pred_df[pred_df["split"] == "test"].copy()
    test["sign_pred"] = np.sign(test["pred_direction"])
    test["sign_act"] = np.sign(test["actual_return"])
    traded = test[test["sign_pred"] != 0]
    n_all = len(test)
    n_traded = len(traded)
    m = {
        "label": label,
        "n_test": n_all,
        "n_traded": n_traded,
        "coverage": n_traded / n_all if n_all else np.nan,
        "dir_acc_traded": float((traded["sign_pred"] == traded["sign_act"]).mean()) if n_traded else np.nan,
        "dir_acc_all_incl_abstain": float((test["sign_pred"] == test["sign_act"]).mean()),
        "mae": float((test["expected_return"] - test["actual_return"]).abs().mean()),
        "rmse": float((((test["expected_return"] - test["actual_return"]) ** 2).mean()) ** 0.5),
    }
    sell = traded[traded["sign_pred"] == 1]
    buy = traded[traded["sign_pred"] == -1]
    m["n_sell"] = len(sell)
    m["n_buy"] = len(buy)
    m["sell_precision_sign"] = float((sell["actual_return"] > 0).mean()) if len(sell) else np.nan
    m["buy_precision_sign"] = float((buy["actual_return"] < 0).mean()) if len(buy) else np.nan
    m["sell_precision_th"] = float((sell["actual_return"] > TH).mean()) if len(sell) else np.nan
    m["buy_precision_th"] = float((buy["actual_return"] < -TH).mean()) if len(buy) else np.nan
    m["sell_avg_pnl"] = float(sell["actual_return"].mean()) if len(sell) else np.nan
    m["buy_avg_pnl"] = float(-buy["actual_return"].mean()) if len(buy) else np.nan  # BUY 头寸 PnL = -Return
    # 分类器 AUC（prob vs 实际正类）
    ybin = (test["actual_return"] > 0).astype(int)
    try:
        from sklearn.metrics import roc_auc_score
        m["auc"] = float(roc_auc_score(ybin, test["prob_return_positive"]))
    except Exception:
        m["auc"] = np.nan
    return m


def make_output_df(pred, actual):
    """组装统一输出 DataFrame。pred: dict/array 各预测列；actual: 对应行信息。"""
    out = pd.DataFrame({
        "node": actual["node"].values,
        "target_date": actual["target_date"].values,
        "hour": actual["hour"].values,
        "split": actual["split"].values,
        "pred_direction": pred["direction"],
        "expected_return": pred["expected"],
        "confidence": pred["confidence"],
        "prob_return_positive": pred["prob"],
        "actual_return": actual["actual_return"].values,
        "actual_direction": actual["actual_direction"].values,
    })
    return out[OUT_COLS]


def dump_pred(df, path):
    df = df.sort_values(["node", "target_date", "hour"]).reset_index(drop=True)
    df["target_date"] = pd.to_datetime(df["target_date"]).dt.strftime("%Y-%m-%d")
    df["actual_direction"] = df["actual_direction"].astype(int)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


# ===========================================================================
# Model 0 · Rule Baseline（完全白盒）
# ===========================================================================
# 估计型规则（产出 $/MWh 幅度）：
#   R1 同节点同 hour 7d 均值 spread      (spread_mean7)
#   R2 同节点同 hour 昨日 spread          (spread_lag1)
#   R3 同节点同 hour 14d 均值 spread      (spread_mean14)
#   R4 昨日全天均值 spread                (spread_day_mean_lag1)
#   R5 同节点同 hour 30d 均值 spread      (spread_mean30)
#   R6 节点×hour×dow×month 历史 bias      (train 均值，分层回退)
# 证据型规则（方向证据 ±1/0，不产幅度）：
#   R7 负荷预测偏差  sign(load_2da_forecast - load_actual_day_mean_lag1)，|dev|>2% 才激活
#   R8 关联节点 spillover  sign(peer_spread_lag1)（ZP26 有 peer；ELCA 无 peer→0）
#   R9 价差动量  sign(spread_lag1 - spread_mean7)，|diff|>th 才激活
EST_RULES = {
    "R1_mean7":   {"col": "spread_mean7",       "w": 0.30},
    "R2_lag1":    {"col": "spread_lag1",        "w": 0.24},
    "R3_mean14":  {"col": "spread_mean14",      "w": 0.15},
    "R4_daymean": {"col": "spread_day_mean_lag1", "w": 0.10},
    "R5_mean30":  {"col": "spread_mean30",      "w": 0.11},
    "R6_bias":    {"col": "node_bias",          "w": 0.10},
}
EVIDENCE_RULES = ["R7_load", "R8_peer", "R9_momentum"]


def _hier_bias_map(tr):
    """训练集上按 (node,hour,dow,month)->(node,hour,dow)->(node,hour)->(node)->0 分层均值。"""
    levels = [
        ["node", "hour", "dow", "month"],
        ["node", "hour", "dow"],
        ["node", "hour"],
        ["node"],
    ]
    maps = []
    for keys in levels:
        g = tr.groupby(keys, observed=True)["actual_return"].mean()
        maps.append(g)
    return maps


def _lookup_bias(maps, row):
    for g in maps:
        idx = tuple(row[k] for k in g.index.names)
        if idx in g.index:
            return float(g.loc[idx])
    return 0.0


def rule_predict_v2(df, bias_maps):
    n = len(df)
    direction = np.zeros(n, dtype=int)
    expected = np.zeros(n)
    prob = np.zeros(n)
    conf = np.zeros(n)
    rule_hits = [0] * len(EST_RULES)

    for i, (_, r) in enumerate(df.iterrows()):
        est_vals, est_w, hit_ids = [], [], []
        for j, (rid, spec) in enumerate(EST_RULES.items()):
            if rid == "R6_bias":
                v = _lookup_bias(bias_maps, r)
            else:
                v = r[spec["col"]]
            if np.isfinite(v):
                est_vals.append(v)
                est_w.append(spec["w"])
                hit_ids.append(j)
        if est_vals:
            w = np.array(est_w) / np.sum(est_w)
            est = float(np.sum(np.array(est_vals) * w))
            d_main = float(np.sum(np.sign(np.array(est_vals)) * w))
        else:
            est, d_main = 0.0, 0.0
        for j in hit_ids:
            rule_hits[j] += 1

        ev = []
        fc = r["load_2da_forecast"]
        act_l = r["load_actual_day_mean_lag1"]
        if np.isfinite(fc) and np.isfinite(act_l) and abs(act_l) > 1:
            dev = fc - act_l
            if abs(dev) > 0.02 * abs(act_l):
                ev.append(np.sign(dev))
        ps = r["peer_spread_lag1"]
        if np.isfinite(ps):
            ev.append(np.sign(ps))
        l1 = r["spread_lag1"]
        m7 = r["spread_mean7"]
        if np.isfinite(l1) and np.isfinite(m7):
            diff = l1 - m7
            if abs(diff) > TH:
                ev.append(np.sign(diff))

        if est > 0:
            e_sign = [1 if e == 1 else -1 if e == -1 else 0 for e in ev]
        elif est < 0:
            e_sign = [-1 if e == 1 else 1 if e == -1 else 0 for e in ev]
        else:
            e_sign = [0] * len(ev)
        nz = [s for s in e_sign if s != 0]
        e_mean = float(np.mean(nz)) if nz else 0.0
        e_cnt = len(nz)

        d = 0
        if abs(est) >= TH and d_main >= 0.5:
            d = int(np.sign(est))
            if e_cnt >= 2 and e_mean <= -0.5:
                d = 0
        direction[i] = d
        expected[i] = est

        mag_c = min(1.0, abs(est) / (3.0 * TH))
        main_c = max(0.0, d_main)
        ev_c = 0.5 + 0.5 * e_mean
        c = float(np.clip(0.35 * mag_c + 0.30 * main_c + 0.35 * ev_c, 0.0, 1.0))
        if d == 0:
            c = c * 0.5
        conf[i] = c
        pseudo = float(np.clip(0.5 + 0.5 * (0.7 * d_main + 0.3 * e_mean), 0.0, 1.0))
        prob[i] = pseudo
    return direction, expected, prob, conf, rule_hits


# ===========================================================================
# Model 1 · Interpretable Baseline
# ===========================================================================
NODE_LIST = ["CONTROLX_1_N001", "ELCAJNGT_7_N001", "SNLNDRO_1_N001"]


def build_matrix(df):
    """构造解释型模型数值特征矩阵。
    - zone 与 node 完全共线（CONTROLX/SNLNDRO→ZP26，ELCA→SP15），剔除；
    - node 做 3 哑变量，保证任意子集（val 无 ELCA）列一致。
    """
    m = df[X_FEATURES].copy()
    m = m.drop(columns=["zone"])
    m = pd.get_dummies(m, columns=["node"], prefix="node")
    for n in NODE_LIST:
        col = f"node_{n}"
        if col not in m.columns:
            m[col] = 0
    return m


def fit_interpretable(Xtr, Xva, Xte, y_clf_tr, y_reg_tr, tr, va, te):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, HuberRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score

    y_clf_va = (va["actual_return"] > 0).astype(int)
    y_clf_te = (te["actual_return"] > 0).astype(int)

    # ---- 方向分类：Logistic Regression（C 由 val AUC 选择）----
    best_c, best_auc = 1.0, -1
    for C in [0.1, 0.5, 1.0, 2.0, 5.0]:
        p = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(C=C, max_iter=3000, random_state=RNG))
        p.fit(Xtr, y_clf_tr)
        try:
            a = roc_auc_score(y_clf_va, p.predict_proba(Xva)[:, 1])
        except Exception:
            a = -1
        if a > best_auc:
            best_auc, best_c = a, C
    pipe_clf = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             LogisticRegression(C=best_c, max_iter=3000, random_state=RNG))
    pipe_clf.fit(Xtr, y_clf_tr)

    # ---- 幅度回归：Huber 鲁棒线性回归（Return 重尾，OLS 会被极端值主导）----
    pipe_reg = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             HuberRegressor(epsilon=1.35, alpha=1e-4, max_iter=300))
    pipe_reg.fit(Xtr, y_reg_tr)

    # ---- 系数提取（标准缩放空间）----
    cols = Xtr.columns
    lr = pipe_clf.named_steps["logisticregression"]
    coef_clf = pd.Series(lr.coef_[0], index=cols)
    hr = pipe_reg.named_steps["huberregressor"]
    coef_reg = pd.Series(hr.coef_, index=cols)

    clf_info = {"type": "LogisticRegression", "C": best_c, "val_auc": best_auc}
    reg_info = {"type": "HuberRegressor(epsilon=1.35)", "note": "鲁棒线性回归，抗 Return 重尾"}

    prob_te = pipe_clf.predict_proba(Xte)[:, 1]
    exp_te = pipe_reg.predict(Xte)
    auc_te = roc_auc_score(y_clf_te, prob_te)

    return (pipe_clf, pipe_reg), (coef_clf, coef_reg), (clf_info, reg_info), (prob_te, exp_te, auc_te)


# ===========================================================================
# Model 2 · ML Challenger (CatBoost 分类 + LightGBM 回归)
# ===========================================================================
def fit_catboost_lgbm(tr, va, y_clf_tr, y_reg_tr):
    import catboost as cb
    import lightgbm as lgb

    cat_cols = ["node", "zone", "dow", "month"]
    feat_cols = [c for c in X_FEATURES if c not in ("hour",)]

    # ---- CatBoost 分类 ----
    cb_tr = tr[feat_cols].copy()
    cb_va = va[feat_cols].copy()
    for c in cat_cols:
        cb_tr[c] = cb_tr[c].astype(str)
        cb_va[c] = cb_va[c].astype(str)
    cb_clf = cb.CatBoostClassifier(
        iterations=1200, learning_rate=0.05, depth=6, l2_leaf_reg=3.0,
        loss_function="Logloss", random_seed=RNG, verbose=0, od_type="Iter",
        eval_metric="AUC", cat_features=cat_cols,
    )
    cb_clf.fit(cb_tr, y_clf_tr, eval_set=(cb_va, (va["actual_return"] > 0).astype(int)),
               early_stopping_rounds=120, use_best_model=True)

    # ---- LightGBM 回归（quantile 0.5，抗重尾）----
    lgb_tr = tr[feat_cols].copy()
    lgb_va = va[feat_cols].copy()
    for c in cat_cols:
        lgb_tr[c] = lgb_tr[c].astype("category")
        lgb_va[c] = lgb_va[c].astype("category")
    lgb_reg = lgb.LGBMRegressor(
        objective="quantile", alpha=0.5, learning_rate=0.05, n_estimators=1500,
        num_leaves=31, min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, random_state=RNG, verbose=-1,
    )
    lgb_reg.fit(lgb_tr, y_reg_tr, eval_set=[(lgb_va, va["actual_return"])],
                callbacks=[lgb.early_stopping(150, verbose=False)])

    # 特征重要性
    cb_imp = pd.Series(cb_clf.get_feature_importance(), index=feat_cols).sort_values(ascending=False)
    lgb_imp = pd.Series(lgb_reg.feature_importances_, index=feat_cols).sort_values(ascending=False)

    return (cb_clf, lgb_reg), (cb_imp, lgb_imp)


# ===========================================================================
# 主流程
# ===========================================================================
def main():
    df = load_canonical()
    labeled = df[df["split"].notna()].copy()

    tr = labeled[labeled["split"] == "train"]
    va = labeled[labeled["split"] == "val"]
    te = labeled[labeled["split"] == "test"].copy()
    te["actual_direction"] = te["direction"].values   # canonical 列名 direction -> 输出列 actual_direction
    print(f"[data] train={len(tr)} val={len(va)} test={len(te)} rows")
    print(f"[data] train nodes: {tr['node'].unique().tolist()}")
    print(f"[data] test nodes: {te['node'].unique().tolist()}")

    y_clf_tr = (tr["actual_return"] > 0).astype(int)
    y_reg_tr = tr["actual_return"]

    results = {}
    models_meta = {}

    # ---------------- Model 0 · Rule Baseline ----------------
    print("\n== Model 0 · Rule Baseline ==")
    bias_maps = _hier_bias_map(tr)
    pred_rows = te.copy()  # 输出只含 test（契约接口口径）
    d, e, p, c, rule_hits = rule_predict_v2(pred_rows, bias_maps)
    pred_rows["pred_direction"] = d
    pred_rows["expected_return"] = e
    pred_rows["prob_return_positive"] = p
    pred_rows["confidence"] = c
    rule_out = make_output_df(
        {"direction": d, "expected": e, "confidence": c, "prob": p},
        pred_rows[["node", "target_date", "hour", "split", "actual_return", "actual_direction"]],
    )
    dump_pred(rule_out, OUT_RULE)
    m = metrics_table(rule_out, "rule")
    results["rule"] = m
    models_meta["rule"] = {
        "type": "Rule Baseline（完全白盒）",
        "rule_hit_counts": {list(EST_RULES)[j]: int(h) for j, h in enumerate(rule_hits)},
    }
    print(json.dumps(m, indent=2, ensure_ascii=False))

    # ---------------- Model 1 · Interpretable ----------------
    print("\n== Model 1 · Interpretable Baseline ==")
    X_all = build_matrix(labeled)
    Xtr = X_all.loc[tr.index].copy()
    Xva = X_all.loc[va.index].copy()
    Xte = X_all.loc[te.index].copy()
    (clf1, reg1), (coef_clf, coef_reg), (clf_info, reg_info), (prob1, exp1, auc_te1) = \
        fit_interpretable(Xtr, Xva, Xte, y_clf_tr, y_reg_tr, tr, va, te)
    dir1 = decision_from_prob_expected(prob1, exp1)
    conf1 = confidence_from_prob_expected(prob1, exp1)
    interp_out = make_output_df(
        {"direction": dir1, "expected": exp1, "confidence": conf1, "prob": prob1},
        te[["node", "target_date", "hour", "split", "actual_return", "actual_direction"]],
    )
    dump_pred(interp_out, OUT_INTERP)
    m = metrics_table(interp_out, "interpretable")
    results["interpretable"] = m
    models_meta["interpretable"] = {
        "type": "LogisticRegression + HuberRegressor",
        "clf": clf_info, "reg": reg_info,
        "top_coef_clf": coef_clf.reindex(coef_clf.abs().sort_values(ascending=False).index).head(15).round(4).to_dict(),
        "top_coef_reg": coef_reg.reindex(coef_reg.abs().sort_values(ascending=False).index).head(15).round(4).to_dict(),
    }
    print(json.dumps(m, indent=2, ensure_ascii=False))

    # ---------------- Model 2 · CatBoost + LightGBM ----------------
    print("\n== Model 2 · ML Challenger ==")
    (cb_clf, lgb_reg), (cb_imp, lgb_imp) = fit_catboost_lgbm(tr, va, y_clf_tr, y_reg_tr)
    cat_cols = ["node", "zone", "dow", "month"]
    feat_cols = [c for c in X_FEATURES if c not in ("hour",)]
    cb_te = te[feat_cols].copy()
    for c in cat_cols:
        cb_te[c] = cb_te[c].astype(str)
    lgb_te = te[feat_cols].copy()
    for c in cat_cols:
        lgb_te[c] = lgb_te[c].astype("category")
    prob2 = cb_clf.predict_proba(cb_te)[:, 1]
    exp2 = lgb_reg.predict(lgb_te)
    dir2 = decision_from_prob_expected(prob2, exp2)
    conf2 = confidence_from_prob_expected(prob2, exp2)
    cat_out = make_output_df(
        {"direction": dir2, "expected": exp2, "confidence": conf2, "prob": prob2},
        te[["node", "target_date", "hour", "split", "actual_return", "actual_direction"]],
    )
    dump_pred(cat_out, OUT_CAT)
    m = metrics_table(cat_out, "catboost")
    results["catboost"] = m
    models_meta["catboost"] = {
        "type": "CatBoostClassifier + LightGBMRegressor(quantile=0.5)",
        "clf": {"algo": "CatBoostClassifier", "loss": "Logloss",
                "iterations": cb_clf.get_best_iteration() if cb_clf.get_best_iteration() else "use_best"},
        "reg": {"algo": "LGBMRegressor", "objective": "quantile", "alpha": 0.5,
                "best_iter": lgb_reg.best_iteration_},
        "top_importance_clf": cb_imp.head(15).round(2).to_dict(),
        "top_importance_reg": lgb_imp.head(15).round(2).to_dict(),
    }
    print(json.dumps(m, indent=2, ensure_ascii=False))

    # ---------------- ELCA 单独评估 ----------------
    print("\n== ELCA cold-start 单独评估（test）==")
    elca_metrics = {}
    for name, path in [("rule", OUT_RULE), ("interpretable", OUT_INTERP), ("catboost", OUT_CAT)]:
        p = pd.read_csv(path)
        p["target_date"] = pd.to_datetime(p["target_date"])
        pe = p[(p["split"] == "test") & (p["node"] == "ELCAJNGT_7_N001")]
        if len(pe) == 0:
            continue
        mm = metrics_table(pe, name)
        elca_metrics[name] = mm
        print(f"  {name}: n={mm['n_test']} cov={mm['coverage']:.3f} acc_traded={mm['dir_acc_traded']:.4f} "
              f"sell_p={mm['sell_precision_sign']:.3f} buy_p={mm['buy_precision_sign']:.3f} "
              f"mae={mm['mae']:.2f} rmse={mm['rmse']:.2f} auc={mm['auc']:.3f}")
    results["elca_test"] = elca_metrics

    # ---------------- 每节点指标（test）----------------
    print("\n== 每节点基础指标（test）==")
    per_node = {}
    for name, path in [("rule", OUT_RULE), ("interpretable", OUT_INTERP), ("catboost", OUT_CAT)]:
        p = pd.read_csv(path)
        p["target_date"] = pd.to_datetime(p["target_date"])
        pn = {}
        for node, g in p[p["split"] == "test"].groupby("node"):
            pn[node] = metrics_table(g, name)
        per_node[name] = pn
        for node, mm in pn.items():
            print(f"  {name:14s} {node:18s} cov={mm['coverage']:.3f} acc_t={mm['dir_acc_traded']:.3f} "
                  f"sell_p={mm['sell_precision_sign']:.3f} buy_p={mm['buy_precision_sign']:.3f} "
                  f"sell_pnl={mm['sell_avg_pnl']:.1f} buy_pnl={mm['buy_avg_pnl']:.1f} mae={mm['mae']:.1f}")
    results["test_by_node"] = per_node

    # ---------------- 保存 model_notes.json ----------------
    notes = {
        "feature_version": "canonical_v1",
        "generated_by": "model_c.py",
        "generated_at": pd.Timestamp.now().isoformat(),
        "contract_ref": "docs/business_contract.md §6/§7",
        "thresholds": {
            "th": TH, "th_unit": "$/MWh", "th_rationale": "CAISO 虚拟报价交易成本代理，|Return|<th 视为弱信号",
            "prob_margin": PROB_MARGIN, "prob_weak_band": [0.5 - PROB_MARGIN, 0.5 + PROB_MARGIN],
            "confidence_formula": "ML: 0.5*|p-0.5|/0.5 + 0.5*min(1,|exp|/(4*th))；Rule: 幅度/主规则一致/证据一致加权",
        },
        "prediction_definition": {
            "pred_direction": "+1=SELL(预测Return>+th 且 P>=0.55) / -1=BUY(预测Return<-th 且 P<=0.45) / 0=观望",
            "expected_return": "预测 Return 幅度（$/MWh）。Rule=估计型规则加权均值；Interp=Huber 鲁棒线性回归；Catboost=LightGBM quantile(0.5)",
            "prob_return_positive": "P(Return>0)：Logistic / CatBoost；Rule=由 D_main 与证据映射的伪概率",
            "confidence": "0-1 置信度，用于 Agent D 风险过滤",
            "only_split": "test（决策日 2026-06-01..2026-08-04）",
        },
        "shared_model": {
            "approach": "Shared Model + Node Feature，不按节点单独建模",
            "nodes": ["CONTROLX_1_N001", "ELCAJNGT_7_N001", "SNLNDRO_1_N001"],
            "elca": "cold-start（数据 2026-03-03 起）：与其他节点共同进 train 特征训练，val 无 ELCA 样本，test 单独评估见 results.elca_test",
        },
        "rule_baseline": {
            "description": "完全白盒规则模型，全部阈值可解释可复现",
            "estimation_rules": {
                "R1_mean7": "同节点同 hour 近7日 spread 均值，权重0.30",
                "R2_lag1": "同节点同 hour 昨日(t-2) spread，权重0.24",
                "R3_mean14": "近14日均值，权重0.15",
                "R4_daymean": "昨日全天 spread 均值，权重0.10",
                "R5_mean30": "近30日均值，权重0.11",
                "R6_bias": "train 上 (node,hour,dow,month) 分层历史 bias（缺失回退到更粗层），权重0.10",
            },
            "evidence_rules": {
                "R7_load": "负荷预测偏差：sign(load_2da_forecast - load_actual_day_mean_lag1)，|dev|>2%·实际负荷才激活",
                "R8_peer": "关联节点 spillover：sign(peer_spread_lag1)，ELCA 无 peer→0",
                "R9_momentum": "价差动量：sign(spread_lag1 - spread_mean7)，|diff|>th 才激活",
            },
            "decision_rule": "|expected_return|>=th 且 D_main>=0.5（估计权重过半数同号）→ 方向=sign(est)；≥2 个非零证据且过半数相反 → 观望；|est|<th → 观望",
        },
        "interpretable_baseline": {
            "clf": "LogisticRegression（C 由 val AUC 选择），输入=38 X（node 哑变量，zone 因与 node 完全共线剔除）",
            "reg": "HuberRegressor(epsilon=1.35)（鲁棒线性回归，抗 Return 重尾；契约允许 Linear/Quantile，重尾下 OLS 被极端值主导故选鲁棒）",
            "preprocessing": "SimpleImputer(median) 与 StandardScaler 仅 fit 在 train",
            "coefficients": models_meta["interpretable"]["top_coef_clf"],
            "regression_coefficients": models_meta["interpretable"]["top_coef_reg"],
        },
        "ml_challenger": {
            "clf": "CatBoostClassifier(Logloss, AUC early-stop on val)",
            "reg": "LightGBMRegressor(objective=quantile alpha=0.5, early-stop on val)",
            "categorical_features": ["node", "zone", "dow", "month"],
            "note": "仅作效果对照，不是最终 Agent",
            "clf_importance": models_meta["catboost"]["top_importance_clf"],
            "reg_importance": models_meta["catboost"]["top_importance_reg"],
        },
        "sensitivity_notes": [
            "load_2da_forecast / load_peak_flag 为 ASSUMED_AVAILABLE（2DA 日前负荷预测，schema 标注）。canonical 内 train/val/test 该列无缺失，但原始 load_CA_ISO_TAC_2DA.csv 仅到 2026-07-09，8 月 test 行取值来自 master.csv（已并入更新源）；若真实部署时 2DA 预测不可得或口径不同，需替换该特征并重训/重测。",
            "weather 滞后特征（t2m_lag1/ssrd_lag1/wind100_lag1）在 2026-07-01..07-29 部分日缺失（三节点共 216 test 行），树模型天然处理 NaN，解释模型用 train 中位数插补。",
            "peer_* 特征对 ELCA 恒为 NaN（SP15 无同区关联节点）；解释模型以中位数插补，树模型按缺失处理。",
            "Return 重尾（test max 2251 $/MWh）：回归用中位数目标（Quantile/quantile）降低极端值主导；MAE/RMSE 受极端值影响大，方向指标更能反映模型价值。",
            "时区：weather valid_pt 为 naive 小时戳，未做 America/Los_Angeles 换算；历史滞后特征不受泄漏影响但存在小时对齐不确定性。",
        ],
        "results_on_test": results,
        "output_files": {
            "rule": str(OUT_RULE),
            "interpretable": str(OUT_INTERP),
            "catboost": str(OUT_CAT),
        },
    }
    OUT_NOTES.write_text(json.dumps(notes, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[notes] written -> {OUT_NOTES}")

    # 汇总打印
    print("\n==== 汇总（test）====")
    for k in ["rule", "interpretable", "catboost"]:
        r = results[k]
        print(f"{k:14s} cov={r['coverage']:.3f} acc_traded={r['dir_acc_traded']:.4f} "
              f"acc_all={r['dir_acc_all_incl_abstain']:.4f} sell_p={r['sell_precision_sign']:.3f} "
              f"buy_p={r['buy_precision_sign']:.3f} mae={r['mae']:.2f} rmse={r['rmse']:.2f} auc={r['auc']:.3f}")


if __name__ == "__main__":
    main()
