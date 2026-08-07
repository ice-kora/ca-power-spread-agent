# -*- coding: utf-8 -*-
"""
建模：双模型组 —— ZP26 主模型（SNLNDRO/CONTROLX）+ ELCAJNGT 专用模型（冷启动）。

主模型（决策核心）：CatBoost 方向分类器，目标 y = (spread_next > 0)，输出 P(价差>0)。
ELCAJNGT 专用模型：单独训练（其数据仅 2026-03 起，原 train 窗口内无样本，冷启动）。
决策规则（单边，与 evaluate.py / app.py 一致）：
  P(spread>0) > 0.5 且 近7日价差波动 std7 <= 120 -> "sell"（日前卖、实时买），否则 "hold"
说明：方向与幅度是两维；单边规避负价差方向的不对称亏损。分位数回归仅用于曲线展示。

输入：code/data/features.parquet
输出：
  code/models/  catboost_spread_clf.pkl（主） catboost_spread_elca.pkl（ELCA）
                lgbm_spread_q0.1/0.5/0.9.pkl, lgbm_da_q0.5.pkl, lgbm_rtpd_q0.5.pkl（主展示）
                elca_*.pkl（ELCA 展示）
  code/models/feature_cols.json
  code/data/test_predictions.csv
"""
import os
import json
import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from catboost import CatBoostClassifier

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "code", "data")
MODELS = os.path.join(ROOT, "code", "models")
FEATURES_PATH = os.path.join(DATA, "features.parquet")
PRED_PATH = os.path.join(DATA, "test_predictions.csv")

MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA_NODE = "ELCAJNGT_7_N001"

FEATURES = [
    "da_lag1", "rtpd_lag1", "spread_lag1",
    "da_lag2", "rtpd_lag2", "spread_lag2",
    "da_lag7", "rtpd_lag7", "spread_lag7",
    "spread_mean7", "spread_std7",
    "spread_mean14", "spread_std14", "spread_mean30", "spread_std30",
    "load_actual_lag1", "load_actual_day_mean_lag1",
    "peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1",
    "spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
    "load_2da_next", "t2m_next", "ssrd_next", "wind100_next",
    "dow_next", "month_next", "is_holiday_next",
    "solar_flag", "load_peak_flag", "hour", "node",
]
LGB_PARAMS = dict(learning_rate=0.05, n_estimators=800, num_leaves=31,
                  colsample_bytree=0.8, subsample=0.8, min_child_samples=20,
                  reg_alpha=0.1, verbose=-1)
DECISION_TH = dict(prob_th=0.5, std_th=120.0)


def decision_from_prob(prob, std7, prob_th=DECISION_TH["prob_th"], std_th=DECISION_TH["std_th"]):
    """单边策略：P>prob_th 且 std7<=std_th -> sell，否则 hold。direction 仅 +1/-1 信息用。"""
    decision = np.where((prob > prob_th) & (std7 <= std_th), "sell", "hold")
    direction = np.where(prob > prob_th, 1, -1)
    return decision, direction


def _fit_eval(tr, va, y, feats):
    n_es = max(30, int(len(tr) * 0.15))
    if va is not None and len(va):
        return (tr[feats], y), (va[feats], (va["spread_next"] > 0).astype(int))
    return (tr.iloc[:-n_es][feats], y.iloc[:-n_es]), (tr.iloc[-n_es:][feats], y.iloc[-n_es:])


def train_clf(df_sub, feats, name, has_val):
    """训练方向分类器（CatBoost）。has_val 用 val 早停，否则用 train 尾部。"""
    tr = df_sub[df_sub.split == "train"]
    va = df_sub[df_sub.split == "val"] if has_val else None
    y = (tr["spread_next"] > 0).astype(int)
    (Xtr, ytr), (Xva, yva) = _fit_eval(tr, va, y, feats)
    model = CatBoostClassifier(iterations=800, learning_rate=0.05, depth=6,
                               l2_leaf_reg=3, loss_function="Logloss",
                               random_seed=42, verbose=0)
    model.fit(Xtr, ytr, eval_set=(Xva, yva), early_stopping_rounds=80)
    path = os.path.join(MODELS, f"{name}.pkl")
    joblib.dump(model, path)
    return model, path


def train_regs(df_sub, feats, prefix):
    """训练展示用回归（spread q10/50/90 + da/rtpd q50），返回 (models dict, paths dict)。"""
    tr = df_sub[df_sub.split == "train"]
    regs, saved = {}, {}
    for q in (0.1, 0.5, 0.9):
        name = f"{prefix}_spread_q{q:g}"
        m = lgb.LGBMRegressor(**{**LGB_PARAMS, "objective": "quantile", "alpha": q})
        m.fit(tr[feats], tr["spread_next"])
        p = os.path.join(MODELS, name + ".pkl")
        joblib.dump(m, p)
        regs[f"spread_q{q:g}"] = m
        saved[f"spread_q{q:g}"] = p
    for col, key in [("da_price_next", "da_q50"), ("rtpd_price_next", "rtpd_q50")]:
        name = f"{prefix}_{key}"
        m = lgb.LGBMRegressor(**{**LGB_PARAMS, "objective": "quantile", "alpha": 0.5})
        m.fit(tr[feats], tr[col])
        p = os.path.join(MODELS, name + ".pkl")
        joblib.dump(m, p)
        regs[key] = m
        saved[key] = p
    return regs, saved


