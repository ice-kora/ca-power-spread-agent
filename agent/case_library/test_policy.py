# -*- coding: utf-8 -*-
"""
agent/case_library/test_policy.py

CaseGenerationPolicy 单元测试（unittest，无第三方依赖）。

覆盖：
  Test 1  五类自动规则是否分别命中
  Test 2  硬约束 is_retrievable（case_available_at <= decision_time，严格防穿越）
  Test 3  时间字段生成（case_created_at / case_available_at / review_completed_at）
  Test 4  HUMAN_OVERRIDE 需 human_action（不伪造人工）
  Test 5  RISK_GATE_FAILURE 只对 PASS（不含 REJECT）的 Tail Loss 命中

运行：python agent/case_library/test_policy.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.case_library.case import Case  # noqa: E402
from agent.case_library.policy import (  # noqa: E402
    CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION,
    CASE_TYPE_HUMAN_OVERRIDE,
    CASE_TYPE_LARGE_PROFIT,
    CASE_TYPE_RISK_GATE_FAILURE,
    CASE_TYPE_TAIL_LOSS,
    CaseGenerationPolicy,
    DecisionRecord,
    decision_time_for,
    generate_cases,
    is_retrievable,
    settlement_available_at,
)

POL = CaseGenerationPolicy()


def _rec(**kw) -> DecisionRecord:
    base = dict(
        node="CONTROLX_1_N001",
        target_date="2026-07-09",
        hour=2,
        suggested_action="BUY_DA",
        expected_return=-141.3,
        confidence=0.905,
        prob_positive=0.095,
        prob_negative=0.905,
        actual_return=2216.3,
        actual_direction=1,
        risk_gate_decision="PASS",
    )
    base.update(kw)
    return DecisionRecord(**base)


class TestPolicyRules(unittest.TestCase):
    """Test 1: 五类自动规则分别命中"""

    def test_tail_loss(self):
        rec = _rec(actual_return=2216.3)  # BUY → PnL = -2216.3 <= -300
        self.assertIn(CASE_TYPE_TAIL_LOSS, POL.classify(rec))

    def test_large_profit(self):
        rec = _rec(suggested_action="SELL_DA", expected_return=180.3,
                   prob_positive=0.82, prob_negative=0.18, actual_return=2216.3, actual_direction=1)
        # SELL → PnL = +2216.3 >= 300
        self.assertIn(CASE_TYPE_LARGE_PROFIT, POL.classify(rec))

    def test_high_signal_wrong_direction(self):
        rec = _rec(prob_positive=0.095, prob_negative=0.905, actual_return=2216.3, actual_direction=1)
        self.assertIn(CASE_TYPE_HIGH_SIGNAL_WRONG_DIRECTION, POL.classify(rec))

    def test_risk_gate_failure(self):
        rec = _rec(actual_return=2216.3, risk_gate_decision="PASS")
        self.assertIn(CASE_TYPE_RISK_GATE_FAILURE, POL.classify(rec))

    def test_human_override(self):
        rec = _rec(human_action="SELL_DA")  # != suggested BUY_DA
        self.assertIn(CASE_TYPE_HUMAN_OVERRIDE, POL.classify(rec))

    def test_human_override_no_human_does_not_fire(self):
        rec = _rec(human_action="")  # 无人工记录 → 不伪造
        self.assertNotIn(CASE_TYPE_HUMAN_OVERRIDE, POL.classify(rec))

    def test_gate_reject_not_failure(self):
        rec = _rec(actual_return=2216.3, risk_gate_decision="REJECT")
        self.assertNotIn(CASE_TYPE_RISK_GATE_FAILURE, POL.classify(rec))


class TestHardConstraint(unittest.TestCase):
    """Test 2: 硬约束 is_retrievable（严格防 Case 穿越）"""

    def test_available_at_before_decision_retrievable(self):
        # case(target 07-09) available_at = 07-10 06:00
        rec = _rec()
        c = POL and generate_cases([rec], POL, case_id_prefix="T-")[0]
        # 决策时点 07-10 10:00（= decision_time_for('07-11')）→ 可检索
        dt = decision_time_for("2026-07-11")
        self.assertTrue(is_retrievable(c, dt))

    def test_available_at_after_decision_not_retrievable(self):
        rec = _rec()
        c = generate_cases([rec], POL, case_id_prefix="T-")[0]
        # 决策时点 07-08 10:00（= decision_time_for('07-09')）→ 目标日尚未结算 → 禁检索
        dt = decision_time_for("2026-07-09")
        self.assertFalse(is_retrievable(c, dt))

    def test_missing_available_at_never_retrievable(self):
        c = Case(case_id="X", decision_date="2026-07-08", node="N", hour=1,
                 model_prediction="BUY", expected_return=-10.0, confidence=0.5,
                 case_available_at="")
        self.assertFalse(is_retrievable(c, decision_time_for("2026-07-11")))

    def test_retrieval_guard_on_generated_json(self):
        # 由生成器产出的 Case 必须带 case_available_at，且硬约束成立
        rec = _rec()
        cases = generate_cases([rec], POL, case_id_prefix="T-")
        for c in cases:
            self.assertTrue(c.case_available_at.startswith("2026-07-10T"))
            self.assertEqual(c.case_created_at, c.case_available_at)
            self.assertEqual(c.review_completed_at, "")


class TestTimestamps(unittest.TestCase):
    """Test 3: 时间字段生成"""

    def test_settlement_available_at(self):
        self.assertEqual(settlement_available_at("2026-07-09"), "2026-07-10T06:00:00")

    def test_decision_time_for(self):
        self.assertEqual(decision_time_for("2026-07-09"), "2026-07-08T10:00:00")

    def test_available_at_strictly_before_next_decision(self):
        # 目标日结算后次日 06:00 <= 次日决策 cutoff 10:00 → 恒可检索
        td = "2026-07-09"
        self.assertLess(settlement_available_at(td), decision_time_for("2026-07-11"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
