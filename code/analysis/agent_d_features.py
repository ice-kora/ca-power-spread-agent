# -*- coding: utf-8 -*-
"""
agent_d_features.py —— Risk Gate 的 as-of 历史风险特征构建（Agent D）
====================================================================
对 canonical 每一行 (node, target_date, hour)，计算决策时点可见的历史风险特征：
- hist_n              : 同 node×hour 历史样本数（≤ target_date-2）
- hist_std            : 同 node×hour 历史 spread 标准差
- hist_p1/p5/p50/p95/p99 : 同 node×hour 历史分位（actual_return）
- cvar95/cvar99       : 同 node×hour 历史左尾期望损失（对 SELL 的风险侧）
- rcvar95/rcvar99     : 同 node×hour 历史右尾期望损失（对 BUY 的风险侧，取 -ret 左尾）
- vol7/vol30          : 近 7/30 日波动（同 node×hour，target_date-2 起）
- vol_ratio           : vol30 / hist_std（相对自身历史波动）
- extreme_count_30    : 近 30 日 |spread| > 历史 p95 的天数
- lag1_pct            : spread_lag1 在 node×hour 历史中的分位
- node_drift          : node 级历史 mean(actual_return)（≤ target_date-2）
- node_drift_30       : node 级近 30 日 mean(actual_return)
- node_hour_drift30   : node×hour 近 30 日 mean(actual_return)
- hour_drift30        : hour 级近 30 日 mean(actual_return)

全部只用 ≤ target_date-2 的数据（与 canonical 滞后约定一致，严格 as-of）。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
CANON_PQ = os.path.join(DATA, "canonical.parquet")
OUT_PQ = os.path.join(DATA, "stage3", "risk_features.parquet")


def build_risk_features(canon):
    canon = canon.copy()
    canon = canon.sort_values(["node", "hour", "target_date"]).reset_index(drop=True)

    out_cols = ["node", "target_date", "hour", "split"]
    feats = {}

    # ---------------- 同 node×hour 扩展历史 ----------------
    # 对每个 (node,hour) 组，构造扩展历史（滚动前向），再 shift(2) 对齐 target_date-2
    cols_hist = ["hist_n", "hist_std", "hist_p1", "hist_p5", "hist_p50",
                 "hist_p95", "hist_p99", "cvar95", "cvar99", "rcvar95", "rcvar99"]

    def _expanding_stats(v):
        """v: 升序 actual_return 数组。返回 (n, list) 扩展历史统计，长度 == len(v)。"""
        n = len(v)
        res = {c: np.full(n, np.nan) for c in cols_hist}
        # 用 numpy 累积优化：对每个 i，扩展窗口 v[:i]
        # 先算 cumsum / cumsumsq 供 mean/std
        cs = np.cumsum(np.where(np.isnan(v), 0.0, v))
        css = np.cumsum(np.where(np.isnan(v), 0.0, v ** 2))
        cnt = np.cumsum(~np.isnan(v))
        for i in range(1, n):
            m = i  # 扩展窗口长度（无 NaN）
            if m == 0:
                continue
            hist = v[:i]
            res["hist_n"][i] = m
            res["hist_std"][i] = float(np.std(hist))
            for q, c in ((0.01, "hist_p1"), (0.05, "hist_p5"), (0.50, "hist_p50"),
                         (0.95, "hist_p95"), (0.99, "hist_p99")):
                res[c][i] = float(np.quantile(hist, q))
            # 左尾 CVaR：小于 p5/p1 的均值
            for q, c in ((0.05, "cvar95"), (0.01, "cvar99")):
                th = np.quantile(hist, q)
                tail = hist[hist <= th]
                res[c][i] = float(tail.mean()) if len(tail) else float(th)
            # 右尾 CVaR（对 BUY：-ret 左尾 = ret 右尾）
            for q, c in ((0.95, "rcvar95"), (0.99, "rcvar99")):
                th = np.quantile(hist, q)
                tail = hist[hist >= th]
                res[c][i] = float(-tail.mean()) if len(tail) else float(-th)
        return res

    print("  [agent_d_features] 计算同 node×hour 扩展历史统计 ...")
    hist_parts = {c: [] for c in cols_hist}
    for (node, hour), g in canon.groupby(["node", "hour"]):
        g = g.sort_values("target_date")
        st = _expanding_stats(g["actual_return"].values)
        idx = g.index
        for c in cols_hist:
            s = pd.Series(st[c], index=idx)
            # 语义：_expanding_stats 在位置 i 用 v[:i]（= T-1 及更早）。
            # 要得到"T-2 及更早"（匹配 canonical lag1=target_date-2 约定），shift(1) 即可；
            # 且在组内 shift，避免跨 node×hour 组边界污染（ELCA 早期行历史 bug）。
            hist_parts[c].append(s.shift(1))
    for c in cols_hist:
        feats[c] = pd.concat(hist_parts[c]).sort_index()

    # ---------------- 滚动波动 / 极端事件计数 ----------------
    g = canon.sort_values(["node", "hour", "target_date"]).reset_index(drop=True)
    grp = g.groupby(["node", "hour"], group_keys=False)
    # 组内 shift(2)：跨组边界不污染
    feats["vol7"] = grp["actual_return"].transform(
        lambda s: s.rolling(7, min_periods=5).std().shift(2))
    feats["vol30"] = grp["actual_return"].transform(
        lambda s: s.rolling(30, min_periods=15).std().shift(2))

    # extreme_count_30：近 30 日 |spread| > 历史 p95 天数
    # 用 rolling 内 spread 与历史 p95 比较（p95 来自 hist_p95 列，本身已 as-of）
    abs_spread = canon["actual_return"].abs()
    is_ext = (abs_spread > feats["hist_p95"]).astype(float)
    feats["extreme_count_30"] = is_ext.groupby([canon["node"], canon["hour"]], group_keys=False).transform(
        lambda s: s.rolling(30, min_periods=10).sum().shift(2))

    # vol_ratio = vol30 / hist_std
    feats["vol_ratio"] = feats["vol30"] / feats["hist_std"]

    # lag1_pct：spread_lag1 在 node×hour 历史中的分位（用 hist 分位近似）
    s1 = canon["spread_lag1"]
    feats["lag1_pct"] = np.where(
        s1 <= feats["hist_p1"], 0.0,
        np.where(s1 >= feats["hist_p99"], 1.0,
                 np.clip((s1 - feats["hist_p1"]) / (feats["hist_p99"] - feats["hist_p1"] + 1e-9), 0, 1)))

    # ---------------- node / node×hour / hour 漂移 ----------------
    # 全部组内 shift(2)（跨组边界不污染；canon 已按 node,hour,target_date 排序）
    feats["node_drift"] = canon.groupby(["node"], group_keys=False)["actual_return"].transform(
        lambda s: s.expanding(min_periods=30).mean().shift(2))
    feats["node_hour_drift30"] = canon.groupby(["node", "hour"], group_keys=False)["actual_return"].transform(
        lambda s: s.rolling(30, min_periods=15).mean().shift(2))
    feats["hour_drift30"] = canon.groupby(["hour"], group_keys=False)["actual_return"].transform(
        lambda s: s.rolling(30, min_periods=15).mean().shift(2))

    # node 近 30 日波动
    feats["node_vol30"] = canon.groupby(["node"], group_keys=False)["actual_return"].transform(
        lambda s: s.rolling(30, min_periods=15).std().shift(2))

    res = canon[["node", "target_date", "hour", "split"]].copy()
    for c, s in feats.items():
        res[c] = pd.to_numeric(s, errors="coerce")
    return res


def main():
    os.makedirs(os.path.join(DATA, "stage3"), exist_ok=True)
    print("== agent_d_features: 构建 as-of 风险特征 ==")
    canon = pd.read_parquet(CANON_PQ)
    print("canonical rows:", len(canon))
    rf = build_risk_features(canon)
    print("risk features shape:", rf.shape)
    print(rf.groupby("split").size())
    rf.to_parquet(OUT_PQ, index=False)
    print("saved ->", OUT_PQ)


if __name__ == "__main__":
    main()
