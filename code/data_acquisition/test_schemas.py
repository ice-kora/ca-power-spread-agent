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
    AVAILABILITY_BASIS_ASSUMED_AVAILABLE,
    AVAILABILITY_BASIS_KEY,
    AVAILABILITY_BASIS_KNOWN_PUBLICATION,
    AVAILABILITY_BASIS_STATIC,
    AVAILABILITY_BASIS_STRUCTURAL_LAG,
    BOUND_RULE_DECISION_DATE_00_PT,
    HAS_PRECISE_PUBLISH_TIME_KEY,
    LATEST_POSSIBLE_AVAILABLE_AT_KEY,
    MODE_BACKTEST,
    MODE_PRODUCTION,
    SOURCE_TYPES,
    AsOfRecord,
    FeatureSnapshot,
    assert_no_post_decision,
    asof_from_dict,
    ensure_asof_dict,
    feature_available_at_display,
    feature_decision_eligible,
    gate_asof_records,
    infer_source_type,
    latest_available_bound,
    lead_hours_of,
    make_decision_cutoff,
    parse_timestamp,
    pt_naive_to_utc_naive,
    resolve_available_at,
    snapshot_from_asof_record,
    structural_lag_available_bound,
    target_time_pt_to_utc,
    validate_asof_record,
    validate_snapshot,
)
from code.market_rules import (
    CURRENT_MARKET_RULE_VERSION,
    MARKET_RULE_VERSION_POST_DAME_EDAM_2026,
    MARKET_RULE_VERSIONS,
    normalize_market_rule_version,
)


def _gfs_backtest_record(decision_date="2026-07-08", **overrides):
    """GFS 回测样例记录：06Z run（可回测 cycle），published_at = init + 6h 保守上界。"""
    d = {
        "source": "NCEP_GFS_025_via_OpenMeteo",
        "field_name": "t2m",
        "forecast_run": "2026-07-08T06:00Z",
        "model_run_time": "2026-07-08T06:00:00",   # 起报时刻 = initialization_time
        "issue_time": "2026-07-08T06:00:00",
        "published_at": "2026-07-08T12:00:00",     # init + 6h 保守上界（发布延迟模型）
        "retrieved_at": "2026-08-09T00:00:00",     # 今天的墙钟，仅审计
        "target_time": target_time_pt_to_utc("2026-07-09", 15),
        "node": "CONTROLX_1_N001",
        "value": 27.9,
        "decision_cutoff": make_decision_cutoff(decision_date),
        "raw_source_id": "run=2026-07-08T06:00",
        "mode": MODE_BACKTEST,
    }
    d.update(overrides)
    rec = asof_from_dict(d)
    if "available_at" not in overrides:
        # 未显式给 available_at → 按 mode 解析（回测 = 历史 vintage）
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


