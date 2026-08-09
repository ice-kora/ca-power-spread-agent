# -*- coding: utf-8 -*-
"""
特征工程：为 CA-ISO 电价价差预测构建特征表。

⚠️ DEPRECATED（2026-08-09）——本模块及其产物 features.parquet 仅保留作对比。
已确认缺陷：幽灵 hour=0 行（51300 行中 2052 行）、label 污染（NaN 被强制标负样本）、
日级特征错位（~96% NaN）、t2m_next/ssrd_next/wind100_next 疑似穿越。
正式数据层请使用 canonical.py（产出 canonical.parquet + feature_schema.json）。

输入：code/data/master.csv   （read_data.py 产物）
输出：code/data/features.parquet（若 pyarrow 不可用则退回 .csv）

业务语义（务必遵守，防泄漏）：
  每行样本 = 决策日 D 的某个 hour，目标是预测 D+1 同 hour 的 spread。
  决策时点 = D 日 10:00 PT 前（DAM Market Close / bid cutoff，官方 BPM），此时：
    - D 日当天的实际价格 / 实际负荷尚未公开；
    - 历史滞后特征只能取 D-1 及更早的实际值；
    - D+1 的预报值（官方日前负荷预报 load_2da、天气预报 t2m/ssrd/wind100）此时已可得，可作特征；
    - D 日当天的价格 / 负荷实际值严格禁用。
    （注：13:00 是 DA 结果发布 = label 可见时点，非 bid cutoff。）

实现方式：
  对每个 node 独立处理，把价格/负荷/天气列用 unstack('hour') 转成宽表
  （rows=date, cols=(col, hour)），shift(k) 沿 date 轴移动 k 天得到滞后/未来值，
  rolling(7) 在同 hour 上计算 7 天均值/波动，再 stack 回长表拼接。
"""
import os
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
MASTER_CSV = os.path.join(ROOT, "data", "master.csv")
OUT_PQ = os.path.join(ROOT, "data", "features.parquet")
OUT_CSV = os.path.join(ROOT, "data", "features.csv")

WIDE_COLS = ["da_price", "rtpd_price", "spread", "load_actual",
             "load_2da", "t2m", "ssrd", "wind100"]

# 输出列顺序（下游 train.py / evaluate.py / app.py 严格依赖，不要改动）
ORDER = [
    "node", "date", "hour", "split",
    "spread_next", "da_price_next", "rtpd_price_next",
    "da_lag1", "rtpd_lag1", "spread_lag1",
    "da_lag2", "rtpd_lag2", "spread_lag2",
    "da_lag7", "rtpd_lag7", "spread_lag7",
    "spread_mean7", "spread_std7",
    "spread_mean14", "spread_std14", "spread_mean30", "spread_std30",
    "load_actual_lag1", "load_actual_day_mean_lag1",
    "peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1",
    "spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
    "load_2da_next", "t2m_next", "ssrd_next", "wind100_next",
    "dow_next", "month_next", "is_holiday_next",
    "solar_flag", "load_peak_flag",
]

