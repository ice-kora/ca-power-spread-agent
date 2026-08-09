# -*- coding: utf-8 -*-
"""
code/data_acquisition/schemas.py —— CA-ISO 价差交易项目 As-of 数据结构（Agent D）
=================================================================================

设计文档：docs/asof_schema_design.md（本文件即其代码实现）。

职责
----
定义"可追溯、防穿越"的输入侧 vintage 层：
  1. AsOfRecord：一条"某时点、某源发布、指向某目标时刻"的原子事实（含全部时间字段）。
  2. feature_snapshot：决策日冻结的特征表，每行可溯源到 AsOfRecord → raw response。
  3. 两套采集模式的 available_at 解析（BACKTEST 用历史 vintage，PRODUCTION 用
     max(published, retrieved)）。

核心铁律（与 agent/evidence/time_gate.py 同语义，程序计算，禁止人工/LLM 覆盖）
-------------------------------------------------------------------------------
  R1  available_at <= decision_cutoff  ⇒ time_eligible = TRUE，否则 FALSE（纯时间门槛）
  R2  任一时间缺失 / 不可解析         ⇒ time_eligible = FALSE（宁保守不穿越）
  R3  decision_eligible 且 target_time 可解析 且 value 非 NaN 且 identity 非空 ⇒ is_usable
  R4  回测模式 available_at 必须来自历史 vintage；禁止用 retrieved_at(=今天) 主张历史可用
  R5  生产模式 available_at = max(published_at, retrieved_at)；先存 raw 再记 retrieved
  R6  snapshot 追加写不可变；post 记录只进复盘
  R9  **Time Gate 只判 available_at <= decision_cutoff**，绝不判 initialization_time <= cutoff
      （发布/下载延迟为正；available_at 必须严格晚于 model_run_time——见 weather_gfs.py 延迟模型）

五个时间概念（P0-1，字段名保留英文；GFS 等预报源）
-------------------------------------------------------------------------------
  model_run_time      : 模型起报时刻（run 起始，UTC naive）
  initialization_time : = model_run_time（同义；代码中 issue_time 字段承载）
  available_at        : 该 run 数据**真正可用**的时刻（发布/下载延迟之后），Time Gate 唯一判据
  retrieved_at        : 本次抓取时刻（墙钟，仅审计）
  decision_cutoff     : D-1 10:00 PT → UTC（DAM Market Close / bid cutoff）

  R10 若某历史 run 无法可靠证明真实 available_at（如 GFS 12Z/18Z：发布与 cutoff 临界或在其后）
      ⇒ available_at = None ⇒ time_eligible = backtest_eligible = FALSE（不自行推测发布时刻）。

eligibility 三拆（MOCK / NOT_BACKTEST_SAFE 硬隔离，Agent B）
-------------------------------------------------------------------------------
  R7  is_mock == TRUE            ⇒ backtest_eligible=FALSE 且 production_eligible=FALSE
      （MOCK 只能用于测试/UI 演示/单元测试；即便 available_at<=cutoff 也不能改变）
  R8  not_backtest_safe == TRUE  ⇒ backtest_eligible=FALSE
      （strict as-of 回测不可用；不禁止 production）
  语义：
      time_eligible       = R1/R2 纯时间判定（available_at <= decision_cutoff）
      backtest_eligible   = time_eligible 且 非 is_mock 且 非 not_backtest_safe
      production_eligible = time_eligible 且 非 is_mock
      decision_eligible   = 模式对应单一门槛（PRODUCTION→production_eligible；
                            其余 BACKTEST/空 → backtest_eligible，最保守）

时间口径
--------
所有时间字段以 **UTC naive ISO 8601**（YYYY-MM-DDTHH:MM:SS）为规范。
PT naive（CAISO 排程、valid_pt）入库前经 pt_naive_to_utc_naive() 转 UTC；
带偏移字符串由 parse_timestamp() 归一化。混口径比较 = 判不可用（保守）。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 保证直接 `python code/data_acquisition/schemas.py` 也能导入 code.*（Provenance 常量）
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 市场规则版本标记（单一事实来源：code/market_rules.py）
from code.market_rules import (  # noqa: E402
    CURRENT_MARKET_RULE_VERSION,
    MARKET_RULE_VERSIONS,
    normalize_market_rule_version,
)

# ---------------------------------------------------------------------------
# 常量与枚举（单一事实来源）
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "asof_v1"

MODE_BACKTEST = "BACKTEST"
MODE_PRODUCTION = "PRODUCTION"
MODES: tuple = (MODE_BACKTEST, MODE_PRODUCTION)

# ---------------------------------------------------------------------------
# 特征可用性语义（Agent B · P0-2：展示口径 == Time Gate 判定口径，单一事实来源）
# ---------------------------------------------------------------------------
#: availability_basis 允许值
#:   STATIC               静态/日历特征（node/zone/hour/dow/month/holiday/solar_flag）—— 无时间门槛，恒可用
#:   STRUCTURAL_LAG       滞后/滚动/日级/历史特征 —— 数据来自**已完整交付的历史日**
#:                        （source_target_date = target_date − 2 或更早），**无精确发布时刻**；
#:                        统一以 latest_possible_available_at = decision_date 00:00 PT 为最晚可证上界。
#:   KNOWN_PUBLICATION    有精确发布时间（available_at = published_at 为真实时刻）
#:   ASSUMED_AVAILABLE    按源约定假定决策时点可得（如 2DA 负荷预报提前 2 日发布），无精确发布时刻
#:   UNKNOWN              无法归类 → 保守判不可用
AVAILABILITY_BASIS_STATIC = "STATIC"
AVAILABILITY_BASIS_STRUCTURAL_LAG = "STRUCTURAL_LAG"
AVAILABILITY_BASIS_KNOWN_PUBLICATION = "KNOWN_PUBLICATION"
AVAILABILITY_BASIS_ASSUMED_AVAILABLE = "ASSUMED_AVAILABLE"
AVAILABILITY_BASIS_UNKNOWN = "UNKNOWN"
AVAILABILITY_BASIS_VALUES: tuple = (
    AVAILABILITY_BASIS_STATIC,
    AVAILABILITY_BASIS_STRUCTURAL_LAG,
    AVAILABILITY_BASIS_KNOWN_PUBLICATION,
    AVAILABILITY_BASIS_ASSUMED_AVAILABLE,
    AVAILABILITY_BASIS_UNKNOWN,
)

#: 统一字段名（canonical availability_map / feature_snapshot / UI / Time Gate 共用）
AVAILABILITY_BASIS_KEY = "availability_basis"
SOURCE_TARGET_DATE_KEY = "source_target_date"
LATEST_POSSIBLE_AVAILABLE_AT_KEY = "latest_possible_available_at"
HAS_PRECISE_PUBLISH_TIME_KEY = "has_precise_publish_time"

#: STRUCTURAL_LAG / ASSUMED_AVAILABLE 的统一"最晚可证可用上界"规则。
#: 滞后特征取自已完整交付的历史日（target_date − 2 = decision_date − 1），该日 H24 结算于
#: decision_date 00:00 PT 完成，故整日数据最晚于此刻完整 —— 这是**可证明的保守上界**，
#: 不是编造的精确发布时间；UI 不得把该上界当成"精确发布时刻"展示。
BOUND_RULE_DECISION_DATE_00_PT = "decision_date 00:00 PT"

# ---------------------------------------------------------------------------
# Provenance：source_type 枚举（任何进入模型/Risk Gate 的字段都要可追溯来源类型）
# ---------------------------------------------------------------------------
#: source_type 允许值（单一事实来源）。`source` 是"具体源标识"（如
#: `NCEP_GFS_025_via_OpenMeteo`），`source_type` 是"该源的语义类别"（天气/价格/负荷/…）。
SOURCE_TYPE_PRICE: str = "PRICE"        # 市场价格（DA LMP / RTPD / Return / spread）
SOURCE_TYPE_LOAD: str = "LOAD"          # 负荷（预报 / 实际）
SOURCE_TYPE_WEATHER: str = "WEATHER"    # 天气（预报 / 实际 / 再分析）
SOURCE_TYPE_EVENT: str = "EVENT"        # 事件证据（outage / 通知 / 山火 / 可再生出力等）
SOURCE_TYPE_STATIC: str = "STATIC"      # 静态 / 日历（node/zone/hour/dow/holiday…）
SOURCE_TYPE_DERIVED: str = "DERIVED"    # 派生特征（rolling/lag/日级统计，由原始源计算）
SOURCE_TYPE_UNKNOWN: str = "UNKNOWN"    # 无法归类

SOURCE_TYPES: tuple = (
    SOURCE_TYPE_PRICE,
    SOURCE_TYPE_LOAD,
    SOURCE_TYPE_WEATHER,
    SOURCE_TYPE_EVENT,
    SOURCE_TYPE_STATIC,
    SOURCE_TYPE_DERIVED,
    SOURCE_TYPE_UNKNOWN,
)


def infer_source_type(source: str = "", field_name: str = "") -> str:
    """从 source / field_name 推断 source_type（启发式；无法判断回 UNKNOWN）。

    仅用于"自动打标签"；显式给出 source_type 的调用方优先使用显式值。
    """
    s = f"{source} {field_name}".upper()
    if any(k in s for k in ("DA_LMP", "RTPD_LMP", "PRC_LMP", "RTPD", "RETURN",
                            "SPREAD", "DARTPD", "LMP")):
        return SOURCE_TYPE_PRICE
    if any(k in s for k in ("LOAD", "SLD_FCST", "TAC", "SYS_FCST")):
        return SOURCE_TYPE_LOAD
    if any(k in s for k in ("GFS", "WEATHER", "T2M", "SSRD", "WIND", "TEMPERATURE",
                            "OPEN-METEO", "ERA5")):
        return SOURCE_TYPE_WEATHER
    if any(k in s for k in ("EVIDENCE", "NOTICE", "OUTAGE", "FIRE", "RENEWABLE",
                            "EXTREME")):
        return SOURCE_TYPE_EVENT
    if any(k in s for k in ("HOLIDAY", "CALENDAR", "DOW", "MONTH", "STATIC")):
        return SOURCE_TYPE_STATIC
    if any(k in s for k in ("ROLLING", "LAG", "MEAN", "STD", "DERIVED")):
        return SOURCE_TYPE_DERIVED
    return SOURCE_TYPE_UNKNOWN

#: 决策截止的本地时刻（DAM Market Close / bid cutoff，官方 BPM "closes at 1000 hours"）
DECISION_CUTOFF_LOCAL = "10:00:00"
TZ_PT = "America/Los_Angeles"

#: As-of 记录的标准字段顺序（含派生/审计字段；decision_eligible 为计算属性不入存储列）
ASOF_KEYS: tuple = (
    "asof_id", "source", "source_type", "is_mock", "not_backtest_safe",
    "field_name", "forecast_run", "model_run_time", "issue_time",
    "published_at", "available_at", "retrieved_at", "target_time", "lead_hours",
    "node", "region", "latitude", "longitude", "value",
    "decision_cutoff", "decision_eligible", "raw_source_id", "version", "mode",
    "market_rule_version",
)

#: feature_snapshot 的标准字段顺序
SNAPSHOT_KEYS: tuple = (
    "snapshot_id", "decision_date", "decision_cutoff", "created_at", "node",
    "target_date", "target_hour", "target_time", "feature_name", "feature_value",
    "source", "source_type", "is_mock", "raw_source_id",
    "available_at", "retrieved_at",
    "time_eligible", "backtest_eligible", "production_eligible",
    "decision_eligible", "asof_record_id", "version", "market_rule_version",
)

#: 本项目已知节点 → 区域（节点位置.xlsx）
NODE_REGION: Dict[str, str] = {
    "SNLNDRO_1_N001": "ZP26",
    "CONTROLX_1_N001": "ZP26",
    "ELCAJNGT_7_N001": "SP15",
}
#: 节点坐标（节点位置.xlsx）
NODE_COORDS: Dict[str, tuple] = {
    "SNLNDRO_1_N001": (37.71123744, -122.1488067),
    "CONTROLX_1_N001": (37.342839, -118.471988),
    "ELCAJNGT_7_N001": (32.79534613, -116.9723386),
}
SYSTEM_NODE = "CAISO_TAC"

# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def _fmt(dt: Optional[datetime]) -> Optional[str]:
    """datetime → UTC naive ISO；None 原样返回。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_timestamp(value: Any) -> Optional[datetime]:
    """任意 ISO 时间 → UTC naive datetime；无法解析返回 None（保守）。

    约定：本层时间一律 UTC naive 存储。带 'Z' / 偏移（如 +08:00、-07:00）的
    字符串会先归一化到 UTC；无偏移字符串按"已是 UTC naive"处理。
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
    except Exception:
        try:
            # 容忍 'YYYY-MM-DD HH:MM:SS' 空格分隔
            dt = datetime.fromisoformat(s.replace(" ", "T", 1))
        except Exception:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _nth_sunday(year: int, month: int, n: int) -> date:
    """该月第 n 个周日（DST 启发式用）。"""
    first = date(year, month, 1)
    offset = (6 - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def pt_naive_to_utc_naive(value: Any) -> Optional[str]:
    """naive PT ISO → naive UTC ISO。

    优先 zoneinfo（America/Los_Angeles）；zoneinfo 不可用回退 DST 启发式
    （3 月第 2 周日 ~ 11 月第 1 周日 = PDT/UTC−7，其余 = PST/UTC−8）。
    """
    s = str(value).strip()
    if not s:
        return None
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(s[:19]).replace(tzinfo=ZoneInfo(TZ_PT))
        return _fmt(dt)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s[:19])
    except Exception:
        return None
    start_dst = datetime.combine(_nth_sunday(dt.year, 3, 2), datetime.min.time())
    end_dst = datetime.combine(_nth_sunday(dt.year, 11, 1), datetime.min.time())
    delta = timedelta(hours=7 if start_dst <= dt < end_dst else 8)
    return _fmt(dt - delta)


def make_decision_cutoff(decision_date: str) -> Optional[str]:
    """决策日 D 10:00 PT → UTC naive ISO（DAM Market Close / bid cutoff）。"""
    if not str(decision_date).strip():
        return None
    return pt_naive_to_utc_naive(f"{str(decision_date)[:10]}T{DECISION_CUTOFF_LOCAL}")


def target_time_pt_to_utc(target_date: str, hour: int) -> Optional[str]:
    """(target_date, hour) → UTC naive ISO。hour∈1..24，H1 = 00:00–01:00 PT。

    与 read_data.py 约定一致：valid_pt 0:00 → H1。
    """
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    if not (1 <= h <= 24):
        return None
    d = str(target_date)[:10]
    if len(d) != 10:
        return None
    return pt_naive_to_utc_naive(f"{d}T{h - 1:02d}:00:00")


def lead_hours_of(target_time: Any, available_at: Any) -> Optional[float]:
    """lead_hours = (target_time − available_at)，单位小时；不可算返回 None。"""
    t = parse_timestamp(target_time)
    a = parse_timestamp(available_at)
    if t is None or a is None:
        return None
    return round((t - a).total_seconds() / 3600.0, 3)


# ---------------------------------------------------------------------------
# 特征可用性语义函数（Agent B · P0-2：展示 == 判定，禁止另造精确时刻）
# ---------------------------------------------------------------------------
def structural_lag_available_bound(decision_date: str) -> Optional[str]:
    """STRUCTURAL_LAG 特征的最晚可证可用上界 = decision_date 00:00 PT → UTC naive ISO。

    滞后特征取自已完整交付的历史日（source day = target_date − 2 = decision_date − 1）。
    该日 H24 的 RTPD 结算于午夜（decision_date 00:00 PT）完成，故整日数据最晚于此刻完整。
    这是**可证明的保守上界**（真实可用只会更早，不会更晚），**不是编造的精确发布时间**。
    Time Gate 与 UI 展示都必须以该上界为准，禁止另造"23:59"之类的精确时刻。
    """
    if not str(decision_date).strip():
        return None
    return pt_naive_to_utc_naive(f"{str(decision_date)[:10]}T00:00:00")


def latest_available_bound(feature_availability: Dict[str, Any],
                           decision_date: str) -> Optional[str]:
    """解析某特征"最晚可证可用上界"（UTC naive ISO）—— Time Gate 与 UI 共用**同一个值**。

    依据 availability_basis：
      STATIC            → None（静态/日历特征恒可用，无时间门槛）
      STRUCTURAL_LAG    → structural_lag_available_bound(decision_date)
      ASSUMED_AVAILABLE → 按 latest_possible_available_at 规则解析
                          （本项目统一为 BOUND_RULE_DECISION_DATE_00_PT）
      KNOWN_PUBLICATION → 解析 available_at（有精确发布时间）
      UNKNOWN / 缺失    → 尝试解析 latest_possible_available_at；否则 None（保守判不可用）

    无法解析 → None ⇒ feature_decision_eligible = False（宁保守不穿越）。
    """
    basis = _coerce_str(feature_availability.get(AVAILABILITY_BASIS_KEY))
    if basis == AVAILABILITY_BASIS_STATIC:
        return None
    if basis == AVAILABILITY_BASIS_STRUCTURAL_LAG:
        return structural_lag_available_bound(decision_date)
    if basis == AVAILABILITY_BASIS_KNOWN_PUBLICATION:
        return _fmt(parse_timestamp(feature_availability.get("available_at")))
    rule = _coerce_str(feature_availability.get(LATEST_POSSIBLE_AVAILABLE_AT_KEY))
    if rule == BOUND_RULE_DECISION_DATE_00_PT:
        return structural_lag_available_bound(decision_date)
    return _fmt(parse_timestamp(rule))


def feature_decision_eligible(feature_availability: Dict[str, Any],
                              decision_date: str,
                              decision_cutoff: Optional[str]) -> bool:
    """特征级 Time Gate（展示口径 == 判定口径）。

    铁律：displayed_available_at（= latest_available_bound 同一上界）> decision_cutoff
          ⇒ decision_eligible MUST NOT = TRUE。
    STATIC 特征无时间门槛，恒 TRUE；任一时间缺失/不可解析 → FALSE（宁保守不穿越）。
    """
    basis = _coerce_str(feature_availability.get(AVAILABILITY_BASIS_KEY))
    if basis == AVAILABILITY_BASIS_STATIC:
        return True
    bound = latest_available_bound(feature_availability, decision_date)
    return _time_eligible_of(bound, decision_cutoff)


def feature_available_at_display(feature_availability: Dict[str, Any],
                                 decision_date: str) -> str:
    """UI 展示的 available_at 字符串（表达与 Time Gate 判定的**同一个上界**，紧凑版）。

    - STATIC            → "静态/日历（恒可用）"
    - STRUCTURAL_LAG    → "≤ {decision_date} 00:00 PT"（最晚可证上界，非编造精确时刻）
    - ASSUMED_AVAILABLE → "≤ {decision_date} 00:00 PT（assumed）"
    - KNOWN_PUBLICATION → "{available_at}"（有精确发布时间）
    - UNKNOWN / 缺字段  → "UNKNOWN（无可用上界 → 判不可用）"

    完整注解（basis / source_target_date / 无精确发布时刻）由调用方通过结构化字段补充。
    """
    basis = _coerce_str(feature_availability.get(AVAILABILITY_BASIS_KEY))
    dd = str(decision_date)[:10]
    if basis == AVAILABILITY_BASIS_STATIC:
        return "静态/日历（恒可用）"
    if basis == AVAILABILITY_BASIS_STRUCTURAL_LAG:
        return f"≤ {dd} 00:00 PT"
    if basis == AVAILABILITY_BASIS_ASSUMED_AVAILABLE:
        return f"≤ {dd} 00:00 PT（assumed）"
    if basis == AVAILABILITY_BASIS_KNOWN_PUBLICATION:
        return f"{_coerce_str(feature_availability.get('available_at'))}"
    rule = _coerce_str(feature_availability.get(LATEST_POSSIBLE_AVAILABLE_AT_KEY))
    if rule == BOUND_RULE_DECISION_DATE_00_PT:
        return f"≤ {dd} 00:00 PT"
    return rule or "UNKNOWN（无可用上界 → 判不可用）"


# ---------------------------------------------------------------------------
# 数值工具
# ---------------------------------------------------------------------------
def _coerce_float(value: Any) -> Optional[float]:
    """数值化；空 / NaN / 不可转 → None（NaN 视为缺失，满足 R3）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """布尔规约：真布尔原样返回；字符串按 true/1/yes 等解析，避免 bool("false")=True。"""
    if isinstance(value, bool):
        return value
    s = _coerce_str(value).lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off", ""):
        return False
    return default


