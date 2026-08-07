# -*- coding: utf-8 -*-
"""
Flask 本地网页后端：输入 节点 + 目标日期 -> 预测次日 24h DA/RTPD/spread + 买卖建议。

接口：
  GET  /                -> 渲染 templates/index.html
  POST /api/predict     -> {node, target_date} -> 预测 JSON（契约见 templates/index.html）

预测语义：target_date = 目标日（D+1），决策日 D = target_date - 1。
滞后特征只使用 D-1 及更早的价格（决策时 D 日当日价格不可见，防泄漏）。
D+1 的 2DA 负荷与天气为预报值，直接作特征；缺失时保留 NaN（LightGBM 原生处理）。

数据边界：价格历史滞后用 master.csv（价格日期范围）；D+1 的 2DA 负荷与天气从
原始文件全范围读取（2DA 至 2026-08-07、天气至 2026-08-19），因此可预测目标日
可延伸到 08-07（受 2DA 覆盖），更远期 2DA 缺失时该特征为 NaN。
"""
import os
import json
import joblib
from datetime import timedelta
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request
from pandas.tseries.holiday import USFederalHolidayCalendar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "code", "data")
MODELS = os.path.join(ROOT, "code", "models")
MASTER_PATH = os.path.join(DATA, "master.csv")
FEAT_COLS_PATH = os.path.join(MODELS, "feature_cols.json")
LOAD2_PATH = os.path.join(ROOT, "load_CA_ISO_TAC_2DA.csv")
WEATHER_PATH = os.path.join(ROOT, "zone_weather_hourly.csv")

MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA_NODE = "ELCAJNGT_7_N001"
NODES = MAIN_NODES + [ELCA_NODE]
ZONE_OF_NODE = {"SNLNDRO_1_N001": "ZP26", "CONTROLX_1_N001": "ZP26", "ELCAJNGT_7_N001": "SP15"}
PRICE_COLS = ["da_price", "rtpd_price", "spread", "load_actual"]
WEATHER_COLS = ["t2m", "ssrd", "wind100"]
HOURS = list(range(1, 25))

_HOLIDAYS = set(USFederalHolidayCalendar().holidays(start="2025-01-01", end="2027-12-31").date)

app = Flask(__name__)
STATE = None


def _pivot(df, col):
    return df.pivot_table(index="date", columns="hour", values=col)


def _get_pivot(pf, day, hour):
    try:
        v = pf.loc[day, hour]
        return float(v) if pd.notna(v) else np.nan
    except (KeyError, TypeError):
        return np.nan


def _get_col(lk, col, day, hour):
    return _get_pivot(lk[col], day, hour)


