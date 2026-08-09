# -*- coding: utf-8 -*-
"""
code/risk_gate/case_adapter.py

Case Library 适配层：从 agent/case_library/cases.json 检索"相似历史亏损 Case"，
供 Risk Gate 的 R8（SIMILAR_TAIL_LOSS_CASE）使用。

设计要点：
  - **as-of 约束**：只有决策时点**之前已知结果**的 Case 才可用。
    Case.decision_date = 该 Case 的目标日 target_date − 1（bid cutoff 当日）。
    对候选（target_date=T，决策日 = T−1），可用 Case 需满足
        case.decision_date < T − 1  ⇔  case.target_date < T
    （即该 Case 的目标日已完整过去，其结果已实际发生并可被决策日看到）。
  - **相似度**：同 node + 同 direction（Case.model_prediction ∈ {BUY, SELL}）+
    |hour − case.hour| <= hour_window。
  - **亏损 Case**：Case.PnL < tail_threshold（默认 −300 $/MWh）。
  - 返回按 |PnL| 降序（最惨在前）截断到 max 条。

⚠️ 诚实边界：当前 cases.json 全部来自 test 窗口（2026-06-14~07-23），
因此对 test 窗口早期候选几乎不触发；随着窗口推进（06-30 后）会逐步命中。
这符合"历史相似案例只在它真实发生后才能被参考"的 as-of 语义。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd

#: cases.json 默认路径（相对本文件：repo/agent/case_library/cases.json）
_DEFAULT_CASES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agent", "case_library", "cases.json",
)


def load_cases(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """加载 cases.json，返回 Case dict 列表（保留原始字段）。"""
    p = path or _DEFAULT_CASES_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases", []) if isinstance(data, dict) else data
    return list(cases)


def _as_ts(x) -> Optional[pd.Timestamp]:
    try:
        ts = pd.Timestamp(x)
        return ts.tz_localize(None) if ts.tz is not None else ts
    except Exception:
        return None


def match_similar_tail_cases(
    candidate: Dict[str, Any],
    cases: Optional[List[Dict[str, Any]]] = None,
    *,
    tail_threshold: float = -300.0,
    hour_window: int = 3,
    max_cases: int = 5,
    as_of: bool = True,
) -> List[Dict[str, Any]]:
    """检索与候选交易相似的、结果已可知的亏损 Case。

    Args:
        candidate: 候选交易 dict（含 node / target_date / hour / direction）。
        cases: Case dict 列表；None 则从 cases.json 加载。
        tail_threshold: Case.PnL 低于该值视为亏损 Case。
        hour_window: 小时相似窗口。
        max_cases: 返回上限。
        as_of: 是否施加 as-of 过滤（默认 True，严格回测必须 True）。

    Returns:
        按 |PnL| 降序的相似亏损 Case 列表（dict 切片，含 case_id/decision_date/hour/PnL...）。
    """
    if cases is None:
        cases = load_cases()

    node = str(candidate.get("node", ""))
    hour = int(candidate.get("hour", -1) or -1)
    direction = str(candidate.get("direction", "")).upper()
    # 候选决策日 = target_date − 1（可被看到的结果边界）
    cand_decision = None
    if as_of:
        td = _as_ts(candidate.get("target_date"))
        if td is not None:
            cand_decision = td - pd.Timedelta(days=1)

    matched: List[Dict[str, Any]] = []
    for c in cases:
        if str(c.get("node", "")) != node:
            continue
        pred = str(c.get("model_prediction", "")).upper()
        if direction and pred not in (direction,):
            continue  # BUY 与 SELL 不相匹配
        try:
            c_hour = int(c.get("hour", -1))
        except (TypeError, ValueError):
            continue
        if abs(c_hour - hour) > hour_window:
            continue
        pnl = c.get("PnL")
        try:
            pnl_f = float(pnl)
        except (TypeError, ValueError):
            continue
        if pnl_f is None or pnl_f != pnl_f or pnl_f >= tail_threshold:
            continue  # 非亏损 Case 不计入
        if as_of:
            cd = _as_ts(c.get("decision_date"))
            if cd is None or cand_decision is None or cd >= cand_decision:
                continue
        matched.append(c)

    matched.sort(key=lambda c: float(c.get("PnL", 0.0)))
    return matched[:max_cases]


def candidate_with_cases(
    candidate: Dict[str, Any],
    cases: Optional[List[Dict[str, Any]]] = None,
    *,
    tail_threshold: float = -300.0,
    hour_window: int = 3,
    max_cases: int = 5,
    as_of: bool = True,
) -> Dict[str, Any]:
    """把相似亏损 Case 挂进候选 dict，供 Risk Gate 直接消费。"""
    out = dict(candidate)
    out["similar_tail_loss_cases"] = match_similar_tail_cases(
        candidate,
        cases=cases,
        tail_threshold=tail_threshold,
        hour_window=hour_window,
        max_cases=max_cases,
        as_of=as_of,
    )
    return out
