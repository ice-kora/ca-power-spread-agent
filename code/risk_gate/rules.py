# -*- coding: utf-8 -*-
"""
code/risk_gate/rules.py

Risk Gate 的白盒规则（每个规则 = 一个纯函数，可读、可配置、可测试）。

规则约定：
  - 每个规则是 (candidate, cfg) -> RuleHit | None 的纯函数；
    RuleHit.level ∈ {REJECT, WARNING}；
  - REJECT 是"一票否决"（gate.py 保证 REJECT 黏住）；
  - WARNING 只标记，不拦（Rule Engine / 交易员据此决策）；
  - candidate 是 dict，字段见 gate.py 的 Candidate 文档；
  - 所有阈值来自 config（train+val 校准），规则本身不写死任何数字。

注意（诚实声明）：
  部分 reason_code（LOW_CONFIDENCE / HIGH_VOLATILITY / MODEL_UNSTABLE /
  EXTREME_TAIL_NODE）在 train+val 验证中**不具判别力**（V0.1 已删/降级），
  本版一律实现为 WARNING 级（审计/参考用），默认不拦截交易。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from code.risk_gate.config import RiskGateConfig
from code.risk_gate.constants import (
    DIRECTION_BUY,
    DIRECTION_SELL,
    LEVEL_REJECT,
    LEVEL_WARNING,
)

# ---------------------------------------------------------------------------
# 规则命中结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuleHit:
    rule_id: str            # 如 "R7a"
    reason_code: str        # 如 "BUY_ON_POSITIVE_DRIFT_NODE"
    level: str              # REJECT / WARNING
    message: str            # 人可读说明
    threshold: str = ""     # 命中的阈值/依据（审计用）
    rule_version: str = "0.2"


#: 规则函数签名：fn(candidate: dict, cfg: RiskGateConfig) -> Optional[RuleHit]
RuleFn = Callable[[Dict, RiskGateConfig], Optional[RuleHit]]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _nan(x) -> bool:
    """判断是否为 NaN/None（字符串等非数值不视为 NaN）。"""
    if x is None:
        return True
    if isinstance(x, float):
        return x != x
    if isinstance(x, (int,)):
        return False
    import numpy as _np
    if isinstance(x, (_np.floating, _np.integer)):
        return float(x) != float(x)
    return False


def _f(x, default: Optional[float] = None) -> Optional[float]:
    if x is None or _nan(x):
        return default
    return float(x)


def _s(x) -> str:
    if x is None:
        return ""
    return str(x).strip()


# ---------------------------------------------------------------------------
# R01 数据完整性（DATA_MISSING → REJECT）
# ---------------------------------------------------------------------------
def rule_data_missing(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R01 数据完整性：关键输入缺失 → 保守 REJECT（DATA_MISSING）。"""
    missing = []
    for f_ in cfg.required_fields:
        v = candidate.get(f_)
        if v is None or _nan(v) or _s(v) == "":
            missing.append(f_)
    # 方向推导所需的 expected_return 缺失属于 Data Missing（不单列 NO_CLEAR_DIRECTION）
    if missing:
        return RuleHit(
            rule_id="R01",
            reason_code="DATA_MISSING",
            level=LEVEL_REJECT,
            message=f"关键输入缺失，保守拒绝: {missing}",
            threshold=f"required_fields={list(cfg.required_fields)}",
        )
    return None


