# -*- coding: utf-8 -*-
"""
agent/evidence/gfs_forecast.py —— GFS Evidence Adapter（P0-1 单一事实来源）
============================================================================

本模块是**纯 Adapter**：只做一件事——把 `code/data_acquisition/weather_gfs.py`
（`GFSWeatherCollector`）产出的 **AsOfRecord** 转成 agent/evidence 层的
**Evidence** 结构。**不自行计算任何时间逻辑**：

  - initialization_time / model_run_time : 取自 AsOfRecord.model_run_time
  - published_at                         : 取自 AsOfRecord.published_at（init + 发布延迟模型）
  - available_at                         : 取自 AsOfRecord.available_at（Time Gate 唯一判据）
  - retrieved_at                         : 取自 AsOfRecord.retrieved_at（仅审计）
  - decision_cutoff                      : 取自 AsOfRecord.decision_cutoff（D 10:00 PT → UTC）
  - decision_eligible                    : 以 AsOfRecord 判定为准；Evidence schema 用
                                            available_at 重算，本模块保证二者一致
                                            （见 _finalize_eligible）。

单一 GFS 采集 + 时间判定源 = **`code/data_acquisition/weather_gfs.py`**。
Web（decision card / fetcher）、CLI（mvp_demo / run_acquisition）、Agent
（fetch_evidence）三条路径都经本 Adapter 走同一源 —— 时间字段完全一致。

已废弃（P0-1 消除双实现）：
  * 旧模块自算的 published_at = forecast_issue_time（把 12Z init 当发布时间，
    导致 12Z 错误 eligible）→ 删除；发布时间改由 weather_gfs 延迟模型给出。
  * 旧模块自算的 available_at / decision_eligible → 删除；全部取自 AsOfRecord。
  * fetch_forecast_df / forecast_for_target（旧 Open-Meteo 直连抓取）→ 删除；
    抓取统一走 GFSWeatherCollector（含降级：LIVE > CACHE > MOCK）。
  * 保留 decision_cutoff_utc / forecast_issue_time_utc 仅作**废弃转发**，
    供历史脚本（backtest_v2_ab 等）引用；可用性判定一律走 weather_gfs。

时间口径：所有时间字段 UTC naive ISO（YYYY-MM-DDTHH:MM:SS），与 schemas 一致。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.schemas import (  # noqa: E402
    MODE_BACKTEST,
    NODE_COORDS,
    make_decision_cutoff,
    pt_naive_to_utc_naive,
)
from code.data_acquisition.weather_gfs import (  # noqa: E402
    DEFAULT_CYCLE,
    GFS_CYCLES_UTC,
    GFSWeatherCollector,
)
from agent.evidence.schema import (  # noqa: E402
    Evidence,
    evidence_from_dict,
)

#: 节点 → (纬度, 经度)，单一事实来源转发（schemas.NODE_COORDS）
NODE_COORDS: Dict[str, tuple] = dict(NODE_COORDS)

#: GFS 运行周期（UTC 初始时刻），单一事实来源转发（weather_gfs.GFS_CYCLES_UTC）
GFS_CYCLES_UTC: Dict[str, str] = dict(GFS_CYCLES_UTC)


# ---------------------------------------------------------------------------
# [废弃转发] 旧时间工具（仅兼容历史脚本；可用性判定请走 weather_gfs）
# ---------------------------------------------------------------------------
def _pt_to_utc_naive(local_naive: str) -> str:
    """[已废弃] naive PT → naive UTC。转发 schemas.pt_naive_to_utc_naive。"""
    return pt_naive_to_utc_naive(local_naive) or ""


def decision_cutoff_utc(decision_date: str) -> str:
    """[已废弃] D 10:00 PT → UTC naive。转发 schemas.make_decision_cutoff。

    仅保留供历史脚本（backtest_v2_ab 的 APPROX 证据）引用；cutoff 判定的
    单一事实来源是 schemas.make_decision_cutoff。
    """
    return make_decision_cutoff(decision_date) or ""


def forecast_issue_time_utc(decision_date: str, cycle: str = "12Z") -> str:
    """[已废弃] GFS run 初始时刻（= initialization_time，UTC naive）。

    旧默认 12Z 仅为了与历史脚本行为一致；新单一源默认 cycle 见
    weather_gfs.DEFAULT_CYCLE（06Z）。本函数只算 run 起始，不判可用性。
    """
    cyc = GFS_CYCLES_UTC.get(cycle, GFS_CYCLES_UTC["12Z"])
    return f"{str(decision_date)[:10]}T{cyc}:00"


# ---------------------------------------------------------------------------
# Adapter 核心
# ---------------------------------------------------------------------------
def _make_collector(
    node: str,
    cycle: str,
    mode: str = MODE_BACKTEST,
    network_enabled: bool = True,
) -> GFSWeatherCollector:
    """构造 GFS 采集器（单一事实来源：weather_gfs.GFSWeatherCollector）。"""
    return GFSWeatherCollector(
        node=node, cycle=cycle, mode=mode, network_enabled=network_enabled)


def _region_of(node: str) -> str:
    if "CONTROLX" in node or "SNLNDRO" in node:
        return "ZP26"
    if "ELCA" in node:
        return "SP15"
    return "UNKNOWN"


def _finalize_eligible(ev: Evidence, asof_eligible: bool) -> Evidence:
    """保证 Evidence 重算的 decision_eligible == AsOfRecord 判定（单一事实来源）。

    V0.3.1.5：Schema **不** fallback published_at，available-at-only + Strong Impossibility
    重算天然与 AsOfRecord 一致，此函数仅作为一致性防御；若不一致（不应发生），
    仅清空 published_at（审计字段，不改变 available_at / eligibility 判定）。
    """
    if bool(ev.decision_eligible) != bool(asof_eligible):
        ev.published_at = ""
    return ev


def _aggregate_records(
    records: List[Dict[str, Any]],
    node: str,
    decision_date: str,
    cycle: str,
    source_url: str,
) -> Optional[Evidence]:
    """把同 run 的 AsOfRecord 列表聚合为一条 D+1 预报 Evidence。

    时间字段（published_at / available_at / retrieved_at / decision_cutoff /
    model_run_time）与 decision_eligible 全部取自 AsOfRecord（同 run 的 72 条
    逐小时记录共享同一时间戳），本函数不自行计算。
    """
    if not records:
        return None
    first = records[0]
    target_time = str(first.get("target_time", ""))
    target_date = target_time[:10] or ""

    asof_eligible = bool(first.get("decision_eligible", False))
    published_at = str(first.get("published_at", ""))
    available_at = str(first.get("available_at", ""))
    retrieved_at = str(first.get("retrieved_at", ""))
    decision_cutoff = str(first.get("decision_cutoff", ""))
    model_run_time = str(first.get("model_run_time", "")) or str(first.get("issue_time", ""))
    forecast_run = str(first.get("forecast_run", "")) or f"{decision_date}T{cycle}"

    # V0.3.1.3 available-at-only 收口：available_at 由 AsOfRecord 显式提供
    # （Time Gate 唯一判据）。无可靠 vintage（12Z/18Z BACKTEST）→ available_at
    # 保持空 + 显式标注缺失，防止 schema 从 published 估计值迁移误判可用。
    available_source = ""
    if available_at:
        available_source = "available_at（AsOfRecord）"
    elif published_at:
        available_source = "MISSING（无可靠 vintage，published_at 为估计值不作可用时刻）"

    # 逐变量 24h 完整度 / 均值（仅用决策日 D 的目标日 T = D+1 的记录）
    fractions: List[float] = []
    means: Dict[str, Optional[float]] = {}
    for fn in ("t2m", "wind100", "ssrd"):
        f_recs = [r for r in records if r.get("field_name") == fn]
        if not f_recs:
            means[fn] = None
            fractions.append(0.0)
            continue
        values = [r.get("value") for r in f_recs if r.get("value") is not None]
        n_present = len(values)
        fractions.append(n_present / max(1, len(f_recs)))
        means[fn] = (sum(values) / len(values)) if values else None
    completeness = (sum(fractions) / len(fractions)) if fractions else 0.0

    t2m = means.get("t2m")
    wind = means.get("wind100")
    ssrd = means.get("ssrd")
    summary = (
        f"D+1({target_date}) GFS {cycle} 预报：t2m 均值 "
        f"{t2m if t2m is not None else float('nan'):.1f}°C，wind100 均值 "
        f"{wind if wind is not None else float('nan'):.1f} m/s，ssrd 均值 "
        f"{ssrd if ssrd is not None else float('nan'):.0f} W/m²"
        f"（forecast_issue={model_run_time} UTC，available="
        f"{available_at or 'UNKNOWN(无可靠 vintage)'}，24h 完整度 {completeness * 100:.0f}%）。"
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
        source_url=source_url,
        source_type=str(first.get("source_type", "") or "WEATHER"),
        is_mock=bool(first.get("is_mock", False)),
        raw_source_id=str(first.get("raw_source_id", "")) or f"run={model_run_time}&node={node}",
        published_at=published_at,
        available_at=available_at,
        available_at_source=available_source,
        initialization_time=model_run_time,
        retrieved_at=retrieved_at,
        target_time=target_time,
        decision_cutoff=decision_cutoff,
        summary=summary,
        directional_effect="UNCERTAIN",   # 预报不直接决定 Return 方向（诚实）
        confidence=round(completeness, 3),
    ).normalize()
    return _finalize_eligible(ev, asof_eligible)


def build_gfs_evidence(
    node: str,
    decision_date: str,
    cycle: Optional[str] = None,
    mode: str = MODE_BACKTEST,
    network_enabled: bool = True,
    use_cache: bool = True,
    _collector: Optional[GFSWeatherCollector] = None,
) -> Dict[str, Any]:
    """构建一条 GFS D+1 天气预报 Evidence（as-of，directional_effect=UNCERTAIN）。

    统一 GFS 源：`weather_gfs.GFSWeatherCollector`（Open-Meteo Single Runs）。
    - cycle 缺省 = weather_gfs.DEFAULT_CYCLE（06Z，可严格回测）。
    - 时间字段 / decision_eligible 全部取自 AsOfRecord（见 _aggregate_records）。
    - MOCK 降级（网络失败且无缓存）→ 返回 {}（诚实：不编造天气预报）。
    - 12Z/18Z 回测（无可靠 vintage）→ available_at="" → decision_eligible=FALSE
      （除非 PRODUCTION 模式在 cutoff 前真实拉到，记 retrieved_at）。
    """
    cyc = cycle or DEFAULT_CYCLE
    col = _collector or _make_collector(
        node=node, cycle=cyc, mode=mode, network_enabled=network_enabled)
    res = col.run(str(decision_date)[:10], save=False, use_cache=use_cache)

    if not res.records or res.metadata.get("is_mock"):
        # 网络失败 / 无缓存 / 底层即 MOCK：宁可无证据，不可用假预报冒充
        return {}

    source_url = ""
    try:
        source_url = col._build_url(str(decision_date)[:10])
    except Exception:
        source_url = ""
    ev = _aggregate_records(res.records, node, str(decision_date)[:10], cyc, source_url)
    return ev.to_dict() if ev is not None else {}


def fetch_gfs_weather_evidence(
    node: str,
    decision_date: str,
    hours: Optional[List[int]] = None,
    cycle: Optional[str] = None,
    mode: str = MODE_BACKTEST,
    network_enabled: bool = True,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Fetcher 注册回调：fetch_evidence → 本函数（见 fetcher.py FETCHER_REGISTRY）。

    Args:
        node: 目标节点
        decision_date: 决策日期（ISO YYYY-MM-DD，= target_date − 1）
        hours: 保留参数（按小时切窗），当前证据按整日聚合
        cycle: GFS 运行周期（缺省 = weather_gfs.DEFAULT_CYCLE=06Z）
    Returns:
        标准 Evidence dict 列表；抓取失败 / MOCK 降级返回 []（不编造）。
    """
    _ = hours
    ev = build_gfs_evidence(
        node, decision_date, cycle=cycle, mode=mode,
        network_enabled=network_enabled, use_cache=use_cache)
    return [ev] if ev else []


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    def _sp(x):
        try:
            print(x)
        except Exception:
            print(str(x).encode("ascii", "replace").decode("ascii"))

    for node in ("CONTROLX_1_N001", "SNLNDRO_1_N001", "ELCAJNGT_7_N001"):
        decision_date = "2026-07-08"
        evs = fetch_gfs_weather_evidence(node, decision_date)
        if not evs:
            _sp(f"[{node}] no evidence (fetch failed / MOCK)")
            continue
        ev = evs[0]
        _sp(f"--- {node} decision={decision_date} ---")
        for k in ("evidence_id", "published_at", "available_at", "decision_cutoff",
                  "decision_eligible", "directional_effect", "confidence", "summary"):
            _sp(f"  {k}: {ev.get(k)}")
        from agent.evidence.time_gate import is_decision_eligible
        _sp("  is_decision_eligible(time_gate): " +
            str(is_decision_eligible(evidence_from_dict(ev))))
