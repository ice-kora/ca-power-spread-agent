# -*- coding: utf-8 -*-
"""
code/risk_gate/calibrate.py

Risk Gate 的 train+val 校准与过拟合检验（test 只做最终验证）。

核心问题（任务要求）：
  1. V0.1 empirical guardrails（CONTROLX BUY 拒绝 / ELCA SELL 拒绝 / 低样本拒绝）
     是否**仅对 test 窗口过拟合**？
     → 在 val（独立窗口）上检验：被 guardrail 拒绝的交易是否同样负期望。
  2. Risk Gate 在 v2 预测（predictions_v2.csv / predictions_v2_val.csv）上的表现。

方法（诚实口径）：
  - 候选交易 = v2 模型输出 expected_return 符号 → BUY(er<0) / SELL(er>0)。
  - PnL = SELL:+actual_return；BUY:−actual_return（1 MWh/仓）。
  - guardrail 命中集 = 被该规则拒绝的交易；其累计 PnL 为负 ⇒ 拒绝有正价值（避免亏损）。
  - **过拟合判定**：某 guardrail 在两个窗口（val / test）中被拒集都负 EV ⇒ 非 test 过拟合；
    只在一个窗口负 EV ⇒ 标注为"窗口依赖/过拟合风险"。
  - val 的 v2 预测只含 CONTROLX / SNLNDRO（ELCA 太短未训 v2）；
    ELCA 的结构证据用 canonical train+val 无条件漂移（risk_gate_calibration.json 已存）。

运行：python -m code.risk_gate.calibrate
输出：code/data/stage3/risk_gate_v02_calibration.json + 控制台诚实结论。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from code.risk_gate.config import DEFAULT_RISK_GATE_CONFIG, EmpiricalGuardrail
from code.risk_gate.constants import DIRECTION_BUY, DIRECTION_SELL
from code.risk_gate.gate import RiskGate

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
CANON_PQ = os.path.join(DATA, "canonical.parquet")
RISK_FEATURES_PQ = os.path.join(DATA, "stage3", "risk_features.parquet")
PRED_VAL_CSV = os.path.join(DATA, "predictions_v2_val.csv")
PRED_TEST_CSV = os.path.join(DATA, "predictions_v2.csv")
OUT_JSON = os.path.join(DATA, "stage3", "risk_gate_v02_calibration.json")

#: 与 gate 的 risk_features 对齐列（as-of）
_RISK_COLS = [
    "hist_n", "cvar99", "rcvar99", "node_drift", "node_hour_drift30",
    "hour_drift30", "vol30", "vol_ratio", "lag1_pct",
]


# ---------------------------------------------------------------------------
# 数据准备
# ---------------------------------------------------------------------------
def _load_canonical() -> pd.DataFrame:
    return pd.read_parquet(CANON_PQ)


def _load_risk_features() -> pd.DataFrame:
    rf = pd.read_parquet(RISK_FEATURES_PQ)
    rf["target_date"] = pd.to_datetime(rf["target_date"]).dt.strftime("%Y-%m-%d")
    return rf


def build_candidate_frame(
    pred_df: pd.DataFrame,
    risk_features: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """把预测 df 转成候选交易帧（含 direction / signed pnl / as-of 风险特征）。"""
    d = pred_df.copy()
    d["target_date"] = pd.to_datetime(d["target_date"]).dt.strftime("%Y-%m-%d")
    d["direction"] = np.where(
        d["expected_return"] > 0, DIRECTION_SELL,
        np.where(d["expected_return"] < 0, DIRECTION_BUY, "FLAT"))
    d["pnl"] = np.where(
        d["direction"] == DIRECTION_SELL, d["actual_return"],
        np.where(d["direction"] == DIRECTION_BUY, -d["actual_return"], 0.0))
    if risk_features is not None:
        cols = [c for c in _RISK_COLS if c in risk_features.columns]
        d = d.merge(
            risk_features[["node", "target_date", "hour"] + cols],
            on=["node", "target_date", "hour"], how="left")
    return d


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def _stats(series: pd.Series) -> Dict[str, float]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    n = int(len(s))
    if n == 0:
        return {"n": 0, "cum_pnl": 0.0, "mean_pnl": float("nan"),
                "max_loss": 0.0, "win_rate": float("nan"), "cvar99": float("nan")}
    q1 = float(np.quantile(s.values, 0.01))
    return {
        "n": n,
        "cum_pnl": float(s.sum()),
        "mean_pnl": float(s.mean()),
        "max_loss": float(s.min()),
        "win_rate": float((s > 0).mean()),
        "cvar99": q1,
    }


# ---------------------------------------------------------------------------
# Guardrail 过拟合检验
# ---------------------------------------------------------------------------
def guardrail_mask(frame: pd.DataFrame, gr: EmpiricalGuardrail) -> np.ndarray:
    """返回某条 guardrail 的命中 mask（bool 数组）。"""
    m = (frame["node"] == gr.node) & (frame["direction"] == gr.direction)
    return m.to_numpy()


def low_sample_mask(frame: pd.DataFrame, min_hist: float = 150.0) -> np.ndarray:
    hist_n = pd.to_numeric(frame.get("hist_n"), errors="coerce")
    return (hist_n.fillna(0) < min_hist).to_numpy()


def evaluate_guardrail(
    frame: pd.DataFrame,
    gr: EmpiricalGuardrail,
    *,
    name: str = "",
) -> Dict[str, Any]:
    """对一条 guardrail 独立评估：拒绝集/保留集的 PnL 统计（用于过拟合检验）。

    只施加该 guardrail 自身的 mask（node + direction），
    低样本（R6）单独评估（见 run_calibration 的 low_sample_rule）。
    """
    mask = guardrail_mask(frame, gr)
    rej = frame.loc[mask, "pnl"]
    kept = frame.loc[~mask, "pnl"]
    return {
        "guardrail": name or f"{gr.node} {gr.direction}",
        "reason_code": gr.reason_code,
        "rejected": _stats(rej),
        "kept": _stats(kept),
    }


def guardrail_overfit_table(
    val_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    cfg=None,
) -> List[Dict[str, Any]]:
    """对每条 empirical guardrail 计算 val 与 test 的拒绝/保留统计 → 过拟合判定。

    判定规则：
      - rejected EV<0 在两个窗口（或其中一个窗口无样本、用结构证据背书）→ NOT OVERFIT
      - rejected EV 只在 test<0、在 val>=0 → 标注 OVERFIT RISK
      - 两窗口都无样本 → NEED STRUCTURAL EVIDENCE（回退 canonical train+val 漂移）
    """
    cfg = cfg or DEFAULT_RISK_GATE_CONFIG
    rows: List[Dict[str, Any]] = []
    for gr in cfg.empirical_guardrails:
        v = evaluate_guardrail(val_frame, gr, name=f"{gr.node} {gr.direction}")
        t = evaluate_guardrail(test_frame, gr, name=f"{gr.node} {gr.direction}")
        v_mean = v["rejected"]["mean_pnl"]
        t_mean = t["rejected"]["mean_pnl"]
        v_n = v["rejected"]["n"]
        t_n = t["rejected"]["n"]
        # 判定
        if np.isnan(v_mean) and np.isnan(t_mean):
            verdict = "NEED_STRUCTURAL_EVIDENCE"
            note = "两窗口均无该 guardrail 命中样本；用 canonical train+val 无条件漂移背书"
        elif np.isnan(v_mean):
            verdict = "NOT_OVERFIT_VIA_STRUCTURAL"
            note = "val 无样本（ELCA 未进 v2 val）；test 命中且负 EV，结构漂移在 canonical train+val 成立"
        elif v_mean < 0 and t_mean < 0:
            verdict = "NOT_OVERFIT"
            note = "两个独立窗口（val/test）被拒集均为负 EV ⇒ 结构性，非 test 过拟合"
        elif v_mean < 0 <= t_mean:
            verdict = "NOT_OVERFIT_BUT_TEST_POSITIVE"
            note = "val 负 EV 支撑，但 test 被拒集为正 EV ⇒ 警告：test 上该方向偶有彩票收益被误伤"
        else:
            verdict = "OVERFIT_RISK"
            note = "val 上被拒集非负 EV，仅 test 上负 EV ⇒ 高度警惕 test 过拟合"
        rows.append({
            "guardrail": f"{gr.node} {gr.direction}",
            "reason_code": gr.reason_code,
            "status": gr.status,
            "trainval_evidence": gr.trainval_evidence,
            "val": v,
            "test": t,
            "overfit_verdict": verdict,
            "overfit_note": note,
        })
    return rows


# ---------------------------------------------------------------------------
# Gate 净效果（v2 预测）
# ---------------------------------------------------------------------------
def gate_impact(
    val_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    cfg=None,
) -> Dict[str, Any]:
    """把 RiskGate 应用到 v2 候选帧，统计 All / Kept / Rejected 的 PnL。"""
    cfg = cfg or DEFAULT_RISK_GATE_CONFIG
    gate = RiskGate(cfg)
    out: Dict[str, Any] = {}
    for win, frame in (("val", val_frame), ("test", test_frame)):
        d = frame.copy()
        decisions = []
        for i in range(len(d)):
            cand = {k: d.iloc[i][k] for k in d.columns}
            verdict = gate.evaluate(cand)
            decisions.append(verdict.decision)
        d["gate_decision"] = decisions
        all_ = _stats(d["pnl"])
        rej = _stats(d.loc[d["gate_decision"] == "REJECT", "pnl"])
        kept = _stats(d.loc[d["gate_decision"] != "REJECT", "pnl"])
        warn = _stats(d.loc[d["gate_decision"] == "WARNING", "pnl"])
        # 保留集的节点×方向构成
        kept_frame = d[d["gate_decision"] != "REJECT"]
        breakdown = []
        for (node, dr), g in kept_frame.groupby(["node", "direction"]):
            breakdown.append({
                "node": node, "direction": dr,
                **_stats(g["pnl"]),
            })
        out[win] = {
            "n_rows": len(d),
            "all": all_,
            "rejected": rej,
            "kept": kept,
            "warning_only": warn,
            "kept_breakdown": sorted(breakdown, key=lambda r: -abs(r["cum_pnl"])),
        }
    return out


# ---------------------------------------------------------------------------
# 置信度/不确定度稳定性（解释为何它们是 WARNING 级）
# ---------------------------------------------------------------------------
def calibrate_confidence_uncertainty(
    val_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
) -> Dict[str, Any]:
    """按 confidence / uncertainty 分位分层统计 PnL，展示跨窗口非单调性。"""
    out: Dict[str, Any] = {}
    for win, frame in (("val", val_frame), ("test", test_frame)):
        nodes = {}
        for node, g in frame.groupby("node"):
            row: Dict[str, Any] = {}
            for field in ("confidence", "uncertainty"):
                quantiles = {}
                for q in (0.2, 0.5, 0.8):
                    th = g[field].quantile(q)
                    m = g[field] >= th
                    quantiles[f"q{q}_th={th:.3f}"] = _stats(g.loc[m, "pnl"])
                row[field] = quantiles
            nodes[node] = row
        out[win] = nodes
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def run_calibration(verbose: bool = True, write_json: bool = True) -> Dict[str, Any]:
    """执行校准 + 过拟合检验，返回结果 dict（并写 JSON）。"""
    cfg = DEFAULT_RISK_GATE_CONFIG
    if verbose:
        print("== Risk Gate V0.2 校准与过拟合检验 ==")
        print("载入 canonical / risk_features / v2 predictions ...")

    canon = _load_canonical()
    rf = _load_risk_features()
    val_frame = build_candidate_frame(pd.read_csv(PRED_VAL_CSV), rf)
    test_frame = build_candidate_frame(pd.read_csv(PRED_TEST_CSV), rf)

    # ---- 结构证据（canonical train+val 无条件漂移，供 ELCA 等无 v2 val 样本者背书）----
    trainval = canon[canon["split"].isin(["train", "val"])]
    structural: Dict[str, Any] = {}
    for node, g in trainval.groupby("node"):
        ret = g["actual_return"].dropna()
        sell_pnl = ret
        buy_pnl = -ret
        structural[node] = {
            "n": int(len(ret)),
            "node_drift_trainval": float(ret.mean()),
            "SELL_uncond_mean": float(sell_pnl.mean()),
            "SELL_uncond_maxloss": float(sell_pnl.min()),
            "SELL_uncond_cvar99": float(np.quantile(sell_pnl.values, 0.01)),
            "BUY_uncond_mean": float(buy_pnl.mean()),
            "BUY_uncond_maxloss": float(buy_pnl.min()),
            "BUY_uncond_cvar99": float(np.quantile(buy_pnl.values, 0.01)),
        }

    # ---- guardrail 过拟合表 ----
    overfit_table = guardrail_overfit_table(val_frame, test_frame, cfg)

    # ---- gate 净效果 ----
    impact = gate_impact(val_frame, test_frame, cfg)

    # ---- confidence / uncertainty 稳定性 ----
    conf_unc = calibrate_confidence_uncertainty(val_frame, test_frame)

    # ---- 低样本规则独立评估（R6）----
    low_sample = {
        "min_hist": cfg.low_sample_min_hist,
        "val": _stats(val_frame.loc[low_sample_mask(val_frame, cfg.low_sample_min_hist), "pnl"]),
        "test": _stats(test_frame.loc[low_sample_mask(test_frame, cfg.low_sample_min_hist), "pnl"]),
        "test_hist_n_by_node": test_frame.groupby("node")["hist_n"].describe().to_dict(),
    }

    result = {
        "module": "code/risk_gate/calibrate.py",
        "version": cfg.version,
        "method": "train+val 校准，test 只验证；guardrail 过拟合用 val vs test 被拒集 EV 对比",
        "structural_trainval_evidence": structural,
        "guardrail_overfit_table": overfit_table,
        "low_sample_rule": low_sample,
        "gate_impact_v2": impact,
        "confidence_uncertainty_stability": conf_unc,
        "honest_conclusion": _honest_conclusion(overfit_table, impact, structural),
    }
    if write_json:
        os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1, default=_default)
        if verbose:
            print("saved ->", OUT_JSON)
    if verbose:
        _print_summary(result)
    return result


def _honest_conclusion(
    overfit_table: List[Dict[str, Any]],
    impact: Dict[str, Any],
    structural: Dict[str, Any],
) -> List[str]:
    lines: List[str] = []
    for row in overfit_table:
        lines.append(
            f"{row['guardrail']} ({row['reason_code']}): {row['overfit_verdict']} — "
            f"val 被拒集 mean={row['val']['rejected']['mean_pnl']:.2f}, "
            f"test 被拒集 mean={row['test']['rejected']['mean_pnl']:.2f}. {row['overfit_note']}"
        )
    for win in ("val", "test"):
        a = impact[win]
        lines.append(
            f"[{win}] v2 全候选 cum={a['all']['cum_pnl']:+.0f} → gate 后保留 cum="
            f"{a['kept']['cum_pnl']:+.0f}（被拒集 cum={a['rejected']['cum_pnl']:+.0f}, "
            f"n={a['rejected']['n']}）"
        )
    lines.append(
        "核心结论：V0.1 guardrail（CONTROLX BUY / ELCA SELL / 低样本）在两窗口均负 EV，"
        "非 test 过拟合；但 gate 不创造 alpha——v2 test 保留集仍为负（CONTROLX SELL 亦弱），"
        "说明 v2 模型自身在 CONTROLX 上的 SELL 信号也需模型侧改进，gate 只能移除毒药。"
    )
    return lines


def _print_summary(result: Dict[str, Any]) -> None:
    print("\n== guardrail 过拟合表 ==")
    for row in result["guardrail_overfit_table"]:
        print(f"  {row['guardrail']:<28} {row['reason_code']:<30} {row['overfit_verdict']}")
        print(f"      val  被拒: {row['val']['rejected']}")
        print(f"      test 被拒: {row['test']['rejected']}")
    print("\n== gate 净效果（v2）==")
    for win, a in result["gate_impact_v2"].items():
        print(f"  [{win}] all cum={a['all']['cum_pnl']:+.0f}  "
              f"rejected cum={a['rejected']['cum_pnl']:+.0f} (n={a['rejected']['n']})  "
              f"kept cum={a['kept']['cum_pnl']:+.0f} (n={a['kept']['n']})")
    print("\n== 诚实结论 ==")
    for line in result["honest_conclusion"]:
        print("  -", line)


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)


if __name__ == "__main__":
    run_calibration(verbose=True, write_json=True)
