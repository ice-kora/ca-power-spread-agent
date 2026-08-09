# -*- coding: utf-8 -*-
"""
code/decision —— White-box Rule Engine（V0.2）。

职责：把 Predictive Model + Risk Gate + Evidence → 最终三态建议
      BUY_DA / SELL_DA / NO_TRADE。

规则全部可读、可配置、可测试；决策不埋在模型内部。
输出含 reason（命中哪些规则）与规则版本号。
"""

from __future__ import annotations

from code.decision.rule_engine import (
    DEFAULT_RULE_ENGINE_CONFIG,
    Decision,
    RuleEngine,
    RuleEngineConfig,
    describe_rules,
)

__all__ = [
    "RuleEngine",
    "RuleEngineConfig",
    "DEFAULT_RULE_ENGINE_CONFIG",
    "Decision",
    "describe_rules",
]
