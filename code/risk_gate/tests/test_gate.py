# -*- coding: utf-8 -*-
"""
code/risk_gate/tests/test_gate.py

Risk Gate 单元测试（unittest，无第三方依赖）。

覆盖（任务要求）：
  - CONTROLX BUY → REJECT（BUY_ON_POSITIVE_DRIFT_NODE）
  - 安全 SELL → PASS
  - ELCA SELL → REJECT（SELL_ON_NEGATIVE_DRIFT_NODE）
  - 低样本 → REJECT（LOW_SAMPLE_SUPPORT）
  - 数据缺失 → REJECT（DATA_MISSING）
  - 尾部/波动/不确定/低置信/小边 → WARNING 级
  - 相似亏损 Case → WARNING（SIMILAR_TAIL_LOSS_CASE）

运行：python -m unittest code.risk_gate.tests.test_gate -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from code.risk_gate.config import DEFAULT_RISK_GATE_CONFIG  # noqa: E402
from code.risk_gate.gate import RiskGate  # noqa: E402
from code.risk_gate.constants import GATE_PASS, GATE_REJECT, GATE_WARNING  # noqa: E402


def _safe_sell(**over):
    """一个干净的安全 SELL 候选（不触发任何规则 → PASS）。"""
    cand = {
        "node": "SNLNDRO_1_N001",
        "target_date": "2026-08-01",
        "hour": 10,
        "expected_return": 8.0,
        "confidence": 0.6,
        "uncertainty": 0.5,
        "direction": "SELL",
        "hist_n": 800,
        "cvar99": -100.0,
        "rcvar99": -80.0,
        "vol_ratio": 1.2,
        "node_drift": 2.0,
        "similar_tail_loss_cases": [],
        "evidence_direction_context": {"SUPPORT_POSITIVE": 0, "SUPPORT_NEGATIVE": 0, "UNCERTAIN": 0},
    }
    cand.update(over)
    return cand


class TestRiskGateBasics(unittest.TestCase):

    def setUp(self):
        self.gate = RiskGate()

    def test_controlx_buy_reject(self):
        v = self.gate.evaluate(_safe_sell(
            node="CONTROLX_1_N001", expected_return=-30.0, direction="BUY"))
        self.assertEqual(v.decision, GATE_REJECT)
        self.assertIn("BUY_ON_POSITIVE_DRIFT_NODE", v.risk_reasons)

    def test_elca_sell_reject(self):
        v = self.gate.evaluate(_safe_sell(
            node="ELCAJNGT_7_N001", expected_return=6.0, direction="SELL"))
        self.assertEqual(v.decision, GATE_REJECT)
        self.assertIn("SELL_ON_NEGATIVE_DRIFT_NODE", v.risk_reasons)

    def test_safe_sell_pass(self):
        v = self.gate.evaluate(_safe_sell())
        self.assertEqual(v.decision, GATE_PASS, v.risk_reasons)
        self.assertEqual(v.risk_reasons, [])

    def test_safe_buy_pass(self):
        v = self.gate.evaluate(_safe_sell(
            expected_return=-8.0, direction="BUY"))
        self.assertEqual(v.decision, GATE_PASS, v.risk_reasons)

    def test_low_sample_reject(self):
        v = self.gate.evaluate(_safe_sell(node="ELCAJNGT_7_N001", hist_n=120, direction="BUY"))
        self.assertEqual(v.decision, GATE_REJECT)
        self.assertIn("LOW_SAMPLE_SUPPORT", v.risk_reasons)

    def test_data_missing_reject(self):
        v = self.gate.evaluate(_safe_sell(expected_return=None, confidence=None))
        self.assertEqual(v.decision, GATE_REJECT)
        self.assertIn("DATA_MISSING", v.risk_reasons)

    def test_direction_from_expected_return_sign(self):
        # direction 缺省时由 expected_return 符号推导
        cand = _safe_sell()
        del cand["direction"]
        v = self.gate.evaluate(cand)
        self.assertEqual(v.details["direction"], "SELL")


class TestRiskGateWarnings(unittest.TestCase):

    def setUp(self):
        self.gate = RiskGate()

    def test_extreme_tail_warning(self):
        v = self.gate.evaluate(_safe_sell(cvar99=-700.0))
        self.assertEqual(v.decision, GATE_WARNING)
        self.assertIn("EXTREME_TAIL_NODE", v.risk_reasons)

    def test_high_volatility_warning(self):
        v = self.gate.evaluate(_safe_sell(vol_ratio=4.0))
        self.assertEqual(v.decision, GATE_WARNING)
        self.assertIn("HIGH_VOLATILITY", v.risk_reasons)

    def test_model_unstable_warning(self):
        v = self.gate.evaluate(_safe_sell(uncertainty=0.99))
        self.assertEqual(v.decision, GATE_WARNING)
        self.assertIn("MODEL_UNSTABLE", v.risk_reasons)

    def test_low_confidence_warning(self):
        v = self.gate.evaluate(_safe_sell(confidence=0.10))
        self.assertEqual(v.decision, GATE_WARNING)
        self.assertIn("LOW_CONFIDENCE", v.risk_reasons)

    def test_expected_return_too_small_warning(self):
        v = self.gate.evaluate(_safe_sell(expected_return=1.0))
        self.assertEqual(v.decision, GATE_WARNING)
        self.assertIn("EXPECTED_RETURN_TOO_SMALL", v.risk_reasons)

    def test_similar_tail_loss_case_warning(self):
        cand = _safe_sell(similar_tail_loss_cases=[{
            "case_id": "CASE-0001", "node": "SNLNDRO_1_N001", "hour": 11,
            "decision_date": "2026-07-30", "model_prediction": "SELL", "PnL": -350.0,
        }])
        v = self.gate.evaluate(cand)
        self.assertEqual(v.decision, GATE_WARNING)
        self.assertIn("SIMILAR_TAIL_LOSS_CASE", v.risk_reasons)

    def test_warning_reasons_accumulate(self):
        v = self.gate.evaluate(_safe_sell(confidence=0.10, uncertainty=0.99))
        self.assertEqual(v.decision, GATE_WARNING)
        self.assertIn("LOW_CONFIDENCE", v.risk_reasons)
        self.assertIn("MODEL_UNSTABLE", v.risk_reasons)

    def test_reject_sticky_above_warning(self):
        # REJECT 优先于 WARNING：同时命中 CONTROLX BUY 与低置信 → REJECT
        v = self.gate.evaluate(_safe_sell(
            node="CONTROLX_1_N001", expected_return=-30.0, direction="BUY", confidence=0.10))
        self.assertEqual(v.decision, GATE_REJECT)
        self.assertIn("BUY_ON_POSITIVE_DRIFT_NODE", v.risk_reasons)
        self.assertIn("LOW_CONFIDENCE", v.risk_reasons)


if __name__ == "__main__":
    unittest.main(verbosity=2)