def _default_asof_id(source: str, raw_source_id: str, target_time: Any) -> str:
    tt = _coerce_str(target_time).replace(":", "").replace("-", "").replace("T", "_")
    return f"ASOF-{_coerce_str(source) or 'src'}-{_coerce_str(raw_source_id) or 'row'}-{tt or 't'}"[:200]


# ---------------------------------------------------------------------------
# AsOfRecord
# ---------------------------------------------------------------------------
@dataclass
class AsOfRecord:
    """一条 vintage 化的原子事实（可追溯 / 防穿越的核心单元）。

    时间字段一律存 UTC naive ISO（YYYY-MM-DDTHH:MM:SS）。
      model_run_time : 模型起报时刻（= initialization_time；GFS run 起始）
      published_at   : 源方公开发布时刻（交易员最早可得）
      available_at   : 本项目采用的可用于决策的 as-of 时点（按模式解析，R4/R5/R10）
      retrieved_at   : 我方采集/落库时刻（仅审计）
      target_time    : 该值指向的交付时刻
      decision_cutoff : 该记录对应的决策截止
    """

    source: str = ""
    source_type: str = ""
    is_mock: bool = False
    not_backtest_safe: bool = False
    field_name: str = ""
    forecast_run: str = ""
    model_run_time: str = ""
    issue_time: str = ""
    published_at: str = ""
    available_at: str = ""
    retrieved_at: str = ""
    target_time: str = ""
    node: str = ""
    region: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    value: Optional[float] = None
    decision_cutoff: str = ""
    raw_source_id: str = ""
    version: str = SCHEMA_VERSION
    mode: str = ""
    asof_id: str = ""
    market_rule_version: str = CURRENT_MARKET_RULE_VERSION

    # ------------------------------------------------------------------ 时间解析
    @property
    def parsed_model_run_time(self) -> Optional[datetime]:
        return parse_timestamp(self.model_run_time)

    @property
    def parsed_published_at(self) -> Optional[datetime]:
        return parse_timestamp(self.published_at)

    @property
    def parsed_available_at(self) -> Optional[datetime]:
        return parse_timestamp(self.available_at)

    @property
    def parsed_retrieved_at(self) -> Optional[datetime]:
        return parse_timestamp(self.retrieved_at)

    @property
    def parsed_target_time(self) -> Optional[datetime]:
        return parse_timestamp(self.target_time)

    @property
    def parsed_decision_cutoff(self) -> Optional[datetime]:
        return parse_timestamp(self.decision_cutoff)

    # ------------------------------------------------------------------ 判定
    @property
    def time_eligible(self) -> bool:
        """R1/R2 纯时间门槛：available_at <= decision_cutoff 且两时间齐全才 TRUE。

        程序计算，禁止人工/LLM 覆盖。任一时间缺失/不可解析 → FALSE（保守）。
        """
        a = self.parsed_available_at
        c = self.parsed_decision_cutoff
        if a is None or c is None:
            return False
        try:
            return a <= c
        except Exception:
            return False

    @property
    def decision_eligible(self) -> bool:
        """模式对应的最终可用性（R7/R8 MOCK / NOT_BACKTEST_SAFE 硬隔离）。

        PRODUCTION → production_eligible；其余（BACKTEST/空）→ backtest_eligible（最保守）。
        程序计算，禁止人工/LLM 覆盖。MOCK 数据此处恒为 FALSE。
        """
        if self.mode == MODE_PRODUCTION:
            return self.production_eligible
        return self.backtest_eligible

    @property
    def backtest_eligible(self) -> bool:
        """该记录可否进入严格 as-of 回测（R4/R7/R8/R9/R10 语义）。

        = time_eligible 且 非 MOCK 且 源非 NOT_BACKTEST_SAFE 且 非生产采集模式，
          且（若有 model_run_time）available_at 严格晚于 model_run_time——
          拒绝把"初始化时刻"当"可用时刻"的退化（发布延迟必须为正，R9）。
        任一条不满足 → False（宁保守不穿越；MOCK 即便 available_at<=cutoff 也为 False）。
        """
        if self.is_mock:
            return False
        if self.not_backtest_safe:
            return False
        if self.mode == MODE_PRODUCTION:
            return False
        if not self.time_eligible:
            return False
        rt = self.parsed_model_run_time
        av = self.parsed_available_at
        if rt is not None and av is not None and av <= rt:
            return False
        return True

    @property
    def production_eligible(self) -> bool:
        """该记录可否进入生产决策（R5/R7 语义）。

        = time_eligible 且 非 MOCK 且 非回测采集模式 且 published/retrieved 齐备
          （生产口径 available_at = max(published, retrieved)）。
        MOCK 即便 available_at<=cutoff 也为 False。
        """
        if self.is_mock:
            return False
        if self.mode == MODE_BACKTEST:
            return False
        if not self.time_eligible:
            return False
        if parse_timestamp(self.published_at) is None or parse_timestamp(self.retrieved_at) is None:
            return False
        return True

    @property
    def is_usable(self) -> bool:
        """R3：可进 feature_snapshot 可用侧 = decision_eligible 且核心字段齐全。"""
        return (
            self.decision_eligible
            and self.parsed_target_time is not None
            and self.value is not None
            and bool(self.source and self.field_name and self.node)
        )

    @property
    def lead_hours(self) -> Optional[float]:
        return lead_hours_of(self.target_time, self.available_at)

    @property
    def missing_time_fields(self) -> List[str]:
        """哪些关键时间缺失/不可解析（审计用）。"""
        miss = []
        for k in ("published_at", "available_at", "retrieved_at", "target_time", "decision_cutoff"):
            if parse_timestamp(getattr(self, k)) is None:
                miss.append(k)
        return miss

    # ------------------------------------------------------------------ 规范化
    def normalize(self) -> "AsOfRecord":
        self.source = _coerce_str(self.source)
        # source_type：显式值优先；未给则按 source/field_name 启发式推断
        if self.source_type not in SOURCE_TYPES:
            self.source_type = infer_source_type(self.source, self.field_name)
        self.is_mock = _coerce_bool(self.is_mock)
        self.not_backtest_safe = _coerce_bool(self.not_backtest_safe)
        self.field_name = _coerce_str(self.field_name)
        self.forecast_run = _coerce_str(self.forecast_run)
        self.model_run_time = _coerce_str(self.model_run_time)
        self.issue_time = _coerce_str(self.issue_time)
        self.published_at = _coerce_str(self.published_at)
        self.available_at = _coerce_str(self.available_at)
        self.retrieved_at = _coerce_str(self.retrieved_at)
        self.target_time = _coerce_str(self.target_time)
        self.node = _coerce_str(self.node)
        if not self.region and self.node in NODE_REGION:
            self.region = NODE_REGION[self.node]
        if self.region not in ("ZP26", "SP15", "NP15", "SYSTEM", ""):
            self.region = ""
        self.latitude = _coerce_float(self.latitude)
        self.longitude = _coerce_float(self.longitude)
        if self.latitude is None and self.node in NODE_COORDS:
            self.latitude = NODE_COORDS[self.node][0]
            self.longitude = NODE_COORDS[self.node][1]
        self.value = _coerce_float(self.value)
        self.decision_cutoff = _coerce_str(self.decision_cutoff)
        self.raw_source_id = _coerce_str(self.raw_source_id)
        self.version = _coerce_str(self.version) or SCHEMA_VERSION
        self.mode = self.mode if self.mode in MODES else ""
        self.market_rule_version = normalize_market_rule_version(self.market_rule_version)
        if not self.asof_id:
            self.asof_id = _default_asof_id(self.source, self.raw_source_id, self.target_time)
        return self

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        self.normalize()
        return {
            "asof_id": self.asof_id,
            "source": self.source,
            "source_type": self.source_type,
            "is_mock": bool(self.is_mock),
            "not_backtest_safe": bool(self.not_backtest_safe),
            "field_name": self.field_name,
            "forecast_run": self.forecast_run,
            "model_run_time": self.model_run_time,
            "issue_time": self.issue_time,
            "published_at": self.published_at,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "target_time": self.target_time,
            "lead_hours": self.lead_hours,
            "node": self.node,
            "region": self.region,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "value": self.value,
            "decision_cutoff": self.decision_cutoff,
            "decision_eligible": bool(self.decision_eligible),
            "time_eligible": bool(self.time_eligible),
            "backtest_eligible": bool(self.backtest_eligible),
            "production_eligible": bool(self.production_eligible),
            "raw_source_id": self.raw_source_id,
            "version": self.version,
            "mode": self.mode,
            "market_rule_version": self.market_rule_version,
        }

    def to_jsonable(self) -> Dict[str, Any]:
        return self.to_dict()


