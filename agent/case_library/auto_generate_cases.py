# -*- coding: utf-8 -*-
"""
agent/case_library/auto_generate_cases.py

Case 自动生成脚本：从 test（可选 val）预测 + 回测口径自动打 Case。
不再靠人工事后从 test 里"挑" Case——凡是 CaseGenerationPolicy 命中的一律生成。

数据源（全部真实、程序化）：
  - code/data/predictions_v2*.csv         v2 模型预测（expected_return / prob / confidence）
  - code/data/stage3/risk_features.parquet  as-of 风险特征（hist_n / cvar99 / rcvar99 / vol_ratio）
  - code/data/canonical.parquet           as-of 特征快照 + actual_da / actual_rtpd
  - code/risk_gate/gate.py                真实 Risk Gate 裁决（PASS/WARNING/REJECT）

派生口径（对齐 Rule Engine / business_contract §5）：
  - suggested_action:  SELL_DA(er>=+5, conf>=0.20) / BUY_DA(er<=-5, conf>=0.20) / NO_TRADE
  - PnL:               SELL_DA=+actual_return；BUY_DA=−actual_return；NO_TRADE=0（1 MWh/仓）
  - direction_wrong:   有方向交易且 sign(actual_return) != 建议方向

输出：agent/case_library/cases_auto.json
  - 全部为自动生成，含 case_created_at / case_available_at / review_completed_at。
  - 检索侧必须用 policy.is_retrievable(case, decision_time) 施加 as-of 硬约束
    （case_available_at <= decision_time），严格防 Case 穿越。

运行：python agent/case_library/auto_generate_cases.py [--splits test] [--include-val]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# 保证从任意 cwd 运行都能 import agent.*
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from agent.case_library.case import Case  # noqa: E402
from agent.case_library.policy import (  # noqa: E402
    CASE_TYPES,
    CaseGenerationPolicy,
    DecisionRecord,
    build_case,
    case_type_counts,
    generate_cases,
)
from code.risk_gate.gate import RiskGate  # noqa: E402

DATA_DIR = REPO_ROOT / "code" / "data"
STAGE3_DIR = DATA_DIR / "stage3"
OUT_PATH = REPO_ROOT / "agent" / "case_library" / "cases_auto.json"

#: 写入 available_information_at_decision_time 的 as-of 快照特征（全部决策时点可见）
SNAPSHOT_KEYS: List[str] = [
    # 滞后价差 / 滚动统计
    "spread_lag1", "spread_lag2", "spread_lag7",
    "spread_mean7", "spread_std7", "spread_mean14", "spread_std14",
    "spread_mean30", "spread_std30",
    # 负荷 / 天气滞后
    "load_2da_forecast", "load_peak_flag", "t2m_lag1", "ssrd_lag1", "wind100_lag1",
    "peer_spread_lag1",
    # 日历
    "dow", "month", "is_holiday", "solar_flag",
]

#: 来自 risk_features.parquet 的风险特征（as-of）
RISK_KEYS: List[str] = ["hist_n", "cvar95", "cvar99", "rcvar95", "rcvar99",
                        "vol7", "vol30", "vol_ratio", "node_drift", "lag1_pct"]

DECISION_CFG = dict(min_spread=5.0, min_confidence=0.20)  # 对齐 Rule Engine / backtest DECISION_CFG


# ---------------------------------------------------------------------------
# 记录构建
# ---------------------------------------------------------------------------
def _suggested_action(er: float, conf: float) -> str:
    if er >= DECISION_CFG["min_spread"] and conf >= DECISION_CFG["min_confidence"]:
        return "SELL_DA"
    if er <= -DECISION_CFG["min_spread"] and conf >= DECISION_CFG["min_confidence"]:
        return "BUY_DA"
    return "NO_TRADE"


def _f(v: Any) -> float:
    try:
        f = float(v)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _json_clean(v: Any) -> Any:
    """把 numpy/pandas 标量转成原生 Python 类型，保证 json.dump 可序列化。"""
    if isinstance(v, (pd.Timestamp,)):
        return str(v)
    if hasattr(v, "item"):  # np.int32/np.int64/np.float64/np.bool_ 等
        try:
            return v.item()
        except (TypeError, ValueError):
            return v
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


def build_records(splits: List[str]) -> List[DecisionRecord]:
    """从预测 CSV + 风险特征 + canonical 构建 DecisionRecord 列表。"""
    frames: List[pd.DataFrame] = []
    for split in splits:
        fname = "predictions_v2_val.csv" if split == "val" else "predictions_v2.csv"
        df = pd.read_csv(DATA_DIR / fname, encoding="utf-8-sig")
        df["target_date"] = df["target_date"].astype(str).str[:10]
        frames.append(df)
    pred = pd.concat(frames, ignore_index=True)

    risk = pd.read_parquet(STAGE3_DIR / "risk_features.parquet")
    risk["target_date"] = risk["target_date"].dt.strftime("%Y-%m-%d")
    risk = risk[["node", "target_date", "hour"] + RISK_KEYS]

    canon_cols = ["node", "target_date", "hour", "actual_da", "actual_rtpd"] + SNAPSHOT_KEYS
    canon = pd.read_parquet(DATA_DIR / "canonical.parquet", columns=canon_cols)
    canon["target_date"] = canon["target_date"].dt.strftime("%Y-%m-%d")

    m = pred.merge(risk, on=["node", "target_date", "hour"], how="left")
    m = m.merge(canon, on=["node", "target_date", "hour"], how="left")

    gate = RiskGate()
    records: List[DecisionRecord] = []
    for i in range(len(m)):
        r = m.iloc[i]
        er = _f(r.get("expected_return"))
        conf = _f(r.get("confidence"))
        action = _suggested_action(er, conf)
        actual_return = _f(r.get("actual_return"))
        actual_dir = _f(r.get("actual_direction"))

        # Risk Gate 裁决（真实 gate 逻辑）
        cand = {
            "node": str(r["node"]),
            "target_date": str(r["target_date"]),
            "hour": int(r["hour"]),
            "expected_return": er,
            "confidence": conf,
            "uncertainty": _f(r.get("uncertainty")),
            "hist_n": r.get("hist_n") if pd.notna(r.get("hist_n")) else None,
            "cvar99": r.get("cvar99") if pd.notna(r.get("cvar99")) else None,
            "rcvar99": r.get("rcvar99") if pd.notna(r.get("rcvar99")) else None,
            "vol_ratio": r.get("vol_ratio") if pd.notna(r.get("vol_ratio")) else None,
        }
        verdict = gate.evaluate(cand)

        # as-of 快照
        snap: Dict[str, Any] = {
            k: _json_clean(r.get(k))
            for k in SNAPSHOT_KEYS + RISK_KEYS
            if k in r.index
        }
        snap["suggested_action"] = action
        snap["gate_decision"] = verdict.decision
        snap["gate_reason_code"] = list(verdict.risk_reasons)
        snap["actual_da"] = _f(r.get("actual_da")) if pd.notna(r.get("actual_da")) else None
        snap["actual_rtpd"] = _f(r.get("actual_rtpd")) if pd.notna(r.get("actual_rtpd")) else None

        records.append(DecisionRecord(
            node=str(r["node"]),
            target_date=str(r["target_date"]),
            hour=int(r["hour"]),
            split=str(r.get("split", "")),
            suggested_action=action,
            expected_return=er,
            confidence=conf,
            prob_positive=_f(r.get("prob_positive")),
            prob_negative=_f(r.get("prob_negative")),
            uncertainty=_f(r.get("uncertainty")),
            actual_return=actual_return,
            actual_direction=int(actual_dir) if actual_dir == actual_dir else 0,
            risk_gate_decision=verdict.decision,
            risk_gate_reasons=list(verdict.risk_reasons),
            extras=snap,
        ))
    return records


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Case 自动生成（从 test/val 预测 + 回测口径）")
    parser.add_argument("--splits", default="test",
                        help="逗号分隔的 split（默认 test；可用 test,val）")
    parser.add_argument("--out", default=str(OUT_PATH), help="输出 JSON 路径")
    args = parser.parse_args()

    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    policy = CaseGenerationPolicy()

    records = build_records(splits)
    print(f"records loaded: {len(records)} (splits={splits})")

    cases = generate_cases(records, policy=policy, case_id_prefix="CASE-AUTO-")
    counts = case_type_counts(cases)

    payload = {
        "meta": {
            "module": "agent/case_library",
            "generator": "agent/case_library/auto_generate_cases.py",
            "policy_version": "0.2",
            "description": (
                "Case Library（自动生成版）：不靠人工事后选样。全部由 "
                "CaseGenerationPolicy 对预测+回测口径机械判定命中生成。"
                "每条 Case 带 case_created_at / case_available_at / review_completed_at；"
                "检索侧必须用 policy.is_retrievable(case, decision_time) 施加 as-of 硬约束。"
                "Case Library ≠ Rule Engine：只记录历史事实与机械标签，不输出交易规则。"
            ),
            "source_files": [
                "code/data/predictions_v2.csv",
                "code/data/predictions_v2_val.csv",
                "code/data/stage3/risk_features.parquet",
                "code/data/canonical.parquet",
                "code/risk_gate/gate.py",
            ],
            "derived_conventions": {
                "suggested_action": "SELL_DA(er>=5, conf>=0.20) / BUY_DA(er<=-5, conf>=0.20) / NO_TRADE",
                "PnL": "SELL_DA=+actual_return; BUY_DA=-actual_return; NO_TRADE=0 (1 MWh/仓)",
                "direction_wrong": "有方向交易且 sign(actual_return) != 建议方向",
                "decision_cutoff": "decision_date 10:00 PT（官方 DAM Market Close）",
                "case_available_at": "target_date+1 06:00（目标日完整结算后，保守）",
            },
            "policy": {
                "tail_loss_threshold": policy.tail_loss_threshold,
                "high_profit_threshold": policy.high_profit_threshold,
                "signal_strength_threshold": policy.signal_strength_threshold,
                "signal_strength_source": policy.signal_strength_source,
                "min_abs_return_for_signal": policy.min_abs_return_for_signal,
                "min_confidence_for_trade": policy.min_confidence_for_trade,
                "risk_gate_failure_include_warning": policy.risk_gate_failure_include_warning,
            },
            "case_type_counts": counts,
            "case_count": len(cases),
            "note": "全部自动生成，无人工挑选；HUMAN_OVERRIDE 需 human_action 输入（当前数据无，未触发）。",
        },
        "cases": [c.to_dict() for c in cases],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"cases_auto.json written: {out}")
    print(f"case_count: {len(cases)}")
    print("case_type_counts:", counts)
    print("---")
    for c in cases[:8]:
        print(c.short_summary())
    if len(cases) > 8:
        print(f"... (+{len(cases) - 8} more)")


if __name__ == "__main__":
    main()
