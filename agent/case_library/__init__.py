# -*- coding: utf-8 -*-
"""
agent/case_library —— V0.2 白盒交易决策 Agent · 模块 2：Case Library。

Case Library ≠ Rule Engine：
  - 只做历史检索（"历史有无类似情况"），不输出决策规则。
  - 本包提供 Case 数据结构 + CaseGenerationPolicy（自动生成规则 + as-of 硬约束）
    + 从 test 预测自动生成的 cases_auto.json + 人工整理的 cases.json。
"""

from agent.case_library.case import Case
from agent.case_library.policy import (
    CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION,
    CASE_TYPE_HUMAN_OVERRIDE,
    CASE_TYPE_LARGE_PROFIT,
    CASE_TYPE_RISK_GATE_FAILURE,
    CASE_TYPE_TAIL_LOSS,
    CaseGenerationPolicy,
    DecisionRecord,
    generate_cases,
    is_retrievable,
    settlement_available_at,
)

__all__ = [
    "Case",
    "CaseGenerationPolicy",
    "DecisionRecord",
    "generate_cases",
    "is_retrievable",
    "settlement_available_at",
    "CASE_TYPE_TAIL_LOSS",
    "CASE_TYPE_LARGE_PROFIT",
    "CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION",
    "CASE_TYPE_RISK_GATE_FAILURE",
    "CASE_TYPE_HUMAN_OVERRIDE",
]
