# -*- coding: utf-8 -*-
"""
Flask 本地网页后端：输入 节点 + 目标日期 -> 预测次日 24h DA/RTPD/spread + 买卖建议。

接口：
  GET  /                -> 渲染 templates/index.html
  POST /api/predict     -> {node, target_date} -> 预测 JSON（契约见 templates/index.html）

预测语义：target_date = 目标日（D+1），决策日 D = target_date - 1。
滞后特征只使用 D-1 及更早的价格（决策时 D 日当日价格不可见，防泄漏）。
D+1 的 2DA 负荷与天气为预报值，可直接作为特征（缺失时保留 NaN，LightGBM 原生处理）。

数据边界：
  - 可预测目标日上限由 D+1 的天气（至 2026-08-19）与 D-1..D-7 价格（D 日 ≤ master 内最后一天价格）决定。
  - 回测：若 target_date 的价格真实值存在（≤ 价格数据末端），返回真实值对比与方向准确率。
"""
import os
import json
from datetime import timedelta
import numpy as np
import pandas as pd
import lightgbm as lgb
from flask import Flask, jsonify, render_template, request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "code", "data")
MODELS = os.path.join(ROOT, "code", "models")
MASTER_PATH = os.path.join(DATA, "master.csv")
FEAT_COLS_PATH = os.path.join(MODELS, "feature_cols.json")

NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]  # 训练覆盖的节点；ELCAJNGT 未训练
VALUE_COLS = ["da_price", "rtpd_price", "spread", "load_actual",
              "load_2da", "t2m", "ssrd", "wind100"]

# 美国联邦假日（2025-2026）
from pandas.tseries.holiday import USFederalHolidayCalendar
_HOLIDAYS = set(USFederalHolidayCalendar().holidays(start="2025-01-01", end="2027-12-31").date)

app = Flask(__name__)


