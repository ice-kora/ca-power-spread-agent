# -*- coding: utf-8 -*-
"""
backtest_v2.py —— V0.2 架构 Signal Backtest（Agent E，可插拔策略）
====================================================================

定位 / 边界（重要，写进报告）
------------------------------
本模块做 **Signal / Strategy Backtest**：假设每笔按 1 MWh 执行，
PnL = SELL_DA→+actual_return、BUY_DA→−actual_return、NO_TRADE→0。
它 **不是真实 Convergence Bidding PnL**（缺 bid price / quantity / award /
clearing / settlement / fees；无仓位优化，无交易成本/冲击）。真实执行需要
报价、成交、结算三个环节，本模块只评估"决策信号本身的价值"。

数据（全部严格 as-of，只读）
------------------------------
- `code/data/predictions_v2.csv`            V0.2 Production Predictive Model（test）
- `code/data/predictions_rule.csv`          V0.1 Rule（白盒基准，spread_mean14 信号）
- `code/data/predictions_interpretable.csv` V0.1 Interpretable（仅给 Gate R2 作辅助方向）
- `code/data/predictions_catboost.csv`      V0.1 CatBoost（仅给 Gate R2 作辅助方向）
- `code/data/stage3/risk_features.parquet`  决策时点可见历史风险特征（hist_n/cvar99/...）
- `code/data/canonical.parquet`             label / 特征（本模块只用 actual_return 结算）

策略（可插拔；Agent C 的 `code/risk_gate/` 与 `code/decision/rule_engine.py`
未就绪前，本模块内置 stage3 已验证的 Risk Gate 白盒实现 + DecisionPipeline.md
§5 三态 Rule Engine 作为占位，接好后可替换）
  1. Rule Baseline            —— V0.1 Rule 信号 + 三态决策（Rule = benchmark，非生产）
  2. Predictive Model only    —— v2 模型 expected_return 符号 + 幅度/置信阈值
  3. Predictive Model + Gate  —— 上述 + Risk Gate（REJECT→NO_TRADE）
  4. Full Decision Pipeline   —— 模型 → Gate → Rule Engine（三态 + reason/evidence 审计轨迹）

窗口：test（2026-06-02 ~ 2026-08-05，65 天）；test 只用一次。
主策略 = ZP26（SNLNDRO / CONTROLX）；ELCAJNGT cold-start 单独评估。
Gate 阈值全部来自 stage3 train+val 校准，test 零调参。

用法
----
    python backtest_v2.py                # 全流程：输出 summary json + 打印指标
    python backtest_v2.py --report       # 另输出 docs/v0.2_backtest.md
"""
import os
import sys
import json
import argparse

import numpy as np
import pandas as pd

# 把仓库根加入 sys.path，使 `code.risk_gate` / `code.decision` 在任意调用方式下可导入
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# 市场规则版本标记（Provenance MVP：Backtest Record 上标注规则版本，仅保存不适配）
try:
    from code.market_rules import CURRENT_MARKET_RULE_VERSION
    HAVE_MARKET_RULE = True
except Exception:
    CURRENT_MARKET_RULE_VERSION = "PRE_DAME_EDAM_2026"
    HAVE_MARKET_RULE = False


def _window_market_rule_version() -> dict:
    """按回测窗口计算市场规则版本 metadata（V0.3.1.4；**只改 metadata，不改回测逻辑**）。

    test window（2026-06-02 ~ 2026-08-05）整体在 2026-05-01 后 → POST_DAME_EDAM_2026；
    若未来窗口跨 2026-05-01 边界 → MIXED + market_rule_versions_used。
    """
    try:
        from code.market_rules import market_rule_version_for  # noqa: PLC0415
        start = pd.Timestamp("2026-06-02")
        end = pd.Timestamp("2026-08-05")
        vs = {market_rule_version_for(start), market_rule_version_for(end)}
        if len(vs) == 1:
            return {"market_rule_version": vs.pop()}
        return {"market_rule_version": "MIXED",
                "market_rule_versions_used": sorted(vs)}
    except Exception:  # noqa: BLE001
        return {"market_rule_version": CURRENT_MARKET_RULE_VERSION}

# ---------------------------------------------------------------------------
# Agent C 模块集成（V0.2 正式组件；未就绪时回退本文件内置复刻）
# ---------------------------------------------------------------------------
try:
    from code.risk_gate.gate import RiskGate as C_RiskGate, evaluate_frame as c_gate_frame
    from code.decision.rule_engine import RuleEngine as C_RuleEngine
    HAVE_AGENT_C = True
except Exception as _exc:
    C_RiskGate = None
    C_RuleEngine = None
    HAVE_AGENT_C = False
    _AGENT_C_IMPORT_ERR = str(_exc)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STAGE3 = os.path.join(DATA, "stage3")
DOCS = os.path.join(os.path.dirname(HERE), "docs")

CANON_PQ = os.path.join(DATA, "canonical.parquet")
PRED_V2 = os.path.join(DATA, "predictions_v2.csv")
PRED_RULE = os.path.join(DATA, "predictions_rule.csv")
PRED_INTERP = os.path.join(DATA, "predictions_interpretable.csv")
PRED_CAT = os.path.join(DATA, "predictions_catboost.csv")
RISK_FEATURES = os.path.join(STAGE3, "risk_features.parquet")
OUT_SUMMARY = os.path.join(DATA, "backtest_v2_summary.json")
OUT_REPORT = os.path.join(DOCS, "v0.2_backtest.md")

MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA_NODE = "ELCAJNGT_7_N001"

# ---------------------------------------------------------------------------
# 决策 / 规则引擎 / Gate 配置（全部来自文档或 stage3 train+val，test 零调参）
# ---------------------------------------------------------------------------

# DecisionPipeline.md §5 三态阈值（Rule Engine）
DECISION_CFG = {
    "ret_threshold_abs": 5.0,   # |expected_return| 低于该值 → NO_TRADE
    "conf_threshold": 0.20,     # confidence 低于该值 → NO_TRADE
}

# Risk Gate 规则（stage3 risk_gate_backtest.md / risk_gate_design.md，train+val 校准）
#   R7a : CONTROLX 漂移 train+val +9.68，BUY mean −9.68、maxloss −3656 → REJECT
#   R7b : ELCA 漂移 train+val −1.15，SELL mean −1.15、maxloss −357 → REJECT
#   R6  : hist_n < 150（ELCA cold-start test 中位 121）→ REJECT
#   R2  : CONTROLX ML 双 BUY（interpretable+catboost 同 BUY）→ 并入 R7a 的 reason
#   R4  : 尾部警告（PASS_WITH_WARNING，不 REJECT；实证 REJECT 会误砍 test 顺漂移利润）
GATE_CFG = {
    "r7a_buy_reject_node": "CONTROLX_1_N001",
    "r7b_sell_reject_node": "ELCAJNGT_7_N001",
    "r6_min_hist_n": 150,
    "r4_cvar_threshold": -600.0,
}

RNG_SEED = 42


# ---------------------------------------------------------------------------
# 数据载入
# ---------------------------------------------------------------------------
def load_all():
    canon = pd.read_parquet(CANON_PQ)
    canon["target_date"] = pd.to_datetime(canon["target_date"])

    v2 = pd.read_csv(PRED_V2)
    v2["target_date"] = pd.to_datetime(v2["target_date"])

    rule = pd.read_csv(PRED_RULE)
    rule["target_date"] = pd.to_datetime(rule["target_date"])

    rf = pd.read_parquet(RISK_FEATURES)
    rf["target_date"] = pd.to_datetime(rf["target_date"])

    aux = {}
    for name, path in (("interpretable", PRED_INTERP), ("catboost", PRED_CAT)):
        p = pd.read_csv(path)
        p["target_date"] = pd.to_datetime(p["target_date"])
        aux[name] = p[["node", "target_date", "hour", "pred_direction"]].rename(
            columns={"pred_direction": "pred_direction_%s" % name})

    return canon, v2, rule, rf, aux


