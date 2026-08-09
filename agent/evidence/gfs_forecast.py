# -*- coding: utf-8 -*-
"""
agent/evidence/gfs_forecast.py

Evidence 数据源接入（V0.2 真实 as-of 数据源）：NCEP GFS 历史预报档案。

选型结论（docs/evidence_source_v021.md 详述）：
  - **唯一真实历史 as-of 数据源**：Open-Meteo Single Runs API 存档的 NCEP GFS
    0.25°（`ncep_gfs025`）历史运行。提供每个模型 run（= forecast_issue_time）
    的逐小时 D+1 天气预报（temperature_2m / wind_speed_100m / shortwave_radiation）。
  - **as-of 语义**：对目标日 T（= decision_date + 1），取 decision_date 当天的
    GFS 12Z run（12:00 UTC）。decision_cutoff = decision_date 10:00 PT
    （夏令时 = 17:00 UTC，冬令时 = 18:00 UTC）。因 12:00 UTC 早于 cutoff，
    forecast_issue_time <= decision_cutoff 恒成立（12Z 数据实际 ~15:30 UTC
    发布，仍早于 17:00 UTC cutoff）——详见 docs 的发布时间口径一节。
  - **不伪造**：若 API 失败/无该 run，返回空列表（无证据），绝不编造。
    directional_effect 恒为 UNCERTAIN（预报本身不直接决定 Return 方向）。

注意（时间口径）：
  - 本模块输出的 published_at / decision_cutoff 一律为 **UTC naive** 字符串，
    与 project 其它模块的 PT naive 口径不同，使用时须显式区分（决策时点换算见
    `_pt_to_utc_naive`）。Evidence.decision_eligible 在本 Evidence 内自洽。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from agent.evidence.schema import Evidence, parse_timestamp  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
#: 节点 → (纬度, 经度)，来源：节点位置.xlsx
NODE_COORDS: Dict[str, tuple] = {
    "SNLNDRO_1_N001": (37.71123744, -122.1488067),
    "CONTROLX_1_N001": (37.342839, -118.471988),
    "ELCAJNGT_7_N001": (32.79534613, -116.9723386),
}

#: GFS 运行周期（UTC 初始时刻）；12Z 是 10:00 PT cutoff 前最新一跑
GFS_CYCLES_UTC: Dict[str, str] = {"00Z": "00:00", "06Z": "06:00", "12Z": "12:00", "18Z": "18:00"}
DEFAULT_CYCLE: str = "12Z"

#: 从 API 取的天气变量（对齐 project 的 t2m_c / wind100 / ssrd_wm2）
FORECAST_VARIABLES: tuple = ("temperature_2m", "wind_speed_100m", "shortwave_radiation")

_SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
_UA = "caiso-evidence-agent/0.2 (as-of GFS forecast fetcher)"


# ---------------------------------------------------------------------------
# 时间工具（PT → UTC naive）
# ---------------------------------------------------------------------------
def _pt_to_utc_naive(local_naive: str) -> str:
    """把 naive PT（如 '2026-07-08T10:00:00'）换算成 naive UTC（ISO 字符串）。

    使用 America/Los_Angeles 时区；zoneinfo 不可用时回退 DST 启发式
    （3 月第 2 周日 ~ 11 月第 1 周日 = PDT/UTC−7，其余 = PST/UTC−8）。
    """
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(str(local_naive)[:19]).replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        pass
    d = datetime.fromisoformat(str(local_naive)[:19])
    year = d.year
    # 3 月第 2 周日（>=8）与 11 月第 1 周日（1~7）
    def _nth_sunday(y, month, n):
        first = date(y, month, 1)
        offset = (6 - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    start_dst = datetime.combine(_nth_sunday(year, 3, 2), datetime.min.time())
    end_dst = datetime.combine(_nth_sunday(year, 11, 1), datetime.min.time())
    offset_h = 7 if start_dst <= d < end_dst else 8
    return (d - timedelta(hours=offset_h)).strftime("%Y-%m-%dT%H:%M:%S")


def decision_cutoff_utc(decision_date: str) -> str:
    """decision_date 10:00 PT → UTC（Evidence.decision_cutoff 用，naive UTC）。"""
    return _pt_to_utc_naive(f"{str(decision_date)[:10]}T10:00:00")


def forecast_issue_time_utc(decision_date: str, cycle: str = DEFAULT_CYCLE) -> str:
    """GFS run 初始时刻 = forecast_issue_time（naive UTC）。"""
    return f"{str(decision_date)[:10]}T{GFS_CYCLES_UTC.get(cycle, GFS_CYCLES_UTC[DEFAULT_CYCLE])}:00"


# ---------------------------------------------------------------------------
# 数据抓取
# ---------------------------------------------------------------------------
def _coord_of(node: str) -> Optional[tuple]:
    return NODE_COORDS.get(node)


def fetch_forecast_df(
    node: str,
    decision_date: str,
    cycle: str = DEFAULT_CYCLE,
    variables: Optional[List[str]] = None,
    timeout: int = 60,
) -> pd.DataFrame:
    """抓取 GFS 指定 run 的逐小时预报（含 forecast 前 7 天，供目标日切窗）。

    Returns:
        空 DataFrame 表示该 run 不可得/请求失败（不伪造）。
    """
    coord = _coord_of(node)
    if coord is None:
        return pd.DataFrame()
    lat, lon = coord
    cyc = GFS_CYCLES_UTC.get(cycle, GFS_CYCLES_UTC[DEFAULT_CYCLE])
    run = f"{str(decision_date)[:10]}T{cyc}"
    vars_ = "&".join(f"hourly={v}" for v in (variables or list(FORECAST_VARIABLES)))
    # wind_speed_unit=ms：与 project wind100 (m/s) 口径一致
    url = (
        f"{_SINGLE_RUNS_URL}?latitude={lat}&longitude={lon}"
        f"&run={run}&{vars_}&wind_speed_unit=ms&models=gfs_global&timezone=UTC"
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:  # 网络/HTTP/解析失败 → 空（诚实，不编造）
        print(f"[gfs_forecast] fetch failed for {node} {decision_date} {cycle}: {type(exc).__name__}: {exc}")
        return pd.DataFrame()
    h = data.get("hourly", {})
    if not h or "time" not in h:
        return pd.DataFrame()
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"])
    return df


def forecast_for_target(
    node: str,
    target_date: str,
    cycle: str = DEFAULT_CYCLE,
) -> pd.DataFrame:
    """取 target_date 当天 24 小时的 GFS 预报（decision_date = target_date − 1）。"""
    d = pd.to_datetime(str(target_date)[:10])
    decision_date = (d - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = fetch_forecast_df(node, decision_date, cycle=cycle)
    if df.empty:
        return df
    t0 = d.strftime("%Y-%m-%d")
    out = df[df["time"].dt.strftime("%Y-%m-%d") == t0].reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Evidence 构建
# ---------------------------------------------------------------------------
def build_gfs_evidence(
    node: str,
    decision_date: str,
    cycle: str = DEFAULT_CYCLE,
) -> Dict[str, Any]:
    """构建一条 GFS D+1 天气预报 Evidence（as-of，directional_effect=UNCERTAIN）。

    时间字段（均 naive UTC）：
      published_at       = forecast_issue_time = decision_date 12Z（12:00 UTC）
      decision_cutoff    = decision_date 10:00 PT → UTC
      decision_eligible  = 程序计算（published_at <= decision_cutoff）
    """
    d = pd.to_datetime(str(decision_date)[:10])
    target_date = (d + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = forecast_for_target(node, target_date, cycle=cycle)
    if df.empty:
        return {}

    pub = forecast_issue_time_utc(decision_date, cycle)
    cutoff = decision_cutoff_utc(decision_date)

    # 数据完整性置信度（证据质量分，非价格概率）
    fractions = []
    for v in FORECAST_VARIABLES:
        col = df[v] if v in df.columns else pd.Series([None] * len(df))
        n_ok = int(col.notna().sum())
        fractions.append(n_ok / max(1, len(df)))
    completeness = sum(fractions) / len(fractions)

    def _mean(v):
        if v in df.columns:
            s = df[v].dropna()
            return float(s.mean()) if len(s) else None
        return None

    t2m = _mean("temperature_2m")
    wind = _mean("wind_speed_100m")
    ssrd = _mean("shortwave_radiation")

    summary = (
        f"D+1({target_date}) GFS {cycle} 预报：t2m 均值 "
        f"{t2m if t2m is not None else float('nan'):.1f}°C，wind100 均值 "
        f"{wind if wind is not None else float('nan'):.1f} m/s，ssrd 均值 "
        f"{ssrd if ssrd is not None else float('nan'):.0f} W/m²"
        f"（forecast_issue={pub} UTC，24h 完整度 {completeness*100:.0f}%）。"
    )

    ev = Evidence(
        evidence_id=f"GFS-{cycle}-{decision_date}-{node}",
        event_type="WEATHER_FORECAST",
        region=_region_of(node),
        affected_nodes=[node],
        event_start_time=f"{target_date}T00:00:00",
        event_end_time=f"{target_date}T23:00:00",
        severity="INFO",
        source="Open-Meteo Single Runs API (NCEP GFS 0.25°, gfs_global)",
        source_url=(
            f"{_SINGLE_RUNS_URL}?latitude={_coord_of(node)[0]}&longitude={_coord_of(node)[1]}"
            f"&run={forecast_issue_time_utc(decision_date, cycle)}&hourly=temperature_2m,"
            f"wind_speed_100m,shortwave_radiation&models=gfs_global&timezone=UTC"
        ),
        published_at=pub,
        retrieved_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        decision_cutoff=cutoff,
        summary=summary,
        directional_effect="UNCERTAIN",  # 预报不直接决定 Return 方向（诚实）
        confidence=round(completeness, 3),
    )
    return ev.to_dict()


def fetch_gfs_weather_evidence(
    node: str,
    decision_date: str,
    hours: Optional[List[int]] = None,
    cycle: str = DEFAULT_CYCLE,
) -> List[Dict[str, Any]]:
    """Fetcher 注册回调：fetch_evidence → 本函数（见 fetcher.py FETCHER_REGISTRY）。

    Args:
        node: 目标节点
        decision_date: 决策日期（ISO YYYY-MM-DD，= target_date − 1）
        hours: 保留参数（按小时切窗），当前证据按整日聚合
        cycle: GFS 运行周期（默认 12Z）
    Returns:
        标准 Evidence dict 列表；抓取失败返回 []（不编造）。
    """
    _ = hours
    ev = build_gfs_evidence(node, decision_date, cycle=cycle)
    return [ev] if ev else []


def _region_of(node: str) -> str:
    if "CONTROLX" in node or "SNLNDRO" in node:
        return "ZP26"
    if "ELCA" in node:
        return "SP15"
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys
    from agent.evidence.time_gate import is_decision_eligible

    def _sp(x):
        """控制台安全打印（GBK 控制台对部分字符会炸）。"""
        try:
            print(x)
        except Exception:
            print(str(x).encode("ascii", "replace").decode("ascii"))

    for node in ("CONTROLX_1_N001", "SNLNDRO_1_N001", "ELCAJNGT_7_N001"):
        decision_date = "2026-07-08"
        evs = fetch_gfs_weather_evidence(node, decision_date)
        if not evs:
            _sp(f"[{node}] no evidence (fetch failed)")
            continue
        ev = evs[0]
        _sp(f"--- {node} decision={decision_date} ---")
        for k in ("evidence_id", "published_at", "decision_cutoff", "decision_eligible",
                  "directional_effect", "confidence", "summary"):
            _sp(f"  {k}: {ev.get(k)}")
        _sp("  is_decision_eligible(time_gate): " +
            str(is_decision_eligible(ev)))
