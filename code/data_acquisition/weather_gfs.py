# -*- coding: utf-8 -*-
"""
code/data_acquisition/weather_gfs.py —— GFS 天气预报采集器（Agent E · 真实源）

数据源：Open-Meteo Single Runs API 存档的 NCEP GFS 0.25°（`gfs_global`）。
  - 按 `run=YYYY-MM-DDTHH:00` 取**历史 as-issued 预报 run**（= forecast_issue_time），
    不是 ERA5/再分析反演（as-of 安全）。
  - 决策日 D，默认取 D 06Z run（06:00 UTC；00Z/06Z 可回测），目标交付日 T = D+1。

**P0-1 单一事实来源**：本模块是 GFS 采集 + 时间判定的唯一实现。
  `agent/evidence/gfs_forecast.py` 已降级为**纯 Adapter**（把本模块的 AsOfRecord
  转成 agent/evidence 的 Evidence），不再自行计算 published/available/eligible。
  Web / CLI / Agent 三条路径均经该 Adapter 走本模块。

五个时间概念（P0-1，全部 UTC naive；字段名保留英文）
------------------------------------------------------
  model_run_time      : 起报时刻（run 起始，= initialization_time）
  initialization_time : = model_run_time（代码中 issue_time / model_run_time 字段承载）
  available_at        : 该 run 数据**真正可用**的时刻 = init + 发布延迟模型
                        （BACKTEST 用保守上界 +6h；PRODUCTION 用 max(published, retrieved)）
  retrieved_at        : 本次抓取时刻（墙钟，仅审计）
  decision_cutoff     : D 10:00 PT → UTC（DAM Market Close / bid cutoff）

  **Time Gate 只判 available_at <= decision_cutoff**，绝不判 initialization_time <= cutoff
  （GFS 12Z init=12:00 UTC 恒早于 cutoff，但发布 ~15:30–18:00 UTC，init ≠ 可用时刻）。

GFS 发布延迟模型（文档化来源 weather_forecast_sources.md §2.2 / evidence_source_v021.md §2）
------------------------------------------------------------------------------------------
  NCEP 惯例：首文件 ~3h15m、完整 ~4–4.5h；Open-Meteo 文档 "global models 通常 4–6 h 发布"。
    GFS_PUBLISH_LAG_TYPICAL_H = 4.0   PRODUCTION published_at 用（典型延迟估计）
    GFS_PUBLISH_LAG_CEILING_H = 6.0   BACKTEST available_at 用（保守上界，可证明性）

可回测（有可靠 vintage）分类（同 weather_forecast_sources.md 结论）：
  00Z / 06Z  → 保守上界 init+6h 仍严格早于 cutoff → backtest_eligible = TRUE
  12Z        → 上界 18:00 UTC：夏=cutoff 之后、冬=cutoff 边界 → 无法可靠证明 → FALSE
  18Z        → init 在 cutoff 之后 → FALSE
  （MVP：① 有可靠 vintage→用；② 无可靠 vintage→available_at=None→不进历史决策；
   ③ PRODUCTION/Shadow→真实当前抓取并记 retrieved_at。不为让 Demo 有天气制造历史穿越。）

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
    MODE_BACKTEST,
    MODE_PRODUCTION,
    AsOfRecord,
    NODE_COORDS,
    parse_timestamp,
    target_time_pt_to_utc,
)

#: 节点 → (纬度, 经度)，来源：节点位置.xlsx（与 agent/evidence/gfs_forecast.py 一致）
NODE_COORDS = dict(NODE_COORDS)

#: GFS 运行周期（UTC 初始时刻）
GFS_CYCLES_UTC: Dict[str, str] = {
    "00Z": "00:00", "06Z": "06:00", "12Z": "12:00", "18Z": "18:00",
}
#: 默认 cycle：06Z（保守上界 init+6h=12:00 UTC 仍严格早于 cutoff；12Z 与 cutoff 临界，不可回测）
DEFAULT_CYCLE = "06Z"

#: GFS 发布延迟（小时；来源 weather_forecast_sources.md §2.2 / evidence_source_v021.md §2）：
#: NCEP 惯例首文件 ~3h15m、完整 ~4–4.5h；Open-Meteo 文档 "global models 通常 4–6 h 发布"。
GFS_PUBLISH_LAG_TYPICAL_H: float = 4.0    # 典型：PRODUCTION published_at
GFS_PUBLISH_LAG_CEILING_H: float = 6.0    # 保守上界：BACKTEST available_at（可证明）

#: 可回测（有可靠 vintage）的 cycle：保守上界 init+6h 仍严格早于 cutoff。
#:   00Z → ≤06:00 UTC；06Z → ≤12:00 UTC（cutoff 17:00 夏 / 18:00 冬 UTC）→ 均可证明。
#:   12Z → 上界 18:00 UTC，夏=cutoff 之后、冬=cutoff 边界 → 无法可靠证明（不自行推测）。
#:   18Z → init 在 cutoff 之后 → 不可回测。
GFS_BACKTEST_SAFE_CYCLES: tuple = ("00Z", "06Z")

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

    # ---------------------------------------------------------- 时间概念拆分（P0-1）
    def model_run_time_utc(self, query_date: str) -> str:
        """模型起报时刻（run 起始，UTC naive）= initialization_time。"""
        return self.run_start_utc(query_date)

    def initialization_time_utc(self, query_date: str) -> str:
        """= model_run_time_utc（同义；任务要求显式拆分并文档化）。"""
        return self.model_run_time_utc(query_date)

    def published_at_utc(self, query_date: str) -> str:
        """GFS 发布时刻估计（UTC naive）= init + 发布延迟模型。

        PRODUCTION : init + GFS_PUBLISH_LAG_TYPICAL_H（源方典型发布延迟的估计）
        BACKTEST   : init + GFS_PUBLISH_LAG_CEILING_H（保守上界；审计与安全判定用）
        """
        init_dt = datetime.fromisoformat(self.run_start_utc(query_date))
        lag = (GFS_PUBLISH_LAG_TYPICAL_H if self.mode == MODE_PRODUCTION
               else GFS_PUBLISH_LAG_CEILING_H)
        return (init_dt + timedelta(hours=lag)).strftime("%Y-%m-%dT%H:%M:%S")

    def backtest_eligible_for_cycle(self, query_date: str) -> bool:
        """该 run 是否有可靠历史 vintage 可进严格 as-of 回测。

        仅当 cycle ∈ GFS_BACKTEST_SAFE_CYCLES 且保守上界 published_at <= decision_cutoff。
        12Z 冬令时恰逢边界（上界 18:00 == 冬 cutoff 18:00）——边界不视为"可靠证明"，
        故显式只允许 00Z / 06Z（不自行推测更短发布延迟）。
        """
        if self.mode == MODE_PRODUCTION:
            return False
        if self.cycle not in GFS_BACKTEST_SAFE_CYCLES:
            return False
        pub = parse_timestamp(self.published_at_utc(query_date))
        cutoff = parse_timestamp(self.decision_cutoff(query_date))
        if pub is None or cutoff is None:
            return False
        return pub <= cutoff

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
        issue_time = self.run_start_utc(query_date)   # = model_run_time = initialization_time
        model_run_time = issue_time
        published_at = self.published_at_utc(query_date)  # init + 发布延迟模型（≠ init）
        # 回测可用性：cycle 无可靠 vintage（12Z/18Z）→ available_at 不解析（None）
        # → time_eligible=backtest_eligible=FALSE，绝不进历史决策（不自行推测发布时刻）
        forced_available_at: Optional[str] = None
        if self.mode == MODE_BACKTEST and self.cycle not in GFS_BACKTEST_SAFE_CYCLES:
            forced_available_at = ""
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
                    model_run_time=model_run_time,
                    issue_time=issue_time,
                    published_at=published_at,
                    available_at=forced_available_at,
                    retrieved_at=retrieved_at,
                    raw_source_id=f"run={issue_time}&node={self.node}",
                    is_mock=bool(is_mock),
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
