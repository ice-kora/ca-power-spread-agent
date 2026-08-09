# -*- coding: utf-8 -*-
"""
code/risk_gate —— Risk Gate 独立模块（V0.2）。

职责（任务定义）：
  **不预测方向，只判定"这笔候选交易是否放行"。**
  输入：Predictive Model Output（expected_return / prob / confidence / uncertainty）
        + node / hour + 可用的 Pre-decision Evidence + 相似历史亏损 Case + as-of 风险特征。
  输出：PASS / WARNING / REJECT + risk_reasons（reason_code 列表）。

保留 V0.1 empirical guardrails（CONTROLX BUY 拒绝、ELCA SELL 拒绝、低样本拒绝），
每条标注 DATA-DERIVED TEMPORARY GUARDRAIL；过拟合检验见 code/risk_gate/calibrate.py。

模块组成：
  constants.py        常量与口径（reason_code / 方向 / PnL 约定）
  config.py           RiskGateConfig（阈值全部 train+val 校准，带版本与修改记录）
  rules.py            白盒规则（每规则一个纯函数，可读可配置可测试）
  gate.py             RiskGate 编排器（PASS/WARNING/REJECT + risk_reasons）
  evidence_adapter.py Evidence Time Gate 适配（只放行 decision_eligible=True）
  case_adapter.py     Case Library 适配（相似历史亏损 Case 检索，as-of）
  calibrate.py        train+val 校准 + 过拟合检验（test 只验证）
"""

from __future__ import annotations

from code.risk_gate.config import (
    DEFAULT_RISK_GATE_CONFIG,
    EmpiricalGuardrail,
    RiskGateConfig,
    build_config,
)
from code.risk_gate.constants import (
    DATA_DERIVED_TEMPORARY_GUARDRAIL,
    DECISION_CUTOFF_DESC,
    DIRECTION_BUY,
    DIRECTION_SELL,
    GATE_PASS,
    GATE_REJECT,
    GATE_WARNING,
    REASON_CODES,
    direction_from_expected_return,
    signed_pnl,
)
from code.risk_gate.gate import (
    GateVerdict,
    RiskGate,
    evaluate_frame,
)
from code.risk_gate.rules import (
    RULES,
    RuleHit,
    describe_rules,
)
from code.risk_gate.evidence_adapter import (
    assert_no_post_decision,
    evidence_direction_context,
    filter_eligible_evidence,
)
from code.risk_gate.case_adapter import (
    candidate_with_cases,
    load_cases,
    match_similar_tail_cases,
)

__all__ = [
    # config
    "RiskGateConfig",
    "EmpiricalGuardrail",
    "DEFAULT_RISK_GATE_CONFIG",
    "build_config",
    # constants
    "REASON_CODES",
    "GATE_PASS",
    "GATE_WARNING",
    "GATE_REJECT",
    "DIRECTION_BUY",
    "DIRECTION_SELL",
    "DATA_DERIVED_TEMPORARY_GUARDRAIL",
    "DECISION_CUTOFF_DESC",
    "direction_from_expected_return",
    "signed_pnl",
    # gate
    "RiskGate",
    "GateVerdict",
    "evaluate_frame",
    # rules
    "RULES",
    "RuleHit",
    "describe_rules",
    # evidence
    "filter_eligible_evidence",
    "assert_no_post_decision",
    "evidence_direction_context",
    # case
    "load_cases",
    "match_similar_tail_cases",
    "candidate_with_cases",
]
