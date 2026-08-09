# -*- coding: utf-8 -*-
"""
agent/explanation/cards.py

基于 test 预测生成历史 Decision Card（V0.2 白盒交易决策 Agent · 模块 3/3）。

Production Explanation 边界（V0.2，单一 Production Predictive Model）：
  本模块只解释：
    Predictive Model Output + Feature Contribution + Agent Evidence +
    Similar Historical Cases + Risk Gate + White-box Rule Engine。
  Rule Baseline = benchmark / 回测基线；Interpretable = 开发与验证工具
  （特征方向 sanity check）。二者均【不参与线上 BUY/SELL 投票】。

数据说明（本文件是历史演示卡，非线上推理）：
  - 线上生产模型是 predictions_v2.csv（Agent B / model_v2.py 整合预测）；
    本演示卡按 CARD_SELECT 显式选行，rule 来源卡片演示"模型输出 → 依据 →
    Evidence → Risk Gate → 建议"的完整链路（Rule = benchmark 兜底），
    interpretable 来源卡片仅用于演示 Risk Gate 对 CONTROLX BUY（R7a）与
    ELCA 低样本（R6）的 REJECT（offline validation，非线上投票）。
  - 特征：canonical.parquet（as-of）+ risk_features.parquet（hist_n/lag1_pct 等，
    全部 ≤ target_date-2，见 code/tmp/agent_d_features.py）。

输出：
  - agent/explanation/decision_cards.json
  - agent/explanation/decision_cards_preview.md

口径：
  - 卡片只用决策时点可见信息 + 模型输出；不写目标日实际价作为依据。
  - Risk Gate 引用已校准规则（R7a/R6/R4 降级为警告），不发明新规则。
  - 不判方向：建议动作来自模型输出，本模块只组织/解释。

运行：python agent/explanation/cards.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from agent.evidence.fetcher import compile_evidence_context  # noqa: E402
from agent.explanation.decision_card import (  # noqa: E402
    DecisionCard,
    RiskGateResult,
    cards_to_json,
    cards_to_markdown_preview,
)

DATA_DIR = REPO_ROOT / "code" / "data"
OUT_DIR = REPO_ROOT / "agent" / "explanation"
CARDS_JSON = OUT_DIR / "decision_cards.json"
CARDS_MD = OUT_DIR / "decision_cards_preview.md"

TEST_START = "2026-06-02"   # test 窗口起点（参考 canonical split）

# 需要生成卡片的 (node, target_date, hour, model_source)
CARD_SELECT: List[tuple] = [
    # ---- rule 来源卡片（8 张）----
    ("CONTROLX_1_N001", "2026-06-30", 14, "rule"),   # 06-30 DA 崩塌日 rule SELL（-1175 行）
    ("CONTROLX_1_N001", "2026-06-30", 8, "rule"),    # 06-30 高置信 rule SELL（conf 0.94，-894 行）
    ("CONTROLX_1_N001", "2026-07-09", 2, "rule"),    # 07-09 RTPD -2365（rule SELL 大赚 +2216）
    ("SNLNDRO_1_N001", "2026-06-02", 19, "rule"),    # SNLNDRO SELL（安全节点，gate PASS）
    ("SNLNDRO_1_N001", "2026-06-02", 7, "rule"),     # SNLNDRO SELL（清晨）
    ("ELCAJNGT_7_N001", "2026-06-05", 20, "rule"),   # ELCA 高置信 SELL（conf 1.0，低样本 REJECT）
    ("CONTROLX_1_N001", "2026-07-01", 1, "rule"),    # NO_TRADE 观望示例
    ("SNLNDRO_1_N001", "2026-08-03", 14, "rule"),    # SNLNDRO 罕见 BUY（rule）
    # ---- interpretable 来源卡片（4 张，演示 R7a / R6 REJECT）----
    ("CONTROLX_1_N001", "2026-07-17", 3, "interpretable"),   # 全样本最大单笔亏损 -2251
    ("CONTROLX_1_N001", "2026-06-30", 19, "interpretable"),  # 06-30 DA 崩盘 BUY（+1305 盈利行）
    ("CONTROLX_1_N001", "2026-06-26", 5, "interpretable"),   # 高置信 BUY(0.969) 错方向 -1039
    ("ELCAJNGT_7_N001", "2026-07-24", 20, "interpretable"),  # ELCA RTPD 尖峰事件（低样本 REJECT）
]


# ---------------------------------------------------------------------------
# 数据加载与合并
# ---------------------------------------------------------------------------

def _load_merged() -> pd.DataFrame:
    canonical = pd.read_parquet(DATA_DIR / "canonical.parquet")
    risk = pd.read_parquet(DATA_DIR / "stage3" / "risk_features.parquet")
    rule = pd.read_csv(DATA_DIR / "predictions_rule.csv", encoding="utf-8-sig")
    interp = pd.read_csv(DATA_DIR / "predictions_interpretable.csv", encoding="utf-8-sig")
    cat = pd.read_csv(DATA_DIR / "predictions_catboost.csv", encoding="utf-8-sig")

    # 统一 target_date 为 YYYY-MM-DD 字符串，避免 datetime/str 混键
    for df in (canonical, risk, rule, interp, cat):
        df["target_date"] = df["target_date"].astype(str).str[:10]
        df["hour"] = df["hour"].astype(int)

    # 断言唯一键
    for name, df in [
        ("canonical", canonical),
        ("risk", risk),
        ("rule", rule),
        ("interp", interp),
        ("cat", cat),
    ]:
        dup = df.duplicated(subset=["node", "target_date", "hour"]).sum()
        assert dup == 0, f"{name} 有重复键: {dup}"

    def _pred(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        sub = df[["node", "target_date", "hour", "pred_direction",
                  "expected_return", "confidence"]].copy()
        sub = sub.rename(columns={
            "pred_direction": f"{prefix}_dir",
            "expected_return": f"{prefix}_er",
            "confidence": f"{prefix}_conf",
        })
        return sub

    r = _pred(rule, "rule")
    i = _pred(interp, "interp")
    c = _pred(cat, "cat")

    m = (
        canonical.merge(risk.drop(columns=["split"]), on=["node", "target_date", "hour"], how="left")
        .merge(r, on=["node", "target_date", "hour"], how="left")
        .merge(i, on=["node", "target_date", "hour"], how="left")
        .merge(c, on=["node", "target_date", "hour"], how="left")
    )
    return m


# ---------------------------------------------------------------------------
# 量化依据 & 风险门 & 风险清单
# ---------------------------------------------------------------------------

def _basis_bullets(row: pd.Series, hour: int, model_source: str) -> List[str]:
    bullets: List[str] = []

    m30 = row.get("spread_mean30")
    if m30 is not None and not (isinstance(m30, float) and m30 != m30):
        side = "偏正" if m30 > 20 else ("偏负" if m30 < -20 else "中性")
        bullets.append(f"同节点HE{hour}近30日Return{side}({m30:+.1f})")

    load = row.get("load_2da_forecast")
    load_mean = row.get("_load_mean_node")
    if load is not None and load_mean is not None and not (isinstance(load, float) and load != load):
        cmp = "高于" if load > load_mean * 1.02 else ("低于" if load < load_mean * 0.98 else "接近")
        bullets.append(f"负荷预测{load:.0f}{cmp}节点均值{load_mean:.0f}")

    std30 = row.get("spread_std30")
    if std30 is not None and not (isinstance(std30, float) and std30 != std30):
        lvl = "很高" if std30 > 400 else ("较高" if std30 > 250 else ("中等" if std30 > 150 else "较低"))
        bullets.append(f"近30日该小时波动±{std30:.0f}({lvl})")

    lag1_pct = row.get("lag1_pct")
    if lag1_pct is not None and not (isinstance(lag1_pct, float) and lag1_pct != lag1_pct):
        bullets.append(f"决策时spread_lag1处于历史{lag1_pct * 100:.0f}%分位")

    conf = row.get("model_conf")
    if conf is not None and not (isinstance(conf, float) and conf != conf) and conf >= 0.9:
        node = row.get("node", "")
        if "CONTROLX" in node:
            bullets.append(f"高置信({conf:.2f})在CONTROLX上不可靠(R1已删除)")

    hist_n = row.get("hist_n")
    if hist_n is not None and not (isinstance(hist_n, float) and hist_n != hist_n):
        bullets.append(f"同节点HE{hour}历史样本n={int(hist_n)}")

    if model_source != "rule":
        bullets.append(f"来源模型={model_source}")
    return bullets


def _risk_gate(row: pd.Series, suggested_action: str) -> RiskGateResult:
    """透明引用已校准 Risk Gate 规则（R7a/R6/R4 警告级）。

    V0.2 正式 Risk Gate（code/risk_gate/rules.py）只消费单一生产模型输出 +
    as-of 风险特征 + eligible Evidence，不含三模型一致性投票。
    """
    node = str(row.get("node", ""))
    hist_n = row.get("hist_n")
    if isinstance(hist_n, float) and hist_n != hist_n:
        hist_n = None

    # R7a —— 结构事实：CONTROLX 正漂移 + BUY 无条件负期望 → REJECT
    if "CONTROLX" in node and suggested_action == "BUY_DA":
        return RiskGateResult(
            status="REJECT",
            reasons=["BUY_ON_POSITIVE_DRIFT_NODE"],
            note="CONTROLX 无条件漂移 +9.68，BUY 逆漂移且尾极深（train+val 验证，见 risk_gate_design R7a）。",
        )
    # R6 —— 低样本（cold-start）→ REJECT
    if hist_n is not None and hist_n < 200:
        return RiskGateResult(
            status="REJECT",
            reasons=["LOW_SAMPLE_SUPPORT"],
            note=f"同节点HE{row.get('hour')}历史样本仅 {int(hist_n)}（<200，cold-start），统计不可靠。",
        )
    # R4 —— 已知重尾节点 → 警告（已由 REJECT 降级为 PASS_WITH_WARNING）
    if "CONTROLX" in node:
        return RiskGateResult(
            status="PASS_WITH_WARNING",
            reasons=["EXTREME_TAIL_NODE"],
            note="CONTROLX 为已知重尾节点（历史 ±900~3656）；R4 已验证无法用历史尾部事前识别具体单日，仅作警告。",
        )
    return RiskGateResult(status="PASS", reasons=["NONE"], note="")


def _main_risks(row: pd.Series, suggested_action: str, rg: RiskGateResult) -> List[str]:
    risks: List[str] = []
    node = str(row.get("node", ""))
    std30 = row.get("spread_std30")
    lag1_pct = row.get("lag1_pct")

    if "CONTROLX" in node:
        risks.append("CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh")
        if suggested_action == "BUY_DA":
            risks.append("CONTROLX BUY 逆 +84 漂移，train+val 无条件负期望")
        elif suggested_action == "SELL_DA":
            risks.append("CONTROLX SELL 存在 DA 崩塌尾（如 06-30 DA -1313），Risk Gate 不覆盖")
    if "ELCA" in node:
        risks.append("ELCA 历史样本少（n<200，cold-start），统计不可靠")
    if std30 is not None and not (isinstance(std30, float) and std30 != std30) and std30 > 250:
        risks.append(f"近30日该小时波动较高(±{std30:.0f})")
    if lag1_pct is not None and not (isinstance(lag1_pct, float) and lag1_pct != lag1_pct):
        if lag1_pct > 0.95 or lag1_pct < 0.05:
            risks.append(f"决策时 spread 处于历史极值分位({lag1_pct * 100:.0f}%)")
    if rg.status == "REJECT":
        risks.append("Risk Gate 判定 REJECT，建议不执行该方向")
    return risks


def _final_recommendation(card: DecisionCard) -> str:
    rg = card.risk_gate
    if rg.status == "REJECT":
        return (
            f"不建议执行 {card.suggested_action}（Risk Gate REJECT: "
            f"{','.join(rg.reasons)}）。若必须交易，需人工复核并显著降仓。"
        )
    if rg.status == "PASS_WITH_WARNING":
        return f"可考虑执行 {card.suggested_action}，但需人工复核风险警告（{','.join(rg.reasons)}）。"
    return f"可按模型建议执行 {card.suggested_action}（Risk Gate PASS）。"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_cards() -> List[DecisionCard]:
    m = _load_merged()

    # 每节点负荷预测均值（只用 test 之前的行，保证 as-of）
    pre = m[m["target_date"].astype(str) < TEST_START]
    load_mean = pre.groupby("node")["load_2da_forecast"].mean()
    m["_load_mean_node"] = m["node"].map(load_mean)

    # 按 key 建立索引便于取行
    m_idx = m.set_index(["node", "target_date", "hour"])

    cards: List[DecisionCard] = []
    seq = 1
    for node, td, hour, source in CARD_SELECT:
        key = (node, td, int(hour))
        if key not in m_idx.index:
            print(f"[skip] 缺失 key: {key}")
            continue
        row = m_idx.loc[key]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        # set_index 后 node/target_date/hour 在索引里，放回列便于按列名读取
        row = row.copy()
        row["node"] = node
        row["target_date"] = td
        row["hour"] = int(hour)

        if source == "rule":
            act_dir, er, conf = row["rule_dir"], row["rule_er"], row["rule_conf"]
        elif source == "interpretable":
            act_dir, er, conf = row["interp_dir"], row["interp_er"], row["interp_conf"]
        elif source == "catboost":
            act_dir, er, conf = row["cat_dir"], row["cat_er"], row["cat_conf"]
        else:  # 兜底：Rule Baseline（benchmark），生产模型 predictions_v2 未接入本演示卡
            act_dir, er, conf = row["rule_dir"], row["rule_er"], row["rule_conf"]

        act_dir = int(act_dir) if act_dir == act_dir else 0
        suggested = {1: "SELL_DA", -1: "BUY_DA", 0: "NO_TRADE"}[act_dir]

        decision_date = str(row["decision_date"])[:10]
        rg = _risk_gate(row, suggested)
        basis = _basis_bullets(row, int(row["hour"]), source)
        card = DecisionCard(
            card_id=f"CARD-{seq:03d}",
            node=node,
            decision_date=decision_date,
            target_date=td,
            hour=int(row["hour"]),
            model_source=source,
            suggested_action=suggested,
            expected_return=float(er) if er == er else 0.0,
            confidence=float(conf) if conf == conf else 0.0,
            key_quantitative_basis=basis,
            agent_evidence=compile_evidence_context(node, decision_date, hours=[int(row["hour"])]),
            risk_gate=rg,
            main_risks=_main_risks(row, suggested, rg),
            final_recommendation="",
            human_confirmation_note="最终执行由交易员确认",
        )
        card.final_recommendation = _final_recommendation(card)
        cards.append(card)
        seq += 1
    return cards


def main() -> None:
    cards = build_cards()
    assert len(cards) >= 10, f"卡片数不足: {len(cards)}"

    meta = {
        "module": "agent/explanation",
        "version": "0.2",
        "description": (
            "Decision Card 历史预览（test 窗口 2026-06-02~08-05），V0.2 单一生产模型口径。"
            "生产模型 predictions_v2.csv 已生成，本演示卡按 CARD_SELECT 显式选行："
            "rule 来源卡片（Rule = benchmark 兜底）演示完整链路；"
            "另附 interpretable 卡片（Interpretable = 开发/验证工具，非线上投票）"
            "演示 Risk Gate 对 CONTROLX BUY / ELCA 低样本的 REJECT。"
        ),
        "prediction_source": "predictions_rule.csv / predictions_interpretable.csv（curated demo；线上生产模型 = predictions_v2.csv）",
        "evidence_note": "Agent Evidence 全部 UNCERTAIN（当前无真实外部数据源，LLM 未判方向）",
        "risk_gate_note": (
            "引用已校准规则 R7a/R6/R4（警告级），透明、非新发明；"
            "完整说明见 docs/stage3/risk_gate_design.md"
        ),
        "card_count": len(cards),
        "note": "卡片只组织/解释，方向由 Rule Engine/交易员决定。",
    }

    cards_to_json(cards, CARDS_JSON, meta=meta)
    cards_to_markdown_preview(
        cards, CARDS_MD,
        header="# Decision Card 预览（test 窗口）\n\n"
               "> 全部为真实日期/节点/小时；Evidence 全 UNCERTAIN；"
               "方向由 Rule Engine/交易员决定。\n",
    )

    print(f"decision_cards.json -> {CARDS_JSON}")
    print(f"decision_cards_preview.md -> {CARDS_MD}")
    print(f"card count: {len(cards)}")
    for c in cards:
        print(
            f"{c.card_id:>9} | {c.target_date} | {c.node} H{c.hour} "
            f"| {c.model_source} | {c.suggested_action} | exp={c.expected_return:+.1f} "
            f"conf={c.confidence:.2f} | gate={c.risk_gate.status}"
        )


if __name__ == "__main__":
    main()
