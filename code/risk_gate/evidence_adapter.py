# -*- coding: utf-8 -*-
"""
code/risk_gate/evidence_adapter.py

Evidence Time Gate 适配层：把 agent/evidence 的 As-of Decision-Time 硬约束
接入 Risk Gate / Rule Engine。

职责：
  1. filter_eligible_evidence —— 只放行 decision_eligible=True 的 Pre-decision Evidence；
  2. assert_no_post_decision  —— 防御性断言：Post-decision Evidence 若误入决策层直接抛错；
  3. evidence_direction_context —— 把 eligible Evidence 汇总成 Risk Gate 可消费的方向上下文
     （当前无真实数据源，全部 UNCERTAIN，不会触发方向冲突规则）。

依赖：agent/evidence/time_gate.py（程序计算 decision_eligible，禁止 LLM 判断）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.evidence.schema import Evidence, evidence_from_dict
from agent.evidence.time_gate import (
    assert_no_post_decision as _assert_no_post_decision,
    is_decision_eligible,
    split_eligible,
)

#: 证据方向三态
DIRECTIONAL_EFFECTS: tuple = ("SUPPORT_POSITIVE", "SUPPORT_NEGATIVE", "UNCERTAIN")


def filter_eligible_evidence(
    evidences: Sequence[Dict[str, Any]],
    decision_cutoff: Optional[str] = None,
) -> Tuple[List[Evidence], List[Evidence]]:
    """按 As-of Decision-Time 约束切分证据。

    Returns:
        (eligible, post_decision)
          eligible      : decision_eligible=True（Pre-decision，可进 Risk Gate / Rule Engine）
          post_decision : decision_eligible=False（Post-decision，只进 Post-trade Review）
    """
    evs = [_coerce(ev) for ev in evidences]
    return split_eligible(evs, decision_cutoff)


def assert_no_post_decision(
    evidences: Sequence[Dict[str, Any]],
    decision_cutoff: Optional[str] = None,
) -> None:
    """防御：若把 Post-decision 证据误传进决策层，直接抛 RuntimeError。"""
    evs = [_coerce(ev) for ev in evidences]
    _assert_no_post_decision(evs, decision_cutoff)


def evidence_direction_context(
    evidences: Sequence[Dict[str, Any]],
    decision_cutoff: Optional[str] = None,
) -> Dict[str, Any]:
    """把（已过滤的）Pre-decision Evidence 汇总成 Risk Gate 的方向上下文。

    当前版本无真实数据源，全部 directional_effect=UNCERTAIN；
    汇总结构保留，供未来接入真实源后由 Rule Engine / Gate 消费。

    Returns:
        {"SUPPORT_POSITIVE": n, "SUPPORT_NEGATIVE": n, "UNCERTAIN": n,
         "any_directional": bool, "max_severity": str, "n": total}
    """
    evs = [_coerce(ev) for ev in evidences]
    summary: Dict[str, int] = {"SUPPORT_POSITIVE": 0, "SUPPORT_NEGATIVE": 0, "UNCERTAIN": 0}
    max_sev = "INFO"
    sev_order = ["INFO", "WATCH", "WARNING", "SEVERE", "CRITICAL"]
    for ev in evs:
        eff = ev.directional_effect
        if eff in summary:
            summary[eff] += 1
        sev = ev.severity or "INFO"
        if sev_order.index(sev) > sev_order.index(max_sev):
            max_sev = sev
    return {
        **summary,
        "any_directional": (summary["SUPPORT_POSITIVE"] + summary["SUPPORT_NEGATIVE"]) > 0,
        "max_severity": max_sev,
        "n": len(evs),
    }


def _coerce(ev) -> Evidence:
    if isinstance(ev, Evidence):
        return ev
    if isinstance(ev, dict):
        return evidence_from_dict(ev)
    raise TypeError(f"无法识别的 Evidence: {type(ev)!r}")