def load_state():
    master = pd.read_csv(MASTER_PATH, parse_dates=["date"])
    master["date"] = master["date"].dt.date

    price_lk = {}
    for node in NODES:
        sub = master[master["node"] == node].sort_values(["date", "hour"])
        price_lk[node] = {c: _pivot(sub, c) for c in PRICE_COLS}

    # 同 zone 关联节点（peer）的价格（ZP26: CONTROLX <-> SNLNDRO）；ELCAJNGT 无 peer
    peer_lk = {}
    for node in MAIN_NODES:
        peer = [m for m in MAIN_NODES if m != node][0]
        sub = master[master["node"] == peer].sort_values(["date", "hour"])
        peer_lk[node] = {f"peer_{c}": _pivot(sub, c) for c in ("da_price", "rtpd_price", "spread")}

    # 完整日期范围的 2DA 负荷（原始文件，含未来）
    l2 = pd.read_csv(LOAD2_PATH)
    l2["date"] = pd.to_datetime(l2["Date"].astype(str), format="mixed").dt.date
    l2m = l2.melt(id_vars="date", value_vars=[f"H{i}" for i in HOURS], var_name="hc", value_name="v")
    l2m["hour"] = l2m["hc"].str.extract(r"(\d+)").astype(int)
    load2_lk = l2m.pivot_table(index="date", columns="hour", values="v")

    # 完整日期范围的天气（按 zone；valid_pt 00:00 -> H1）
    wt = pd.read_csv(WEATHER_PATH)
    wt = wt.rename(columns={"t2m_c": "t2m", "ssrd_wm2": "ssrd"})
    vt = pd.to_datetime(wt["valid_pt"])
    wt["date"] = vt.dt.date
    wt["hour"] = vt.dt.hour + 1
    weather_lk = {z: {c: _pivot(wt[wt["zone"] == z], c) for c in WEATHER_COLS}
                  for z in wt["zone"].unique()}

    with open(FEAT_COLS_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    models = {name: joblib.load(os.path.join(MODELS, os.path.basename(p)))
              for name, p in meta["models"].items()}
    models_elca = {name: joblib.load(os.path.join(MODELS, os.path.basename(p)))
                   for name, p in meta.get("elca_models", {}).items()}
    return {"master": master, "price_lk": price_lk, "peer_lk": peer_lk,
            "load2_lk": load2_lk, "weather_lk": weather_lk, "meta": meta,
            "models": models, "models_elca": models_elca}


def get_state():
    global STATE
    if STATE is None:
        STATE = load_state()
    return STATE


def build_features_for_day(state, node, target_date):
    """构造单个目标日 24 行特征 DataFrame（列顺序 = feature_cols.json 的 features）。"""
    price_lk = state["price_lk"][node]
    peer_lk = state["peer_lk"].get(node)  # ELCAJNGT 无 peer -> None
    wlk = state["weather_lk"][ZONE_OF_NODE[node]]
    load2_lk = state["load2_lk"]
    D = target_date - timedelta(days=1)
    d1 = D - timedelta(days=1)

    # 预取 D-1 的 24h 序列（日内形态 / 日均负荷）
    def _day_series(lk, col):
        if d1 in lk[col].index:
            return lk[col].loc[d1]
        return pd.Series(dtype=float)

    sp_d1 = _day_series(price_lk, "spread")
    la_d1 = _day_series(price_lk, "load_actual")
    day_std1 = float(sp_d1.std()) if sp_d1.notna().sum() > 1 else np.nan
    day_max1 = float(sp_d1.max()) if sp_d1.notna().any() else np.nan
    day_range1 = float(sp_d1.max() - sp_d1.min()) if sp_d1.notna().sum() > 1 else np.nan
    load_day_mean1 = float(la_d1.mean()) if la_d1.notna().any() else np.nan

    rows = []
    for h in HOURS:
        getp = lambda col, day: _get_col(price_lk, col, day, h)  # noqa: E731
        getw = lambda col, day: _get_col(wlk, col, day, h)      # noqa: E731
        getpeer = (lambda col, day: _get_col(peer_lk, col, day, h)) if peer_lk else (lambda col, day: np.nan)  # noqa: E731

        def _agg(col, days):
            vals = [getp(col, D - timedelta(days=k)) for k in range(1, days + 1)]
            return [v for v in vals if pd.notna(v)]

        v7 = _agg("spread", 7)
        v14 = _agg("spread", 14)
        v30 = _agg("spread", 30)
        mean7 = float(np.mean(v7)) if v7 else np.nan
        std7 = float(np.std(v7)) if len(v7) > 1 else np.nan
        mean14 = float(np.mean(v14)) if v14 else np.nan
        std14 = float(np.std(v14)) if len(v14) > 1 else np.nan
        mean30 = float(np.mean(v30)) if v30 else np.nan
        std30 = float(np.std(v30)) if len(v30) > 1 else np.nan

        # load_peak_flag：D+1 当天 2DA 负荷是否峰值（缺失则 NaN）
        peak = np.nan
        if target_date in load2_lk.index:
            row = load2_lk.loc[target_date]
            if row.notna().any():
                hv = row.get(h, np.nan)
                peak = 1.0 if pd.notna(hv) and hv == row.max() else 0.0

        dt = pd.Timestamp(target_date)
        rows.append({
            "da_lag1": getp("da_price", D - timedelta(days=1)),
            "rtpd_lag1": getp("rtpd_price", D - timedelta(days=1)),
            "spread_lag1": getp("spread", D - timedelta(days=1)),
            "da_lag2": getp("da_price", D - timedelta(days=2)),
            "rtpd_lag2": getp("rtpd_price", D - timedelta(days=2)),
            "spread_lag2": getp("spread", D - timedelta(days=2)),
            "da_lag7": getp("da_price", D - timedelta(days=7)),
            "rtpd_lag7": getp("rtpd_price", D - timedelta(days=7)),
            "spread_lag7": getp("spread", D - timedelta(days=7)),
            "spread_mean7": mean7,
            "spread_std7": std7,
            "spread_mean14": mean14,
            "spread_std14": std14,
            "spread_mean30": mean30,
            "spread_std30": std30,
            "load_actual_lag1": getp("load_actual", D - timedelta(days=1)),
            "load_actual_day_mean_lag1": load_day_mean1,
            "peer_spread_lag1": getpeer("peer_spread", D - timedelta(days=1)),
            "peer_da_lag1": getpeer("peer_da_price", D - timedelta(days=1)),
            "peer_rtpd_lag1": getpeer("peer_rtpd_price", D - timedelta(days=1)),
            "spread_day_std_lag1": day_std1,
            "spread_day_range_lag1": day_range1,
            "spread_day_max_lag1": day_max1,
            "load_2da_next": _get_pivot(load2_lk, target_date, h),
            "t2m_next": getw("t2m", target_date),
            "ssrd_next": getw("ssrd", target_date),
            "wind100_next": getw("wind100", target_date),
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
    meta = state["meta"]
    feat_cols = meta["features"]
    X = build_features_for_day(state, node, target_date)
    is_elca = node == ELCA_NODE
    if is_elca:
        models = state["models_elca"]
        X["node"] = 0.0
    else:
        models = state["models"]
        node_map = {str(c): float(i) for i, c in enumerate(meta.get("node_categories", []))}
        X["node"] = X["node"].map(node_map)
    X = X[feat_cols]

    # 方向分类器（决策核心，CatBoost）：P(spread>0)。单边策略：只在预测正价差且波动可控时卖，其余观望。
    clf = models["catboost_spread_elca"] if is_elca else models["catboost_spread_clf"]
    prob = clf.predict_proba(X)[:, 1]
    std7 = X["spread_std7"].fillna(99).values
    th = meta.get("decision_thresholds", {"prob_th": 0.5, "std_th": 120.0})
    decision = np.where((prob > th["prob_th"]) & (std7 <= th["std_th"]), "sell", "hold")
    label_map = {"sell": "卖", "hold": "观望"}
    direction = np.where(prob > 0.5, 1, -1)

    # 展示用回归（主模型键 lgbm_*；ELCA 键 *_q50）
    spr_q10 = models["spread_q0.1"].predict(X)
    spr_q50 = models["spread_q0.5"].predict(X)
    spr_q90 = models["spread_q0.9"].predict(X)
    da_pred = models.get("lgbm_da_q0.5", models.get("da_q50")).predict(X)
    rt_pred = models.get("lgbm_rtpd_q0.5", models.get("rtpd_q50")).predict(X)

    price_lk = state["price_lk"][node]
    has_actual = target_date in price_lk["da_price"].index
    da_act = rt_act = spr_act = acc = None
    if has_actual:
        da_act = [price_lk["da_price"].loc[target_date, h] for h in HOURS]
        rt_act = [price_lk["rtpd_price"].loc[target_date, h] for h in HOURS]
        spr_act = [price_lk["spread"].loc[target_date, h] for h in HOURS]
        acc = float((np.sign(np.array(spr_act)) == direction).mean())

    return {
        "ok": True,
        "node": node,
        "target_date": target_date.isoformat(),
        "hours": HOURS,
        "prob_sell": [float(x) for x in prob],
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
        return jsonify({"ok": False, "error": "不支持的节点（当前已训练：" + "、".join(NODES) + "）"})

    state = get_state()
    master = state["master"]
    min_date = master["date"].min()
    max_date = master["date"].max()
    if target_date < min_date:
        return jsonify({"ok": False, "error": "目标日期过早（无足够历史滞后）"})
    if target_date > max_date + timedelta(days=2):
        return jsonify({"ok": False, "error": f"目标日期超出可预测范围（数据截至 {max_date}，建议 ≤ {max_date + timedelta(days=2)}）"})

    try:
        result = predict_day(state, node, target_date)
    except Exception as e:
        return jsonify({"ok": False, "error": f"预测失败: {e}"})
    return jsonify(result)


if __name__ == "__main__":
    print("启动 CA-ISO 电价价差预测网页: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