class TestEligibilityHardRules(unittest.TestCase):
    """R7/R8 硬隔离：MOCK / NOT_BACKTEST_SAFE 永不进入回测/生产可用。"""

    def test_mock_never_backtest_or_production_eligible(self):
        # 时间合格（available_at <= cutoff）但 is_mock=True → 三个可用门槛全 FALSE
        rec = _gfs_backtest_record(is_mock=True)
        self.assertTrue(rec.time_eligible)
        self.assertFalse(rec.backtest_eligible)
        self.assertFalse(rec.production_eligible)
        self.assertFalse(rec.decision_eligible)
        self.assertFalse(rec.is_usable)

    def test_mock_blocks_even_if_before_cutoff(self):
        # 硬规则：即便 available_at 远早于 cutoff 也不能改变 is_mock 的隔离
        rec = _gfs_backtest_record(is_mock=True, available_at="2026-07-07T00:00:00")
        self.assertTrue(rec.time_eligible)
        self.assertFalse(rec.backtest_eligible)
        self.assertFalse(rec.production_eligible)
        self.assertFalse(rec.decision_eligible)

    def test_not_backtest_safe_blocks_backtest_only(self):
        # not_backtest_safe=True 只禁回测：backtest_eligible=False；生产仍可用
        rec = _gfs_backtest_record(not_backtest_safe=True)
        self.assertTrue(rec.time_eligible)
        self.assertFalse(rec.backtest_eligible)
        self.assertFalse(rec.decision_eligible)   # 空/回测模式下 decision = backtest_eligible

    def test_not_backtest_safe_production_allowed(self):
        # 生产采集：retrieved 早于 cutoff → time 合格；not_backtest_safe 不阻塞 production
        rec = _gfs_backtest_record(
            not_backtest_safe=True, mode=MODE_PRODUCTION,
            retrieved_at="2026-07-08T09:00:00",   # 生产口径 available_at = max(12Z, 09:00)=12Z
        )
        self.assertTrue(rec.time_eligible)
        self.assertFalse(rec.backtest_eligible)
        self.assertTrue(rec.production_eligible)
        self.assertTrue(rec.decision_eligible)

    def test_available_after_cutoff_rejected_all(self):
        # available_at > cutoff → 三个门槛全 FALSE
        rec = _gfs_backtest_record(available_at="2026-07-08T18:00:00")
        self.assertFalse(rec.time_eligible)
        self.assertFalse(rec.backtest_eligible)
        self.assertFalse(rec.production_eligible)
        self.assertFalse(rec.decision_eligible)

    def test_validate_catches_mock_eligibility_drift(self):
        # 存储声明 is_mock=True 却 backtest_eligible=True → 校验报硬规则违规
        bad = _gfs_backtest_record(is_mock=True).to_dict()
        bad["backtest_eligible"] = True
        bad["production_eligible"] = True
        bad["decision_eligible"] = True
        errs = validate_asof_record(bad)
        self.assertTrue(any("硬规则 R7" in e for e in errs), errs)
        self.assertTrue(any("漂移" in e for e in errs), errs)

    def test_validate_catches_not_backtest_safe_drift(self):
        bad = _gfs_backtest_record(not_backtest_safe=True).to_dict()
        bad["backtest_eligible"] = True
        bad["decision_eligible"] = True
        errs = validate_asof_record(bad)
        self.assertTrue(any("硬规则 R8" in e for e in errs), errs)
        self.assertTrue(any("漂移" in e for e in errs), errs)

    def test_gate_excludes_mock_to_post(self):
        mock_rec = _gfs_backtest_record(is_mock=True, raw_source_id="mock")
        ok_rec = _gfs_backtest_record(raw_source_id="real")
        eligible, post = gate_asof_records([mock_rec, ok_rec])
        self.assertEqual(len(eligible), 1)
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0].raw_source_id, "mock")
        self.assertEqual(eligible[0].raw_source_id, "real")

    def test_assert_no_post_decision_blocks_mock(self):
        mock_rec = _gfs_backtest_record(is_mock=True)
        with self.assertRaises(RuntimeError):
            assert_no_post_decision([mock_rec])

    def test_snapshot_from_mock_record_flagged(self):
        rec = _gfs_backtest_record(is_mock=True)
        snap = snapshot_from_asof_record(
            rec, "2026-07-08", "2026-07-09", 15, "t2m", created_at="2026-07-08T10:00:00")
        d = snap.to_dict()
        self.assertTrue(d["is_mock"])
        self.assertTrue(d["time_eligible"])      # 时间门槛仍合格（R1），但 MOCK 硬隔离
        self.assertFalse(d["backtest_eligible"])
        self.assertFalse(d["production_eligible"])
        self.assertFalse(d["decision_eligible"])
        self.assertFalse(snap.is_usable)
        self.assertEqual(validate_snapshot(d), [])

    def test_snapshot_validate_catches_mock_eligible(self):
        # 快照声明 is_mock=True 却 backtest_eligible=True → 校验报硬规则
        bad = _gfs_backtest_record(is_mock=True)
        d = snapshot_from_asof_record(
            bad, "2026-07-08", "2026-07-09", 15, "t2m", created_at="2026-07-08T10:00:00").to_dict()
        d["backtest_eligible"] = True
        d["production_eligible"] = True
        errs = validate_snapshot(d)
        self.assertTrue(any("硬规则 R7" in e for e in errs), errs)


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


