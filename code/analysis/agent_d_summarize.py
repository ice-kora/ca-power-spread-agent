# -*- coding: utf-8 -*-
"""agent_d_summarize.py —— 汇总 Risk Gate 全部结果，输出供报告使用的 JSON/CSV。"""
import os, json
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_d_backtest as AB

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
STAGE3 = os.path.join(DATA, "stage3")
OUT = os.path.join(STAGE3, "risk_gate_all_results.json")


def main():
    cf, preds = AB.load_all()
    rows_main, rows_elca = [], []
    for nodes, key in ((AB.MAIN_NODES, "main"), ([AB.ELCA], "elca")):
        node_cf = cf[cf["node"].isin(nodes)]
        node_preds = {k: v[v["node"].isin(nodes)].copy() for k, v in preds.items()}
        target_rows = rows_main if key == "main" else rows_elca
        for name, lab in (("rule", "All Trade[rule]"), ("interpretable", "All Trade[interpretable]"),
                          ("catboost", "All Trade[catboost]")):
            target_rows.append(AB.compute_metrics(AB.single_model_frame(node_preds[name]), lab))
        for name, lab in (("rule", "原 Rule"), ("interpretable", "Interpretable"), ("catboost", "CatBoost")):
            d = AB.single_model_frame(node_preds[name])
            target_rows.append(AB.compute_metrics(d, lab))
            g = AB.gate_single(node_preds[name], node_cf, name, nodes)
            target_rows.append(AB.compute_metrics(g, "%s + Risk Gate" % lab))
        committee = AB.build_committee(node_preds, nodes)
        target_rows.append(AB.compute_metrics(committee, "Model Committee"))
        com_g = AB.gate_committee(committee, node_cf)
        target_rows.append(AB.compute_metrics(com_g, "Committee + Risk Gate"))

    # gate 触发统计
    gate_stats = {}
    for nodes, key in ((AB.MAIN_NODES, "main"), ([AB.ELCA], "elca")):
        node_cf = cf[cf["node"].isin(nodes)]
        node_preds = {k: v[v["node"].isin(nodes)].copy() for k, v in preds.items()}
        committee = AB.build_committee(node_preds, nodes)
        com_g = AB.gate_committee(committee, node_cf)
        rej = com_g[com_g["gate_decision"] == "REJECT"]
        gate_stats[key] = {
            "committee_trades": int((committee["decision"] != "NO_TRADE").sum()),
            "kept_trades": int((com_g["decision"] != "NO_TRADE").sum()),
            "rejected": int(len(rej)),
            "reject_reasons": rej["reason_code"].value_counts().to_dict(),
            "warnings": com_g[com_g["gate_decision"] == "PASS_WITH_WARNING"]["warning_code"].value_counts().to_dict(),
        }

    # 保存
    out = {"main": rows_main, "elca": rows_elca, "gate_stats": gate_stats}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("saved ->", OUT)
    # 打印主表
    print("\n===== 主策略 ZP26 =====")
    for r in rows_main:
        print("%-28s n=%5d cov=%.3f cum=%10.1f mdd=%10.1f worst=%8.1f cvar1=%8.1f sharpe=%6.2f pf=%5.2f" % (
            r["label"], r["n_traded"], r["trade_coverage"], r["cum_pnl"], r["max_drawdown"],
            r["worst_trade"], r["cvar_es1"], r["sharpe_daily"], r["profit_factor"] or 0))
    print("\n===== ELCA =====")
    for r in rows_elca:
        if "cum_pnl" in r:
            print("%-28s n=%5d cov=%.3f cum=%10.1f mdd=%10.1f worst=%8.1f" % (
                r["label"], r["n_traded"], r["trade_coverage"], r["cum_pnl"], r["max_drawdown"], r["worst_trade"]))
    print("\n===== gate stats =====")
    print(json.dumps(gate_stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
