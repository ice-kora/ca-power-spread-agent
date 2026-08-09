# -*- coding: utf-8 -*-
"""
code/decision/audit.py —— DecisionService 运行时审计（V0.3.1.1 工程加固）
========================================================================

对一次已完成的决策对象做**真实运行检查**（不是写死的 "PASS"）。每项检查输出
PASS / WARNING / FAIL + checked_count / failed_count / reason；OVERALL 由真实
结果计算（任一 FAIL → FAIL；否则任一 WARNING → WARNING；否则 PASS），
禁止硬编码。

至少 5 项检查（任务要求）：
  1. feature_eligibility  : Feature Time Gate —— 每个决策特征的有效可用上界
                            （latest_possible_available_at，UI 展示口径）必须
                            <= decision_cutoff，且 decision_eligible 不得在
                            上界晚于 cutoff 时仍为 True（P0-2 硬规则）。
  2. evidence_time_gate   : Evidence Time Gate —— eligible 证据的 available_at
                            （Time Gate 真正用的那个）必须 <= decision_cutoff 且
                            非 MOCK；rejected 证据不得出现"实际可在 cutoff 前
                            使用却进了 rejected"（泄漏 / 误标）。
  3. mock_data            : 决策路径无 MOCK —— features / eligible evidence /
                            rule_engine 输入中不得出现 is_mock=True；
                            MOCK 只允许存在于 rejected 隔离桶。
  4. case_asof            : Case 不可穿越 —— 所有进入决策的相似案例必须
                            is_retrievable(case, decision_time)（case_available_at
                            <= 决策时点）。
  5. outcome_leakage      : Outcome 泄漏 —— 未 reveal 时决策对象任何决策关键
                            段（prediction / risk_gate / rule_engine / outcome /
                            post_trade / _post_inputs）不得出现 actual_* / pnl；
                            reveal 后必须带 outcome + post_trade_review。

时间口径：与 schemas / time_gate 完全一致——available_at / decision_cutoff 均
为 UTC naive ISO 或可解析的时间字符串；本模块不重算业务判定，只做一致性复算
（单一真相来源 = AsOfRecord / Time Gate Result / RuleEngine 判定）。
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from agent.evidence.time_gate import validate_feature_eligibility  # noqa: E402
from agent.case_library.policy import is_retrievable  # noqa: E402

AUDIT_VERSION = "0.1.0"

#: 决策关键段中禁止出现的"当前交易 outcome"字段（未 reveal 时）
ACTUAL_KEYS: tuple = ("actual_da", "actual_rtpd", "actual_return", "pnl")
#: 审计遍历时跳过（历史 case / 证据段允许携带历史 outcome；audit 段自身）
_SKIP_SECTIONS = {"cases", "top_cases", "evidences", "evidence", "audit"}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _parse_ts(value: Any) -> Optional[pd.Timestamp]:
    """解析为 naive pd.Timestamp；失败返回 None。"""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(str(value).strip())
        return ts.tz_localize(None) if ts.tz is not None else ts
    except Exception:
        return None


def _ts_le(value: Any, cutoff: Any) -> bool:
    """value 可解析且 <= cutoff 才 True（任一时间缺失 → False，宁保守不穿越）。"""
    a = _parse_ts(value)
    c = _parse_ts(cutoff)
    if a is None or c is None:
        return False
    try:
        return a <= c
    except Exception:
        return False


def _effective_available_at(ev: Dict[str, Any]) -> str:
    """Time Gate 真正用的 available_at：优先 available_at，缺失回退 published_at。"""
    return str(ev.get("available_at") or ev.get("published_at") or "").strip()


def _check(check_id: str, label: str, checked_count: int, failed_count: int,
           status: str, reason: str = "") -> Dict[str, Any]:
    return {
        "check_id": check_id,
        "label": label,
        "status": status,                      # PASS / WARNING / FAIL
        "checked_count": int(checked_count),
        "failed_count": int(failed_count),
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 五项检查
# ---------------------------------------------------------------------------
def audit_feature_eligibility(features: Sequence[Dict[str, Any]],
                              decision_cutoff: Optional[str]) -> Dict[str, Any]:
    """检查 1：Feature Time Gate（P0-2 硬规则，展示 == 判定）。

    复用 time_gate.validate_feature_eligibility：任何 decision_eligible=True 的
    特征，其 displayed available_at 上界必须存在且 <= decision_cutoff。
    """
    feats = list(features or [])
    violations = validate_feature_eligibility(feats, decision_cutoff)
    if violations:
        return _check(
            "feature_eligibility", "Feature Eligibility",
            len(feats), len(violations), "FAIL",
            reason="Feature Time Gate 违反 P0-2（decision_eligible=True 但 available_at "
                   "上界缺失或晚于 decision_cutoff）: " + "; ".join(violations[:5]),
        )
    return _check(
        "feature_eligibility", "Feature Eligibility",
        len(feats), 0, "PASS",
        reason=f"全部 {len(feats)} 个决策特征可用上界 <= decision_cutoff（P0-2 展示==判定）",
    )


def audit_evidence_time_gate(evidence_section: Dict[str, Any],
                             decision_cutoff: Optional[str]) -> Dict[str, Any]:
    """检查 2：Evidence Time Gate。

    - eligible 中每条：effective available_at <= decision_cutoff 且非 MOCK。
    - rejected 中每条（非 MOCK）：effective available_at 必须晚于 cutoff
      （否则它本应 eligible —— 是泄漏 / 误标）。
    """
    sec = evidence_section or {}
    eligible = list(sec.get("eligible", []) or [])
    rejected = list(sec.get("rejected", sec.get("post_decision", [])) or [])
    checked = eligible + rejected
    fails: List[str] = []
    for e in eligible:
        eid = str(e.get("evidence_id", "?"))
        if e.get("is_mock"):
            fails.append(f"eligible 含 MOCK: {eid}")
        avail = _effective_available_at(e)
        if not _ts_le(avail, decision_cutoff):
            fails.append(f"eligible 的 available_at({avail}) > decision_cutoff({decision_cutoff}): {eid}")
    for e in rejected:
        eid = str(e.get("evidence_id", "?"))
        if e.get("is_mock"):
            continue  # MOCK 隔离桶合法
        avail = _effective_available_at(e)
        if _ts_le(avail, decision_cutoff):
            fails.append(f"rejected 却可在 cutoff 前使用（available_at={avail}<=cutoff）→ 泄漏/误标: {eid}")
    if fails:
        return _check(
            "evidence_time_gate", "Evidence Time Gate",
            len(checked), len(fails), "FAIL",
            reason="Evidence Time Gate 不一致: " + "; ".join(fails[:5]),
        )
    return _check(
        "evidence_time_gate", "Evidence Time Gate",
        len(checked), 0, "PASS",
        reason=f"{len(eligible)} eligible / {len(rejected)} rejected，时间门槛与 MOCK 隔离一致",
    )


def audit_mock_data(features: Sequence[Dict[str, Any]],
                    evidence_section: Dict[str, Any],
                    rule_engine_out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """检查 3：Mock Data —— 决策路径（features / eligible / rule_engine 输入）无 MOCK。

    MOCK 只允许存在于 rejected 隔离桶（测试/演示用）。rule_engine.features_used /
    evidence_used 若含 is_mock 也计失败。
    """
    feats = list(features or [])
    sec = evidence_section or {}
    eligible = list(sec.get("eligible", []) or [])
    rejected = list(sec.get("rejected", sec.get("post_decision", [])) or [])
    fails: List[str] = []
    for f in feats:
        if f.get("is_mock"):
            fails.append(f"feature is_mock: {f.get('feature')}")
    for e in eligible:
        if e.get("is_mock"):
            fails.append(f"eligible evidence is_mock: {e.get('evidence_id')}")
    re_out = rule_engine_out or {}
    for k in ("features_used", "evidence_used"):
        v = re_out.get(k)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and item.get("is_mock"):
                    fails.append(f"rule_engine.{k} is_mock: {item.get('feature') or item.get('evidence_id')}")
    # MOCK 在 rejected 桶里是合法隔离（不计失败，仅计入检查项）
    checked = len(feats) + len(eligible) + len(rejected)
    mock_in_rejected = sum(1 for e in rejected if e.get("is_mock"))
    if fails:
        return _check(
            "mock_data", "Mock Data", checked, len(fails), "FAIL",
            reason="决策路径出现 MOCK（仅 rejected 隔离桶允许）: " + "; ".join(fails[:5]),
        )
    note = f"（另 {mock_in_rejected} 条 MOCK 在 rejected 隔离桶，合法）" if mock_in_rejected else ""
    return _check(
        "mock_data", "Mock Data", checked, 0, "PASS",
        reason=f"决策路径（features/eligible/rule_engine）无 MOCK{note}",
    )


def audit_case_asof(cases: Sequence[Dict[str, Any]],
                    decision_time: Optional[str]) -> Dict[str, Any]:
    """检查 4：Case As-of —— 决策用相似案例必须 is_retrievable（不可穿越）。"""
    cxs = list(cases or [])
    fails: List[str] = []
    for c in cxs:
        cid = str(c.get("case_id", "?"))
        if not is_retrievable(c, decision_time):
            fails.append(f"case 不可检索（case_available_at > decision_time 或缺省）: {cid}")
    if fails:
        return _check(
            "case_asof", "Case As-of", len(cxs), len(fails), "FAIL",
            reason="相似案例穿越决策时点: " + "; ".join(fails[:5]),
        )
    return _check(
        "case_asof", "Case As-of", len(cxs), 0, "PASS",
        reason=f"全部 {len(cxs)} 个相似案例 case_available_at <= decision_time({decision_time})",
    )


def audit_outcome_leakage(decision_obj: Dict[str, Any],
                          outcome_revealed: bool) -> Dict[str, Any]:
    """检查 5：Outcome Leakage —— 未 reveal 时决策关键段不得出现 actual_* / pnl。

    历史 case（cases/top_cases）与证据段（evidences/evidence）允许携带历史
    outcome（合法），审计跳过；audit 段自身跳过。
    """
    obj = decision_obj or {}
    if outcome_revealed:
        missing = []
        checks = (("outcome", obj.get("outcome")), ("post_trade_review", obj.get("post_trade_review")))
        for name, val in checks:
            if not val:
                missing.append(name)
        if missing:
            return _check(
                "outcome_leakage", "Outcome Leakage", len(checks), len(missing), "FAIL",
                reason="outcome_revealed=True 但缺少 " + ", ".join(missing),
            )
        return _check(
            "outcome_leakage", "Outcome Leakage", len(checks), 0, "PASS",
            reason="已 reveal：outcome 与 post_trade_review 齐备，actual_* 仅在 outcome 段",
        )

    leaks: List[str] = []
    visited: Dict[str, int] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                kp = f"{path}.{k}" if path else str(k)
                visited[kp] = visited.get(kp, 0) + 1
                if str(k) in _SKIP_SECTIONS:
                    continue
                if str(k) in ACTUAL_KEYS and v is not None:
                    leaks.append(f"{kp}={v!r}")
                else:
                    walk(v, kp)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(obj, "")
    checked = sum(visited.values())
    if leaks:
        return _check(
            "outcome_leakage", "Outcome Leakage", checked, len(leaks), "FAIL",
            reason="未 reveal 却出现 actual_*/pnl: " + "; ".join(leaks[:5]),
        )
    return _check(
        "outcome_leakage", "Outcome Leakage", checked, 0, "PASS",
        reason=f"未 reveal：遍历 {checked} 个字段未发现 actual_*/pnl 泄漏",
    )


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------
def run_runtime_audit(
    *,
    features: Sequence[Dict[str, Any]],
    evidence_section: Dict[str, Any],
    cases: Sequence[Dict[str, Any]],
    decision_time: Optional[str],
    decision_cutoff: Optional[str],
    decision_obj: Dict[str, Any],
    outcome_revealed: bool,
    rule_engine_out: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对一次决策运行全部运行时检查，OVERALL 由真实结果计算（不硬编码）。"""
    checks: List[Dict[str, Any]] = [
        audit_feature_eligibility(features, decision_cutoff),
        audit_evidence_time_gate(evidence_section, decision_cutoff),
        audit_mock_data(features, evidence_section, rule_engine_out),
        audit_case_asof(cases, decision_time),
        audit_outcome_leakage(decision_obj, outcome_revealed),
    ]
    statuses = [c["status"] for c in checks]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARNING" in statuses:
        overall = "WARNING"
    else:
        overall = "PASS"
    n_pass = sum(1 for c in checks if c["status"] == "PASS")
    return {
        "overall": overall,
        "version": AUDIT_VERSION,
        "summary": f"{n_pass}/{len(checks)} PASS",
        "checks": {c["check_id"]: c for c in checks},
        "check_list": checks,
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bad = run_runtime_audit(
        features=[{"feature": "spread_lag1", "decision_eligible": True,
                   "available_at": "2026-07-08 23:59:00",   # 晚于 cutoff 的伪精确时间戳
                   "decision_cutoff": "2026-07-08T17:00:00"}],
        evidence_section={"eligible": [{"evidence_id": "E", "is_mock": True}]},
        cases=[{"case_id": "C", "case_available_at": "2026-07-10T09:00:00"}],
        decision_time="2026-07-08T10:00:00",
        decision_cutoff="2026-07-08T17:00:00",
        decision_obj={"_post_inputs": {"actual_da": 1.0}},
        outcome_revealed=False,
    )
    import json
    print(json.dumps(bad, ensure_ascii=False, indent=2))