class TestGFSAvailableAtSplit(unittest.TestCase):
    """P0-1：五个时间概念拆分 + Time Gate 只判 available_at + 回测可靠性（R9/R10）。"""

    def test_available_at_strictly_after_model_run_time(self):
        # GFS 发布延迟为正：available_at(12:00) > model_run_time(06:00)，绝不相等
        rec = _gfs_backtest_record()
        self.assertEqual(rec.model_run_time, "2026-07-08T06:00:00")
        self.assertGreater(rec.parsed_available_at, rec.parsed_model_run_time)
        self.assertTrue(rec.backtest_eligible)

    def test_init_time_must_not_be_available_at(self):
        # 回归保护：把 available_at 设成 model_run_time（原 bug）→ backtest_eligible=False
        rec = _gfs_backtest_record(model_run_time="2026-07-08T06:00:00",
                                   available_at="2026-07-08T06:00:00")
        self.assertTrue(rec.time_eligible)          # 时间上 06:00 <= cutoff（纯时间门槛）
        self.assertFalse(rec.backtest_eligible)     # 但 init ≠ 可用时刻 → 不可回测
        self.assertFalse(rec.decision_eligible)

    def test_validate_catches_init_as_available(self):
        # R9 校验：BACKTEST available_at <= model_run_time → validate 报错
        d = _gfs_backtest_record(model_run_time="2026-07-08T06:00:00",
                                 available_at="2026-07-08T06:00:00").to_dict()
        errs = validate_asof_record(d)
        self.assertTrue(any("严格晚于 model_run_time" in e for e in errs), errs)

    def test_unresolved_available_at_ineligible(self):
        # R10：无可靠 vintage → available_at 空 → 不进历史决策
        rec = _gfs_backtest_record(available_at="")
        self.assertFalse(rec.time_eligible)
        self.assertFalse(rec.backtest_eligible)
        self.assertFalse(rec.decision_eligible)
        self.assertIn("available_at", rec.missing_time_fields)

    def test_model_run_time_roundtrip(self):
        rec = _gfs_backtest_record()
        d2 = ensure_asof_dict(asof_from_dict(rec.to_dict()).to_dict())
        self.assertEqual(d2["model_run_time"], "2026-07-08T06:00:00")
        self.assertEqual(d2["backtest_eligible"], rec.backtest_eligible)


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


