# -*- coding: utf-8 -*-
"""
code/risk_gate/gate.py

Risk Gate 编排器：把候选交易 + as-of 风险特征 + 可用证据 + 相似 Case
送进白盒规则，输出 PASS / WARNING / REJECT + risk_reasons（reason_code 列表）。

职责边界（重要）：
  - Risk Gate **不预测方向**，只判定"这笔候选交易是否放行"。
  - 方向（BUY/SELL）由 Predictive Model 的 expected_return 符号 + Rule Engine 决定；
    gate 只按候选方向检查"顺漂移/逆漂移/尾部/样本量"等风险。
  - gate 是护栏，不是引擎：它不创造 alpha，只移除系统性负期望/高尾风险交易。

输入（candidate dict，全部 as-of）：
    node          节点 ID（如 "CONTROLX_1_N001"）
    target_date   交付日（ISO YYYY-MM-DD，候选 D+1）
    hour          小时 1~24
    expected_return  模型预期 Return = DA−RTPD（$/MWh）
    confidence    模型置信度 0~1（未校准，仅作参考）
    uncertainty   模型不确定度 0~1（v2，仅作参考）
    direction     "BUY"/"SELL"（缺省由 expected_return 符号推导）
    hist_n        同 node×hour 历史样本数（as-of，≤ target_date-2）
    cvar99        SELL 侧左尾 CVaR；rcvar99 BUY 侧右尾 CVaR（as-of）
    vol_ratio     近 30 日波动 / 历史波动（as-of）
    node_drift    node 级历史漂移 mean(actual_return)（as-of）
    similar_tail_loss_cases   Case Library 匹配的相似亏损 Case 列表（as-of）
    evidence_direction_context  可用的 Pre-decision Evidence 方向汇总 dict

输出：GateVerdict（decision / risk_reasons / rules_hit / details / version）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from code.risk_gate.config import DEFAULT_RISK_GATE_CONFIG, RiskGateConfig
from code.risk_gate.constants import (
    GATE_PASS,
    GATE_REJECT,
    GATE_WARNING,
    LEVEL_REJECT,
    LEVEL_WARNING,
    direction_from_expected_return,
)
from code.risk_gate.rules import RULES, RuleHit


# ---------------------------------------------------------------------------
# GateVerdict
# ---------------------------------------------------------------------------
@dataclass
class GateVerdict:
    decision: str                        # PASS / WARNING / REJECT
    risk_reasons: List[str] = field(default_factory=list)      # reason_code 列表（有序）
    rules_hit: List[Dict] = field(default_factory=list)        # 命中的规则（RuleHit dict）
    details: Dict = field(default_factory=dict)                # 附加信息（方向/样本/尾部等）
    version: str = "0.2"

    # -- 便捷属性 --
    @property
    def is_pass(self) -> bool:
        return self.decision == GATE_PASS

    @property
    def is_warning(self) -> bool:
        return self.decision == GATE_WARNING

    @property
    def is_reject(self) -> bool:
        return self.decision == GATE_REJECT

    @property
    def reason_codes(self) -> List[str]:
        return list(self.risk_reasons)

    def to_dict(self) -> Dict:
        return {
            "decision": self.decision,
            "risk_reasons": list(self.risk_reasons),
            "rules_hit": list(self.rules_hit),
            "details": dict(self.details),
            "version": self.version,
        }


# ---------------------------------------------------------------------------
# RiskGate
# ---------------------------------------------------------------------------
class RiskGate:
    """白盒风险闸门：candidate dict in → GateVerdict out。纯规则，无学习/拟合。"""

    def __init__(self, cfg: Optional[RiskGateConfig] = None):
        self.cfg = cfg or DEFAULT_RISK_GATE_CONFIG

    # ------------------------------------------------------------------
    def evaluate(self, candidate: Dict, verbose: bool = False) -> GateVerdict:
        """对一笔候选交易应用 Risk Gate。

        Args:
            candidate: as-of 候选交易 dict（见模块 docstring）。
            verbose: 是否在 details 中附加原始候选字段子集（审计用）。
        Returns:
            GateVerdict。
        """
        cand = dict(candidate)
        # 方向推导：优先用显式 direction，否则由 expected_return 符号推导
        if "direction" not in cand or cand.get("direction") in (None, ""):
            cand["direction"] = direction_from_expected_return(cand.get("expected_return"))

        hits: List[RuleHit] = []
        reasons: List[str] = []
        rejected = False

        for rule in RULES:
            fn = rule["fn"]
            try:
                hit = fn(cand, self.cfg)
            except Exception as exc:  # 规则异常不应阻塞整笔（保守记一条警告）
                hit = RuleHit(
                    rule_id=rule["rule_id"],
                    reason_code=rule["reason_code"],
                    level=LEVEL_WARNING,
                    message=f"规则执行异常: {exc}",
                )
            if hit is None:
                continue
            hits.append(hit)
            if hit.reason_code not in reasons:
                reasons.append(hit.reason_code)
            if hit.level == LEVEL_REJECT:
                rejected = True

        if rejected:
            decision = GATE_REJECT
        elif hits:
            decision = GATE_WARNING
        else:
            decision = GATE_PASS

        details: Dict = {
            "node": cand.get("node"),
            "target_date": cand.get("target_date"),
            "hour": cand.get("hour"),
            "direction": cand.get("direction"),
            "expected_return": cand.get("expected_return"),
            "confidence": cand.get("confidence"),
            "uncertainty": cand.get("uncertainty"),
            "hist_n": cand.get("hist_n"),
        }
        if verbose:
            details["_candidate"] = {k: v for k, v in cand.items() if k not in ("similar_tail_loss_cases",)}

        return GateVerdict(
            decision=decision,
            risk_reasons=reasons,
            rules_hit=[h.__dict__ if hasattr(h, "__dict__") else dict(h) for h in hits],
            details=details,
            version=self.cfg.version,
        )

    # ------------------------------------------------------------------
    def evaluate_row(self, **candidate_fields) -> GateVerdict:
        """关键字参数形式的便捷入口。"""
        return self.evaluate(candidate_fields)


# ---------------------------------------------------------------------------
# 批处理辅助（DataFrame → 逐行 verdict，供回测/校准脚本使用）
# ---------------------------------------------------------------------------
def evaluate_frame(df, gate: Optional[RiskGate] = None, verbose: bool = False):
    """对预测 DataFrame 逐行应用 Risk Gate，返回带 verdict 列的副本。

    输入列要求：node, target_date, hour, expected_return, confidence, uncertainty；
    可选风险特征列（如存在则使用）：hist_n, cvar99, rcvar99, vol_ratio, node_drift。
    direction 缺省由 expected_return 符号推导。
    """
    import pandas as pd

    g = gate or RiskGate()
    d = df.copy()
    n = len(d)
    decisions = [None] * n
    reasons = [None] * n
    rules = [None] * n
    for i in range(n):
        row = d.iloc[i]
        cand = {k: row[k] for k in d.columns}
        verdict = g.evaluate(cand, verbose=verbose)
        decisions[i] = verdict.decision
        reasons[i] = "|".join(verdict.risk_reasons)
        rules[i] = [h["rule_id"] for h in verdict.rules_hit]
    d["gate_decision"] = decisions
    d["gate_reason_code"] = reasons
    d["gate_rules_hit"] = rules
    return d
