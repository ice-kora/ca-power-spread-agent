# -*- coding: utf-8 -*-
"""
agent/evidence/test_provenance.py

Evidence Provenance 单元测试（unittest）：source_type / is_mock / raw_source_id /
target_time / time_eligible / backtest_eligible / production_eligible /
feature_value / market_rule_version。

运行：python agent/evidence/test_provenance.py   （或 python -m unittest 发现）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.evidence.schema import (  # noqa: E402
    SOURCE_TYPES,
    Evidence,
    evidence_from_dict,
    new_uncertain_evidence,
    validate_evidence,
)
from code.market_rules import (  # noqa: E402
    CURRENT_MARKET_RULE_VERSION,
    MARKET_RULE_VERSIONS,
    normalize_market_rule_version,
)

CUTOFF = "2025-07-09T10:00:00"


def _ev(**over):
    base = {
        "published_at": "2025-07-09T09:00:00",
        "available_at": "2025-07-09T09:00:00",   # Source Adapter 显式：published 即真正可用时刻
        "retrieved_at": "2025-07-09T09:30:00",
        "decision_cutoff": CUTOFF,
    }
    base.update(over)
    return evidence_from_dict(base)


class TestEvidenceProvenance(unittest.TestCase):

    # ---- source_type ------------------------------------------------------
    def test_source_type_auto_inferred(self):
        # WEATHER_FORECAST event_type → source_type=WEATHER
        ev = _ev(event_type="WEATHER_FORECAST", source="Open-Meteo GFS")
        self.assertEqual(ev.source_type, "WEATHER")

    def test_source_type_explicit_wins(self):
        ev = _ev(event_type="OTHER", source_type="EVENT")
        self.assertEqual(ev.source_type, "EVENT")

    def test_source_type_illegal_replaced(self):
        ev = _ev(event_type="OTHER", source_type="HACK").normalize()
        self.assertIn(ev.source_type, SOURCE_TYPES)
        errs = validate_evidence(ev.to_dict())
        self.assertFalse(any("source_type" in e for e in errs))  # normalize 已规约

    # ---- is_mock 硬隔离（R7 语义对齐 schemas）-----------------------------
    def test_mock_never_eligible(self):
        ev = _ev(is_mock=True)   # published_at < cutoff，时间合格
        self.assertTrue(ev.time_eligible)
        self.assertFalse(ev.backtest_eligible)
        self.assertFalse(ev.production_eligible)
        self.assertFalse(ev.decision_eligible)

    def test_real_before_cutoff_eligible(self):
        ev = _ev()
        self.assertTrue(ev.time_eligible)
        self.assertTrue(ev.backtest_eligible)
        self.assertTrue(ev.production_eligible)
        self.assertTrue(ev.decision_eligible)

    def test_real_after_cutoff_ineligible(self):
        ev = _ev(published_at="2025-07-09T11:00:00", available_at="2025-07-09T11:00:00")
        self.assertFalse(ev.time_eligible)
        self.assertFalse(ev.backtest_eligible)
        self.assertFalse(ev.production_eligible)
        self.assertFalse(ev.decision_eligible)

    def test_missing_retrieved_blocks_production_only(self):
        ev = _ev(retrieved_at="")
        self.assertTrue(ev.time_eligible)
        self.assertTrue(ev.backtest_eligible)      # 回测只需可核实的 published_at
        self.assertFalse(ev.production_eligible)   # 生产需 published+retrieved 齐备

    # ---- feature_value / raw_source_id / target_time ----------------------
    def test_feature_value_coerced(self):
        ev = _ev(feature_value=float("nan"))
        self.assertIsNone(ev.feature_value)
        ev2 = _ev(feature_value=27.5)
        self.assertEqual(ev2.feature_value, 27.5)
        ev3 = _ev(feature_value="bad")
        self.assertIsNone(ev3.feature_value)

    def test_provenance_trace_fields_present(self):
        ev = _ev(raw_source_id="oasis-row-42", target_time="2025-07-10T21:00:00")
        d = ev.to_dict()
        for k in ("source", "source_type", "is_mock", "raw_source_id", "target_time",
                  "published_at", "retrieved_at", "time_eligible",
                  "backtest_eligible", "production_eligible", "feature_value"):
            self.assertIn(k, d, f"Evidence provenance 字段 {k} 缺失")

    # ---- market_rule_version ----------------------------------------------
    def test_market_rule_default(self):
        ev = _ev()
        self.assertEqual(ev.market_rule_version, CURRENT_MARKET_RULE_VERSION)
        self.assertIn(ev.market_rule_version, MARKET_RULE_VERSIONS)

    def test_market_rule_explicit_and_normalize(self):
        ev = _ev(market_rule_version="POST_DAME_EDAM_2026")
        self.assertEqual(ev.market_rule_version, "POST_DAME_EDAM_2026")
        ev2 = _ev(market_rule_version="FUTURE_V99").normalize()
        self.assertEqual(ev2.market_rule_version, CURRENT_MARKET_RULE_VERSION)
        self.assertEqual(normalize_market_rule_version(""), CURRENT_MARKET_RULE_VERSION)

    # ---- validate_evidence ------------------------------------------------
    def test_validate_catches_bad_provenance(self):
        bad = _ev().to_dict()
        bad["source_type"] = "NOPE"
        bad["market_rule_version"] = "NOPE"
        bad["is_mock"] = "yes"   # 非 bool
        errs = validate_evidence(bad)
        self.assertTrue(any("source_type" in e for e in errs), errs)
        self.assertTrue(any("market_rule_version" in e for e in errs), errs)
        self.assertTrue(any("is_mock" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
