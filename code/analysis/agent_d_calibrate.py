# -*- coding: utf-8 -*-
"""
agent_d_calibrate.py —— Risk Gate 规则阈值校准（Agent D，TRAIN+VAL only）
=====================================================================
用 train+val 的候选交易（Rule 重建 + LR walk-forward 代理）校准风险门规则，
test 不参与。输出阈值决策 JSON 供回测/反事实脚本使用。

规则：
  R3 Volatility Gate   : vol_ratio 阈值（可能删除——验证是否有效区分尾部）
  R4 Tail Risk Gate    : node×hour 历史 CVaR99（对 SELL 看左尾，对 BUY 看右尾）
  R5 Expected Edge     : |expected_return| 阈值
  R6 Sample Support    : hist_n 阈值（ELCA cold-start）
  R7 BUY-direction     : 正漂移节点上 BUY 的历史表现（验证是否成立）
  R2 Agreement Gate    : 在 train+val 用 Rule+LR 代理验证"冲突/双 ML BUY"逻辑
"""
import os, json
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from backtest import build_rule_predictions, build_lr_predictions

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA = os.path.join(ROOT, "data")
STAGE3 = os.path.join(DATA, "stage3")
CANON_PQ = os.path.join(DATA, "canonical.parquet")
RF_PQ = os.path.join(STAGE3, "risk_features.parquet")
OUT_JSON = os.path.join(STAGE3, "risk_gate_calibration.json")

MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA = "ELCAJNGT_7_N001"


def load_merged(split_filter=("train", "val")):
    canon = pd.read_parquet(CANON_PQ)
    rf = pd.read_parquet(RF_PQ).drop(columns=["split"])
    canon = canon.merge(rf, on=["node", "target_date", "hour"], how="left")
    return canon


def pnl_from_dir(dir_, ret):
    """direction: +1 SELL, -1 BUY。PnL = SELL:+ret, BUY:-ret。"""
    return np.where(dir_ > 0, ret, -ret)


def trade_metrics(d, label=""):
    """对候选交易帧计算风险指标（tail 视角）。d 需含 pnl 列。"""
    if len(d) == 0:
        return {"label": label, "n": 0, "coverage": 0.0, "cum_pnl": 0.0,
                "mean_pnl": np.nan, "win_rate": np.nan, "max_loss": 0.0,
                "p1": 0.0, "cvar99": 0.0, "n_losers_gt300": 0, "n_losers_gt500": 0}
    p = d["pnl"].astype(float)
    n = len(d)
    cov = n  # 传入的已是交易子集
    cvar99 = float(p[p <= p.quantile(0.01)].mean()) if (p <= p.quantile(0.01)).sum() >= 1 else float(p.min())
    return {
        "label": label, "n": n, "coverage": float(n),
        "cum_pnl": round(float(p.sum()), 1), "mean_pnl": round(float(p.mean()), 2),
        "median_pnl": round(float(p.median()), 2), "win_rate": round(float((p > 0).mean()), 4),
        "max_loss": round(float(p.min()), 1), "p1": round(float(p.quantile(0.01)), 1),
        "cvar99": round(cvar99, 1),
        "n_loss_gt300": int((p < -300).sum()), "n_loss_gt500": int((p < -500).sum()),
        "n_loss_gt1000": int((p < -1000).sum()),
    }


def scan_threshold(df, feature, direction, th_grid, direction_filter=None, node_filter=None,
                   metric="cvar99", min_cov_frac=0.30):
    """在 df（候选交易，含 pnl）上扫描 feature 阈值。
    direction_filter: 只在这些方向（+1 SELL / -1 BUY）上评估。
    返回按 (尾部指标改善, 覆盖率) 的阈值列表。
    """
    d = df.copy()
    if node_filter is not None:
        d = d[d["node"] == node_filter]
    if direction_filter is not None:
        d = d[np.sign(d["pred_direction"]) == direction_filter]
    if len(d) == 0:
        return []
    base = trade_metrics(d, "base")
    rows = []
    for th in th_grid:
        if direction_filter == 1:   # SELL：尾部来自 ret 左尾
            keep = d[feature] >= th   # 值越大越保守? 对 cvar99(负) 用 <=
        elif direction_filter == -1:  # BUY
            keep = d[feature] >= th
        else:
            keep = d[feature] >= th
        m = trade_metrics(keep, "th=%s" % th)
        cov_frac = m["n"] / base["n"]
        rows.append({"threshold": th, "n": m["n"], "cov_frac": round(cov_frac, 3),
                     "cum_pnl": m["cum_pnl"], "mean_pnl": m["mean_pnl"],
                     "max_loss": m["max_loss"], "cvar99": m["cvar99"],
                     "n_loss_gt500": m["n_loss_gt500"]})
    return rows


