# -*- coding: utf-8 -*-
"""
agent/explanation/decision_card.py

Decision Card（结构化决策卡片）—— V0.2 白盒交易决策 Agent · 模块 3/3。

一张卡 = 某 node × hour × 目标日 在决策时点可生成的全部解释性信息，
给交易员/审计看，不给模型看。内容分五块：

  1. 建议动作（Decision）    来自 Rule Engine / 模型输出（本模块只引用，不产生）
  2. 主要量化依据（依据）    决策时点可见的特征/历史统计（as-of，只读）
  3. Agent Evidence（证据）  外部证据上下文（当前版本全部 UNCERTAIN）
  4. Risk Gate 结果（风控）  引用已校准规则（R7a/R6/R4 警告），不重复发明
  5. 主要风险 + 人工确认     交易员最终把关

边界（与 business_contract 一致）：
  - 卡片内所有数值必须来自决策时点可见信息或模型输出；
  - 不写入任何目标日实际价格（actual_*）作为"依据"；
  - 不判方向——建议动作来自上游，本模块只负责"组织 + 解释"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 风险门状态（与 risk_gate_design.md 的 reason_code 对齐）
# ---------------------------------------------------------------------------

GATE_STATUS_PASS = "PASS"
GATE_STATUS_WARNING = "PASS_WITH_WARNING"
GATE_STATUS_REJECT = "REJECT"

#: 已知 reason_code（本版本实际会出现的；V0.2 正式 Risk Gate 见 code/risk_gate/rules.py）
KNOWN_REASON_CODES = (
    "BUY_ON_POSITIVE_DRIFT_NODE",  # R7a: CONTROLX 正漂移 + BUY
    "LOW_SAMPLE_SUPPORT",          # R6: hist_n < 200（cold-start）
    "EXTREME_TAIL_NODE",           # R4 降级：已知重尾节点（警告级）
    "NONE",
)


@dataclass
class RiskGateResult:
    """Risk Gate 对单笔候选交易的结果（引用已校准规则，非新发明）。"""

    status: str = GATE_STATUS_PASS
    reasons: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionCard:
    """一张决策卡片（字段全部为决策时点可生成信息）。"""

    card_id: str = ""
    node: str = ""
    decision_date: str = ""      # 决策日期（bid cutoff 当日，D-1）
    target_date: str = ""        # 目标交易日（D+1，与决策日期区别开）
    hour: int = -1
    model_source: str = "rule"   # 预测来源（rule / interpretable / catboost；仅 benchmark/验证，线上生产模型 = predictions_v2）
    suggested_action: str = "NO_TRADE"  # SELL_DA / BUY_DA / NO_TRADE
    expected_return: float = 0.0
    confidence: float = 0.0
    # 主要量化依据（每项为一行中文说明，含数字与来源特征名）
    key_quantitative_basis: List[str] = field(default_factory=list)
    # Agent Evidence 上下文（evidence/fetcher.py 输出）
    agent_evidence: Dict[str, Any] = field(default_factory=dict)
    # Risk Gate 结果
    risk_gate: RiskGateResult = field(default_factory=RiskGateResult)
    # 主要风险（中文说明列表）
    main_risks: List[str] = field(default_factory=list)
    # 最终建议（文本，人工可读）
    final_recommendation: str = ""
    # 人工确认提示（默认）
    human_confirmation_note: str = "最终执行由交易员确认"

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["risk_gate"] = self.risk_gate.to_dict()
        return d

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "DecisionCard":
        rg = raw.get("risk_gate", {}) or {}
        return DecisionCard(
            card_id=str(raw.get("card_id", "")),
            node=str(raw.get("node", "")),
            decision_date=str(raw.get("decision_date", "")),
            target_date=str(raw.get("target_date", "")),
            hour=int(raw.get("hour", -1)),
            model_source=str(raw.get("model_source", "rule")),
            suggested_action=str(raw.get("suggested_action", "NO_TRADE")),
            expected_return=float(raw.get("expected_return", 0.0) or 0.0),
            confidence=float(raw.get("confidence", 0.0) or 0.0),
            key_quantitative_basis=list(raw.get("key_quantitative_basis", [])),
            agent_evidence=raw.get("agent_evidence", {}),
            risk_gate=RiskGateResult(
                status=str(rg.get("status", GATE_STATUS_PASS)),
                reasons=list(rg.get("reasons", [])),
                note=str(rg.get("note", "")),
            ),
            main_risks=list(raw.get("main_risks", [])),
            final_recommendation=str(raw.get("final_recommendation", "")),
            human_confirmation_note=str(
                raw.get("human_confirmation_note", "最终执行由交易员确认")
            ),
        )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def format_card_markdown(card: DecisionCard) -> str:
    """把一张卡渲染成人类可读的 md 文本块（决策卡预览用）。"""
    lines = []
    node_short = card.node.replace("_1_N001", "")
    act = card.suggested_action
    lines.append(
        f"### {card.node} · HE{card.hour} · {card.target_date} "
        f"(决策日 {card.decision_date}) · source={card.model_source}"
    )
    lines.append(
        f"Node {node_short} · HE{card.hour} · Expected {card.expected_return:+.1f} "
        f"· Confidence {card.confidence:.2f} · Decision {act}"
    )
    if card.key_quantitative_basis:
        lines.append("依据: " + "；".join(card.key_quantitative_basis))
    else:
        lines.append("依据: （无量化依据可组织）")

    # Evidence 块（无真实源时压缩为一行摘要，细节保留在 JSON）
    ev_list = card.agent_evidence.get("evidence_list", []) if card.agent_evidence else []
    if ev_list:
        all_uncertain = all(
            ev.get("directional_effect", "UNCERTAIN") == "UNCERTAIN" for ev in ev_list
        )
        if all_uncertain:
            etypes = sorted({ev.get("event_type", "OTHER") for ev in ev_list})
            lines.append(
                "Evidence: 全部 UNCERTAIN（{} 类数据源未接入：{}）；"
                "未发现可核实的外部事件".format(len(ev_list), ", ".join(etypes))
            )
        else:
            ev_lines = [
                f"[{ev.get('directional_effect','UNCERTAIN')}] "
                f"{ev.get('event_type','OTHER')}({ev.get('source') or '未接入'}): "
                f"{ev.get('summary','')[:70]}"
                for ev in ev_list
            ]
            lines.append("Evidence: " + " | ".join(ev_lines))
    else:
        lines.append("Evidence: 未发现可核实的外部事件（全部 UNCERTAIN）")

    rg = card.risk_gate
    status_txt = rg.status
    if rg.reasons:
        status_txt += f"({','.join(rg.reasons)})"
    lines.append(f"RiskGate: {status_txt}")
    if rg.note:
        lines.append(f"  RiskGate 说明: {rg.note}")

    if card.main_risks:
        lines.append("风险: " + "；".join(card.main_risks))

    lines.append(f"建议: {card.final_recommendation}")
    lines.append(f"注意: {card.human_confirmation_note}")
    return "\n".join(lines)


def cards_to_json(cards: List[DecisionCard], out_path: Path, meta: Optional[Dict[str, Any]] = None) -> None:
    """把卡片列表写为 JSON（含 meta 说明）。"""
    payload = {
        "meta": meta or {
            "module": "agent/explanation",
            "description": "Decision Card 历史预览（test 窗口）",
        },
        "cards": [c.to_dict() for c in cards],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def cards_to_markdown_preview(cards: List[DecisionCard], out_path: Path, header: str = "") -> None:
    """把卡片列表写为一个 md 预览文件（决策卡人工审阅用）。"""
    lines = [header, ""] if header else []
    for i, c in enumerate(cards, start=1):
        lines.append(format_card_markdown(c))
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