def asof_from_dict(raw: Dict[str, Any]) -> AsOfRecord:
    """从任意 dict 安全构造 AsOfRecord（缺字段补默认）。"""
    rec = AsOfRecord(
        source=raw.get("source", ""),
        source_type=raw.get("source_type", ""),
        is_mock=_coerce_bool(raw.get("is_mock", False)),
        not_backtest_safe=_coerce_bool(raw.get("not_backtest_safe", False)),
        field_name=raw.get("field_name", ""),
        forecast_run=raw.get("forecast_run", ""),
        model_run_time=raw.get("model_run_time", ""),
        issue_time=raw.get("issue_time", ""),
        published_at=raw.get("published_at", ""),
        available_at=raw.get("available_at", ""),
        retrieved_at=raw.get("retrieved_at", ""),
        target_time=raw.get("target_time", ""),
        node=raw.get("node", ""),
        region=raw.get("region", ""),
        latitude=raw.get("latitude"),
        longitude=raw.get("longitude"),
        value=raw.get("value"),
        decision_cutoff=raw.get("decision_cutoff", ""),
        raw_source_id=raw.get("raw_source_id", ""),
        version=raw.get("version", SCHEMA_VERSION),
        mode=raw.get("mode", ""),
        asof_id=raw.get("asof_id", ""),
        market_rule_version=raw.get("market_rule_version", CURRENT_MARKET_RULE_VERSION),
    )
    return rec.normalize()


