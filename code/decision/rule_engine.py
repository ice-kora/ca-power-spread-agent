# -*- coding: utf-8 -*-
"""
code/decision/rule_engine.py

White-box Rule Engine（V0.2）：把 Predictive Model + Risk Gate + Evidence
组合成最终三态建议 BUY_DA / SELL_DA / NO_TRADE。

设计原则（任务要求）：
  - 规则全部可读、可配置（阈值来自 config，不硬编码）、可测试；
  - **不要把决策埋在模型内部**——模型只输出预测量，交易动作由本引擎判定；
  - 输出含 reason（命中哪些规则）与规则版本号。

规则流水线（按优先级，命中即返回）：
    R-A  RiskGate == REJECT              → NO_TRADE（risk_reasons 记录在案）
    R-B  关键模型输出缺失/NaN            → NO_TRADE（DATA_MISSING）
    R-C  |expected_return| < min_spread  → NO_TRADE（EXPECTED_RETURN_TOO_SMALL）
    R-D  confidence < min_confidence     → NO_TRADE（LOW_CONFIDENCE）
    R-E  eligible Evidence 方向冲突       → NO_TRADE（EVIDENCE_CONFLICT）
    R-F  RiskGate == WARNING 且配置拒绝警告 → NO_TRADE（GATE_WARNING_ESCALATED）
    R-G  expected_return > 0             → SELL_DA
    R-H  expected_return < 0             → BUY_DA
    R-I  其余（=0 / 无方向）             → NO_TRADE（NO_CLEAR_DIRECTION）

阈值来源：
  min_spread     = DECISION_CFG.ret_threshold_abs = 5.0（code/backtest.py；V0.1 三态策略）
  min_confidence = DECISION_CFG.conf_threshold    = 0.20
  reject_on_warning = False（默认：WARNING 只标记不拦；交易员可手动加严）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from code.risk_gate.config import DEFAULT_RISK_GATE_CONFIG, RiskGateConfig
from code.risk_gate.constants import (
    DIRECTION_BUY,
    DIRECTION_SELL,
    direction_from_expected_return,
)
from code.risk_gate.evidence_adapter import (
    assert_no_post_decision,
    evidence_direction_context,
    filter_eligible_evidence,
)
from code.risk_gate.gate import GateVerdict, RiskGate
from agent.evidence.time_gate import (
    assert_feature_eligibility_consistent,
    validate_feature_eligibility,
)
from code.market_rules import (  # noqa: E402
    CURRENT_MARKET_RULE_VERSION,
    MARKET_RULE_VERSIONS,
    normalize_market_rule_version,
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuleEngineConfig:
    """Rule Engine 阈值配置（可覆盖；阈值默认对齐 DECISION_CFG / RiskGateConfig）。"""

    version: str = "0.2"
    min_spread: float = 5.0            # |expected_return| 下限（$/MWh）
    min_confidence: float = 0.20       # 置信度下限（未校准，仅作保守过滤）
    reject_on_warning: bool = False    # RiskGate WARNING 是否升级为 NO_TRADE（默认不拦）
    require_direction_probability: bool = False  # 是否要求 prob_positive/prob_negative 齐备
    risk_gate_config: RiskGateConfig = field(default_factory=lambda: DEFAULT_RISK_GATE_CONFIG)
    market_rule_version: str = CURRENT_MARKET_RULE_VERSION  # DAME/EDAM 标记（本轮仅保存）
    changelog: tuple = (
        "0.2: 独立 Rule Engine；消费 RiskGate verdict + eligible Evidence；规则可读可配置。",
        "0.1: backtest.py 内置三态策略（DECISION_CFG）——本次升级为独立白盒模块。",
    )


DEFAULT_RULE_ENGINE_CONFIG: RuleEngineConfig = RuleEngineConfig()


# ---------------------------------------------------------------------------
# 决策结果
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    decision: str                                  # BUY_DA / SELL_DA / NO_TRADE
    direction_sign: int                            # +1 SELL / −1 BUY / 0 NO_TRADE
    expected_return: Optional[float] = None
    confidence: Optional[float] = None
    reasons: List[str] = field(default_factory=list)        # reason_code 列表
    rules_hit: List[str] = field(default_factory=list)      # 命中的规则 id（如 R-A）
    risk_verdict: Optional[str] = None             # Risk Gate 判定 PASS/WARNING/REJECT
    risk_reasons: List[str] = field(default_factory=list)   # Gate 的 reason_code
    evidence_used: Dict[str, Any] = field(default_factory=dict)  # eligible 证据汇总
    features_used: List[Dict[str, Any]] = field(default_factory=list)  # 决策展示的特征（P0-2）
    feature_eligibility_consistent: bool = True    # P0-2：展示 available_at 与 Time Gate 一致
    version: str = "0.2"
    market_rule_version: str = CURRENT_MARKET_RULE_VERSION  # 决策上下文规则版本标记

    @property
    def is_trade(self) -> bool:
        return self.decision in ("BUY_DA", "SELL_DA")

    def feature_eligibility_violations(self,
                                       decision_cutoff: Optional[str] = None) -> List[str]:
        """P0-2 硬规则复算：displayed available_at > cutoff ⇒ eligible MUST NOT = TRUE。"""
        cutoff = decision_cutoff or self.decision_cutoff
        return validate_feature_eligibility(self.features_used, cutoff)

    @property
    def decision_cutoff(self) -> Optional[str]:
        """从 features_used 推断 decision_cutoff（有则取第一个非空）。"""
        for f in self.features_used:
            c = f.get("decision_cutoff")
            if c:
                return c
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "direction_sign": self.direction_sign,
            "expected_return": self.expected_return,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "rules_hit": list(self.rules_hit),
            "risk_verdict": self.risk_verdict,
            "risk_reasons": list(self.risk_reasons),
            "evidence_used": dict(self.evidence_used),
            "features_used": list(self.features_used),
            "feature_eligibility_consistent": bool(self.feature_eligibility_consistent),
            "version": self.version,
            "market_rule_version": normalize_market_rule_version(self.market_rule_version),
        }


# ---------------------------------------------------------------------------
# Rule Engine
# ---------------------------------------------------------------------------
class RuleEngine:
    """白盒三态决策引擎。"""

    def __init__(
        self,
        cfg: Optional[RuleEngineConfig] = None,
        risk_gate: Optional[RiskGate] = None,
    ):
        self.cfg = cfg or DEFAULT_RULE_ENGINE_CONFIG
        self.risk_gate = risk_gate or RiskGate(self.cfg.risk_gate_config)

    # ------------------------------------------------------------------
    def evaluate(
        self,
        prediction: Dict[str, Any],
        gate_verdict: Optional[GateVerdict] = None,
        evidences: Sequence[Any] = (),
        decision_cutoff: Optional[str] = None,
        features_used: Sequence[Dict[str, Any]] = (),
    ) -> Decision:
        """组合 Predictive Model + Risk Gate + Evidence → 三态建议。

        Args:
            prediction: 模型输出 dict（node, target_date, hour, expected_return,
                        confidence, uncertainty, prob_positive, prob_negative ...）。
            gate_verdict: Risk Gate 判定；None 时由本引擎内部调用 RiskGate。
            evidences: 原始证据列表（本引擎内部先过 Evidence Time Gate 过滤）。
            decision_cutoff: 决策截止（D-1 10:00 PT）；缺省用证据自带。
            features_used: 决策展示的特征行（P0-2）；每条须携带 availability_basis /
                           latest_possible_available_at / decision_eligible / decision_cutoff，
                           本引擎强制"展示口径 == Time Gate 判定口径"，不一致直接抛错。

        Returns:
            Decision。
        """
        pred = dict(prediction)
        direction = pred.get("direction") or direction_from_expected_return(pred.get("expected_return"))
        pred["direction"] = direction

        # ---- Feature Time Gate（P0-2）：展示 available_at == 判定 available_at ----
        features_used_list = list(features_used or [])
        feat_violations = validate_feature_eligibility(features_used_list, decision_cutoff)
        if feat_violations:
            assert_feature_eligibility_consistent(features_used_list, decision_cutoff)  # 抛错
        feat_consistent = not feat_violations

        # ---- Evidence Time Gate：只放行 Pre-decision Evidence ----
        eligible, post = filter_eligible_evidence(list(evidences), decision_cutoff)
        ev_ctx = evidence_direction_context(eligible, decision_cutoff)
        # 防御：post-decision 证据若混入决策层则抛错
        if post:
            assert_no_post_decision(list(evidences), decision_cutoff)

        # ---- Risk Gate ----
        if gate_verdict is None:
            gate_verdict = self.risk_gate.evaluate(pred)
        # 兼容 dict 形式的 verdict（如从 JSON/流水线传入）
        if isinstance(gate_verdict, dict):
            gate_verdict = GateVerdict(
                decision=str(gate_verdict.get("decision", "PASS")),
                risk_reasons=list(gate_verdict.get("risk_reasons", [])),
                rules_hit=list(gate_verdict.get("rules_hit", [])),
                details=dict(gate_verdict.get("details", {})),
                version=str(gate_verdict.get("version", "0.2")),
            )
        risk_reasons = list(getattr(gate_verdict, "risk_reasons", []))
        risk_decision = getattr(gate_verdict, "decision", "PASS")

        # ---- 规则流水线 ----
        reasons: List[str] = []
        rules_hit: List[str] = []

        def _no_trade(code: str, rule: str, note: str = "") -> Decision:
            return Decision(
                decision="NO_TRADE",
                direction_sign=0,
                expected_return=_num(pred.get("expected_return")),
                confidence=_num(pred.get("confidence")),
                reasons=reasons + [code],
                rules_hit=rules_hit + [rule],
                risk_verdict=risk_decision,
                risk_reasons=risk_reasons,
                evidence_used=ev_ctx,
                features_used=features_used_list,
                feature_eligibility_consistent=feat_consistent,
                version=self.cfg.version,
                market_rule_version=self.cfg.market_rule_version,
            )

        er = _num(pred.get("expected_return"))
        conf = _num(pred.get("confidence"))

        # R-A：Risk Gate REJECT → NO_TRADE
        if risk_decision == "REJECT":
            return _no_trade("RISK_GATE_REJECTED", "R-A", f"gate={risk_reasons}")

        # R-B：关键输出缺失
        if er is None:
            return _no_trade("DATA_MISSING", "R-B", "expected_return 缺失")
        if conf is None:
            return _no_trade("DATA_MISSING", "R-B", "confidence 缺失")

        # R-C：|expected_return| 过小
        if abs(er) < self.cfg.min_spread:
            return _no_trade("EXPECTED_RETURN_TOO_SMALL", "R-C",
                             f"|er|={abs(er):.2f} < {self.cfg.min_spread:.2f}")

        # R-D：置信度过低
        if conf < self.cfg.min_confidence:
            return _no_trade("LOW_CONFIDENCE", "R-D",
                             f"conf={conf:.3f} < {self.cfg.min_confidence:.2f}")

        # R-E：eligible 证据方向冲突
        if ev_ctx.get("any_directional"):
            n_pos = ev_ctx.get("SUPPORT_POSITIVE", 0)
            n_neg = ev_ctx.get("SUPPORT_NEGATIVE", 0)
            if direction == DIRECTION_SELL and n_neg > 0:
                return _no_trade("EVIDENCE_CONFLICT", "R-E", f"{n_neg} 条证据支持 Return<0")
            if direction == DIRECTION_BUY and n_pos > 0:
                return _no_trade("EVIDENCE_CONFLICT", "R-E", f"{n_pos} 条证据支持 Return>0")

        # R-F：Gate WARNING 且配置拒绝警告
        if risk_decision == "WARNING" and self.cfg.reject_on_warning:
            return _no_trade("RISK_GATE_WARNING_ESCALATED", "R-F",
                             f"gate_warning={risk_reasons}")

        # R-G / R-H：方向判定
        if er > 0:
            return Decision(
                decision="SELL_DA",
                direction_sign=+1,
                expected_return=er,
                confidence=conf,
                reasons=reasons + ["EXPECTED_RETURN_POSITIVE"],
                rules_hit=rules_hit + ["R-G"],
                risk_verdict=risk_decision,
                risk_reasons=risk_reasons,
                evidence_used=ev_ctx,
                features_used=features_used_list,
                feature_eligibility_consistent=feat_consistent,
                version=self.cfg.version,
                market_rule_version=self.cfg.market_rule_version,
            )
        if er < 0:
            return Decision(
                decision="BUY_DA",
                direction_sign=-1,
                expected_return=er,
                confidence=conf,
                reasons=reasons + ["EXPECTED_RETURN_NEGATIVE"],
                rules_hit=rules_hit + ["R-H"],
                risk_verdict=risk_decision,
                risk_reasons=risk_reasons,
                evidence_used=ev_ctx,
                features_used=features_used_list,
                feature_eligibility_consistent=feat_consistent,
                version=self.cfg.version,
                market_rule_version=self.cfg.market_rule_version,
            )

        # R-I：无明确方向
        return _no_trade("NO_CLEAR_DIRECTION", "R-I", "expected_return == 0")


def describe_rules() -> List[Dict[str, str]]:
    """返回规则目录（供文档/审计）。"""
    return [
        {"rule_id": "R-A", "condition": "RiskGate == REJECT", "decision": "NO_TRADE",
         "reason_code": "RISK_GATE_REJECTED"},
        {"rule_id": "R-B", "condition": "expected_return/confidence 缺失", "decision": "NO_TRADE",
         "reason_code": "DATA_MISSING"},
        {"rule_id": "R-C", "condition": "|expected_return| < min_spread", "decision": "NO_TRADE",
         "reason_code": "EXPECTED_RETURN_TOO_SMALL"},
        {"rule_id": "R-D", "condition": "confidence < min_confidence", "decision": "NO_TRADE",
         "reason_code": "LOW_CONFIDENCE"},
        {"rule_id": "R-E", "condition": "eligible Evidence 方向冲突", "decision": "NO_TRADE",
         "reason_code": "EVIDENCE_CONFLICT"},
        {"rule_id": "R-F", "condition": "RiskGate == WARNING 且 reject_on_warning=True", "decision": "NO_TRADE",
         "reason_code": "RISK_GATE_WARNING_ESCALATED"},
        {"rule_id": "R-G", "condition": "expected_return > 0", "decision": "SELL_DA",
         "reason_code": "EXPECTED_RETURN_POSITIVE"},
        {"rule_id": "R-H", "condition": "expected_return < 0", "decision": "BUY_DA",
         "reason_code": "EXPECTED_RETURN_NEGATIVE"},
        {"rule_id": "R-I", "condition": "expected_return == 0 / 无方向", "decision": "NO_TRADE",
         "reason_code": "NO_CLEAR_DIRECTION"},
    ]


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
        return f if f == f else None
    except (TypeError, ValueError):
        return None
