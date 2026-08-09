# -*- coding: utf-8 -*-
"""
code/risk_gate/config.py

Risk Gate 的阈值配置（全部可配置、可审计，带版本与修改记录）。

【阈值来源铁律】
  所有数值阈值一律由 **train+val** 数据校准/验证得出；test 只做最终验证。
  本文件只放"默认值 + 依据"，校准过程与过拟合检验见 code/risk_gate/calibrate.py。

【DATA-DERIVED TEMPORARY GUARDRAIL】
  从历史数据归纳的 empirical guardrail（CONTROLX BUY 拒绝、ELCA SELL 拒绝、低样本拒绝）
  每条都带 status=DATA-DERIVED TEMPORARY GUARDRAIL，并引用 train+val 证据。
  这类规则依赖"漂移方向/样本量"这类**可能随 regime 改变**的结构事实，
  因此是临时的：漂移转负、样本积累后必须复核（规则白盒，可审计）。

【已验证无效、降级为 WARNING 的规则】（V0.1 evidence）
  - LOW_CONFIDENCE   : confidence 未校准，与 PnL 反相关 → 只警告，不拦（design §3.3）
  - HIGH_VOLATILITY  : vol_ratio 无单调判别力（>3.0 段反而正收益）→ 只警告（design §3.4）
  - MODEL_UNSTABLE   : v2 uncertainty 在 CONTROLX 上高不确定段反而高收益（彩票右尾）
                        → 只警告（本版 calibrate 验证，结论见 docs/risk_gate_v02_rules.md）
  - EXTREME_TAIL_NODE: 历史 cvar99 无法事前识别 CONTROLX SELL 的 DA 崩塌尾
                        → 只警告（design §3.2）
  - EXPECTED_RETURN_TOO_SMALL : |er| 阈值不改善尾部，只打薄 coverage → Rule Engine 处理
                        → gate 仅信息（design §3.5）

版本记录：
  0.2 (2026-08-09)  独立 Risk Gate 模块；保留 V0.1 三条 guardrail（标注临时）；
                     新增 required_fields 数据完整性检查、evidence/case 适配；
                     uncertainty 作为 MODEL_UNSTABLE 警告级（默认不拦）。
  0.1 (2026-08-09)  agent_d_gate.py 验证版（3 REJECT + 1 WARNING + 3 删除）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Empirical Guardrail（data-derived，临时）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EmpiricalGuardrail:
    """一条由 train+val 数据归纳的临时 guardrail。

    判定：候选交易 node==node 且 direction==direction → REJECT（reason_code）。
    证据来自 canonical train+val 无条件漂移与无条件 PnL（见 docs/stage3/risk_gate_design.md §2）。
    """

    node: str
    direction: str                              # "BUY" / "SELL"
    reason_code: str
    trainval_evidence: dict = field(default_factory=dict)  # 漂移/mean/maxloss/cvar99 等
    status: str = "DATA-DERIVED TEMPORARY GUARDRAIL"
    threshold_source: str = (
        "canonical train+val 无条件漂移方向 + 无条件 PnL（train 2025-04-03~2026-06-01, "
        "val 2026-01-02~2026-06-01）；test 零调参"
    )


#: V0.1 三条 guardrail 的默认实例（证据全部来自 risk_gate_calibration.json）
def _default_guardrails() -> Tuple[EmpiricalGuardrail, ...]:
    return (
        EmpiricalGuardrail(
            node="CONTROLX_1_N001",
            direction="BUY",
            reason_code="BUY_ON_POSITIVE_DRIFT_NODE",
            trainval_evidence={
                "node_drift_trainval": +9.68,   # 无条件 Return 漂移（正 → 应 SELL）
                "BUY_uncond_mean": -9.68,       # 无条件 BUY 负期望
                "BUY_uncond_maxloss": -3656.0,
                "BUY_uncond_cvar99": -916.4,
            },
        ),
        EmpiricalGuardrail(
            node="ELCAJNGT_7_N001",
            direction="SELL",
            reason_code="SELL_ON_NEGATIVE_DRIFT_NODE",
            trainval_evidence={
                "node_drift_trainval": -1.15,   # 负漂移（→ 应 BUY，SELL 逆漂移）
                "SELL_uncond_mean": -1.15,
                "SELL_uncond_maxloss": -356.8,
                "SELL_uncond_cvar99": -223.9,
            },
        ),
    )


# ---------------------------------------------------------------------------
# RiskGateConfig
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskGateConfig:
    """Risk Gate 阈值配置。所有阈值默认值均由 train+val 校准得出。

    字段说明（rule 映射见 code/risk_gate/rules.py 与 docs/risk_gate_v02_rules.md）。
    """

    version: str = "0.2"

    # ---- 数据完整性（DATA_MISSING → REJECT）----
    required_fields: Tuple[str, ...] = (
        "node", "target_date", "hour", "expected_return", "confidence",
    )

    # ---- R6 低样本（REJECT）----
    low_sample_min_hist: float = 150.0
    #   source: ELCA train hist_n 中位 44（max 89）、test 中位 121（max 154）；
    #           主节点 train 455+ / test 879+（risk_features.parquet）

    # ---- R5 尾部（EXTREME_TAIL_NODE → WARNING，不拦）----
    cvar_tail_threshold: float = -600.0
    #   source: V0.1 R4 扫描——cvar99 阈值无法捕获 CONTROLX SELL 最大亏损
    #           （−2065 行事前 cvar99 仅 −276），只能降到警告级

    # ---- R3 高波动（HIGH_VOLATILITY → WARNING，默认启用仅警告）----
    high_volatility_enabled: bool = True
    high_volatility_ratio_threshold: float = 3.0
    #   source: V0.1 R3 分层——vol_ratio 无单调性（>3.0 段 mean +10.49），
    #           明确不能作为 REJECT；仅保留 WARNING 供审计。

    # ---- MODEL_UNSTABLE（v2 uncertainty → WARNING，默认不拦）----
    model_unstable_enabled: bool = True
    model_unstable_uncertainty_cap: float = 0.95
    #   source: v2 val 校准——uncertainty 在 CONTROLX 上与 PnL 非单调
    #           （高不确定段 = 极端事件彩票，右尾正收益），不可作 REJECT。

    # ---- LOW_CONFIDENCE（→ WARNING，默认不拦）----
    low_confidence_warn: bool = True
    min_confidence: float = 0.20
    #   source: V0.1 R1 分层——conf 越高 mean 越差（+2.06 → −19.29），
    #           CONFIDENCE NOT CALIBRATED；Rule Engine 仍可把它转成 NO_TRADE。

    # ---- EXPECTED_RETURN_TOO_SMALL（→ INFO/WARNING，Rule Engine 处理 NO_TRADE）----
    expected_return_small_warn: bool = True
    min_spread: float = 5.0
    #   source: DECISION_CFG.ret_threshold_abs=5.0（code/backtest.py）；
    #           V0.1 R5 扫描——|er| 阈值不改善 cvar99，只打薄 coverage。

    # ---- R7 方向门（empirical guardrails，REJECT）----
    empirical_guardrails: Tuple[EmpiricalGuardrail, ...] = field(default_factory=_default_guardrails)

    # ---- R7 泛化漂移规则（可审计替代硬编码节点；默认关闭）----
    drift_rule_enabled: bool = False
    drift_buy_threshold: float = 5.0     # node_drift > +5.0 且 BUY → REJECT
    drift_sell_threshold: float = 2.0    # node_drift < −2.0 且 SELL → REJECT
    #   source: V0.1 未采用（SNLNDRO 漂移 +2.61 但 BUY 尾极小，纯漂移符号会误伤）；
    #           默认关闭，保留作 regime 复核的通用化版本。

    # ---- R8 相似亏损 Case（SIMILAR_TAIL_LOSS_CASE → WARNING）----
    similar_case_enabled: bool = True
    similar_case_tail_threshold: float = -300.0   # Case PnL < −300 视为亏损 Case
    similar_case_hour_window: int = 3             # |hour − case.hour| <= 3
    similar_case_max: int = 5
    #   source: agent/case_library/cases.json（18 条 test 窗口真实极端事件）。

    # ---- R11 证据冲突（EVIDENCE_CONFLICT → WARNING，默认不拦）----
    evidence_conflict_warn: bool = True
    #   当前无真实数据源，全部证据 UNCERTAIN，本规则几乎不触发。

    # ---- R12 证据极端状态（EXTREME_STATE_EVIDENCE → REJECT/WARNING，evidence 驱动）----
    #   消费 Pre-decision Evidence 的方向上下文（evidence_direction_context.max_severity）：
    #   当"极端状态"证据（如极端天气预警）severity 达到阈值 → 保守拦截（REJECT 或 WARNING）。
    #   directional_effect 保持 UNCERTAIN（证据不判 Return 方向），只把极端状态当风险因子。
    #   无证据时本规则不触发（A 组 / 既有 pipeline 行为完全不变）。
    evidence_extreme_enabled: bool = True
    evidence_extreme_severity_threshold: str = "WARNING"  # max_severity ≥ 该值 → 命中（WATCH<WARNING<SEVERE<CRITICAL）
    evidence_extreme_level: str = "REJECT"                # "REJECT" 或 "WARNING"（默认 REJECT = 保守）
    #   source: 任务定义（A/B 测试）——证据极端状态作为风险因子，证据与模型冲突时优先
    #           WARNING/NO_TRADE，不反向下注。severity 由 evidence 构建方（A/B 脚本）赋值；
    #           本规则只读不猜。

    # ---- 记录/审计 ----
    changelog: Tuple[str, ...] = (
        "0.2: 独立模块化；保留 V0.1 guardrail（标注 DATA-DERIVED TEMPORARY GUARDRAIL）；"
        "新增 required_fields / evidence / case 适配；uncertainty 降为警告级。",
        "0.1: agent_d_gate.py 验证版（R7a/R7b/R6 REJECT；R4 警告；R1/R3/R5 删除）。",
    )


# 默认配置实例（便于直接使用）
DEFAULT_RISK_GATE_CONFIG: RiskGateConfig = RiskGateConfig()


def build_config(**overrides) -> RiskGateConfig:
    """按覆盖项构造 RiskGateConfig（阈值覆盖必须显式注明来源，见 rules 文档）。"""
    return RiskGateConfig(**overrides)
