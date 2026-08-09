# -*- coding: utf-8 -*-
"""
code/data_acquisition/weather_gfs.py —— GFS 天气预报采集器（Agent E · 真实源）

数据源：Open-Meteo Single Runs API 存档的 NCEP GFS 0.25°（`gfs_global`）。
  - 按 `run=YYYY-MM-DDTHH:00` 取**历史 as-issued 预报 run**（= forecast_issue_time），
    不是 ERA5/再分析反演（as-of 安全，与 agent/evidence/gfs_forecast.py 同源）。
  - 决策日 D，取 D 12Z run（12:00 UTC），目标交付日 T = D+1。
  - 时间戳（全部 UTC naive）：
      published_at = issue_time = D 12:00 UTC
      available_at = 按模式解析（BACKTEST=vintage=published_at；PRODUCTION=max(published,retrieved)）
      decision_cutoff = D 10:00 PT → UTC
      decision_eligible = (available_at <= decision_cutoff)，由 schemas 程序计算。
  - target_time 对齐项目 hour∈1..24（PT）约定：`target_time_pt_to_utc(T, h)`。

降级：网络失败 → 读缓存 raw → 确定性 MOCK（is_mock=True，明确标注，不冒充真实预报）。
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.base import Collector, FetchError, utc_now_naive  # noqa: E402
from code.data_acquisition.schemas import (  # noqa: E402
    AsOfRecord,
    NODE_COORDS,
    target_time_pt_to_utc,
)

#: 节点 → (纬度, 经度)，来源：节点位置.xlsx（与 agent/evidence/gfs_forecast.py 一致）
NODE_COORDS = dict(NODE_COORDS)

#: GFS 运行周期（UTC 初始时刻）；12Z 是 10:00 PT cutoff 前最新一跑
GFS_CYCLES_UTC: Dict[str, str] = {
    "00Z": "00:00", "06Z": "06:00", "12Z": "12:00", "18Z": "18:00",
}
DEFAULT_CYCLE = "12Z"

#: Open-Meteo 变量 → (field_name, unit)，field_name 对齐项目 canonical（t2m/ssrd/wind100）
VAR_MAP: Dict[str, tuple] = {
    "temperature_2m": ("t2m", "°C"),
    "wind_speed_100m": ("wind100", "m/s"),
    "shortwave_radiation": ("ssrd", "W/m²"),
}

_SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
_UA = "caiso-data-acquisition-poc/0.1 (GFS as-of collector)"


class GFSWeatherCollector(Collector):
    """GFS 历史预报采集器（Open-Meteo Single Runs，as-of）。"""

    source_name = "NCEP_GFS_025_via_OpenMeteo"
    not_backtest_safe = False   # GFS Single Runs 提供 as-issued 历史 run，回测安全

    def __init__(
        self,
        node: str = "CONTROLX_1_N001",
        cycle: str = DEFAULT_CYCLE,
        variables: Optional[List[str]] = None,
        cache_dir: Optional[Path] = None,
        mode: str = "BACKTEST",
        network_enabled: bool = True,
    ) -> None:
        super().__init__(cache_dir=cache_dir, mode=mode, network_enabled=network_enabled)
        if node not in NODE_COORDS:
            raise ValueError(f"未知节点 {node!r}（可用: {sorted(NODE_COORDS)}）")
        if cycle not in GFS_CYCLES_UTC:
            raise ValueError(f"未知 cycle {cycle!r}（可用: {sorted(GFS_CYCLES_UTC)}）")
        self.node = node
        self.cycle = cycle
        self.variables = list(variables) if variables else list(VAR_MAP.keys())

    # ------------------------------------------------------------------ 派生
    @property
    def lat_lon(self) -> tuple:
        return NODE_COORDS[self.node]

    def cache_slug(self, query_date: str) -> str:
        return f"{str(query_date)[:10]}_{self.cycle}"

    def run_start_utc(self, query_date: str) -> str:
        """GFS run 初始时刻（= forecast_issue_time，UTC naive）。"""
        return f"{str(query_date)[:10]}T{GFS_CYCLES_UTC[self.cycle]}:00"

    def forecast_run_id(self, query_date: str) -> str:
        return f"{str(query_date)[:10]}T{GFS_CYCLES_UTC[self.cycle]}Z"

    # ------------------------------------------------------------------ fetch
    def _build_url(self, query_date: str) -> str:
        lat, lon = self.lat_lon
        run = f"{str(query_date)[:10]}T{GFS_CYCLES_UTC[self.cycle]}"
        vars_ = ",".join(self.variables)
        return (
            f"{_SINGLE_RUNS_URL}?latitude={lat}&longitude={lon}&run={run}"
            f"&hourly={vars_}&wind_speed_unit=ms&models=gfs_global&timezone=UTC"
        )

    def _fetch_raw(self, query_date: str) -> Dict[str, Any]:
        url = self._build_url(query_date)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
        except Exception as exc:
            raise FetchError(f"Open-Meteo 请求失败 {self.node} {query_date} {self.cycle}: "
                             f"{type(exc).__name__}: {exc}") from exc
        if not data.get("hourly"):
            raise FetchError(f"Open-Meteo 返回无 hourly 数据: {url}")
        data["_request_url"] = url
        return data

    # ------------------------------------------------------------------ normalize
    def _normalize(
        self,
        payload: Dict[str, Any],
        query_date: str,
        *,
        provenance: str,
        is_mock: bool,
        retrieved_at: str,
    ) -> List[AsOfRecord]:
        hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
        times = hourly.get("time", []) or []
        if not times:
            return []
        target_date = self.target_date(query_date)
        # UTC 时刻 → 值（Open-Meteo 数组按 time 对齐；time 可能是 'HH:MM' 16 字符，
        # 统一补秒为 19 字符 'YYYY-MM-DDTHH:MM:SS' 再匹配 target_time）
        def _norm_utc_hour(t: Any) -> str:
            key = str(t)[:19]
            if len(key) == 16 and key[13] == ":":
                key += ":00"
            return key
        by_time: Dict[str, int] = {}
        for i, t in enumerate(times):
            key = _norm_utc_hour(t)
            if key not in by_time:
                by_time[key] = i
        lat, lon = self.lat_lon
        issue_time = self.run_start_utc(query_date)
        published_at = issue_time
        records: List[AsOfRecord] = []
        for var in self.variables:
            if var not in VAR_MAP:
                continue
            field_name, unit = VAR_MAP[var]
            col = hourly.get(var, [None] * len(times)) or [None] * len(times)
            for h in range(1, self.expected_hours + 1):  # 1..24
                tt_utc = target_time_pt_to_utc(target_date, h)
                idx = by_time.get(tt_utc)
                value = None if idx is None else col[idx] if idx < len(col) else None
                rec = self._make_record(
                    query_date,
                    field_name=field_name,
                    target_time=tt_utc or "",
                    value=value,
                    unit=unit,
                    node=self.node,
                    region=_region_of(self.node),
                    latitude=lat,
                    longitude=lon,
                    forecast_run=self.forecast_run_id(query_date),
                    issue_time=issue_time,
                    published_at=published_at,
                    retrieved_at=retrieved_at,
                    raw_source_id=f"run={issue_time}&node={self.node}",
                )
                records.append(rec)
        return records

    # ------------------------------------------------------------------ mock
    def _mock_raw(self, query_date: str) -> Dict[str, Any]:
        """确定性合成 GFS raw（仅离线演示）。季节性 + 日周期，明显非真实，标注 mock。"""
        lat, lon = self.lat_lon
        start = datetime.fromisoformat(self.run_start_utc(query_date))
        n = 168  # 7 天预报
        doy = start.timetuple().tm_yday
        seasonal = 12.0 + 14.0 * math.sin(2 * math.pi * (doy - 80) / 365.0)
        times, t2m, wind, ssrd = [], [], [], []
        for i in range(n):
            t = start + timedelta(hours=i)
            times.append(t.strftime("%Y-%m-%dT%H:%M:%S"))
            hour = t.hour
            t2m.append(round(seasonal + 7.0 * math.sin(2 * math.pi * (hour - 14) / 24.0), 2))
            wind.append(round(4.5 + 2.0 * math.sin(2 * math.pi * hour / 24.0 + 1.0), 2))
            ssrd.append(round(max(0.0, 700.0 * math.sin(2 * math.pi * (hour - 6) / 24.0)), 0))
        return {
            "mock": True,
            "latitude": lat,
            "longitude": lon,
            "timezone": "UTC",
            "hourly_units": {
                "time": "iso8601",
                "temperature_2m": "°C",
                "wind_speed_100m": "m/s",
                "shortwave_radiation": "W/m²",
            },
            "hourly": {
                "time": times,
                "temperature_2m": t2m,
                "wind_speed_100m": wind,
                "shortwave_radiation": ssrd,
            },
        }


def _region_of(node: str) -> str:
    if "CONTROLX" in node or "SNLNDRO" in node:
        return "ZP26"
    if "ELCA" in node:
        return "SP15"
    return ""