def ensure_asof_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    """任意输入 → 标准 AsOfRecord dict（decision_eligible 程序计算）。"""
    return asof_from_dict(raw).to_dict()


def validate_asof_record(rec: Dict[str, Any]) -> List[str]:
    """校验一个 AsOfRecord dict，返回所有违规项（空列表 = 通过）。

    只校验"结构/时间口径/取值合法性"，不校验"事实真假"——真假由数据源负责。
    """
    errors: List[str] = []
    missing = [k for k in ASOF_KEYS if k not in rec]
    if missing:
        errors.append(f"缺少字段: {missing}")

    def _must_ts(field: str) -> None:
        if parse_timestamp(rec.get(field)) is None:
            errors.append(f"{field} 缺失或不可解析: {rec.get(field)!r}（要求 UTC naive ISO）")

    _must_ts("published_at")
    _must_ts("available_at")
    _must_ts("retrieved_at")
    _must_ts("target_time")
    _must_ts("decision_cutoff")

    if not _coerce_str(rec.get("source")):
        errors.append("source 缺失")
    if not _coerce_str(rec.get("field_name")):
        errors.append("field_name 缺失")
    if not _coerce_str(rec.get("node")):
        errors.append("node 缺失")
    if _coerce_float(rec.get("value")) is None:
        errors.append(f"value 缺失/非数值/NaN: {rec.get('value')!r}")

    mode = _coerce_str(rec.get("mode"))
    if mode and mode not in MODES:
        errors.append(f"mode 非法: {mode!r}（允许: {MODES}）")

    # Provenance 字段合法性
    st = _coerce_str(rec.get("source_type"))
    if st and st not in SOURCE_TYPES:
        errors.append(f"source_type 非法: {st!r}（允许: {SOURCE_TYPES}）")
    if not isinstance(rec.get("is_mock"), bool):
        errors.append(f"is_mock 非布尔: {rec.get('is_mock')!r}")
    if not isinstance(rec.get("not_backtest_safe"), bool):
        errors.append(f"not_backtest_safe 非布尔: {rec.get('not_backtest_safe')!r}")
    mrv = _coerce_str(rec.get("market_rule_version"))
    if mrv and mrv not in MARKET_RULE_VERSIONS:
        errors.append(f"market_rule_version 非法: {mrv!r}（允许: {MARKET_RULE_VERSIONS}）")

    # 时间逻辑一致性（R1/R2/R4/R5/R9）
    if mode == MODE_BACKTEST:
        if parse_timestamp(rec.get("available_at")) is None:
            errors.append("BACKTEST 模式: available_at 必须来自历史 vintage，缺失即 NOT_BACKTEST_SAFE")
        # R9：available_at 必须严格晚于 model_run_time（发布延迟为正；禁止 init 当 available）
        rt = parse_timestamp(rec.get("model_run_time"))
        av = parse_timestamp(rec.get("available_at"))
        if rt is not None and av is not None and av <= rt:
            errors.append("BACKTEST 模式: available_at 必须严格晚于 model_run_time"
                          "（发布/下载延迟为正；不可把初始化时刻当可用时刻）")
    if mode == MODE_PRODUCTION:
        pub = parse_timestamp(rec.get("published_at"))
        ret = parse_timestamp(rec.get("retrieved_at"))
        if pub is None or ret is None:
            errors.append("PRODUCTION 模式: available_at = max(published, retrieved)，两者缺一不可")

    # eligibility 三拆复算 + 硬规则（R7/R8）：程序计算，存储漂移即违规
    try:
        obj = asof_from_dict(rec)
    except Exception:
        obj = None
    if obj is not None:
        for key in ("time_eligible", "backtest_eligible", "production_eligible", "decision_eligible"):
            if bool(rec.get(key)) != bool(getattr(obj, key)):
                errors.append(
                    f"{key} 漂移: stored={bool(rec.get(key))} "
                    f"recomputed={bool(getattr(obj, key))}")
        # 硬规则以“存储声明”为准：is_mock/not_backtest_safe 由对象归一化判定，
        # eligibility 由存储 dict 声明——二者冲突即违规（R7/R8）。
        if obj.is_mock and (bool(rec.get("backtest_eligible")) or bool(rec.get("production_eligible"))):
            errors.append(
                "硬规则 R7: is_mock=True 禁止声明 backtest_eligible / production_eligible "
                "（MOCK 仅用于测试/演示，即便 available_at<=cutoff 也不行）")
        if obj.not_backtest_safe and bool(rec.get("backtest_eligible")):
            errors.append(
                "硬规则 R8: not_backtest_safe=True 禁止声明 backtest_eligible "
                "（strict as-of 回测不可用）")
        if obj.is_mock and bool(rec.get("decision_eligible")):
            errors.append("硬规则: is_mock=True 记录 decision_eligible 必须为 FALSE")
    return errors