# ---------------------------------------------------------------------------
# R02 正漂移节点上 BUY（BUY_ON_POSITIVE_DRIFT_NODE → REJECT，empirical guardrail）
# ---------------------------------------------------------------------------
def rule_buy_on_positive_drift(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R7a 正漂移节点上 BUY（逆漂移）→ REJECT（BUY_ON_POSITIVE_DRIFT_NODE，empirical guardrail）。"""
    node = _s(candidate.get("node"))
    direction = _s(candidate.get("direction")).upper()
    if direction != DIRECTION_BUY:
        return None
    # 1) empirical guardrail（V0.1，train+val 证据，DATA-DERIVED TEMPORARY）
    for gr in cfg.empirical_guardrails:
        if gr.direction == DIRECTION_BUY and gr.node == node:
            ev = gr.trainval_evidence
            return RuleHit(
                rule_id="R7a",
                reason_code=gr.reason_code,
                level=LEVEL_REJECT,
                message=(
                    f"{node} 上 BUY 逆正漂移（漂移 {ev.get('node_drift_trainval', '?')}），"
                    f"无条件 BUY mean {ev.get('BUY_uncond_mean', '?')}、"
                    f"maxloss {ev.get('BUY_uncond_maxloss', '?')} → 拒绝。"
                ),
                threshold=f"train+val node_drift>0 且 BUY tail 深（cvar99 {ev.get('BUY_uncond_cvar99', '?')}）",
                rule_version=gr.status,
            )
    # 2) 泛化漂移规则（默认关闭）
    if cfg.drift_rule_enabled:
        drift = _f(candidate.get("node_drift"))
        if drift is not None and drift > cfg.drift_buy_threshold:
            return RuleHit(
                rule_id="R7a-gen",
                reason_code="BUY_ON_POSITIVE_DRIFT_NODE",
                level=LEVEL_REJECT,
                message=f"node_drift={drift:+.2f} > {cfg.drift_buy_threshold} 且 BUY → 拒绝",
                threshold=f"node_drift > {cfg.drift_buy_threshold}",
            )
    return None


# ---------------------------------------------------------------------------
# R03 负漂移节点上 SELL（SELL_ON_NEGATIVE_DRIFT_NODE → REJECT，empirical guardrail）
# ---------------------------------------------------------------------------
def rule_sell_on_negative_drift(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R7b 负漂移节点上 SELL（逆漂移）→ REJECT（SELL_ON_NEGATIVE_DRIFT_NODE，empirical guardrail）。"""
    node = _s(candidate.get("node"))
    direction = _s(candidate.get("direction")).upper()
    if direction != DIRECTION_SELL:
        return None
    for gr in cfg.empirical_guardrails:
        if gr.direction == DIRECTION_SELL and gr.node == node:
            ev = gr.trainval_evidence
            return RuleHit(
                rule_id="R7b",
                reason_code=gr.reason_code,
                level=LEVEL_REJECT,
                message=(
                    f"{node} 上 SELL 逆负漂移（漂移 {ev.get('node_drift_trainval', '?')}），"
                    f"无条件 SELL mean {ev.get('SELL_uncond_mean', '?')}、"
                    f"maxloss {ev.get('SELL_uncond_maxloss', '?')} → 拒绝。"
                ),
                threshold=f"train+val node_drift<0 且 SELL 尾深（cvar99 {ev.get('SELL_uncond_cvar99', '?')}）",
                rule_version=gr.status,
            )
    if cfg.drift_rule_enabled:
        drift = _f(candidate.get("node_drift"))
        if drift is not None and drift < -cfg.drift_sell_threshold:
            return RuleHit(
                rule_id="R7b-gen",
                reason_code="SELL_ON_NEGATIVE_DRIFT_NODE",
                level=LEVEL_REJECT,
                message=f"node_drift={drift:+.2f} < {cfg.drift_sell_threshold:+.2f} 且 SELL → 拒绝",
                threshold=f"node_drift < {-cfg.drift_sell_threshold}",
            )
    return None


# ---------------------------------------------------------------------------
# R04 低样本（LOW_SAMPLE_SUPPORT → REJECT）
# ---------------------------------------------------------------------------
def rule_low_sample(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R6 低样本：同 node×hour 历史样本不足 → REJECT（LOW_SAMPLE_SUPPORT，empirical guardrail）。"""
    hist_n = _f(candidate.get("hist_n"))
    if hist_n is not None and hist_n < cfg.low_sample_min_hist:
        return RuleHit(
            rule_id="R6",
            reason_code="LOW_SAMPLE_SUPPORT",
            level=LEVEL_REJECT,
            message=(
                f"同 node×hour 历史样本 hist_n={hist_n:g} < "
                f"{cfg.low_sample_min_hist:g}（cold-start）→ 统计不可靠，拒绝"
            ),
            threshold=f"hist_n < {cfg.low_sample_min_hist:g}",
            rule_version="DATA-DERIVED TEMPORARY GUARDRAIL",
        )
    return None


# ---------------------------------------------------------------------------
# R05 尾部过深（EXTREME_TAIL_NODE → WARNING，不拦）
# ---------------------------------------------------------------------------
def rule_extreme_tail(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R5 尾部过深：历史 cvar99/rcvar99 过深 → WARNING（EXTREME_TAIL_NODE，不拦）。"""
    direction = _s(candidate.get("direction")).upper()
    # SELL 侧看左尾 cvar99；BUY 侧看右尾 rcvar99（rcvar99 本身为负号表示 BUY 亏损深度）
    cvar = None
    if direction == DIRECTION_SELL:
        cvar = _f(candidate.get("cvar99"))
    elif direction == DIRECTION_BUY:
        cvar = _f(candidate.get("rcvar99"))
    if cvar is not None and cvar < cfg.cvar_tail_threshold:
        return RuleHit(
            rule_id="R5",
            reason_code="EXTREME_TAIL_NODE",
            level=LEVEL_WARNING,
            message=(
                f"同 node×hour 历史 {('cvar99' if direction == DIRECTION_SELL else 'rcvar99')}="
                f"{cvar:.0f} < {cfg.cvar_tail_threshold:.0f}，尾部深（仅警告，不拦）"
            ),
            threshold=f"{'cvar99' if direction == DIRECTION_SELL else 'rcvar99'} < {cfg.cvar_tail_threshold:.0f}",
        )
    return None


# ---------------------------------------------------------------------------
# R06 高波动（HIGH_VOLATILITY → WARNING，默认仅警告）
# ---------------------------------------------------------------------------
def rule_high_volatility(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R3 高波动：vol_ratio 过高 → WARNING（HIGH_VOLATILITY，V0.1 验证无判别力，不拦）。"""
    if not cfg.high_volatility_enabled:
        return None
    ratio = _f(candidate.get("vol_ratio"))
    if ratio is not None and ratio > cfg.high_volatility_ratio_threshold:
        return RuleHit(
            rule_id="R3",
            reason_code="HIGH_VOLATILITY",
            level=LEVEL_WARNING,
            message=(
                f"vol_ratio={ratio:.2f} > {cfg.high_volatility_ratio_threshold:.2f} "
                f"（V0.1 验证无单调判别力，仅警告）"
            ),
            threshold=f"vol_ratio > {cfg.high_volatility_ratio_threshold:.2f}",
        )
    return None


# ---------------------------------------------------------------------------
# R07 模型不稳定（MODEL_UNSTABLE → WARNING，默认仅警告）
# ---------------------------------------------------------------------------
def rule_model_unstable(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R9 模型不稳定：uncertainty 过高 → WARNING（MODEL_UNSTABLE，v2 校准非单调，不拦）。"""
    if not cfg.model_unstable_enabled:
        return None
    unc = _f(candidate.get("uncertainty"))
    if unc is not None and unc > cfg.model_unstable_uncertainty_cap:
        return RuleHit(
            rule_id="R9",
            reason_code="MODEL_UNSTABLE",
            level=LEVEL_WARNING,
            message=(
                f"uncertainty={unc:.3f} > {cfg.model_unstable_uncertainty_cap:.3f} "
                f"（v2 校准：与 PnL 非单调，仅警告，不拦）"
            ),
            threshold=f"uncertainty > {cfg.model_unstable_uncertainty_cap:.3f}",
        )
    return None


# ---------------------------------------------------------------------------
# R08 相似亏损 Case（SIMILAR_TAIL_LOSS_CASE → WARNING，默认不拦）
# ---------------------------------------------------------------------------
def rule_similar_tail_loss_case(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R8 相似亏损 Case：命中历史亏损 Case → WARNING（SIMILAR_TAIL_LOSS_CASE，提示级）。"""
    if not cfg.similar_case_enabled:
        return None
    cases = candidate.get("similar_tail_loss_cases") or []
    if not cases:
        return None
    top = cases[0]
    return RuleHit(
        rule_id="R8",
        reason_code="SIMILAR_TAIL_LOSS_CASE",
        level=LEVEL_WARNING,
        message=(
            f"命中 {len(cases)} 条相似亏损 Case（最相似: "
            f"{top.get('case_id', '?')} {top.get('decision_date', '?')} H{top.get('hour', '?')} "
            f"PnL={top.get('PnL', '?')}）→ 仅警告"
        ),
        threshold=f"同 node×direction，|Δhour|<= {cfg.similar_case_hour_window}，PnL < {cfg.similar_case_tail_threshold:.0f}",
    )


# ---------------------------------------------------------------------------
# R09 低置信度（LOW_CONFIDENCE → WARNING，默认不拦）
# ---------------------------------------------------------------------------
def rule_low_confidence(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R1 低置信度：confidence 过低 → WARNING（LOW_CONFIDENCE，V0.1 验证未校准，不拦）。"""
    if not cfg.low_confidence_warn:
        return None
    conf = _f(candidate.get("confidence"))
    if conf is not None and conf < cfg.min_confidence:
        return RuleHit(
            rule_id="R1",
            reason_code="LOW_CONFIDENCE",
            level=LEVEL_WARNING,
            message=(
                f"confidence={conf:.3f} < {cfg.min_confidence:.2f} "
                f"（V0.1 验证未校准，仅警告；Rule Engine 可据此转 NO_TRADE）"
            ),
            threshold=f"confidence < {cfg.min_confidence:.2f}",
        )
    return None


# ---------------------------------------------------------------------------
# R10 期望边过小（EXPECTED_RETURN_TOO_SMALL → WARNING/INFO，Rule Engine 处理）
# ---------------------------------------------------------------------------
def rule_expected_return_too_small(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R10 期望边过小：|expected_return| 过小 → WARNING（EXPECTED_RETURN_TOO_SMALL，Rule Engine 处理）。"""
    if not cfg.expected_return_small_warn:
        return None
    er = _f(candidate.get("expected_return"))
    if er is not None and abs(er) < cfg.min_spread:
        return RuleHit(
            rule_id="R10",
            reason_code="EXPECTED_RETURN_TOO_SMALL",
            level=LEVEL_WARNING,
            message=(
                f"|expected_return|={abs(er):.2f} < {cfg.min_spread:.2f} "
                f"（边太小；由 Rule Engine 转 NO_TRADE，gate 仅提示）"
            ),
            threshold=f"|expected_return| < {cfg.min_spread:.2f}",
        )
    return None


# ---------------------------------------------------------------------------
# R11 证据冲突（EVIDENCE_CONFLICT → WARNING，默认不拦）
# ---------------------------------------------------------------------------
def rule_evidence_conflict(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R11 证据冲突：eligible Evidence 方向与候选相反 → WARNING（EVIDENCE_CONFLICT）。"""
    if not cfg.evidence_conflict_warn:
        return None
    direction = _s(candidate.get("direction")).upper()
    ctx = candidate.get("evidence_direction_context") or {}
    # ctx: {"SUPPORT_POSITIVE": n, "SUPPORT_NEGATIVE": n, "UNCERTAIN": n}
    n_pos = int(ctx.get("SUPPORT_POSITIVE", 0))
    n_neg = int(ctx.get("SUPPORT_NEGATIVE", 0))
    if n_pos == 0 and n_neg == 0:
        return None  # 无方向证据（当前全 UNCERTAIN）
    conflict = None
    if direction == DIRECTION_SELL and n_neg > 0:
        conflict = f"证据 {n_neg} 条支持 Return<0，与 SELL 冲突"
    if direction == DIRECTION_BUY and n_pos > 0:
        conflict = f"证据 {n_pos} 条支持 Return>0，与 BUY 冲突"
    if conflict is None:
        return None
    return RuleHit(
        rule_id="R11",
        reason_code="EVIDENCE_CONFLICT",
        level=LEVEL_WARNING,
        message=conflict + "（仅警告）",
        threshold="eligible Evidence.directional_effect 与候选方向相反",
    )


# ---------------------------------------------------------------------------
# R12 证据极端状态（EXTREME_STATE_EVIDENCE → REJECT/WARNING，evidence 驱动）
# ---------------------------------------------------------------------------
_SEVERITY_ORDER: tuple = ("INFO", "WATCH", "WARNING", "SEVERE", "CRITICAL")


def rule_extreme_state_evidence(candidate: Dict, cfg: RiskGateConfig) -> Optional[RuleHit]:
    """R12 证据极端状态：Pre-decision Evidence 的极端状态 severity 达到阈值 → REJECT/WARNING。

    消费 evidence_direction_context（由 evidence_adapter 汇总 eligible Evidence 产出）：
    - 只读 max_severity，不判方向（directional_effect=UNCERTAIN，证据仅当风险因子）；
    - severity ≥ cfg.evidence_extreme_severity_threshold → 命中；
    - 级别由 cfg.evidence_extreme_level 决定（默认 REJECT=保守；WARNING 则只标记）；
    - 无证据（max_severity=INFO / ctx 为空）→ 不触发（既有行为完全不变）。
    """
    if not cfg.evidence_extreme_enabled:
        return None
    ctx = candidate.get("evidence_direction_context") or {}
    sev = str(ctx.get("max_severity", "INFO")).upper()
    thr = str(cfg.evidence_extreme_severity_threshold or "WARNING").upper()
    if sev not in _SEVERITY_ORDER or thr not in _SEVERITY_ORDER:
        return None
    if _SEVERITY_ORDER.index(sev) < _SEVERITY_ORDER.index(thr):
        return None
    level = (LEVEL_REJECT if cfg.evidence_extreme_level == "REJECT" else LEVEL_WARNING)
    n_ev = int(ctx.get("n", 0))
    return RuleHit(
        rule_id="R12",
        reason_code="EXTREME_STATE_EVIDENCE",
        level=level,
        message=(
            f"Pre-decision Evidence 极端状态（{n_ev} 条，severity={sev} ≥ {thr}）→ "
            f"{cfg.evidence_extreme_level}；directional_effect=UNCERTAIN，仅风险因子，不判方向"
        ),
        threshold=f"evidence_direction_context.max_severity ≥ {thr}",
        rule_version="0.2 (evidence-driven)",
    )


# ---------------------------------------------------------------------------
# 规则注册表（按判定优先级排序：REJECT 规则在前）
# ---------------------------------------------------------------------------
#: 有序规则列表。gate.py 依次执行；任何 REJECT 命中即整体 REJECT。
RULES: List[Dict] = [
    {"rule_id": "R01", "reason_code": "DATA_MISSING",              "fn": rule_data_missing},
    {"rule_id": "R7a", "reason_code": "BUY_ON_POSITIVE_DRIFT_NODE","fn": rule_buy_on_positive_drift},
    {"rule_id": "R7b", "reason_code": "SELL_ON_NEGATIVE_DRIFT_NODE","fn": rule_sell_on_negative_drift},
    {"rule_id": "R6",  "reason_code": "LOW_SAMPLE_SUPPORT",        "fn": rule_low_sample},
    {"rule_id": "R12", "reason_code": "EXTREME_STATE_EVIDENCE",    "fn": rule_extreme_state_evidence},
    {"rule_id": "R5",  "reason_code": "EXTREME_TAIL_NODE",         "fn": rule_extreme_tail},
    {"rule_id": "R3",  "reason_code": "HIGH_VOLATILITY",           "fn": rule_high_volatility},
    {"rule_id": "R9",  "reason_code": "MODEL_UNSTABLE",            "fn": rule_model_unstable},
    {"rule_id": "R8",  "reason_code": "SIMILAR_TAIL_LOSS_CASE",    "fn": rule_similar_tail_loss_case},
    {"rule_id": "R1",  "reason_code": "LOW_CONFIDENCE",            "fn": rule_low_confidence},
    {"rule_id": "R10", "reason_code": "EXPECTED_RETURN_TOO_SMALL", "fn": rule_expected_return_too_small},
    {"rule_id": "R11", "reason_code": "EVIDENCE_CONFLICT",         "fn": rule_evidence_conflict},
]

#: 规则一览（供文档/审计）
def describe_rules() -> List[Dict]:
    """返回规则目录：rule_id / reason_code / 级别 / 一句话说明。"""
    return [
        {
            "rule_id": r["rule_id"],
            "reason_code": r["reason_code"],
            "level": _rule_default_level(r["rule_id"]),
            "description": r["fn"].__doc__.strip().split("\n")[0] if r["fn"].__doc__ else "",
        }
        for r in RULES
    ]


def _rule_default_level(rule_id: str) -> str:
    if rule_id in ("R01", "R7a", "R7b", "R6", "R12"):
        return LEVEL_REJECT
    return LEVEL_WARNING
