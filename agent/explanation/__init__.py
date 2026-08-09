# -*- coding: utf-8 -*-
"""
agent/explanation —— V0.2 白盒交易决策 Agent · 模块 3：Decision Card。

职责：生成结构化决策卡片（建议动作 / 量化依据 / Agent Evidence /
      Risk Gate 结果 / 主要风险 / 最终建议 / 人工确认）。
"""

from agent.explanation.decision_card import (
    GATE_STATUS_PASS,
    GATE_STATUS_WARNING,
    GATE_STATUS_REJECT,
    RiskGateResult,
    DecisionCard,
    format_card_markdown,
    cards_to_json,
    cards_to_markdown_preview,
)

__all__ = [
    "GATE_STATUS_PASS",
    "GATE_STATUS_WARNING",
    "GATE_STATUS_REJECT",
    "RiskGateResult",
    "DecisionCard",
    "format_card_markdown",
    "cards_to_json",
    "cards_to_markdown_preview",
]