# ---------------------------------------------------------------------------
# available_at 解析（两套采集模式，R4/R5）
# ---------------------------------------------------------------------------
def resolve_available_at(
    published_at: Any,
    retrieved_at: Any,
    mode: str = MODE_PRODUCTION,
) -> Optional[str]:
    """按采集模式计算 available_at（UTC naive ISO）。

    BACKTEST : 只认历史 vintage（published_at）；retrieved_at 仅审计，绝不用它
               主张历史可用（禁止"今天查历史真实值假装过去知道"）。
    PRODUCTION: 两者缺一返回 None（保守）；否则取 max(published, retrieved)
                —— 源没发布、或我们没拉到，都不可用。
    """
    pub = parse_timestamp(published_at)
    if mode == MODE_BACKTEST:
        return _fmt(pub) if pub is not None else None
    ret = parse_timestamp(retrieved_at)
    if pub is None or ret is None:
        return None
    return _fmt(max(pub, ret))


def gate_asof_records(
    records: Sequence[AsOfRecord],
    decision_cutoff: Optional[str] = None,
) -> Tuple[List[AsOfRecord], List[AsOfRecord]]:
    """按时间门槛切分。

    Returns:
        (eligible, post_decision)
          eligible      : decision_eligible=True（Pre-decision，可进 feature_snapshot）
          post_decision : decision_eligible=False（只进 Post-trade Review）
    """
    eligible: List[AsOfRecord] = []
    post: List[AsOfRecord] = []
    for rec in records:
        probe = rec
        if decision_cutoff:
            probe = AsOfRecord(**{**rec.__dict__, "decision_cutoff": decision_cutoff}).normalize()
        (eligible if probe.decision_eligible else post).append(rec)
    return eligible, post


