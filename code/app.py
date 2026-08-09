# -*- coding: utf-8 -*-
"""
Flask 本地网页后端：输入 节点 + 目标日期 -> 预测次日 24h DA/RTPD/spread + 买卖建议。

接口：
  GET  /                -> 渲染 templates/index.html
  POST /api/predict     -> {node, target_date} -> 预测 JSON（契约见 templates/index.html）

预测语义：target_date = 目标日（D+1），决策日 D = target_date - 1。
特征构造统一走 canonical.py 的单一特征构造函数 build_row_features()（与训练同源，
消除双实现）：滞后只使用 target_date-2 及更早（决策时点已知的最后一个整日完整交付日），
D+1 的 2DA 负荷为预报直接作特征；target_date 实际值仅作展示/回测，不作输入。

⚠️ 模型兼容：feature_cols.json 的 features 必须与 canonical.X_COLUMNS 一致。
旧模型（在旧 features.py 上训练的、含 t2m_next 等泄漏特征）无法直接使用——
Agent C 用 canonical 重建模型后本 app 自动可用；不匹配时返回明确错误。
"""
import os
import sys
import json
import joblib
from datetime import timedelta
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

# 确保能从任意 cwd 导入同目录的 canonical.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canonical import build_row_features, X_COLUMNS

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
MODELS = os.path.join(ROOT, "models")
MASTER_PATH = os.path.join(DATA, "master.csv")
FEAT_COLS_PATH = os.path.join(MODELS, "feature_cols.json")

MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA_NODE = "ELCAJNGT_7_N001"
NODES = MAIN_NODES + [ELCA_NODE]
HOURS = list(range(1, 25))

app = Flask(__name__)
STATE = None


def load_state():
    master = pd.read_csv(MASTER_PATH, parse_dates=["date"])
    # master.csv 2026-07-21~07-26 存在整行重复，去重（与 canonical.py 同源）
    master = master.drop_duplicates(subset=["node", "date", "hour"], keep="first")

    with open(FEAT_COLS_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    models = {name: joblib.load(os.path.join(MODELS, os.path.basename(p)))
              for name, p in meta["models"].items()}
    models_elca = {name: joblib.load(os.path.join(MODELS, os.path.basename(p)))
                   for name, p in meta.get("elca_models", {}).items()}
    return {"master": master, "meta": meta,
            "models": models, "models_elca": models_elca}


def get_state():
    global STATE
    if STATE is None:
        STATE = load_state()
    return STATE


def predict_day(state, node, target_date):
    meta = state["meta"]
    feat_cols = meta["features"]

    # 单一特征构造函数（canonical.py，与训练同源）
    X = build_row_features(state["master"], node, target_date)
    missing = [c for c in feat_cols if c not in X.columns]
    if missing:
        raise RuntimeError(
            "模型特征与 canonical schema 不匹配（缺失: %s）。请用 canonical 重建模型（Agent C）。"
            % ", ".join(missing))

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

    # 目标日实际值（仅展示/回测，不作特征输入）
    master = state["master"]
    sub = master[(master["node"] == node) & (master["date"] == pd.Timestamp(target_date))]
    has_actual = len(sub) > 0
    da_act = rt_act = spr_act = acc = None
    if has_actual:
        sub = sub.sort_values("hour").set_index("hour")
        da_act = [float(sub.loc[h, "da_price"]) if h in sub.index else None for h in HOURS]
        rt_act = [float(sub.loc[h, "rtpd_price"]) if h in sub.index else None for h in HOURS]
        spr_act = [float(d - r) if (d is not None and r is not None) else None
                   for d, r in zip(da_act, rt_act)]
        valid = [(s, di) for s, di in zip(spr_act, direction) if s is not None]
        if valid:
            acc = float(np.mean(np.sign([s for s, _ in valid]) == [di for _, di in valid]))

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
        "da_actual": da_act,
        "rtpd_actual": rt_act,
        "spread_actual": spr_act,
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
