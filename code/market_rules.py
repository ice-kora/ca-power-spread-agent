# -*- coding: utf-8 -*-
"""
code/market_rules.py —— 市场规则版本标记（Provenance + 版本工程 · Agent C）

单一事实来源：CAISO 市场规则版本（`market_rule_version`）标记。

用途
----
在 Decision Context / Backtest Record / Decision Card / Post-trade Review 上
标注"这笔决策 / 回测 / 复盘是在哪套市场规则下做出的"，防止 CAISO 市场改革
（DAME / EDAM）落地后把新旧口径的记录混在一起、产生不可复现的结论。

取值
----
  PRE_DAME_EDAM_2026  现行 Day-Ahead Market Engine（DAME / EDAM 之前的
                      legacy DAM + RTPD/FMM 口径）—— 本项目当前全部数据
                      采集 / 对账 / 回测均按此口径（见 docs/settlement_scope.md
                      与 docs/company_vs_official_reconciliation.md）。
  POST_DAME_EDAM_2026 未来 DAME（Day-Ahead Market Enhancements）/ EDAM
                      （Extended Day-Ahead Market）上线后的新市场规则。
                      本轮**只保存版本标记，不开发 DAME/EDAM 适配逻辑**（MVP 边界）。

用法
----
    from code.market_rules import CURRENT_MARKET_RULE_VERSION
    card.market_rule_version = CURRENT_MARKET_RULE_VERSION

凡进入模型 / Risk Gate 的字段、或产生决策的记录，都应能回答：
"这笔是在哪套市场规则下做的？" —— 即本标记。
"""

from __future__ import annotations

from typing import Optional

#: 现行市场规则（DAME/EDAM 前）：legacy DAM + RTPD/FMM。
MARKET_RULE_VERSION_PRE_DAME_EDAM_2026: str = "PRE_DAME_EDAM_2026"
#: 未来市场规则（DAME/EDAM 上线后）；本轮仅标记，不做适配。
MARKET_RULE_VERSION_POST_DAME_EDAM_2026: str = "POST_DAME_EDAM_2026"

#: 全部已知市场规则版本（单一事实来源）
MARKET_RULE_VERSIONS: tuple = (
    MARKET_RULE_VERSION_PRE_DAME_EDAM_2026,
    MARKET_RULE_VERSION_POST_DAME_EDAM_2026,
)

#: 当前项目采用的市场规则版本
CURRENT_MARKET_RULE_VERSION: str = MARKET_RULE_VERSION_PRE_DAME_EDAM_2026

#: DAME/EDAM 市场规则生效边界（按项目确认：2026-05-01 起的交易日标记为 POST）。
#: 若官方实际生效日不同，仅需改此一处。
MARKET_RULE_CUTOFF_DATE = __import__("pandas").Timestamp("2026-05-01")


def market_rule_version_for(trade_date) -> str:
    """按交付日（trade_date）返回该笔交易所属的市场规则版本（单一来源）。

    trade_date >= 2026-05-01 -> POST_DAME_EDAM_2026，否则 PRE。
    这是"版本标签"而非完整 EDAM 适配；边界日期由 MARKET_RULE_CUTOFF_DATE 统一控制。
    """
    from datetime import date as _date
    if isinstance(trade_date, (str, _date)):
        try:
            ts = __import__("pandas").Timestamp(trade_date)
            return (MARKET_RULE_VERSION_POST_DAME_EDAM_2026
                    if ts >= MARKET_RULE_CUTOFF_DATE
                    else MARKET_RULE_VERSION_PRE_DAME_EDAM_2026)
        except Exception:
            pass
    return CURRENT_MARKET_RULE_VERSION


def normalize_market_rule_version(value: Optional[str]) -> str:
    """把任意输入规约为合法市场规则版本；非法/缺失回退当前版本。

    回退到 CURRENT 而非留空，是为了保证"未显式标注 = 按现行规则"，与
    本项目全部数据均按 legacy DAM 口径采集的事实一致。若未来需要严格
    拒绝未知版本，调用方可自行断言 value in MARKET_RULE_VERSIONS。
    """
    v = str(value or "").strip()
    if v in MARKET_RULE_VERSIONS:
        return v
    return CURRENT_MARKET_RULE_VERSION
