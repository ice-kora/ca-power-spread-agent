# -*- coding: utf-8 -*-
"""
agent_d_counterfactual.py —— Risk Gate 反事实分析（Agent D，TEST）
==================================================================
针对 Agent A 的 Top Loss 事件（code/data/stage3/top_loss_events.csv）：
  逐笔问：当时若启用 Risk Gate，是否被拦截？被哪条规则拦截？能避免多少亏损？
统计：Top20/50 prevented count、prevented loss amount、false rejection
      （误伤本来赚钱的交易）、因 Risk Gate 错过的 Top Profit 数量与金额、
      avoided loss vs missed profit 对比。
"""
import os, json
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_d_gate import apply_gate

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
STAGE3 = os.path.join(DATA, "stage3")
RF_PQ = os.path.join(STAGE3, "risk_features.parquet")
LOSS_CSV = os.path.join(STAGE3, "top_loss_events.csv")
PROFIT_CSV = os.path.join(STAGE3, "top_profit_events.csv")
OUT_JSON = os.path.join(STAGE3, "risk_gate_counterfactual.json")
OUT_CSV = os.path.join(STAGE3, "risk_gate_counterfactual_loss.csv")
OUT_PROFIT_CSV = os.path.join(STAGE3, "risk_gate_counterfactual_profit.csv")


def load_events():
    loss = pd.read_csv(LOSS_CSV)
    loss["target_date"] = pd.to_datetime(loss["target_date"])
    profit = pd.read_csv(PROFIT_CSV)
    profit["target_date"] = pd.to_datetime(profit["target_date"])
    rf = pd.read_parquet(RF_PQ).drop(columns=["split"])
    # 处理同名列冲突：保留 rf 的 as-of 风险特征（cvar99/rcvar99/hist_n/node_drift 等）
    for col in ["cvar99", "rcvar99", "hist_n", "hist_std", "vol_ratio", "lag1_pct",
                "node_drift", "node_hour_drift30", "hour_drift30", "node_vol30",
                "extreme_count_30", "vol7", "vol30", "hist_p1", "hist_p5", "hist_p50",
                "hist_p95", "hist_p99"]:
        if col in loss.columns:
            loss = loss.drop(columns=[col])
        if col in profit.columns:
            profit = profit.drop(columns=[col])
    loss = loss.merge(rf, on=["node", "target_date", "hour"], how="left")
    profit = profit.merge(rf, on=["node", "target_date", "hour"], how="left")
    return loss, profit


def classify_event(row, rf):
    """对单个事件判定 gate 结果。返回 (gate_decision, reason_code, node, dir)。"""
    # 方向：用 worst_model 的实际动作
    node = row["node"]
    action = str(row.get("action", "")).upper()
    if "BUY" in action:
        dir_ = -1
    elif "SELL" in action:
        dir_ = 1
    else:
        dir_ = 0
    # 组装三模型 dir（来自 CSV 列）
    def gd(c):
        v = row.get(c, np.nan)
        return v if pd.notna(v) else np.nan
    rule_dir = gd("rule_dir")
    interp_dir = gd("interpretable_dir")
    cat_dir = gd("catboost_dir")

    tmp = pd.DataFrame([{
        "node": node, "pred_direction": dir_,
        "rule_dir": rule_dir, "interpretable_dir": interp_dir, "catboost_dir": cat_dir,
        "cvar99": row.get("cvar99", np.nan), "rcvar99": row.get("rcvar99", np.nan),
        "hist_n": row.get("hist_n", np.nan), "hist_std": row.get("hist_std", np.nan),
        "vol_ratio": row.get("vol_ratio", np.nan), "lag1_pct": row.get("lag1_pct", np.nan),
        "node_drift": row.get("node_drift", np.nan),
    }])
    g = apply_gate(tmp)
    return g.iloc[0]["gate_decision"], g.iloc[0]["reason_code"]


