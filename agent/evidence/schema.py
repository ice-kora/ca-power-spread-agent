# -*- coding: utf-8 -*-
"""
agent/evidence/schema.py

Agent Evidence 的统一数据结构（V0.2 白盒交易决策 Agent · 模块 1/3）。

项目约定（business_contract.md §2 铁律，V0.2 已按官方 BPM 修正）：
  决策时点 = Day-Ahead bid cutoff 前（D-1 日 10:00 PT，官方 BPM "DAM closes at 1000 hours"）。
  D+1 实际 DA / RTPD / Return 尚未产生，不能作输入。
  Evidence 只描述"决策时点之前真实可获得的外部事件信息"。

As-of Decision-Time Evidence 硬约束（本次修正核心）：
  - 任何 Evidence 必须满足 published_at <= decision_cutoff 才能参与交易建议（Pre-decision）。
  - decision_eligible 由程序计算（见 Evidence.decision_eligible / time_gate.py），
    禁止由 LLM 自行判断可用性。
  - published_at > decision_cutoff 的证据 = Post-decision Evidence，只能进 Post-trade Review，
    绝不能进入 Risk Gate / Rule Engine / 交易建议。
  - 历史回测中数据源若无法提供历史发布时间 -> 标记 NOT_BACKTEST_SAFE，不能用于严格 as-of 回测。

结构（在团队统一口径基础上增加时间字段）：
    {"evidence_id":"","event_type":"","region":"","affected_nodes":[],
     "event_start_time":"","event_end_time":"","severity":"",
     "source":"","source_url":"","published_at":"","retrieved_at":"",
     "decision_cutoff":"","decision_eligible":false,
     "summary":"","directional_effect":"SUPPORT_POSITIVE|SUPPORT_NEGATIVE|UNCERTAIN",
     "confidence":0.0}

关键约束：
  - directional_effect 只能由已核实的真实数据源给出；LLM 猜测一律回退 UNCERTAIN。
  - confidence 是证据可信度/时效性评分（0~1），不是价格概率、不是交易置信度。
  - 本模块不做方向判断；方向由 Rule Engine / 交易员决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# 枚举常量（单一事实来源）
# ---------------------------------------------------------------------------

#: directional_effect 允许值（严格三态）
DIRECTIONAL_EFFECTS: tuple = ("SUPPORT_POSITIVE", "SUPPORT_NEGATIVE", "UNCERTAIN")

#: severity 允许值（自低到高）
SEVERITY_LEVELS: tuple = ("INFO", "WATCH", "WARNING", "SEVERE", "CRITICAL")

#: Evidence dict 的标准字段顺序（含时间字段，团队口径）
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
    "published_at",
    "retrieved_at",
    "decision_cutoff",
    "decision_eligible",
    "summary",
    "directional_effect",
    "confidence",
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
      published_at                      : 证据公开时间（核心：与 decision_cutoff 比较）
      retrieved_at                      : Agent 检索时间（审计用）
      decision_cutoff                   : 该证据对应的决策截止（D-1 日 10:00 PT）
      decision_eligible                 : 程序计算 = (published_at <= decision_cutoff)
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
    published_at: str = ""
    retrieved_at: str = ""
    decision_cutoff: str = ""
    summary: str = ""
    directional_effect: str = "UNCERTAIN"
    confidence: float = 0.0

    # -- 程序计算的时间门槛（As-of Decision-Time 硬约束）-------------------
    @property
    def decision_eligible(self) -> bool:
        """是否可在该决策时点使用：published_at <= decision_cutoff（程序计算）。

        任一时间缺失 -> False（保守，宁保守不穿越）。绝不由 LLM 判断。
        """
        pub = parse_timestamp(self.published_at)
        cutoff = parse_timestamp(self.decision_cutoff)
        if pub is None or cutoff is None:
            return False
        try:
            return pub <= cutoff
        except Exception:
            return False

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
        self.published_at = _coerce_str(self.published_at)
        self.retrieved_at = _coerce_str(self.retrieved_at)
        self.decision_cutoff = _coerce_str(self.decision_cutoff)
        self.summary = _coerce_str(self.summary)
        # 严格三态：LLM/上游给任何非法值都回退 UNCERTAIN（宁可未知，不可乱判方向）
        if self.directional_effect not in DIRECTIONAL_EFFECTS:
            self.directional_effect = "UNCERTAIN"
        self.confidence = min(1.0, max(0.0, _coerce_float(self.confidence)))
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
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "decision_cutoff": self.decision_cutoff,
            "decision_eligible": bool(self.decision_eligible),
            "summary": self.summary,
            "directional_effect": self.directional_effect,
            "confidence": self.confidence,
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
    retrieved_at: str = "",
    decision_cutoff: str = "",
    summary: str = "暂无真实数据源：该证据当前无法核实，方向未知（UNCERTAIN）。",
    confidence: float = 0.0,
) -> Evidence:
    """构造一条方向未知的证据（当前版本无真实数据源，一律走这里）。"""
    return Evidence(
        event_type=event_type,
        region=region,
        affected_nodes=affected_nodes or [],
        severity=severity,
        source=source,
        source_url=source_url,
        published_at=published_at,
        retrieved_at=retrieved_at,
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
        published_at=raw.get("published_at", ""),
        retrieved_at=raw.get("retrieved_at", ""),
        decision_cutoff=raw.get("decision_cutoff", ""),
        summary=raw.get("summary", ""),
        directional_effect=raw.get("directional_effect", "UNCERTAIN"),
        confidence=raw.get("confidence", 0.0),
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
