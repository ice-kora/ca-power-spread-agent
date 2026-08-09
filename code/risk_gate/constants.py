# -*- coding: utf-8 -*-
"""
code/risk_gate/constants.py

Risk Gate 的常量与口径（单一事实来源）。

与团队统一口径对齐（docs/business_contract.md）：
  - Return = DA − RTPD（$/MWh）
  - SELL_DA / Virtual Supply：预期 Return > 0 时 DA 卖出、RT 反向买回  →  PnL = +actual_return
  - BUY_DA  / Virtual Demand ：预期 Return < 0 时 DA 买入、RT 反向卖回  →  PnL = −actual_return
  - NO_TRADE                                            →  PnL = 0

决策时点 = D-1 日 10:00 PT（DAM Market Close / bid cutoff，官方 BPM）。
任何 Risk Gate 输入都必须满足 as-of 约束（<= target_date-2 的滞后约定，宁保守不泄漏）。

本模块只定义常量与口径，不包含任何决策逻辑。
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Gate 三态判定
# ---------------------------------------------------------------------------
GATE_PASS: str = "PASS"
GATE_WARNING: str = "WARNING"
GATE_REJECT: str = "REJECT"

GATE_DECISIONS: tuple = (GATE_PASS, GATE_WARNING, GATE_REJECT)

# 规则触发的“级别”：REJECT 直接拦下；WARNING 放行但标记（供 Rule Engine / 交易员决策）
LEVEL_REJECT: str = "REJECT"
LEVEL_WARNING: str = "WARNING"

# ---------------------------------------------------------------------------
# 方向
# ---------------------------------------------------------------------------
DIRECTION_BUY: str = "BUY"
DIRECTION_SELL: str = "SELL"
DIRECTION_FLAT: str = "FLAT"

#: BUY 的 PnL 系数 = −1（Return<0 时 DA 买入赚钱），SELL 的 PnL 系数 = +1
_PNL_SIGN: dict = {DIRECTION_SELL: 1.0, DIRECTION_BUY: -1.0}


def direction_from_expected_return(expected_return: Optional[float]) -> Optional[str]:
    """由 expected_return 的符号推导候选交易方向。

    Returns:
        "SELL" 当 expected_return > 0；"BUY" 当 < 0；None 当缺失/为 0。
    """
    if expected_return is None:
        return None
    try:
        er = float(expected_return)
    except (TypeError, ValueError):
        return None
    if er != er:  # NaN
        return None
    if er > 0:
        return DIRECTION_SELL
    if er < 0:
        return DIRECTION_BUY
    return None


def signed_pnl(direction: str, actual_return: Optional[float]) -> Optional[float]:
    """把实际 Return 折算成按交易方向的 PnL（$/MWh，1 MWh/仓）。

    SELL → +actual_return；BUY → −actual_return；FLAT/None → 0 / None。
    """
    if actual_return is None:
        return None
    try:
        ar = float(actual_return)
    except (TypeError, ValueError):
        return None
    if ar != ar:  # NaN
        return None
    sign = _PNL_SIGN.get(str(direction).strip().upper(), 0.0)
    return sign * ar


# ---------------------------------------------------------------------------
# Risk Gate reason_code 全集
# ---------------------------------------------------------------------------
# 任务要求的 10 个必备码 + 本项目补充码（证据冲突 / 无方向）。
# 注意：不是所有码都会 REJECT——LOW_CONFIDENCE / HIGH_VOLATILITY / MODEL_UNSTABLE
# 在 train+val 验证中**不具判别力**（V0.1 已删/降级，见 docs/stage3/risk_gate_design.md），
# 因此本版保留为 WARNING 级（供审计/交易员参考），默认不拦截交易。
# 已移除：MODEL_DISAGREEMENT（V0.1 R2 三模型一致性投票残留；V0.2 单一生产模型，
# Interpretable/CatBoost 仅作 benchmark/offline validation，见 docs/DecisionPipeline.md §4）。
REASON_CODES: tuple = (
    # ---- 必备码（10 个）----
    "LOW_CONFIDENCE",               # 置信度过低（V0.1 验证未校准，仅警告）
    "EXPECTED_RETURN_TOO_SMALL",    # |expected_return| 过小（Rule Engine 负责 NO_TRADE，gate 仅信息）
    "EXTREME_TAIL_NODE",            # 同 node×hour 历史尾部过深（V0.1 R4：警告级，不拦）
    "HIGH_VOLATILITY",              # 历史波动过高（V0.1 R3：验证无效，仅警告）
    "MODEL_UNSTABLE",               # 模型不确定性过高（v2 uncertainty，仅警告）
    "SIMILAR_TAIL_LOSS_CASE",       # 命中相似历史亏损 Case（Case Library 检索）
    "DATA_MISSING",                 # 关键输入缺失 → 保守 REJECT
    "BUY_ON_POSITIVE_DRIFT_NODE",   # R7a：正漂移节点上 BUY（逆漂移）→ REJECT
    "SELL_ON_NEGATIVE_DRIFT_NODE",  # R7b：负漂移节点上 SELL（逆漂移）→ REJECT
    "LOW_SAMPLE_SUPPORT",           # R6：样本量不足（cold-start）→ REJECT
    # ---- 补充码 ----
    "EVIDENCE_CONFLICT",            # 可用的 Pre-decision Evidence 方向与候选方向冲突
    "NO_CLEAR_DIRECTION",           # expected_return 无明确方向（=0 / 缺失）
)

#: 默认会触发 REJECT 的 reason_code（其余为 WARNING 或按配置启用）
REJECT_REASON_CODES: tuple = (
    "DATA_MISSING",
    "BUY_ON_POSITIVE_DRIFT_NODE",
    "SELL_ON_NEGATIVE_DRIFT_NODE",
    "LOW_SAMPLE_SUPPORT",
)

# ---------------------------------------------------------------------------
# 版本与标注
# ---------------------------------------------------------------------------
#: 所有从历史数据归纳、非第一性原理的 guardrail 必须带此标注，并在 config 中留证据。
DATA_DERIVED_TEMPORARY_GUARDRAIL: str = "DATA-DERIVED TEMPORARY GUARDRAIL"

#: 决策时点口径（官方 BPM，见 docs/market_timeline.md）
DECISION_CUTOFF_DESC: str = "D-1 日 10:00 PT（Day-Ahead Market Close / bid cutoff）"