class TestProvenanceFields(unittest.TestCase):
    """Provenance MVP：source_type / is_mock / raw_source_id / market_rule_version。"""

    # ---- source_type 推断 -------------------------------------------------
    def test_infer_source_type_by_field(self):
        self.assertEqual(infer_source_type("src", "da_lmp"), "PRICE")
        self.assertEqual(infer_source_type("src", "rtpd_price"), "PRICE")
        self.assertEqual(infer_source_type("src", "darptd_return"), "PRICE")
        self.assertEqual(infer_source_type("src", "load_2da"), "LOAD")
        self.assertEqual(infer_source_type("src", "t2m"), "WEATHER")
        self.assertEqual(infer_source_type("src", "ssrd"), "WEATHER")
        self.assertEqual(infer_source_type("src", "wind100"), "WEATHER")
        self.assertEqual(infer_source_type("CAISO_OASIS_SLD_FCST", "load_2da"), "LOAD")
        self.assertEqual(infer_source_type("src", "holiday"), "STATIC")
        self.assertEqual(infer_source_type("src", "rolling_std30"), "DERIVED")
        self.assertEqual(infer_source_type("src", "spread_lag1"), "PRICE")  # spread 优先归价格
        self.assertEqual(infer_source_type("weird", "zzz_unknown"), "UNKNOWN")

    def test_source_type_auto_inferred_on_record(self):
        rec = _gfs_backtest_record()   # field_name=t2m
        self.assertEqual(rec.source_type, "WEATHER")

    def test_source_type_explicit_wins(self):
        rec = _gfs_backtest_record(source_type="LOAD")
        self.assertEqual(rec.source_type, "LOAD")

    def test_source_type_illegal_replaced(self):
        rec = _gfs_backtest_record(source_type="HACK").normalize()
        self.assertIn(rec.source_type, SOURCE_TYPES)
        errs = validate_asof_record(rec.to_dict())
        self.assertFalse(any("source_type" in e for e in errs))  # normalize 已规约

    def test_snapshot_carries_source_type(self):
        rec = _gfs_backtest_record()
        snap = snapshot_from_asof_record(
            rec, "2026-07-08", "2026-07-09", 15, "t2m", created_at="2026-07-08T10:00:00")
        self.assertEqual(snap.source_type, "WEATHER")

    # ---- market_rule_version ----------------------------------------------
    def test_market_rule_default(self):
        rec = _gfs_backtest_record()
        self.assertEqual(rec.market_rule_version, CURRENT_MARKET_RULE_VERSION)
        self.assertIn(rec.market_rule_version, MARKET_RULE_VERSIONS)

    def test_market_rule_normalize_unknown_falls_back(self):
        rec = _gfs_backtest_record(market_rule_version="FUTURE_V99").normalize()
        self.assertEqual(rec.market_rule_version, CURRENT_MARKET_RULE_VERSION)
        self.assertEqual(normalize_market_rule_version("POST_DAME_EDAM_2026"),
                         MARKET_RULE_VERSION_POST_DAME_EDAM_2026)
        self.assertEqual(normalize_market_rule_version(""), CURRENT_MARKET_RULE_VERSION)

    def test_market_rule_in_to_dict_and_snapshot(self):
        rec = _gfs_backtest_record(market_rule_version="POST_DAME_EDAM_2026")
        self.assertEqual(rec.to_dict()["market_rule_version"], "POST_DAME_EDAM_2026")
        snap = snapshot_from_asof_record(
            rec, "2026-07-08", "2026-07-09", 15, "t2m", created_at="2026-07-08T10:00:00")
        self.assertEqual(snap.market_rule_version, "POST_DAME_EDAM_2026")
        self.assertEqual(validate_snapshot(snap.to_dict()), [])

    # ---- is_mock / raw_source_id 溯源 -------------------------------------
    def test_provenance_trace_fields_present(self):
        rec = _gfs_backtest_record(raw_source_id="run=2026-07-08T12:00")
        d = rec.to_dict()
        for k in ("source", "source_type", "is_mock", "raw_source_id", "target_time",
                  "available_at", "retrieved_at", "time_eligible",
                  "backtest_eligible", "production_eligible", "value"):
            self.assertIn(k, d, f"provenance 字段 {k} 缺失")

    def test_snapshot_provenance_trace_fields_present(self):
        snap = snapshot_from_asof_record(
            _gfs_backtest_record(), "2026-07-08", "2026-07-09", 15, "t2m",
            created_at="2026-07-08T10:00:00").to_dict()
        for k in ("source", "source_type", "is_mock", "raw_source_id", "target_time",
                  "available_at", "retrieved_at", "time_eligible",
                  "backtest_eligible", "production_eligible", "feature_value"):
            self.assertIn(k, snap, f"snapshot provenance 字段 {k} 缺失")

    def test_validate_reports_bad_source_type_and_mrv(self):
        bad = _gfs_backtest_record().to_dict()
        bad["source_type"] = "NOPE"
        bad["market_rule_version"] = "NOPE"
        errs = validate_asof_record(bad)
        self.assertTrue(any("source_type" in e for e in errs), errs)
        self.assertTrue(any("market_rule_version" in e for e in errs), errs)


