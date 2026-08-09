# -*- coding: utf-8 -*-
"""
code/risk_gate/tests/test_overfit_check.py

过拟合检验逻辑测试：
  - guardrail_overfit_table 能区分 NOT_OVERFIT / OVERFIT_RISK / NEED_STRUCTURAL_EVIDENCE；
  - build_candidate_frame 正确派生 direction 与 signed pnl。

用合成数据（不依赖真实预测文件），验证的是"判定逻辑本身"是否正确。

运行：python -m unittest code.risk_gate.tests.test_overfit_check -v
"""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from code.risk_gate.calibrate import (  # noqa: E402
    build_candidate_frame,
    guardrail_overfit_table,
)
from code.risk_gate.config import EmpiricalGuardrail, RiskGateConfig  # noqa: E402

N1 = EmpiricalGuardrail(node="N1", direction="BUY", reason_code="BUY_ON_POSITIVE_DRIFT_NODE")
N2 = EmpiricalGuardrail(node="N2", direction="SELL", reason_code="SELL_ON_NEGATIVE_DRIFT_NODE")
N3 = EmpiricalGuardrail(node="N3", direction="BUY", reason_code="BUY_ON_POSITIVE_DRIFT_NODE")


def _frame(node_pnl_pairs):
    """构造候选帧：[(node, direction, pnl), ...]"""
    rows = [{"node": n, "direction": d, "pnl": p, "expected_return": 1.0,
             "confidence": 0.6, "hist_n": 500, "target_date": "2026-01-01", "hour": 1}
            for n, d, p in node_pnl_pairs]
    return pd.DataFrame(rows)


class TestOverfitCheck(unittest.TestCase):

    def test_not_overfit_when_negative_ev_both_windows(self):
        # N1 BUY：val 与 test 被拒集都负 EV → NOT_OVERFIT
        val = _frame([("N1", "BUY", -5.0), ("N1", "BUY", -3.0), ("N1", "SELL", 2.0)])
        test = _frame([("N1", "BUY", -50.0), ("N1", "SELL", 2.0)])
        cfg = RiskGateConfig(empirical_guardrails=(N1,))
        table = guardrail_overfit_table(val, test, cfg)
        row = table[0]
        self.assertEqual(row["overfit_verdict"], "NOT_OVERFIT")
        self.assertLess(row["val"]["rejected"]["mean_pnl"], 0)
        self.assertLess(row["test"]["rejected"]["mean_pnl"], 0)

    def test_overfit_risk_when_positive_ev_in_val(self):
        # N2 SELL：val 被拒集正 EV，test 负 EV → OVERFIT_RISK
        val = _frame([("N2", "SELL", 5.0), ("N2", "SELL", 3.0)])
        test = _frame([("N2", "SELL", -40.0)])
        cfg = RiskGateConfig(empirical_guardrails=(N2,))
        row = guardrail_overfit_table(val, test, cfg)[0]
        self.assertEqual(row["overfit_verdict"], "OVERFIT_RISK")

    def test_need_structural_evidence_when_no_samples(self):
        # N3 BUY：两窗口都没有命中样本 → NEED_STRUCTURAL_EVIDENCE
        val = _frame([("OTHER", "SELL", 2.0)])
        test = _frame([("OTHER", "SELL", 2.0)])
        cfg = RiskGateConfig(empirical_guardrails=(N3,))
        row = guardrail_overfit_table(val, test, cfg)[0]
        self.assertEqual(row["overfit_verdict"], "NEED_STRUCTURAL_EVIDENCE")

    def test_build_candidate_frame_direction_and_pnl(self):
        pred = pd.DataFrame({
            "node": ["N1", "N1"],
            "target_date": ["2026-01-01", "2026-01-01"],
            "hour": [1, 2],
            "expected_return": [8.0, -8.0],
            "actual_return": [10.0, -6.0],
        })
        out = build_candidate_frame(pred)
        self.assertEqual(out.iloc[0]["direction"], "SELL")
        self.assertEqual(out.iloc[0]["pnl"], 10.0)    # SELL: +actual_return
        self.assertEqual(out.iloc[1]["direction"], "BUY")
        self.assertEqual(out.iloc[1]["pnl"], 6.0)     # BUY: -actual_return


if __name__ == "__main__":
    unittest.main(verbosity=2)
