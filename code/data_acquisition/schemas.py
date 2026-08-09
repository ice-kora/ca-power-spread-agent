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
  R1  available_at <= decision_cutoff  ⇒ decision_eligible = TRUE，否则 FALSE
  R2  任一时间缺失 / 不可解析         ⇒ decision_eligible = FALSE（宁保守不穿越）
  R3  decision_eligible 且 target_time 可解析 且 value 非 NaN 且 identity 非空 ⇒ is_usable
  R4  回测模式 available_at 必须来自历史 vintage；禁止用 retrieved_at(=今天) 主张历史可用
  R5  生产模式 available_at = max(published_at, retrieved_at)；先存 raw 再记 retrieved
  R6  snapshot 追加写不可变；post 记录只进复盘

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

# ---------------------------------------------------------------------------
# 常量与枚举（单一事实来源）
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "asof_v1"

MODE_BACKTEST = "BACKTEST"
MODE_PRODUCTION = "PRODUCTION"
MODES: tuple = (MODE_BACKTEST, MODE_PRODUCTION)

#: 决策截止的本地时刻（DAM Market Close / bid cutoff，官方 BPM "closes at 1000 hours"）
DECISION_CUTOFF_LOCAL = "10:00:00"
TZ_PT = "America/Los_Angeles"

#: As-of 记录的标准字段顺序（含派生/审计字段；decision_eligible 为计算属性不入存储列）
ASOF_KEYS: tuple = (
    "asof_id", "source", "field_name", "forecast_run", "issue_time",
    "published_at", "available_at", "retrieved_at", "target_time", "lead_hours",
    "node", "region", "latitude", "longitude", "value",
    "decision_cutoff", "decision_eligible", "raw_source_id", "version", "mode",
)

#: feature_snapshot 的标准字段顺序
SNAPSHOT_KEYS: tuple = (
    "snapshot_id", "decision_date", "decision_cutoff", "created_at", "node",
    "target_date", "target_hour", "feature_name", "feature_value", "source",
    "available_at", "decision_eligible", "asof_record_id", "version",
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
      published_at  : 源方公开发布时刻（交易员最早可得）
      available_at  : 本项目采用的可用于决策的 as-of 时点（按模式解析，R4/R5）
      retrieved_at  : 我方采集/落库时刻（仅审计）
      target_time   : 该值指向的交付时刻
      decision_cutoff : 该记录对应的决策截止
    """

    source: str = ""
    field_name: str = ""
    forecast_run: str = ""
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

    # ------------------------------------------------------------------ 时间解析
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
    def decision_eligible(self) -> bool:
        """R1/R2：available_at <= decision_cutoff 且两时间齐全才 TRUE。

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
        self.field_name = _coerce_str(self.field_name)
        self.forecast_run = _coerce_str(self.forecast_run)
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
        if not self.asof_id:
            self.asof_id = _default_asof_id(self.source, self.raw_source_id, self.target_time)
        return self

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        self.normalize()
        return {
            "asof_id": self.asof_id,
            "source": self.source,
            "field_name": self.field_name,
            "forecast_run": self.forecast_run,
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
            "raw_source_id": self.raw_source_id,
            "version": self.version,
            "mode": self.mode,
        }

    def to_jsonable(self) -> Dict[str, Any]:
        return self.to_dict()


def asof_from_dict(raw: Dict[str, Any]) -> AsOfRecord:
    """从任意 dict 安全构造 AsOfRecord（缺字段补默认）。"""
    rec = AsOfRecord(
        source=raw.get("source", ""),
        field_name=raw.get("field_name", ""),
        forecast_run=raw.get("forecast_run", ""),
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

    # 时间逻辑一致性（R1/R2/R4/R5）
    if mode == MODE_BACKTEST:
        if parse_timestamp(rec.get("available_at")) is None:
            errors.append("BACKTEST 模式: available_at 必须来自历史 vintage，缺失即 NOT_BACKTEST_SAFE")
    if mode == MODE_PRODUCTION:
        pub = parse_timestamp(rec.get("published_at"))
        ret = parse_timestamp(rec.get("retrieved_at"))
        if pub is None or ret is None:
            errors.append("PRODUCTION 模式: available_at = max(published, retrieved)，两者缺一不可")
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
    """决策日冻结的特征行；可沿 asof_record_id 溯源到 AsOfRecord → raw response。"""

    snapshot_id: str = ""
    decision_date: str = ""
    decision_cutoff: str = ""
    created_at: str = ""
    node: str = ""
    target_date: str = ""
    target_hour: int = 0
    feature_name: str = ""
    feature_value: Optional[float] = None
    source: str = ""
    available_at: str = ""
    decision_eligible: bool = False
    asof_record_id: str = ""
    version: str = SCHEMA_VERSION

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
        self.feature_name = _coerce_str(self.feature_name)
        self.feature_value = _coerce_float(self.feature_value)
        self.source = _coerce_str(self.source)
        self.available_at = _coerce_str(self.available_at)
        self.asof_record_id = _coerce_str(self.asof_record_id)
        self.version = _coerce_str(self.version) or SCHEMA_VERSION
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
            "feature_name": self.feature_name,
            "feature_value": self.feature_value,
            "source": self.source,
            "available_at": self.available_at,
            "decision_eligible": bool(self.decision_eligible),
            "asof_record_id": self.asof_record_id,
            "version": self.version,
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

    decision_eligible 复制自 AsOfRecord（程序计算）；created_at 默认 = 当前 UTC。
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
        feature_name=feature_name,
        feature_value=rec.value,
        source=rec.source,
        available_at=rec.available_at,
        decision_eligible=bool(rec.decision_eligible),
        asof_record_id=rec.asof_id,
        version=rec.version,
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
        feature_name=raw.get("feature_name", ""),
        feature_value=raw.get("feature_value"),
        source=raw.get("source", ""),
        available_at=raw.get("available_at", ""),
        decision_eligible=bool(raw.get("decision_eligible", False)),
        asof_record_id=raw.get("asof_record_id", ""),
        version=raw.get("version", SCHEMA_VERSION),
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
    return errors


# ---------------------------------------------------------------------------
# 自检演示
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # GFS 回测样例：决策日 2026-07-08，12Z run，目标 07-09
    cutoff = make_decision_cutoff("2026-07-08")
    rec = asof_from_dict({
        "source": "NCEP_GFS_025_via_OpenMeteo",
        "field_name": "t2m",
        "forecast_run": "2026-07-08T12:00Z",
        "issue_time": "2026-07-08T12:00:00",
        "published_at": "2026-07-08T12:00:00",       # GFS 12Z init
        "retrieved_at": "2026-08-09T00:00:00",        # 今天（审计用）
        "available_at": None,                          # 由模式解析
        "target_time": target_time_pt_to_utc("2026-07-09", 15),
        "node": "CONTROLX_1_N001",
        "value": 27.9,
        "decision_cutoff": cutoff,
        "raw_source_id": "run=2026-07-08T12:00",
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
    sp("available_at(BACKTEST=vintage): " + rec.available_at)
    sp("decision_eligible: " + str(rec.decision_eligible))
    sp("lead_hours: " + str(rec.lead_hours))
    snap = snapshot_from_asof_record(rec, "2026-07-08", "2026-07-09", 15, "t2m").to_dict()
    sp("snapshot_id: " + snap["snapshot_id"])
    sp("validate_asof errors: " + str(validate_asof_record(rec.to_dict())))
    sp("validate_snapshot errors: " + str(validate_snapshot(snap)))
