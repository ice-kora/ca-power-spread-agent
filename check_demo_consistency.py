# -*- coding: utf-8 -*-
"""
check_demo_consistency.py —— FULL vs DEMO Golden Case 一致性检查（Agent A）
================================================================================

对 5 个 Golden Cases，比较 FULL（完整数据）与 DEMO（demo_artifacts 切片）的决策结果：
  expected_return / direction_probability / model_signal_strength / risk_gate_result /
  rule_engine_result / final_recommendation / actual_da / actual_rtpd / pnl
一致（合理容差）。输出 docs/v0311_consistency.md（Golden Case Consistency: 5/5 PASS）。

设计（保证可比性）：
  * 两个模式都用 StaticEvidenceAdapter([])（确定性、离线）——证据（GFS）severity=INFO /
    directional_effect=UNCERTAIN，不参与 gate/rule；网络可用性不影响所比较字段。
  * canonical_demo 只做行切片 → Golden 行 expected_return / actual_* 与 FULL 逐位一致。
  * cases_demo.json 为任一 Golden 决策时点可检索案例的**超集** → R8（SIMILAR_TAIL_LOSS_CASE）
    与相似案例展示与 FULL 一致。
  * 额外核验：manifest.contains_mock==false / data_mode==DEMO / 决策对象无 is_mock 特征
    （DEMO ≠ MOCK）。

用法：
    python check_demo_consistency.py          # 写 docs/v0311_consistency.md
    python build_demo_artifacts.py --check    # 生成后自动调用本脚本
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.decision_service import DecisionService, StaticEvidenceAdapter  # noqa: E402
from data_mode import MODE_DEMO, MODE_FULL  # noqa: E402

#: Golden Cases（与 docs/mvp_demo_cases.md / build_demo_artifacts.py 一致）
GOLDEN_CASES: List[Dict[str, Any]] = [
    {"id": "B",  "label": "B · SELL 盈利（彩票右尾）",            "decision_date": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3},
    {"id": "C1", "label": "C1 · NO_TRADE 避险（RiskGate 成功）",  "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
    {"id": "C2", "label": "C2 · NO_TRADE 弱信号",                "decision_date": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10},
    {"id": "D",  "label": "D · 模型 SELL 但错（诚实展示）",       "decision_date": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20},
    {"id": "E",  "label": "E · Evidence 被 Time Gate 拒（同 C1 参数）", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
]

#: 数值容差（相对 + 绝对）
RTOL = 1e-6
ATOL = 1e-6


def _close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= ATOL + RTOL * max(abs(a), abs(b))


def _extract(dec: Dict[str, Any]) -> Dict[str, Any]:
    """从决策对象提取一致性比较所需字段。"""
    mo = dec.get("model_output", {})
    rg = dec.get("risk_gate", {})
    re = dec.get("rule_engine", {})
    pt = dec.get("post_trade", {}) or {}
    return {
        "expected_return": mo.get("expected_return"),
        "direction_probability": mo.get("direction_probability"),
        "model_signal_strength": mo.get("model_signal_strength"),
        "risk_gate_result": (rg.get("decision"), tuple(rg.get("risk_reasons", []))),
        "rule_engine_result": (re.get("decision"), tuple(re.get("reasons", []))),
        "final_recommendation": dec.get("final_recommendation"),
        "actual_da": pt.get("actual_da"),
        "actual_rtpd": pt.get("actual_rtpd"),
        "pnl": pt.get("pnl"),
    }


_COMPARATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "expected_return": lambda a, b: _close(a, b),
    "direction_probability": lambda a, b: _close(a, b),
    "model_signal_strength": lambda a, b: _close(a, b),
    "actual_da": lambda a, b: _close(a, b),
    "actual_rtpd": lambda a, b: _close(a, b),
    "pnl": lambda a, b: _close(a, b),
    "risk_gate_result": lambda a, b: a == b,
    "rule_engine_result": lambda a, b: a == b,
    "final_recommendation": lambda a, b: a == b,
}

_FIELD_LABELS: Dict[str, str] = {
    "expected_return": "expected_return",
    "direction_probability": "direction_probability",
    "model_signal_strength": "signal_strength",
    "risk_gate_result": "risk_gate_result",
    "rule_engine_result": "rule_engine_result",
    "final_recommendation": "final_recommendation",
    "actual_da": "actual_da",
    "actual_rtpd": "actual_rtpd",
    "pnl": "pnl",
}


def _flat_tuple(v: Any) -> List[Any]:
    """递归展开嵌套 tuple → 扁平列表。"""
    out: List[Any] = []
    if isinstance(v, tuple):
        for item in v:
            if isinstance(item, tuple):
                out.extend(_flat_tuple(item))
            else:
                out.append(item)
    else:
        out.append(v)
    return out


def _fmt(v: Any) -> str:
    if isinstance(v, tuple):
        parts = [str(x) for x in _flat_tuple(v) if x not in ("", None)]
        return " · ".join(parts)
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.4f}"
    return str(v)


def compare_case(svc_full: DecisionService, svc_demo: DecisionService,
                 case: Dict[str, Any]) -> Dict[str, Any]:
    """对单个 Golden Case 运行 FULL 与 DEMO，返回逐字段比较结果。"""
    dd, node, hour = case["decision_date"], case["node"], int(case["hour"])
    dec_full = svc_full.run_decision(dd, node, hour, reveal=True)
    dec_demo = svc_demo.run_decision(dd, node, hour, reveal=True)
    f = _extract(dec_full)
    d = _extract(dec_demo)

    per_field: List[Dict[str, Any]] = []
    all_ok = True
    for key, cmp in _COMPARATORS.items():
        a, b = f.get(key), d.get(key)
        ok = cmp(a, b)
        if not ok:
            all_ok = False
        per_field.append({
            "field": _FIELD_LABELS[key],
            "full": _fmt(a),
            "demo": _fmt(b),
            "ok": ok,
        })
    # 附带核验：DEMO 决策对象不含 MOCK（is_mock 特征 / 证据一律 False）
    mock_feat = [t.get("feature") for t in dec_demo.get("top_features", []) if t.get("is_mock")]
    mock_ev = [e.get("evidence_id") for e in dec_demo.get("evidence", {}).get("eligible", []) if e.get("is_mock")]
    demo_no_mock = (len(mock_feat) == 0 and len(mock_ev) == 0)
    similar_full = [c.get("case_id") for c in dec_full.get("top_cases", [])]
    similar_demo = [c.get("case_id") for c in dec_demo.get("top_cases", [])]
    similar_same = similar_full == similar_demo
    return {
        "case_id": case["id"],
        "label": case["label"],
        "params": f"{dd} {node} H{hour}",
        "all_ok": all_ok,
        "demo_no_mock": demo_no_mock,
        "similar_same": similar_same,
        "per_field": per_field,
    }


def run_consistency(repo_root: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """运行 FULL vs DEMO 一致性，返回 (逐案例结果, 摘要)。"""
    root = Path(repo_root) if repo_root else REPO_ROOT
    svc_full = DecisionService(data_dir=root / "code" / "data",
                               evidence_adapter=StaticEvidenceAdapter([]))
    svc_demo = DecisionService(data_dir=root / "demo_artifacts",
                               evidence_adapter=StaticEvidenceAdapter([]))

    results = []
    for case in GOLDEN_CASES:
        r = compare_case(svc_full, svc_demo, case)
        results.append(r)

    n_pass = sum(1 for r in results if r["all_ok"] and r["demo_no_mock"])
    n_total = len(results)
    manifest_path = root / "demo_artifacts" / "manifest.json"
    manifest_ok = False
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_ok = (manifest.get("data_mode") == MODE_DEMO
                       and manifest.get("contains_mock") is False
                       and manifest.get("contains_future_outcome") is True)
    summary = {
        "n_pass": n_pass,
        "n_total": n_total,
        "overall": f"Golden Case Consistency: {n_pass}/{n_total} PASS",
        "manifest_ok": manifest_ok,
    }
    return results, summary


def _md_table(rows: List[Dict[str, Any]]) -> str:
    header = "| 案例 | decision_date node H | expected_return | direction_prob | signal_strength | risk_gate | rule_engine | final | actual_da | actual_rtpd | pnl | 结论 |"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows:
        pf = {p["field"]: p for p in r["per_field"]}
        g = pf["risk_gate_result"]["full"]
        rule = pf["rule_engine_result"]["full"]
        lines.append(
            f"| **{r['case_id']}** | {r['params']} "
            f"| {pf['expected_return']['full']} | {pf['direction_probability']['full']} "
            f"| {pf['signal_strength']['full']} | {g} | {rule} "
            f"| {pf['final_recommendation']['full']} | {pf['actual_da']['full']} "
            f"| {pf['actual_rtpd']['full']} | {pf['pnl']['full']} "
            f"| {'PASS' if r['all_ok'] and r['demo_no_mock'] else 'FAIL'} |"
        )
    return "\n".join(lines)


def write_consistency_doc(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root else REPO_ROOT
    results, summary = run_consistency(root)

    detail_lines = []
    for r in results:
        detail_lines.append(f"#### Case {r['case_id']} · {r['label']}")
        detail_lines.append(f"- 参数：`{r['params']}`")
        detail_lines.append(f"- DEMO 无 MOCK（is_mock 特征/证据均为 False）："
                            f"{'PASS' if r['demo_no_mock'] else 'FAIL'}")
        detail_lines.append(f"- 相似案例（top cases case_id）FULL == DEMO："
                            f"{'PASS' if r['similar_same'] else 'FAIL'}")
        bad = [p for p in r["per_field"] if not p["ok"]]
        if bad:
            detail_lines.append("- 不一致字段：")
            for p in bad:
                detail_lines.append(f"  - `{p['field']}`：FULL=`{p['full']}`，DEMO=`{p['demo']}`")
        else:
            detail_lines.append("- 9 项比较字段全部一致。")
        detail_lines.append("")

    doc = f"""# Golden Case Consistency（FULL vs DEMO）