def assert_no_post_decision(records: Sequence[AsOfRecord],
                            decision_cutoff: Optional[str] = None) -> None:
    """防御性断言：post-decision 记录误入决策层直接抛错。"""
    _, post = gate_asof_records(records, decision_cutoff)
    if post:
        ids = [r.asof_id or f"{r.source}:{r.target_time}" for r in post]
        raise RuntimeError(
            "As-of Time Gate: 检测到 %d 条 Post-decision 记录进入决策层，已拦截: %s"
            % (len(post), ids)
        )


# ---------------------------------------------------------------------------
# feature_snapshot
# ---------------------------------------------------------------------------
@dataclass
class FeatureSnapshot:
    """决策日冻结的特征行；可沿 asof_record_id 溯源到 AsOfRecord → raw response。

    Provenance 铁律：任何进入模型/Risk Gate 的字段都必须可追溯
      source / source_type / is_mock / raw_source_id / target_time /
      available_at / retrieved_at / time_eligible / backtest_eligible /
      production_eligible / feature_value。
    本行复制自来源 AsOfRecord（normalize 时程序计算），禁止人工/LLM 覆盖。
    """

    snapshot_id: str = ""
    decision_date: str = ""
    decision_cutoff: str = ""
    created_at: str = ""
    node: str = ""
    target_date: str = ""
    target_hour: int = 0
    target_time: str = ""
    feature_name: str = ""
    feature_value: Optional[float] = None
    source: str = ""
    source_type: str = ""
    is_mock: bool = False
    raw_source_id: str = ""
    available_at: str = ""
    retrieved_at: str = ""
    time_eligible: bool = False
    backtest_eligible: bool = False
    production_eligible: bool = False
    decision_eligible: bool = False
    asof_record_id: str = ""
    version: str = SCHEMA_VERSION
    market_rule_version: str = CURRENT_MARKET_RULE_VERSION

    @property
    def is_usable(self) -> bool:
        return (
            self.decision_eligible
            and self.feature_value is not None
            and bool(self.node and self.target_date and self.feature_name)
        )

    def normalize(self) -> "FeatureSnapshot":
        self.decision_date = _coerce_str(self.decision_date)
        self.decision_cutoff = _coerce_str(self.decision_cutoff)
        self.created_at = _coerce_str(self.created_at)
        self.node = _coerce_str(self.node)
        self.target_date = _coerce_str(self.target_date)
        try:
            self.target_hour = int(self.target_hour)
        except (TypeError, ValueError):
            self.target_hour = 0
        # target_time：显式值优先；缺省由 (target_date, target_hour) 派生
        if not self.target_time:
            self.target_time = target_time_pt_to_utc(self.target_date, self.target_hour) or ""
        self.feature_name = _coerce_str(self.feature_name)
        self.feature_value = _coerce_float(self.feature_value)
        self.source = _coerce_str(self.source)
        # source_type：显式值优先；未给则按 source/feature_name 启发式推断
        if self.source_type not in SOURCE_TYPES:
            self.source_type = infer_source_type(self.source, self.feature_name)
        self.is_mock = _coerce_bool(self.is_mock)
        self.raw_source_id = _coerce_str(self.raw_source_id)
        self.available_at = _coerce_str(self.available_at)
        self.retrieved_at = _coerce_str(self.retrieved_at)
        # 三个 eligibility 一律由字段程序化判定（不可人工/LLM 覆盖）
        self.time_eligible = bool(self.time_eligible)
        self.backtest_eligible = bool(self.backtest_eligible)
        self.production_eligible = bool(self.production_eligible)
        self.decision_eligible = bool(self.decision_eligible)
        self.asof_record_id = _coerce_str(self.asof_record_id)
        self.version = _coerce_str(self.version) or SCHEMA_VERSION
        self.market_rule_version = normalize_market_rule_version(self.market_rule_version)
        if not self.snapshot_id:
            self.snapshot_id = (
                f"SNAP-{self.decision_date}-{self.node}-{self.target_date}"
                f"-H{self.target_hour}-{self.feature_name}"
            )
        return self

    def to_dict(self) -> Dict[str, Any]:
        self.normalize()
        return {
            "snapshot_id": self.snapshot_id,
            "decision_date": self.decision_date,
            "decision_cutoff": self.decision_cutoff,
            "created_at": self.created_at,
            "node": self.node,
            "target_date": self.target_date,
            "target_hour": self.target_hour,
            "target_time": self.target_time,
            "feature_name": self.feature_name,
            "feature_value": self.feature_value,
            "source": self.source,
            "source_type": self.source_type,
            "is_mock": bool(self.is_mock),
            "raw_source_id": self.raw_source_id,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "time_eligible": bool(self.time_eligible),
            "backtest_eligible": bool(self.backtest_eligible),
            "production_eligible": bool(self.production_eligible),
            "decision_eligible": bool(self.decision_eligible),
            "asof_record_id": self.asof_record_id,
            "version": self.version,
            "market_rule_version": self.market_rule_version,
        }

    def to_jsonable(self) -> Dict[str, Any]:
        return self.to_dict()