def predict_group(clf, regs, te, feats):
    """对测试集预测，返回契约 DataFrame 片段。"""
    X = te[feats]
    p = clf.predict_proba(X)[:, 1]
    std7 = te["spread_std7"].fillna(99).values
    decision, direction = decision_from_prob(p, std7)
    out = pd.DataFrame({
        "node": te["node"].values,
        "date": (pd.to_datetime(te["date"]) + pd.Timedelta(days=1)).dt.date,
        "hour": te["hour"].values,
        "prob_sell": p,
        "spread_std7": std7,
        "spread_q10": regs["spread_q0.1"].predict(X),
        "spread_q50": regs["spread_q0.5"].predict(X),
        "spread_q90": regs["spread_q0.9"].predict(X),
        "spread_actual": te["spread_next"].values,
        "da_pred": regs["da_q50"].predict(X),
        "rtpd_pred": regs["rtpd_q50"].predict(X),
        "da_actual": te["da_price_next"].values,
        "rtpd_actual": te["rtpd_price_next"].values,
        "direction_pred": direction,
        "decision_pred": decision,
    })
    return out


def main():
    os.makedirs(MODELS, exist_ok=True)
    df = pd.read_parquet(FEATURES_PATH)

    # ---- 主模型组（ZP26 两节点）----
    dmain = df[df["node"].isin(MAIN_NODES)].copy()
    nc, nl = pd.factorize(dmain["node"])
    dmain["node"] = nc
    feats_main = FEATURES
    clf_main, p_clf_main = train_clf(dmain, feats_main, "catboost_spread_clf", has_val=True)
    regs_main, saved_main = train_regs(dmain, feats_main, "lgbm")
    saved_main["catboost_spread_clf"] = p_clf_main
    te_main = dmain[dmain.split == "test"]

    # ---- ELCAJNGT 专用模型（冷启动：数据 2026-03 起，无 val，train 尾部早停）----
    delca = df[df["node"] == ELCA_NODE].copy()
    delca["node"] = 0  # 单节点常量
    clf_elca, p_clf_elca = train_clf(delca, feats_main, "catboost_spread_elca", has_val=False)
    regs_elca, saved_elca = train_regs(delca, feats_main, "elca")
    saved_elca["catboost_spread_elca"] = p_clf_elca
    te_el = delca[delca.split == "test"]

    print("主模型 best_iter=%s | ELCA best_iter=%s" % (clf_main.get_best_iteration(), clf_elca.get_best_iteration()))

    # ---- 合并测试预测 ----
    out_main = predict_group(clf_main, regs_main, te_main, feats_main)
    out_el = predict_group(clf_elca, regs_elca, te_el, feats_main)
    # node 映射回原始名
    name_map = {i: str(n) for i, n in enumerate(nl)}
    out_main["node"] = out_main["node"].map(name_map)
    out_el["node"] = ELCA_NODE
    out = pd.concat([out_main, out_el], ignore_index=True)
    out.to_csv(PRED_PATH, index=False)
    print("saved", PRED_PATH, "rows:", len(out))

    with open(os.path.join(MODELS, "feature_cols.json"), "w", encoding="utf-8") as f:
        json.dump({
            "features": FEATURES,
            "node_categories": [str(x) for x in nl],
            "models": {k: os.path.basename(v) for k, v in saved_main.items()},
            "elca_models": {k: os.path.basename(v) for k, v in saved_elca.items()},
            "decision_thresholds": DECISION_TH,
        }, f, ensure_ascii=False, indent=2)

    # ---- 简版指标 ----
    t = out.dropna(subset=["spread_actual"])
    acc = (np.sign(t.spread_actual) == t.direction_pred).mean()
    trm = t.decision_pred == "sell"
    pnl = np.where(trm, t.direction_pred * t.spread_actual, 0.0).sum()
    print(f"整体方向准确率={acc*100:.1f}% 单边策略收益={pnl:.0f} 决策={t.decision_pred.value_counts().to_dict()}")
    for n, g in t.groupby("node"):
        accn = (np.sign(g.spread_actual) == g.direction_pred).mean()
        pnln = np.where(g.decision_pred == "sell", g.direction_pred * g.spread_actual, 0.0).sum()
        print(f"  {n}: 方向acc={accn*100:.1f}% 收益={pnln:.0f}")


if __name__ == "__main__":
    main()
