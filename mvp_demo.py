# -*- coding: utf-8 -*-
"""
mvp_demo.py —— CAISO 价差交易 MVP Demo（CLI 渲染层 · 决策链单一来源 DecisionService）
======================================================================================

系统：CA-ISO Day-Ahead vs Real-Time 价差（Return = DA − RTPD）交易决策演示。

**V0.3.1.2 起：本脚本不再重复任何决策逻辑。**
Model / Evidence / Evidence Time Gate / Case Retrieval / Risk Gate / Rule Engine /
final / PnL 全部由 `code/decision_service.py` 的 `DecisionService.run_decision()`
计算一次并归档为 **DecisionSnapshot（单一事实来源）**。CLI 只把同一份快照渲染成
文本报告；Web（mvp_web.py）/ CLI / LLM Tools 消费**同一个 DecisionSnapshot**——
仓库内不存在第二套 RiskGate / RuleEngine（`code/tests/test_v0312_freeze.py`
做源码级断言守护，杜绝回归）。

生命周期（与 Web 完全一致 · Outcome Access Control）：
    run_decision(reveal=False)   → 渲染决策（无任何 actual_*）
    → lock_decision(decision_id) → reveal_decision(decision_id) → 渲染 Post-trade
    Lock 前 reveal 一律返回 NOT_LOCKED，绝不穿越。

诚实标注（重要）：
    * MODEL SIGNAL IS EXPERIMENTAL / CURRENT ALPHA = WEAK / MVP ≠ 已验证盈利系统
    * 本 Demo 不做任何方向编造：所有证据 / 案例 / 特征均为真实数据（as-of 约束）。
    * actual_da / actual_rtpd / actual_return 在 LOCK + REVEAL 之前绝不展示（不穿越）。

用法（在仓库根目录）：
    python mvp_demo.py                                          # 默认：CONTROLX_1_N001 决策日 2026-07-08 H2
    python mvp_demo.py --decision-date 2026-07-16 --node CONTROLX_1_N001 --hour 3
    python mvp_demo.py --auto-reveal                             # 自动揭晓 Post-trade（不等待 Enter）
    python mvp_demo.py --offline                                 # 不取外部 GFS 证据（纯本地演示）
    python mvp_demo.py --list-rows                               # 列出可用 (node, target_date, hour)
    python mvp_demo.py --json-out mvp_demo_card.json             # 另存同一 DecisionSnapshot 审计 JSON

说明：预测窗口为 test（target_date 2026-06-02 ~ 2026-08-05），故合法 decision_date
      = 2026-06-01 ~ 2026-08-04。节点限 ZP26（SNLNDRO / CONTROLX）与 SP15（ELCA）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Windows 控制台常为 GBK，先切到 UTF-8，保证中文 UI 与 ° 等字符可显示
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from data_mode import MODE_DEMO, resolve_data_mode  # noqa: E402
from code.decision_service import (  # noqa: E402
    ALPHA_LABEL,
    DECISION_CUTOFF_DESC,
    DecisionService,
    HistoricalSnapshotEvidenceAdapter,
    StaticEvidenceAdapter,
)
from code.data_acquisition.schemas import NODE_REGION  # noqa: E402

# ---------------------------------------------------------------------------
# 数据模式 / 常量
# ---------------------------------------------------------------------------
# 数据模式（FULL / DEMO）由 data_mode.py 自动探测；MVP_DATA_MODE / DATA_MODE 可覆盖。
# DEMO ≠ MOCK：DEMO 是真实历史最小切片，可真实推荐；MOCK 永不参与真实推荐。
_DM = resolve_data_mode()
DATA_MODE = _DM.mode

#: 候选交易方向常量（与 decision_service / risk_gate.constants 一致）
DIR_SELL, DIR_BUY, DIR_FLAT = "SELL", "BUY", "FLAT"

#: 展示用：特征 → 中文类别（纯渲染映射，非决策字段；snapshot 已含 source/available_at）
_FEAT_ZH = {
    "spread_lag1": "Historical Return Lag（T-2 价差 DA−RTPD）",
    "spread_lag2": "Historical Return Lag（T-3 价差 DA−RTPD）",
    "spread_lag7": "Historical Return Lag（T-8 价差 DA−RTPD）",
    "spread_mean7": "Rolling Spread 7d 均值",
    "spread_std7": "Rolling Spread 7d 标准差",
    "spread_mean14": "Rolling Spread 14d 均值",
    "spread_std14": "Rolling Spread 14d 标准差",
    "spread_mean30": "Rolling Spread 30d 均值",
    "spread_std30": "Rolling Spread 30d 标准差",
    "spread_day_std_lag1": "当日 Spread 日内波动（T-2）",
    "spread_day_range_lag1": "当日 Spread 日内极差（T-2）",
    "da_lag1": "DA Lag（T-2 日前价）",
    "rtpd_lag1": "RTPD Lag（T-2 实时价）",
    "da_day_mean_lag1": "DA 日均价（T-2）",
    "rtpd_day_mean_lag1": "RTPD 日均价（T-2）",
    "spread_day_mean_lag1": "Spread 日均（T-2）",
    "load_actual_lag1": "Load Actual Lag（T-2 实际负荷）",
    "load_2da_forecast": "Load Forecast（2DA 日前负荷预测）",
    "load_peak_flag": "负荷峰值标记",
    "t2m_lag1": "Weather Lag（2m 气温，T-2）",
    "ssrd_lag1": "Weather Lag（太阳辐射，T-2）",
    "wind100_lag1": "Weather Lag（100m 风速，T-2）",
    "peer_spread_lag1": "Congestion / Peer（同区节点 T-2 价差）",
    "peer_da_lag1": "Congestion / Peer DA（T-2）",
    "peer_rtpd_lag1": "Congestion / Peer RTPD（T-2）",
}


# ---------------------------------------------------------------------------
# 格式化工具（渲染层）
# ---------------------------------------------------------------------------
def _f(x, nd: int = 2) -> str:
    """数值格式化；NaN/None → '-（缺失）'。"""
    if x is None:
        return "-（缺失）"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if v != v:  # NaN
        return "-（缺失）"
    return f"{v:,.{nd}f}"


def _utc_label(dt_iso: str) -> str:
    """'YYYY-MM-DDTHH:MM:SS' → 'YYYY-MM-DD HH:MM UTC'。"""
    if not dt_iso:
        return "-"
    return str(dt_iso).replace("T", " ")


def _rule_zh(code: str) -> str:
    return {
        "RISK_GATE_REJECTED": "风控闸门 REJECT → 保守放弃交易",
        "DATA_MISSING": "关键输入缺失",
        "EXPECTED_RETURN_TOO_SMALL": "预期收益幅度过小（< 5 $/MWh）",
        "LOW_CONFIDENCE": "模型信号强度过低（< 0.20）",
        "EVIDENCE_CONFLICT": "可用证据与候选方向冲突",
        "RISK_GATE_WARNING_ESCALATED": "闸门 WARNING 且配置升级拦截",
        "EXPECTED_RETURN_POSITIVE": "预期 Return > 0 → 卖出 DA（SELL_DA）",
        "EXPECTED_RETURN_NEGATIVE": "预期 Return < 0 → 买入 DA（BUY_DA）",
        "NO_CLEAR_DIRECTION": "预期 Return 无明确方向",
    }.get(code, code)


def _gate_zh(code: str) -> str:
    return {
        "DATA_MISSING": "关键输入缺失（宁保守不穿越）",
        "BUY_ON_POSITIVE_DRIFT_NODE": "正漂移节点上做多（逆漂移）：该节点历史上 DA 持续高于 RTPD，做多长期负期望，被闸门拒绝",
        "SELL_ON_NEGATIVE_DRIFT_NODE": "负漂移节点上做空（逆漂移）：该节点历史上 DA 持续低于 RTPD，做空长期负期望，被闸门拒绝",
        "LOW_SAMPLE_SUPPORT": "同节点×小时历史样本不足（cold-start），统计不可靠，被闸门拒绝",
        "EXTREME_TAIL_NODE": "历史尾部风险深（cvar99/rcvar99 < −600），仅警告不拦截",
        "HIGH_VOLATILITY": "近 30 日波动 / 历史波动偏高，仅提示（V0.1 验证无判别力）",
        "MODEL_UNSTABLE": "模型不确定度偏高（uncertainty > 0.95），仅提示",
        "SIMILAR_TAIL_LOSS_CASE": "命中历史相似亏损案例，提示交易员复核",
        "LOW_CONFIDENCE": "模型信号强度偏低（< 0.20），仅提示",
        "EXPECTED_RETURN_TOO_SMALL": "|预期收益| < 5 $/MWh，闸门仅提示（Rule Engine 负责转 NO_TRADE）",
        "EVIDENCE_CONFLICT": "可用证据方向与候选相反，仅提示",
        "EXTREME_STATE_EVIDENCE": "Pre-decision 证据出现极端状态（severity ≥ WARNING），保守拦截",
        "NO_CLEAR_DIRECTION": "方向不明",
    }.get(code, code)


def box(title: str, width: int = 88) -> None:
    print("=" * width)
    print(f" {title} ".center(width, "·"))
    print("=" * width)


# ---------------------------------------------------------------------------
# 渲染（全部从 DecisionSnapshot 取数，不重算任何决策字段）
# ---------------------------------------------------------------------------
def render_section1(d) -> dict:
    """Section 1 · Decision Context（决策上下文）——直接来自 snapshot.context。"""
    ctx = d["context"]
    box("Section 1 · Decision Context（决策上下文）")
    print(f"  Decision Date      : {ctx['decision_date']}（D-1，DAM bid cutoff 当日）")
    print(f"  Decision Cutoff    : {ctx['decision_cutoff_pt']}  →  {_utc_label(ctx['decision_cutoff_utc'])} UTC（{DECISION_CUTOFF_DESC}）")
    print(f"  Target Date        : {ctx['target_date']}（D+1，交付日）")
    print(f"  Target Hour        : H{ctx['hour']}")
    print(f"  Node               : {ctx['node']}（区域 {ctx['zone']}）")
    print(f"  Market Rule Version: {ctx['market_rule_version']}（date-aware，按 target_date 判定）")
    print(f"  {'─' * 76}")
    print(f"  ⚠ {ctx['as_of_banner']} —— 之后产生的任何信息（实际价格 / 晚报证据）都不得进入决策。")
    return ctx


def render_section2(d) -> list:
    """Section 2 · Available Data（决策时点可见 · Top 特征）——snapshot.top_features。"""
    rows = d["top_features"]
    box("Section 2 · Available Data（决策时点可见 · Top 特征）")
    print("  （只展示决策相关 Top 特征，非全量；全部来自 canonical X 区 as-of 特征）")
    print("  （P0-2 统一口径：展示 available_at == Time Gate 判定口径；无精确发布时刻 → 显示")
    print("    ≤ decision_date 00:00 PT 的最晚可证上界，不编造 23:59 之类的精确时间戳）")
    print(f"  {'类别':<32}{'特征':<20}{'value':>14}  {'available_at（上界）':<22}{'basis':<20}{'eligible'}")
    print("  " + "─" * 106)
    for r in rows:
        zh = _FEAT_ZH.get(r["feature"], r["feature"])
        print(f"  {zh:<30}{r['feature']:<18}{_f(r['value'], 2):>14}  {r['available_at']:<20}  {r['availability_basis']:<18}  {'YES' if r['decision_eligible'] else 'NO'}")
    print()
    print("  每项 source / 口径（available_at 为最晚可证上界，非精确发布时间）：")
    for r in rows:
        note = f"source_target_date={r.get('source_target_date') or '—'}"
        if not r.get("has_precise_publish_time"):
            note += "；无精确发布时刻（诚实标注）"
        print(f"    · {r['feature']}  ← {r.get('source', '?')}（{r['availability_basis']}；{note}）")
    return rows


def render_section3(d) -> None:
    """Section 3 · Predictive Model（预测模型 · EXPERIMENTAL）——snapshot.model_output。"""
    mo = d["model_output"]
    box("Section 3 · Predictive Model（预测模型 · EXPERIMENTAL）")
    if "note" in mo:  # 数据缺失（不在 test 窗口）
        print(f"  ⚠ {mo['note']}")
        return
    er = mo.get("expected_return")
    print(f"  expected_return          : {_f(er, 2)} $/MWh   （Return = DA − RTPD 预期幅度，中位数目标）")
    print(f"  prob_positive            : {_f(mo.get('prob_positive'), 4)}   （P(Return > 0)）")
    print(f"  prob_negative            : {_f(mo.get('prob_negative'), 4)}   （P(Return ≤ 0)）")
    print(f"  direction_probability    : {_f(mo.get('direction_probability'), 4)}   （模型押注方向 {mo.get('direction')} 的概率）")
    ms = mo.get("model_signal_strength")
    print(f"  model_signal_strength    : {_f(ms, 4)}   （⚠ 方向概率强度×幅度信噪比的组合量，非校准概率；")
    print(f"                              Rule Engine 仅以 ≥0.20 作保守过滤；V0.2 起不再称 confidence）")
    print(f"  uncertainty              : {_f(mo.get('uncertainty'), 4)}   （0-1，q10/q90 区间宽度相对分布尺度的归一化）")
    print()
    print("  Top Feature Contributions（特征统计 z-score，as-of；⚠ 非 SHAP，供解释参考）：")
    print(f"    {'feature':<22}{'value':>12}{'hist_mean':>12}{'hist_std':>12}{'z':>10}   解读")
    any_z = False
    for c in d["top_features"]:
        z = c.get("z")
        if z is None:
            continue
        any_z = True
        how = "异常偏高" if z > 0 else "异常偏低"
        print(f"    {c['feature']:<20}{_f(c['value'],2):>12}{_f(c.get('hist_mean'),2):>12}"
              f"{_f(c.get('hist_std'),2):>12}{z:>10.2f}   该特征相对历史 {how} {abs(z):.2f}σ")
    if not any_z:
        print("    （无足够历史样本计算特征统计贡献）")


def render_section4(d) -> None:
    """Section 4 · Agent Evidence（外部证据 · 经 Evidence Time Gate）——snapshot.evidence。"""
    ev = d["evidence"]
    box("Section 4 · Agent Evidence（外部证据 · 经 Evidence Time Gate）")
    eligible = ev.get("eligible", [])
    rejected = ev.get("rejected", ev.get("post_decision", []))
    print(f"  {ev.get('gate_note', '')}")
    if eligible:
        print()
        print("  ▶ Pre-decision / ELIGIBLE（真实证据，进入决策）：")
        for r in eligible:
            print(f"    · [{r['event_type']}/{r['severity']}] {r['source']}")
            print(f"      summary      : {r['summary']}")
            print(f"      available_at : {_utc_label(r['available_at'])} UTC  |  decision_cutoff : {_utc_label(r['decision_cutoff'])} UTC")
            print(f"      decision_eligible : {r['decision_eligible']}  |  directional : {r['directional_effect']}")
    else:
        print()
        print("  ▶ NO ELIGIBLE EXTERNAL EVIDENCE —— 决策时点（10:00 PT）前无可用真实外部证据。")
        print("    （不编造证据：宁可未知，不可乱判方向）")
    if rejected:
        print()
        print("  ▶ POST-DECISION / NOT USED（初始化晚于 cutoff 或可用性不可证 → 只进复盘，绝不影响决策）：")
        for r in rejected:
            print(f"    · [{r['event_type']}/{r['severity']}] {r['source']}")
            print(f"      summary      : {r['summary']}")
            avail_disp = (_utc_label(r.get('available_at')) if r.get('available_at')
                          else 'UNKNOWN / NOT PROVEN')
            print(f"      available_at : {avail_disp} UTC  |  rejection_reason : {r.get('rejection_reason', '')}")
    else:
        print()
        print("  ▶ POST-DECISION / NOT USED：（本决策日未获取到晚于 cutoff 的真实证据）")


def render_section5(d) -> None:
    """Section 5 · Similar Historical Cases（历史相似案例 · as-of）——snapshot.top_cases。"""
    cases = d["top_cases"]
    ctx = d["context"]
    box("Section 5 · Similar Historical Cases（历史相似案例 · as-of）")
    print(f"  检索门槛：case_available_at <= {ctx['decision_date']}T10:00:00（决策时点）—— 只检索结果已完整结算的案例，防穿越。")
    print(f"  命中 {len(cases)} 条（同 node × 小时窗 |Δh|≤3/6，按 |PnL| 排序）")
    if not cases:
        print("  （决策时点前无相似已结算案例）")
        return
    print(f"  {'case_id':<16}{'date':<12}{'node(short)':<18}{'hour':<6}{'signal':<16}{'decision':<10}{'outcome':>10}{'PnL':>12}  lesson")
    for c in cases:
        node_s = str(c.get("node", "")).replace("_1_N001", "")
        sig = f"{c.get('model_prediction','?')} exp={_f(c.get('expected_return'),0)}"
        lesson = ""
        lessons = c.get("lessons") or []
        if lessons:
            lesson = str(lessons[0])[:34]
        elif c.get("why_correct_or_wrong"):
            lesson = str(c.get("why_correct_or_wrong", ""))[:34]
        print(f"  {str(c.get('case_id','?')):<16}{str(c.get('decision_date',''))[:10]:<12}{node_s:<18}"
              f"H{int(c.get('hour',-1)):<5}{sig:<18}{str(c.get('model_prediction','')):<12}"
              f"{_f(c.get('actual_Return'),0):>10}{_f(c.get('PnL'),0):>12}  {lesson}")


def render_section6(d) -> None:
    """Section 6 · Risk Gate（风控闸门）——snapshot.risk_gate。"""
    rg = d["risk_gate"]
    box("Section 6 · Risk Gate（风控闸门）")
    print(f"  Verdict : {rg.get('decision')}")
    reasons = list(rg.get("risk_reasons", []))
    if reasons:
        print("  reason_code（业务解释）：")
        for code in reasons:
            print(f"    · {code}: {_gate_zh(code)}")
    else:
        print("  无风险规则命中（PASS）。")
    rules = rg.get("rules_hit") or []
    if rules:
        print("  命中规则详情（as-of 风险特征 / 阈值）：")
        for r in rules[:6]:
            msg = str(r.get("message", "")).strip()
            if msg:
                print(f"    · {r.get('rule_id')}: {msg[:96]}")


def render_section7(d) -> None:
    """Section 7 · Final Recommendation（最终建议）——snapshot.final_recommendation。"""
    box("Section 7 · Final Recommendation（最终建议）")
    print(f"  ▶ {d['final_recommendation']}")
    print()
    print("  Why（全部来自同一 DecisionSnapshot 字段）：")
    why = _build_why(d)
    for key in ("Model", "Historical", "Evidence", "RiskGate", "RuleEngine"):
        text = why.get(key, "")
        if text:
            print(f"    · {key:<10}: {text}")


def _build_why(d) -> dict:
    """把 snapshot 已有字段拼成 Why 解释（渲染加工，不产生新决策字段）。"""
    ctx = d["context"]
    mo = d["model_output"]
    rg = d["risk_gate"]
    re = d["rule_engine"]
    ev = d["evidence"]
    cases = d["top_cases"]

    why: dict = {}
    if "note" in mo:
        why["Model"] = mo["note"]
    else:
        why["Model"] = (
            f"expected_return={_f(mo.get('expected_return'),2)} $/MWh，方向概率 {_f(mo.get('direction_probability'),3)}"
            f"（押注 {mo.get('direction')}），model_signal_strength={_f(mo.get('model_signal_strength'),3)}，"
            f"uncertainty={_f(mo.get('uncertainty'),3)}"
        )
    why["Historical"] = f"检索到 {len(cases)} 条相似案例（as-of）"
    n_ev = len(ev.get("eligible", []))
    n_rej = len(ev.get("rejected", ev.get("post_decision", [])))
    if n_ev:
        why["Evidence"] = f"{n_ev} 条可用证据（directional 见 Evidence 段）；{n_rej} 条晚于 cutoff 被隔离"
    else:
        why["Evidence"] = ("NO ELIGIBLE EXTERNAL EVIDENCE（决策时点前无可用真实外部证据，无方向信号）"
                           if n_rej == 0 else "无可用真实证据；有晚于 cutoff 的证据被隔离（只进复盘）")
    why["RiskGate"] = rg.get("decision", "?") + (
        (f"：{'；'.join(_gate_zh(c) for c in rg.get('risk_reasons', []))}") if rg.get("risk_reasons") else "：无命中")
    hit = re.get("rules_hit") or []
    why["RuleEngine"] = (" / ".join(f"{rid}({_rule_zh(rc)})"
                                    for rid, rc in zip(hit, re.get("reasons", [])))
                         if hit else "未命中规则")
    return why


def render_post_trade(d, pt) -> None:
    """Post-trade（决策锁定后 · Reveal）——snapshot.post_trade / reveal_decision 返回值。"""
    ctx = d["context"]
    box("Post-trade · 决策已锁定并揭晓" if pt.get("status") == "REVEALED" else "Post-trade · 决策已锁定")
    print(f"  锁定决策：{d['final_recommendation']}（{ctx['decision_date']} 10:00 PT 决策，目标 {ctx['target_date']} H{ctx['hour']}）")
    print(f"  ⚠ 以下内容为真实事后结算信息，仅用于复盘，绝不回灌决策。")
    if pt.get("status") != "REVEALED":
        print(f"\n  Post-trade 未揭晓（{pt.get('status')}）。运行加 --auto-reveal 可自动查看。")
        return
    print()
    print("  ┌─────────────────────────────────────────────────────────────────────────────┐")
    print(f"  │  Actual DA   : {pt['actual_da']:>12,.2f} $/MWh     Actual RTPD : {pt['actual_rtpd']:>12,.2f} $/MWh │")
    print(f"  │  Actual Return(DA−RTPD) : {pt['actual_return']:>12,.2f} $/MWh                                │")
    print("  └─────────────────────────────────────────────────────────────────────────────┘")
    print()
    print(f"  PnL（1 MWh/仓）: {pt['pnl']:+,.2f} $/MWh   （BUY=RTPD−DA；SELL=DA−RTPD；NO_TRADE=0）")
    if pt.get("model_prediction_error") is not None:
        print(f"  Model Prediction Error : {pt['model_prediction_error']:+,.2f} $/MWh（actual − expected）")
    dc = pt.get("direction_correct")
    print(f"  Direction Correct?     : {'YES' if dc else ('N/A（无交易）' if dc is None else 'NO')}")
    print(f"  Trade Profitable?      : {'YES' if (pt.get('pnl') or 0) > 0 else ('NO' if (pt.get('pnl') or 0) < 0 else 'N/A（未交易）')}")
    print()
    print("  Post-trade Review 分类：")
    review = pt.get("review") or {}
    for tag in review.get("primary", []):
        print(f"    · {tag}")
    for n in review.get("notes", []):
        print(f"      ↳ {n}")


def render_audit(d) -> None:
    """Audit Panel（审计面板）——snapshot.audit（真实运行检查，OVERALL 由结果推导，不硬编码）。"""
    audit = d["audit"]
    ctx = d["context"]
    box("Audit Panel（审计面板）")
    print(f"  Runtime Audit OVERALL : {audit.get('overall')}（{audit.get('summary', '')}，真实运行检查，非硬编码）")
    for cid, ch in (audit.get("checks") or {}).items():
        note = str(ch.get("note", ""))[:72]
        print(f"    · {cid:<26} {ch.get('status','?'):<7}（{note}）")
    print(f"  Mock Data Used       : {audit.get('mock_data_used', 'NONE')}（决策路径无 MOCK；全部真实数据 as-of）")
    print(f"  Backtest-safe Feat.  : {audit.get('backtest_safe_features', '-')}")
    print(f"  Evidence Time Gate   : {audit.get('evidence_time_gate', '-')}")
    print(f"  Decision Cutoff      : {ctx['decision_cutoff_pt']} = {_utc_label(ctx['decision_cutoff_utc'])} UTC（{DECISION_CUTOFF_DESC}）")
    meta = audit.get("meta", {})
    print(f"  Market Rule Version  : {ctx['market_rule_version']}（date-aware 按 target_date）")
    print(f"  Model Version        : {meta.get('model_version', '-')}")
    print(f"  Rule Engine Version  : {meta.get('rule_engine_version', '-')}")
    print(f"  Risk Gate Version    : {meta.get('risk_gate_version', '-')}")
    print(f"  Time Gate Version    : {meta.get('evidence_time_gate_version', '-')}")
    print(f"  Case Library Version : {meta.get('case_library_version', '-')}")
    print(f"  As-of Schema Version : {meta.get('schema_version', '-')}")
    print(f"  Data Mode            : {meta.get('data_mode', DATA_MODE)}"
          + ("（DEMO：真实历史最小切片，非 MOCK；outcome 仅 Reveal 后经 service/tool 可访问）"
             if meta.get("data_mode") == MODE_DEMO else "（FULL：完整数据）"))


# ---------------------------------------------------------------------------
# 主流程（决策链唯一来源 DecisionService；本脚本只渲染 + Lock/Reveal）
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="CAISO 价差交易 MVP Demo（CLI 渲染层 · 决策链单一来源 DecisionService）")
    ap.add_argument("--decision-date", default="2026-07-08", help="决策日期 D（ISO YYYY-MM-DD；目标日=D+1）")
    ap.add_argument("--node", default="CONTROLX_1_N001", choices=sorted(NODE_REGION), help="目标节点")
    ap.add_argument("--hour", type=int, default=2, help="目标小时 H1~H24")
    ap.add_argument("--auto-reveal", action="store_true", help="自动揭晓 Post-trade（不等待 Enter）")
    ap.add_argument("--offline", action="store_true", help="不取外部 GFS 证据（StaticEvidenceAdapter，纯本地演示）")
    ap.add_argument("--json-out", default="", help="另存同一 DecisionSnapshot 审计 JSON 路径")
    ap.add_argument("--list-rows", action="store_true", help="列出可用 (node, target_date, hour) 选项")
    args = ap.parse_args()

    print()
    print("#" * 92)
    print("  CAISO 价差交易 · 可解释决策 MVP Demo（CLI 渲染层）")
    print("  决策链单一来源：DecisionService.run_decision → DecisionSnapshot（Web/CLI/Tool 同一份）")
    print(f"  ⚠ MODEL SIGNAL IS EXPERIMENTAL / CURRENT ALPHA = {ALPHA_LABEL} / MVP ≠ 已验证盈利系统")
    print(f"  DATA MODE = {DATA_MODE}"
          + ("（DEMO：真实历史最小切片，非 MOCK，可真实推荐）" if DATA_MODE == MODE_DEMO else "（FULL：完整数据）"))
    print("#" * 92)
    print()

    dd = str(args.decision_date)[:10]
    node = args.node
    hour = int(args.hour)

    # V0.3.1.5：三端一致 —— DEMO MODE 用真实历史 GFS 快照（HISTORICAL_SNAPSHOT，可重复），
    # FULL/LIVE 用真实 Collector，offline 用静态（NONE）；均不 fallback published_at。
    if args.offline:
        adapter = StaticEvidenceAdapter([])
    elif DATA_MODE == MODE_DEMO:
        adapter = HistoricalSnapshotEvidenceAdapter()
    else:
        adapter = None            # DefaultEvidenceAdapter（FULL/LIVE 真实 Collector）
    svc = DecisionService(evidence_adapter=adapter)

    if args.list_rows:
        print("可用 test 预测（node × target_date × hour 数量）：")
        print(svc.pred.groupby("node")["target_date"].agg(["count", "min", "max"]).to_string())
        return

    # ---- 完整白盒决策（DecisionService 只算一次；本脚本不重算任何决策字段）----
    try:
        decision_obj = svc.run_decision(dd, node, hour, reveal=False)
    except ValueError as exc:
        print(f"❌ {exc}")
        print("  用 --list-rows 查看可用 target_date。")
        sys.exit(1)
    did = decision_obj["decision_id"]

    # ================= Section 1 ~ 7（渲染 DecisionSnapshot）=================
    render_section1(decision_obj)
    render_section2(decision_obj)
    render_section3(decision_obj)
    render_section4(decision_obj)
    render_section5(decision_obj)
    render_section6(decision_obj)
    render_section7(decision_obj)

    # ================= Post-trade：LOCK → REVEAL（Outcome Access Control）=================
    print()
    svc.lock_decision(did)   # Lock 前置：reveal 前禁止显示 actual_*
    print(f"  [已锁定] decision_id={did}")

    reveal = args.auto_reveal
    if not reveal:
        try:
            _ = input("\n  按 Enter 揭晓 Actual DA / RTPD / Return ...（--auto-reveal 可跳过等待）> ")
            reveal = True
        except EOFError:
            reveal = False

    if reveal:
        post_trade = svc.reveal_decision(did)
        render_post_trade(decision_obj, post_trade)
    else:
        print("\n  Post-trade 未揭晓（运行加 --auto-reveal 可自动查看）。")

    # ================= Audit Panel =================
    render_audit(decision_obj)

    # ================= 审计 JSON（同一 DecisionSnapshot）=================
    if args.json_out:
        out_path = Path(args.json_out)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(decision_obj, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[audit json] -> {out_path.resolve()}")

    print()
    print("─" * 92)
    print("  复盘闭环完成。本 Demo 每一步可审计、可解释、不穿越；")
    print("  决策对象与 Web / LLM Tools 为同一 DecisionSnapshot（decision_id 一致）。")


if __name__ == "__main__":
    main()