def main():
    print("== agent_d_calibrate: 规则阈值校准（TRAIN+VAL） ==")
    canon = load_merged()

    canon_feats = canon[["node", "target_date", "hour",
                         "actual_return", "spread_lag1", "spread_mean14", "spread_std7",
                         "hist_n", "hist_std", "cvar95", "cvar99", "rcvar95", "rcvar99",
                         "vol7", "vol30", "vol_ratio", "extreme_count_30", "lag1_pct",
                         "node_drift", "node_hour_drift30", "hour_drift30", "node_vol30"]]

    # ---------------- 候选集构建 ----------------
    # Rule 重建（主候选，as-of）
    rule_tv = build_rule_predictions(canon, MAIN_NODES, split_filter=("train", "val"))
    rule_tv = rule_tv.drop(columns=["actual_return"], errors="ignore")
    rule_tv = rule_tv.merge(canon_feats, on=["node", "target_date", "hour"], how="left")
    rule_tv["pnl"] = pnl_from_dir(rule_tv["pred_direction"], rule_tv["actual_return"])

    # LR walk-forward 代理（ML-like，BUY 侧），val 上
    lr_val = build_lr_predictions(canon, MAIN_NODES, split_filter=("val",))
    lr_val = lr_val.drop(columns=["actual_return"], errors="ignore")
    lr_val = lr_val.merge(canon_feats, on=["node", "target_date", "hour"], how="left")
    lr_val["pnl"] = pnl_from_dir(lr_val["pred_direction"], lr_val["actual_return"])

    out = {}

    # ---------------- R7 BUY-direction 验证 ----------------
    print("\n== R7 BUY-direction（train+val 市场结构） ==")
    r7 = {}
    tv_only = canon[canon["split"].isin(["train", "val"])]
    for node in MAIN_NODES + [ELCA]:
        g = tv_only[tv_only["node"] == node]
        drift = float(g["actual_return"].mean())
        # BUY 无条件历史 PnL
        buy_pnl = -g["actual_return"]
        sell_pnl = g["actual_return"]
        r7[node] = {
            "node_drift_trainval": round(drift, 2),
            "BUY_uncond_mean": round(float(buy_pnl.mean()), 2),
            "BUY_uncond_maxloss": round(float(buy_pnl.min()), 1),
            "BUY_uncond_cvar99": round(float(buy_pnl[buy_pnl <= buy_pnl.quantile(0.01)].mean()), 1),
            "SELL_uncond_mean": round(float(sell_pnl.mean()), 2),
            "SELL_uncond_maxloss": round(float(sell_pnl.min()), 1),
            "SELL_uncond_cvar99": round(float(sell_pnl[sell_pnl <= sell_pnl.quantile(0.01)].mean()), 1),
            "n": len(g),
        }
        print(json.dumps(r7[node], ensure_ascii=False))
    out["r7_market_structure"] = r7

    # CONTROLX BUY 历史（LR 代理在 val 上的 BUY 表现）
    lr_buy = lr_val[(lr_val["node"] == "CONTROLX_1_N001") & (lr_val["pred_direction"] < 0)]
    print("\nLR代理 CONTROLX BUY on val: n=%d cum=%.0f mean=%.2f maxloss=%.0f cvar99=%.0f"
          % (len(lr_buy), lr_buy["pnl"].sum(), lr_buy["pnl"].mean(),
             lr_buy["pnl"].min(),
             lr_buy["pnl"][lr_buy["pnl"] <= lr_buy["pnl"].quantile(0.01)].mean() if len(lr_buy) else np.nan))
    out["r7_lr_proxy_controlx_buy_val"] = trade_metrics(lr_buy, "LR_BUY_CONTROLX")

    # ---------------- R4 Tail Risk（CVaR99 阈值扫描） ----------------
    print("\n== R4 Tail Risk 阈值扫描（train+val） ==")
    r4 = {}
    # SELL：尾部来自 actual_return 左尾 -> 用 cvar99（历史左尾）。keep 条件：cvar99 > th（即左尾不深于阈值）
    # cvar99 为负，th 取负值（如 -400）：cvar99 > -400 保留
    for node in MAIN_NODES:
        d = rule_tv[rule_tv["node"] == node]
        sell = d[d["pred_direction"] > 0]
        rows = []
        for th in [np.nan, -800, -700, -600, -500, -450, -400, -350, -300, -250, -200]:
            if np.isnan(th):
                keep = sell
            else:
                keep = sell[sell["cvar99"] > th]
            m = trade_metrics(keep, "cvar99>%s" % th)
            rows.append({"th": (None if np.isnan(th) else th), "n": m["n"],
                         "cov": round(m["n"] / len(sell), 3) if len(sell) else None,
                         "cum": m["cum_pnl"], "mean": m["mean_pnl"],
                         "max_loss": m["max_loss"], "cvar99": m["cvar99"],
                         "n_loss_gt500": m["n_loss_gt500"]})
        r4[node] = {"side": "SELL", "scan": rows}
        print(node, "SELL:", json.dumps(rows, ensure_ascii=False))
    out["r4_seed_scan"] = r4

    # ---------------- R5 Expected Edge 阈值扫描（Rule 的 expected_return） ----------------
    print("\n== R5 Expected Edge（|expected_return| 阈值） ==")
    r5 = {}
    for node in MAIN_NODES:
        d = rule_tv[rule_tv["node"] == node]
        rows = []
        for th in [0, 5, 10, 20, 30, 40, 50, 75, 100, 150]:
            keep = d[d["expected_return"].abs() >= th]
            m = trade_metrics(keep, "edge>=%s" % th)
            rows.append({"th": th, "n": m["n"],
                         "cov": round(m["n"] / len(d), 3), "cum": m["cum_pnl"],
                         "mean": m["mean_pnl"], "max_loss": m["max_loss"], "cvar99": m["cvar99"]})
        r5[node] = rows
        print(node, json.dumps(rows, ensure_ascii=False))
    out["r5_scan"] = r5

    # ---------------- R6 Sample Support ----------------
    print("\n== R6 Sample Support（hist_n） ==")
    r6 = canon.groupby("node")["hist_n"].agg(["min", "mean", "max"]).round(0)
    print(r6.to_string())
    out["r6_hist_n_by_node"] = r6.to_dict()

    # ---------------- R3 Volatility（验证是否有效区分尾部） ----------------
    print("\n== R3 Volatility（vol_ratio 分层，train+val） ==")
    r3 = {}
    for node in MAIN_NODES:
        d = rule_tv[rule_tv["node"] == node]
        # 分层看 vol_ratio 各段的尾部
        bins = [0, 1.0, 1.5, 2.0, 3.0, 1e9]
        labels = ["<1.0", "1.0-1.5", "1.5-2.0", "2.0-3.0", ">3.0"]
        d = d.copy()
        d["vb"] = pd.cut(d["vol_ratio"], bins=bins, labels=labels, right=False)
        rows = []
        for lab, g in d.groupby("vb", observed=True):
            if len(g) == 0:
                continue
            m = trade_metrics(g, lab)
            rows.append({"vol_ratio": str(lab), "n": m["n"], "mean": m["mean_pnl"],
                         "max_loss": m["max_loss"], "cvar99": m["cvar99"],
                         "n_loss_gt500": m["n_loss_gt500"]})
        r3[node] = rows
        print(node, json.dumps(rows, ensure_ascii=False))
    out["r3_vol_ratio_layers"] = r3

    # ---------------- R2 Agreement（Rule + LR 代理，val） ----------------
    print("\n== R2 Agreement（Rule+LR 代理，val） ==")
    r2 = {}
    rr = rule_tv[rule_tv["split"] == "val"].copy()
    rr = rr[["node", "target_date", "hour", "pred_direction", "pnl"]].rename(
        columns={"pred_direction": "rule_dir", "pnl": "rule_pnl"})
    mm = lr_val[lr_val["split"] == "val"].copy()
    mm = mm[["node", "target_date", "hour", "pred_direction", "pnl"]].rename(
        columns={"pred_direction": "lr_dir", "pnl": "lr_pnl"})
    ag = rr.merge(mm, on=["node", "target_date", "hour"], how="inner")
    # 一致/冲突
    ag["agree"] = np.where(ag["rule_dir"] == ag["lr_dir"], "agree", "conflict")
    # 参考 PnL = Rule 自身交易（冲突时若强行交易 rule 方向）
    ag["pnl"] = ag["rule_pnl"]
    for k, g in ag.groupby("agree"):
        r2[k] = trade_metrics(g, k)
        print(k, json.dumps(r2[k], ensure_ascii=False))
    # 双 BUY（两模型都 BUY） vs 其余
    dbl = ag[(ag["rule_dir"] < 0) & (ag["lr_dir"] < 0)]
    r2["both_BUY"] = trade_metrics(dbl, "both_BUY")
    print("both_BUY:", json.dumps(r2["both_BUY"], ensure_ascii=False))
    r2["n_agree"] = int((ag["agree"] == "agree").sum())
    r2["n_conflict"] = int((ag["agree"] == "conflict").sum())
    out["r2_rule_lr_proxy_val"] = r2

    # ---------------- R1 Confidence（验证是否有效） ----------------
    print("\n== R1 Confidence（train+val Rule 置信度分层） ==")
    r1 = {}
    d = rule_tv.copy()
    d["cb"] = pd.cut(d["confidence"], bins=[0, 0.3, 0.5, 0.7, 1.0], labels=["<0.3", "0.3-0.5", "0.5-0.7", ">0.7"])
    for lab, g in d.groupby("cb", observed=True):
        r1[str(lab)] = trade_metrics(g, str(lab))
        print(lab, json.dumps(r1[str(lab)], ensure_ascii=False))
    out["r1_rule_confidence_layers"] = r1

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\nsaved ->", OUT_JSON)


if __name__ == "__main__":
    main()
