# -*- coding: utf-8 -*-
"""
agent/evidence/schema.py

Agent Evidence 的统一数据结构（V0.2 白盒交易决策 Agent · 模块 1/3）。

项目约定（business_contract.md §2 铁律，V0.2 已按官方 BPM 修正）：
  决策时点 = Day-Ahead bid cutoff 前（D-1 日 10:00 PT，官方 BPM "DAM closes at 1000 hours"）。
  D+1 实际 DA / RTPD / Return 尚未产生，不能作输入。
  Evidence 只描述"决策时点之前真实可获得的外部事件信息"。

As-of Decision-Time Evidence 硬约束（本次修正核心）：
  - 任何 Evidence 必须满足 available_at <= decision_cutoff 才能参与交易建议（Pre-decision）。
    available_at 是**唯一** eligibility timestamp，只能由 Source Adapter / Collector
    **显式写出**（若源能证明 published_at 即真正可用时刻，Adapter 显式写
    available_at = published_at 并标注 SOURCE_PUBLISHED_AT_IS_PROVEN_AVAILABILITY）；
    Schema / Time Gate / DecisionService / Web / Tool **禁止**自动推断 / fallback /
    migration。available_at 缺失 → 不可参与决策（AVAILABILITY_NOT_PROVEN）。
  - decision_eligible 由程序计算（见 Evidence.decision_eligible / time_gate.py），
    禁止由 LLM 自行判断可用性。
  - available_at > decision_cutoff（或缺失不可证）的证据 = Post-decision Evidence，
    只能进 Post-trade Review，绝不能进入 Risk Gate / Rule Engine / 交易建议。
  - 历史回测中数据源若无法提供历史发布时间 -> 标记 NOT_BACKTEST_SAFE，不能用于严格 as-of 回测。

结构（在团队统一口径基础上增加时间字段）：
    {"evidence_id":"","event_type":"","region":"","affected_nodes":[],
     "event_start_time":"","event_end_time":"","severity":"",
     "source":"","source_url":"","published_at":"","available_at":"","retrieved_at":"",
     "decision_cutoff":"","decision_eligible":false,
     "summary":"","directional_effect":"SUPPORT_POSITIVE|SUPPORT_NEGATIVE|UNCERTAIN",
     "confidence":0.0}

P0-1（GFS 单一事实来源）时间语义：
  - available_at : 证据真正可用的时刻（= 数据层 AsOfRecord.available_at，
                   Time Gate 唯一判据；空串 = 无可靠 vintage → 不判定可用）。
  - time_eligible: 只判 available_at <= decision_cutoff；available_at 缺失 → FALSE
                   （MISSING_AVAILABLE_AT），绝不 fallback published_at /
                   initialization_time。

关键约束：
  - directional_effect 只能由已核实的真实数据源给出；LLM 猜测一律回退 UNCERTAIN。
  - confidence 是证据可信度/时效性评分（0~1），不是价格概率、不是交易置信度。
  - 本模块不做方向判断；方向由 Rule Engine / 交易员决定。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# 保证从任意 cwd 导入 code.*（Provenance 常量单一事实来源）
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.market_rules import (  # noqa: E402
    CURRENT_MARKET_RULE_VERSION,
    MARKET_RULE_VERSIONS,
    normalize_market_rule_version,
)
from code.data_acquisition.schemas import (  # noqa: E402
    SOURCE_TYPES,
    infer_source_type,
)

# ---------------------------------------------------------------------------
# 枚举常量（单一事实来源）
# ---------------------------------------------------------------------------

#: directional_effect 允许值（严格三态）
DIRECTIONAL_EFFECTS: tuple = ("SUPPORT_POSITIVE", "SUPPORT_NEGATIVE", "UNCERTAIN")

#: severity 允许值（自低到高）
SEVERITY_LEVELS: tuple = ("INFO", "WATCH", "WARNING", "SEVERE", "CRITICAL")

#: Evidence dict 的标准字段顺序（含时间字段 + Provenance 字段，团队口径）
EVIDENCE_KEYS: tuple = (
    "evidence_id",
    "event_type",
    "region",
    "affected_nodes",
    "event_start_time",
    "event_end_time",
    "severity",
    "source",
    "source_url",
    "source_type",
    "is_mock",
    "raw_source_id",
    "published_at",
    "available_at",
    "available_at_source",
    "initialization_time",
    "retrieved_at",
    "target_time",
    "decision_cutoff",
    "time_eligible",
    "backtest_eligible",
    "production_eligible",
    "decision_eligible",
    "summary",
    "directional_effect",
    "confidence",
    "feature_value",
    "market_rule_version",
)

#: 未来待接入的真实数据源类型（当前版本无真实数据源，全部留 TODO）
KNOWN_EVENT_TYPES: tuple = (
    "WEATHER_FORECAST",         # 真实历史 D+1 天气预报（GFS 等，as-of，含温度/风/辐照）
    "EXTREME_WEATHER",          # 极端天气（高温热浪/暴风雨/热浪预警等）
    "CAISO_MARKET_NOTICE",      # CAISO 市场通知 / 系统公告
    "OUTAGE_AND_CONSTRAINT",    # 机组停运 / 输电阻塞
    "WILDFIRE",                 # 山火及其对输电/负荷的影响
    "RENEWABLE_GENERATION",     # 可再生能源出力（尤其夜间/清晨实际+预测，负电价预警）
    "LOAD_FORECAST_REVISION",   # 负荷预测实时修正
    "FUEL_PRICE",               # 本地燃气价（长期补充，非本轮关键）
    "OTHER",                    # 其它
)


def _coerce_str(value: Any, default: str = "") -> str:
    """空值/None 统一规约为空字符串，避免脏数据进入结构。"""
    if value is None:
        return default
    return str(value).strip()


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
        return f if f == f else default  # 去除 NaN
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    s = _coerce_str(value).lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return default


def parse_timestamp(value: Any) -> Optional["pd.Timestamp"]:
    """把 ISO/字符串时间解析为 pandas Timestamp；无法解析返回 None。

    约定：生产数据用 ISO 8601（可含 UTC 偏移）。naive 与 aware 混合时统一为
    naive 比较（由调用方保证口径一致，否则判不可用，宁保守不穿越）。
    """
    s = _coerce_str(value)
    if not s:
        return None
    try:
        ts = pd.Timestamp(s)
        return ts.tz_localize(None) if ts.tz is not None else ts
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Evidence dataclass
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """一条外部证据（不可用于传输方向判断的事实性记录）。

    时间字段含义：
      event_start_time / event_end_time : 事件本身发生的时段
      published_at                      : 证据公开时间（源方发布时刻估计）
      available_at                      : 证据真正可用的时刻（Time Gate 判据；
                                         空串 = 无可靠 vintage → 不可用）
      retrieved_at                      : Agent 检索时间（审计用）
      target_time                       : 该证据指向的交付时刻（UTC naive，可为空）
      decision_cutoff                   : 该证据对应的决策截止（D-1 日 10:00 PT）
      decision_eligible                 : 程序计算 = (available_at <= decision_cutoff)

    Provenance 字段（进入 Risk Gate / Rule Engine 前的可追溯性）：
      source / source_type / is_mock / raw_source_id / target_time /
      available_at(=published_at) / retrieved_at /
      time_eligible / backtest_eligible / production_eligible / feature_value
    """

    evidence_id: str = ""
    event_type: str = "OTHER"
    region: str = ""
    affected_nodes: List[str] = field(default_factory=list)
    event_start_time: str = ""
    event_end_time: str = ""
    severity: str = "INFO"
    source: str = ""
    source_url: str = ""
    source_type: str = ""
    is_mock: bool = False
    raw_source_id: str = ""
    published_at: str = ""
    available_at: str = ""
    available_at_source: str = ""      # available_at 来源：available_at / SOURCE_PUBLISHED_AT_IS_PROVEN_AVAILABILITY（Adapter 显式）/ NOT_PROVEN
    initialization_time: str = ""      # forecast run 初始化时刻（Strong Impossibility：init>cutoff → 必然不可用）
    retrieved_at: str = ""
    target_time: str = ""
    decision_cutoff: str = ""
    summary: str = ""
    directional_effect: str = "UNCERTAIN"
    confidence: float = 0.0
    feature_value: Optional[float] = None
    market_rule_version: str = CURRENT_MARKET_RULE_VERSION

    # -- 程序计算的时间门槛（As-of Decision-Time 硬约束）-------------------
    @property
    def time_eligible(self) -> bool:
        """R1/R2 纯时间门槛（程序计算，绝不由 LLM 判断）。

        V0.3.1.4 统一判断顺序（**published_at 不参与 eligibility**）：
          1. is_mock → False（R7 硬隔离，decision_eligible 处处理）
          2. **initialization_time > decision_cutoff → False（INITIALIZATION_AFTER_CUTOFF）**
             —— Strong Impossibility Check：该 forecast run 连初始化都发生在 cutoff 之后，
             其 available_at 必然更晚，因此不可能在 cutoff 前可用。
             （这不是把 initialization_time 当 available_at，而是用"必然更晚"的逻辑提前拒绝。）
          3. available_at 缺失 → False（AVAILABILITY_NOT_PROVEN / 无已证可用时刻）
          4. available_at > decision_cutoff → False（AVAILABLE_AFTER_CUTOFF）
          5. 否则 → True
        """
        cutoff = parse_timestamp(self.decision_cutoff)
        if cutoff is None:
            return False
        # Strong Impossibility Check：初始化本身已晚于 cutoff → available_at 必然更晚
        init = parse_timestamp(self.initialization_time)
        if init is not None and init > cutoff:
            return False            # INITIALIZATION_AFTER_CUTOFF
        avail = parse_timestamp(self.available_at)
        if avail is None:
            return False            # AVAILABILITY_NOT_PROVEN：无已证可用时刻
        try:
            return avail <= cutoff
        except Exception:
            return False

    @property
    def decision_eligible(self) -> bool:
        """R7 硬隔离：时间合格 且 非 MOCK 才可参与决策。MOCK 证据恒为 FALSE。

        available_at > decision_cutoff（或缺失不可证）→ Post-decision，只能进
        Post-trade Review。程序计算，禁止 LLM 判断。
        """
        return bool(self.time_eligible and not self.is_mock)

    @property
    def backtest_eligible(self) -> bool:
        """该证据可否进入严格 as-of 回测：时间合格 且 非 MOCK 且 有可核实的发布时间。"""
        if self.is_mock:
            return False
        if not self.time_eligible:
            return False
        return parse_timestamp(self.published_at) is not None

    @property
    def production_eligible(self) -> bool:
        """该证据可否进入生产决策：时间合格 且 非 MOCK 且 published/retrieved 齐备。"""
        if self.is_mock:
            return False
        if not self.time_eligible:
            return False
        if parse_timestamp(self.published_at) is None or parse_timestamp(self.retrieved_at) is None:
            return False
        return True

    # -- 规范化 ------------------------------------------------------------
    def normalize(self) -> "Evidence":
        """把字段规约到标准结构：非法 directional_effect 一律回退 UNCERTAIN。"""
        self.evidence_id = _coerce_str(self.evidence_id)
        self.event_type = _coerce_str(self.event_type, default="OTHER") or "OTHER"
        self.region = _coerce_str(self.region)
        self.affected_nodes = [
            _coerce_str(n) for n in self.affected_nodes if _coerce_str(n)
        ]
        self.event_start_time = _coerce_str(self.event_start_time)
        self.event_end_time = _coerce_str(self.event_end_time)
        self.severity = self.severity if self.severity in SEVERITY_LEVELS else "INFO"
        self.source = _coerce_str(self.source)
        self.source_url = _coerce_str(self.source_url)
        # source_type：显式值优先；未给则按 source/event_type 启发式推断
        if self.source_type not in SOURCE_TYPES:
            self.source_type = infer_source_type(self.source, self.event_type)
        self.is_mock = _coerce_bool(self.is_mock)
        self.raw_source_id = _coerce_str(self.raw_source_id)
        self.published_at = _coerce_str(self.published_at)
        self.available_at = _coerce_str(self.available_at)
        self.available_at_source = _coerce_str(self.available_at_source)
        self.initialization_time = _coerce_str(self.initialization_time)
        self.retrieved_at = _coerce_str(self.retrieved_at)
        # V0.3.1.5 Final Invariant：Schema **只做 Schema**，不做业务推断。
        # 禁止 Schema 自动 published_at → available_at（fallback / migration /
        # substitution）。published_at 只是发布元数据；available_at 是唯一 eligibility
        # timestamp，只能由 **Source Adapter 显式写出**（或在构造时已显式提供）。
        # available_at 缺失 → 保持 ""（AVAILABILITY_NOT_PROVEN / NOT ELIGIBLE），
        # 绝不隐式回退 / 猜 initialization_time / 猜 init+delay。
        if self.available_at and not self.available_at_source:
            self.available_at_source = "available_at"
        self.target_time = _coerce_str(self.target_time)
        self.decision_cutoff = _coerce_str(self.decision_cutoff)
        self.summary = _coerce_str(self.summary)
        # 严格三态：LLM/上游给任何非法值都回退 UNCERTAIN（宁可未知，不可乱判方向）
        if self.directional_effect not in DIRECTIONAL_EFFECTS:
            self.directional_effect = "UNCERTAIN"
        self.confidence = min(1.0, max(0.0, _coerce_float(self.confidence)))
        if self.feature_value is not None:
            try:
                fv = float(self.feature_value)
                self.feature_value = fv if fv == fv else None  # NaN → None
            except (TypeError, ValueError):
                self.feature_value = None
        self.market_rule_version = normalize_market_rule_version(self.market_rule_version)
        return self

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        self.normalize()
        return {
            "evidence_id": self.evidence_id,
            "event_type": self.event_type,
            "region": self.region,
            "affected_nodes": list(self.affected_nodes),
            "event_start_time": self.event_start_time,
            "event_end_time": self.event_end_time,
            "severity": self.severity,
            "source": self.source,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "is_mock": bool(self.is_mock),
            "raw_source_id": self.raw_source_id,
            "published_at": self.published_at,
            "available_at": self.available_at,
            "available_at_source": self.available_at_source,
            "initialization_time": self.initialization_time,
            "retrieved_at": self.retrieved_at,
            "target_time": self.target_time,
            "decision_cutoff": self.decision_cutoff,
            "time_eligible": bool(self.time_eligible),
            "backtest_eligible": bool(self.backtest_eligible),
            "production_eligible": bool(self.production_eligible),
            "decision_eligible": bool(self.decision_eligible),
            "summary": self.summary,
            "directional_effect": self.directional_effect,
            "confidence": self.confidence,
            "feature_value": self.feature_value,
            "market_rule_version": self.market_rule_version,
        }

    def to_jsonable(self) -> Dict[str, Any]:
        """与 to_dict 相同，供 json.dump 使用。"""
        return self.to_dict()


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def new_uncertain_evidence(
    event_type: str = "OTHER",
    region: str = "",
    affected_nodes: Optional[List[str]] = None,
    severity: str = "INFO",
    source: str = "",
    source_url: str = "",
    published_at: str = "",
    available_at: str = "",
    retrieved_at: str = "",
    decision_cutoff: str = "",
    summary: str = "暂无真实数据源：该证据当前无法核实，方向未知（UNCERTAIN）。",
    confidence: float = 0.0,
    is_mock: bool = False,
    raw_source_id: str = "",
    target_time: str = "",
) -> Evidence:
    """构造一条方向未知的证据（当前版本无真实数据源，一律走这里）。"""
    return Evidence(
        event_type=event_type,
        region=region,
        affected_nodes=affected_nodes or [],
        severity=severity,
        source=source,
        source_url=source_url,
        is_mock=_coerce_bool(is_mock),
        raw_source_id=raw_source_id,
        published_at=published_at,
        available_at=available_at,
        retrieved_at=retrieved_at,
        target_time=target_time,
        decision_cutoff=decision_cutoff,
        summary=summary,
        directional_effect="UNCERTAIN",
        confidence=confidence,
    ).normalize()


def evidence_from_dict(raw: Dict[str, Any]) -> Evidence:
    """从任意 dict 安全构造 Evidence（缺字段补默认，非法字段回退）。"""
    ev = Evidence(
        evidence_id=raw.get("evidence_id", ""),
        event_type=raw.get("event_type", "OTHER"),
        region=raw.get("region", ""),
        affected_nodes=raw.get("affected_nodes", []),
        event_start_time=raw.get("event_start_time", ""),
        event_end_time=raw.get("event_end_time", ""),
        severity=raw.get("severity", "INFO"),
        source=raw.get("source", ""),
        source_url=raw.get("source_url", ""),
        source_type=raw.get("source_type", ""),
        is_mock=_coerce_bool(raw.get("is_mock", False)),
        raw_source_id=raw.get("raw_source_id", ""),
        published_at=raw.get("published_at", ""),
        available_at=raw.get("available_at", ""),
        available_at_source=raw.get("available_at_source", ""),
        initialization_time=raw.get("initialization_time", ""),
        retrieved_at=raw.get("retrieved_at", ""),
        target_time=raw.get("target_time", ""),
        decision_cutoff=raw.get("decision_cutoff", ""),
        summary=raw.get("summary", ""),
        directional_effect=raw.get("directional_effect", "UNCERTAIN"),
        confidence=raw.get("confidence", 0.0),
        feature_value=raw.get("feature_value"),
        market_rule_version=raw.get("market_rule_version", CURRENT_MARKET_RULE_VERSION),
    )
    return ev.normalize()


def validate_evidence(ev: Dict[str, Any]) -> List[str]:
    """校验一个 Evidence dict，返回所有违反口径的错误信息（空列表=通过）。

    只校验"结构/取值合法性"，不校验"事实真假"——真假由数据源负责。
    """
    errors: List[str] = []
    missing = [k for k in EVIDENCE_KEYS if k not in ev]
    if missing:
        errors.append(f"缺少字段: {missing}")
    if ev.get("directional_effect") not in DIRECTIONAL_EFFECTS:
        errors.append(
            f"directional_effect 非法: {ev.get('directional_effect')!r} "
            f"(允许: {DIRECTIONAL_EFFECTS})"
        )
    if ev.get("severity") not in SEVERITY_LEVELS:
        errors.append(
            f"severity 非法: {ev.get('severity')!r} (允许: {SEVERITY_LEVELS})"
        )
    if ev.get("event_type") not in KNOWN_EVENT_TYPES:
        errors.append(
            f"event_type 未知: {ev.get('event_type')!r} "
            f"(允许: {KNOWN_EVENT_TYPES})"
        )
    try:
        c = float(ev.get("confidence", 0.0))
        if not (0.0 <= c <= 1.0):
            errors.append(f"confidence 越界: {c}")
    except (TypeError, ValueError):
        errors.append(f"confidence 非数值: {ev.get('confidence')!r}")
    st = _coerce_str(ev.get("source_type"))
    if st and st not in SOURCE_TYPES:
        errors.append(f"source_type 非法: {st!r}（允许: {SOURCE_TYPES}）")
    if not isinstance(ev.get("is_mock"), bool):
        errors.append(f"is_mock 非布尔: {ev.get('is_mock')!r}")
    mrv = _coerce_str(ev.get("market_rule_version"))
    if mrv and mrv not in MARKET_RULE_VERSIONS:
        errors.append(f"market_rule_version 非法: {mrv!r}（允许: {MARKET_RULE_VERSIONS}）")

    # eligibility 三拆复算 + 硬规则（R7）：MOCK 证据永不参与决策
    is_mock = _coerce_bool(ev.get("is_mock"))
    if is_mock and (ev.get("decision_eligible") or ev.get("backtest_eligible")
                    or ev.get("production_eligible")):
        errors.append(
            "硬规则 R7: is_mock=True 禁止声明 decision_eligible / backtest_eligible "
            "/ production_eligible（MOCK 仅用于测试/演示，即便 published_at<=cutoff 也不行）")
    try:
        obj = evidence_from_dict(ev)
        for key in ("time_eligible", "backtest_eligible", "production_eligible",
                    "decision_eligible"):
            if bool(ev.get(key)) != bool(getattr(obj, key)):
                errors.append(
                    f"{key} 漂移: stored={bool(ev.get(key))} "
                    f"recomputed={bool(getattr(obj, key))}")
    except Exception:
        pass
    return errors


def ensure_evidence_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把任意输入规范化成标准 Evidence dict；非法方向一律回退 UNCERTAIN。"""
    return evidence_from_dict(raw).to_dict()


# -- 供 REPL/import 测试 ----------------------------------------------------
if __name__ == "__main__":
    d = new_uncertain_evidence(
        event_type="RENEWABLE_GENERATION",
        region="ZP26",
        affected_nodes=["CONTROLX_1_N001"],
        severity="WATCH",
        source="(no-op placeholder)",
        published_at="2025-07-08T09:00:00",
        decision_cutoff="2025-07-09T10:00:00",
        summary="占位证据：未来接入 CAISO 可再生能源出力数据后填充。",
    ).to_dict()
    print(d)
    print("validate errors:", validate_evidence(d))
    print("decision_eligible:", d["decision_eligible"])