> 由 `check_demo_consistency.py` 生成（Agent A · Demo Artifacts）。比较 5 个 Golden Cases 在
> **FULL**（完整数据 `code/data/*`）与 **DEMO**（`demo_artifacts/` 真实历史最小切片）两种模式下的
> 决策结果一致性。交易核心冻结：只做数据切片，不改任何模型 / 规则 / 阈值 / PnL。

## 总体结论

**{summary['overall']}**

- 比较字段（9 项）：`expected_return` / `direction_probability` / `model_signal_strength`
  / `risk_gate_result` / `rule_engine_result` / `final_recommendation` / `actual_da` /
  `actual_rtpd` / `pnl`（数值容差 RTOL={RTOL} / ATOL={ATOL}）。
- 证据口径：两模式均用 `StaticEvidenceAdapter([])`（确定性、离线）。GFS 证据 severity=INFO /
  directional_effect=UNCERTAIN，不参与 gate/rule；`evidence` 不打包进 demo 切片，网络可用性
  **不影响**所比较字段。
- 展示类字段（非比较项）：`top_features` 的特征统计 z-score 属解释性展示（非 SHAP），
  DEMO 用 90 天前置窗口计算，可能与 FULL（全历史）略有差异；**不影响**任何决策字段。
- manifest 核验：`data_mode=="DEMO"`、`contains_mock==false`、`contains_future_outcome==true`
  → **{'PASS' if summary['manifest_ok'] else 'FAIL'}**。