def snapshot_from_asof_record(
    rec: AsOfRecord,
    decision_date: str,
    target_date: str,
    target_hour: int,
    feature_name: str,
    created_at: Optional[str] = None,
) -> FeatureSnapshot:
    """从一条 AsOfRecord 派生当日 feature_snapshot 行。

    Provenance 字段（source_type / is_mock / raw_source_id / retrieved_at /
    target_time / time_eligible / backtest_eligible / production_eligible /
    market_rule_version）全部复制自来源 AsOfRecord（程序计算）；
    created_at 默认 = 当前 UTC。
    """
    rec.normalize()
    created = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return FeatureSnapshot(
        decision_date=str(decision_date)[:10],
        decision_cutoff=rec.decision_cutoff,
        created_at=created,
        node=rec.node,
        target_date=str(target_date)[:10],
        target_hour=target_hour,
        target_time=rec.target_time,
        feature_name=feature_name,
        feature_value=rec.value,
        source=rec.source,
        source_type=rec.source_type,
        is_mock=bool(rec.is_mock),
        raw_source_id=rec.raw_source_id,
        available_at=rec.available_at,
        retrieved_at=rec.retrieved_at,
        time_eligible=bool(rec.time_eligible),
        backtest_eligible=bool(rec.backtest_eligible),
        production_eligible=bool(rec.production_eligible),
        decision_eligible=bool(rec.decision_eligible),
        asof_record_id=rec.asof_id,
        version=rec.version,
        market_rule_version=rec.market_rule_version,
    ).normalize()


def snapshot_from_dict(raw: Dict[str, Any]) -> FeatureSnapshot:
    return FeatureSnapshot(
        snapshot_id=raw.get("snapshot_id", ""),
        decision_date=raw.get("decision_date", ""),
        decision_cutoff=raw.get("decision_cutoff", ""),
        created_at=raw.get("created_at", ""),
        node=raw.get("node", ""),
        target_date=raw.get("target_date", ""),
        target_hour=raw.get("target_hour", 0),
        target_time=raw.get("target_time", ""),
        feature_name=raw.get("feature_name", ""),
        feature_value=raw.get("feature_value"),
        source=raw.get("source", ""),
        source_type=raw.get("source_type", ""),
        is_mock=_coerce_bool(raw.get("is_mock", False)),
        raw_source_id=raw.get("raw_source_id", ""),
        available_at=raw.get("available_at", ""),
        retrieved_at=raw.get("retrieved_at", ""),
        time_eligible=bool(raw.get("time_eligible", False)),
        backtest_eligible=bool(raw.get("backtest_eligible", False)),
        production_eligible=bool(raw.get("production_eligible", False)),
        decision_eligible=bool(raw.get("decision_eligible", False)),
        asof_record_id=raw.get("asof_record_id", ""),
        version=raw.get("version", SCHEMA_VERSION),
        market_rule_version=raw.get("market_rule_version", CURRENT_MARKET_RULE_VERSION),
    ).normalize()


