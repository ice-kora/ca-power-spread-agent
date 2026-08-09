# -*- coding: utf-8 -*-
"""
agent/evidence/test_time_gate.py

Evidence Time Gate 单元测试（unittest，无第三方依赖）。

覆盖（用户规范 §12）：
  Test 1  published 09:00 <= cutoff 10:00 -> decision_eligible = TRUE
  Test 2  published 11:00 >  cutoff 10:00 -> decision_eligible = FALSE（不得进入 Risk Gate）
  Test 3  D+1 actual weather（即使高度相关）-> decision_eligible = FALSE
  Test 4  历史回测中 published_at > historical decision_cutoff -> 触发 Leakage Guard（隔离）
  Test 5  feature available_at <= decision_cutoff -> 允许进入生产特征
  Test 6  feature available_at >  decision_cutoff -> 禁止进入生产（business_contract §4 铁律）
  Test 7  边界：available_at == decision_cutoff -> 允许（规则只禁严格大于）

运行：python agent/evidence/test_time_gate.py   （或 python -m unittest 发现）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.evidence.schema import new_uncertain_evidence, parse_timestamp  # noqa: E402
from agent.evidence.time_gate import (  # noqa: E402
    is_decision_eligible,
    is_available_before_cutoff,
    split_eligible,
    assert_no_post_decision,
)

CUTOFF_10 = "2025-07-09T10:00:00"  # D-1 日 10:00 PT（官方 BPM bid close）


class TestTimeGate(unittest.TestCase):
    """Test 1: published_at(09:00) <= decision_cutoff(10:00) -> TRUE"""

    def test_1_eligible_before_cutoff(self):
        ev = new_uncertain_evidence(
            published_at="2025-07-09T09:00:00", decision_cutoff=CUTOFF_10)
        self.assertTrue(ev.decision_eligible)
        self.assertTrue(is_decision_eligible(ev))
        eligible, post = split_eligible([ev], CUTOFF_10)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(len(post), 0)

    """Test 2: published_at(11:00) > decision_cutoff(10:00) -> FALSE，不得进入 Risk Gate"""

    def test_2_not_eligible_after_cutoff(self):
        ev = new_uncertain_evidence(
            published_at="2025-07-09T11:00:00", decision_cutoff=CUTOFF_10)
        self.assertFalse(ev.decision_eligible)
        self.assertFalse(is_decision_eligible(ev))
        eligible, post = split_eligible([ev], CUTOFF_10)
        self.assertEqual(len(eligible), 0)
        self.assertEqual(len(post), 1)  # 隔离到 post_decision

    """Test 3: D+1 actual weather 即使与结果高度相关，也必须在决策前发布才算可用"""

    def test_3_dplus1_actual_weather_not_eligible(self):
        # D+1 当天实际天气：最早 D 日（2025-07-10）才可得，晚于 cutoff
        ev = new_uncertain_evidence(
            event_type="EXTREME_WEATHER",
            published_at="2025-07-10T06:00:00",   # 次日实际观测发布时间
            decision_cutoff=CUTOFF_10)
        self.assertFalse(ev.decision_eligible)
        # 即便 summary 声称"与当日负电价高度相关"，也不得进入决策
        eligible, post = split_eligible([ev], CUTOFF_10)
        self.assertEqual(len(eligible), 0)
        self.assertEqual(len(post), 1)

    """Test 4: 历史回测中 published_at > historical decision_cutoff -> Leakage Guard 触发"""

    def test_4_historical_backtest_leakage_guard(self):
        ev = new_uncertain_evidence(
            published_at="2025-07-10T12:00:00",  # 决策后才发布（如事后事故通告）
            decision_cutoff=CUTOFF_10)
        # 单独判断：不可用
        self.assertFalse(ev.decision_eligible)
        # 防御断言：把 post-decision 误传进决策层必须抛错（Leakage Guard）
        with self.assertRaises(RuntimeError):
            assert_no_post_decision([ev], CUTOFF_10)

    # -- 特征可用性门槛（business_contract §4：available_at > decision_cutoff 禁止）---

    """Test 5: feature available_at(09:00) <= decision_cutoff(10:00) -> 允许进入生产特征"""

    def test_5_feature_available_before_cutoff(self):
        self.assertTrue(is_available_before_cutoff("2025-07-09T09:00:00", CUTOFF_10))
        # evidence 口径等价：published_at <= cutoff -> eligible
        ev = new_uncertain_evidence(
            published_at="2025-07-09T09:00:00", decision_cutoff=CUTOFF_10)
        eligible, post = split_eligible([ev], CUTOFF_10)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(len(post), 0)

    """Test 6: feature available_at(11:00) > decision_cutoff(10:00) -> 禁止进入生产"""

    def test_6_feature_available_after_cutoff_forbidden(self):
        # 特征/证据可用时点晚于 bid cutoff -> 不得进训练/推理/决策
        self.assertFalse(is_available_before_cutoff("2025-07-09T11:00:00", CUTOFF_10))
        ev = new_uncertain_evidence(
            published_at="2025-07-09T11:00:00", decision_cutoff=CUTOFF_10)
        eligible, post = split_eligible([ev], CUTOFF_10)
        self.assertEqual(len(eligible), 0)
        self.assertEqual(len(post), 1)  # 隔离到 post_decision
        with self.assertRaises(RuntimeError):  # 误传进生产决策层 -> Leakage Guard 拦截
            assert_no_post_decision([ev], CUTOFF_10)

    """Test 7: 边界 —— available_at == decision_cutoff -> 允许（规则只禁严格大于）"""

    def test_7_feature_available_exactly_at_cutoff(self):
        self.assertTrue(is_available_before_cutoff("2025-07-09T10:00:00", CUTOFF_10))
        ev = new_uncertain_evidence(
            published_at="2025-07-09T10:00:00", decision_cutoff=CUTOFF_10)
        eligible, post = split_eligible([ev], CUTOFF_10)
        self.assertEqual(len(eligible), 1)  # == cutoff 属于 Pre-decision
        self.assertEqual(len(post), 0)

    def test_feature_available_missing_or_unparseable(self):
        # 缺失 / 不可解析 -> False（宁保守不穿越）
        self.assertFalse(is_available_before_cutoff("", CUTOFF_10))
        self.assertFalse(is_available_before_cutoff(None, CUTOFF_10))
        self.assertFalse(is_available_before_cutoff("not-a-timestamp", CUTOFF_10))
        # cutoff 缺失同样不可用
        self.assertFalse(is_available_before_cutoff("2025-07-09T09:00:00", ""))

    # -- 附加：时间缺失 / 不可解析（NOT_BACKTEST_SAFE 语义）-----------------
    def test_missing_time_is_not_eligible(self):
        ev = new_uncertain_evidence(decision_cutoff=CUTOFF_10)  # published_at 空
        self.assertFalse(ev.decision_eligible)

    def test_unparseable_time_is_not_eligible(self):
        ev = new_uncertain_evidence(
            published_at="not-a-timestamp", decision_cutoff=CUTOFF_10)
        self.assertFalse(ev.decision_eligible)
        self.assertIsNone(parse_timestamp("not-a-timestamp"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
