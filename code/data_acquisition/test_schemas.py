# -*- coding: utf-8 -*-
"""
code/data_acquisition/test_schemas.py —— As-of 数据结构单测（unittest）

运行：
    python -m unittest code.data_acquisition.test_schemas -v
    （或在仓库根目录: python -m unittest discover -s code/data_acquisition -p "test_*.py"）
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.schemas import (
    MODE_BACKTEST,
    MODE_PRODUCTION,
    AsOfRecord,
    FeatureSnapshot,
    assert_no_post_decision,
    asof_from_dict,
    ensure_asof_dict,
    gate_asof_records,
    lead_hours_of,
    make_decision_cutoff,
    parse_timestamp,
    pt_naive_to_utc_naive,
    resolve_available_at,
    snapshot_from_asof_record,
    target_time_pt_to_utc,
    validate_asof_record,
    validate_snapshot,
)


def _gfs_backtest_record(decision_date="2026-07-08", **overrides):
    """GFS 回测样例记录：published_at = D 12Z UTC（vintage），retrieved_at = 今天。"""
    d = {
        "source": "NCEP_GFS_025_via_OpenMeteo",
        "field_name": "t2m",
        "forecast_run": "2026-07-08T12:00Z",
        "issue_time": "2026-07-08T12:00:00",
        "published_at": "2026-07-08T12:00:00",
        "retrieved_at": "2026-08-09T00:00:00",   # 今天的墙钟，仅审计
        "target_time": target_time_pt_to_utc("2026-07-09", 15),
        "node": "CONTROLX_1_N001",
        "value": 27.9,
        "decision_cutoff": make_decision_cutoff(decision_date),
        "raw_source_id": "run=2026-07-08T12:00",
        "mode": MODE_BACKTEST,
    }
    d.update(overrides)
    rec = asof_from_dict(d)
    if "available_at" not in overrides:
        # 未显式给 available_at → 按 mode 解析（默认回测 vintage 语义）
        rec.available_at = resolve_available_at(
            rec.published_at, rec.retrieved_at, mode=rec.mode or MODE_BACKTEST) or ""
    return rec


class TestDecisionEligibility(unittest.TestCase):
    """R1/R2：available_at <= decision_cutoff，时间缺失即不可用（保守）。"""

    def setUp(self):
        self.cutoff = "2026-07-08T17:00:00"   # D 10:00 PT → UTC（PDT）

    def test_eligible_before_cutoff(self):
        rec = _gfs_backtest_record()
        self.assertTrue(rec.decision_eligible)
        self.assertEqual(rec.available_at, "2026-07-08T12:00:00")
        self.assertTrue(rec.is_usable)

    def test_equal_is_eligible(self):
        # available_at == decision_cutoff（<= 语义）
        rec = _gfs_backtest_record(available_at=self.cutoff)
        self.assertTrue(rec.decision_eligible)

    def test_ineligible_after_cutoff(self):
        rec = _gfs_backtest_record(available_at="2026-07-08T18:00:00")
        self.assertFalse(rec.decision_eligible)
        self.assertFalse(rec.is_usable)

    def test_ineligible_missing_available_at(self):
        rec = _gfs_backtest_record(available_at="")
        self.assertFalse(rec.decision_eligible)
        self.assertIn("available_at", rec.missing_time_fields)

    def test_ineligible_unparseable_available_at(self):
        rec = _gfs_backtest_record(available_at="not-a-time")
        self.assertFalse(rec.decision_eligible)

    def test_ineligible_missing_decision_cutoff(self):
        rec = _gfs_backtest_record(decision_cutoff="")
        self.assertFalse(rec.decision_eligible)

    def test_ineligible_missing_both(self):
        rec = _gfs_backtest_record(available_at="", decision_cutoff="")
        self.assertFalse(rec.decision_eligible)

    def test_na_value_but_time_ok(self):
        # 时间合格但 value NaN → decision_eligible 仍 TRUE（时间判定），is_usable=False
        rec = _gfs_backtest_record(value=float("nan"))
        self.assertTrue(rec.decision_eligible)
        self.assertFalse(rec.is_usable)
        errs = validate_asof_record(rec.to_dict())
        self.assertTrue(any("value" in e for e in errs))


class TestTimeConversion(unittest.TestCase):
    """PT → UTC、cutoff、target_time、lead_hours。"""

    def test_summer_pdt_cutoff(self):
        self.assertEqual(make_decision_cutoff("2026-07-08"), "2026-07-08T17:00:00")

    def test_winter_pst_cutoff(self):
        self.assertEqual(make_decision_cutoff("2026-01-08"), "2026-01-08T18:00:00")

    def test_pt_naive_to_utc_summer(self):
        self.assertEqual(pt_naive_to_utc_naive("2026-07-09T14:00:00"), "2026-07-09T21:00:00")

    def test_pt_naive_to_utc_winter(self):
        self.assertEqual(pt_naive_to_utc_naive("2026-01-09T14:00:00"), "2026-01-09T22:00:00")

    def test_offset_string_normalized(self):
        # 带 -07:00 偏移 → 归一化到 UTC naive
        self.assertEqual(parse_timestamp("2026-07-08T05:00:00-07:00"),
                         datetime(2026, 7, 8, 12, 0, 0))
        self.assertEqual(parse_timestamp("2026-07-08T12:00:00Z"),
                         datetime(2026, 7, 8, 12, 0, 0))

    def test_target_time_mapping(self):
        # (target_date, hour=15) = 07-09 14:00 PT → UTC（PDT）
        self.assertEqual(target_time_pt_to_utc("2026-07-09", 15), "2026-07-09T21:00:00")
        # H1 = 00:00–01:00 PT
        self.assertEqual(target_time_pt_to_utc("2026-07-09", 1), "2026-07-09T07:00:00")

    def test_invalid_hour(self):
        self.assertIsNone(target_time_pt_to_utc("2026-07-09", 0))
        self.assertIsNone(target_time_pt_to_utc("2026-07-09", 25))
        self.assertIsNone(target_time_pt_to_utc("2026-07-09", "x"))

    def test_lead_hours(self):
        rec = _gfs_backtest_record()
        # target 07-09T21:00Z - available 07-08T12:00Z = 33h
        self.assertEqual(rec.lead_hours, 33.0)
        self.assertEqual(lead_hours_of("2026-07-09T21:00:00", "2026-07-08T12:00:00"), 33.0)
        self.assertIsNone(lead_hours_of("bad", "2026-07-08T12:00:00"))


class TestModeAvailability(unittest.TestCase):
    """R4/R5：两套采集模式的 available_at 解析。"""

    def test_backtest_vintage_eligible_despite_today_retrieval(self):
        # retrieved_at 是今天（远晚于 cutoff），但历史 vintage 早于 cutoff → eligible
        rec = _gfs_backtest_record()
        self.assertEqual(rec.available_at, "2026-07-08T12:00:00")
        self.assertTrue(rec.decision_eligible)

    def test_backtest_missing_vintage_ineligible(self):
        # 回测无法重建历史 published_at → available_at=None → 不可用
        av = resolve_available_at("", "2026-08-09T00:00:00", mode=MODE_BACKTEST)
        self.assertIsNone(av)
        rec = _gfs_backtest_record(published_at="", available_at=av or "")
        self.assertFalse(rec.decision_eligible)
        self.assertIn("published_at", rec.missing_time_fields)

    def test_production_max_of_published_retrieved(self):
        av = resolve_available_at(
            "2026-07-08T12:00:00", "2026-07-08T14:30:00", mode=MODE_PRODUCTION)
        self.assertEqual(av, "2026-07-08T14:30:00")   # 取较晚者

    def test_production_retrieved_after_cutoff_ineligible(self):
        av = resolve_available_at(
            "2026-07-08T12:00:00", "2026-07-08T17:05:00", mode=MODE_PRODUCTION)
        rec = _gfs_backtest_record(available_at=av or "", mode=MODE_PRODUCTION)
        self.assertFalse(rec.decision_eligible)   # 拉取晚于 cutoff → 不可用

    def test_production_missing_any_ineligible(self):
        self.assertIsNone(resolve_available_at("2026-07-08T12:00:00", None, MODE_PRODUCTION))
        self.assertIsNone(resolve_available_at(None, "2026-07-08T09:00:00", MODE_PRODUCTION))


class TestGate(unittest.TestCase):
    """gate 切分 + 防御性断言。"""

    def test_split_eligible_and_post(self):
        ok = _gfs_backtest_record(available_at="2026-07-08T12:00:00")
        late = _gfs_backtest_record(
            available_at="2026-07-08T18:00:00", raw_source_id="late")
        eligible, post = gate_asof_records([ok, late])
        self.assertEqual(len(eligible), 1)
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0].raw_source_id, "late")

    def test_assert_no_post_decision_raises(self):
        late = _gfs_backtest_record(available_at="2026-07-08T18:00:00")
        with self.assertRaises(RuntimeError):
            assert_no_post_decision([late])


class TestSnapshot(unittest.TestCase):
    """feature_snapshot：溯源 + 校验。"""

    def test_snapshot_from_eligible_record(self):
        rec = _gfs_backtest_record()
        snap = snapshot_from_asof_record(
            rec, "2026-07-08", "2026-07-09", 15, "t2m", created_at="2026-07-08T10:00:00")
        d = snap.to_dict()
        self.assertEqual(d["snapshot_id"], "SNAP-2026-07-08-CONTROLX_1_N001-2026-07-09-H15-t2m")
        self.assertTrue(d["decision_eligible"])
        self.assertEqual(d["feature_value"], 27.9)
        self.assertEqual(d["asof_record_id"], rec.asof_id)
        self.assertEqual(validate_snapshot(d), [])

    def test_snapshot_from_post_decision_record_ineligible(self):
        rec = _gfs_backtest_record(available_at="2026-07-08T18:00:00")
        snap = snapshot_from_asof_record(
            rec, "2026-07-08", "2026-07-09", 15, "t2m", created_at="2026-07-08T10:00:00")
        self.assertFalse(snap.decision_eligible)
        self.assertFalse(snap.is_usable)

    def test_snapshot_validate_catches_missing(self):
        bad = {"decision_date": "2026-07-08",
               "decision_cutoff": "2026-07-08T17:00:00",
               "created_at": "2026-07-08T10:00:00",
               "node": "", "target_date": "", "target_hour": 0,
               "feature_name": "", "feature_value": None,
               "source": "", "available_at": "", "decision_eligible": False,
               "asof_record_id": ""}
        errs = validate_snapshot(bad)
        self.assertGreaterEqual(len(errs), 4)
        self.assertTrue(any("target_hour" in e for e in errs))
        self.assertTrue(any("asof_record_id" in e for e in errs))

    def test_snapshot_hour_out_of_range(self):
        d = snapshot_from_asof_record(
            _gfs_backtest_record(), "2026-07-08", "2026-07-09", 25, "t2m").to_dict()
        self.assertTrue(any("target_hour" in e for e in validate_snapshot(d)))


class TestNormalize(unittest.TestCase):
    """规范化 / 序列化 / region 自动推断。"""

    def test_region_inferred_from_node(self):
        rec = _gfs_backtest_record(region="")
        self.assertEqual(rec.normalize().region, "ZP26")
        rec2 = asof_from_dict({"node": "ELCAJNGT_7_N001", "source": "s",
                               "field_name": "f", "value": 1}).normalize()
        self.assertEqual(rec2.region, "SP15")

    def test_coords_inferred(self):
        rec = _gfs_backtest_record(latitude=None, longitude=None).normalize()
        self.assertAlmostEqual(rec.latitude, 37.342839)
        self.assertAlmostEqual(rec.longitude, -118.471988)

    def test_illegal_mode_reset(self):
        rec = _gfs_backtest_record(mode="HACK").normalize()
        self.assertEqual(rec.mode, "")
        errs = validate_asof_record(rec.to_dict())
        self.assertFalse(any("mode" in e for e in errs))   # 空 mode 不报非法

    def test_roundtrip_dict(self):
        rec = _gfs_backtest_record()
        d2 = ensure_asof_dict(asof_from_dict(rec.to_dict()).to_dict())
        self.assertEqual(d2["decision_eligible"], rec.decision_eligible)
        self.assertEqual(d2["asof_id"], rec.asof_id)
        self.assertEqual(d2["lead_hours"], 33.0)

    def test_validate_reports_missing_fields(self):
        errs = validate_asof_record({"source": "x"})
        self.assertTrue(any("缺少字段" in e for e in errs))
        self.assertTrue(any("available_at" in e for e in errs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