def main():
    print("== agent_d_counterfactual: Risk Gate 反事实分析 ==")
    loss, profit = load_events()

    # ---------------- Top Loss 反事实 ----------------
    loss_res = []
    prevented = []
    for _, r in loss.iterrows():
        dec, reason = classify_event(r, None)
        loss_res.append({"index": int(r.name), "node": r["node"],
                         "target_date": str(r["target_date"].date()), "hour": int(r["hour"]),
                         "action": r["action"], "worst_pnl": round(float(r["worst_pnl"]), 1),
                         "type": r["type"], "gate_decision": dec, "reason_code": reason,
                         "prevented": (dec == "REJECT")})
        if dec == "REJECT":
            prevented.append(float(r["worst_pnl"]))
    loss_df = pd.DataFrame(loss_res)

    top50_prevented = int((loss_df["prevented"]).sum())
    top50_prevented_loss = round(float(-sum(prevented)), 1)   # 正数：避免的亏损金额
    top20 = loss_df.head(20)
    top20_prevented = int(top20["prevented"].sum())
    top20_prevented_loss = round(float(-top20[top20["prevented"]]["worst_pnl"].sum()), 1)

    # 按 Type 统计
    by_type = loss_df.groupby("type").apply(
        lambda g: pd.Series({"n": len(g), "prevented": int(g["prevented"].sum()),
                             "prevented_loss": round(float(-g[g["prevented"]]["worst_pnl"].sum()), 1)}),
        include_groups=False).to_dict()

    # ---------------- Top Profit 反事实（误伤检查） ----------------
    profit_res = []
    missed = []
    for _, r in profit.iterrows():
        dec, reason = classify_event(r, None)
        profit_res.append({"index": int(r.name), "node": r["node"],
                           "target_date": str(r["target_date"].date()), "hour": int(r["hour"]),
                           "action": r["action"], "pnl": round(float(r["pnl"]), 1),
                           "gate_decision": dec, "reason_code": reason,
                           "missed": (dec == "REJECT")})
        if dec == "REJECT":
            missed.append(float(r["pnl"]))
    profit_df = pd.DataFrame(profit_res)
    top50_missed = int(profit_df["missed"].sum())
    top50_missed_profit = round(float(sum(missed)), 1)
    top20_missed = int(profit_df.head(20)["missed"].sum())
    top20_missed_profit = round(float(profit_df.head(20)[profit_df.head(20)["missed"]]["pnl"].sum()), 1)

    # ---------------- 汇总 ----------------
    summary = {
        "top_loss": {
            "total_events": len(loss_df),
            "top50_prevented_count": top50_prevented,
            "top50_prevented_loss": top50_prevented_loss,
            "top20_prevented_count": top20_prevented,
            "top20_prevented_loss": top20_prevented_loss,
            "prevented_ratio": round(top50_prevented / len(loss_df), 4),
            "by_type": by_type,
        },
        "top_profit": {
            "total_events": len(profit_df),
            "top50_missed_count": top50_missed,
            "top50_missed_profit": top50_missed_profit,
            "top20_missed_count": top20_missed,
            "top20_missed_profit": top20_missed_profit,
            "missed_ratio": round(top50_missed / len(profit_df), 4),
        },
        "avoided_vs_missed": {
            "top50_avoided_loss": top50_prevented_loss,
            "top50_missed_profit": top50_missed_profit,
            "net": round(top50_prevented_loss - top50_missed_profit, 1),
        },    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ---------------- 落盘 ----------------
    loss_df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    profit_df.to_csv(OUT_PROFIT_CSV, index=False, encoding="utf-8-sig")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "loss_events": loss_res, "profit_events": profit_res},
                  f, ensure_ascii=False, indent=2, default=str)
    print("\nsaved ->", OUT_JSON)
    print("saved ->", OUT_CSV)
    print("saved ->", OUT_PROFIT_CSV)


if __name__ == "__main__":
    main()
