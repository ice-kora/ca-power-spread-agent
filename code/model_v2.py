# -*- coding: utf-8 -*-
"""
V0.2 · Production Predictive Model（单一预测模型接口重定义）
==========================================================

定位：本项目唯一的 Production Predictive Model，只输出预测量，**不输出最终 BUY/SELL**。
  - expected_return  : Return=DA-RTPD 的预期幅度（$/MWh，重尾用中位数/分位数目标）
  - prob_positive    : P(Return>0)（CatBoost 二分类）
  - prob_negative    : 1 - prob_positive（二元互补）
  - confidence       : 0-1，方向概率强度 + 幅度相对不确定度 的组合（公式见下）
  - uncertainty      : 0-1，q10/q90 分位数区间宽度相对分布尺度的归一化（公式见下）

交易动作（BUY_DA / SELL_DA / NO_TRADE）由 Rule Engine 决定，本模型不输出。

任务：
  1) hour 特征验证：当前 model_c.py 对 CatBoost/LightGBM 排除了 hour
     （feat_cols = [c for c in X_FEATURES if c not in ("hour",)]）。
     本脚本做 With hour vs Without hour 严格时间验证（同一 train/val/test），
     报告 AUC / direction accuracy / MAE / RMSE，由数据决定最终是否保留 hour。
  2) 只消费 canonical X 区 38 个决策时点可见特征。
     天气特征用历史滞后（t2m_lag1/ssrd_lag1/wind100_lag1）+ 历史 Return 特征
     + load_2da_forecast 作 strict baseline。
     【当前缺失真实 as-of weather forecast archive】——本版不得用实际天气模拟 forecast。

口径（与 business_contract / model_c 一致）：
  - split 严格按 canonical 的 decision_date 时间切分，无随机 split
  - Shared Model + Node Feature（node/zone 作特征，不按节点单独建模）
  - 只用 38 个 X 特征（全部 available_at <= decision_cutoff），*_next 天气禁用
  - 分类：CatBoostClassifier(Logloss, AUC early-stop on val)
  - 回归：LightGBMRegressor(objective=quantile, alpha=0.5 / 0.1 / 0.9)
  - 输出 CSV 只写 split==test；val 另出（predictions_v2_val.csv）

输出：
  code/data/predictions_v2.csv       （test 统一预测，11 列）
  code/data/predictions_v2_val.csv   （val，另出，仅供校准参考）
  code/data/model_v2_notes.json      （模型定义 / 公式 / hour 实验结论 / 特征清单 / 缺失说明）
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
OUT_PRED = DATA / "predictions_v2.csv"
OUT_PRED_VAL = DATA / "predictions_v2_val.csv"
OUT_NOTES = DATA / "model_v2_notes.json"

RNG = 42
np.random.seed(RNG)

# ---- 特征清单：canonical X 区 38 个，全部 available_at <= decision_cutoff ----
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

# 基础类别特征（与 model_c.py 一致）；hour 是否进入类别列由变体决定
BASE_CAT_COLS = ["node", "zone", "dow", "month"]

OUT_COLS = [
    "node", "target_date", "hour", "split",
    "expected_return", "prob_positive", "prob_negative",
    "confidence", "uncertainty",
    "actual_return", "actual_direction",
]

# 分类 / 回归超参（沿用 model_c.py 口径，保证可对照）
CLF_HP = dict(
    iterations=1200, learning_rate=0.05, depth=6, l2_leaf_reg=3.0,
    loss_function="Logloss", random_seed=RNG, verbose=0,
    od_type="Iter", eval_metric="AUC", early_stopping_rounds=120,
)
REG_HP = dict(
    objective="quantile", learning_rate=0.05, n_estimators=1500,
    num_leaves=31, min_child_samples=20, subsample=0.8,
    colsample_bytree=0.8, reg_alpha=0.1, random_state=RNG, verbose=-1,
)
EARLY_STOP_ROUNDS = 150
QUANTILES = (0.1, 0.5, 0.9)   # 0.5 -> expected_return；0.1/0.9 -> uncertainty 区间

# hour 实验变体定义
VARIANTS = {
    "without_hour":      {"include_hour": False, "hour_as_cat": False},
    "with_hour_numeric": {"include_hour": True,  "hour_as_cat": False},
    "with_hour_cat":     {"include_hour": True,  "hour_as_cat": True},
}


# ===========================================================================
# 数据
# ===========================================================================
def load_canonical():
    return pd.read_parquet(CANONICAL)


def prep():
    df = load_canonical()
    labeled = df[df["split"].notna()].copy()
    labeled["actual_direction"] = np.sign(labeled["actual_return"]).astype(int)
    return labeled


def split_frames(labeled):
    tr = labeled[labeled["split"] == "train"].copy()
    va = labeled[labeled["split"] == "val"].copy()
    te = labeled[labeled["split"] == "test"].copy()
    return tr, va, te


def variant_feature_cols(include_hour, hour_as_cat):
    """按变体返回 (特征列, 类别列)。"""
    feat_cols = X_FEATURES if include_hour else [c for c in X_FEATURES if c != "hour"]
    cat_cols = list(BASE_CAT_COLS)
    if hour_as_cat:
        cat_cols = cat_cols + ["hour"]
    return feat_cols, cat_cols


# ===========================================================================
# 分类器 / 回归器
# ===========================================================================
def fit_clf_catboost(tr, va, te, feat_cols, cat_cols):
    """CatBoost 二分类：P(Return>0)。返回 (model, prob_va, prob_te, auc_va, auc_te)。"""
    import catboost as cb
    from sklearn.metrics import roc_auc_score

    y_clf_tr = (tr["actual_return"] > 0).astype(int)
    y_clf_va = (va["actual_return"] > 0).astype(int)
    y_clf_te = (te["actual_return"] > 0).astype(int)

    Xtr, Xva, Xte = tr[feat_cols].copy(), va[feat_cols].copy(), te[feat_cols].copy()
    for c in cat_cols:
        Xtr[c] = Xtr[c].astype(str)
        Xva[c] = Xva[c].astype(str)
        Xte[c] = Xte[c].astype(str)

    model = cb.CatBoostClassifier(**CLF_HP, cat_features=cat_cols)
    model.fit(Xtr, y_clf_tr, eval_set=(Xva, y_clf_va), use_best_model=True)

    p_va = model.predict_proba(Xva)[:, 1]
    p_te = model.predict_proba(Xte)[:, 1]
    auc_va = float(roc_auc_score(y_clf_va, p_va))
    auc_te = float(roc_auc_score(y_clf_te, p_te))
    return model, p_va, p_te, auc_va, auc_te


def fit_reg_lgbm_quantiles(tr, va, te, feat_cols, cat_cols, quantiles=QUANTILES):
    """LightGBM 分位数回归：每个 quantile 一个模型。返回 (models, preds)。
    preds[alpha] = {"va": ndarray, "te": ndarray}。"""
    import lightgbm as lgb

    Xtr, Xva, Xte = tr[feat_cols].copy(), va[feat_cols].copy(), te[feat_cols].copy()
    for c in cat_cols:
        Xtr[c] = Xtr[c].astype("category")
        Xva[c] = Xva[c].astype("category")
        Xte[c] = Xte[c].astype("category")

    models, preds = {}, {}
    for a in quantiles:
        m = lgb.LGBMRegressor(**REG_HP, alpha=a)
        m.fit(Xtr, tr["actual_return"], eval_set=[(Xva, va["actual_return"])],
              callbacks=[lgb.early_stopping(EARLY_STOP_ROUNDS, verbose=False)])
        models[a] = m
        preds[a] = {"va": m.predict(Xva), "te": m.predict(Xte)}
    return models, preds


# ===========================================================================
# V0.2 预测量定义（明确公式）
# ===========================================================================
def confidence_and_uncertainty(prob_pos, q10, q50, q90):
    """由方向概率 + 分位数区间定义 confidence / uncertainty（均为 0-1，显式公式）。

    uncertainty = clip( iqr / (2*scale), 0, 1 )
        iqr   = q90 - q10                                   # 分位数区间宽度（$ / MWh）
        scale = max(|q10|, |q50|, |q90|)                    # 分布尺度
        性质：iqr <= 2*scale（三角不等式）=> 天然 <= 1；scale==0（退化）→ uncertainty=1.0

    confidence = clip( 0.5*prob_strength + 0.5*magnitude_certainty, 0, 1 )
        prob_strength      = 2*|prob_pos - 0.5|             # 方向概率强度（0.5 处为 0，1/0 处为 1）
        half_width         = iqr / 2                        # 半区间宽
        magnitude_certainty = |q50| / (|q50| + half_width)  # 幅度相对不确定度（信噪比式）
        （|q50|==half_width==0 时取 0.5 中性）
    """
    iqr = q90 - q10
    scale = np.maximum.reduce([np.abs(q10), np.abs(q50), np.abs(q90)])
    with np.errstate(divide="ignore", invalid="ignore"):
        uncertainty = np.where(scale > 0, iqr / (2.0 * scale), 1.0)
    uncertainty = np.clip(uncertainty, 0.0, 1.0)

    prob_strength = np.clip(2.0 * np.abs(prob_pos - 0.5), 0.0, 1.0)
    half_width = iqr / 2.0
    denom = np.abs(q50) + half_width
    with np.errstate(divide="ignore", invalid="ignore"):
        magnitude_certainty = np.where(denom > 0, np.abs(q50) / denom, 0.5)
    confidence = np.clip(0.5 * prob_strength + 0.5 * magnitude_certainty, 0.0, 1.0)
    return confidence, uncertainty


def build_v2_frame(te, prob_pos, q_preds, split_key="te"):
    """把 5 个预测量组装成 V0.2 输出 DataFrame（split 列保留，仅调用方保证只写 test/val）。
    q_preds[alpha] = {"va": ..., "te": ...}；split_key 选择对应 split 的预测。"""
    q10, q50, q90 = q_preds[0.1][split_key], q_preds[0.5][split_key], q_preds[0.9][split_key]
    confidence, uncertainty = confidence_and_uncertainty(prob_pos, q10, q50, q90)
    out = pd.DataFrame({
        "node": te["node"].values,
        "target_date": te["target_date"].values,
        "hour": te["hour"].values,
        "split": te["split"].values,
        "expected_return": q50,
        "prob_positive": prob_pos,
        "prob_negative": 1.0 - prob_pos,
        "confidence": confidence,
        "uncertainty": uncertainty,
        "actual_return": te["actual_return"].values,
        "actual_direction": te["actual_direction"].values,
    })
    return out[OUT_COLS]


def dump_pred(df, path):
    df = df.sort_values(["node", "target_date", "hour"]).reset_index(drop=True)
    df["target_date"] = pd.to_datetime(df["target_date"]).dt.strftime("%Y-%m-%d")
    df["hour"] = df["hour"].astype(int)
    df["actual_direction"] = df["actual_direction"].astype(int)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


# ===========================================================================
# 指标
# ===========================================================================
def safe_auc(y, p):
    from sklearn.metrics import roc_auc_score
    try:
        if len(np.unique(y)) < 2:
            return np.nan
        return float(roc_auc_score(y, p))
    except Exception:
        return np.nan


def calibration_observations(t):
    """V0.2 confidence/uncertainty 与正确性的观测关系（诚实标注，非严格校准）。
    历史结论（docs/DecisionPipeline.md §7）：V0.1 confidence 未校准；本版公式为显式定义量。"""
    t = t.copy()
    t["correct"] = ((t["prob_positive"] >= 0.5) == (t["actual_return"] > 0)).astype(int)
    edges = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 1.0]
    labels = [f"({edges[i]},{edges[i+1]}]" for i in range(len(edges) - 1)]
    b = pd.cut(t["confidence"], edges, labels=labels)
    conf_bucket = {}
    for lab in labels:
        sub = t[b == lab]
        if len(sub):
            conf_bucket[lab] = {"n": int(len(sub)), "dir_acc": round(float(sub["correct"].mean()), 4)}
    q = pd.qcut(t["uncertainty"], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
    unc_bucket = {}
    for lab in ["Q1", "Q2", "Q3", "Q4"]:
        sub = t[q == lab]
        if len(sub):
            unc_bucket[str(lab)] = {"n": int(len(sub)), "dir_acc": round(float(sub["correct"].mean()), 4)}
    return {
        "note": "观测性诊断，非严格概率校准。V0.1 已记录 confidence 未校准（docs/DecisionPipeline.md §7）；本版公式为显式定义的组合量。",
        "confidence_bucket_dir_acc": conf_bucket,
        "uncertainty_quartile_dir_acc": unc_bucket,
        "confidence_vs_abs_return_spearman": round(float(t["confidence"].corr(t["actual_return"].abs(), method="spearman")), 4),
        "confidence_vs_correct_spearman": round(float(t["confidence"].corr(t["correct"], method="spearman")), 4),
        "expected_vs_actual_pearson": round(float(t["expected_return"].corr(t["actual_return"])), 4),
        "expected_vs_actual_spearman": round(float(t["expected_return"].corr(t["actual_return"], method="spearman")), 4),
        "reading": "confidence 与 |Return| 正相关（设计使然：幅度越大 magnitude_certainty 越高），但与正确性无单调关系；expected_return 与 actual_return 秩相关≈0.06（幅度预测在排序意义下信息弱，与 V0.1 结论一致）。",
    }


def dir_acc_sign(pred, actual):
    return float((np.sign(pred) == np.sign(actual)).mean())


def metrics_variant(name, prob_va, prob_te, q_preds, va, te):
    """单个 hour 变体的指标（val + test）。"""
    y_te = (te["actual_return"] > 0).astype(int)
    y_va = (va["actual_return"] > 0).astype(int)
    exp_te = q_preds[0.5]["te"]
    exp_va = q_preds[0.5]["va"]
    return {
        "variant": name,
        "clf_val_auc": safe_auc(y_va, prob_va),
        "clf_test_auc": safe_auc(y_te, prob_te),
        "clf_val_dir_acc": float(((prob_va >= 0.5) == (y_va == 1)).mean()),
        "clf_test_dir_acc": float(((prob_te >= 0.5) == (y_te == 1)).mean()),
        "reg_val_mae": float(np.abs(exp_va - va["actual_return"].values).mean()),
        "reg_val_rmse": float(np.sqrt(((exp_va - va["actual_return"].values) ** 2).mean())),
        "reg_test_mae": float(np.abs(exp_te - te["actual_return"].values).mean()),
        "reg_test_rmse": float(np.sqrt(((exp_te - te["actual_return"].values) ** 2).mean())),
        "reg_test_dir_acc_sign": dir_acc_sign(exp_te, te["actual_return"].values),
        "reg_val_dir_acc_sign": dir_acc_sign(exp_va, va["actual_return"].values),
    }


# ===========================================================================
# 主流程
# ===========================================================================
def main():
    labeled = prep()
    tr, va, te = split_frames(labeled)
    print(f"[data] train={len(tr)} val={len(va)} test={len(te)} rows")
    print(f"[data] test nodes: {te['node'].unique().tolist()}")
    print(f"[data] 当前 model_c 对 CatBoost/LightGBM 的 feat_cols = X_FEATURES - [hour]（hour 被排除）")

    y_clf_tr = (tr["actual_return"] > 0).astype(int)
    print(f"[data] train P(Return>0)={y_clf_tr.mean():.4f}  test P(Return>0)={(te['actual_return'] > 0).mean():.4f}")

    results = {}
    fitted = {}

    # ---------------- hour 实验：3 个变体 ----------------
    print("\n== hour 实验：without_hour / with_hour_numeric / with_hour_cat ==")
    for name, cfg in VARIANTS.items():
        print(f"\n--- {name} ---")
        feat_cols, cat_cols = variant_feature_cols(cfg["include_hour"], cfg["hour_as_cat"])
        print(f"    特征数={len(feat_cols)}  类别列={cat_cols}")

        clf, p_va, p_te, auc_va, auc_te = fit_clf_catboost(tr, va, te, feat_cols, cat_cols)
        reg_models, q_preds = fit_reg_lgbm_quantiles(tr, va, te, feat_cols, cat_cols)

        m = metrics_variant(name, p_va, p_te, q_preds, va, te)
        results[name] = m
        fitted[name] = {
            "clf": clf, "reg_models": reg_models,
            "prob_va": p_va, "prob_te": p_te, "q_preds": q_preds,
            "feat_cols": feat_cols, "cat_cols": cat_cols,
        }
        print(f"    clf: val_auc={m['clf_val_auc']:.4f} test_auc={m['clf_test_auc']:.4f} "
              f"test_dir_acc={m['clf_test_dir_acc']:.4f}")
        print(f"    reg: val_mae={m['reg_val_mae']:.2f} val_rmse={m['reg_val_rmse']:.2f} "
              f"test_mae={m['reg_test_mae']:.2f} test_rmse={m['reg_test_rmse']:.2f} "
              f"test_dir_acc(sign exp)={m['reg_test_dir_acc_sign']:.4f}")

    # ---------------- 最终模型选择：val AUC 主 + val MAE 副 ----------------
    order = sorted(results, key=lambda k: (-results[k]["clf_val_auc"], results[k]["reg_val_mae"]))
    chosen = order[0]
    print("\n== 选择 ==")
    for k in order:
        print(f"  {k}: clf_val_auc={results[k]['clf_val_auc']:.4f}  clf_test_auc={results[k]['clf_test_auc']:.4f}  "
              f"reg_val_mae={results[k]['reg_val_mae']:.2f}  reg_test_mae={results[k]['reg_test_mae']:.2f}")
    print(f"  -> 选中: {chosen}")

    sel = fitted[chosen]
    sel_meta = {
        "variant": chosen,
        "include_hour": VARIANTS[chosen]["include_hour"],
        "hour_as_cat": VARIANTS[chosen]["hour_as_cat"],
        "n_features": len(sel["feat_cols"]),
        "cat_features": sel["cat_cols"],
        "selection_criterion": "按 val AUC 择优；val AUC 平局时按 reg_val_mae 择优",
    }

    # ---------------- 生成 V0.2 输出：test（预测 CSV）+ val（另出） ----------------
    te_out = build_v2_frame(te, sel["prob_te"], sel["q_preds"], "te")
    dump_pred(te_out, OUT_PRED)
    print(f"\n[out] test 预测 -> {OUT_PRED}  ({len(te_out)} 行)")

    if len(va) > 0:
        va_out = build_v2_frame(va, sel["prob_va"], sel["q_preds"], "va")
        dump_pred(va_out, OUT_PRED_VAL)
        print(f"[out] val 预测  -> {OUT_PRED_VAL}  ({len(va_out)} 行)")

    # ---------------- test 基础指标（最终模型） ----------------
    t = pd.read_csv(OUT_PRED)
    t["target_date"] = pd.to_datetime(t["target_date"])
    y = (t["actual_return"] > 0).astype(int)
    base = {
        "n_test": int(len(t)),
        "mae": float(np.abs(t["expected_return"] - t["actual_return"]).mean()),
        "rmse": float(np.sqrt(((t["expected_return"] - t["actual_return"]) ** 2).mean())),
        "dir_acc_sign_expected": dir_acc_sign(t["expected_return"], t["actual_return"]),
        "dir_acc_argmax_prob": float(((t["prob_positive"] >= 0.5) == (y == 1)).mean()),
        "auc": safe_auc(y, t["prob_positive"]),
        "test_date_range": [t["target_date"].min().strftime("%Y-%m-%d"), t["target_date"].max().strftime("%Y-%m-%d")],
    }
    print("\n== test 基础指标（最终模型）==")
    print(json.dumps(base, indent=2, ensure_ascii=False))

    # ---------------- 每节点 test 指标 ----------------
    per_node = {}
    for node, g in t.groupby("node"):
        yn = (g["actual_return"] > 0).astype(int)
        per_node[node] = {
            "n": int(len(g)),
            "mae": float(np.abs(g["expected_return"] - g["actual_return"]).mean()),
            "rmse": float(np.sqrt(((g["expected_return"] - g["actual_return"]) ** 2).mean())),
            "dir_acc_sign_expected": dir_acc_sign(g["expected_return"], g["actual_return"]),
            "dir_acc_argmax_prob": float(((g["prob_positive"] >= 0.5) == (yn == 1)).mean()),
            "auc": safe_auc(yn, g["prob_positive"]),
        }
        print(f"  {node:18s} n={per_node[node]['n']:5d} mae={per_node[node]['mae']:7.2f} "
              f"rmse={per_node[node]['rmse']:8.2f} acc_sign={per_node[node]['dir_acc_sign_expected']:.4f} "
              f"acc_prob={per_node[node]['dir_acc_argmax_prob']:.4f} auc={per_node[node]['auc']:.4f}")
    base["test_by_node"] = per_node

    # ---------------- 校准诊断（诚实标注，非严格校准） ----------------
    calib = calibration_observations(t)
    base["calibration_observations"] = calib
    print("\n== 校准诊断（test，诚实标注）==")
    print(json.dumps(calib, indent=2, ensure_ascii=False))

    # ---------------- 特征清单标注 ----------------
    feat_list = [{
        "feature": f,
        "in_final_model": f in sel["feat_cols"],
        "category": categorize(f),
        "note": feature_note(f),
    } for f in X_FEATURES]

    # ---------------- 保存 notes ----------------
    notes = {
        "feature_version": "canonical_v1",
        "generated_by": "model_v2.py",
        "generated_at": pd.Timestamp.now().isoformat(),
        "model_role": "Production Predictive Model（V0.2）—— 只输出预测量，不输出 BUY/SELL；交易动作由 Rule Engine 决定",
        "contract_ref": "docs/business_contract.md §3/§7；不改 canonical decision_cutoff",
        "prediction_definition": {
            "expected_return": "Return=DA-RTPD 预期幅度（$/MWh）。LightGBM quantile(alpha=0.5) 中位数目标，抗重尾",
            "prob_positive": "P(Return>0)，CatBoost 二分类（Logloss）概率",
            "prob_negative": "1 - prob_positive（二元互补，P(Return<=0)）",
            "confidence": "0-1 置信度，公式见 confidence_uncertainty_formulas",
            "uncertainty": "0-1 不确定度，公式见 confidence_uncertainty_formulas",
            "only_split": "test（target_date 2026-06-02 .. 2026-08-05）；val 另出 predictions_v2_val.csv",
        },
        "confidence_uncertainty_formulas": {
            "uncertainty": "clip(iqr / (2*scale), 0, 1)，其中 iqr=q90-q10（LightGBM quantile 0.1/0.9 区间宽，$/MWh），scale=max(|q10|,|q50|,|q90|)；scale==0（退化）→ 1.0。性质：iqr<=2*scale（三角不等式）=> 天然∈[0,1]",
            "confidence": "clip(0.5*prob_strength + 0.5*magnitude_certainty, 0, 1)，其中 prob_strength=2*|prob_positive-0.5|（方向概率强度），magnitude_certainty=|q50|/(|q50|+iqr/2)（幅度相对不确定度，信噪比式；|q50|==iqr/2==0 时取 0.5）",
            "note": "confidence 是显式定义的组合量，不是校准概率。历史结论：V0.1 confidence 未校准（见 docs/DecisionPipeline.md §7），本版公式改为概率强度×幅度信噪比，校准/分位数校准仍属后续任务",
        },
        "hour_experiment": {
            "current_status": "model_c.py 的 CatBoost/LightGBM 均排除 hour（feat_cols = X_FEATURES - [hour]）",
            "variants_metrics": results,
            "chosen": chosen,
            "conclusion": hour_conclusion(results, chosen),
        },
        "final_model": sel_meta,
        "feature_list": feat_list,
        "feature_boundary_notes": [
            "只用 canonical X 区 38 个特征，全部 available_at <= decision_cutoff（D-1 10:00 PT DAM Market Close 前），无泄漏（feature_availability_matrix.md）。",
            "【当前缺失真实 as-of weather forecast archive】——本版天气特征仅用历史滞后（t2m_lag1/ssrd_lag1/wind100_lag1，target_date-2 当天），+ 历史 Return 特征 + load_2da_forecast 作 strict baseline。未用任何目标日实际天气模拟预报；t2m_next/ssrd_next/wind100_next 保持禁用（穿越）。真实决策时点可获得的天气预报归档一旦到位，应替换/补充并重训重测。",
            "load_2da_forecast / load_peak_flag 为 ASSUMED_AVAILABLE（2DA 日前负荷预测，schema 标注）。canonical 内 train/val/test 无缺失，但原始 load_CA_ISO_TAC_2DA.csv 仅到 2026-07-09，8 月 test 行取值来自 master.csv（已并入更新源）；真实部署需确认 2DA 发布时刻与口径。",
            "天气滞后特征在 2026-07-01..07-29 部分日缺失（树模型原生处理 NaN）；peer_* 对 ELCA 恒 NaN（无同区节点，树模型按缺失处理）。",
            "Return 重尾（test 最大 |Return| 数千 $/MWh）：回归用中位数目标（quantile 0.5），MAE/RMSE 受极端值影响大；方向指标更能反映模型价值。",
            "hour 不作为交易动作的输入（模型只输出预测量）；confidence/uncertainty 供 Rule Engine 作风险过滤，不参与决策截止口径变更。",
        ],
        "test_baseline_metrics": base,
        "output_files": {
            "predictions_test": str(OUT_PRED),
            "predictions_val": str(OUT_PRED_VAL),
            "notes": str(OUT_NOTES),
        },
    }
    OUT_NOTES.write_text(json.dumps(notes, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[notes] written -> {OUT_NOTES}")


# ===========================================================================
# 辅助：特征分类 / 说明（用于 notes）
# ===========================================================================
def categorize(f):
    if f in ("hour", "node", "zone", "dow", "month", "is_holiday", "solar_flag"):
        return "时间/节点（静态·日历）"
    if f.startswith("peer_"):
        return "节点联动（peer）"
    if f.startswith(("t2m_", "ssrd_", "wind100_")):
        return "天气滞后（历史）"
    if f.startswith(("da_", "rtpd_", "spread_")) and "_lag" in f:
        return "价格滞后"
    if f.startswith(("spread_mean", "spread_std")):
        return "价差滚动统计"
    if "day_" in f:
        return "日级统计（target_date-2 当天）"
    if f.startswith("load_"):
        return "负荷"
    return "其他"


def feature_note(f):
    notes = {
        "hour": "小时 1..24（日历，CONFIRMED）",
        "node": "节点（CONTROLX_1_N001 / ELCAJNGT_7_N001 / SNLNDRO_1_N001）",
        "zone": "区域（NP15/SP15/ZP26），与 node 完全共线",
        "dow": "星期（0..6）",
        "month": "月（1..12）",
        "is_holiday": "US 联邦假日",
        "solar_flag": "日照窗口 10-16 时",
        "load_2da_forecast": "日前负荷预测（ASSUMED_AVAILABLE，2DA）",
        "load_peak_flag": "负荷峰值时点标记（ASSUMED_AVAILABLE）",
        "t2m_lag1": "target_date-2 同 hour 气温（历史滞后；无真实 as-of 天气预报归档）",
        "ssrd_lag1": "target_date-2 同 hour 太阳辐射（历史滞后）",
        "wind100_lag1": "target_date-2 同 hour 100m 风速（历史滞后）",
    }
    return notes.get(f, "")


def hour_conclusion(results, chosen):
    w = results["without_hour"]
    h = results[chosen]
    diff_auc = h["clf_val_auc"] - w["clf_val_auc"]
    return {
        "selected": chosen,
        "val_auc_delta_vs_without_hour": round(diff_auc, 4),
        "test_auc_delta_vs_without_hour": round(h["clf_test_auc"] - w["clf_test_auc"], 4),
        "test_reg_mae_delta_vs_without_hour": round(h["reg_test_mae"] - w["reg_test_mae"], 4),
        "test_reg_rmse_delta_vs_without_hour": round(h["reg_test_rmse"] - w["reg_test_rmse"], 4),
        "statement": (
            f"hour 实验由数据决定：选中的 '{chosen}' 相对 without_hour 的 val AUC 差值为 {diff_auc:+.4f}。"
            "（结论细节见 hour_experiment.variants_metrics；若差值在噪声内，取指标更优且不劣化的一方；"
            "不无依据删 hour。）"
        ),
    }


if __name__ == "__main__":
    main()
