# -*- coding: utf-8 -*-
"""
code/risk_gate/tests/test_evidence_gate.py

Evidence Time Gate 接入测试：
  - Post-decision Evidence 不进入 Risk Gate / Rule Engine（隔离到 post_decision）；
  - 误传 post-decision 证据进决策层 → RuntimeError（Leakage Guard）；
  - 只有 decision_eligible=True 的 Pre-decision Evidence 可被 gate 消费。

运行：python -m unittest code.risk_gate.tests.test_evidence_gate -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from agent.evidence.schema import (  # noqa: E402
    evidence_from_dict,
    new_uncertain_evidence,
)
from code.risk_gate.evidence_adapter import (  # noqa: E402
    assert_no_post_decision,
    evidence_direction_context,
    filter_eligible_evidence,
)

CUTOFF = "2026-07-09T10:00:00"  # D-1 日 10:00 PT（DAM Market Close）


def _ev(published_at, directional="UNCERTAIN", confidence=0.0):
    return evidence_from_dict({
        "event_type": "OTHER",
        "affected_nodes": ["SNLNDRO_1_N001"],
        "published_at": published_at,
        "available_at": published_at,   # Source Adapter 显式：published 即真正可用时刻
        "decision_cutoff": CUTOFF,
        "directional_effect": directional,
        "confidence": confidence,
    }).to_dict()


class TestEvidenceGate(unittest.TestCase):

    def test_pre_decision_evidence_eligible(self):
        ev = _ev("2026-07-09T09:00:00")  # cutoff 前 1 小时
        eligible, post = filter_eligible_evidence([ev], CUTOFF)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(len(post), 0)

    def test_post_decision_evidence_isolated(self):
        ev = _ev("2026-07-10T06:00:00")  # D+1 实际天气，晚于 cutoff
        eligible, post = filter_eligible_evidence([ev], CUTOFF)
        self.assertEqual(len(eligible), 0)
        self.assertEqual(len(post), 1)

    def test_assert_no_post_decision_raises(self):
        ev = _ev("2026-07-10T06:00:00")
        with self.assertRaises(RuntimeError):
            assert_no_post_decision([ev], CUTOFF)

    def test_assert_no_post_decision_passes_for_eligible(self):
        ev = _ev("2026-07-09T09:00:00")
        assert_no_post_decision([ev], CUTOFF)  # 不应抛错

    def test_missing_time_not_eligible(self):
        ev = new_uncertain_evidence(decision_cutoff=CUTOFF).to_dict()  # published_at 空
        eligible, post = filter_eligible_evidence([ev], CUTOFF)
        self.assertEqual(len(eligible), 0)

    def test_direction_context_counts(self):
        evs = [
            _ev("2026-07-09T09:00:00", directional="SUPPORT_POSITIVE"),
            _ev("2026-07-09T09:30:00", directional="UNCERTAIN"),
        ]
        eligible, _ = filter_eligible_evidence(evs, CUTOFF)
        ctx = evidence_direction_context(eligible, CUTOFF)
        self.assertEqual(ctx["SUPPORT_POSITIVE"], 1)
        self.assertEqual(ctx["UNCERTAIN"], 1)
        self.assertTrue(ctx["any_directional"])

    def test_uncertain_evidence_no_directional_signal(self):
        evs = [_ev("2026-07-09T09:00:00")]  # 默认 UNCERTAIN
        eligible, _ = filter_eligible_evidence(evs, CUTOFF)
        ctx = evidence_direction_context(eligible, CUTOFF)
        self.assertFalse(ctx["any_directional"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
