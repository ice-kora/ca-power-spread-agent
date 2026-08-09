# -*- coding: utf-8 -*-
"""
agent/case_library/case.py

Case 结构定义（V0.2 白盒交易决策 Agent · 模块 2/3：Case Library）。

定位声明（重要）：
  Case Library ≠ Rule Engine。
  - Case Library：只做"历史检索"——回答"历史上有没有类似情况、当时发生了什么、
    为什么对/错、教训是什么"。它【不包含任何决策规则】，不输出方向判断。
  - Rule Engine：由独立模块负责，做方向/风控的判定。
  本模块严禁写入"if spread>X then SELL"这类规则；经验教训以文本形式记录，
  供交易员/后续模块人工参考，而不是被程序直接执行。

字段（对齐团队口径，全部为决策时点 as-of 可见信息 + 事后结算信息）：
  case_id            案例编号
  decision_date      决策日期（bid cutoff 当日，ISO YYYY-MM-DD）
  node               节点 ID
  hour               小时 1~24
  model_prediction   模型在决策时点给出的建议动作
                     （BUY_DA / SELL_DA / NO_TRADE）
  expected_return    模型期望 Return（$/MWh）
  confidence         模型置信度（0~1；注意本项目置信度未校准，仅作记录）
  available_information_at_decision_time
                     决策时点真实可见的信息快照（dict：特征/历史统计等）
  event_evidence     Agent Evidence 列表（当前版本全部 UNCERTAIN）
  human_decision     交易员实际/应做决策的记录（事实性描述）
  actual_DA / actual_RTPD / actual_Return
                     目标日实际价格（仅事后结算用，禁止作决策输入）
  PnL                实际盈亏（$/MWh，1 MWh/仓）
  why_correct_or_wrong
                     为什么对/错的机制解释（来源：stage3 事件分析）
  lessons            教训列表（文本，供人工参考）
  related_rules      相关风控/分析规则的编号（如 R7a / Type A），仅作引用
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.market_rules import (  # noqa: E402
    CURRENT_MARKET_RULE_VERSION,
    market_rule_version_for,
    normalize_market_rule_version,
)

#: 建议动作三态（与 business_contract §5 一致）
ACTIONS: tuple = ("BUY_DA", "SELL_DA", "NO_TRADE")


@dataclass
class Case:
    case_id: str
    decision_date: str
    node: str
    hour: int
    model_prediction: str
    expected_return: float
    confidence: float
    available_information_at_decision_time: Dict[str, Any] = field(default_factory=dict)
    event_evidence: List[Dict[str, Any]] = field(default_factory=list)
    human_decision: str = ""
    actual_DA: float = None  # type: ignore  # 事后结算，可能缺失
    actual_RTPD: float = None  # type: ignore
    actual_Return: float = None  # type: ignore
    PnL: float = None  # type: ignore
    why_correct_or_wrong: str = ""
    lessons: List[str] = field(default_factory=list)
    related_rules: List[str] = field(default_factory=list)

    # -- V0.2 时间字段（CaseGenerationPolicy，防 Case 穿越）----------------
    # case_created_at      生成时刻（结算后可生成）
    # case_available_at    最早可被未来决策检索的时刻（严格 as-of；缺失=不可检索）
    # review_completed_at  人工/Agent 复核完成时刻（未复核为空字符串）
    case_created_at: str = ""
    case_available_at: str = ""
    review_completed_at: str = ""

    # -- Provenance / 版本标记 ----------------------------------------------
    # market_rule_version  该 Case 对应交易所在的市场规则版本（DAME/EDAM 标记，本轮仅保存）
    market_rule_version: str = CURRENT_MARKET_RULE_VERSION

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 json.dump 的 dict（None 保留，便于区分"缺失"与 0）。"""
        d = asdict(self)
        d["market_rule_version"] = normalize_market_rule_version(self.market_rule_version)
        # 把嵌套 Evidence dataclass（若有）转成 dict
        if isinstance(self.event_evidence, (list, tuple)):
            d["event_evidence"] = [
                ev.to_dict() if hasattr(ev, "to_dict") else ev
                for ev in self.event_evidence
            ]
        return d

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "Case":
        """从 dict 重建 Case（字段缺失给默认值）。

        market_rule_version（V0.3.1.2 date-aware 单源）：raw 无该字段时按
        decision_date 用 market_rule_version_for 推断（边界 2026-05-01 →
        POST_DAME_EDAM_2026），保证 2026-05-01+ 案例标 POST；raw 有显式标注则保留。
        仅影响 provenance 标签，不改变任何交易/结算字段。
        """
        raw_mrv = str(raw.get("market_rule_version", "") or "").strip()
        dd = str(raw.get("decision_date", ""))[:10]
        if raw_mrv:
            mrv = normalize_market_rule_version(raw_mrv)
        elif dd:
            mrv = market_rule_version_for(dd)
        else:
            mrv = CURRENT_MARKET_RULE_VERSION
        return Case(
            case_id=str(raw.get("case_id", "")),
            decision_date=str(raw.get("decision_date", "")),
            node=str(raw.get("node", "")),
            hour=int(raw.get("hour", -1)),
            model_prediction=str(raw.get("model_prediction", "")),
            expected_return=float(raw.get("expected_return", 0.0) or 0.0),
            confidence=float(raw.get("confidence", 0.0) or 0.0),
            available_information_at_decision_time=raw.get(
                "available_information_at_decision_time", {}
            ),
            event_evidence=raw.get("event_evidence", []),
            human_decision=str(raw.get("human_decision", "")),
            actual_DA=raw.get("actual_DA"),
            actual_RTPD=raw.get("actual_RTPD"),
            actual_Return=raw.get("actual_Return"),
            PnL=raw.get("PnL"),
            why_correct_or_wrong=str(raw.get("why_correct_or_wrong", "")),
            lessons=list(raw.get("lessons", [])),
            related_rules=list(raw.get("related_rules", [])),
            case_created_at=str(raw.get("case_created_at", "")),
            case_available_at=str(raw.get("case_available_at", "")),
            review_completed_at=str(raw.get("review_completed_at", "")),
            market_rule_version=mrv,
        )

    # ------------------------------------------------------------------
    def short_summary(self) -> str:
        """一行摘要，供检索/列表展示。"""
        return (
            f"{self.case_id} | {self.decision_date} | {self.node} H{self.hour} | "
            f"{self.model_prediction} | exp={self.expected_return:+.1f} "
            f"conf={self.confidence:.2f} | actual={self.actual_Return if self.actual_Return is not None else float('nan'):+.1f} "
            f"PnL={self.PnL if self.PnL is not None else float('nan'):+.1f}"
        )
