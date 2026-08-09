# -*- coding: utf-8 -*-
"""
code/tests/test_v0311_hardening.py —— V0.3.1.1 Demo Hardening 15 项验收测试（Agent D）
========================================================================================

覆盖（任务要求 15 项）：
  H1  Clean Demo Mode artifacts 可启动（clean clone：无 code/data，仅 demo_artifacts → DEMO，跑通 5 个 Golden Cases）
  H2  Demo Mode 不等于 Mock（DEMO 决策对象无任何 is_mock 项，给出真实推荐）
  H3  Demo artifact contains_mock == false（manifest.json / metadata.json 声明 + 哈希可复现）
  H4  FULL / DEMO Golden Cases 一致（5/5；仅当完整 artifacts 存在时运行，clean clone 自动跳过）
  H5  Evidence published_at / available_at 不再混用（快照行同时保留两字段，available_at_source 诚实标注）
  H6  DecisionService 不重算 Evidence Time Gate（消费 AsOfRecord 判定：available_at 优先，
      非 published_at；split_eligible 程序裁决被原样消费）
  H7  Web 与 Tool 返回相同 available_at（同一 DecisionSnapshot，展示口径单一事实来源）
  H8  Runtime Audit 不存在硬编码 PASS（OVERALL 由 check 真实 status 推导，源码含推导分支）
  H9  Audit FAIL 能真实触发（feature_eligibility 违例 / outcome 泄漏 / Web NO_MOCK → FAIL → OVERALL FAIL）
  H10 Rule Engine 收到 features_used（rule_engine.features_used == top_features，P0-2 一致）
  H11 Rule Engine 收到 Evidence（rule_engine.evidence_used 反映 eligible 证据方向上下文）
  H12 Outcome Lock/Reveal Access Control（Service + Tool + Web 三层）
  H13 Web / CLI / Tool 同一个 DecisionSnapshot（decision_id / final / 数值一致）
  H14 Missing data 错误可读（非 traceback：Web 结构化错误 + Service 可读 ValueError）
  H15 无 LLM 时 Web 核心功能正常（决策 / Lock / Reveal 照常，Ask 诚实降级）

全部测试离线、确定性：
  * 决策一律用 StaticEvidenceAdapter([])（不联网）。
  * 测试 4 仅在完整 artifacts 存在时运行；clean clone 自动 skip（一致性需在完整数据机验证）。
  * clean-clone 的 prepare_mvp.py auto→DEMO 另以子进程模拟（见 H1 + 报告）。

运行（仓库根目录）：
    python code/tests/test_v0311_hardening.py
    python -m unittest code.tests.test_v0311_hardening -v
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.decision_service import (  # noqa: E402
    OUTCOME_NOT_REVEALED,
    DecisionService,
    StaticEvidenceAdapter,
)
from data_mode import (  # noqa: E402
    DEMO_DIR_NAME,
    MODE_DEMO,
    MODE_FULL,
    resolve_data_mode,
)

#: Golden Cases（与 docs/mvp_demo_cases.md / build_demo_artifacts.py / check_demo_consistency.py 一致）
GOLDEN_CASES = [
    {"id": "B",  "decision_date": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3, "final": "SELL_DA",  "gate": "PASS"},
    {"id": "C1", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "gate": "REJECT"},
    {"id": "C2", "decision_date": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10, "final": "NO_TRADE", "gate": "WARNING"},
    {"id": "D",  "decision_date": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20, "final": "SELL_DA",  "gate": "WARNING"},
    {"id": "E",  "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "gate": "REJECT"},
]

DD = "2026-07-08"
NODE = "CONTROLX_1_N001"
HOUR = 2
TARGET_DATE = "2026-07-09"
CUTOFF_UTC = "2026-07-08T17:00:00"      # decision_date 10:00 PT → UTC（PDT=UTC−7）
PRE_CUTOFF = "2026-07-08T09:00:00"      # <= cutoff
POST_CUTOFF = "2026-07-08T18:00:00"     # > cutoff


def _mk_ev(eid: str, published_at: str, *, available_at: str = "",
           severity: str = "WATCH", is_mock: bool = False,
           directional_effect: str = "UNCERTAIN") -> dict:
    """测试用证据 dict（与既有 test_decision_service 口径一致，另支持 available_at）。"""
    return {
        "evidence_id": eid,
        "event_type": "WEATHER_FORECAST",
        "region": "ZP26",
        "affected_nodes": [NODE],
        "severity": severity,
        "source": "TEST_GFS",
        "source_url": "",
        "source_type": "WEATHER",
        "is_mock": is_mock,
        "raw_source_id": eid,
        "published_at": published_at,
        "available_at": available_at,
        "retrieved_at": "2026-08-09T00:00:00",
        "target_time": "2026-07-09T02:00:00",
        "decision_cutoff": CUTOFF_UTC,
        "summary": f"test evidence {eid}",
        "directional_effect": directional_effect,
        "confidence": 0.5,
    }


def make_svc(evidence=None, data_dir=None) -> DecisionService:
    """离线确定性 DecisionService：默认静态空证据（不联网）。"""
    adapter = evidence if evidence is not None else StaticEvidenceAdapter([])
    return DecisionService(data_dir=data_dir, evidence_adapter=adapter)


# ---------------------------------------------------------------------------
# CLI 决策链（V0.3.1.2 起：CLI 即 DecisionService，无第二套 RiskGate/RuleEngine；
# 供 H13 验证 Web / CLI / Tool 消费同一个 DecisionSnapshot）
# ---------------------------------------------------------------------------
def _cli_decision(svc: DecisionService, dd: str, node: str, hour: int) -> dict:
    """CLI 的最终建议 == 同一 DecisionService 的 DecisionSnapshot（不重算）。"""
    dec = svc.run_decision(dd, node, hour)
    return {"final": dec["final_recommendation"],
            "rule_reasons": list(dec.get("reason_codes", [])),
            "expected_return": dec["model_output"].get("expected_return")}


class V0311HardeningTests(unittest.TestCase):
    """V0.3.1.1 Demo Hardening 15 项验收。"""

    @classmethod
    def setUpClass(cls):
        info = resolve_data_mode()
        if not info.is_ready:
            raise unittest.SkipTest(f"数据不可用（mode={info.mode}）：请先运行 prepare_mvp.py")

    # ============================================================= H1
    def test_h1_clean_demo_mode_artifacts_can_start(self):
        """Clean Demo Mode artifacts 可启动：模拟 clean clone（无 code/data，仅 demo_artifacts）。"""
        demo_dir = REPO_ROOT / DEMO_DIR_NAME
        self.assertTrue(demo_dir.exists(),
                        "demo_artifacts/ 应存在（先运行 python build_demo_artifacts.py）")
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp)
            shutil.copytree(demo_dir, clone / DEMO_DIR_NAME)
            # 仅 demo_artifacts → 自动解析为 DEMO
            info = resolve_data_mode(repo_root=clone)
            self.assertEqual(info.mode, MODE_DEMO)
            self.assertFalse((clone / "code" / "data" / "canonical.parquet").exists())
            svc = make_svc(data_dir=clone / DEMO_DIR_NAME)
            self.assertEqual(svc.data_mode, MODE_DEMO)
            # 5 个 Golden Cases 全部跑通，final / gate 与 golden 一致
            for case in GOLDEN_CASES:
                with self.subTest(case=case["id"]):
                    dec = svc.run_decision(case["decision_date"], case["node"],
                                           case["hour"], reveal=True)
                    self.assertEqual(dec["final_recommendation"], case["final"])
                    self.assertEqual(dec["risk_gate"]["decision"], case["gate"])
                    self.assertEqual(dec["post_trade"]["status"], "REVEALED")

    # ============================================================= H2
    def test_h2_demo_mode_is_not_mock(self):
        """Demo Mode 不等于 Mock：DEMO 决策对象无任何 is_mock 项，且有真实模型输出。"""
        demo_dir = REPO_ROOT / DEMO_DIR_NAME
        svc = make_svc(data_dir=demo_dir)
        self.assertEqual(svc.data_mode, MODE_DEMO)
        for case in GOLDEN_CASES:
            with self.subTest(case=case["id"]):
                dec = svc.run_decision(case["decision_date"], case["node"], case["hour"])
                mock_feats = [f["feature"] for f in dec.get("top_features", []) if f.get("is_mock")]
                self.assertEqual(mock_feats, [])
                for ev in dec.get("evidence", {}).get("eligible", []):
                    self.assertFalse(ev.get("is_mock"))
                self.assertIn(dec["final_recommendation"], ("BUY_DA", "SELL_DA", "NO_TRADE"))
                self.assertEqual(dec["audit"]["meta"]["data_mode"], MODE_DEMO)
                # 有真实模型输出（不是 MOCK 占位）
                mo = dec.get("model_output", {})
                self.assertIsNotNone(mo.get("expected_return"))

    # ============================================================= H3
    def test_h3_demo_artifact_contains_mock_false(self):
        """Demo artifact contains_mock == false：manifest + metadata 声明 + 哈希可复现。"""
        demo_dir = REPO_ROOT / DEMO_DIR_NAME
        manifest_path = demo_dir / "manifest.json"
        metadata_path = demo_dir / "metadata.json"
        self.assertTrue(manifest_path.exists())
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(m["data_mode"], MODE_DEMO)
        self.assertFalse(m["contains_mock"])
        self.assertTrue(m["contains_future_outcome"])
        self.assertIn("artifact_version", m)
        self.assertEqual(len(m.get("golden_cases", [])), 5)
        self.assertIn("source_files", m)
        self.assertIn("row_counts", m)
        self.assertIn("hashes", m)
        # V0.3.1.2：跨平台 canonical 哈希（CRLF/LF 归一化，clean clone 可复现）
        from code.artifact_hash import canonical_sha256  # noqa: PLC0415
        for fname, h in m["hashes"].items():
            self.assertTrue((demo_dir / fname).exists(), fname)
            digest = canonical_sha256(demo_dir / fname)
            self.assertEqual(digest, h, f"{fname} 哈希应匹配 manifest（canonical）")
        # metadata 同样诚实声明
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["data_mode"], MODE_DEMO)
        self.assertFalse(meta["contains_mock"])

    # ============================================================= H4
    def test_h4_full_vs_demo_golden_consistent(self):
        """FULL / DEMO Golden Cases 一致（5/5）。仅当完整 artifacts 存在时运行；clean clone 自动跳过。"""
        if resolve_data_mode().mode != MODE_FULL:
            self.skipTest("完整 artifacts 不存在（clean clone）——一致性需在完整数据机验证")
        from check_demo_consistency import run_consistency  # noqa: PLC0415
        results, summary = run_consistency(REPO_ROOT)
        self.assertTrue(summary["manifest_ok"], "manifest 声明应一致")
        self.assertEqual(summary["n_pass"], summary["n_total"], summary["overall"])
        for r in results:
            self.assertTrue(r["all_ok"], f"Case {r['case_id']} 存在不一致字段")
            self.assertTrue(r["demo_no_mock"], f"Case {r['case_id']} DEMO 混入 MOCK")

    # ============================================================= H5
    def test_h5_evidence_published_available_not_mixed(self):
        """Evidence published_at / available_at 不再混用：快照行同时保留两字段。"""
        evs = [
            _mk_ev("EV-BOTH", PRE_CUTOFF, available_at="2026-07-08T10:30:00"),
            _mk_ev("EV-NO-AVAIL", PRE_CUTOFF),   # 只有 published_at → available_at 缺失
        ]
        svc = make_svc(evidence=StaticEvidenceAdapter(evs))
        dec = svc.run_decision(DD, NODE, HOUR)
        rows = {r["evidence_id"]: r for r in dec["evidence"]["eligible"]}
        self.assertIn("EV-BOTH", rows)
        b = rows["EV-BOTH"]
        # 两字段同时存在且保持原值，绝不把 available_at 合并进 published_at
        self.assertEqual(b["published_at"], PRE_CUTOFF)
        self.assertEqual(b["available_at"], "2026-07-08T10:30:00")
        self.assertNotEqual(b["published_at"], b["available_at"])
        self.assertEqual(b["available_at_source"], "available_at")
        # V0.3.1.5：只有 published_at、无 proven available_at → **不 eligible**
        # （published_at 只是发布元数据，绝不迁移 / fallback 成可用时刻）
        rej = {r["evidence_id"]: r for r in dec["evidence"]["rejected"]}
        self.assertIn("EV-NO-AVAIL", rej)
        n = rej["EV-NO-AVAIL"]
        self.assertEqual(n["published_at"], PRE_CUTOFF)
        self.assertEqual(n["available_at"], "")
        self.assertIn("AVAILABILITY_NOT_PROVEN", n["rejection_reason"])
        # 决策路径（eligible）无 MOCK，Time Gate 正常
        self.assertTrue(all(rr["decision_eligible"] for rr in rows.values()))

    # ============================================================= H6
    def test_h6_service_consumes_as_of_record_not_recompute(self):
        """DecisionService 不重算 Evidence Time Gate：消费 AsOfRecord 判定（available_at 优先）。

        构造两组证据证明 Time Gate 的判据是 available_at（AsOfRecord 时点）而非 published_at：
          * EV-LATE-AVAIL：published_at<=cutoff 但 available_at>cutoff → 必须 rejected。
          * EV-EARLY-AVAIL：published_at>cutoff 但 available_at<=cutoff → 必须 eligible。
        """
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        evs = [
            _mk_ev("EV-LATE-AVAIL", PRE_CUTOFF, available_at=POST_CUTOFF),
            _mk_ev("EV-EARLY-AVAIL", POST_CUTOFF, available_at=PRE_CUTOFF),
        ]
        svc = make_svc(evidence=StaticEvidenceAdapter(evs))
        dec = svc.run_decision(DD, NODE, HOUR)
        # 先独立验证 Time Gate 的判定（同一输入、同一 cutoff）
        gate_eligible, gate_post = split_eligible(list(evs), CUTOFF_UTC)
        gate_elig_ids = {e.get("evidence_id") for e in gate_eligible}
        gate_post_ids = {e.get("evidence_id") for e in gate_post}
        self.assertIn("EV-EARLY-AVAIL", gate_elig_ids)   # available_at 优先 → eligible
        self.assertIn("EV-LATE-AVAIL", gate_post_ids)    # available_at 晚于 cutoff → rejected
        # DecisionService 快照与 Time Gate 判定完全一致（原样消费，不重算）
        elig_ids = {r["evidence_id"] for r in dec["evidence"]["eligible"]}
        rej_ids = {r["evidence_id"] for r in dec["evidence"]["rejected"]}
        self.assertEqual(elig_ids, gate_elig_ids)
        self.assertEqual(rej_ids, gate_post_ids)
        # 逐行 decision_eligible == Time Gate 判定（decision_service 只写桶、不重算）
        for r in dec["evidence"]["eligible"]:
            self.assertTrue(r["decision_eligible"])
            self.assertEqual(r["rejection_reason"], "")
        for r in dec["evidence"]["rejected"]:
            self.assertFalse(r["decision_eligible"])
            self.assertTrue(r["rejection_reason"])
        # 拒绝原因使用 available_at（Time Gate 真正判据）——不出现"显示晚于 cutoff 却判 eligible"的矛盾
        late = next(r for r in dec["evidence"]["rejected"] if r["evidence_id"] == "EV-LATE-AVAIL")
        self.assertIn("POST_DECISION_EVIDENCE", late["rejection_reason"])

    # ============================================================= H7
    def test_h7_web_and_tool_same_available_at(self):
        """Web 与 Tool 返回相同 available_at：同一 DecisionSnapshot，单一事实来源。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        dec = r.get_json()["decision"]
        did = dec["decision_id"]
        web_avail = {f["feature"]: f.get("available_at") for f in dec["top_features"]}
        self.assertTrue(web_avail)
        # Tool 5 get_data_provenance（同一 web service 实例上的同一 decision_id）
        svc = mvp_web._SERVICES["offline"]  # noqa: SLF001
        provs = svc.get_data_provenance(did)["provenance"]
        self.assertTrue(provs)
        for p in provs:
            self.assertIn(p["feature"], web_avail,
                          f"Tool 返回的特征 {p['feature']} 应在 Web top_features 中")
            self.assertEqual(p.get("available_at"), web_avail[p["feature"]],
                             f"feature={p['feature']} 的 available_at Web 与 Tool 不一致")
            self.assertEqual(p.get("available_at_display"), web_avail[p["feature"]])

    # ============================================================= H8
    def test_h8_runtime_audit_no_hardcoded_pass(self):
        """Runtime Audit 不存在硬编码 PASS：OVERALL 由 5 项检查的真实 status 推导。"""
        from code.decision.audit import run_runtime_audit  # noqa: PLC0415
        # 1) 源码含推导分支（OVERALL 由 statuses 计算，不是常量）
        src = (REPO_ROOT / "code" / "decision" / "audit.py").read_text(encoding="utf-8")
        self.assertIn("def run_runtime_audit", src)
        self.assertIn('if "FAIL" in statuses', src)
        self.assertIn('elif "WARNING" in statuses', src)
        self.assertIn("overall = \"PASS\"", src)   # 仅作为推导末枝存在
        # 2) 健康决策 → 全部检查 PASS（V0.3.1.3 含 Evidence Availability/Provenance），
        #    且 OVERALL == 由 check_list 推导的结果（summary 动态，不写死 5/5）
        svc = make_svc()
        dec = svc.run_decision(DD, NODE, HOUR)
        audit = dec["audit"]
        checks = list(audit["checks"].values())
        self.assertEqual(audit["summary"], f"{len(checks)}/{len(checks)} PASS")
        self.assertEqual(audit["overall"], "PASS")
        statuses = [ch["status"] for ch in checks]
        derived = "FAIL" if "FAIL" in statuses else ("WARNING" if "WARNING" in statuses else "PASS")
        self.assertEqual(audit["overall"], derived)   # 由真实结果推导，非写死
        # 3) 每项检查 status 与 checked/failed 计数自洽
        for ch in checks:
            if ch["failed_count"] > 0:
                self.assertEqual(ch["status"], "FAIL", ch["check_id"])
            self.assertGreaterEqual(ch["checked_count"], 0)
            self.assertGreaterEqual(ch["failed_count"], 0)
        # 4) Web 侧审计同样由真实计数推导（无写死 PASS）
        import mvp_web  # noqa: PLC0415
        self.assertIn("if failed > 0", (REPO_ROOT / "mvp_web.py").read_text(encoding="utf-8"))
        self.assertIn('if "FAIL" in statuses',
                      (REPO_ROOT / "mvp_web.py").read_text(encoding="utf-8"))

    # ============================================================= H9
    def test_h9_audit_fail_truly_triggerable(self):
        """Audit FAIL 能真实触发：feature_eligibility 违例 / outcome 泄漏 / Web NO_MOCK。"""
        from code.decision.audit import run_runtime_audit  # noqa: PLC0415
        # --- 服务层：feature_eligibility 违例（decision_eligible=True 但 available_at 晚于 cutoff）---
        bad_feature = [{
            "feature": "spread_lag1", "decision_eligible": True,
            "availability_basis": "STRUCTURAL_LAG",
            "latest_possible_available_at": "2026-07-09T00:00:00",   # > cutoff
            "available_at": "≤ 2026-07-09 00:00 PT",
            "decision_cutoff": CUTOFF_UTC,
        }]
        audit = run_runtime_audit(
            features=bad_feature,
            evidence_section={"eligible": [], "rejected": []},
            cases=[], decision_time="2026-07-08T10:00:00",
            decision_cutoff=CUTOFF_UTC,
            decision_obj={}, outcome_revealed=False,
            rule_engine_out=None,
        )
        self.assertEqual(audit["checks"]["feature_eligibility"]["status"], "FAIL")
        self.assertEqual(audit["overall"], "FAIL")
        # --- 服务层：outcome 泄漏（未 reveal 却出现 actual_da）---
        leak_audit = run_runtime_audit(
            features=[], evidence_section={"eligible": [], "rejected": []},
            cases=[], decision_time="2026-07-08T10:00:00",
            decision_cutoff=CUTOFF_UTC,
            decision_obj={"_post_inputs": {"actual_da": 1.0}},
            outcome_revealed=False, rule_engine_out=None,
        )
        self.assertEqual(leak_audit["checks"]["outcome_leakage"]["status"], "FAIL")
        self.assertEqual(leak_audit["overall"], "FAIL")
        # --- Web 层：NO_MOCK=FAIL（eligible 含 is_mock 证据）---
        import mvp_web  # noqa: PLC0415
        dec = {
            "context": {"decision_date": DD, "target_date": TARGET_DATE,
                        "decision_cutoff_utc": CUTOFF_UTC},
            "top_features": [],
            "evidence": {"eligible": [{"evidence_id": "E1", "is_mock": True,
                                       "published_at": PRE_CUTOFF}], "rejected": []},
            "top_cases": [],
            "post_trade": {"status": "OUTCOME_NOT_REVEALED"},
            "outcome_revealed": False,
        }
        rt = mvp_web._compute_runtime_audit(dec)  # noqa: SLF001
        mock_item = next(i for i in rt["items"] if i["key"] == "NO_MOCK")
        self.assertEqual(mock_item["status"], "FAIL")
        self.assertEqual(rt["overall"], "FAIL")

    # ============================================================= H10
    def test_h10_rule_engine_receives_features_used(self):
        """Rule Engine 收到 features_used：rule_engine.features_used 与 top_features 一致。"""
        svc = make_svc()
        dec = svc.run_decision(DD, NODE, HOUR)
        re = dec["rule_engine"]
        fu = re.get("features_used") or []
        self.assertTrue(fu, "rule_engine.features_used 不应为空")
        self.assertEqual(len(fu), len(dec["top_features"]),
                         "RuleEngine 收到的 features_used 数量应等于展示 top_features")
        fu_names = {f["feature"] for f in fu}
        top_names = {f["feature"] for f in dec["top_features"]}
        self.assertEqual(fu_names, top_names)
        # P0-2 一致性校验：展示 available_at 与 Time Gate 判定一致（RuleEngine 自证）
        self.assertTrue(re.get("feature_eligibility_consistent", False))

    # ============================================================= H11
    def test_h11_rule_engine_receives_evidence(self):
        """Rule Engine 收到 Evidence：evidence_used 反映 eligible 证据的方向上下文。"""
        # 用 Golden B（SELL_DA，gate PASS）注入 SUPPORT_POSITIVE 证据：与方向一致不触发冲突
        evs = [_mk_ev("EV-SUPPORT", PRE_CUTOFF, available_at=PRE_CUTOFF,
                      directional_effect="SUPPORT_POSITIVE", severity="INFO")]
        svc = make_svc(evidence=StaticEvidenceAdapter(evs))
        dd_b, node_b, hour_b = "2026-07-16", "CONTROLX_1_N001", 3
        dec = svc.run_decision(dd_b, node_b, hour_b)
        # 证据确实进入 eligible（Time Gate 放行）
        elig_ids = {r["evidence_id"] for r in dec["evidence"]["eligible"]}
        self.assertIn("EV-SUPPORT", elig_ids)
        # RuleEngine 收到该证据（evidence_used 方向上下文）
        ev_used = dec["rule_engine"]["evidence_used"]
        self.assertIsInstance(ev_used, dict)
        self.assertGreaterEqual(ev_used.get("n", 0), 1)
        self.assertGreaterEqual(ev_used.get("SUPPORT_POSITIVE", 0), 1)
        self.assertTrue(ev_used.get("any_directional", False))
        # 决策不受该一致证据干扰（方向一致，不触发 EVIDENCE_CONFLICT）
        self.assertEqual(dec["final_recommendation"], "SELL_DA")

    # ============================================================= H12
    def test_h12_outcome_lock_reveal_access_control(self):
        """Outcome Lock/Reveal Access Control：Service + Tool + Web 三层。"""
        # ---- Service + Tool 层 ----
        svc = make_svc()
        did = svc.run_decision(DD, NODE, HOUR, reveal=False)["decision_id"]
        # Tool 6：未 reveal → 拒绝
        out = svc.get_post_trade_review(did)
        self.assertEqual(out["status"], OUTCOME_NOT_REVEALED)
        self.assertNotIn("actual_da", out)
        # Service：Lock 前 reveal 被拒（NOT_LOCKED，不穿越）
        locked = svc.reveal_decision(did)
        self.assertEqual(locked["status"], "NOT_LOCKED")
        self.assertNotIn("actual_da", locked)
        # Service：Lock 后 reveal 成功
        svc.lock_decision(did)
        revealed = svc.reveal_decision(did)
        self.assertEqual(revealed["status"], "REVEALED")
        # Tool 6：reveal 后可读 actual_*
        out2 = svc.get_post_trade_review(did)
        self.assertEqual(out2["status"], "REVEALED")
        self.assertIsNotNone(out2.get("actual_return"))
        # ---- Web 层 ----
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        web_did = r.get_json()["decision"]["decision_id"]
        r = c.post(f"/api/decision/{web_did}/reveal")   # 未锁定 → 403 NOT_LOCKED
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["status"], "NOT_LOCKED")
        self.assertNotIn("actual_da", json.dumps(r.get_json(), ensure_ascii=False))
        r = c.post(f"/api/decision/{web_did}/lock")
        self.assertEqual(r.status_code, 200)
        r = c.post(f"/api/decision/{web_did}/reveal")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "REVEALED")

    # ============================================================= H13
    def test_h13_web_cli_tool_same_decision_snapshot(self):
        """Web / CLI / Tool 同一个 DecisionSnapshot：同一 decision_id，final / 数值一致。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        web = r.get_json()["decision"]
        web_did, web_final = web["decision_id"], web["final_recommendation"]
        web_er = web["model_output"]["expected_return"]
        # Tool get_decision：同一 service 实例上同一参数 → 复用同一 decision_id（不重算）
        svc = mvp_web._SERVICES["offline"]  # noqa: SLF001
        tool = svc.get_decision(DD, NODE, HOUR)
        self.assertEqual(tool["decision_id"], web_did,
                         "Web 与 Tool 应消费同一个 DecisionSnapshot（同一 decision_id）")
        self.assertEqual(tool["final_recommendation"], web_final)
        self.assertEqual(tool["model_output"]["expected_return"], web_er)
        # 存储的快照对象（DecisionSnapshot 的 as_jsonable 结果）
        stored = svc._decisions[web_did]  # noqa: SLF001
        self.assertEqual(stored["final_recommendation"], web_final)
        self.assertEqual(stored["context"]["decision_date"], DD)
        self.assertEqual(stored["context"]["node"], NODE)
        # CLI 决策链（mvp_demo Section 6/7）与 Web 最终建议一致
        cli = _cli_decision(svc, DD, NODE, HOUR)
        self.assertEqual(cli["final"], web_final,
                         "CLI 与 Web 最终建议应一致（同一个 DecisionSnapshot 语义）")
        self.assertEqual(cli["expected_return"], web_er)

    # ============================================================= H14
    def test_h14_missing_data_error_readable(self):
        """Missing data 错误可读（非 traceback）：Web 结构化错误 + Service 可读 ValueError。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        # 超出数据范围 → UNSUPPORTED_DATE，结构化 error，绝无 traceback
        r = c.post("/api/decision", json={"decision_date": "2025-01-01",
                                          "node": NODE, "hour": HOUR})
        self.assertEqual(r.status_code, 400)
        body = r.get_data(as_text=True)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("File \"", body)
        j = r.get_json()
        self.assertEqual(j["status"], "error")
        self.assertEqual(j["error"]["code"], "UNSUPPORTED_DATE")
        self.assertTrue(j["error"]["message"])
        self.assertTrue(j["error"]["suggested_action"])
        # 核心 artifact 缺失 → MISSING_ARTIFACT（结构化，非 traceback）
        with mock.patch.object(mvp_web, "service", side_effect=FileNotFoundError(
                "code/data/canonical.parquet")):
            r = c.post("/api/decision", json={"decision_date": DD,
                                              "node": NODE, "hour": HOUR})
        self.assertEqual(r.status_code, 500)
        body = r.get_data(as_text=True)
        self.assertNotIn("Traceback", body)
        j = r.get_json()
        self.assertEqual(j["status"], "error")
        self.assertEqual(j["error"]["code"], "MISSING_ARTIFACT")
        self.assertTrue(j["error"]["suggested_action"])
        # Service 层：缺行 → 可读 ValueError（中文业务口径，非 traceback）
        svc = make_svc()
        with self.assertRaises(ValueError) as ctx:
            svc.run_decision("2020-01-01", NODE, HOUR)
        msg = str(ctx.exception)
        self.assertIn("canonical 无", msg)
        self.assertNotIn("Traceback", msg)

    # ============================================================= H15
    def test_h15_web_core_works_without_llm(self):
        """无 LLM 时 Web 核心功能正常：决策 / Lock / Reveal 照常，Ask 诚实降级。"""
        import code.llm_copilot as _lc  # noqa: PLC0415
        with mock.patch.object(_lc, "_load_env_file", lambda: None), \
                mock.patch.dict(os.environ, {"LLM_API_KEY": "", "LLM_PROVIDER": "",
                                             "LLM_MODEL": "", "LLM_BASE_URL": ""},
                                clear=False):
            _lc._default_copilot = None  # noqa: SLF001 - 强制无 LLM 状态
            import mvp_web  # noqa: PLC0415
            c = mvp_web.app.test_client()
            # 核心决策流程照常
            r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                              "hour": HOUR, "evidence": "offline"})
            self.assertEqual(r.status_code, 200)
            dec = r.get_json()["decision"]
            self.assertIn(dec["final_recommendation"], ("BUY_DA", "SELL_DA", "NO_TRADE"))
            did = dec["decision_id"]
            # LOCK → REVEAL 完整生命周期可用
            self.assertEqual(c.post(f"/api/decision/{did}/lock").status_code, 200)
            self.assertEqual(c.post(f"/api/decision/{did}/reveal").status_code, 200)
            # Ask 诚实降级（LLM NOT CONFIGURED + 结构化 ERROR CODE），不影响交易流程
            r2 = c.post("/api/ask", json={"question": "为什么不卖", "decision_id": did})
            self.assertEqual(r2.status_code, 200)
            j = r2.get_json()
            self.assertEqual(j["status"], "degraded")
            self.assertTrue(j["degraded"])
            self.assertIn("LLM NOT CONFIGURED", j["answer"])
            self.assertEqual(j["error"]["code"], "LLM_UNAVAILABLE")
            # MVP Status 诚实标注 LLM=NOT CONFIGURED
            meta = c.get("/api/meta").get_json()
            self.assertEqual(meta["mvp_status"]["llm"], "NOT CONFIGURED")


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(V0311HardeningTests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("V0.3.1.1 Demo Hardening 验收测试报告（15 项）")
    print(f"TOTAL   : {n_total}")
    print(f"PASSED  : {n_passed}")
    print(f"FAILED  : {n_failed}")
    print(f"SKIPPED : {n_skipped}")
    if result.failures or result.errors:
        print("-" * 64)
        for t, tb in result.failures + result.errors:
            print(f"  FAILED  {t.id()}")
            print(f"          {tb.splitlines()[-1]}")
    print("=" * 64)
    return 1 if n_failed else 0


if __name__ == "__main__":
    sys.exit(main())