SPLIT_RANGES = {
    # train 扩至天气起始（2025-04-02），让模型见过更多市场状态（2DA 早期缺失由 NaN 处理）
    "train": (pd.Timestamp("2025-04-02"), pd.Timestamp("2025-12-31")),
    "val":   (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-05-31")),
    "test":  (pd.Timestamp("2026-06-01"), pd.Timestamp("2026-08-05")),
}
# ELCAJNGT（SP15）价格数据仅 2026-03-03 起，原 train 窗口内无样本，单独用其后数据切分
ELCA_SPLIT_RANGES = {
    "train": (pd.Timestamp("2026-03-03"), pd.Timestamp("2026-05-31")),
    "test":  (pd.Timestamp("2026-06-01"), pd.Timestamp("2026-08-05")),
}


def assign_split_elca(d):
    for name, (lo, hi) in ELCA_SPLIT_RANGES.items():
        if lo <= d <= hi:
            return name
    return np.nan


def assign_split(d):
    """按决策日 date 切分，范围外返回 NaN（作为 lag 的预热数据，不进建模集）。"""
    for name, (lo, hi) in SPLIT_RANGES.items():
        if lo <= d <= hi:
            return name
    return np.nan


def build_node(node_df, node_name, peer_df=None):
    """单节点特征构建。peer_df：同 zone 关联节点（用于节点间联动特征）。"""
    node_df = node_df.sort_values(["date", "hour"]).reset_index(drop=True)

    # 1) 转宽表：rows=date, cols=hour（对每列独立 unstack）
    wide = {}
    for col in WIDE_COLS:
        w = node_df.set_index(["date", "hour"])[col].unstack("hour").sort_index()
        wide[col] = w

    # 补齐连续日历日，确保 shift(k) == k 天、rolling 窗口按自然日对齐
    full_idx = pd.date_range(node_df["date"].min(), node_df["date"].max(), freq="D")
    wide = {c: w.reindex(full_idx) for c, w in wide.items()}

    # 1b) 同 zone 关联节点（peer）的价格宽表，用于节点间联动特征
    peer_wide = None
    if peer_df is not None:
        peer_df = peer_df.sort_values(["date", "hour"]).reset_index(drop=True)
        peer_wide = {}
        for col in ("da_price", "rtpd_price", "spread"):
            w = peer_df.set_index(["date", "hour"])[col].unstack("hour").sort_index().reindex(full_idx)
            peer_wide[col] = w

    # 2) 历史滞后（D-1 / D-2 / D-7 同 hour）
    feats = {}
    for k in (1, 2, 7):
        feats["da_lag%d" % k] = wide["da_price"].shift(k)
        feats["rtpd_lag%d" % k] = wide["rtpd_price"].shift(k)
        feats["spread_lag%d" % k] = wide["spread"].shift(k)
    feats["load_actual_lag1"] = wide["load_actual"].shift(1)
    feats["load_actual_day_mean_lag1"] = wide["load_actual"].mean(axis=1).shift(1)

    # 3) 均值/波动：D-1..D-7 / 14 / 30 日同 hour（rolling 后 shift(1)，剔除 D 当天）
    feats["spread_mean7"] = wide["spread"].rolling(7).mean().shift(1)
    feats["spread_std7"] = wide["spread"].rolling(7).std().shift(1)
    feats["spread_mean14"] = wide["spread"].rolling(14).mean().shift(1)
    feats["spread_std14"] = wide["spread"].rolling(14).std().shift(1)
    feats["spread_mean30"] = wide["spread"].rolling(30).mean().shift(1)
    feats["spread_std30"] = wide["spread"].rolling(30).std().shift(1)

    # 3b) D-1 日内形态（跨 24h 统计，shift(1) 取昨天）
    sw = wide["spread"]
    feats["spread_day_std_lag1"] = sw.std(axis=1).shift(1)
    feats["spread_day_range_lag1"] = (sw.max(axis=1) - sw.min(axis=1)).shift(1)
    feats["spread_day_max_lag1"] = sw.max(axis=1).shift(1)

    # 3c) 节点间联动：peer 的 D-1 同 hour 价格（同 zone 另一节点；无 peer 时为 NaN）
    if peer_wide is not None:
        feats["peer_spread_lag1"] = peer_wide["spread"].shift(1)
        feats["peer_da_lag1"] = peer_wide["da_price"].shift(1)
        feats["peer_rtpd_lag1"] = peer_wide["rtpd_price"].shift(1)
    else:
        for col in ("peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1"):
            feats[col] = pd.Series(np.nan, index=wide["spread"].index)

    # 4) 未来已知（D+1 预报值，决策时已可得）
    feats["spread_next"] = wide["spread"].shift(-1)
    feats["da_price_next"] = wide["da_price"].shift(-1)
    feats["rtpd_price_next"] = wide["rtpd_price"].shift(-1)
    for col in ("load_2da", "t2m", "ssrd", "wind100"):
        feats[col + "_next"] = wide[col].shift(-1)

    # 5) 决策特征：load_peak_flag（D+1 当天 load_2da 是否全天最大；全缺失 -> NaN）
    w2 = wide["load_2da"]
    day_max = w2.max(axis=1)                     # 每行(天)跨 24 小时的最大值
    peak = w2.eq(day_max, axis=0).where(day_max.notna(), np.nan).astype(float)
    feats["load_peak_flag"] = peak.shift(-1)

    # 6) 合并全部宽表特征并 stack 回长表
    combined = pd.concat(feats, axis=1)
    combined.columns.names = ["feat", "hour"]
    combined.index.name = "date"
    out = combined.stack("hour").reset_index()   # -> date, hour, feat...

    # 7) 日历（取 D+1 的属性）
    out["date_next"] = out["date"] + pd.Timedelta(days=1)
    out["dow_next"] = out["date_next"].dt.dayofweek
    out["month_next"] = out["date_next"].dt.month
    holidays = USFederalHolidayCalendar().holidays(start="2024-01-01", end="2027-01-01")
    out["is_holiday_next"] = out["date_next"].isin(holidays).astype(int)
    out["solar_flag"] = ((out["hour"] >= 10) & (out["hour"] <= 16)).astype(int)
    out = out.drop(columns=["date_next"])

    out["node"] = node_name
    return out


def load_master():
    m = pd.read_csv(MASTER_CSV, parse_dates=["date"])
    # master.csv 中 2026-07-21 ~ 2026-07-26 存在整行重复（576 行，已验证为全列相同副本），
    # 这里按 (node, date, hour) 去重，避免 unstack 时撞索引。
    n_before = len(m)
    m = m.drop_duplicates(subset=["node", "date", "hour"], keep="first")
    if len(m) != n_before:
        print("[info] master 去重：%d -> %d 行（重复 %d 行）" % (n_before, len(m), n_before - len(m)))
    return m


def build_features(master):
    nodes = sorted(master["node"].unique())
    # 同 zone 互为 peer（ZP26: CONTROLX <-> SNLNDRO），跨节点联动
    zone_of = master.drop_duplicates("node").set_index("node")["zone"].to_dict()
    peer_of = {}
    for n in nodes:
        peers = [m for m in nodes if m != n and zone_of.get(m) == zone_of.get(n)]
        peer_of[n] = peers[0] if peers else None

    frames = []
    for node in nodes:
        nd = master[master["node"] == node]
        pn = peer_of[node]
        peer_df = master[master["node"] == pn] if pn else None
        frames.append(build_node(nd, node, peer_df))
    df = pd.concat(frames, ignore_index=True)
    df["split"] = [assign_split_elca(d) if n == "ELCAJNGT_7_N001" else assign_split(d)
                   for n, d in zip(df["node"], df["date"])]
    df = df[ORDER].sort_values(["node", "date", "hour"]).reset_index(drop=True)
    return df


def save_features(df):
    """优先 parquet；pyarrow 缺失则 pip 安装后重试；仍失败退回 csv。"""
    try:
        df.to_parquet(OUT_PQ, index=False)
        print("saved ->", OUT_PQ)
        return "parquet"
    except Exception as e1:
        print("[warn] to_parquet failed: %s" % e1)
        try:
            import subprocess
            import sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pyarrow"])
            df.to_parquet(OUT_PQ, index=False)
            print("saved (after pip install pyarrow) ->", OUT_PQ)
            return "parquet"
        except Exception as e2:
            print("[warn] pip install pyarrow failed: %s" % e2)
            df.to_csv(OUT_CSV, index=False, encoding="utf-8")
            print("fallback saved ->", OUT_CSV)
            return "csv"


def verify(df, master):
    """防泄漏自检 + 一致性检查（打印并断言）。"""
    ok = True

    # 1) 特征中不得包含 D 日当天的价格列
    assert "da_price" not in df.columns, "features 不应包含当日 da_price"
    assert "rtpd_price" not in df.columns, "features 不应包含当日 rtpd_price"
    print("[check] 特征不含 D 日当日的 da_price / rtpd_price 列: PASS")

    # 2) 随机抽 5 行 train，验证 da_lag1 / spread_lag1 == 同 node 在 date-1 的原始值
    #    master 行 (node, D-1, hour) 是 feature 行 (node, D, hour) 的 lag1 来源：
    #    m["date_m1"] = master.date + 1 天，与 feature.date 对齐。
    m = master[["node", "date", "hour", "da_price", "spread"]].copy()
    m["date_m1"] = m["date"] + pd.Timedelta(days=1)
    tr = df[df["split"] == "train"]
    rng = np.random.default_rng(42)
    samp_idx = rng.choice(tr.index, size=min(5, len(tr)), replace=False)
    chk = tr.loc[samp_idx, ["node", "date", "hour", "da_lag1", "spread_lag1"]].merge(
        m[["node", "date_m1", "hour", "da_price", "spread"]],
        left_on=["node", "date", "hour"],
        right_on=["node", "date_m1", "hour"],
        how="left",
    )
    da_diff = (chk["da_lag1"] - chk["da_price"]).abs().max()
    sp_diff = (chk["spread_lag1"] - chk["spread"]).abs().max()
    print("[check] 防泄漏样本（node/date/hour/da_lag1/master da_price）:")
    print(chk[["node", "date", "hour", "da_lag1", "da_price", "spread_lag1", "spread"]].to_string(index=False))
    assert np.allclose(chk["da_lag1"], chk["da_price"], equal_nan=True), "da_lag1 错位!"
    assert np.allclose(chk["spread_lag1"], chk["spread"], equal_nan=True), "spread_lag1 错位!"
    print("[check] da_lag1 与 date-1 原始 da_price 最大偏差=%.3e" % da_diff)
    print("[check] spread_lag1 与 date-1 原始 spread 最大偏差=%.3e" % sp_diff)
    print("[check] 防泄漏 lag 断言: PASS")

    # 3) spread_next == da_price_next - rtpd_price_next
    mask = df["spread_next"].notna()
    rhs = df.loc[mask, "da_price_next"] - df.loc[mask, "rtpd_price_next"]
    diff = (df.loc[mask, "spread_next"] - rhs).abs().max()
    assert np.allclose(df.loc[mask, "spread_next"], rhs), "spread_next 与 da_price_next-rtpd_price_next 不一致!"
    print("[check] spread_next == da_price_next - rtpd_price_next（max abs diff=%.3e）: PASS" % diff)
    return ok


def main():
    print("== 读取 master.csv ==")
    master = load_master()
    print("master rows:", len(master))

    print("== 构建特征 ==")
    df = build_features(master)
    print("feature rows:", len(df), "cols:", len(df.columns))

    # 行数 / split 分布
    print("== split 分布（按决策日 date）==")
    cnt = df["split"].value_counts(dropna=False)
    for k in ["train", "val", "test", np.nan]:
        if k == np.nan:
            print("  (warm-up/范围外): %d" % int(cnt.get(np.nan, 0)))
        else:
            print("  %s: %d" % (k, int(cnt.get(k, 0))))

    # NaN 比例（只列 >0）
    print("== NaN 比例（仅 NaN>0 的列）==")
    nan_ratio = df.isna().mean()
    nan_ratio = nan_ratio[nan_ratio > 0].sort_values(ascending=False)
    for col, r in nan_ratio.items():
        print("  %-20s %.4f" % (col, r))

    # train 样本行样例
    print("== train 样本行样例 ==")
    sample = df[df["split"] == "train"].iloc[[0]]
    print(sample.T.to_string())

    print("== 防泄漏/一致性验证 ==")
    verify(df, master)

    print("== 保存 ==")
    kind = save_features(df)
    print("done. format =", kind)


if __name__ == "__main__":
    main()