def validate_snapshot(snap: Dict[str, Any]) -> List[str]:
    """校验 feature_snapshot dict，返回违规项（空列表 = 通过）。"""
    errors: List[str] = []
    missing = [k for k in SNAPSHOT_KEYS if k not in snap]
    if missing:
        errors.append(f"缺少字段: {missing}")
    for f in ("decision_cutoff", "created_at", "available_at"):
        if parse_timestamp(snap.get(f)) is None:
            errors.append(f"{f} 缺失或不可解析: {snap.get(f)!r}")
    if not _coerce_str(snap.get("decision_date")):
        errors.append("decision_date 缺失")
    if not _coerce_str(snap.get("target_date")):
        errors.append("target_date 缺失")
    try:
        h = int(snap.get("target_hour"))
        if not (1 <= h <= 24):
            errors.append(f"target_hour 越界: {h}")
    except (TypeError, ValueError):
        errors.append(f"target_hour 非整数: {snap.get('target_hour')!r}")
    if not _coerce_str(snap.get("feature_name")):
        errors.append("feature_name 缺失")
    if _coerce_float(snap.get("feature_value")) is None:
        errors.append(f"feature_value 缺失/非数值/NaN: {snap.get('feature_value')!r}")
    if not _coerce_str(snap.get("asof_record_id")):
        errors.append("asof_record_id 缺失（无法溯源）")
    if not _coerce_str(snap.get("source")):
        errors.append("source 缺失（provenance）")
    if not _coerce_str(snap.get("raw_source_id")):
        errors.append("raw_source_id 缺失（provenance，无法还原原始响应）")
    st = _coerce_str(snap.get("source_type"))
    if st and st not in SOURCE_TYPES:
        errors.append(f"source_type 非法: {st!r}（允许: {SOURCE_TYPES}）")
    if not isinstance(snap.get("is_mock"), bool):
        errors.append(f"is_mock 非布尔: {snap.get('is_mock')!r}")
    if not _coerce_str(snap.get("target_time")):
        errors.append("target_time 缺失或不可解析（provenance）")
    else:
        # 一致性：target_time 应由 (target_date, target_hour) 可复算
        expected_tt = target_time_pt_to_utc(snap.get("target_date"), snap.get("target_hour"))
        if expected_tt and snap.get("target_time") != expected_tt:
            errors.append(
                f"target_time 与 (target_date, target_hour) 不一致: "
                f"{snap.get('target_time')!r} != {expected_tt!r}")
    mrv = _coerce_str(snap.get("market_rule_version"))
    if mrv and mrv not in MARKET_RULE_VERSIONS:
        errors.append(f"market_rule_version 非法: {mrv!r}（允许: {MARKET_RULE_VERSIONS}）")

    # eligibility 硬规则（R7/R8）：MOCK 快照绝不参与回测/生产
    is_mock = _coerce_bool(snap.get("is_mock"))
    if is_mock and (snap.get("backtest_eligible") or snap.get("production_eligible")):
        errors.append(
            "硬规则 R7: is_mock=True 禁止 backtest_eligible / production_eligible "
            "（MOCK 仅用于测试/演示）")
    if is_mock and snap.get("decision_eligible"):
        errors.append("硬规则: is_mock=True 快照 decision_eligible 必须为 FALSE")
    if snap.get("decision_eligible") and not snap.get("time_eligible"):
        errors.append("硬规则: decision_eligible=True 但 time_eligible=False（时间不通过）")
    # time_eligible 可复算（available_at <= decision_cutoff）→ 漂移即违规
    recomputed_time = _time_eligible_of(snap.get("available_at"), snap.get("decision_cutoff"))
    if bool(snap.get("time_eligible")) != recomputed_time:
        errors.append(
            f"time_eligible 漂移: stored={bool(snap.get('time_eligible'))} "
            f"recomputed={recomputed_time}")
    return errors


def _time_eligible_of(available_at: Any, decision_cutoff: Any) -> bool:
    """纯时间门槛复算（供 FeatureSnapshot 漂移校验）。"""
    a = parse_timestamp(available_at)
    c = parse_timestamp(decision_cutoff)
    if a is None or c is None:
        return False
    try:
        return a <= c
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 自检演示
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # GFS 回测样例：决策日 2026-07-08，06Z run（可回测 cycle），目标 07-09
    cutoff = make_decision_cutoff("2026-07-08")
    rec = asof_from_dict({
        "source": "NCEP_GFS_025_via_OpenMeteo",
        "field_name": "t2m",
        "forecast_run": "2026-07-08T06:00Z",
        "model_run_time": "2026-07-08T06:00:00",     # 起报时刻 = initialization_time
        "issue_time": "2026-07-08T06:00:00",
        "published_at": "2026-07-08T12:00:00",       # init + 6h 保守上界（发布延迟模型）
        "retrieved_at": "2026-08-09T00:00:00",        # 今天（审计用）
        "available_at": None,                          # 由模式解析
        "target_time": target_time_pt_to_utc("2026-07-09", 15),
        "node": "CONTROLX_1_N001",
        "value": 27.9,
        "decision_cutoff": cutoff,
        "raw_source_id": "run=2026-07-08T06:00",
        "mode": MODE_BACKTEST,
    })
    rec.available_at = resolve_available_at(
        rec.published_at, rec.retrieved_at, mode=MODE_BACKTEST) or ""

    def sp(x):
        try:
            print(x)
        except Exception:
            print(str(x).encode("ascii", "replace").decode("ascii"))

    sp("cutoff(UTC): " + str(cutoff))
    sp("model_run_time: " + rec.model_run_time)
    sp("available_at(BACKTEST=init+6h): " + rec.available_at)
    sp("time_eligible: " + str(rec.time_eligible))
    sp("backtest_eligible: " + str(rec.backtest_eligible))
    sp("decision_eligible: " + str(rec.decision_eligible))
    sp("lead_hours: " + str(rec.lead_hours))
    snap = snapshot_from_asof_record(rec, "2026-07-08", "2026-07-09", 15, "t2m").to_dict()
    sp("snapshot_id: " + snap["snapshot_id"])
    sp("validate_asof errors: " + str(validate_asof_record(rec.to_dict())))
    sp("validate_snapshot errors: " + str(validate_snapshot(snap)))
