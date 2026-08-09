# -*- coding: utf-8 -*-
"""
canonical.py —— CA-ISO 价差项目 Canonical Dataset 重建（无泄漏数据层）
=====================================================================

背景
----
本模块是数据/特征的**唯一实现**，取代 features.py（已标记 deprecated，其产物
features.parquet 保留作对比）。修复 2026-08-07 审计确认的全部数据问题：

    1) 幽灵 hour=0 行（日级特征与小时宽表混拼产生）  -> 每 (node,target_date) 严格 24 行，
       日级特征正确广播到 24 小时；
    2) label 污染（NaN spread_next 被 (NaN>0)=0 标成负样本）
        -> label 只从真实存在的 DA/RTPD/Return 创建，目标 NaN 的行不进数据集；
    3) 日级特征错位（spread_day_std/range/max、load_actual_day_mean 只在单个 hour 有值）
        -> 正确广播到 24 小时；
    4) lag/rolling 严格只看过去（shift 以 target_date 为锚，见下方日期约定）；
    5) 严格按时间切分（decision_date 落在 train/val/test 区间，无随机 split）；
    6) 单一特征构造函数 canonical.build_row_features()，app.py 与 train 复用（消除双实现）；
    7) D+1 的实际 DA / RTPD / Return / 实际负荷 / 实际天气 只存在于 label 区，绝不进特征 X；
    8) Leakage Guard：每个 X 特征标注 available_at，须 <= decision_cutoff 才放行；
    9) *_next 天气特征（t2m_next / ssrd_next / wind100_next）默认禁用
        （UNKNOWN，疑似实测/再分析而非决策时可得预报，见 DISABLED_FEATURES）。

日期约定（与业务契约一致）
--------------------------
    行   = (node, target_date, hour)
    决策日 decision_date = target_date - 1
    决策截止 decision_cutoff = decision_date 10:00（Day-Ahead bid cutoff，契约冻结）

历史特征以"交付日"对齐（delivery day = target_date - k）：
    lag1        -> target_date - 2  （决策时已知的最后一个"整日完整"交付日：DA(target_date-1)
                    虽已于 target_date-2 13:00 出清（DA 结果发布，非 bid cutoff），但
                    RTPD(target_date-1) 要到决策日深夜才完整，故对称起见滞后从
                    target_date-2 起，宁保守不泄漏）
    lag2        -> target_date - 3
    lag7        -> target_date - 8
    rolling(w)  -> 同 hour 上 target_date-2 .. target_date-(w+1) 共 w 天
    日级统计    -> target_date-2 当天的 24 小时
    load_2da_forecast -> target_date 当天（日前负荷预测；ASSUMED 决策时可得，schema 标注）

输出
----
    code/data/canonical.parquet   一行 = 一个 (node, target_date, hour)，X + label + 标识
    code/data/feature_schema.json X 列 / label 列 / 标识 / 禁用特征 / 可用性矩阵 / 版本

本模块不训练模型、不写回测；下游 Agent C（建模）/ Agent D（回测）消费。
"""
import os
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

# 保证直接 `python code/canonical.py` 也能导入 code.*（可用性语义单一来源）
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from code.data_acquisition.schemas import (  # noqa: E402
    AVAILABILITY_BASIS_ASSUMED_AVAILABLE,
    AVAILABILITY_BASIS_KEY,
    AVAILABILITY_BASIS_STATIC,
    AVAILABILITY_BASIS_STRUCTURAL_LAG,
    BOUND_RULE_DECISION_DATE_00_PT,
    HAS_PRECISE_PUBLISH_TIME_KEY,
    LATEST_POSSIBLE_AVAILABLE_AT_KEY,
    SOURCE_TARGET_DATE_KEY,
    feature_decision_eligible,
    make_decision_cutoff,
)

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
MASTER_CSV = os.path.join(DATA, "master.csv")
OUT_PQ = os.path.join(DATA, "canonical.parquet")
OUT_SCHEMA = os.path.join(DATA, "feature_schema.json")

WIDE_COLS = ["da_price", "rtpd_price", "spread", "load_actual",
             "load_2da", "t2m", "ssrd", "wind100"]
HOUR_LABELS = ["H%d" % h for h in range(1, 25)]

# ---------------------------------------------------------------------------
# 列集合（单一事实来源）
# ---------------------------------------------------------------------------
# X 特征（模型输入）：hour/node/zone 兼具标识与特征角色；其余全部为决策时点可见的历史/静态特征。
X_COLUMNS = [
    # 时间/节点（静态或日历，决策时已知）
    "hour", "node", "zone",
    "dow", "month", "is_holiday", "solar_flag",
    # 价格滞后（delivery day = target_date-2 / -3 / -8）
    "da_lag1", "rtpd_lag1", "spread_lag1",
    "da_lag2", "rtpd_lag2", "spread_lag2",
    "da_lag7", "rtpd_lag7", "spread_lag7",
    # 滚动统计（同 hour，target_date-2 .. -(w+1)，w=7/14/30）
    "spread_mean7", "spread_std7",
    "spread_mean14", "spread_std14",
    "spread_mean30", "spread_std30",
    # 日级统计（target_date-2 当天 24h，正确广播到每小时）
    "spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
    "da_day_mean_lag1", "rtpd_day_mean_lag1", "spread_day_mean_lag1",
    # 负荷
    "load_actual_lag1", "load_actual_day_mean_lag1",
    "load_2da_forecast", "load_peak_flag",
    # 历史天气滞后（target_date-2 同 hour；决策时可得，无论预报/实测）
    "t2m_lag1", "ssrd_lag1", "wind100_lag1",
    # 同 zone 关联节点（peer）滞后（target_date-2 同 hour）
    "peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1",
]