class TestFeatureAvailabilitySemantics(unittest.TestCase):
    """Agent B P0-2：展示口径 == Time Gate 判定口径，禁止编造精确发布时刻。"""

    def _lag(self, **over):
        meta = {
            AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_STRUCTURAL_LAG,
            LATEST_POSSIBLE_AVAILABLE_AT_KEY: BOUND_RULE_DECISION_DATE_00_PT,
            HAS_PRECISE_PUBLISH_TIME_KEY: False,
        }
        meta.update(over)
        return meta

    # ---- 最晚可证上界 ----
    def test_structural_lag_bound_decision_date_00pt_to_utc(self):
        # 2026-07-08 为 PDT（UTC−7）：00:00 PT → 07:00 UTC
        self.assertEqual(structural_lag_available_bound("2026-07-08"), "2026-07-08T07:00:00")
        # 2026-01-08 为 PST（UTC−8）：00:00 PT → 08:00 UTC
        self.assertEqual(structural_lag_available_bound("2026-01-08"), "2026-01-08T08:00:00")
        self.assertIsNone(structural_lag_available_bound(""))
        self.assertIsNone(structural_lag_available_bound(None))

    def test_latest_available_bound_by_basis(self):
        self.assertEqual(latest_available_bound(self._lag(), "2026-07-08"), "2026-07-08T07:00:00")
        assumed = {AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_ASSUMED_AVAILABLE,
                   LATEST_POSSIBLE_AVAILABLE_AT_KEY: BOUND_RULE_DECISION_DATE_00_PT}
        self.assertEqual(latest_available_bound(assumed, "2026-07-08"), "2026-07-08T07:00:00")
        known = {AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_KNOWN_PUBLICATION,
                 "available_at": "2026-07-08T12:00:00"}
        self.assertEqual(latest_available_bound(known, "2026-07-08"), "2026-07-08T12:00:00")
        static = {AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_STATIC}
        self.assertIsNone(latest_available_bound(static, "2026-07-08"))   # 静态无时间门槛
        self.assertIsNone(latest_available_bound({}, "2026-07-08"))       # UNKNOWN/缺字段 → 无上界

    # ---- 特征级 Time Gate ----
    def test_feature_decision_eligible_hard_rule(self):
        cutoff = make_decision_cutoff("2026-07-08")   # 17:00 UTC（PDT）
        # STRUCTURAL_LAG：上界 00:00 PT = 07:00 UTC <= 17:00 UTC → TRUE
        self.assertTrue(feature_decision_eligible(self._lag(), "2026-07-08", cutoff))
        # STATIC：恒 TRUE
        self.assertTrue(feature_decision_eligible({AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_STATIC},
                                                  "2026-07-08", cutoff))
        # KNOWN_PUBLICATION：available_at(12:00 UTC) <= cutoff(17:00) → TRUE
        known = {AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_KNOWN_PUBLICATION,
                 "available_at": "2026-07-08T12:00:00"}
        self.assertTrue(feature_decision_eligible(known, "2026-07-08", cutoff))
        # KNOWN_PUBLICATION：available_at(18:00 UTC) > cutoff(17:00) → FALSE（铁律反例）
        late = {AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_KNOWN_PUBLICATION,
                "available_at": "2026-07-08T18:00:00"}
        self.assertFalse(feature_decision_eligible(late, "2026-07-08", cutoff))
        # UNKNOWN/缺字段 → FALSE
        self.assertFalse(feature_decision_eligible({}, "2026-07-08", cutoff))
        # cutoff 缺失 → FALSE（宁保守不穿越）
        self.assertFalse(feature_decision_eligible(self._lag(), "2026-07-08", None))

    # ---- 展示字符串：不出现伪精确时间戳 ----
    def test_display_has_no_fake_precise_timestamp(self):
        disp = feature_available_at_display(self._lag(), "2026-07-16")
        self.assertEqual(disp, "≤ 2026-07-16 00:00 PT")   # 最晚可证上界
        self.assertNotIn("23:59", disp)
        self.assertIn("≤", disp)
        assumed = {AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_ASSUMED_AVAILABLE,
                   LATEST_POSSIBLE_AVAILABLE_AT_KEY: BOUND_RULE_DECISION_DATE_00_PT}
        self.assertIn("≤ 2026-07-16 00:00 PT", feature_available_at_display(assumed, "2026-07-16"))
        known = {AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_KNOWN_PUBLICATION,
                 "available_at": "2026-07-08T12:00:00"}
        self.assertEqual(feature_available_at_display(known, "2026-07-16"), "2026-07-08T12:00:00")
        self.assertEqual(feature_available_at_display({AVAILABILITY_BASIS_KEY: AVAILABILITY_BASIS_STATIC},
                                                      "2026-07-16"), "静态/日历（恒可用）")

    # ---- canonical availability_map 全特征复算（P0-2 铁律）----
    def test_canonical_availability_map_all_eligible(self):
        from code.canonical import X_COLUMNS, availability_map
        av = availability_map()
        self.assertEqual(set(av.keys()), set(X_COLUMNS))
        for dd in ("2026-01-08", "2026-07-08"):
            cutoff = make_decision_cutoff(dd) or ""
            for f, meta in av.items():
                self.assertTrue(
                    feature_decision_eligible(meta, dd, cutoff),
                    f"P0-2 特征 {f} @ {dd} 判定不可用（展示与 Time Gate 不一致）")
                self.assertNotIn("23:59", feature_available_at_display(meta, dd),
                                 f"P0-2 特征 {f} 展示出现伪精确时间戳 23:59")
            # 静态特征无精确发布时刻；其余 X 特征均有 basis
            for f in X_COLUMNS:
                self.assertIn(AVAILABILITY_BASIS_KEY, av[f], f"特征 {f} 缺 availability_basis")


if __name__ == "__main__":
    unittest.main(verbosity=2)
