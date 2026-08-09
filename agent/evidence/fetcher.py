# -*- coding: utf-8 -*-
"""
agent/evidence/fetcher.py

Agent Evidence 的获取/整理接口骨架（V0.2 白盒交易决策 Agent · 模块 1/3）。

当前版本【无真实外部数据源】：
  - 所有 fetch 一律输出 directional_effect=UNCERTAIN（宁可未知，不可乱判方向）。
  - 这是正确行为，不是缺陷——本模块只负责"管道与口径"，不负责"编造事实"。

LLM 边界（硬约束）：
  - LLM 可以做：信息抽取、事件分类、摘要、时间线对齐。
  - LLM 禁止做：凭空判断价格/Return 方向；没有真实数据源时禁止编造事件。
  - 任何一条 Evidence 若没有真实、可核实的来源，directional_effect 必须为
    UNCERTAIN 且 confidence 必须为 0（或明确的时间衰减权重，默认 0）。

未来数据源 TODO（按 stage3_lead_summary 的优先排序）：
  1. RENEWABLE_GENERATION   —— CA-ISO 可再生能源出力（尤其夜间/清晨实际+预测，
     供给过剩 → 深度负电价信号）。直接命中本轮最大亏损机制（RTPD 负电价）。
  2. LOAD_FORECAST_REVISION —— 负荷预测实时修正 / 夜间低谷负荷。
  3. OUTAGE_AND_CONSTRAINT  —— 机组停运 / 输电阻塞（RTPD 尖峰类亏损的驱动）。
  4. CAISO_MARKET_NOTICE    —— CAISO 官方公告 / 系统事件通知。
  5. WILDFIRE               —— 山火及输电/负荷影响。
  6. EXTREME_WEATHER        —— 极端天气（热浪/风暴）。
  7. FUEL_PRICE             —— 本地燃气价（长期补充，优先级低于上述）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.evidence.schema import (
    EVIDENCE_KEYS,
    Evidence,
    new_uncertain_evidence,
    validate_evidence,
)

# ---------------------------------------------------------------------------
# 数据源注册表（未来接入真实源时在此登记，并实现 fetch_one 回调）
# ---------------------------------------------------------------------------

#: 每个数据源类型 -> 实现函数签名 fetch(source_cfg, ctx) -> List[Evidence]
#: 当前全部为 None（占位），即"未接入"。任何未接入源 fetch 都返回 UNCERTAIN。
FETCHER_REGISTRY: Dict[str, Optional[str]] = {
    "RENEWABLE_GENERATION": None,   # TODO: 接入 CA-ISO 可再生能源出力（最高优先）
    "LOAD_FORECAST_REVISION": None,  # TODO: 接入负荷预测实时修正
    "OUTAGE_AND_CONSTRAINT": None,   # TODO: 接入机组停运/输电阻塞
    "CAISO_MARKET_NOTICE": None,     # TODO: 接入 CAISO 官方通知
    "WILDFIRE": None,                # TODO: 接入山火数据
    "EXTREME_WEATHER": None,         # TODO: 接入极端天气预警
    "FUEL_PRICE": None,              # TODO: 接入本地燃气价（长期）
    "OTHER": None,
}

#: 未接入数据源的占位说明（写入 summary，供交易员/审计知晓"这条是空的"）
NO_SOURCE_NOTE = (
    "当前无真实数据源接入（fetcher 占位）。该证据方向未知，"
    "directional_effect=UNCERTAIN，不参与任何方向/风控决策。"
)


def _default_sources() -> List[str]:
    """返回需要生成 UNCERTAIN 占位证据的数据源类型。"""
    return list(FETCHER_REGISTRY.keys())


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def fetch_evidence(
    node: str,
    decision_date: str,
    region: Optional[str] = None,
    hours: Optional[List[int]] = None,
    event_types: Optional[List[str]] = None,
    include_placeholders: bool = True,
) -> List[Dict[str, Any]]:
    """获取决策时点之前真实可见的外部证据（接口骨架）。

    Args:
        node:           目标节点，如 "CONTROLX_1_N001"
        decision_date:  决策日期（ISO YYYY-MM-DD，即 bid cutoff 当日）
        region:         区域，默认从 node 前缀推断或留空
        hours:          目标小时列表（仅用于按小时切分事件窗口，默认全部）
        event_types:    只取哪些事件类型；None 表示全部
        include_placeholders: True 时对每个未接入数据源生成一条 UNCERTAIN
                             占位证据（保证决策卡里能看到"有哪些源待接入"）

    Returns:
        标准 Evidence dict 列表。当前版本每条都是 directional_effect=UNCERTAIN。

    TODO(接入真实源后):
        对每个已实现的数据源调用其 fetch 回调，做去重、时间窗口过滤
        （只在 published_at <= decision_date 13:00 的事件才可见）、
        影响节点过滤，然后合并返回。占位逻辑保留给仍未接入的源。
    """
    _ = hours  # 保留参数位，未来按小时切窗用
    results: List[Dict[str, Any]] = []

    if not include_placeholders:
        return results

    for etype in event_types or _default_sources():
        placeholder = new_uncertain_evidence(
            event_type=etype,
            region=region or _region_of(node),
            affected_nodes=[node],
            severity="INFO",
            source="(not-connected)",
            published_at="",
            summary=f"[{etype}] {NO_SOURCE_NOTE}",
            confidence=0.0,
        )
        results.append(placeholder.to_dict())
    return results


def compile_evidence_context(
    node: str,
    decision_date: str,
    hours: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """把证据整理成决策卡可直接展示的上下文。

    - 任何一条 directional_effect != UNCERTAIN 的证据都必须是真实数据源产出；
      当前版本不存在这种证据，所以汇总永远是 UNCERTAIN。
    - 输出含：evidence 列表、方向汇总、数据源接入状态。
    """
    evidences = fetch_evidence(node=node, decision_date=decision_date, hours=hours)

    directional_summary: Dict[str, int] = {}
    for ev in evidences:
        directional_summary[ev["directional_effect"]] = (
            directional_summary.get(ev["directional_effect"], 0) + 1
        )

    connected = {
        k: (v is not None) for k, v in FETCHER_REGISTRY.items()
    }

    return {
        "node": node,
        "decision_date": decision_date,
        "evidence_list": evidences,
        "directional_summary": directional_summary,  # 例: {"UNCERTAIN": 8}
        "any_supported_positive": directional_summary.get("SUPPORT_POSITIVE", 0) > 0,
        "any_supported_negative": directional_summary.get("SUPPORT_NEGATIVE", 0) > 0,
        "has_uncertain": directional_summary.get("UNCERTAIN", 0) > 0,
        "connected_sources": connected,
        "note": (
            "当前版本无真实外部数据源，全部证据 directional_effect=UNCERTAIN。"
            "LLM 仅做整理/摘要，未做任何价格方向判断。"
        ),
    }


def attach_uncertain_evidence(
    node: str,
    decision_date: str,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """给 Case / Decision Card 挂一条"无真实证据"的标准占位记录。

    这是当前版本唯一合法的证据挂载方式：全部 UNCERTAIN、confidence=0。
    """
    return fetch_evidence(
        node=node, decision_date=decision_date, region=region,
        include_placeholders=True,
    )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _region_of(node: str) -> str:
    """从节点 ID 推断区域（节点→zone 映射见 节点位置.xlsx）。"""
    if "CONTROLX" in node or "SNLNDRO" in node:
        return "ZP26"
    if "ELCA" in node:
        return "SP15"
    return "UNKNOWN"


def summarize_evidence_list(evidences: List[Dict[str, Any]]) -> List[str]:
    """把证据列表压缩成一行一句的摘要文本（供决策卡 'Evidence:' 行使用）。"""
    lines = []
    for ev in evidences:
        eff = ev.get("directional_effect", "UNCERTAIN")
        etype = ev.get("event_type", "OTHER")
        src = ev.get("source", "") or "(未接入)"
        lines.append(
            f"[{eff}] {etype} ({src}): {ev.get('summary', '')[:80]}"
        )
    return lines


def assert_no_direction_guess(evidence_list: List[Dict[str, Any]]) -> None:
    """防御性校验：当前版本不允许任何非 UNCERTAIN 证据混入。

    未来接入真实源后，应改为"校验来源真实存在 + published_at 不晚于决策时点"，
    而不是一律拒绝。此函数仅用于守住"无源不判方向"的口径。
    """
    for ev in evidence_list:
        if ev.get("directional_effect") != "UNCERTAIN":
            raise ValueError(
                f"证据来源未接入真实数据，却出现非 UNCERTAIN 方向: {ev}"
            )


if __name__ == "__main__":
    ctx = compile_evidence_context(
        node="CONTROLX_1_N001",
        decision_date="2026-06-29",
        hours=[14],
    )
    for k, v in ctx.items():
        print(f"{k}: {v}")
    print()
    for line in summarize_evidence_list(ctx["evidence_list"][:3]):
        print(line)
