# -*- coding: utf-8 -*-
"""
agent/evidence —— V0.2 白盒交易决策 Agent · 模块 1：Agent Evidence。

职责：外部证据的统一结构与获取管道。
当前版本无真实数据源，所有证据 directional_effect=UNCERTAIN；
LLM 只做抽取/分类/摘要，禁止凭空判断价格方向。
"""

from agent.evidence.schema import (
    DIRECTIONAL_EFFECTS,
    EVIDENCE_KEYS,
    SEVERITY_LEVELS,
    KNOWN_EVENT_TYPES,
    Evidence,
    evidence_from_dict,
    ensure_evidence_dict,
    new_uncertain_evidence,
    validate_evidence,
)
from agent.evidence.fetcher import (
    FETCHER_REGISTRY,
    fetch_evidence,
    compile_evidence_context,
    attach_uncertain_evidence,
    summarize_evidence_list,
    assert_no_direction_guess,
)

__all__ = [
    "DIRECTIONAL_EFFECTS",
    "EVIDENCE_KEYS",
    "SEVERITY_LEVELS",
    "KNOWN_EVENT_TYPES",
    "Evidence",
    "evidence_from_dict",
    "ensure_evidence_dict",
    "new_uncertain_evidence",
    "validate_evidence",
    "FETCHER_REGISTRY",
    "fetch_evidence",
    "compile_evidence_context",
    "attach_uncertain_evidence",
    "summarize_evidence_list",
    "assert_no_direction_guess",
]