LABEL_COLUMNS = ["actual_da", "actual_rtpd", "actual_return", "direction"]

# 纯标识/元数据（不进 X）：target_date 为行主键；split 供时间切分；has_label 恒 True（已过滤）
ID_COLUMNS = ["target_date", "decision_date", "split", "has_label"]

# 数据集物理列顺序
ORDER = (["node", "zone", "target_date", "decision_date", "hour", "split", "has_label"]
         + [c for c in X_COLUMNS if c not in ("node", "zone", "hour")]
         + LABEL_COLUMNS)

# 分段时间区间（按 decision_date 判定，与 features.py 保持同口径以便对比）
SPLIT_RANGES = {
    "train": (pd.Timestamp("2025-04-02"), pd.Timestamp("2025-12-31")),
    "val":   (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-05-31")),
    "test":  (pd.Timestamp("2026-06-01"), pd.Timestamp("2026-08-05")),
}
ELCA_SPLIT_RANGES = {
    "train": (pd.Timestamp("2026-03-03"), pd.Timestamp("2026-05-31")),
    "test":  (pd.Timestamp("2026-06-01"), pd.Timestamp("2026-08-05")),
}

# 决策截止（业务契约冻结）
DECISION_CUTOFF_DESC = "decision_date 10:00 (Day-Ahead bid cutoff, frozen contract)"

# 默认禁用的特征（保守模式：UNKNOWN 未确认前不进入训练/推理）
DISABLED_FEATURES = {
    "t2m_next": ("目标日(target_date)天气温度。zone_weather_hourly.csv 疑似实测/再分析"
                 "（变量名 ssrd_wm2/wind100 为 ERA5 风格），且数据延伸到未来（2026-08-19），"
                 "决策时点不可得。上一轮审计判定很可能用了目标日实际天气（穿越）。默认禁用。"),
    "ssrd_next": ("目标日太阳辐射。同上，UNKNOWN 未确认是决策时可得预报前，默认禁用。"),
    "wind100_next": ("目标日 100m 风速。同上，默认禁用。"),
}

# ---------------------------------------------------------------------------
# Leakage Guard：每个 X 特征的可用时点（相对 target_date T 的偏移）
#   available_at 必须是"决策截止（decision_date 10:00）之前或等于"才放行。
# ---------------------------------------------------------------------------
STATIC_FEATURES = {"hour", "node", "zone", "dow", "month", "is_holiday", "solar_flag"}
PRICE_LAG_FEATURES = {"da_lag1", "rtpd_lag1", "spread_lag1",
                      "da_lag2", "rtpd_lag2", "spread_lag2",
                      "da_lag7", "rtpd_lag7", "spread_lag7"}
ROLLING_FEATURES = {"spread_mean7", "spread_std7",
                    "spread_mean14", "spread_std14",
                    "spread_mean30", "spread_std30"}
DAY_LEVEL_FEATURES = {"spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
                      "da_day_mean_lag1", "rtpd_day_mean_lag1", "spread_day_mean_lag1",
                      "load_actual_day_mean_lag1"}
LOAD_HIST_FEATURES = {"load_actual_lag1"}
FORECAST_FEATURES = {"load_2da_forecast", "load_peak_flag"}   # ASSUMED 可得（日前负荷预测）
WEATHER_HIST_FEATURES = {"t2m_lag1", "ssrd_lag1", "wind100_lag1"}
PEER_FEATURES = {"peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1"}


def availability_map():
    """返回 {feature: {available_at, status, availability_basis, source_target_date,
                      latest_possible_available_at, has_precise_publish_time}}。

    Agent B P0-2 统一口径（展示 == Time Gate 判定）：
      - STATIC 特征              : availability_basis=STATIC，无时间门槛，恒可用。
      - 滞后/滚动/日级/历史特征   : availability_basis=STRUCTURAL_LAG，数据来自**已完整交付的
                                  历史日**（source_target_date = target_date − 2 或更早），
                                  **无精确发布时刻**；最晚可证可用上界 latest_possible_available_at
                                  = decision_date 00:00 PT（Time Gate 与 UI 展示共用同一上界）。
      - 2DA 负荷预报              : availability_basis=ASSUMED_AVAILABLE，按源约定提前 2 日发布，
                                  无精确发布时刻，同样以 decision_date 00:00 PT 为最晚可证上界。

    本 map 是 UI 展示与 Time Gate 判定的**单一事实来源**；禁止在展示层另造"23:59"之类的
    精确时刻（没有精确发布时间就必须用 STRUCTURAL_LAG / ASSUMED_AVAILABLE 表达上界）。
    """
    lag_meta = {
        AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_STRUCTURAL_LAG,
        LATEST_POSSIBLE_AVAILABLE_AT_KEY: BOUND_RULE_DECISION_DATE_00_PT,
        HAS_PRECISE_PUBLISH_TIME_KEY: False,
    }
    m = {}
    for f in STATIC_FEATURES:
        m[f] = {
            "available_at": "已知（静态/日历）",
            "status": "CONFIRMED",
            AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_STATIC,
            SOURCE_TARGET_DATE_KEY: "",
            LATEST_POSSIBLE_AVAILABLE_AT_KEY: "",
            HAS_PRECISE_PUBLISH_TIME_KEY: False,
        }
    for f in PRICE_LAG_FEATURES:
        k = int(f.rsplit("_lag", 1)[1])
        m[f] = {
            "available_at": f"target_date - {k+1}（交付日整日完整）",
            "status": "CONFIRMED",
            SOURCE_TARGET_DATE_KEY: f"target_date - {k+1}",
            **lag_meta,
        }
    for f in ROLLING_FEATURES:
        w = int(f.split("mean")[1] if "mean" in f else f.split("std")[1])
        m[f] = {
            "available_at": f"target_date-2 .. target_date-{w+1}（同 hour 共 {w} 天完整）",
            "status": "CONFIRMED",
            SOURCE_TARGET_DATE_KEY: f"target_date-2 .. target_date-{w+1}",
            **lag_meta,
        }
    for f in DAY_LEVEL_FEATURES:
        m[f] = {
            "available_at": "target_date - 2（当天 24h 完整）",
            "status": "CONFIRMED",
            SOURCE_TARGET_DATE_KEY: "target_date - 2",
            **lag_meta,
        }
    for f in LOAD_HIST_FEATURES:
        m[f] = {
            "available_at": "target_date - 2（当天实际负荷完整）",
            "status": "CONFIRMED",
            SOURCE_TARGET_DATE_KEY: "target_date - 2",
            **lag_meta,
        }
    for f in WEATHER_HIST_FEATURES:
        m[f] = {
            "available_at": "target_date - 2（当天天气完整）",
            "status": "CONFIRMED",
            SOURCE_TARGET_DATE_KEY: "target_date - 2",
            **lag_meta,
        }
    for f in PEER_FEATURES:
        m[f] = {
            "available_at": "target_date - 2（peer 交付日完整）",
            "status": "CONFIRMED",
            SOURCE_TARGET_DATE_KEY: "target_date - 2",
            **lag_meta,
        }
    for f in FORECAST_FEATURES:
        m[f] = {
            "available_at": "target_date - 2（2DA 负荷预测，预计提前 2 日发布）",
            "status": "ASSUMED_AVAILABLE",
            AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_ASSUMED_AVAILABLE,
            SOURCE_TARGET_DATE_KEY: "target_date - 2",
            LATEST_POSSIBLE_AVAILABLE_AT_KEY: BOUND_RULE_DECISION_DATE_00_PT,
            HAS_PRECISE_PUBLISH_TIME_KEY: False,
        }
    return m


# ---------------------------------------------------------------------------
# 基础数据操作
# ---------------------------------------------------------------------------
def load_master():
    m = pd.read_csv(MASTER_CSV, parse_dates=["date"])
    # master.csv 在 2026-07-21 ~ 07-26 存在整行重复（已实证为全列相同副本），按主键去重
    n_before = len(m)
    m = m.drop_duplicates(subset=["node", "date", "hour"], keep="first").reset_index(drop=True)
    if len(m) != n_before:
        print("[info] master 去重：%d -> %d 行（重复 %d 行）" % (n_before, len(m), n_before - len(m)))
    return m


def _node_wide(node_df, extra_days=0):
    """单节点 -> 宽表 dict {col: DataFrame(index=date, columns=H1..H24)}。

    index 补齐为连续自然日（价格区间 + extra_days），保证 shift(k)/rolling 按自然日对齐。
    """
    nd = node_df.drop_duplicates(["date", "hour"]).sort_values(["date", "hour"])
    price_dates = pd.DatetimeIndex(sorted(nd["date"].unique()))
    full_dates = pd.date_range(price_dates.min(),
                               price_dates.max() + pd.Timedelta(days=extra_days), freq="D")
    wide = {}
    for col in WIDE_COLS:
        w = nd.set_index(["date", "hour"])[col].unstack("hour")
        w = w.reindex(columns=range(1, 25)).rename(columns=lambda h: "H%d" % h)
        wide[col] = w.reindex(full_dates)
    zone = str(nd["zone"].iloc[0])
    return wide, full_dates, zone


def _hourly_feature_frames(wide, peer_wide=None):
    """所有 date×hour 级特征/标签 DataFrame（date × H1..H24）。"""
    out = {}

    def lag(w, k):
        return w.shift(k)

    for k, off in (("1", 2), ("2", 3), ("7", 8)):
        out["da_lag%s" % k] = lag(wide["da_price"], off)
        out["rtpd_lag%s" % k] = lag(wide["rtpd_price"], off)
        out["spread_lag%s" % k] = lag(wide["spread"], off)

    out["spread_mean7"] = wide["spread"].rolling(7).mean().shift(2)
    out["spread_std7"] = wide["spread"].rolling(7).std().shift(2)
    out["spread_mean14"] = wide["spread"].rolling(14).mean().shift(2)
    out["spread_std14"] = wide["spread"].rolling(14).std().shift(2)
    out["spread_mean30"] = wide["spread"].rolling(30).mean().shift(2)
    out["spread_std30"] = wide["spread"].rolling(30).std().shift(2)

    out["load_actual_lag1"] = lag(wide["load_actual"], 2)
    out["load_2da_forecast"] = wide["load_2da"]                 # target_date 当天预报，无 shift
    w2 = wide["load_2da"]
    day_max = w2.max(axis=1)
    out["load_peak_flag"] = w2.eq(day_max, axis=0).where(day_max.notna(), np.nan).astype(float)

    for c in ("t2m", "ssrd", "wind100"):
        out["%s_lag1" % c] = lag(wide[c], 2)

    if peer_wide is not None:
        out["peer_spread_lag1"] = lag(peer_wide["spread"], 2)
        out["peer_da_lag1"] = lag(peer_wide["da_price"], 2)
        out["peer_rtpd_lag1"] = lag(peer_wide["rtpd_price"], 2)
    else:
        for c in ("peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1"):
            out[c] = pd.DataFrame(np.nan, index=wide["spread"].index, columns=HOUR_LABELS)

    # ---- labels 区（隔离：仅存在于 label 列，绝不作特征）----
    out["actual_da"] = wide["da_price"]
    out["actual_rtpd"] = wide["rtpd_price"]
    out["actual_return"] = wide["da_price"] - wide["rtpd_price"]   # 由真实 DA/RTPD 重算
    out["direction"] = np.sign(wide["da_price"] - wide["rtpd_price"])
    return out


def _day_level_series(wide):
    """日级统计（date 级 Series，之后广播到 24 小时）。全部取 target_date-2 当天。"""
    sw = wide["spread"]
    out = {
        "spread_day_std_lag1": sw.std(axis=1).shift(2),
        "spread_day_range_lag1": (sw.max(axis=1) - sw.min(axis=1)).shift(2),
        "spread_day_max_lag1": sw.max(axis=1).shift(2),
        "da_day_mean_lag1": wide["da_price"].mean(axis=1).shift(2),
        "rtpd_day_mean_lag1": wide["rtpd_price"].mean(axis=1).shift(2),
        "spread_day_mean_lag1": sw.mean(axis=1).shift(2),
        "load_actual_day_mean_lag1": wide["load_actual"].mean(axis=1).shift(2),
    }
    return out


_HOLIDAYS = set(USFederalHolidayCalendar().holidays(start="2024-01-01", end="2027-01-01"))


def _add_calendar(long_df):
    """在长表上补充 target_date 的日历特征（dow/month/is_holiday/solar_flag）。"""
    t = pd.DatetimeIndex(long_df["target_date"])
    long_df["dow"] = t.dayofweek
    long_df["month"] = t.month
    long_df["is_holiday"] = t.isin(_HOLIDAYS).astype(int)
    long_df["solar_flag"] = ((long_df["hour"] >= 10) & (long_df["hour"] <= 16)).astype(int)
    return long_df


# ---------------------------------------------------------------------------
# 单一特征构造函数（app.py 与 train 复用）
# ---------------------------------------------------------------------------
def build_node_grid(node_df, peer_df=None, extra_days=0):
    """构建单个节点的完整特征网格（date×hour，含 labels）。

    修复要点：日级特征与小时特征分离拼装——小时特征统一 concat 成 date×H1..H24 后 stack，
    日级特征以 date 为键 merge 广播到 24 小时；杜绝旧实现产生的幽灵 hour=0 行。
    """
    wide, full_dates, zone = _node_wide(node_df, extra_days)
    peer_wide = None
    if peer_df is not None:
        pw, _, _ = _node_wide(peer_df, extra_days)
        peer_wide = pw

    hourly = _hourly_feature_frames(wide, peer_wide)
    daylv = _day_level_series(wide)

    # 1) 全部 date×hour 级特征/标签 concat -> MultiIndex columns (feat, H*)，再 stack
    combined = pd.concat(hourly, axis=1)
    combined.columns.names = ["feat", "hour"]
    combined.index.name = "date"          # 命名日期索引，stack 后 reset_index 才得到 'date' 列
    long = combined.stack("hour").reset_index()
    long["hour"] = long["hour"].astype(str).str.replace("H", "", regex=False).astype(int)
    long["target_date"] = long["date"]
    long = long.drop(columns=["date"])

    # 2) 日级特征按 date 广播（正确 broadcast 到 24 小时）
    for name, s in daylv.items():
        d = s.rename(name).reset_index()
        d.columns = ["target_date", name]
        long = long.merge(d, on="target_date", how="left")

    # 3) 日历 / 静态
    long = _add_calendar(long)
    long["zone"] = zone
    return long


def build_row_features(master, node, target_date):
    """【单一特征构造函数】为单个 (node, target_date) 构造 24 行 X 特征（供 app.py 推理）。

    与 build_canonical 完全同源；target_date 可为未来日期（决策时点之后），用于推理。
    返回 DataFrame：columns = X_COLUMNS，rows = 24（hour 1..24）。
    """
    node_df = master[master["node"] == node]
    if node_df.empty:
        raise ValueError("未知节点: %s" % node)
    zone_of = master.drop_duplicates("node").set_index("node")["zone"].to_dict()
    peers = [n for n in zone_of if n != node and zone_of[n] == zone_of[node]]
    peer_df = master[master["node"] == peers[0]] if peers else None

    target = pd.Timestamp(target_date)
    price_max = node_df["date"].max()
    extra_days = max(0, (target - pd.Timestamp(price_max)).days) + 2

    grid = build_node_grid(node_df, peer_df, extra_days=extra_days)
    grid["node"] = node
    row = grid[grid["target_date"] == target].sort_values("hour").reset_index(drop=True)
    if len(row) < 24:
        # 目标日越界或部分缺失：补齐小时，缺失值留 NaN（模型原生处理）
        full = pd.DataFrame({"hour": range(1, 25)})
        row = full.merge(row, on="hour", how="left")
        for c in X_COLUMNS:
            if c not in row.columns:
                row[c] = np.nan
    return row[X_COLUMNS]


def assign_split_elca(d):
    for name, (lo, hi) in ELCA_SPLIT_RANGES.items():
        if lo <= d <= hi:
            return name
    return np.nan


def assign_split(d):
    for name, (lo, hi) in SPLIT_RANGES.items():
        if lo <= d <= hi:
            return name
    return np.nan


def build_canonical(master):
    """重建 canonical dataset：一行 = 一个 (node, target_date, hour)。

    - 只保留真实存在于价格数据中的 target_date（label 可确认）。
    - label 区（actual_*）由真实 DA/RTPD 创建；actual_return 为 NaN 的行剔除（目标 NaN 不进数据集）。
    - 严格按时间切分：split 由 decision_date = target_date - 1 判定。
    """
    nodes = sorted(master["node"].unique())
    zone_of = master.drop_duplicates("node").set_index("node")["zone"].to_dict()
    peer_of = {}
    for n in nodes:
        ps = [m for m in nodes if m != n and zone_of.get(m) == zone_of.get(n)]
        peer_of[n] = ps[0] if ps else None

    frames = []
    for node in nodes:
        nd = master[master["node"] == node]
        pn = peer_of[node]
        peer_df = master[master["node"] == pn] if pn else None
        g = build_node_grid(nd, peer_df, extra_days=0)
        g["node"] = node
        frames.append(g)
    df = pd.concat(frames, ignore_index=True)

    # split 按决策日判定
    df["decision_date"] = df["target_date"] - pd.Timedelta(days=1)
    df["split"] = [assign_split_elca(d) if n == "ELCAJNGT_7_N001" else assign_split(d)
                   for n, d in zip(df["node"], df["decision_date"])]

    # 剔除 label 无法确认的行（目标 NaN 不进训练）
    df["has_label"] = df["actual_return"].notna()
    n_nan_label = int((~df["has_label"]).sum())
    df = df[df["has_label"]].reset_index(drop=True)

    df = df[ORDER].sort_values(["node", "target_date", "hour"]).reset_index(drop=True)
    return df, n_nan_label


# ---------------------------------------------------------------------------
# Leakage Guard 与一致性自检
# ---------------------------------------------------------------------------
def verify(canon, master):
    """自检（打印并断言）：
      1) 无幽灵 hour=0；hour ∈ 1..24
      2) label 无 NaN（has_label 全 True）
      3) X 与 label 列不相交；X 不含任何 D+1 实际值（时间上 available_at <= decision_cutoff）
      4) lag 对位：spread_lag1 == master (node, target_date-2, hour) 的 spread
      5) rolling 对位：spread_mean7 == 同 hour 上 target_date-2..-8 的均值
    """
    print("== Leakage Guard 与一致性自检 ==")

    # 1) 无幽灵行
    assert canon["hour"].between(1, 24).all(), "存在 hour 越界行"
    assert not (canon["hour"] == 0).any(), "存在幽灵 hour=0 行"
    print("[1] hour 无 0/越界，取值 1..24: PASS")

    # 2) label 无 NaN
    assert canon["has_label"].all(), "存在 has_label=False 行（不应出现在 canonical 中）"
    for c in LABEL_COLUMNS:
        assert canon[c].notna().all(), "label 列 %s 含 NaN" % c
    print("[2] label 无 NaN（has_label 全 True，%s 列全非空）: PASS" % ", ".join(LABEL_COLUMNS))

    # 3) X ∩ labels = ∅；X 不含目标日实际值
    assert set(X_COLUMNS).isdisjoint(LABEL_COLUMNS), "X 与 label 列重叠!"
    assert set(ID_COLUMNS).isdisjoint(LABEL_COLUMNS)
    assert "load_actual" not in X_COLUMNS and "t2m" not in X_COLUMNS, "X 混入实际列"
    print("[3] X 与 label 列不相交；X 不含 target_date 实际 DA/RTPD/Return/负荷/天气: PASS")

    # 4) Leakage Guard：所有 X 特征 available_at <= decision_cutoff
    av = availability_map()
    assert set(av.keys()) == set(X_COLUMNS), "availability_map 与 X_COLUMNS 不一致: %s" % (
        set(av.keys()) ^ set(X_COLUMNS))
    late = {f for f in X_COLUMNS if av[f]["status"] != "CONFIRMED"
            and av[f]["status"] != "ASSUMED_AVAILABLE"}
    assert not late, "存在 available_at 晚于 decision_cutoff 的 X 特征: %s" % late
    print("[4] Leakage Guard: %d 个 X 特征 available_at 均 <= decision_cutoff (%s): PASS"
          % (len(X_COLUMNS), DECISION_CUTOFF_DESC))

    # 5) lag 对位抽查（对比 master 原始值）
    m = master[["node", "date", "hour", "da_price", "rtpd_price", "spread"]].copy()
    m["lag_src_date"] = m["date"] + pd.Timedelta(days=2)   # 使 master 交付日 = 该行 target_date-2
    rng = np.random.default_rng(42)
    samp = canon.sample(n=min(8, len(canon)), random_state=rng).reset_index(drop=True)
    chk = samp[["node", "target_date", "hour", "da_lag1", "rtpd_lag1", "spread_lag1"]].merge(
        m[["node", "lag_src_date", "hour", "da_price", "rtpd_price", "spread"]],
        left_on=["node", "target_date", "hour"],
        right_on=["node", "lag_src_date", "hour"], how="left")
    assert np.allclose(chk["da_lag1"], chk["da_price"], equal_nan=True), "da_lag1 错位!"
    assert np.allclose(chk["rtpd_lag1"], chk["rtpd_price"], equal_nan=True), "rtpd_lag1 错位!"
    assert np.allclose(chk["spread_lag1"], chk["spread"], equal_nan=True), "spread_lag1 错位!"
    print("[5] lag 对位抽查（da/rtpd/spread_lag1 vs master target_date-2 原始值）: PASS")
    print(chk[["node", "target_date", "hour", "da_lag1", "da_price",
               "rtpd_lag1", "rtpd_price", "spread_lag1", "spread"]].to_string(index=False))

    # 6) rolling 对位抽查：spread_mean7 == 同 hour 上 target_date-2..-8 的均值
    m7 = master[["node", "date", "hour", "spread"]].copy()
    rows = []
    for _, r in samp.iterrows():
        src = m7[(m7["node"] == r["node"]) & (m7["hour"] == r["hour"]) &
                 m7["date"].between(r["target_date"] - pd.Timedelta(days=8),
                                    r["target_date"] - pd.Timedelta(days=2))]
        rows.append({"expected": float(src["spread"].mean())})
    exp = pd.Series([x["expected"] for x in rows])
    got = samp["spread_mean7"].reset_index(drop=True)
    assert np.allclose(got, exp, equal_nan=True), "spread_mean7 错位!"
    print("[6] rolling 对位抽查（spread_mean7 == target_date-2..-8 同 hour 均值）: PASS")

    # 7) 日级特征广播检查：每个 (node, target_date) 内 spread_day_std_lag1 应全部一致
    grp = canon.groupby(["node", "target_date"])["spread_day_std_lag1"]
    nunique = grp.nunique()
    bad = nunique[nunique > 1]
    assert len(bad) == 0, "日级特征未正确广播到 24h: %d 组" % len(bad)
    print("[7] 日级特征广播（spread_day_std_lag1 每 (node,target_date) 24h 一致）: PASS")

    # 8) 每 (node,target_date) 行数 == master 中该 (node,date) 的有效价格小时数（真实部分日允许 <24）
    m_counts = (master[master["spread"].notna()].groupby(["node", "date"]).size()
                .rename("n_master"))
    r = canon.groupby(["node", "target_date"]).size().rename("n_canon")
    cmp = r.reset_index().rename(columns={"target_date": "date"}).merge(
        m_counts.reset_index(), on=["node", "date"], how="left")
    bad = cmp[cmp["n_canon"] != cmp["n_master"]]
    assert len(bad) == 0, "canonical 行数与 master 有效小时数不一致: %s" % bad.to_dict()
    n24 = int((cmp["n_canon"] == 24).sum())
    nlt = int((cmp["n_canon"] < 24).sum())
    print("[8] 每 (node,target_date) 行数 == master 有效价格小时数（24=%d 组, <24=%d 组，无幽灵行）: PASS"
          % (n24, nlt))

    # 9) P0-2 统一口径：availability_map 全部 X 特征经 feature gate 均 decision_eligible
    #    （展示 available_at 上界 == Time Gate 判定口径；无精确发布时刻 → 用最晚可证上界）
    sample_dds = pd.to_datetime(canon["decision_date"].unique()[:5])
    for dd in sample_dds:
        dstr = str(dd.date())
        cutoff = make_decision_cutoff(dstr) or ""
        for f in X_COLUMNS:
            assert feature_decision_eligible(av[f], dstr, cutoff), (
                "P0-2 特征 %s @ %s 判定不可用（displayed available_at 与 Time Gate 不一致）"
                % (f, dstr))
    print("[9] P0-2 统一口径：%d 个 X 特征 available_at 上界全部 <= decision_cutoff"
          "（STRUCTURAL_LAG/ASSUMED → decision_date 00:00 PT 上界）: PASS" % len(X_COLUMNS))
    return True


# ---------------------------------------------------------------------------
# 保存与 schema
# ---------------------------------------------------------------------------
def save_canonical(df):
    df.to_parquet(OUT_PQ, index=False)
    print("saved ->", OUT_PQ)


def build_schema(df, n_nan_label_dropped, master):
    av = availability_map()
    return {
        "feature_version": "canonical_v1",
        "generated_at": pd.Timestamp.now().isoformat(),
        "generated_by": "canonical.py",
        "status": "canonical（取代已废弃 features.py / features.parquet，旧文件保留作对比）",
        "row_semantics": (
            "一行 = 一个 (node, target_date, hour)；target_date = 交付日 D+1；"
            "decision_date = target_date - 1；decision_cutoff = decision_date 10:00（契约冻结）"),
        "source": "code/data/master.csv",
        "rows": int(len(df)),
        "nodes": sorted(df["node"].unique().tolist()),
        "date_min": str(df["target_date"].min().date()),
        "date_max": str(df["target_date"].max().date()),
        "n_nan_label_rows_dropped": n_nan_label_dropped,
        "split_ranges_by_decision_date": {k: [str(v[0].date()), str(v[1].date())]
                                          for k, v in SPLIT_RANGES.items()},
        "elca_split_ranges_by_decision_date": {k: [str(v[0].date()), str(v[1].date())]
                                               for k, v in ELCA_SPLIT_RANGES.items()},
        "id_columns": ["node", "zone", "target_date", "decision_date", "hour",
                       "split", "has_label"],
        "x_columns": X_COLUMNS,
        "label_columns": LABEL_COLUMNS,
        "label_definitions": {
            "actual_da": "target_date 当日 DA 清价（$ / MWh）",
            "actual_rtpd": "target_date 当日 RTPD 价格（$ / MWh）",
            "actual_return": "actual_da - actual_rtpd（= DARTPD Return，契约冻结定义）",
            "direction": "np.sign(actual_return)：+1 / -1 / 0",
        },
        "disabled_features": DISABLED_FEATURES,
        "feature_availability": av,
        "availability_semantics": {
            "unified_rule": (
                "displayed_available_at == time_gate_used_available_at（单一来源：本 map）；"
                "滞后/历史特征无精确发布时刻 → availability_basis=STRUCTURAL_LAG，"
                "latest_possible_available_at = decision_date 00:00 PT（最晚可证上界，非编造时刻）；"
                "2DA 负荷预报 → ASSUMED_AVAILABLE，同样以上界为准；UI 不得显示虚假精确时间戳。"),
            "bound_rule_decision_date_00_pt": BOUND_RULE_DECISION_DATE_00_PT,
        },
        "decision_cutoff": DECISION_CUTOFF_DESC,
        "lag_convention": (
            "lag1 -> target_date-2, lag2 -> target_date-3, lag7 -> target_date-8; "
            "rolling(w) -> target_date-2 .. target_date-(w+1); 日级统计 -> target_date-2 当天。"
            "（DA(target_date-1) 虽已出清，但 RTPD(target_date-1) 决策日深夜才完整，"
            "故滞后从 target_date-2 起，宁保守不泄漏）"),
        "notes": [
            "label 只从真实存在的 DA/RTPD/Return 创建；actual_return 为 NaN 的行不进入数据集。",
            "X 不含任何 target_date 实际值；*_next 天气特征默认禁用（见 disabled_features）。",
            "canonical 覆盖 target_date ∈ 各节点价格数据区间；未来推理日由 canonical.build_row_features 构造。",
            "weather valid_pt 为 naive 小时戳（CA-ISO 为 America/Los_Angeles），未做时区换算，历史滞后特征不受泄漏影响，但存在小时对齐不确定性（待 Agent C/后续确认）。",
            "本文件只含训练/评估数据；不含预测-only 行（未来日）。",
        ],
    }


def write_availability_matrix(schema):
    """按业务契约 §4 生成 docs/feature_availability_matrix.md（单一来源：schema）。"""
    out_path = os.path.join(os.path.dirname(ROOT), "docs", "feature_availability_matrix.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines = [
        "# 特征可用性矩阵（canonical dataset）",
        "",
        "> 生成时间：%s　|　特征版本：%s　|　生成者：%s" % (
            schema["generated_at"], schema["feature_version"], schema["generated_by"]),
        "> 行语义：一行 = (node, target_date, hour)；决策时点 = decision_date = target_date-1 的 10:00 前。",
        "> **铁律**：任何特征若 `available_at > decision_cutoff` 禁止进入训练/推理；UNKNOWN 未确认前默认禁用。",
        "> **P0-2 统一口径**：displayed_available_at == time_gate_used_available_at；无精确发布时刻的滞后/历史特征",
        "> 一律以 `availability_basis=STRUCTURAL_LAG`、`latest_possible_available_at=decision_date 00:00 PT`（最晚可证",
        "> 上界）表达，UI 不显示虚假精确时间戳（如 23:59）。",
        "",
        "## X 特征（决策时点可见）",
        "",
        "| 特征 | 类别 | available_at（相对 target_date） | 状态 | availability_basis | latest_possible_available_at |",
        "|---|---|---|---|---|---|",
    ]
    cat_of = {}
    for f in X_COLUMNS:
        if f in STATIC_FEATURES:
            cat_of[f] = "时间/节点（静态·日历）"
        elif f in PRICE_LAG_FEATURES:
            cat_of[f] = "价格滞后"
        elif f in ROLLING_FEATURES:
            cat_of[f] = "价差滚动统计"
        elif f in DAY_LEVEL_FEATURES:
            cat_of[f] = "日级统计（target_date-2 当天）"
        elif f in LOAD_HIST_FEATURES:
            cat_of[f] = "实际负荷（历史）"
        elif f in FORECAST_FEATURES:
            cat_of[f] = "负荷预报（2DA）"
        elif f in WEATHER_HIST_FEATURES:
            cat_of[f] = "天气滞后（历史）"
        elif f in PEER_FEATURES:
            cat_of[f] = "关联节点（peer）"
        else:
            cat_of[f] = "?"
    for f in X_COLUMNS:
        m = schema["feature_availability"][f]
        basis = m.get("availability_basis", "UNKNOWN")
        latest = m.get("latest_possible_available_at", "") or "—"
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            f, cat_of[f], m["available_at"], m["status"], basis, latest))
    lines += [
        "",
        "## 默认禁用特征（UNKNOWN / 穿越风险，保守模式）",
        "",
        "| 特征 | 原因 |",
        "|---|---|",
    ]
    for f, reason in schema["disabled_features"].items():
        lines.append("| %s | %s |" % (f, reason.replace("\n", " ")))
    lines += [
        "",
        "## Label 区（决策时点不可见，仅训练/回测）",
        "",
        "| 列 | 定义 | available_at |",
        "|---|---|---|",
        "| actual_da | target_date 当日 DA 清价 | target_date-1 13:00（DA 结果发布，非 bid cutoff）|",
        "| actual_rtpd | target_date 当日 RTPD | target_date 深夜（实时市场）|",
        "| actual_return | actual_da - actual_rtpd | 两者齐备后 |",
        "| direction | sign(actual_return) | 两者齐备后 |",
        "",
        "## 滞后约定",
        "",
        schema["lag_convention"].replace("\n", " "),
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("saved ->", out_path)


def main():
    print("== 读取 master.csv ==")
    master = load_master()

    print("== 重建 canonical dataset ==")
    df, n_nan_label = build_canonical(master)
    print("canonical rows:", len(df), "cols:", len(df.columns))

    print("== split 分布（按 decision_date）==")
    cnt = df["split"].value_counts(dropna=False)
    for k in ["train", "val", "test", np.nan]:
        key = k if not (isinstance(k, float) and np.isnan(k)) else None
        print("  %s: %d" % (("warm-up/范围外" if k is None else k), int(cnt.get(key, 0))))

    print("== NaN 比例（仅 NaN>0 的列）==")
    nan_ratio = df.isna().mean()
    nan_ratio = nan_ratio[nan_ratio > 0].sort_values(ascending=False)
    for col, r in nan_ratio.items():
        print("  %-24s %.4f" % (col, r))

    print("== 自检 ==")
    verify(df, master)

    print("== 保存 ==")
    save_canonical(df)
    schema = build_schema(df, n_nan_label, master)
    with open(OUT_SCHEMA, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    print("saved ->", OUT_SCHEMA)
    write_availability_matrix(schema)
    print("done.")


if __name__ == "__main__":
    main()
