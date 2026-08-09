# -*- coding: utf-8 -*-
"""
agent_d_backtest.py —— Risk Gate 回测比较（Agent D，TEST 最终验证）v2
====================================================================
比较策略（严格 as-of，train→val→test 时间顺序）：
  All Trade[rule/interpretable/catboost]  / 原 Rule / Interpretable / CatBoost
  / Model Committee / Committee + Risk Gate
指标：trade coverage、accuracy、win rate、mean/median PnL、cumulative PnL、
      max drawdown、worst trade、downside deviation、CVaR(ES)、profit factor、Sharpe-like。

主策略 = ZP26（SNLNDRO + CONTROLX）；ELCA 单独评估。
TEST 只做验证，不参与任何阈值选择。
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
CANON_PQ = os.path.join(DATA, "canonical.parquet")
RF_PQ = os.path.join(STAGE3, "risk_features.parquet")
OUT_JSON = os.path.join(STAGE3, "risk_gate_backtest_metrics.json")

MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA = "ELCAJNGT_7_N001"


def load_all():
    canon = pd.read_parquet(CANON_PQ)
    rf = pd.read_parquet(RF_PQ).drop(columns=["split"])
    cf = canon[["node", "target_date", "hour", "actual_return"]].merge(
        rf, on=["node", "target_date", "hour"], how="left")
    preds = {}
    for name in ["rule", "interpretable", "catboost"]:
        p = pd.read_csv(os.path.join(DATA, "predictions_%s.csv" % name))
        p["target_date"] = pd.to_datetime(p["target_date"])
        preds[name] = p
    return cf, preds


def pnl_of(direction, ret):
    return np.where(direction > 0, ret, -ret)


def compute_metrics(d, label=""):
    """d 含 decision 与 pnl 列。coverage 分母 = d 的样本行数。"""
    traded = d[d["decision"] != "NO_TRADE"] if "decision" in d.columns else d
    n = len(traded)
    total = len(d)
    if n == 0:
        return {"label": label, "n_traded": 0, "trade_coverage": 0.0}
    p = traded["pnl"].astype(float)
    daily = traded.groupby("target_date")["pnl"].sum()
    full = pd.date_range(traded["target_date"].min(), traded["target_date"].max(), freq="D")
    daily = daily.reindex(full).fillna(0.0)
    cum = daily.cumsum()
    mdd = float((cum - cum.cummax()).min())
    daily_std = daily.std()
    sharpe = float(daily.mean() / daily_std * np.sqrt(252)) if daily_std and daily_std > 0 else np.nan
    dd = daily[daily < 0]
    ddev = float(np.sqrt((dd ** 2).mean())) if len(dd) else 0.0
    es5 = float(p[p <= p.quantile(0.05)].mean()) if (p <= p.quantile(0.05)).sum() else float(p.min())
    es1 = float(p[p <= p.quantile(0.01)].mean()) if (p <= p.quantile(0.01)).sum() else float(p.min())
    gross = p[p > 0].sum()
    gl = -p[p < 0].sum()
    pf = float(gross / gl) if gl > 0 else np.nan
    acc = np.nan
    if "pred_direction" in traded.columns:
        pd_ = pd.to_numeric(traded["pred_direction"], errors="coerce")
        m = pd_.notna() & (pd_ != 0)
        if m.sum():
            ad = pd.to_numeric(traded.get("actual_direction"), errors="coerce")
            if ad is not None and ad.notna().any():
                acc = float((np.sign(pd_[m]) == np.sign(ad[m])).mean())
    return {
        "label": label, "n_traded": n,
        "trade_coverage": round(n / total, 4),
        "dir_accuracy": round(acc, 4) if acc == acc else None,
        "win_rate": round(float((p > 0).mean()), 4),
        "mean_pnl": round(float(p.mean()), 2), "median_pnl": round(float(p.median()), 2),
        "cum_pnl": round(float(p.sum()), 1), "max_drawdown": round(mdd, 1),
        "worst_trade": round(float(p.min()), 1), "downside_dev": round(ddev, 2),
        "cvar_es5": round(es5, 1), "cvar_es1": round(es1, 1),
        "profit_factor": round(pf, 3) if pf == pf else None,
        "sharpe_daily": round(sharpe, 3) if sharpe == sharpe else None,
        "n_sell": int((traded["decision"] == "SELL_DA").sum()),
        "n_buy": int((traded["decision"] == "BUY_DA").sum()),
    }


def single_model_frame(pred, nodes=None):
    d = pred.copy()
    if nodes is not None:
        d = d[d["node"].isin(nodes)].copy()
    d["decision"] = np.where(d["pred_direction"] > 0, "SELL_DA",
                             np.where(d["pred_direction"] < 0, "BUY_DA", "NO_TRADE"))
    d["pnl"] = pnl_of(d["pred_direction"], d["actual_return"])
    d["actual_direction"] = np.sign(d["actual_return"])
    return d


def build_committee(preds, nodes):
    base = preds["rule"][["node", "target_date", "hour"]].copy()
    for name in ["rule", "interpretable", "catboost"]:
        base = base.merge(preds[name][["node", "target_date", "hour", "pred_direction"]],
                          on=["node", "target_date", "hour"], how="left")
        base = base.rename(columns={"pred_direction": "pred_direction_%s" % name})
    base = base[base["node"].isin(nodes)].copy()
    dirs = base[["pred_direction_rule", "pred_direction_interpretable",
                 "pred_direction_catboost"]].values
    dec, com_dir = [], []
    for d in dirs:
        nz = d[d != 0]
        if len(nz) >= 2:
            vals, cnts = np.unique(nz, return_counts=True)
            if cnts.max() >= 2:
                v = vals[cnts.argmax()]
                com_dir.append(v)
                dec.append("SELL_DA" if v > 0 else "BUY_DA")
                continue
        com_dir.append(0.0)
        dec.append("NO_TRADE")
    base["committee_dir"] = com_dir
    base["pred_direction"] = com_dir
    base["decision"] = dec
    base = base.merge(preds["rule"][["node", "target_date", "hour", "actual_return",
                                     "actual_direction"]],
                      on=["node", "target_date", "hour"], how="left")
    base["pnl"] = pnl_of(base["committee_dir"], base["actual_return"])
    return base


def attach_feats(d, cf):
    d = d.merge(cf[["node", "target_date", "hour", "cvar99", "rcvar99", "hist_n",
                    "hist_std", "vol_ratio", "lag1_pct", "node_drift"]],
                on=["node", "target_date", "hour"], how="left")
    return d


def apply_gate_frame(d, role):
    """role: 'single_rule' / 'single_ml' / 'committee' 决定 R2 如何用三模型方向。"""
    g = apply_gate(d)
    g["decision"] = np.where((g["decision"] != "NO_TRADE") & (g["gate_decision"] == "REJECT"),
                             "NO_TRADE", g["decision"])
    g["pnl"] = np.where(g["decision"] == "NO_TRADE", 0.0, g["pnl"])
    return g


def gate_single(pred, cf, name, nodes):
    d = single_model_frame(pred, nodes)
    d = attach_feats(d, cf)
    d = d.rename(columns={"pred_direction": "pred_direction_src"})
    d["pred_direction"] = d["pred_direction_src"]
    if name == "rule":
        d["rule_dir"] = d["pred_direction"]
        d["interpretable_dir"] = np.nan
        d["catboost_dir"] = np.nan
    else:
        d["rule_dir"] = np.nan
        d["interpretable_dir"] = d["pred_direction"]
        d["catboost_dir"] = d["pred_direction"]
    g = apply_gate_frame(d, "single_ml" if name != "rule" else "single_rule")
    return g


def gate_committee(committee, cf):
    c = committee.copy()
    c = attach_feats(c, cf)
    c = c.rename(columns={"pred_direction_rule": "rule_dir",
                          "pred_direction_interpretable": "interpretable_dir",
                          "pred_direction_catboost": "catboost_dir"})
    return apply_gate_frame(c, "committee")


def main():
    print("== agent_d_backtest v2: Risk Gate 回测比较（TEST） ==")
    cf, preds = load_all()

    rows_main, rows_elca = [], []

    for nodes, key in ((MAIN_NODES, "main"), ([ELCA], "elca")):
        node_cf = cf[cf["node"].isin(nodes)]
        node_preds = {k: v[v["node"].isin(nodes)].copy() for k, v in preds.items()}
        target_rows = rows_main if key == "main" else rows_elca

        # All Trade（每模型独立）
        for name, lab in (("rule", "All Trade[rule]"), ("interpretable", "All Trade[interpretable]"),
                          ("catboost", "All Trade[catboost]")):
            d = single_model_frame(node_preds[name])
            target_rows.append(compute_metrics(d, lab))
        # 原 Rule / Interpretable / CatBoost（决策 = 各模型自身，pred_direction=0 已含 NO_TRADE）
        for name, lab in (("rule", "原 Rule"), ("interpretable", "Interpretable"), ("catboost", "CatBoost")):
            d = single_model_frame(node_preds[name])
            target_rows.append(compute_metrics(d, lab))
            # Gate 版本
            g = gate_single(node_preds[name], node_cf, name, nodes)
            target_rows.append(compute_metrics(g, "%s + Risk Gate" % lab))
        # Committee
        committee = build_committee(node_preds, nodes)
        target_rows.append(compute_metrics(committee, "Model Committee"))
        com_g = gate_committee(committee, node_cf)
        target_rows.append(compute_metrics(com_g, "Committee + Risk Gate"))

    out = {"main": rows_main, "elca": rows_elca}
    print("\n===== 主策略（ZP26）=====")
    for r in rows_main:
        print(json.dumps(r, ensure_ascii=False))
    print("\n===== ELCA（单独评估）=====")
    for r in rows_elca:
        print(json.dumps(r, ensure_ascii=False))

    # gate 触发原因分布（committee, ZP26）
    committee = build_committee(node_preds := {k: v[v["node"].isin(MAIN_NODES)].copy() for k, v in preds.items()},
                                MAIN_NODES)
    com_g = gate_committee(committee, cf[cf["node"].isin(MAIN_NODES)])
    rej = com_g[com_g["gate_decision"] == "REJECT"]
    print("\n===== Committee + Gate 的 REJECT 原因分布（ZP26）=====")
    print(rej["reason_code"].value_counts().to_dict())
    out["committee_gate_reject_reasons"] = rej["reason_code"].value_counts().to_dict()
    out["committee_gate_reject_n"] = int(len(rej))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\nsaved ->", OUT_JSON)


if __name__ == "__main__":
    main()
