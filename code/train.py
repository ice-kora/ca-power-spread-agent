# -*- coding: utf-8 -*-
"""
建模：naive 基线 + LightGBM 分位数回归。

主目标：预测 D+1 各 hour 的 spread = DA - RTPD（分位数 q10/q50/q90）。
展示辅助：DA、RTPD 的 q50 点预测。
决策规则（与 evaluate.py / app.py 一致）：
  spread_q90 < 0  -> "buy"  （价差为负、RTPD 更高 -> 日前买、实时卖）
  spread_q10 > 0  -> "sell" （价差为正 -> 日前卖、实时买）
  否则            -> "hold"（区间跨零 -> 观望）

基线 naive = spread_lag1（D-1 同 hour spread 直推 D+1），因为禁止使用 D 日当日价格。

输入：code/data/features.parquet（由 features.py 生成，列契约见下）
输出：
  code/models/lgbm_spread_q0.1.txt / q0.5 / q0.9, lgbm_da_q0.5.txt, lgbm_rtpd_q0.5.txt
  code/models/feature_cols.json
  code/data/test_predictions.csv（供 evaluate.py）
"""
import os
import json
import numpy as np
import pandas as pd
import lightgbm as lgb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "code", "data")
MODELS = os.path.join(ROOT, "code", "models")
FEATURES_PATH = os.path.join(DATA, "features.parquet")
PRED_PATH = os.path.join(DATA, "test_predictions.csv")

# 与 features.py 契约一致的特征列（不含目标 spread_next/da_price_next/rtpd_price_next）
FEATURES = [
    "da_lag1", "rtpd_lag1", "spread_lag1",
    "da_lag2", "rtpd_lag2", "spread_lag2",
    "da_lag7", "rtpd_lag7", "spread_lag7",
    "spread_mean7", "spread_std7", "load_actual_lag1",
    "load_2da_next", "t2m_next", "ssrd_next", "wind100_next",
    "dow_next", "month_next", "is_holiday_next",
    "solar_flag", "load_peak_flag", "hour", "node",
]
CATEGORICAL = ["hour", "node"]

LGB_PARAMS = dict(
    objective="quantile",
    learning_rate=0.05,
    n_estimators=800,
    num_leaves=31,
    colsample_bytree=0.8,
    subsample=0.8,
    min_child_samples=20,
    reg_alpha=0.1,
    verbose=-1,
)


def make_model(alpha):
    return lgb.LGBMRegressor(**{**LGB_PARAMS, "alpha": alpha})


def train_one(df, target_col, alpha, name):
    """训练单目标分位数模型，用 val 早停，返回模型。"""
    model = make_model(alpha)
    model.fit(
        df.loc[df.split == "train", FEATURES],
        df.loc[df.split == "train", target_col],
        categorical_feature=CATEGORICAL,
        eval_set=[(df.loc[df.split == "val", FEATURES], df.loc[df.split == "val", target_col])],
        callbacks=[lgb.early_stopping(80, verbose=False)],
    )
    path = os.path.join(MODELS, f"{name}.txt")
    model.booster_.save_model(path)
    return model, path


def predict_model(model, X):
    return model.predict(X)


def decision_from_q(q10, q50, q90):
    """三态决策规则。返回 (decision, direction)。direction: 1/-1（q50 符号）。"""
    decision = np.where(q90 < 0, "buy", np.where(q10 > 0, "sell", "hold"))
    direction = np.where(q50 < 0, -1, 1)  # q50=0 时按 1 处理
    return decision, direction


def main():
    os.makedirs(MODELS, exist_ok=True)
    df = pd.read_parquet(FEATURES_PATH)

    # 确保分类特征为 category dtype（LightGBM categorical 要求）
    for c in CATEGORICAL:
        df[c] = df[c].astype("category")

    train = df[df.split == "train"]
    val = df[df.split == "val"]
    test = df[df.split == "test"]
    print(f"rows: train={len(train)} val={len(val)} test={len(test)}")

    # ---- 基线 naive：spread_lag1 直推（D-1 同 hour）----
    t = test.dropna(subset=["spread_next", "spread_lag1"])
    naive_mae = (t["spread_next"] - t["spread_lag1"]).abs().mean()
    naive_rmse = np.sqrt(((t["spread_next"] - t["spread_lag1"]) ** 2).mean())
    print(f"[baseline naive=spread_lag1] spread MAE={naive_mae:.3f} RMSE={naive_rmse:.3f}")

    # ---- 主目标：spread 分位数 ----
    models = {}
    for q in (0.1, 0.5, 0.9):
        m, p = train_one(df, "spread_next", q, f"lgbm_spread_q{q:g}")
        models[f"spread_q{q:g}"] = p
        print(f"trained spread q{q} best_iter={m.best_iteration_}")

    # ---- 展示辅助：DA / RTPD q50 点预测 ----
    for col, name in [("da_price_next", "lgbm_da_q0.5"), ("rtpd_price_next", "lgbm_rtpd_q0.5")]:
        m, p = train_one(df, col, 0.5, name)
        models[name] = p
        print(f"trained {col} best_iter={m.best_iteration_}")

    # ---- 测试集预测（契约输出）----
    X_test = test[FEATURES]
    spr_q10 = predict_model(models["spread_q0.1"], X_test)
    spr_q50 = predict_model(models["spread_q0.5"], X_test)
    spr_q90 = predict_model(models["spread_q0.9"], X_test)
    da_pred = predict_model(models["lgbm_da_q0.5"], X_test)
    rt_pred = predict_model(models["lgbm_rtpd_q0.5"], X_test)
    decision, direction = decision_from_q(spr_q10, spr_q50, spr_q90)

    out = pd.DataFrame({
        "node": test["node"].values,
        "date": (pd.to_datetime(test["date"]) + pd.Timedelta(days=1)).dt.date,  # 目标日 D+1
        "hour": test["hour"].values,
        "spread_q10": spr_q10, "spread_q50": spr_q50, "spread_q90": spr_q90,
        "spread_actual": test["spread_next"].values,
        "da_pred": da_pred, "rtpd_pred": rt_pred,
        "da_actual": test["da_price_next"].values, "rtpd_actual": test["rtpd_price_next"].values,
        "direction_pred": direction,
        "decision_pred": decision,
    })
    out.to_csv(PRED_PATH, index=False)
    print("saved", PRED_PATH, "rows:", len(out))

    # 存特征契约，供 app.py 复用
    with open(os.path.join(MODELS, "feature_cols.json"), "w", encoding="utf-8") as f:
        json.dump({"features": FEATURES, "categorical": CATEGORICAL, "models": models}, f, ensure_ascii=False, indent=2)

    # ---- 简版测试集指标 ----
    mae = (out["spread_q50"] - out["spread_actual"]).abs().mean()
    rmse = np.sqrt(((out["spread_q50"] - out["spread_actual"]) ** 2).mean())
    dir_acc = ((out["direction_pred"] == np.sign(out["spread_actual"]))).mean()
    print(f"[LightGBM spread q50] MAE={mae:.3f} RMSE={rmse:.3f} 方向准确率(全样本)={dir_acc:.3f}")
    print("决策占比:", out["decision_pred"].value_counts().to_dict())


if __name__ == "__main__":
    main()
