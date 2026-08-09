# -*- coding: utf-8 -*-
"""
agent_d_validate_tv.py —— 在 TRAIN+VAL 上验证 Risk Gate 效果（Agent D）
====================================================================
把 gate 应用到 train+val 的候选集（Rule 重建 + LR 代理 + 无条件市场），
检查：是否降低尾部/回撤、coverage 保留多少。TEST 不参与。
"""
import os, sys, json
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from backtest import build_rule_predictions, build_lr_predictions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_d_gate import apply_gate

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
CANON_PQ = os.path.join(DATA, "canonical.parquet")
RF_PQ = os.path.join(DATA, "stage3", "risk_features.parquet")
MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA = "ELCAJNGT_7_N001"


def load_canon():
    canon = pd.read_parquet(CANON_PQ)
    rf = pd.read_parquet(RF_PQ).drop(columns=["split"])
    return canon.merge(rf, on=["node", "target_date", "hour"], how="left")


def metrics(d):
    if len(d) == 0:
        return dict(n=0)
    p = d["pnl"].astype(float)
    daily = d.groupby("target_date")["pnl"].sum()
    full = pd.date_range(d["target_date"].min(), d["target_date"].max(), freq="D")
    daily = daily.reindex(full).fillna(0)
    cum = daily.cumsum()
    mdd = float((cum - cum.cummax()).min())
    q99 = p.quantile(0.01)
    cvar = float(p[p <= q99].mean()) if (p <= q99).sum() else float(p.min())
    return {
        "n": len(d),
        "cum_pnl": round(float(p.sum()), 1),
        "mean_pnl": round(float(p.mean()), 2),
        "win_rate": round(float((p > 0).mean()), 4),
        "max_loss": round(float(p.min()), 1),
        "cvar99": round(cvar, 1),
        "n_loss_gt500": int((p < -500).sum()),
        "n_loss_gt1000": int((p < -1000).sum()),
        "max_drawdown": round(mdd, 1),
    }


def main():
    print("== agent_d_validate_tv: gate 在 train+val 上的验证 ==")
    canon = load_canon()

    rule_tv = build_rule_predictions(canon, MAIN_NODES, split_filter=("train", "val"))
    rule_tv = rule_tv.drop(columns=["actual_return"], errors="ignore")
    cf = canon[["node", "target_date", "hour", "split", "actual_return",
                "cvar99", "rcvar99", "hist_n", "spread_lag1", "spread_mean14"]]
    rule_tv = rule_tv.merge(cf, on=["node", "target_date", "hour"], how="left")
    rule_tv["pnl"] = np.where(rule_tv["pred_direction"] > 0, rule_tv["actual_return"],
                              -rule_tv["actual_return"])
    # 为 gate 提供三模型 dir 列：Rule 重建的 rule_dir；ML 侧用 LR 代理
    lr_val = build_lr_predictions(canon, MAIN_NODES, split_filter=("val",))
    lr_val = lr_val.drop(columns=["actual_return"], errors="ignore")
    lr_val = lr_val.merge(canon[["node", "target_date", "hour", "actual_return",
                                 "cvar99", "rcvar99", "hist_n"]],
                          on=["node", "target_date", "hour"], how="left")
    lr_val["pnl"] = np.where(lr_val["pred_direction"] > 0, lr_val["actual_return"],
                             -lr_val["actual_return"])

    # Rule 候选：gate 判定（rule_dir = rule, interp/cat 列设为 NaN → R2 不误伤 Rule）
    rule_g = rule_tv.copy()
    rule_g["rule_dir"] = rule_g["pred_direction"]
    rule_g["interpretable_dir"] = np.nan
    rule_g["catboost_dir"] = np.nan
    rule_g = apply_gate(rule_g)

    # LR 候选：gate 判定（interp/cat 列都用 LR → R2 生效）
    lr_g = lr_val.copy()
    lr_g["rule_dir"] = np.nan
    lr_g["interpretable_dir"] = lr_g["pred_direction"]
    lr_g["catboost_dir"] = lr_g["pred_direction"]
    lr_g = apply_gate(lr_g)

    print("\n--- Rule 候选（train+val, ZP26）gate 前后 ---")
    m_before = metrics(rule_g)
    m_after = metrics(rule_g[rule_g["gate_decision"] != "REJECT"])
    print("before:", json.dumps(m_before, ensure_ascii=False))
    print("after :", json.dumps(m_after, ensure_ascii=False))
    rej = rule_g[rule_g["gate_decision"] == "REJECT"]
    print("rejected:", len(rej), "of", len(rule_g), "coverage_frac=%.3f" % (len(rej)/len(rule_g)))
    print("reject by reason:", rej["reason_code"].value_counts().head(8).to_dict())

    print("\n--- LR 代理候选（val, ZP26）gate 前后 ---")
    mb = metrics(lr_g)
    ma = metrics(lr_g[lr_g["gate_decision"] != "REJECT"])
    print("before:", json.dumps(mb, ensure_ascii=False))
    print("after :", json.dumps(ma, ensure_ascii=False))
    rej2 = lr_g[lr_g["gate_decision"] == "REJECT"]
    print("rejected:", len(rej2), "of", len(lr_g), "coverage_frac=%.3f" % (len(rej2)/len(lr_g)))
    print("reject by reason:", rej2["reason_code"].value_counts().head(8).to_dict())

    # 无条件市场验证：全 SELL / 全 BUY 在 train+val 的 tail（R7 依据）
    print("\n--- 无条件方向在 train+val 的 tail（R7 依据） ---")
    tv = canon[canon["split"].isin(["train", "val"])]
    for node in MAIN_NODES + [ELCA]:
        g = tv[tv["node"] == node]
        buy = -g["actual_return"]
        sell = g["actual_return"]
        print("%s: BUY mean=%.2f cvar99=%.0f maxloss=%.0f | SELL mean=%.2f cvar99=%.0f maxloss=%.0f"
              % (node, buy.mean(), buy[buy <= buy.quantile(0.01)].mean(),
                 buy.min(), sell.mean(), sell[sell <= sell.quantile(0.01)].mean(), sell.min()))

    # 保存结果
    out = {
        "rule_before": m_before, "rule_after": m_after, "rule_rejected": len(rej),
        "rule_total": len(rule_g),
        "lr_before": mb, "lr_after": ma, "lr_rejected": len(rej2), "lr_total": len(lr_g),
    }
    with open(os.path.join(DATA, "stage3", "risk_gate_validate_tv.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\nsaved ->", os.path.join(DATA, "stage3", "risk_gate_validate_tv.json"))


if __name__ == "__main__":
    main()
