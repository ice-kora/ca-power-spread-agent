# -*- coding: utf-8 -*-
"""
code/data_acquisition/tests/test_data_acquisition.py —— 采集框架单测（Agent E）

覆盖：
  TestAsOfSchemaConsistency   As-of 时间口径：schemas 与 agent/evidence 双实现一致、DST 边界
  TestGFSCollectorOffline     GFS 采集器离线路径：fixture normalize / MOCK 降级 / CACHE 降级 / 落盘
  TestCAISOCollectorOffline   CAISO 采集器离线路径：fixture normalize / MOCK 降级 / CACHE 降级
  TestCollectorDegradation    采集基类降级语义（LIVE > CACHE > MOCK）
  TestValidation              数据质量校验：eligible 漂移 / 缺失率 / 重复 / NaN / DST / mock 声明
  TestLiveSources             真实源（联网时）：GFS + CAISO live 拉取，decision_eligible 全过

运行（仓库根目录）：
    python -m unittest code.data_acquisition.tests.test_data_acquisition -v
不联网时 MOCK/CACHE 路径照常通过；仅 TestLiveSources 会被跳过（skipTest）。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.base import add_days  # noqa: E402
from code.data_acquisition.caiso_oasis import CAISOLoadForecastCollector  # noqa: E402
from code.data_acquisition.schemas import (  # noqa: E402
    MODE_BACKTEST,
    MODE_PRODUCTION,
    asof_from_dict,
    make_decision_cutoff,
    parse_timestamp,
    target_time_pt_to_utc,
)
from code.data_acquisition.validation import (  # noqa: E402
    check_dst,
    expected_cutoff_utc_zoneinfo,
    validate_collection,
)
from code.data_acquisition.weather_gfs import GFSWeatherCollector  # noqa: E402

DECISION_DATE = "2026-07-08"
TARGET_DATE = add_days(DECISION_DATE, 1)   # "2026-07-09"
RETRIEVED = "2026-08-09T00:00:00"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def make_gfs_payload(decision_date: str = DECISION_DATE, n_hours: int = 168):
    """Open-Meteo 风格 GFS hourly payload（确定性）。run = decision_date 12Z。"""
    start = datetime.fromisoformat(f"{decision_date}T12:00:00")
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
             for i in range(n_hours)]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [round(20.0 + 0.1 * i, 2) for i in range(n_hours)],
            "wind_speed_100m": [round(5.0 + 0.01 * i, 2) for i in range(n_hours)],
            "shortwave_radiation": [round(300.0 + i, 0) for i in range(n_hours)],
        },
    }


def make_caiso_payload(decision_date: str = DECISION_DATE):
    """OASIS SLD_FCST 风格 rows（确定性，24 小时 CA ISO-TAC）。"""
    target = add_days(decision_date, 1)
    rows = []
    for h in range(1, 25):
        tt = target_time_pt_to_utc(target, h) or ""
        rows.append({
            "OPR_DT": target,
            "OPR_HR": str(h),
            "TAC_AREA_NAME": "CA ISO-TAC",
            "INTERVALSTARTTIME_GMT": f"{tt}-00:00",
            "MW": f"{20000.0 + 500.0 * h:.2f}",
            "MARKET_RUN_ID": "DAM",
            "EXECUTION_TYPE": "DAM",
        })
    return {"rows": rows, "target_date": target}


def _network_ok() -> bool:
    for host, port in (("single-runs-api.open-meteo.com", 443),
                       ("oasis.caiso.com", 80)):
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            continue
    return False


NETWORK_OK = _network_ok()


# ---------------------------------------------------------------------------
# 1. As-of 时间口径一致性（schemas ↔ agent/evidence 双实现 + DST）
# ---------------------------------------------------------------------------
class TestAsOfSchemaConsistency(unittest.TestCase):
    """schemas.make_decision_cutoff 与 agent/evidence 的 decision_cutoff_utc 必须一致。"""

    def test_cutoff_matches_agent_evidence(self):
        from agent.evidence.gfs_forecast import decision_cutoff_utc
        for d in ("2026-01-15", "2026-03-08", "2026-07-08", "2026-11-01", "2026-12-01"):
            self.assertEqual(make_decision_cutoff(d), decision_cutoff_utc(d),
                             msg=f"decision_cutoff 双实现不一致 @ {d}")

    def test_dst_boundary_cutoffs(self):
        # 2026 夏时制：3 月第 2 周日（03-08）起 PDT(UTC-7)，11 月第 1 周日（11-01）起 PST(UTC-8)
        self.assertEqual(make_decision_cutoff("2026-07-08"), "2026-07-08T17:00:00")  # PDT
        self.assertEqual(make_decision_cutoff("2026-01-08"), "2026-01-08T18:00:00")  # PST
        self.assertEqual(make_decision_cutoff("2026-03-08"), "2026-03-08T17:00:00")  # 转夏令时当日
        self.assertEqual(make_decision_cutoff("2026-11-01"), "2026-11-01T18:00:00")  # 转冬令时当日

    def test_zoneinfo_independent_agreement(self):
        for d in ("2026-01-15", "2026-03-08", "2026-07-08", "2026-11-01", "2026-12-01"):
            self.assertEqual(make_decision_cutoff(d), expected_cutoff_utc_zoneinfo(d),
                             msg=f"zoneinfo 独立换算不一致 @ {d}")

    def test_target_time_pt_to_utc_dst(self):
        self.assertEqual(target_time_pt_to_utc("2026-07-09", 1), "2026-07-09T07:00:00")   # PDT H1
        self.assertEqual(target_time_pt_to_utc("2026-07-09", 24), "2026-07-10T06:00:00")  # PDT H24
        self.assertEqual(target_time_pt_to_utc("2026-01-09", 1), "2026-01-09T08:00:00")   # PST H1
        self.assertEqual(target_time_pt_to_utc("2026-01-09", 24), "2026-01-10T07:00:00")  # PST H24


# ---------------------------------------------------------------------------
# 2. GFS 采集器（离线路径）
# ---------------------------------------------------------------------------
class TestGFSCollectorOffline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.col = GFSWeatherCollector(node="CONTROLX_1_N001", cache_dir=self.tmp)

    def test_normalize_from_fixture(self):
        records = self.col._normalize(
            make_gfs_payload(), DECISION_DATE,
            provenance="FIXTURE", is_mock=False, retrieved_at=RETRIEVED)
        dicts = [r.to_dict() for r in records]
        # 24h × 3 vars = 72 条
        self.assertEqual(len(dicts), 72)
        self.assertEqual({r["field_name"] for r in dicts}, {"t2m", "wind100", "ssrd"})
        # 全部 eligible（BACKTEST：available_at = published_at = 12Z < cutoff 17:00Z）
        self.assertTrue(all(r["decision_eligible"] for r in dicts))
        # 三拆：GFS 非 mock、非 NOT_BACKTEST_SAFE → backtest 合格；
        # production_eligible=False 因采集模式=BACKTEST（历史回放不用于生产）。
        self.assertTrue(all(r["time_eligible"] for r in dicts))
        self.assertTrue(all(r["backtest_eligible"] for r in dicts))
        self.assertTrue(all(r["production_eligible"] is False for r in dicts))
        self.assertTrue(all(r["is_mock"] is False for r in dicts))
        self.assertTrue(all(r["not_backtest_safe"] is False for r in dicts))
        # 时间戳口径
        self.assertTrue(all(r["published_at"] == "2026-07-08T12:00:00" for r in dicts))
        self.assertTrue(all(r["decision_cutoff"] == "2026-07-08T17:00:00" for r in dicts))
        # H1 t2m 值 = fixture index 19（07-09T07:00Z 距 07-08T12:00Z 19h）
        r0 = next(r for r in dicts if r["field_name"] == "t2m" and r["target_time"] == "2026-07-09T07:00:00")
        self.assertEqual(r0["value"], round(20.0 + 0.1 * 19, 2))
        # 健康数据校验无 ERROR
        errs = validate_collection(self.col, dicts, DECISION_DATE,
                                   {"is_mock": False, "not_backtest_safe": False})
        self.assertFalse(any(v["level"] == "ERROR" for v in errs), errs)

    def test_hour_mapping_h1_h24(self):
        records = self.col._normalize(
            make_gfs_payload(), DECISION_DATE,
            provenance="FIXTURE", is_mock=False, retrieved_at=RETRIEVED)
        h1 = next(r for r in records if r.field_name == "t2m" and r.target_time.endswith("T07:00:00")
                  and r.target_time.startswith("2026-07-09"))
        h24 = next(r for r in records if r.field_name == "t2m" and r.target_time == "2026-07-10T06:00:00")
        self.assertEqual(h1.target_time, "2026-07-09T07:00:00")
        self.assertEqual(h24.target_time, "2026-07-10T06:00:00")
        self.assertIsNotNone(h1.value)
        self.assertIsNotNone(h24.value)

    def test_run_mock_fallback(self):
        self.col.network_enabled = False
        res = self.col.run(DECISION_DATE, save=False)
        self.assertEqual(res.metadata["provenance"], "MOCK")
        self.assertTrue(res.metadata["is_mock"])
        self.assertTrue(res.metadata["degraded"])
        self.assertGreaterEqual(res.n_records, 72)
        # 硬隔离：MOCK 数据 n_eligible=0，逐条 backtest/production/decision 全 FALSE
        self.assertEqual(res.n_eligible, 0)
        self.assertTrue(all(r["is_mock"] for r in res.records))
        self.assertTrue(all(r["time_eligible"] for r in res.records))   # 时间门槛合格
        self.assertTrue(all(not r["backtest_eligible"] for r in res.records))
        self.assertTrue(all(not r["production_eligible"] for r in res.records))
        self.assertTrue(all(not r["decision_eligible"] for r in res.records))
        self.assertTrue(any(v["level"] == "WARNING" and "MOCK" in v["message"]
                            for v in res.validation))

    def test_run_cache_fallback(self):
        # 预置 LIVE 缓存 → 离线运行时走 CACHE（数据本身非 mock）
        self.col._save_raw(make_gfs_payload(), DECISION_DATE, "LIVE", RETRIEVED)
        self.col.network_enabled = False
        res = self.col.run(DECISION_DATE, save=False, use_cache=True)
        self.assertEqual(res.metadata["provenance"], "CACHE")
        self.assertFalse(res.metadata["is_mock"])
        self.assertEqual(res.n_eligible, res.n_records)

    def test_run_saves_files(self):
        self.col._fetch_raw = lambda q: make_gfs_payload(q)   # 模拟 LIVE
        self.col.network_enabled = True
        res = self.col.run(DECISION_DATE, save=True)
        self.assertEqual(res.metadata["provenance"], "LIVE")
        self.assertIsNotNone(res.raw_path)
        self.assertIsNotNone(res.normalized_path)
        self.assertTrue(res.raw_path.exists())
        self.assertTrue(res.normalized_path.exists())
        doc = json.loads(res.normalized_path.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["records"]), 72)
        self.assertEqual(doc["timestamps"]["published_at"], "2026-07-08T12:00:00")

    def test_live_preferred_over_cache(self):
        # 有缓存 + 网络成功 → provenance=LIVE（网络优先，缓存仅降级）
        self.col._save_raw(make_gfs_payload(), DECISION_DATE, "LIVE", RETRIEVED)
        self.col._fetch_raw = lambda q: make_gfs_payload(q)
        self.col.network_enabled = True
        res = self.col.run(DECISION_DATE, save=False, use_cache=True)
        self.assertEqual(res.metadata["provenance"], "LIVE")


# ---------------------------------------------------------------------------
# 2b. GFS cycle 回测可靠性（P0-1 available_at 拆分）
# ---------------------------------------------------------------------------
class TestGFSCycleBacktestEligibility(unittest.TestCase):
    """GFS run 可回测分类：00Z/06Z 有可靠 vintage；12Z/18Z 无法可靠证明 → FALSE。"""

    def _normalized(self, cycle, mode=MODE_BACKTEST, retrieved_at=RETRIEVED):
        col = GFSWeatherCollector(node="CONTROLX_1_N001", cycle=cycle,
                                  cache_dir=Path(tempfile.mkdtemp()), mode=mode)
        start_h = int(cycle[:2])
        start = datetime.fromisoformat(f"{DECISION_DATE}T{start_h:02d}:00:00")
        payload = {
            "hourly": {
                "time": [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
                         for i in range(168)],
                "temperature_2m": [round(20.0 + 0.1 * i, 2) for i in range(168)],
                "wind_speed_100m": [round(5.0 + 0.01 * i, 2) for i in range(168)],
                "shortwave_radiation": [round(300.0 + i, 0) for i in range(168)],
            },
        }
        recs = col._normalize(payload, DECISION_DATE, provenance="FIXTURE",
                              is_mock=False, retrieved_at=retrieved_at)
        return col, [r.to_dict() for r in recs]

    def test_safe_cycles_backtest_eligible(self):
        for cyc in ("00Z", "06Z"):
            col, dicts = self._normalized(cyc)
            self.assertTrue(col.backtest_eligible_for_cycle(DECISION_DATE),
                            f"{cyc} 应为可回测 cycle")
            self.assertTrue(all(r["decision_eligible"] for r in dicts), cyc)
            self.assertTrue(all(r["backtest_eligible"] for r in dicts), cyc)
            self.assertTrue(all(r["time_eligible"] for r in dicts), cyc)
            # available_at 严格晚于 model_run_time（发布延迟为正）
            for r in dicts:
                self.assertGreater(parse_timestamp(r["available_at"]),
                                   parse_timestamp(r["model_run_time"]))

    def test_unsafe_cycles_backtest_ineligible(self):
        for cyc in ("12Z", "18Z"):
            col, dicts = self._normalized(cyc)
            self.assertFalse(col.backtest_eligible_for_cycle(DECISION_DATE),
                             f"{cyc} 应为不可回测 cycle")
            self.assertTrue(all(r["available_at"] == "" for r in dicts), cyc)
            self.assertTrue(all(not r["time_eligible"] for r in dicts), cyc)
            self.assertTrue(all(not r["backtest_eligible"] for r in dicts), cyc)
            self.assertTrue(all(not r["decision_eligible"] for r in dicts), cyc)

    def test_12z_production_live_fetch_eligible_when_before_cutoff(self):
        # ③ PRODUCTION/Shadow：真实当前抓取 + retrieved_at；12Z 在 cutoff 前拉到 → eligible
        col, dicts = self._normalized("12Z", mode=MODE_PRODUCTION,
                                      retrieved_at="2026-07-08T16:30:00")
        self.assertTrue(all(r["time_eligible"] for r in dicts))
        self.assertTrue(all(r["decision_eligible"] for r in dicts))
        self.assertTrue(all(r["available_at"] == "2026-07-08T16:30:00" for r in dicts))

    def test_model_run_time_field_written(self):
        _, dicts = self._normalized("06Z")
        self.assertTrue(all(r["model_run_time"] == "2026-07-08T06:00:00" for r in dicts))
        self.assertTrue(all(r["issue_time"] == "2026-07-08T06:00:00" for r in dicts))
        self.assertTrue(all(r["forecast_run"] == "2026-07-08T06:00Z" for r in dicts))


# ---------------------------------------------------------------------------
# 3. CAISO 采集器（离线路径）
# ---------------------------------------------------------------------------
class TestCAISOCollectorOffline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.col = CAISOLoadForecastCollector(resource="CA ISO-TAC", cache_dir=self.tmp)

    def test_normalize_from_fixture(self):
        records = self.col._normalize(
            make_caiso_payload(), DECISION_DATE,
            provenance="FIXTURE", is_mock=False, retrieved_at=RETRIEVED)
        dicts = [r.to_dict() for r in records]
        self.assertEqual(len(dicts), 24)
        self.assertEqual({r["field_name"] for r in dicts}, {"load_2da"})
        self.assertEqual({r["node"] for r in dicts}, {"CAISO_TAC"})
        self.assertEqual({r["region"] for r in dicts}, {"SYSTEM"})
        # NOT_BACKTEST_SAFE 硬隔离：时间合格但 backtest/decision 全 FALSE
        self.assertTrue(all(r["time_eligible"] for r in dicts))
        self.assertTrue(all(r["production_eligible"] is False for r in dicts))
        self.assertTrue(all(r["backtest_eligible"] is False for r in dicts))
        self.assertTrue(all(r["decision_eligible"] is False for r in dicts))
        self.assertTrue(all(r["not_backtest_safe"] for r in dicts))
        self.assertTrue(all(r["published_at"] == "2026-07-08T17:00:00" for r in dicts))
        self.assertTrue(all(r["decision_cutoff"] == "2026-07-08T17:00:00" for r in dicts))
        # H1 → 07-09T07:00Z，H24 → 07-10T06:00Z
        by_hour = {r["target_time"]: r for r in dicts}
        self.assertIn("2026-07-09T07:00:00", by_hour)
        self.assertIn("2026-07-10T06:00:00", by_hour)
        h1 = next(r for r in dicts if r["target_time"] == "2026-07-09T07:00:00")
        self.assertEqual(h1["value"], 20500.0)

    def test_run_mock_fallback(self):
        self.col.network_enabled = False
        res = self.col.run(DECISION_DATE, save=False)
        self.assertEqual(res.metadata["provenance"], "MOCK")
        self.assertEqual(res.n_records, 24)
        # 硬隔离：MOCK 数据 n_eligible=0
        self.assertEqual(res.n_eligible, 0)
        self.assertTrue(all(r["is_mock"] for r in res.records))
        self.assertTrue(all(not r["backtest_eligible"] for r in res.records))
        self.assertTrue(all(not r["production_eligible"] for r in res.records))
        self.assertTrue(any(v["level"] == "WARNING" and "MOCK" in v["message"]
                            for v in res.validation))

    def test_run_cache_fallback(self):
        self.col._save_raw(make_caiso_payload(), DECISION_DATE, "LIVE", RETRIEVED)
        self.col.network_enabled = False
        res = self.col.run(DECISION_DATE, save=False, use_cache=True)
        self.assertEqual(res.metadata["provenance"], "CACHE")
        self.assertFalse(res.metadata["is_mock"])
        self.assertEqual(res.n_records, 24)
        # LIVE 缓存数据非 mock，但源 NOT_BACKTEST_SAFE → 回测不可用
        self.assertEqual(res.n_eligible, 0)
        self.assertTrue(all(r["time_eligible"] for r in res.records))
        self.assertTrue(all(not r["backtest_eligible"] for r in res.records))

    def test_not_backtest_safe_flag(self):
        self.assertTrue(self.col.not_backtest_safe)
        self.col.network_enabled = False
        res = self.col.run(DECISION_DATE, save=False)
        self.assertTrue(res.metadata["not_backtest_safe"])
        self.assertTrue(any(v["level"] == "WARNING" and "not_backtest_safe" in v["message"]
                            for v in res.validation))
        # 逐条记录携带 not_backtest_safe 标记，且永不 backtest_eligible
        self.assertTrue(all(r["not_backtest_safe"] for r in res.records))
        self.assertTrue(all(not r["backtest_eligible"] for r in res.records))
        self.assertTrue(all(r["time_eligible"] for r in res.records))


# ---------------------------------------------------------------------------
# 4. 采集基类降级语义
# ---------------------------------------------------------------------------
class TestCollectorDegradation(unittest.TestCase):
    def test_mode_backtest_vs_production_availability(self):
        from code.data_acquisition.schemas import resolve_available_at
        # BACKTEST：available_at = vintage（published_at），忽略今天检索时刻
        self.assertEqual(resolve_available_at("2026-07-08T12:00:00", RETRIEVED, MODE_BACKTEST),
                         "2026-07-08T12:00:00")
        # PRODUCTION：available_at = max(published, retrieved)；cutoff 后拉取 → ineligible
        late = resolve_available_at("2026-07-08T12:00:00", "2026-07-08T17:05:00", MODE_PRODUCTION)
        rec = asof_from_dict({"available_at": late, "decision_cutoff": "2026-07-08T17:00:00"})
        self.assertFalse(rec.decision_eligible)


# ---------------------------------------------------------------------------
# 5. 数据质量校验
# ---------------------------------------------------------------------------
class TestValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.gfs = GFSWeatherCollector(node="CONTROLX_1_N001", cache_dir=self.tmp)
        self.caiso = CAISOLoadForecastCollector(resource="CA ISO-TAC", cache_dir=self.tmp)

    def _gfs_dicts(self, n_hours_drop=0):
        records = self.gfs._normalize(
            make_gfs_payload(), DECISION_DATE,
            provenance="FIXTURE", is_mock=False, retrieved_at=RETRIEVED)
        return [r.to_dict() for r in records]

    def test_eligibility_drift_detected(self):
        dicts = self._gfs_dicts()
        dicts[0]["decision_eligible"] = not dicts[0]["decision_eligible"]  # 篡改
        errs = validate_collection(self.gfs, dicts, DECISION_DATE,
                                   {"is_mock": False, "not_backtest_safe": False})
        self.assertTrue(any("decision_eligible 漂移" in v["message"] and v["level"] == "ERROR"
                            for v in errs))

    def test_hard_rule_mock_declared_eligible_flagged(self):
        # R7：is_mock=True 却声明 backtest_eligible/production_eligible → ERROR
        dicts = self._gfs_dicts()
        for r in dicts:
            r["is_mock"] = True
            r["backtest_eligible"] = True
            r["production_eligible"] = True
        errs = validate_collection(self.gfs, dicts, DECISION_DATE,
                                   {"is_mock": True, "not_backtest_safe": False})
        self.assertTrue(any("硬规则 R7" in v["message"] and v["level"] == "ERROR"
                            for v in errs), errs)
        self.assertTrue(any("backtest_eligible 漂移" in v["message"] for v in errs))

    def test_hard_rule_not_backtest_safe_declared_eligible_flagged(self):
        # R8：not_backtest_safe=True 却声明 backtest_eligible → ERROR
        dicts = self._gfs_dicts()
        for r in dicts:
            r["not_backtest_safe"] = True
            r["backtest_eligible"] = True
        errs = validate_collection(self.gfs, dicts, DECISION_DATE,
                                   {"is_mock": False, "not_backtest_safe": True})
        self.assertTrue(any("硬规则 R8" in v["message"] and v["level"] == "ERROR"
                            for v in errs), errs)

    def test_missing_hours_warning(self):
        dicts = self._gfs_dicts()
        # 只保留 t2m 的前 20 个目标小时（其余字段完整）→ t2m 覆盖 20/24 → WARNING
        t2m_hours = sorted({r["target_time"] for r in dicts if r["field_name"] == "t2m"})[:20]
        subset = [r for r in dicts
                  if r["field_name"] != "t2m" or r["target_time"] in set(t2m_hours)]
        errs = validate_collection(self.gfs, subset, DECISION_DATE,
                                   {"is_mock": False, "not_backtest_safe": False})
        self.assertTrue(any("缺失率" in v["message"] and v["level"] == "WARNING"
                            for v in errs))

    def test_duplicate_detected(self):
        dicts = self._gfs_dicts()
        dicts.append(dict(dicts[0]))  # 复制一条 → 重复
        errs = validate_collection(self.gfs, dicts, DECISION_DATE,
                                   {"is_mock": False, "not_backtest_safe": False})
        self.assertTrue(any("重复" in v["message"] and v["level"] == "ERROR"
                            for v in errs))

    def test_nan_value_reported(self):
        dicts = self._gfs_dicts()
        dicts[1]["value"] = None
        # value=None → t2m 该小时视为缺失 → 覆盖度 WARNING
        errs = validate_collection(self.gfs, dicts, DECISION_DATE,
                                   {"is_mock": False, "not_backtest_safe": False})
        self.assertTrue(any(v["level"] == "WARNING" for v in errs))

    def test_mock_warning_emitted(self):
        self.gfs.network_enabled = False
        res = self.gfs.run(DECISION_DATE, save=False)
        self.assertTrue(any(v["level"] == "WARNING" and "MOCK" in v["message"]
                            for v in res.validation))

    def test_dst_check_detects_mismatch(self):
        bad = "2026-07-08T18:00:00"   # 故意给错（应为 17:00Z）
        msgs = check_dst("2026-07-08", bad)
        self.assertTrue(any(v["level"] == "ERROR" and "DST" in v["message"] for v in msgs))
        # 正确值 → 通过
        self.assertEqual(check_dst("2026-07-08", "2026-07-08T17:00:00"), [])
        self.assertEqual(check_dst("2026-01-08", "2026-01-08T18:00:00"), [])

    def test_production_late_retrieval_ineligible_flagged(self):
        # PRODUCTION 模式下今天拉历史 → available_at=今天 > cutoff → 不可用
        col = GFSWeatherCollector(node="CONTROLX_1_N001", cache_dir=self.tmp,
                                  mode=MODE_PRODUCTION)
        records = col._normalize(make_gfs_payload(), DECISION_DATE,
                                 provenance="FIXTURE", is_mock=False,
                                 retrieved_at="2026-08-09T00:00:00")
        dicts = [r.to_dict() for r in records]
        self.assertFalse(any(r["decision_eligible"] for r in dicts))
        errs = validate_collection(col, dicts, DECISION_DATE,
                                   {"is_mock": False, "not_backtest_safe": False})
        # 校验会重算 eligible → 存储与重算一致，无漂移 ERROR；但也不会有 eligible 记录
        self.assertFalse(any("decision_eligible 漂移" in v["message"] for v in errs))


# ---------------------------------------------------------------------------
# 6. 真实源（联网）
# ---------------------------------------------------------------------------
@unittest.skipUnless(NETWORK_OK, "no network")
class TestLiveSources(unittest.TestCase):
    def test_gfs_live(self):
        tmp = Path(tempfile.mkdtemp())
        col = GFSWeatherCollector(node="CONTROLX_1_N001", cache_dir=tmp)
        res = col.run(DECISION_DATE, save=True)
        self.assertEqual(res.metadata["provenance"], "LIVE")
        self.assertEqual(res.n_records, 72)
        self.assertEqual(res.n_eligible, 72)
        self.assertFalse(any(v["level"] == "ERROR" for v in res.validation))
        self.assertFalse(any(r["value"] is None for r in res.records))

    def test_caiso_live(self):
        tmp = Path(tempfile.mkdtemp())
        col = CAISOLoadForecastCollector(resource="CA ISO-TAC", cache_dir=tmp)
        res = col.run(DECISION_DATE, save=True)
        self.assertEqual(res.metadata["provenance"], "LIVE")
        self.assertEqual(res.n_records, 24)
        # NOT_BACKTEST_SAFE 硬隔离：时间合格但不参与严格回测
        self.assertEqual(res.n_eligible, 0)
        self.assertTrue(all(r["time_eligible"] for r in res.records))
        self.assertTrue(all(not r["backtest_eligible"] for r in res.records))
        self.assertFalse(any(v["level"] == "ERROR" for v in res.validation))
        self.assertFalse(any(r["value"] is None for r in res.records))
        # 负荷合理性
        self.assertTrue(all(5000.0 <= (r["value"] or 0) <= 80000.0 for r in res.records))


if __name__ == "__main__":
    unittest.main(verbosity=2)
