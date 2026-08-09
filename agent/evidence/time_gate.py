# -*- coding: utf-8 -*-
"""
agent/evidence/time_gate.py

Evidence Time Gate（V0.2 修正核心）：防止 Agent 信息穿越 + MOCK 硬隔离。

职责：
  在 Agent Evidence 与 Risk Gate / Rule Engine 之间，程序化判断每条证据
  是否满足 As-of Decision-Time 约束：

      IF 非 MOCK 且 available_at <= decision_cutoff:  decision_eligible = TRUE（Pre-decision）
      ELSE:                                            decision_eligible = FALSE（Post-decision / DEMO MOCK）

  available_at 是 Time Gate **唯一**判据（AsOfRecord 的 as-of 时点）；available_at
  缺失 → 不可决策（AVAILABILITY_NOT_PROVEN），**绝不 fallback** published_at /
  initialization_time。若初始化时刻本身已晚于 cutoff（INITIALIZATION_AFTER_CUTOFF），
  可用 Strong Impossibility 提前判不可用（不是把 init 当 available_at）。

  decision_eligible 一律由本模块程序计算，禁止由 LLM 自行判断。

  Post-decision Evidence 自动隔离到 post_decision 列表，只能用于 Post-trade
  Review（解释"为什么亏/是否有不可预见事件"），绝不进入生产交易决策。

  # MOCK 硬隔离（Agent B）
  is_mock=True 的 Evidence 只能用于测试程序 / UI 演示结构 / 单元测试；
  split_by_eligibility() 会把它单独归入 demo_mock 桶，Decision Card 显示
  "DATA NOT ELIGIBLE / DEMO MOCK"，绝不悄悄参与交易建议。

  # NOT_BACKTEST_SAFE
  若数据源无法提供历史发布时间（published_at 缺失或不可信），该证据
  在严格 historical as-of backtest 中应视为不可用（默认 FALSE），并标记
  NOT_BACKTEST_SAFE，不能用于回测。

  # 特征级 Time Gate（Agent B P0-2）
  特征（canonical X 区）的可用性由 feature_is_decision_eligible() 判定，与 UI 展示
  共用**同一上界**（availability_basis / latest_possible_available_at）。
  无精确发布时刻的滞后/历史特征 → STRUCTURAL_LAG + decision_date 00:00 PT（最晚可证上界），
  UI 不显示虚假精确时间戳（如 23:59）。validate/assert_feature_eligibility_consistent 强制
  铁律：displayed available_at > decision_cutoff ⇒ decision_eligible MUST NOT = TRUE。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.evidence.schema import Evidence, evidence_from_dict, parse_timestamp
from code.data_acquisition.schemas import (
    AVAILABILITY_BASIS_KEY,
    AVAILABILITY_BASIS_STATIC,
    LATEST_POSSIBLE_AVAILABLE_AT_KEY,
    feature_decision_eligible as _feature_decision_eligible,
    latest_available_bound as _latest_available_bound,
    structural_lag_available_bound as _structural_lag_available_bound,
)


def is_mock_evidence(ev) -> bool:
    """判断一条证据是否为 DEMO/MOCK 数据（仅测试/演示用）。

    兼容 Evidence 对象与 dict：优先读 is_mock 布尔，其次 provenance 语义。
    """
    if isinstance(ev, Evidence):
        return bool(ev.is_mock)
    if isinstance(ev, dict):
        is_mock = ev.get("is_mock", False)
        if isinstance(is_mock, str):
            return is_mock.strip().lower() in ("true", "1", "yes")
        return bool(is_mock)
    return False


def is_available_before_cutoff(available_at: Optional[str],
                               decision_cutoff: Optional[str]) -> bool:
    """特征可用性门槛：`available_at <= decision_cutoff` 才允许进入生产特征/证据。

    business_contract §4 铁律：任何特征若 `available_at > decision_cutoff`
    （D-1 日 10:00 PT，DAM Market Close / bid cutoff）→ **禁止进入训练/推理**。
    Evidence 的 available_at 即该条证据的可用时刻（Time Gate 唯一判据；缺失 → 不可决策），
    本函数与 `Evidence.decision_eligible` 同语义（纯程序计算，禁止 LLM 判断）。

    Returns:
        True  仅当 available_at 非空、可解析且 <= decision_cutoff
        False 任一时间缺失 / 不可解析 / 晚于 cutoff（宁保守不穿越）
    """
    pub = parse_timestamp(available_at)
    cutoff = parse_timestamp(decision_cutoff)
    if pub is None or cutoff is None:
        return False
    try:
        return pub <= cutoff
    except Exception:
        return False


def is_decision_eligible(ev: Evidence, decision_cutoff: Optional[str] = None) -> bool:
    """程序判断单条证据是否可在该决策时点使用。

    R7 硬隔离：is_mock=True 恒为 False，即便 available_at <= decision_cutoff。

    Args:
        ev: Evidence 对象（或可转 Evidence 的 dict）
        decision_cutoff: 决策截止 ISO 字符串；缺省用 ev.decision_cutoff
    Returns:
        True 仅当 非 MOCK、初始化未晚于 cutoff、且 available_at 非空且 <= decision_cutoff
    """
    if isinstance(ev, dict):
        ev = evidence_from_dict(ev)
    if not isinstance(ev, Evidence):
        return False
    if is_mock_evidence(ev):
        return False
    cutoff = decision_cutoff or getattr(ev, "decision_cutoff", None)
    if not cutoff:
        return False
    # 用副本评估，避免改动原对象
    probe = copy.copy(ev)
    probe.decision_cutoff = cutoff
    return bool(probe.decision_eligible)


def split_by_eligibility(
    evidences: Sequence[Evidence],
    decision_cutoff: Optional[str] = None,
) -> Tuple[List[Evidence], List[Evidence], List[Evidence]]:
    """把证据按「可用 / DEMO MOCK / Post-decision」三桶切分（MOCK 不悄悄参与）。

    Returns:
        (eligible, demo_mock, post_decision)
          eligible      : 非 MOCK 且 decision_eligible=True（Pre-decision，可进
                          Risk Gate / Rule Engine / 交易建议）
          demo_mock     : is_mock=True（只进测试/UI 演示；Decision Card 显示
                          DATA NOT ELIGIBLE / DEMO MOCK）
          post_decision : 非 MOCK 且 decision_eligible=False（只进 Post-trade Review）
    """
    eligible: List[Evidence] = []
    demo_mock: List[Evidence] = []
    post: List[Evidence] = []
    for ev in evidences:
        if is_mock_evidence(ev):
            demo_mock.append(ev)
        elif is_decision_eligible(ev, decision_cutoff):
            eligible.append(ev)
        else:
            post.append(ev)
    return eligible, demo_mock, post


def split_eligible(
    evidences: Sequence[Evidence],
    decision_cutoff: Optional[str] = None,
) -> Tuple[List[Evidence], List[Evidence]]:
    """按 As-of Decision-Time 约束切分（兼容旧两桶接口）。

    MOCK 证据并入 post_decision（它们同样不可进决策层；如需区分用
    split_by_eligibility 拿 demo_mock 桶）。

    Returns:
        (eligible, post_decision)
          eligible      : decision_eligible=True（Pre-decision，可进 Risk Gate/Rule Engine）
          post_decision : decision_eligible=False（Post-decision 或 DEMO MOCK，
                          只进 Post-trade Review / 演示）
    """
    eligible, demo_mock, post = split_by_eligibility(evidences, decision_cutoff)
    return eligible, post + demo_mock


def assert_no_post_decision(evidences: Sequence[Evidence],
                            decision_cutoff: Optional[str] = None) -> None:
    """防御性断言：若把 Post-decision / DEMO MOCK 证据误传进决策层，直接抛错。

    MOCK 证据给出专门的 "DATA NOT ELIGIBLE / DEMO MOCK" 错误，不能悄悄参与。
    """
    _, demo_mock, post = split_by_eligibility(evidences, decision_cutoff)
    if demo_mock:
        ids = [getattr(e, "evidence_id", None) or e.source or e.summary[:20] for e in demo_mock]
        raise RuntimeError(
            "Evidence Time Gate: 检测到 %d 条 DEMO MOCK 证据进入决策层，已拦截: %s\n"
            "DATA NOT ELIGIBLE / DEMO MOCK —— MOCK 仅用于测试/演示，禁止参与交易建议。"
            % (len(demo_mock), ids)
        )
    if post:
        ids = [e.evidence_id or e.source or e.summary[:20] for e in post]
        raise RuntimeError(
            "Evidence Time Gate: 检测到 %d 条 Post-decision 证据进入决策层，已拦截: %s"
            % (len(post), ids)
        )


# ---------------------------------------------------------------------------
# 特征级 Time Gate（Agent B · P0-2：展示口径 == 判定口径）
# ---------------------------------------------------------------------------
def feature_is_decision_eligible(feature_availability: Dict[str, Any],
                                 decision_date: str,
                                 decision_cutoff: Optional[str]) -> bool:
    """特征级 Time Gate：与 UI 展示共用**同一上界**（schemas.feature_decision_eligible）。

    铁律：displayed available_at（= latest_available_bound 同一上界）> decision_cutoff
          ⇒ 本函数 MUST NOT 返回 True。
    无精确发布时刻的滞后/历史特征以 availability_basis=STRUCTURAL_LAG、
    latest_possible_available_at=decision_date 00:00 PT（最晚可证上界）判定。
    """
    return _feature_decision_eligible(feature_availability, decision_date, decision_cutoff)


def feature_available_bound(feature_availability: Dict[str, Any],
                            decision_date: str) -> Optional[str]:
    """Time Gate 实际使用的 available_at 上界（UTC naive ISO），与 UI 展示完全一致。"""
    return _latest_available_bound(feature_availability, decision_date)


def structural_lag_available_bound(decision_date: str) -> Optional[str]:
    """STRUCTURAL_LAG 特征的最晚可证可用上界 = decision_date 00:00 PT → UTC naive ISO。"""
    return _structural_lag_available_bound(decision_date)


def validate_feature_eligibility(
    features_used: Sequence[Dict[str, Any]],
    decision_cutoff: Optional[str] = None,
) -> List[str]:
    """硬规则校验（P0-2）：返回所有违规项（空列表 = 通过）。

    硬规则：IF displayed available_at（上界）> decision_cutoff ⇒ decision_eligible MUST NOT = TRUE。
    反之，只要 decision_eligible=True，其 available_at/latest_possible_available_at 上界必须
    存在且 <= decision_cutoff（STATIC 特征无时间门槛，跳过）。
    """
    violations: List[str] = []
    for i, f in enumerate(features_used or []):
        if not isinstance(f, dict):
            continue
        eligible = bool(f.get("decision_eligible", False))
        basis = str(f.get(AVAILABILITY_BASIS_KEY, "")).upper()
        if basis == AVAILABILITY_BASIS_STATIC or not eligible:
            continue  # STATIC 无时间门槛；eligible=False 恒允许
        bound = (f.get("latest_possible_available_at")
                 or f.get("available_at"))
        cutoff = f.get("decision_cutoff") or decision_cutoff
        name = f.get("feature", f"features_used[{i}]")
        if not _time_bound_ok(bound, cutoff):
            violations.append(
                f"P0-2 硬规则: feature={name} decision_eligible=True 但其 displayed available_at "
                f"({bound!r}) 不存在或晚于 decision_cutoff ({cutoff!r}) —— 展示与 Time Gate 不一致")
    return violations


def assert_feature_eligibility_consistent(
    features_used: Sequence[Dict[str, Any]],
    decision_cutoff: Optional[str] = None,
) -> None:
    """防御性断言：特征展示与 Time Gate 判定不一致时直接抛错（P0-2 铁律）。"""
    violations = validate_feature_eligibility(features_used, decision_cutoff)
    if violations:
        raise RuntimeError(
            "Feature Time Gate: 展示 available_at 与 eligibility 判定不一致，已拦截:\n  "
            + "\n  ".join(violations))


def _time_bound_ok(bound: Optional[str], cutoff: Optional[str]) -> bool:
    """上界存在、可解析且 <= cutoff（缺一即 False，宁保守不穿越）。"""
    b = parse_timestamp(bound)
    c = parse_timestamp(cutoff)
    if b is None or c is None:
        return False
    try:
        return b <= c
    except Exception:
        return False
