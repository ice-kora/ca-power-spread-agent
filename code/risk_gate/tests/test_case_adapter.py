# -*- coding: utf-8 -*-
"""
code/risk_gate/tests/test_case_adapter.py

Case Library 适配测试：
  - as-of 过滤：只有决策时点之前已发生结果的亏损 Case 才被检索；
  - 相似度：同 node + 同 direction + 小时窗口；
  - 只返回亏损 Case（PnL < 阈值），按 |PnL| 排序截断。

运行：python -m unittest code.risk_gate.tests.test_case_adapter -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from code.risk_gate.case_adapter import (  # noqa: E402
    candidate_with_cases,
    match_similar_tail_cases,
)

# 构造一个 fake case 列表（避开真实 cases.json 的 test 窗口日期限制）
_FAKE_CASES = [
    {"case_id": "C-OLD",   "node": "CONTROLX_1_N001", "hour": 10, "model_prediction": "BUY",
     "decision_date": "2026-06-28", "PnL": -1200.0},
    {"case_id": "C-OLD2",  "node": "CONTROLX_1_N001", "hour": 11, "model_prediction": "BUY",
     "decision_date": "2026-06-28", "PnL": -900.0},
    {"case_id": "C-SELL",  "node": "CONTROLX_1_N001", "hour": 10, "model_prediction": "SELL",
     "decision_date": "2026-06-28", "PnL": -1100.0},
    {"case_id": "C-FUTURE","node": "CONTROLX_1_N001", "hour": 10, "model_prediction": "BUY",
     "decision_date": "2026-07-30", "PnL": -2000.0},   # 未来，as-of 不可见
    {"case_id": "C-SMALL", "node": "CONTROLX_1_N001", "hour": 10, "model_prediction": "BUY",
     "decision_date": "2026-06-28", "PnL": -50.0},     # 小亏，不算尾
    {"case_id": "C-FARH",  "node": "CONTROLX_1_N001", "hour": 22, "model_prediction": "BUY",
     "decision_date": "2026-06-28", "PnL": -800.0},    # 小时太远
]


class TestCaseAdapter(unittest.TestCase):

    def test_matches_same_node_direction_hour(self):
        # 候选 target_date=2026-06-30 → 决策日 06-29 → 可用 case decision_date < 06-29
        cand = {"node": "CONTROLX_1_N001", "target_date": "2026-06-30", "hour": 10, "direction": "BUY"}
        hits = match_similar_tail_cases(cand, cases=_FAKE_CASES, tail_threshold=-300.0,
                                        hour_window=3, max_cases=5, as_of=True)
        ids = [h["case_id"] for h in hits]
        self.assertIn("C-OLD", ids)
        self.assertIn("C-OLD2", ids)       # 同方向、小时窗口内、亏损
        self.assertNotIn("C-SELL", ids)    # 方向不匹配（SELL vs BUY）
        self.assertNotIn("C-FUTURE", ids)  # 未来案例，as-of 不可见
        self.assertNotIn("C-SMALL", ids)   # 亏损太浅
        self.assertNotIn("C-FARH", ids)    # 小时超窗
        # 按 |PnL| 排序：最惨在前
        self.assertEqual(ids[0], "C-OLD")

    def test_as_of_filter_disabled_includes_future(self):
        cand = {"node": "CONTROLX_1_N001", "target_date": "2026-06-30", "hour": 10, "direction": "BUY"}
        hits = match_similar_tail_cases(cand, cases=_FAKE_CASES, tail_threshold=-300.0,
                                        hour_window=3, max_cases=5, as_of=False)
        ids = [h["case_id"] for h in hits]
        self.assertIn("C-FUTURE", ids)  # as_of=False 时不滤未来（非严格回测用途）

    def test_candidate_with_cases_attaches(self):
        cand = {"node": "CONTROLX_1_N001", "target_date": "2026-06-30", "hour": 10, "direction": "BUY"}
        out = candidate_with_cases(cand, cases=_FAKE_CASES, tail_threshold=-300.0,
                                   hour_window=3, max_cases=2, as_of=True)
        self.assertEqual(len(out["similar_tail_loss_cases"]), 2)

    def test_no_match_returns_empty(self):
        cand = {"node": "SNLNDRO_1_N001", "target_date": "2026-06-30", "hour": 10, "direction": "SELL"}
        hits = match_similar_tail_cases(cand, cases=_FAKE_CASES, tail_threshold=-300.0,
                                        hour_window=3, max_cases=5, as_of=True)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
