# -*- coding: utf-8 -*-
"""
code/decision/tests/test_rule_engine.py

White-box Rule Engine 单元测试（unittest）。

覆盖（任务要求）：
  - RiskGate == REJECT → NO_TRADE
  - |expected_return| < min_spread → NO_TRADE（EXPECTED_RETURN_TOO_SMALL）
  - confidence < min_confidence → NO_TRADE（LOW_CONFIDENCE）
  - expected_return > 0 → SELL_DA；< 0 → BUY_DA
  - 证据方向冲突 → NO_TRADE（EVIDENCE_CONFLICT）
  - WARNING 升级（reject_on_warning=True）→ NO_TRADE
  - 输出含 reason / 版本号；Post-decision 证据不进入决策层

运行：python -m unittest code.decision.tests.test_rule_engine -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from agent.evidence.schema import evidence_from_dict  # noqa: E402
from code.decision.rule_engine import (  # noqa: E402
    DEFAULT_RULE_ENGINE_CONFIG,
    RuleEngine,
    RuleEngineConfig,
    describe_rules,
)
from code.risk_gate.gate import GateVerdict, RiskGate  # noqa: E402

CUTOFF = "2026-07-09T10:00:00"


def _pred(**over):
    pred = {
        "node": "SNLNDRO_1_N001",
        "target_date": "2026-07-10",
        "hour": 10,
        "expected_return": 8.0,
        "confidence": 0.6,
        "uncertainty": 0.5,
        "prob_positive": 0.75,
        "prob_negative": 0.25,
    }
    pred.update(over)
    return pred


class TestRuleEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RuleEngine()

    # ---- R-A：Risk Gate REJECT → NO_TRADE ----
    def test_gate_reject_no_trade(self):
        verdict = GateVerdict(decision="REJECT", risk_reasons=["BUY_ON_POSITIVE_DRIFT_NODE"])
        d = self.engine.evaluate(_pred(expected_return=-30.0), gate_verdict=verdict)
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("RISK_GATE_REJECTED", d.reasons)
        self.assertEqual(d.risk_reasons, ["BUY_ON_POSITIVE_DRIFT_NODE"])

    # ---- R-B：数据缺失 → NO_TRADE ----
    def test_missing_expected_return_no_trade(self):
        # 用 PASS verdict 隔离 R-B（否则 RiskGate 会先以 DATA_MISSING REJECT）
        verdict = GateVerdict(decision="PASS", risk_reasons=[])
        d = self.engine.evaluate(_pred(expected_return=None), gate_verdict=verdict)
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("DATA_MISSING", d.reasons)

    def test_missing_expected_return_rejected_by_gate_first(self):
        # 端到端：gate_verdict 缺省时，RiskGate 先以 DATA_MISSING REJECT
        d = self.engine.evaluate(_pred(expected_return=None))
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertEqual(d.risk_verdict, "REJECT")
        self.assertIn("DATA_MISSING", d.risk_reasons)

    # ---- R-C：|er| < min_spread → NO_TRADE ----
    def test_small_spread_no_trade(self):
        d = self.engine.evaluate(_pred(expected_return=2.0))
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("EXPECTED_RETURN_TOO_SMALL", d.reasons)

    # ---- R-D：confidence < min_confidence → NO_TRADE ----
    def test_low_confidence_no_trade(self):
        d = self.engine.evaluate(_pred(confidence=0.05))
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("LOW_CONFIDENCE", d.reasons)

    # ---- R-G / R-H：方向判定 ----
    def test_positive_expected_return_sell_da(self):
        d = self.engine.evaluate(_pred(expected_return=8.0))
        self.assertEqual(d.decision, "SELL_DA")
        self.assertEqual(d.direction_sign, +1)
        self.assertTrue(d.is_trade)
        self.assertIn("EXPECTED_RETURN_POSITIVE", d.reasons)
        self.assertIn("R-G", d.rules_hit)

    def test_negative_expected_return_buy_da(self):
        d = self.engine.evaluate(_pred(expected_return=-8.0))
        self.assertEqual(d.decision, "BUY_DA")
        self.assertEqual(d.direction_sign, -1)
        self.assertTrue(d.is_trade)
        self.assertIn("R-H", d.rules_hit)

    # ---- R-I：expected_return == 0 → NO_TRADE（需 min_spread=0 才能走到 R-I）----
    def test_zero_expected_return_no_trade(self):
        engine = RuleEngine(RuleEngineConfig(min_spread=0.0))
        verdict = GateVerdict(decision="PASS", risk_reasons=[])
        d = engine.evaluate(_pred(expected_return=0.0), gate_verdict=verdict)
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("NO_CLEAR_DIRECTION", d.reasons)

    # ---- 版本号 ----
    def test_version_present(self):
        d = self.engine.evaluate(_pred())
        self.assertEqual(d.version, DEFAULT_RULE_ENGINE_CONFIG.version)

    # ---- 描述函数 ----
    def test_describe_rules(self):
        rules = describe_rules()
        self.assertTrue(any(r["rule_id"] == "R-G" and r["decision"] == "SELL_DA" for r in rules))


class TestRuleEngineEvidence(unittest.TestCase):

    def setUp(self):
        self.engine = RuleEngine()

    def _ev(self, published_at, directional="UNCERTAIN", confidence=0.0):
        return evidence_from_dict({
            "event_type": "OTHER",
            "affected_nodes": ["SNLNDRO_1_N001"],
            "published_at": published_at,
            "available_at": published_at,   # Source Adapter 显式：published 即真正可用时刻
            "decision_cutoff": CUTOFF,
            "directional_effect": directional,
            "confidence": confidence,
        }).to_dict()

    def test_eligible_evidence_counts_in_decision(self):
        evs = [self._ev("2026-07-09T09:00:00")]
        d = self.engine.evaluate(_pred(), evidences=evs, decision_cutoff=CUTOFF)
        self.assertEqual(d.decision, "SELL_DA")
        self.assertEqual(d.evidence_used["n"], 1)

    def test_post_decision_evidence_blocked(self):
        evs = [self._ev("2026-07-10T06:00:00")]  # 决策后发布 → 防御断言抛错
        with self.assertRaises(RuntimeError):
            self.engine.evaluate(_pred(), evidences=evs, decision_cutoff=CUTOFF)

    def test_evidence_conflict_no_trade(self):
        # 未来真实源接入后：支持 Return<0 的证据与 SELL 冲突 → NO_TRADE
        evs = [self._ev("2026-07-09T09:00:00", directional="SUPPORT_NEGATIVE")]
        d = self.engine.evaluate(_pred(), evidences=evs, decision_cutoff=CUTOFF)
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("EVIDENCE_CONFLICT", d.reasons)


class TestRuleEngineWarningEscalation(unittest.TestCase):

    def setUp(self):
        self.engine = RuleEngine()

    def test_warning_escalated_to_no_trade_when_configured(self):
        # RiskGate 给出 WARNING（如尾部深），reject_on_warning=True → NO_TRADE
        engine = RuleEngine(RuleEngineConfig(reject_on_warning=True))
        verdict = GateVerdict(decision="WARNING", risk_reasons=["EXTREME_TAIL_NODE"])
        d = engine.evaluate(_pred(), gate_verdict=verdict)
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("RISK_GATE_WARNING_ESCALATED", d.reasons)

    def test_warning_not_blocked_by_default(self):
        # 默认 reject_on_warning=False：WARNING 只标记，仍按方向交易
        verdict = GateVerdict(decision="WARNING", risk_reasons=["EXTREME_TAIL_NODE"])
        d = self.engine.evaluate(_pred(), gate_verdict=verdict)
        self.assertEqual(d.decision, "SELL_DA")
        self.assertEqual(d.risk_reasons, ["EXTREME_TAIL_NODE"])

    def test_gate_verdict_as_dict_supported(self):
        # gate_verdict 也可用 dict 形式传入（兼容 JSON 流水线）
        d = self.engine.evaluate(
            _pred(), gate_verdict={"decision": "REJECT", "risk_reasons": ["LOW_SAMPLE_SUPPORT"]})
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("RISK_GATE_REJECTED", d.reasons)
        self.assertEqual(d.risk_reasons, ["LOW_SAMPLE_SUPPORT"])

    def test_end_to_end_gate_then_engine(self):
        # 端到端：RiskGate 判定 CONTROLX BUY → RuleEngine 必须 NO_TRADE
        gate = RiskGate()
        v = gate.evaluate({
            "node": "CONTROLX_1_N001", "target_date": "2026-07-10", "hour": 10,
            "expected_return": -30.0, "confidence": 0.6, "uncertainty": 0.5,
            "hist_n": 900, "direction": "BUY",
        })
        d = RuleEngine().evaluate(_pred(expected_return=-30.0), gate_verdict=v)
        self.assertEqual(v.decision, "REJECT")
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertIn("RISK_GATE_REJECTED", d.reasons)


class TestDecisionFeatureEligibility(unittest.TestCase):
    """Agent B P0-2：Decision 携带特征展示，强制"展示 == Time Gate 判定"。"""

    CUTOFF = "2026-07-09T17:00:00"   # 2026-07-09 10:00 PT → UTC（PDT）

    def setUp(self):
        self.engine = RuleEngine()

    def _feat(self, eligible=True, bound=None, basis="STRUCTURAL_LAG"):
        bound_val = "2026-07-09T00:00:00" if bound is None else bound
        return {
            "feature": "spread_lag1",
            "available_at": bound_val,
            "latest_possible_available_at": bound_val,
            "availability_basis": basis,
            "decision_cutoff": self.CUTOFF,
            "decision_eligible": eligible,
        }

    def test_valid_features_used_stored_and_consistent(self):
        feats = [self._feat(eligible=True, bound="2026-07-09T00:00:00")]
        d = self.engine.evaluate(_pred(), features_used=feats, decision_cutoff=self.CUTOFF)
        self.assertEqual(d.decision, "SELL_DA")
        self.assertEqual(d.features_used, feats)
        self.assertTrue(d.feature_eligibility_consistent)
        self.assertEqual(d.feature_eligibility_violations(self.CUTOFF), [])
        d2 = d.to_dict()
        self.assertIn("features_used", d2)
        self.assertIn("feature_eligibility_consistent", d2)
        self.assertTrue(d2["feature_eligibility_consistent"])

    def test_late_bound_with_eligible_true_raises(self):
        # 铁律反例：displayed available_at 上界(18:00) > cutoff(17:00) 但声明 eligible=True
        feats = [self._feat(eligible=True, bound="2026-07-09T18:00:00")]
        with self.assertRaises(RuntimeError):
            self.engine.evaluate(_pred(), features_used=feats, decision_cutoff=self.CUTOFF)

    def test_ineligible_feature_allowed(self):
        # eligible=False 不产生展示矛盾（不可用就是不可用）
        feats = [self._feat(eligible=False, bound="2026-07-09T18:00:00")]
        d = self.engine.evaluate(_pred(), features_used=feats, decision_cutoff=self.CUTOFF)
        self.assertEqual(d.decision, "SELL_DA")
        self.assertTrue(d.feature_eligibility_consistent)

    def test_static_feature_allowed(self):
        feats = [self._feat(eligible=True, bound="", basis="STATIC")]
        d = self.engine.evaluate(_pred(), features_used=feats, decision_cutoff=self.CUTOFF)
        self.assertTrue(d.feature_eligibility_consistent)

    def test_no_features_used_default_consistent(self):
        d = self.engine.evaluate(_pred())
        self.assertEqual(d.features_used, [])
        self.assertTrue(d.feature_eligibility_consistent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