## 案例表（FULL 值；DEMO 与之一致）

{_md_table(results)}

## 逐案例明细

{chr(10).join(detail_lines)}

## DEMO ≠ MOCK（明确声明）

- **DEMO**：`demo_artifacts/` 是**真实历史记录**的子集（Golden Cases 决策所需行），保留全部
  PnL / prediction / decision 数值；决策链照常运行，可给出真实 BUY_DA / SELL_DA / NO_TRADE 推荐。
- **MOCK**：编造/占位数据，`is_mock=True`，永不参与真实推荐（Evidence Time Gate R7 硬隔离）。
- 本一致性核验同时断言：每个 DEMO 决策对象的 `top_features` 与 `evidence.eligible` 中
  **不存在任何 is_mock 项**。

## 复现

```bash
# 1. 在完整数据机上抽取 demo 切片 + 跑一致性（生成本文档）
python build_demo_artifacts.py --check

# 2. 单独跑一致性
python check_demo_consistency.py

# 3. clean clone（无 code/data）直接以 DEMO 模式启动
python prepare_mvp.py            # 显示 DATA MODE = DEMO
python mvp_web.py
```
"""
    out = root / "docs" / "v0311_consistency.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return summary


def run_and_write_consistency_doc(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """供 build_demo_artifacts.py --check 调用。"""
    summary = write_consistency_doc(repo_root)
    print(f"    consistency: {summary['overall']} · manifest {summary['manifest_ok']}")
    return summary


if __name__ == "__main__":
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    results, summary = run_consistency()
    for r in results:
        tag = "PASS" if r["all_ok"] and r["demo_no_mock"] else "FAIL"
        print(f"  Case {r['case_id']}: {tag}  {r['params']}")
    print(f"  {summary['overall']}  · manifest {summary['manifest_ok']}")
    write_consistency_doc()
    print(f"  -> docs/v0311_consistency.md")
