# -*- coding: utf-8 -*-
"""
agent/evidence/test_gfs_unified.py —— P0-1 GFS 单一事实来源一致性测试（unittest）
================================================================================

覆盖（P0-1 任务验收）：
  TestGFSSingleSource        Web / CLI / Agent 三条路径拿到**同一条** GFS Evidence 的
                             published_at / available_at / decision_cutoff / decision_eligible
                             完全一致（均来自 weather_gfs.GFSWeatherCollector 的 AsOfRecord）。
  TestGFSCycleEligibility    12Z / 18Z 按修复后逻辑回测 eligible=FALSE；
                             06Z 回测 eligible=TRUE；
                             12Z PRODUCTION 在 cutoff 前真实拉到 → eligible=TRUE（记 retrieved_at）。
  TestGFSAdapterHonesty      MOCK 降级返回空（诚实：不编造天气预报）；
                             validate_evidence 对 adapter 输出零违规。

运行（仓库根目录）：
    python -m unittest agent.evidence.test_gfs_unified -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.schemas import (  # noqa: E402
    MODE_BACKTEST,
    MODE_PRODUCTION,
    make_decision_cutoff,
)
from code.data_acquisition.weather_gfs import (  # noqa: E402
    DEFAULT_CYCLE,
    GFSWeatherCollector,
)
from agent.evidence.fetcher import fetch_evidence  # noqa: E402
from agent.evidence.gfs_forecast import (  # noqa: E402
    build_gfs_evidence,
    decision_cutoff_utc,
    forecast_issue_time_utc,
)
from agent.evidence.schema import (  # noqa: E402
    evidence_from_dict,
    validate_evidence,
)

NODE = "CONTROLX_1_N001"
DD_SUMMER = "2026-07-08"   # 夏时制：cutoff = 17:00 UTC
DD_WINTER = "2026-01-08"   # 冬令时：cutoff = 18:00 UTC


# ---------------------------------------------------------------------------
# Fixtures / helpers（确定性离线，不联网）
# ---------------------------------------------------------------------------
def make_gfs_payload(decision_date: str, cycle: str, n_hours: int = 168):
    """Open-Meteo 风格 GFS hourly payload（确定性）。run = decision_date {cycle}。"""
    start_h = int(cycle[:2])
    start = datetime.fromisoformat(f"{decision_date}T{start_h:02d}:00:00")
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


def patch_gfs_fetch(cycle: str):
    """把 GFSWeatherCollector._fetch_raw 换成确定性 fixture（离线）。"""
    return mock.patch.object(
        GFSWeatherCollector, "_fetch_raw",
        new=lambda self, q: make_gfs_payload(q, cycle))


def _asof_dict(cycle: str, decision_date: str, mode: str = MODE_BACKTEST) -> dict:
    """Path C：data_acquisition 采集器（CLI / run_acquisition 同款）→ 首条 AsOfRecord。"""
    col = GFSWeatherCollector(
        node=NODE, cycle=cycle, mode=mode,
        cache_dir=Path(tempfile.mkdtemp()), network_enabled=True)
    with patch_gfs_fetch(cycle):
        res = col.run(decision_date, save=False)
    assert res.records, f"{cycle} no records"
    return res.records[0]


def _evidence_via_fetcher(cycle: str, decision_date: str,
                          mode: str = MODE_BACKTEST) -> dict:
    """Path A：fetch_evidence（Agent / Web decision-card 同款注册回调）。"""
    with patch_gfs_fetch(cycle):
        evs = fetch_evidence(
            node=NODE, decision_date=decision_date,
            include_placeholders=False, include_real_sources=True)
    gfs = [e for e in evs if e.get("event_type") == "WEATHER_FORECAST"]
    assert gfs, "fetch_evidence 未返回 GFS 证据"
    return gfs[0]


def _evidence_via_adapter(cycle: str, decision_date: str,
                          mode: str = MODE_BACKTEST) -> dict:
    """Path B：build_gfs_evidence（mvp_demo / CLI 同款 Adapter 直连）。"""
    with patch_gfs_fetch(cycle):
        return build_gfs_evidence(NODE, decision_date, cycle=cycle, mode=mode)


class TestGFSSingleSource(unittest.TestCase):
    """Web / CLI / Agent 三端同一条 GFS Evidence 时间字段完全一致。"""

    TIME_FIELDS = ("published_at", "available_at", "decision_cutoff", "decision_eligible")

    def _assert_three_paths(self, cycle: str, decision_date: str,
                            mode: str = MODE_BACKTEST) -> dict:
        """三端一致（仅默认 cycle 可三端比：fetch_evidence 走注册回调默认 cycle）。"""
        a = _evidence_via_fetcher(cycle, decision_date, mode)      # Agent / Web
        b = _evidence_via_adapter(cycle, decision_date, mode)      # CLI (mvp_demo)
        c = _asof_dict(cycle, decision_date, mode)                 # data_acquisition CLI
        for f in self.TIME_FIELDS:
            self.assertEqual(a.get(f), b.get(f),
                             msg=f"[{cycle}] Evidence(fetcher) vs Evidence(adapter) {f} 不一致")
            self.assertEqual(a.get(f), c.get(f),
                             msg=f"[{cycle}] Evidence(fetcher) vs AsOfRecord {f} 不一致")
            self.assertEqual(b.get(f), c.get(f),
                             msg=f"[{cycle}] Evidence(adapter) vs AsOfRecord {f} 不一致")
        return a

    def _assert_adapter_asof(self, cycle: str, decision_date: str,
                             mode: str = MODE_BACKTEST) -> dict:
        """12Z/18Z：Adapter 与 AsOfRecord 的可用性时间字段一致。

        available_at / decision_cutoff / decision_eligible 必须逐字一致；
        published_at 在"无可靠 vintage 且 published 估计 == cutoff"边界会被
        Adapter 清空（防止 schema 回退 published 误判可用），故单独断言。
        """
        b = _evidence_via_adapter(cycle, decision_date, mode)
        c = _asof_dict(cycle, decision_date, mode)
        for f in ("available_at", "decision_cutoff", "decision_eligible"):
            self.assertEqual(b.get(f), c.get(f),
                             msg=f"[{cycle}] Evidence(adapter) vs AsOfRecord {f} 不一致")
        return b, c

    def test_06z_default_backtest_consistent(self):
        ev = self._assert_three_paths("06Z", DD_SUMMER)
        # 06Z 可回测：available_at = init+6h 保守上界，严格早于 cutoff → eligible
        self.assertEqual(ev["published_at"], "2026-07-08T12:00:00")
        self.assertEqual(ev["available_at"], "2026-07-08T12:00:00")
        self.assertEqual(ev["decision_cutoff"], "2026-07-08T17:00:00")
        self.assertTrue(ev["decision_eligible"])

    def test_12z_backtest_consistent_ineligible(self):
        b, c = self._assert_adapter_asof("12Z", DD_SUMMER)
        self.assertEqual(b["published_at"], "2026-07-08T18:00:00")  # 保守上界（正常保留）
        self.assertEqual(c["published_at"], "2026-07-08T18:00:00")
        self.assertEqual(b["available_at"], "")                      # 无可靠 vintage
        self.assertFalse(b["decision_eligible"])

    def test_18z_backtest_consistent_ineligible(self):
        b, _ = self._assert_adapter_asof("18Z", DD_SUMMER)
        self.assertFalse(b["decision_eligible"])
        self.assertEqual(b["available_at"], "")
        # 18Z published 保守上界 = 次日 00:00（严格 > cutoff，正常保留）
        self.assertEqual(b["published_at"], "2026-07-09T00:00:00")

    def test_winter_12z_boundary_consistent_ineligible(self):
        # 冬令时 12Z：published 估计 == cutoff（18:00 == 18:00），但无可靠 vintage
        # → 必须仍为 ineligible（边界不视为可证明），Adapter 与 AsOfRecord 可用性一致。
        b, c = self._assert_adapter_asof("12Z", DD_WINTER)
        self.assertFalse(b["decision_eligible"])
        self.assertEqual(b["available_at"], "")     # 无可靠 vintage → available_at 保持空
        # V0.3.1.3 available-at-only：Time Gate 不 fallback published_at，故 published_at
        # 仅作审计保留（与 AsOfRecord 完全一致，不因边界被清空）；可用性只由 available_at 决定。
        self.assertEqual(b["published_at"], "2026-01-08T18:00:00")
        self.assertEqual(c["published_at"], "2026-01-08T18:00:00")

    def test_fetcher_always_default_cycle(self):
        # fetch_evidence（Web / Agent / decision-card）不传 cycle →
        # 走注册回调默认 cycle = weather_gfs.DEFAULT_CYCLE（06Z）
        self.assertEqual(DEFAULT_CYCLE, "06Z")
        with patch_gfs_fetch(DEFAULT_CYCLE):
            evs = fetch_evidence(
                node=NODE, decision_date=DD_SUMMER,
                include_placeholders=False, include_real_sources=True)
        gfs = [e for e in evs if e.get("event_type") == "WEATHER_FORECAST"][0]
        self.assertIn(f"GFS-{DEFAULT_CYCLE}-", gfs["evidence_id"])
        self.assertTrue(gfs["decision_eligible"])

    def test_evidence_passes_validation(self):
        ev = _evidence_via_adapter("06Z", DD_SUMMER)
        self.assertEqual(validate_evidence(ev), [])
        # 存储的 decision_eligible 与 schema 重算一致（无漂移）
        self.assertEqual(ev["decision_eligible"],
                         bool(evidence_from_dict(ev).decision_eligible))


class TestGFSCycleEligibility(unittest.TestCase):
    """12Z/18Z 回测 FALSE；06Z 回测 TRUE；12Z PRODUCTION 按 retrieved_at 判定。"""

    def test_12z_backtest_never_eligible(self):
        for dd in (DD_SUMMER, DD_WINTER):
            with patch_gfs_fetch("12Z"):
                ev = build_gfs_evidence(NODE, dd, cycle="12Z", mode=MODE_BACKTEST)
            self.assertFalse(ev["decision_eligible"], f"12Z backtest @ {dd} 应 FALSE")
            self.assertEqual(ev["available_at"], "")

    def test_12z_production_fetched_before_cutoff_eligible(self):
        # 诚实口径：12Z 若在 PRODUCTION 模式 cutoff 前真实拉到 → eligible（记 retrieved_at）
        with mock.patch("code.data_acquisition.base.utc_now_naive",
                        return_value="2026-07-08T16:30:00"):
            with patch_gfs_fetch("12Z"):
                ev = build_gfs_evidence(NODE, DD_SUMMER, cycle="12Z", mode=MODE_PRODUCTION)
        self.assertEqual(ev["published_at"], "2026-07-08T16:00:00")    # init + 典型 4h
        self.assertEqual(ev["available_at"], "2026-07-08T16:30:00")    # max(pub, retrieved)
        self.assertEqual(ev["retrieved_at"], "2026-07-08T16:30:00")
        self.assertTrue(ev["decision_eligible"])

    def test_12z_production_fetched_after_cutoff_ineligible(self):
        # 生产在 cutoff 后拉到 → available_at = 17:05 > 17:00 → 不可用
        with mock.patch("code.data_acquisition.base.utc_now_naive",
                        return_value="2026-07-08T17:05:00"):
            with patch_gfs_fetch("12Z"):
                ev = build_gfs_evidence(NODE, DD_SUMMER, cycle="12Z", mode=MODE_PRODUCTION)
        self.assertEqual(ev["available_at"], "2026-07-08T17:05:00")
        self.assertFalse(ev["decision_eligible"])

    def test_06z_backtest_eligible(self):
        with patch_gfs_fetch("06Z"):
            ev = build_gfs_evidence(NODE, DD_SUMMER, cycle="06Z", mode=MODE_BACKTEST)
        self.assertTrue(ev["decision_eligible"])
        self.assertEqual(ev["available_at"], "2026-07-08T12:00:00")
        # R9 回归：available_at 严格晚于 model_run_time（发布延迟为正）
        self.assertGreater(
            datetime.fromisoformat(ev["available_at"]),
            datetime.fromisoformat("2026-07-08T06:00:00"))

    def test_18z_backtest_ineligible(self):
        with patch_gfs_fetch("18Z"):
            ev = build_gfs_evidence(NODE, DD_SUMMER, cycle="18Z", mode=MODE_BACKTEST)
        self.assertFalse(ev["decision_eligible"])
        self.assertEqual(ev["available_at"], "")
        # 18Z published 保守上界 = 次日 00:00（严格 > cutoff）
        self.assertEqual(ev["published_at"], "2026-07-09T00:00:00")

    def test_deprecated_cutoff_delegation(self):
        # [废弃转发] decision_cutoff_utc / forecast_issue_time_utc 与单一源一致
        self.assertEqual(decision_cutoff_utc(DD_SUMMER), make_decision_cutoff(DD_SUMMER))
        self.assertEqual(forecast_issue_time_utc(DD_SUMMER), "2026-07-08T12:00:00")


class TestGFSAdapterHonesty(unittest.TestCase):
    """MOCK 降级不编造；AsOfRecord 时间字段透传。"""

    def test_mock_degradation_returns_empty(self):
        # 网络失败 + 无缓存 → 确定性 MOCK → Adapter 返回 {}（不冒充真实预报）
        col = GFSWeatherCollector(
            node=NODE, cycle="06Z", cache_dir=Path(tempfile.mkdtemp()))
        with mock.patch.object(GFSWeatherCollector, "_fetch_raw",
                               side_effect=OSError("offline")):
            ev = build_gfs_evidence(NODE, DD_SUMMER, cycle="06Z", _collector=col)
        self.assertEqual(ev, {})

    def test_fetcher_empty_on_mock(self):
        # 网络失败 + 无缓存（隔离 tmp cache）→ MOCK 降级 → fetch_evidence 无 GFS 证据
        tmp_col = GFSWeatherCollector(
            node=NODE, cycle=DEFAULT_CYCLE, cache_dir=Path(tempfile.mkdtemp()))
        with mock.patch.object(GFSWeatherCollector, "_fetch_raw",
                               side_effect=OSError("offline")), \
             mock.patch("agent.evidence.gfs_forecast._make_collector", return_value=tmp_col):
            evs = fetch_evidence(
                node=NODE, decision_date=DD_SUMMER,
                include_placeholders=False, include_real_sources=True)
        gfs = [e for e in evs if e.get("event_type") == "WEATHER_FORECAST"]
        self.assertEqual(gfs, [])

    def test_asof_time_fields_carried_unchanged(self):
        # Adapter 不重算：Evidence 的初始化/发布/可用/判定 == AsOfRecord 原值
        rec = _asof_dict("06Z", DD_SUMMER)
        with patch_gfs_fetch("06Z"):
            ev = build_gfs_evidence(NODE, DD_SUMMER, cycle="06Z")
        self.assertEqual(ev["published_at"], rec["published_at"])
        self.assertEqual(ev["available_at"], rec["available_at"])
        self.assertEqual(ev["decision_cutoff"], rec["decision_cutoff"])
        self.assertEqual(ev["decision_eligible"], rec["decision_eligible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