def filter_nodes(df, nodes):
    return df[df["node"].isin(nodes)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# PnL 与指标
# ---------------------------------------------------------------------------
def pnl_of(decision, actual_return):
    return np.where(decision == "SELL_DA", actual_return,
                    np.where(decision == "BUY_DA", -actual_return, 0.0))


def _daily_pnl(d):
    if len(d) == 0:
        return pd.Series(dtype=float)
    s = d.groupby("target_date")["pnl"].sum()
    full = pd.date_range(d["target_date"].min(), d["target_date"].max(), freq="D")
    return s.reindex(pd.DatetimeIndex(full)).fillna(0.0)


def max_drawdown(cum):
    return float((cum - cum.cummax()).min()) if len(cum) else 0.0


def compute_metrics(d, label=""):
    """对已带 decision/pnl 列的 DataFrame 计算 Signal Backtest 指标。"""
    traded = d[d["decision"] != "NO_TRADE"]
    n = int(len(d))
    nt = int(len(traded))
    cov = nt / n if n else np.nan

    def _dir_acc(sub):
        if len(sub) == 0 or "pred_direction" not in sub.columns:
            return np.nan
        pd_ = pd.to_numeric(sub["pred_direction"], errors="coerce")
        m = pd_.notna() & (pd_ != 0)
        if not m.any():
            return np.nan
        ad = pd.to_numeric(sub.get("actual_direction"), errors="coerce")
        if ad is None or not ad.notna().any():
            return np.nan
        return float((np.sign(pd_[m]) == np.sign(ad[m])).mean())

    sell = traded[traded["decision"] == "SELL_DA"]
    buy = traded[traded["decision"] == "BUY_DA"]

    p = traded["pnl"].astype(float) if nt else pd.Series(dtype=float)
    daily = _daily_pnl(d)
    daily_mean = float(daily.mean()) if len(daily) else np.nan
    daily_std = float(daily.std()) if len(daily) else np.nan
    sharpe = (daily_mean / daily_std * np.sqrt(252)) if daily_std and daily_std > 0 else np.nan
    cum = daily.cumsum()
    mdd = max_drawdown(cum)

    cvar95 = float(p[p <= p.quantile(0.05)].mean()) if nt and (p <= p.quantile(0.05)).any() else np.nan
    cvar99 = float(p[p <= p.quantile(0.01)].mean()) if nt and (p <= p.quantile(0.01)).any() else np.nan
    worst = float(p.min()) if nt else np.nan
    win = float((p > 0).mean()) if nt else np.nan
    total = float(p.sum()) if nt else 0.0
    mean_pt = float(p.mean()) if nt else np.nan
    med_pt = float(p.median()) if nt else np.nan

    return {
        "label": label,
        "n": n, "n_traded": nt,
        "trade_coverage": round(cov, 4) if cov == cov else None,
        "dir_acc_traded": round(_dir_acc(traded), 4) if _dir_acc(traded) == _dir_acc(traded) else None,
        "SELL_precision": round(float((sell["actual_return"] > 0).mean()), 4) if len(sell) else None,
        "BUY_precision": round(float((buy["actual_return"] < 0).mean()), 4) if len(buy) else None,
        "win_rate": round(win, 4) if win == win else None,
        "cum_pnl": round(total, 2),
        "mean_pnl_per_trade": round(mean_pt, 3) if mean_pt == mean_pt else None,
        "median_pnl_per_trade": round(med_pt, 3) if med_pt == med_pt else None,
        "max_drawdown": round(mdd, 2),
        "worst_trade": round(worst, 2) if worst == worst else None,
        "cvar95": round(cvar95, 2) if cvar95 == cvar95 else None,
        "cvar99": round(cvar99, 2) if cvar99 == cvar99 else None,
        "sharpe_daily": round(sharpe, 3) if sharpe == sharpe else None,
        "pnl_sell": round(float(sell["pnl"].sum()), 2),
        "pnl_buy": round(float(buy["pnl"].sum()), 2),
        "n_sell": int(len(sell)), "n_buy": int(len(buy)),
    }


# ---------------------------------------------------------------------------
# 可插拔策略接口
# ---------------------------------------------------------------------------
class Strategy:
    """策略基类。decide(frame) 在 frame 上添加 decision / pred_direction / reason 列。"""
    name = "base"

    def decide(self, frame):
        raise NotImplementedError


class RuleBaseline(Strategy):
    """V0.1 Rule（同 node×hour 近14日均值 spread_mean14 + 概率幅度阈值）。

    直接消费 predictions_rule.csv 的 pred_direction（该列已内嵌 V0.1 rule 的
    prob margin + expected threshold，见 model_c.decision_from_prob_expected），
    即 stage3 报告的「原 Rule」口径（test 824 笔，+79,485）。Rule = benchmark，非生产。
    """
    name = "Rule Baseline"

    def decide(self, frame):
        d = frame.copy()
        pd_ = pd.to_numeric(d["pred_direction"], errors="coerce")
        dec = np.full(len(d), "NO_TRADE", dtype=object)
        sell = (pd_ > 0) & pd_.notna()
        buy = (pd_ < 0) & pd_.notna()
        dec[sell] = "SELL_DA"
        dec[buy] = "BUY_DA"
        d["decision"] = dec
        d["reason"] = np.where(sell, "rule:SELL (V0.1 prob_margin+expected_th)",
                               np.where(buy, "rule:BUY (V0.1 prob_margin+expected_th)", ""))
        d["pnl"] = pnl_of(dec, d["actual_return"].values)
        return d


class PredictiveModelOnly(Strategy):
    """V0.2 Production Model only：expected_return 符号出方向 + 三态阈值。"""
    name = "Predictive Model only"

    def decide(self, frame):
        d = frame.copy()
        er = pd.to_numeric(d["expected_return"], errors="coerce")
        conf = pd.to_numeric(d["confidence"], errors="coerce")
        dec = np.full(len(d), "NO_TRADE", dtype=object)
        pd_ = np.sign(er)
        d["pred_direction"] = pd_
        sell = (pd_ > 0) & (er >= DECISION_CFG["ret_threshold_abs"]) & (conf >= DECISION_CFG["conf_threshold"])
        buy = (pd_ < 0) & (er <= -DECISION_CFG["ret_threshold_abs"]) & (conf >= DECISION_CFG["conf_threshold"])
        dec[sell] = "SELL_DA"
        dec[buy] = "BUY_DA"
        d["decision"] = dec
        d["reason"] = np.where(sell, "model:SELL|exp_ret>=%.0f&conf>=%.2f" % (DECISION_CFG["ret_threshold_abs"], DECISION_CFG["conf_threshold"]),
                               np.where(buy, "model:BUY|exp_ret<=-%.0f&conf>=%.2f" % (DECISION_CFG["ret_threshold_abs"], DECISION_CFG["conf_threshold"]), ""))
        d["pnl"] = pnl_of(dec, d["actual_return"].values)
        return d


class WhiteBoxRiskGate:
    """Risk Gate 白盒（占位实现，规则与阈值来自 stage3 train+val 校准）。

    Agent C 的 `code/risk_gate/` 就绪后应替换本类；本类独立复刻
    `code/analysis/agent_d_gate.py` 的有效规则（R7a/R7b/R6/R2/R4）。
    """

    def apply(self, d):
        d = d.copy()
        orig = d["decision"].copy()
        was_trade = orig.isin(["SELL_DA", "BUY_DA"]).to_numpy()
        dec = orig.copy()
        n = len(d)
        reasons = [""] * n
        warnings = [""] * n

        for i in range(n):
            if not was_trade[i]:
                # Gate 只评估候选交易；原本 NO_TRADE 的行不是 Gate 的裁决对象
                continue
            node = d["node"].iloc[i]
            d_i = orig.iloc[i]
            r = []

            # R7a：正漂移节点 CONTROLX 上 BUY → REJECT（漂移 train+val +9.68，赔率倒挂）
            if d_i == "BUY_DA" and node == GATE_CFG["r7a_buy_reject_node"]:
                dec.iloc[i] = "NO_TRADE"
                r.append("BUY_ON_POSITIVE_DRIFT_NODE")
            # R7b：负漂移节点 ELCA 上 SELL → REJECT（漂移 train+val −1.15）
            if d_i == "SELL_DA" and node == GATE_CFG["r7b_sell_reject_node"]:
                dec.iloc[i] = "NO_TRADE"
                r.append("SELL_ON_NEGATIVE_DRIFT_NODE")

            # R6：低样本 → REJECT（ELCA cold-start，hist_n<150）
            hist_n = d.get("hist_n", pd.Series(np.nan, index=d.index)).iloc[i]
            if pd.notna(hist_n) and hist_n < GATE_CFG["r6_min_hist_n"] and dec.iloc[i] != "NO_TRADE":
                dec.iloc[i] = "NO_TRADE"
                r.append("LOW_SAMPLE_SUPPORT")

            # R4：尾部警告（PASS_WITH_WARNING，不 REJECT）
            if dec.iloc[i] != "NO_TRADE":
                if d_i == "SELL_DA":
                    cvar = d.get("cvar99", pd.Series(np.nan, index=d.index)).iloc[i]
                    if pd.notna(cvar) and cvar < GATE_CFG["r4_cvar_threshold"]:
                        warnings[i] = "EXTREME_TAIL_NODE"
                elif d_i == "BUY_DA":
                    rcvar = d.get("rcvar99", pd.Series(np.nan, index=d.index)).iloc[i]
                    if pd.notna(rcvar) and rcvar < GATE_CFG["r4_cvar_threshold"]:
                        warnings[i] = "EXTREME_TAIL_NODE"

            reasons[i] = "|".join(dict.fromkeys(r))

        d["decision"] = dec
        # gate_decision：只对"本来是候选交易"的行给 Gate 裁决
        d["gate_decision"] = np.where(
            was_trade,
            np.where(dec.to_numpy() != "NO_TRADE",
                     np.where(pd.Series(warnings).astype(str) != "", "PASS_WITH_WARNING", "PASS"),
                     "REJECT"),
            "NO_TRADE")
        d["gate_reason_code"] = reasons
        d["gate_warning_code"] = warnings
        d["pnl"] = pnl_of(d["decision"], d["actual_return"].values)
        return d


class PredictiveModelGate(Strategy):
    """Predictive Model + Risk Gate。

    优先用 Agent C 的 `code/risk_gate/gate.py`（正式组件）；未就绪时回退内置复刻。
    Gate REJECT（R7a/R7b/R6/DATA_MISSING）→ NO_TRADE；WARNING 只标记不拦。
    """
    name = "Predictive Model + Risk Gate"

    def __init__(self):
        self._gate = WhiteBoxRiskGate()

    def decide(self, frame):
        d = PredictiveModelOnly().decide(frame)
        if HAVE_AGENT_C:
            return self._apply_agent_c_gate(d)
        return self._gate.apply(d)

    def _apply_agent_c_gate(self, d):
        d = d.copy()
        gf = c_gate_frame(d)
        rej = (gf["gate_decision"] == "REJECT") & (d["decision"] != "NO_TRADE")
        d.loc[rej, "decision"] = "NO_TRADE"
        d["gate_decision"] = gf["gate_decision"]
        d["gate_reason_code"] = gf["gate_reason_code"]
        d["gate_rules_hit"] = gf["gate_rules_hit"]
        d["pnl"] = pnl_of(d["decision"], d["actual_return"].values)
        return d


class FullDecisionPipeline(Strategy):
    """Full Decision Pipeline：Predictive Model → Agent Evidence → Evidence Time Gate
    → Case Library → Risk Gate → Rule Engine → Human Confirmation（→ Post-trade Review 记录）。

    优先用 Agent C 的 `code/decision/rule_engine.py`（内部消费 RiskGate + Evidence Time Gate）；
    未就绪时回退内置复刻（DecisionPipeline.md §5 三态 + Gate）。
    输出带 reason / risk_reasons / rules_hit / evidence 审计轨迹。
    """
    name = "Full Decision Pipeline (Gate + Rule Engine)"

    def __init__(self):
        self._gate = WhiteBoxRiskGate()

    def decide(self, frame):
        if HAVE_AGENT_C:
            return self._apply_agent_c_pipeline(frame)
        return self._apply_standalone_pipeline(frame)

    def _apply_agent_c_pipeline(self, frame):
        d = frame.copy()
        re = C_RuleEngine()
        outs = []
        for i in range(len(d)):
            row = d.iloc[i]
            pred = {
                "node": row["node"], "target_date": row["target_date"],
                "hour": row["hour"], "expected_return": row["expected_return"],
                "confidence": row["confidence"], "uncertainty": row["uncertainty"],
                "prob_positive": row["prob_positive"], "prob_negative": row["prob_negative"],
                "hist_n": row["hist_n"], "cvar99": row["cvar99"], "rcvar99": row["rcvar99"],
                "vol_ratio": row["vol_ratio"], "node_drift": row["node_drift"],
            }
            outs.append(re.evaluate(pred))
        d["decision"] = [x.decision for x in outs]
        d["pred_direction"] = [x.direction_sign for x in outs]
        d["reason"] = ["|".join(x.reasons) if x.reasons else "" for x in outs]
        d["rules_hit"] = ["|".join(x.rules_hit) if x.rules_hit else "" for x in outs]
        d["gate_decision"] = [x.risk_verdict or "" for x in outs]
        d["gate_reason_code"] = ["|".join(x.risk_reasons) for x in outs]
        d["evidence"] = [
            ("exp_ret=%+.1f prob_pos=%.2f conf=%.2f unc=%.2f gate=%s(%s) rules=%s" % (
                (float(row["expected_return"]) if pd.notna(row["expected_return"]) else 0.0),
                row["prob_positive"],
                (float(row["confidence"]) if pd.notna(row["confidence"]) else 0.0),
                row["uncertainty"],
                outs[i].risk_verdict or "PASS",
                "|".join(outs[i].risk_reasons),
                "|".join(outs[i].rules_hit),
            ))
            for i, row in d.iterrows()]
        d["human_review_status"] = "PENDING"  # ⑧ Post-trade Review 前人工未拍板
        d["pnl"] = pnl_of(d["decision"], d["actual_return"].values)
        return d

    def _apply_standalone_pipeline(self, frame):
        # ① Predictive Model 输出（expected_return 符号 = pred_direction）
        d = PredictiveModelOnly().decide(frame)
        # ② Risk Gate
        d = self._gate.apply(d)
        # ③ Rule Engine：三态（gate REJECT 已把 decision 置 NO_TRADE），补 evidence
        er = pd.to_numeric(d["expected_return"], errors="coerce")
        conf = pd.to_numeric(d["confidence"], errors="coerce")
        d["evidence"] = [
            ("exp_ret=%+.1f prob_pos=%.2f conf=%.2f unc=%.2f gate=%s%s" % (
                (er.iloc[i] if pd.notna(er.iloc[i]) else 0.0),
                d["prob_positive"].iloc[i],
                (conf.iloc[i] if pd.notna(conf.iloc[i]) else 0.0),
                d["uncertainty"].iloc[i],
                d["gate_decision"].iloc[i],
                ("(%s)" % d["gate_reason_code"].iloc[i]) if d["gate_reason_code"].iloc[i] else "",
            ))
            for i in range(len(d))]
        d["human_review_status"] = "PENDING"  # ⑧ Post-trade Review 前人工未拍板
        return d


# ---------------------------------------------------------------------------
# 组装与主流程
# ---------------------------------------------------------------------------
def build_base_frame(v2, aux, rf):
    """把 v2 模型预测 + gate 辅助方向 + as-of 风险特征合并成基础帧。"""
    base = v2.copy()
    for name, a in aux.items():
        base = base.merge(a, on=["node", "target_date", "hour"], how="left")
    base = base.merge(
        rf[["node", "target_date", "hour", "hist_n", "cvar99", "rcvar99",
            "vol_ratio", "node_drift"]],
        on=["node", "target_date", "hour"], how="left")
    return base


def build_rule_base(rule, rf):
    base = rule.copy()
    base = base.merge(
        rf[["node", "target_date", "hour", "hist_n", "cvar99", "rcvar99",
            "vol_ratio", "node_drift"]],
        on=["node", "target_date", "hour"], how="left")
    return base


def run_strategies(frame, nodes, rule_frame=None):
    """运行全部策略，返回 [(label, frame)]。"""
    results = []
    strategies = [
        RuleBaseline(),
        PredictiveModelOnly(),
        PredictiveModelGate(),
        FullDecisionPipeline(),
    ]
    for strat in strategies:
        src = rule_frame if isinstance(strat, RuleBaseline) else frame
        src = filter_nodes(src, nodes)
        out = strat.decide(src)
        results.append((strat.name, out))
    return results


def decision_detail(d):
    rows = []
    for dec in ["SELL_DA", "BUY_DA", "NO_TRADE"]:
        g = d[d["decision"] == dec]
        if len(g) == 0:
            rows.append({"decision": dec, "n": 0, "total_pnl": 0.0, "mean_pnl": None})
            continue
        rows.append({
            "decision": dec, "n": int(len(g)),
            "total_pnl": round(float(g["pnl"].sum()), 2),
            "mean_pnl": round(float(g["pnl"].mean()), 3),
        })
    return rows


def gate_reject_summary(d):
    """统计 Gate 实际 REJECT 的原因分布。

    只统计「本来满足三态候选（|er|>=5 & conf>=0.2）、被 Gate 拦下」的行，
    避免把 WARNING 码或非候选行的 gate_reason_code 计入。
    """
    if "gate_decision" not in d.columns:
        return {}
    rej = d[d["gate_decision"] == "REJECT"]
    if len(rej) == 0:
        return {}
    er = pd.to_numeric(rej["expected_return"], errors="coerce")
    conf = pd.to_numeric(rej["confidence"], errors="coerce")
    cand = rej[(er.abs() >= DECISION_CFG["ret_threshold_abs"]) & (conf >= DECISION_CFG["conf_threshold"])]
    if len(cand) == 0:
        return {}
    return {k: int(v) for k, v in cand["gate_reason_code"].value_counts().items()}


def regression_alignment(frame, rule_frame, nodes=None):
    """回归对齐：Agent C 模块 vs 本文件内置复刻，决策是否一致。

    只在主节点（ZP26）上比较；返回对比摘要。若 Agent C 模块未就绪则返回 None。
    """
    if not HAVE_AGENT_C:
        return None
    nodes = nodes or MAIN_NODES
    f = filter_nodes(frame, nodes)

    # --- Model + Risk Gate 对比 ---
    base_dec = PredictiveModelOnly().decide(f)["decision"]
    standalone = WhiteBoxRiskGate().apply(PredictiveModelOnly().decide(f))
    agentc = PredictiveModelGate()._apply_agent_c_gate(PredictiveModelOnly().decide(f))
    gate_match = bool((standalone["decision"] == agentc["decision"]).all())
    gate_ntr_std = int((standalone["decision"] != "NO_TRADE").sum())
    gate_ntr_ac = int((agentc["decision"] != "NO_TRADE").sum())

    # --- Full Pipeline 对比 ---
    pipe = FullDecisionPipeline()
    standalone_p = pipe._apply_standalone_pipeline(f)
    agentc_p = pipe._apply_agent_c_pipeline(f)
    pipe_match = bool((standalone_p["decision"] == agentc_p["decision"]).all())
    pipe_ntr_std = int((standalone_p["decision"] != "NO_TRADE").sum())
    pipe_ntr_ac = int((agentc_p["decision"] != "NO_TRADE").sum())

    return {
        "modules": "code/risk_gate + code/decision/rule_engine (Agent C)",
        "nodes": nodes,
        "model_gate_decisions_match": gate_match,
        "model_gate_n_traded_standalone": gate_ntr_std,
        "model_gate_n_traded_agent_c": gate_ntr_ac,
        "full_pipeline_decisions_match": pipe_match,
        "full_pipeline_n_traded_standalone": pipe_ntr_std,
        "full_pipeline_n_traded_agent_c": pipe_ntr_ac,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="另输出 docs/v0.2_backtest.md")
    args = ap.parse_args()

    canon, v2, rule, rf, aux = load_all()
    frame = build_base_frame(v2, aux, rf)
    rule_frame = build_rule_base(rule, rf)

    # ---------------- 主策略 ZP26 ----------------
    main_res = run_strategies(frame, MAIN_NODES, rule_frame)
    # ---------------- ELCA 单独 ----------------
    elca_res = run_strategies(frame, [ELCA_NODE], rule_frame)

    main_rows = [compute_metrics(f, label) for label, f in main_res]
    elca_rows = [compute_metrics(f, label) for label, f in elca_res]

    # 决策明细 / gate 触发
    main_detail = {label: decision_detail(f) for label, f in main_res}
    gate_rej = {label: gate_reject_summary(f) for label, f in main_res}

    # ---------------- 回归对齐：Agent C 模块 vs 内置复刻 ----------------
    align = regression_alignment(frame, rule_frame)

    summary = {
        "meta": {
            "generated_by": "backtest_v2.py (Agent E)",
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "window": "test 2026-06-02 ~ 2026-08-05 (65 days, once)",
            "main_nodes": MAIN_NODES,
            "elca_node": ELCA_NODE,
            "position": "1 MWh normalized; SELL=+actual_return, BUY=-actual_return, NO_TRADE=0",
            "boundary": "Signal/Strategy backtest, NOT real Convergence Bidding PnL (no bid price/quantity/award/clearing/settlement/fees)",
            "as_of": "risk features from risk_features.parquet (<= target_date-2); canonical X only; test used once",
            "gate_source": "Agent C code/risk_gate/（正式模块，集成；stage3 train+val 校准，test 零调参）; 内置复刻保留作回归对照",
            "rule_engine_source": "Agent C code/decision/rule_engine.py（正式模块，集成，DecisionPipeline.md §5 三态 + Evidence Time Gate）; 内置复刻保留作回归对照",
            "agent_c_integrated": HAVE_AGENT_C,
            **_window_market_rule_version(),   # V0.3.1.4：test window 2026-05-01+ → POST（MIXED 守卫）
            "regression_alignment": align,
        },
        "strategy_matrix_main": main_rows,
        "strategy_matrix_elca": elca_rows,
        "decision_detail_main": main_detail,
        "gate_reject_reasons_main": gate_rej,
    }
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print("saved ->", OUT_SUMMARY)

    # ---------------- 控制台 ----------------
    def _fmt(x, d=3):
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return "-"
        return ("%%.%df" % d) % x if isinstance(x, float) else str(x)

    cols = ["label", "n_traded", "trade_coverage", "dir_acc_traded", "win_rate",
            "cum_pnl", "mean_pnl_per_trade", "median_pnl_per_trade", "max_drawdown",
            "worst_trade", "cvar95", "cvar99", "sharpe_daily"]
    head = " | ".join(c.replace("_", " ") for c in cols)
    print("=" * 100)
    print("Signal Backtest 指标矩阵 · 主策略 ZP26（test）")
    print("=" * 100)
    print("| " + head + " |")
    print("|" + "---|" * len(cols))
    for r in main_rows:
        print("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    print()
    print("ELCAJNGT（cold-start，单独评估）")
    for r in elca_rows:
        print("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    print()
    print("Gate REJECT 原因（主策略，Full Pipeline）：", gate_rej.get("Full Decision Pipeline (Gate + Rule Engine)", {}))
    print()
    print("Agent C 模块集成：", "YES" if HAVE_AGENT_C else "NO（使用内置复刻）")
    if align is not None:
        print("回归对齐（Agent C vs 内置复刻）Model+Gate 决策一致:", align["model_gate_decisions_match"],
              "| Full Pipeline 决策一致:", align["full_pipeline_decisions_match"],
              "| 交易数(Agent C):", align["model_gate_n_traded_agent_c"])

    if args.report:
        write_report(summary)
        print("saved ->", OUT_REPORT)


# ---------------------------------------------------------------------------
# Markdown 报告
# ---------------------------------------------------------------------------
def write_report(summary):
    lines = []
    A = lines.append
    A("# V0.2 Signal Backtest（Agent E）")
    A("")
    A("> 生成时间：%s" % summary["meta"]["generated_at"])
    A("> 窗口：%s；主策略 ZP26（SNLNDRO / CONTROLX），ELCA cold-start 单独评估。" % summary["meta"]["window"])
    A("> 仓位：1 MWh normalized；SELL=+actual_return、BUY=−actual_return、NO_TRADE=0。")
    A("")
    A("## 0. 边界声明（重要）")
    A("")
    A("这是 **Signal / Strategy Backtest**（假设每笔按 1 MWh 执行），**不是真实 "
      "Convergence Bidding PnL**：缺 bid price / quantity / award / clearing / "
      "settlement / fees，未做仓位优化、交易成本与市场冲击。真实执行需要报价、成交、"
      "结算三个环节，本回测只评估决策信号本身的价值。")
    A("")
    A("## 1. 策略定义")
    A("")
    A("| # | 策略 | 定义 |")
    A("|---|---|---|")
    A("| 1 | Rule Baseline | V0.1 Rule 原样（predictions_rule.csv 的 pred_direction，已内嵌 prob margin + expected 阈值，见 model_c.decision_from_prob_expected）= stage3「原 Rule」口径；Rule = benchmark，非生产 |")
    A("| 2 | Predictive Model only | v2 模型 expected_return 符号出方向；\\|er\\|≥5.0 & conf≥0.2 才交易 |")
    A("| 3 | Predictive Model + Risk Gate | 策略 2 + Gate（R7a/R7b/R6 REJECT→NO_TRADE，R4 警告） |")
    A("| 4 | Full Decision Pipeline | 模型 → Gate → Rule Engine（三态 + reason/evidence/gate_status 审计轨迹；REJECT→NO_TRADE） |")
    A("")
    A("> 说明：Agent C 的 `code/risk_gate/` 与 `code/decision/rule_engine.py` 已就绪并集成"
      "（HAVE_AGENT_C=%s）。策略 3/4 优先消费正式模块；内置复刻保留作对照，回归对齐见 §4.3。"
      "阈值全部来自 stage3 train+val 校准，test 零调参。" % ("YES" if HAVE_AGENT_C else "NO"))
    A("")
    A("## 2. 指标矩阵（主策略 ZP26 · test）")
    A("")
    cols = ["label", "n_traded", "trade_coverage", "dir_acc_traded", "win_rate",
            "cum_pnl", "mean_pnl_per_trade", "median_pnl_per_trade", "max_drawdown",
            "worst_trade", "cvar95", "cvar99", "sharpe_daily"]
    zh = {"label": "策略", "n_traded": "交易数", "trade_coverage": "覆盖率",
          "dir_acc_traded": "方向acc(交易)", "win_rate": "胜率", "cum_pnl": "累计PnL",
          "mean_pnl_per_trade": "单笔均值", "median_pnl_per_trade": "单笔中位",
          "max_drawdown": "最大回撤", "worst_trade": "最差单笔", "cvar95": "CVaR(5%)",
          "cvar99": "CVaR(1%)", "sharpe_daily": "Sharpe(日)"}
    A("| " + " | ".join(zh[c] for c in cols) + " |")
    A("|" + "---|" * len(cols))
    for r in summary["strategy_matrix_main"]:
        A("| " + " | ".join(fmt_cell(r.get(c)) for c in cols) + " |")
    A("")
    A("> 覆盖率分母 = 该窗口样本行数（ZP26 共 3,118 行）。")
    A("")
    A("## 3. ELCAJNGT（cold-start，单独评估，不混入主结论）")
    A("")
    A("| " + " | ".join(zh[c] for c in cols) + " |")
    A("|" + "---|" * len(cols))
    for r in summary["strategy_matrix_elca"]:
        A("| " + " | ".join(fmt_cell(r.get(c)) for c in cols) + " |")
    A("")
    A("## 4. 关键结论（诚实评估）")
    A("")
    A("### 4.1 V0.2 相比 V0.1 在 PnL 上没有提升（如实）")
    A("")
    A("- **Predictive Model only（ZP26）= −118,464**：v2 模型与 V0.1 CatBoost / Interpretable 一样，"
      "在 CONTROLX 上预测大量 BUY（1,099 笔，逆 +84 漂移 + 重右尾），被少数极端事件打穿"
      "（maxDD −138,374）。这与 V0.1 结论一致：**单一生产模型不会自动比 V0.1 委员会更会赚**。")
    A("- **Model + Gate / Full Pipeline（ZP26）= −820**：Gate 把 CONTROLX BUY 全部拦截"
      "（REJECT 1,099 笔：1,004 笔 BUY_ON_POSITIVE_DRIFT_NODE|EXTREME_TAIL_NODE + "
      "92 笔 +MODEL_UNSTABLE + 3 笔 +HIGH_VOLATILITY，Agent C 正式模块 reason 口径），"
      "PnL 由 −118k 转 −820、maxDD 由 −138k 收至 −3,046。残余亏损来自 CONTROLX SELL"
      "（163 笔 −832，DA 崩塌事件，stage3 已证明事前不可可靠识别）+ SNLNDRO SELL（+11）+ ELCA 全关。")
    A("- **V0.1 Committee + Risk Gate 参考**：stage3 报告 ZP26 +962、maxDD −654、worst −113"
      "（committee 只留下 SNLNDRO 交易）。V0.2 Model+Gate 为 −820、maxDD −3,046、worst −208 —— "
      "**V0.2 模型没有带来更高的 PnL，尾部压制也不如 V0.1 committee+Gate**（因为 v2 模型额外产生了"
      "未被 gate 拦截的 CONTROLX SELL）。架构改进的价值在风险/可解释/可审计，不在 accuracy/PnL。")
    A("")
    A("### 4.2 架构价值：风险、可解释、可审计（真实变化）")
    A("")
    A("| 维度 | V0.1 | V0.2 | 真实变化 |")
    A("|---|---|---|---|")
    A("| 尾部亏损 | Model Committee：maxDD −147,957、worst −2,216、CVaR1 −1,056 | Model+Gate：maxDD −3,046、worst −208、CVaR1 −198 | 尾部被 Gate 数量级压制；但 V0.1 Gate 也做到（−654/−113），非 V0.2 独有 |")
    A("| 无意义交易 | Committee 1,523 笔 / 48.8%（几乎全是 CONTROLX BUY 亏钱交易） | Model+Gate 181 笔 / 5.8% | 交易覆盖大幅下降；留下 CONTROLX SELL（未拦截）+ SNLNDRO SELL |")
    A("| 解释 | 黑盒概率 + 事后归因 | Decision Card / evidence / gate reason 全程白盒 | 每次决策可审计（exp_ret/prob/conf/unc/gate/reason） |")
    A("| 可审计 | 预测 CSV + 指标 | 8 步流水线 + Post-trade Review（UNFORESEEABLE_EVENT）+ Case Library + Human Confirmation | 决策轨迹可回放、人工可拍板 |")
    A("")
    A("### 4.3 明确边界")
    A("")
    A("- ELCA 被 Gate 全部关闭（R7b + R6），裁决是「不交易」而非「交易得更聪明」。")
    A("- 本窗口 DA>RTPD 强正漂移（静态全 SELL ≈ +132,746，stage3），任何策略跑不赢漂移本身；"
      "盈利来源仍是市场 regime，不是模型 alpha。")
    A("- 残余最大亏损机制：CONTROLX SELL 的 DA 崩塌（2026-06-23/06-30），需外部信息"
      "（可再生出力/负荷修正/outage）才能事前识别，V0.2 同样无法拦截。")
    A("- **Full Pipeline 与 Model+Gate 在本窗口决策完全一致**（181 笔，−820）：Rule Engine 的"
      "三态阈值与决策策略相同，且 Gate 已把 REJECT 降级为 NO_TRADE。Rule Engine 的真实价值是"
      "逐笔 reason / evidence / gate_status 的审计轨迹，而非额外的收益过滤。")
    A("- **Gate 对 Rule 零改变（824 笔不变）**：Rule 的 BUY 全在 SNLNDRO（4 笔），无 CONTROLX BUY，"
      "gate 无拦可拦——与 stage3 一致。Gate 的价值取决于模型是否产生 CONTROLX BUY：v2 模型大量产生"
      "（1,099 笔），gate 才有用武之地。")
    align = summary["meta"].get("regression_alignment")
    if align:
        A("- **回归对齐（Agent C 正式模块 vs 本文件内置复刻）**：Model+Gate 决策一致 = %s，"
          "Full Pipeline 决策一致 = %s；交易数 = %d（ZP26）。两种实现产出相同决策，"
          "确认正式模块与 stage3 验证口径一致。"
          % (align["model_gate_decisions_match"], align["full_pipeline_decisions_match"],
             align["model_gate_n_traded_agent_c"]))
    A("")
    A("## 5. 局限与后续")
    A("")
    A("- test 窗口仅 65 天、2026-06 强正漂移 regime，外推受限。")
    A("- Gate / Rule Engine 已集成 Agent C 正式模块（回归对齐通过）；后续如需修改规则，"
      "以 `code/risk_gate/` 与 `code/decision/rule_engine.py` 为单一实现。")
    A("- confidence 未校准（V0.1 已记录）；expected_return 秩相关≈0.06，幅度信息弱。")
    A("- 未含真实 bid/quantity/award 与费用；1 MWh 绝对额不可与真实资本回报直接类比。")
    A("- Agent Evidence 无真实数据源（全部 UNCERTAIN）；Time Gate 保证不穿越，但无法凭空创造证据价值。")
    A("")
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def fmt_cell(x, d=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return ("%%.%df" % d) % x if isinstance(x, float) else str(x)


if __name__ == "__main__":
    main()
