# -*- coding: utf-8 -*-
"""
agent/case_library/policy.py

CaseGenerationPolicy：Case 自动生成规则 + 检索硬约束（严格防 Case 穿越）。

用途（V0.2 Agent · 模块 2/3 的自动化）：
  - 不再靠人工事后从 test 结果里"挑" Case；改由本策略对每条交易记录做
    机械判定，命中即打 Case。判定完全基于可审计字段（PnL / 方向 / 风险门
    裁决 / 人工决策），不依赖 LLM，不依赖人工事后选样。

自动生成规则（5 类，全部可配置阈值）：
  1. TAIL_LOSS                   : PnL <= tail_loss_threshold
  2. LARGE_PROFIT                : PnL >= high_profit_threshold
  3. HIGH_SIGNAL_WRONG_DIRECTION : signal_strength >= signal_strength_threshold
                                    AND direction_wrong=True
                                    （signal_strength 默认 = max(prob_pos, prob_neg)，
                                      本项目的 confidence 压缩在 [0,0.44] 不具备
                                      高信号语义，见 stage3 confidence 校准结论）
  4. RISK_GATE_FAILURE           : risk_gate_decision==PASS（或含 WARNING，可选）
                                    AND 该笔同时命中 Tail Loss
  5. HUMAN_OVERRIDE              : human_action 非空 AND human_action != suggested_action

时间字段（每个 Case 必须带，缺省由 policy 计算）：
  case_created_at      生成时刻（本项目 = 目标日完整结算后 target_date+1 06:00）
  case_available_at    最早可被未来决策检索的时刻（= case_created_at）
  review_completed_at  人工/Agent 复核完成时刻（未复核为空字符串）

硬约束（严格防 Case 穿越，检索方必须调用 is_retrievable）：
  is_retrievable(case, decision_time)
      ⇔  case_available_at <= decision_time
  任一未来决策（decision_time = decision_date 10:00 PT，naive local PT）只能
  检索到 case_available_at 不晚于该时点的 Case；否则该 Case 的目标日结算尚未
  完整发生，禁止进入决策层（只能进 Post-trade Review）。
  case_available_at 缺失 → 一律不可检索（保守，宁可不穿越）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 保证从任意 cwd 运行都能 import agent.*
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.case_library.case import Case  # noqa: E402
from code.market_rules import CURRENT_MARKET_RULE_VERSION  # noqa: E402

# ---------------------------------------------------------------------------
# Case 类型常量（单一事实来源）
# ---------------------------------------------------------------------------
CASE_TYPE_TAIL_LOSS: str = "TAIL_LOSS"
CASE_TYPE_LARGE_PROFIT: str = "LARGE_PROFIT"
CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION: str = "HIGH_SIGNAL_WRONG_DIRECTION"
CASE_TYPE_RISK_GATE_FAILURE: str = "RISK_GATE_FAILURE"
CASE_TYPE_HUMAN_OVERRIDE: str = "HUMAN_OVERRIDE"

CASE_TYPES: tuple = (
    CASE_TYPE_TAIL_LOSS,
    CASE_TYPE_LARGE_PROFIT,
    CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION,
    CASE_TYPE_RISK_GATE_FAILURE,
    CASE_TYPE_HUMAN_OVERRIDE,
)

#: 与 CaseLibrary 历史口径一致的 action 短名（无 _DA 后缀）
_ACTION_SHORT: Dict[str, str] = {
    "BUY_DA": "BUY",
    "SELL_DA": "SELL",
    "NO_TRADE": "NO_TRADE",
}

# ---------------------------------------------------------------------------
# 时间口径常量（对齐 market_timeline.md：决策 cutoff = decision_date 10:00 PT）
# ---------------------------------------------------------------------------
_DECISION_CUTOFF_LOCAL_HH: str = "10"   # D-1（= decision_date）10:00 PT，官方 DAM Market Close
_SETTLEMENT_AVAILABLE_HH: str = "06"    # 目标日完整结算后次日 06:00（保守，RTPD(T) 于 T 24:00 完结）


# ---------------------------------------------------------------------------
# 决策记录（输入：一条回测/预测交易记录）
# ---------------------------------------------------------------------------
@dataclass
class DecisionRecord:
    """一条待判定的交易记录（全部字段程序化填充，不依赖 LLM）。"""

    node: str = ""
    target_date: str = ""                     # 交付日 ISO YYYY-MM-DD
    hour: int = -1
    split: str = ""
    suggested_action: str = ""                # BUY_DA / SELL_DA / NO_TRADE
    human_action: str = ""                    # 人工决策；空 = 无人工记录
    expected_return: float = 0.0
    confidence: float = 0.0
    prob_positive: float = 0.0
    prob_negative: float = 0.0
    uncertainty: float = 0.0
    actual_return: float = 0.0
    actual_direction: int = 0                 # sign(actual_return)
    PnL: Optional[float] = None               # None = 由 suggested_action 推导
    risk_gate_decision: str = ""              # PASS / WARNING / REJECT
    risk_gate_reasons: List[str] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)  # 附加 as-of 快照等
    # 市场规则版本标记（Post-trade Review 记录：这笔是在哪套规则下做的）
    market_rule_version: str = CURRENT_MARKET_RULE_VERSION

    # -- 派生 ----------------------------------------------------------
    @property
    def is_directional(self) -> bool:
        return self.suggested_action in ("BUY_DA", "SELL_DA")

    @property
    def expected_direction(self) -> int:
        """建议方向（SELL_DA→+1, BUY_DA→-1, NO_TRADE→0）。"""
        if self.suggested_action == "SELL_DA":
            return 1
        if self.suggested_action == "BUY_DA":
            return -1
        return 0

    @property
    def direction_wrong(self) -> bool:
        """有方向的交易且实际 Return 符号与建议方向相反 = 方向判错。

        PnL 口径（business_contract §5，1 MWh/仓）：
          SELL_DA: PnL = +actual_return；BUY_DA: PnL = −actual_return。
        因此对方向性交易，direction_wrong ⇔ PnL < 0。
        """
        if not self.is_directional:
            return False
        exp_dir = self.expected_direction
        if exp_dir == 0:
            return False
        act_dir = self.actual_direction
        return act_dir != 0 and act_dir != exp_dir

    @property
    def pnl(self) -> float:
        """实际 PnL（$/MWh，1 MWh/仓）；NO_TRADE = 0。"""
        if self.PnL is not None:
            return float(self.PnL)
        if self.suggested_action == "SELL_DA":
            return float(self.actual_return)
        if self.suggested_action == "BUY_DA":
            return -float(self.actual_return)
        return 0.0

    def signal_strength(self, source: str = "max_prob") -> float:
        """模型信号强度（默认 max(prob_pos, prob_neg)，即模型押注方向的概率质量）。

        Args:
            source: "max_prob"（默认，方向概率质量）| "confidence"（模型置信度）
        """
        if source == "confidence":
            return float(self.confidence or 0.0)
        return float(max(self.prob_positive, self.prob_negative) or 0.0)


# ---------------------------------------------------------------------------
# 时间工具（naive local PT；与 schema.parse_timestamp 的 naive 口径一致）
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def settlement_available_at(target_date: str) -> str:
    """Case 最早可检索时刻 = 目标日完整结算后次日 06:00（naive local PT）。

    RTPD(T) 全部 24 小时于 T 当日实时产生，T 24:00 完结；保守取 T+1 06:00，
    保证结果已完整沉淀。不可解析时返回空串（保守）。
    """
    d = _parse_date(target_date)
    if d is None:
        return ""
    return f"{(d + timedelta(days=1)).isoformat()}T{_SETTLEMENT_AVAILABLE_HH}:00:00"


def decision_time_for(target_date: str) -> str:
    """某候选 target_date 对应的决策时点 = (target_date−1) 10:00 PT（naive local PT）。"""
    d = _parse_date(target_date)
    if d is None:
        return ""
    return f"{(d - timedelta(days=1)).isoformat()}T{_DECISION_CUTOFF_LOCAL_HH}:00:00"


def is_retrievable(case: Any, decision_time: str) -> bool:
    """硬约束：只有 case_available_at <= decision_time 才允许在未来决策中检索。

    case 可以是 Case dataclass 或 dict。case_available_at 缺失/不可解析
    → 一律 False（保守，宁可不穿越，绝不穿越）。
    """
    avail = getattr(case, "case_available_at", None)
    if avail is None and isinstance(case, dict):
        avail = case.get("case_available_at")
    if not avail:
        return False
    a = _parse_date(str(avail)[:10])
    c = _parse_date(decision_time)
    if a is None or c is None:
        return False
    return str(avail)[:16] <= str(decision_time)[:16]  # 精确到分钟比较，保持自洽


# ---------------------------------------------------------------------------
# CaseGenerationPolicy（阈值全部可配置，默认对齐项目既有口径）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CaseGenerationPolicy:
    """Case 自动生成策略。所有阈值可配置，默认值对齐 Risk Gate / Rule Engine 口径。"""

    tail_loss_threshold: float = -300.0
    #   source: code/risk_gate/config.py similar_case_tail_threshold = -300
    #           （与 R8 的"相似亏损 Case"同一口径）
    high_profit_threshold: float = 300.0
    #   对称口径（盈利端镜像）
    signal_strength_threshold: float = 0.70
    #   source: predictions_v2 的 max(prob_pos, prob_neg) 分布——0.70 为其历史
    #           最高分位附近，命中即"模型高信号"；confidence 压缩在 [0,0.44]
    #           不适用，故默认走 max_prob（见 DecisionRecord.signal_strength）。
    signal_strength_source: str = "max_prob"   # "max_prob" | "confidence"
    min_abs_return_for_signal: float = 5.0
    #   |expected_return| >= 5 才算有意义的方向信号（对齐 Rule Engine min_spread）
    min_confidence_for_trade: float = 0.20
    #   对齐 Rule Engine min_confidence（用于推导 suggested_action）
    risk_gate_failure_include_warning: bool = False
    #   默认严格：只有 RiskGate==PASS 才算"放行后仍 Tail Loss"；
    #   置 True 则把 WARNING（放行但标记）也计入。
    human_override_requires_human: bool = True
    #   无人工决策记录时（human_action 空）不触发 HUMAN_OVERRIDE（不伪造人工）。

    # ------------------------------------------------------------------
    def signal_strength_of(self, rec: DecisionRecord) -> float:
        return rec.signal_strength(source=self.signal_strength_source)

    def is_tail_loss(self, rec: DecisionRecord) -> bool:
        return rec.pnl <= self.tail_loss_threshold

    def is_large_profit(self, rec: DecisionRecord) -> bool:
        return rec.pnl >= self.high_profit_threshold

    def is_high_signal_wrong_direction(self, rec: DecisionRecord) -> bool:
        if not rec.is_directional:
            return False
        if abs(rec.expected_return) < self.min_abs_return_for_signal:
            return False
        if self.signal_strength_of(rec) < self.signal_strength_threshold:
            return False
        return rec.direction_wrong

    def is_risk_gate_failure(self, rec: DecisionRecord) -> bool:
        gate_ok = rec.risk_gate_decision == "PASS"
        if self.risk_gate_failure_include_warning:
            gate_ok = rec.risk_gate_decision in ("PASS", "WARNING")
        return gate_ok and self.is_tail_loss(rec)

    def is_human_override(self, rec: DecisionRecord) -> bool:
        if self.human_override_requires_human and not rec.human_action:
            return False
        return bool(rec.human_action) and rec.human_action != rec.suggested_action

    # ------------------------------------------------------------------
    def classify(self, rec: DecisionRecord) -> List[str]:
        """对一条记录返回命中的所有 Case 类型（有序、去重）。"""
        types: List[str] = []
        if self.is_tail_loss(rec):
            types.append(CASE_TYPE_TAIL_LOSS)
        if self.is_large_profit(rec):
            types.append(CASE_TYPE_LARGE_PROFIT)
        if self.is_high_signal_wrong_direction(rec):
            types.append(CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION)
        if self.is_risk_gate_failure(rec):
            types.append(CASE_TYPE_RISK_GATE_FAILURE)
        if self.is_human_override(rec):
            types.append(CASE_TYPE_HUMAN_OVERRIDE)
        return types


# ---------------------------------------------------------------------------
# Case 构建与批量生成
# ---------------------------------------------------------------------------
def _why_text(case_type: str, rec: DecisionRecord, policy: CaseGenerationPolicy) -> str:
    """机械生成的机制说明（事实性，不做方向判断，不输出交易规则）。"""
    if case_type == CASE_TYPE_TAIL_LOSS:
        return (
            f"自动规则命中 TAIL_LOSS：PnL={rec.pnl:.1f} <= "
            f"{policy.tail_loss_threshold:.1f}，suggested_action={rec.suggested_action}。"
            f"实际 Return={rec.actual_return:+.1f}。"
        )
    if case_type == CASE_TYPE_LARGE_PROFIT:
        return (
            f"自动规则命中 LARGE_PROFIT：PnL={rec.pnl:.1f} >= "
            f"{policy.high_profit_threshold:.1f}，suggested_action={rec.suggested_action}。"
            f"实际 Return={rec.actual_return:+.1f}。"
        )
    if case_type == CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION:
        return (
            f"自动规则命中 HIGH_SIGNAL_WRONG_DIRECTION：signal_strength="
            f"{policy.signal_strength_of(rec):.3f} >= {policy.signal_strength_threshold:.2f}，"
            f"但实际 Return 方向（{rec.actual_direction:+d}）与建议方向 "
            f"（{rec.expected_direction:+d}）相反（direction_wrong=True）。"
        )
    if case_type == CASE_TYPE_RISK_GATE_FAILURE:
        return (
            f"自动规则命中 RISK_GATE_FAILURE：RiskGate 裁决 {rec.risk_gate_decision} "
            f"（放行）但该笔仍发生 Tail Loss（PnL={rec.pnl:.1f}）。"
            f"gate 理由={rec.risk_gate_reasons or '(无)'}。"
        )
    if case_type == CASE_TYPE_HUMAN_OVERRIDE:
        return (
            f"自动规则命中 HUMAN_OVERRIDE：human_action={rec.human_action!r} != "
            f"suggested_action={rec.suggested_action!r}。"
        )
    return f"自动生成 Case（类型 {case_type}）。"


def build_case(
    rec: DecisionRecord,
    case_type: str,
    policy: CaseGenerationPolicy,
    case_id: str,
) -> Case:
    """把一条记录 + 一个命中的类型构造成 Case（时间字段由 policy 口径计算）。"""
    available_at = settlement_available_at(rec.target_date)
    return Case(
        case_id=case_id,
        decision_date=str((_parse_date(rec.target_date) or date.today()) - timedelta(days=1)),
        node=rec.node,
        hour=int(rec.hour),
        model_prediction=_ACTION_SHORT.get(rec.suggested_action, rec.suggested_action or "NO_TRADE"),
        expected_return=float(rec.expected_return or 0.0),
        confidence=float(rec.confidence or 0.0),
        available_information_at_decision_time=dict(rec.extras or {}),
        event_evidence=[],  # 由 Evidence 层另行挂载（GFS forecast / placeholder）
        human_decision=rec.human_action or "",
        actual_DA=rec.extras.get("actual_da"),
        actual_RTPD=rec.extras.get("actual_rtpd"),
        actual_Return=float(rec.actual_return or 0.0),
        PnL=rec.pnl,
        why_correct_or_wrong=_why_text(case_type, rec, policy),
        lessons=[f"auto-tagged by {case_type}"],
        related_rules=list(rec.risk_gate_reasons or []),
        case_created_at=available_at,
        case_available_at=available_at,
        review_completed_at="",  # 未复核
        market_rule_version=rec.market_rule_version,
    )


def generate_cases(
    records: List[DecisionRecord],
    policy: Optional[CaseGenerationPolicy] = None,
    case_id_prefix: str = "CASE-AUTO-",
) -> List[Case]:
    """对记录列表批量自动生成 Case（一条记录可命中多个类型 → 多个 Case）。

    不做人工选样：凡是策略命中的一律生成。去重键 = (node, target_date, hour,
    case_type)，同键只保留第一条。
    """
    policy = policy or CaseGenerationPolicy()
    cases: List[Case] = []
    seen: set = set()
    seq = 1
    for rec in records:
        for ctype in policy.classify(rec):
            key = (rec.node, rec.target_date, int(rec.hour), ctype)
            if key in seen:
                continue
            seen.add(key)
            cases.append(build_case(rec, ctype, policy, f"{case_id_prefix}{seq:04d}"))
            seq += 1
    return cases


def case_type_counts(cases: List[Case]) -> Dict[str, int]:
    """按类型统计（用于审计自动生成的覆盖）。"""
    counts: Dict[str, int] = {}
    for c in cases:
        why = c.why_correct_or_wrong or ""
        for t in CASE_TYPES:
            if why.startswith(f"自动规则命中 {t}"):
                counts[t] = counts.get(t, 0) + 1
                break
    return counts


# ---------------------------------------------------------------------------
# 自检（unittest 可直接 import 本模块）
# ---------------------------------------------------------------------------
def _demo_record() -> DecisionRecord:
    return DecisionRecord(
        node="CONTROLX_1_N001",
        target_date="2026-07-09",
        hour=2,
        split="test",
        suggested_action="BUY_DA",
        expected_return=-141.3,
        confidence=0.905,
        prob_positive=0.095,
        prob_negative=0.905,
        uncertainty=0.5,
        actual_return=2216.3,
        actual_direction=1,
        risk_gate_decision="PASS",
    )


if __name__ == "__main__":
    rec = _demo_record()
    pol = CaseGenerationPolicy()
    print("demo record PnL:", rec.pnl)
    print("direction_wrong:", rec.direction_wrong)
    print("signal_strength:", pol.signal_strength_of(rec))
    print("classify:", pol.classify(rec))
    c = build_case(rec, CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION, pol, "CASE-AUTO-0001")
    print("case:", c.short_summary())
    print("  case_created_at:", c.case_created_at)
    print("  case_available_at:", c.case_available_at)
    # 硬约束演示
    dt = decision_time_for("2026-07-09")     # 2026-07-08 10:00（决策时点）
    dt_later = decision_time_for("2026-07-11")  # 2026-07-10 10:00
    print("decision_time_for(07-09):", dt, "→ is_retrievable:", is_retrievable(c, dt))
    print("decision_time_for(07-11):", dt_later, "→ is_retrievable:", is_retrievable(c, dt_later))