def load_state():
    """加载 master + 模型 + 特征契约，返回查找表。"""
    master = pd.read_csv(MASTER_PATH, parse_dates=["date"])
    master["date"] = master["date"].dt.date
    lookups = {}
    for node in NODES:
        sub = master[master["node"] == node].sort_values(["date", "hour"])
        lookups[node] = {
            col: sub.pivot_table(index="date", columns="hour", values=col)
            for col in VALUE_COLS
        }
    with open(FEAT_COLS_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    models = {name: lgb.Booster(model_file=os.path.join(MODELS, os.path.basename(p)))
              for name, p in meta["models"].items()}
    return master, lookups, meta, models


STATE = None


def get_state():
    global STATE
    if STATE is None:
        STATE = load_state()
    return STATE


def _lookup(node_lk, col, day, hour):
    """取某节点某天某 hour 的值，缺失返回 NaN。"""
    try:
        v = node_lk[col].loc[day, hour]
        return float(v) if pd.notna(v) else np.nan
    except (KeyError, TypeError):
        return np.nan


def build_features_for_day(lookups, node, target_date):
    """构造单个目标日 24 行的特征 DataFrame（列顺序 = feature_cols.json 的 features）。"""
    node_lk = lookups[node]
    D = target_date - timedelta(days=1)
    rows = []
    for h in range(1, 25):
        def get(col, day):
            return _lookup(node_lk, col, day, h)

        vals = [get("spread", D - timedelta(days=k)) for k in range(1, 8)]
        vals = [v for v in vals if pd.notna(v)]
        mean7 = float(np.mean(vals)) if vals else np.nan
        std7 = float(np.std(vals)) if len(vals) > 1 else np.nan

        # D+1 当天 load_2da 是否峰值（用于 load_peak_flag）
        load2_row = node_lk["load_2da"].loc[target_date] if target_date in node_lk["load_2da"].index else pd.Series(dtype=float)
        peak = np.nan
        if not load2_row.empty and load2_row.notna().any():
            peak = 1.0 if load2_row.get(h, np.nan) == load2_row.max() else 0.0

        dt = pd.Timestamp(target_date)
        rows.append({
            "da_lag1": get("da_price", D - timedelta(days=1)),
            "rtpd_lag1": get("rtpd_price", D - timedelta(days=1)),
            "spread_lag1": get("spread", D - timedelta(days=1)),
            "da_lag2": get("da_price", D - timedelta(days=2)),
            "rtpd_lag2": get("rtpd_price", D - timedelta(days=2)),
            "spread_lag2": get("spread", D - timedelta(days=2)),
            "da_lag7": get("da_price", D - timedelta(days=7)),
            "rtpd_lag7": get("rtpd_price", D - timedelta(days=7)),
            "spread_lag7": get("spread", D - timedelta(days=7)),
            "spread_mean7": mean7,
            "spread_std7": std7,
            "load_actual_lag1": get("load_actual", D - timedelta(days=1)),
            "load_2da_next": get("load_2da", target_date),
            "t2m_next": get("t2m", target_date),
            "ssrd_next": get("ssrd", target_date),
            "wind100_next": get("wind100", target_date),
            "dow_next": dt.weekday(),
            "month_next": dt.month,
            "is_holiday_next": 1 if target_date in _HOLIDAYS else 0,
            "solar_flag": 1 if 10 <= h <= 16 else 0,
            "load_peak_flag": peak,
            "hour": h,
            "node": node,
        })
    return pd.DataFrame(rows)


def predict_day(state, node, target_date):
    master, lookups, meta, models = state
    feat_cols = meta["features"]
    X = build_features_for_day(lookups, node, target_date)
    for c in meta["categorical"]:
        X[c] = X[c].astype("category")
    X = X[feat_cols]

    spr_q10 = models["spread_q0.1"].predict(X)
    spr_q50 = models["spread_q0.5"].predict(X)
    spr_q90 = models["spread_q0.9"].predict(X)
    da_pred = models["lgbm_da_q0.5"].predict(X)
    rt_pred = models["lgbm_rtpd_q0.5"].predict(X)

    decision = np.where(spr_q90 < 0, "buy", np.where(spr_q10 > 0, "sell", "hold"))
    label_map = {"buy": "买", "sell": "卖", "hold": "观望"}
    direction = np.where(spr_q50 < 0, -1, 1)

    # 回测：target_date 的真实价格是否已存在（master 覆盖）
    lk = lookups[node]
    has_actual = target_date in lk["da_price"].index
    da_act = rt_act = spr_act = None
    acc = None
    if has_actual:
        da_act = [lk["da_price"].loc[target_date, h] for h in range(1, 25)]
        rt_act = [lk["rtpd_price"].loc[target_date, h] for h in range(1, 25)]
        spr_act = [lk["spread"].loc[target_date, h] for h in range(1, 25)]
        acc = float((np.sign(np.array(spr_act)) == direction).mean())

    return {
        "ok": True,
        "node": node,
        "target_date": target_date.isoformat(),
        "hours": list(range(1, 25)),
        "da_pred": [float(x) for x in da_pred],
        "rtpd_pred": [float(x) for x in rt_pred],
        "spread_q10": [float(x) for x in spr_q10],
        "spread_q50": [float(x) for x in spr_q50],
        "spread_q90": [float(x) for x in spr_q90],
        "decision": [str(x) for x in decision],
        "decision_label": [label_map[x] for x in decision],
        "has_actual": has_actual,
        "da_actual": [float(x) for x in da_act] if da_act else None,
        "rtpd_actual": [float(x) for x in rt_act] if rt_act else None,
        "spread_actual": [float(x) for x in spr_act] if spr_act else None,
        "direction_accuracy": acc,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/predict", methods=["POST"])
def api_predict():
    try:
        req = request.get_json(force=True)
        node = str(req.get("node", "")).strip()
        target_date = pd.Timestamp(str(req.get("target_date", ""))).date()
    except Exception:
        return jsonify({"ok": False, "error": "参数格式错误：需要 node 和 target_date"})

    if node not in NODES:
        return jsonify({"ok": False, "error": "不支持的节点（当前已训练节点：" + "、".join(NODES) + "）"})

    state = get_state()
    master, lookups, meta, models = state
    # 边界校验：D-1 价格须存在（即 D-7 至少在 master 价格范围内）
    min_date = master["date"].min()
    max_date = master["date"].max()
    if target_date - timedelta(days=8) < min_date:
        return jsonify({"ok": False, "error": "目标日期过早（无足够历史滞后）"})
    if target_date > max_date + timedelta(days=1):
        return jsonify({"ok": False, "error": f"目标日期超出可预测范围（数据截至 {max_date}，可预测至 {max_date + timedelta(days=1)}）"})

    try:
        result = predict_day(state, node, target_date)
    except Exception as e:
        return jsonify({"ok": False, "error": f"预测失败: {e}"})
    return jsonify(result)


if __name__ == "__main__":
    print("启动 CA-ISO 电价价差预测网页: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
