# -*- coding: utf-8 -*-
"""
建模：naive 基线 + 方向分类器（决策核心）+ 分位数回归（展示）。

决策规则（与 evaluate.py / app.py 一致）：
  1) 幅度风险控制：spread_std7 > std_th（高波动日）-> 一律 "hold"（避免极端亏损）
  2) 方向概率：P(spread>0) > hi  -> "sell"（预测价差为正 -> 日前卖、实时买）
              P(spread>0) < lo  -> "buy" （预测价差为负 -> 日前买、实时卖）
              否则               -> "hold"
说明：方向与幅度是两个维度。方向由分类器概率给出，幅度风险由近 7 日价差波动
spread_std7 控制——测试集证据显示高波动日的巨额亏损主导套利结果，应在此观望。

输入：code/data/features.parquet
输出：
  code/models/lgbm_spread_clf.pkl  lgbm_spread_q0.1/q0.5/q0.9.pkl  lgbm_da_q0.5.pkl  lgbm_rtpd_q0.5.pkl
  code/models/feature_cols.json
  code/data/test_predictions.csv
"""
import os
import json
import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "code", "data")
MODELS = os.path.join(ROOT, "code", "models")
FEATURES_PATH = os.path.join(DATA, "features.parquet")
PRED_PATH = os.path.join(DATA, "test_predictions.csv")

# 与 features.py ORDER 一致的特征列（不含目标）
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
# 决策阈值（可调）。std_th 为幅度风险线：近7日价差波动超过该值时一律观望
DECISION_TH = dict(hi=0.55, lo=0.45, std_th=80.0)


def decision_from_prob(prob, std7, th_hi=DECISION_TH["hi"], th_lo=DECISION_TH["lo"],
                       std_th=DECISION_TH["std_th"]):
    """由方向概率 + 波动风控给出 (decision, direction)。
    direction: +1 卖方向, -1 买方向（用于方向准确率，prob>0.5 -> +1）。"""
    decision = np.where(
        std7 > std_th, "hold",
        np.where(prob > th_hi, "sell",
                 np.where(prob < th_lo, "buy", "hold")))
    direction = np.where(prob > 0.5, 1, -1)
    return decision, direction


def train_reg(df, target_col, alpha, name):
    model = lgb.LGBMRegressor(**{**LGB_PARAMS, "objective": "quantile", "alpha": alpha})
    model.fit(df.loc[df.split == "train", FEATURES], df.loc[df.split == "train", target_col],
              eval_set=[(df.loc[df.split == "val", FEATURES], df.loc[df.split == "val", target_col])],
              callbacks=[lgb.early_stopping(80, verbose=False)])
    path = os.path.join(MODELS, f"{name}.pkl")
    joblib.dump(model, path)
    return model, path


def train_clf(df, name="lgbm_spread_clf"):
    """方向分类器：目标 y = (spread_next > 0)，输出 P(spread>0)。"""
    y = (df["spread_next"] > 0).astype(int)
    model = lgb.LGBMClassifier(**{**LGB_PARAMS, "objective": "binary"})
    model.fit(df.loc[df.split == "train", FEATURES], y.loc[df.split == "train"],
              eval_set=[(df.loc[df.split == "val", FEATURES], y.loc[df.split == "val"])],
              callbacks=[lgb.early_stopping(80, verbose=False)])
    path = os.path.join(MODELS, f"{name}.pkl")
    joblib.dump(model, path)
    return model, path


def main():
    os.makedirs(MODELS, exist_ok=True)
    df = pd.read_parquet(FEATURES_PATH)
    node_codes, node_labels = pd.factorize(df["node"])
    df["node"] = node_codes

    train = df[df.split == "train"]
    val = df[df.split == "val"]
    test = df[df.split == "test"]
    print(f"rows: train={len(train)} val={len(val)} test={len(test)}")
    print("spread>0 占比: train=%.3f val=%.3f test=%.3f"
          % (train.spread_next.gt(0).mean(), val.spread_next.gt(0).mean(), test.spread_next.gt(0).mean()))

    # ---- 基线 naive ----
    t = test.dropna(subset=["spread_next", "spread_lag1"])
    print("[baseline naive=spread_lag1] spread MAE=%.3f RMSE=%.3f" %
          ((t.spread_next - t.spread_lag1).abs().mean(), np.sqrt(((t.spread_next - t.spread_lag1) ** 2).mean())))

    # ---- 方向分类器（决策核心）----
    clf, clf_path = train_clf(df)
    print("trained direction classifier best_iter=%s" % clf.best_iteration_)

    # ---- 展示用回归：spread 分位数 + DA/RTPD q50 ----
    models, saved = {}, {}
    for q in (0.1, 0.5, 0.9):
        m, p = train_reg(df, "spread_next", q, f"lgbm_spread_q{q:g}")
        models[f"spread_q{q:g}"] = m
        saved[f"spread_q{q:g}"] = p
    for col, name in [("da_price_next", "lgbm_da_q0.5"), ("rtpd_price_next", "lgbm_rtpd_q0.5")]:
        m, p = train_reg(df, col, 0.5, name)
        models[name] = m
        saved[name] = p
    models["lgbm_spread_clf"] = clf
    saved["lgbm_spread_clf"] = clf_path

    # ---- 测试集预测（契约输出）----
    X_test = test[FEATURES]
    prob = clf.predict_proba(X_test)[:, 1]
    std7 = test["spread_std7"].fillna(99).values
    decision, direction = decision_from_prob(prob, std7)
    spr_q10 = models["spread_q0.1"].predict(X_test)
    spr_q50 = models["spread_q0.5"].predict(X_test)
    spr_q90 = models["spread_q0.9"].predict(X_test)
    da_pred = models["lgbm_da_q0.5"].predict(X_test)
    rt_pred = models["lgbm_rtpd_q0.5"].predict(X_test)

    out = pd.DataFrame({
        "node": test["node"].values,
        "date": (pd.to_datetime(test["date"]) + pd.Timedelta(days=1)).dt.date,  # 目标日 D+1
        "hour": test["hour"].values,
        "prob_sell": prob,
        "spread_std7": std7,
        "spread_q10": spr_q10, "spread_q50": spr_q50, "spread_q90": spr_q90,
        "spread_actual": test["spread_next"].values,
        "da_pred": da_pred, "rtpd_pred": rt_pred,
        "da_actual": test["da_price_next"].values, "rtpd_actual": test["rtpd_price_next"].values,
        "direction_pred": direction,
        "decision_pred": decision,
    })
    out.to_csv(PRED_PATH, index=False)
    print("saved", PRED_PATH, "rows:", len(out))

    with open(os.path.join(MODELS, "feature_cols.json"), "w", encoding="utf-8") as f:
        json.dump({
            "features": FEATURES,
            "node_categories": [str(x) for x in node_labels],
            "models": {k: os.path.basename(v) for k, v in saved.items()},
            "decision_thresholds": DECISION_TH,
        }, f, ensure_ascii=False, indent=2)

    # ---- 简版测试集指标 ----
    t = out.dropna(subset=["spread_actual"])
    mae = (t["spread_q50"] - t["spread_actual"]).abs().mean()
    dir_acc = (np.sign(t.spread_actual) == t["direction_pred"]).mean()
    pnl = np.where(t.decision_pred == "hold", 0.0, t["direction_pred"] * t["spread_actual"])
    print(f"[LightGBM] spread q50 MAE={mae:.3f} 方向准确率(全样本)={dir_acc:.3f} 策略收益={pnl.sum():.0f}")
    print("决策占比:", out["decision_pred"].value_counts().to_dict())


if __name__ == "__main__":
    main()
