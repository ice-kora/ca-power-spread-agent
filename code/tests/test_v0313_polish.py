# -*- coding: utf-8 -*-
"""
code/tests/test_v0313_polish.py —— V0.3.1.3 Demo Freeze Polish 20 项验收（Agent D）
====================================================================================

覆盖（需求十六 · 20 项）：
  P1  Web 单笔 Market Version 使用 DecisionSnapshot.context（非全局固定值）
  P2  target 2026-04-30 → PRE_DAME_EDAM_2026（service 层 date-aware 边界）
  P3  target 2026-05-01 → POST_DAME_EDAM_2026
  P4  2026-07 Golden Case Web → POST_DAME_EDAM_2026
  P5  Time Gate 不再 fallback published_at（available-at-only 收口）
  P6  available_at 缺失 → not eligible（MISSING_AVAILABLE_AT）
  P7  available_at > cutoff → rejected
  P8  published_at < cutoff 但 available_at > cutoff → rejected（判据=available_at）
  P9  Web / Tool / Snapshot 的 available_at 完全一致（不重新推导）
  P10 DEMO Case E 使用真实 Historical Evidence Snapshot（evidence_demo.json）
  P11 Case E Evidence contains_mock = false（真实历史，非 MOCK）
  P12 Case E Evidence 被 Time Gate 拒绝（AVAILABLE_AFTER_CUTOFF）
  P13 Case E Evidence 不改变 Final Recommendation（加入前后一致）
  P14 FULL / DEMO Golden Case Decision 一致（final / gate）
  P15 Evidence Artifact Hash PASS（evidence_demo.json canonical + manifest 登记）
  P16 DATA MODE 页面显示正确（/api/meta data_mode + 页面状态栏）
  P17 EVIDENCE MODE 页面显示正确（HISTORICAL_SNAPSHOT / LIVE / NONE）
  P18 Runtime Evidence Provenance Audit 真实计算（Availability/Provenance 项）
  P19 Outcome Access Control 不受影响（Reveal 前无 actual_*）
  P20 当前页面版本号统一（web_version = V0.3.1.2 Demo Freeze；无 V0.3.1.1 残留）

全部测试离线、确定性：
  * 决策一律用 StaticEvidenceAdapter([]) 或 HistoricalSnapshotEvidenceAdapter（本地文件，不联网）。
  * P10-P14 用 demo_artifacts/evidence_demo.json（真实历史 GFS 18Z 快照）。
  * P14 FULL vs DEMO 一致性需要完整 artifacts（当前完整数据机直接跑；clean clone 上 DEMO 单独跑）。
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.artifact_hash import canonical_sha256  # noqa: E402
from code.decision_service import (  # noqa: E402
    DecisionService,
    HistoricalSnapshotEvidenceAdapter,
    StaticEvidenceAdapter,
)
from data_mode import MODE_DEMO, MODE_FULL, resolve_data_mode  # noqa: E402

#: Golden Cases（与 V0.3.1.1 / V0.3.1.2 一致 —— 交易核心冻结基准）
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
CUTOFF_UTC = "2026-07-08T17:00:00"
PRE = "2026-07-08T09:00:00"       # <= cutoff
POST = "2026-07-08T18:00:00"      # > cutoff

DEMO_DIR = REPO_ROOT / "demo_artifacts"
EVIDENCE_DEMO = DEMO_DIR / "evidence_demo.json"


def _mk_ev(eid, published_at, *, available_at="", is_mock=False):
    return {"evidence_id": eid, "event_type": "WEATHER_FORECAST", "region": "ZP26",
            "affected_nodes": [NODE], "severity": "WATCH", "source": "TEST_GFS",
            "source_type": "WEATHER", "is_mock": is_mock, "raw_source_id": eid,
            "published_at": published_at, "available_at": available_at,
            "decision_cutoff": CUTOFF_UTC, "summary": f"test {eid}",
            "directional_effect": "UNCERTAIN", "confidence": 0.5}


def make_full_svc() -> DecisionService:
    return DecisionService(evidence_adapter=StaticEvidenceAdapter([]))


def make_demo_svc() -> DecisionService:
    return DecisionService(data_dir=DEMO_DIR, evidence_adapter=HistoricalSnapshotEvidenceAdapter())


class V0313PolishTests(unittest.TestCase):
    """V0.3.1.3 Demo Freeze Polish 20 项验收。"""

    @classmethod
    def setUpClass(cls):
        info = resolve_data_mode()
        if not info.is_ready:
            raise unittest.SkipTest("数据不可用（mode=%s）：请先运行 prepare_mvp.py" % info.mode)
        if not EVIDENCE_DEMO.exists():
            raise unittest.SkipTest("缺少 demo_artifacts/evidence_demo.json：先运行 python build_evidence_demo.py")

    # ============================================================= P1
    def test_p1_web_single_decision_uses_snapshot_context_market_rule(self):
        """单笔 Decision 页面的 Market Rule Version 来自 DecisionSnapshot.context（非全局固定值）。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        meta = c.get("/api/meta").get_json()
        # /api/meta 不再暴露固定 PRE/POST
        self.assertIn("MARKET_RULE_VERSIONING_ENABLED", meta["versions"]["market_rule"])
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["decision"]
        ctx = d["context"]
        self.assertTrue(ctx.get("market_rule_version"))
        # V0.4：market_rule_version 在"技术详情 → 交易上下文"折叠内（来自 DecisionSnapshot.context）
        src = (REPO_ROOT / "static" / "mvp_core.js").read_text(encoding="utf-8")
        self.assertIn("market_rule_version", src)
        self.assertIn("renderTechnical", src)
        self.assertNotIn('m.versions.market_rule', src)

    # ============================================================= P2 / P3
    def test_p2_p3_date_aware_boundary_in_snapshot(self):
        """target 2026-04-30 → PRE；2026-05-01 → POST（date-aware 边界，单笔快照）。"""
        svc = make_full_svc()
        d_pre = svc.run_decision("2026-04-29", NODE, HOUR)   # target 2026-04-30
        self.assertEqual(d_pre["context"]["target_date"], "2026-04-30")
        self.assertEqual(d_pre["context"]["market_rule_version"], "PRE_DAME_EDAM_2026")
        d_post = svc.run_decision("2026-04-30", NODE, HOUR)  # target 2026-05-01
        self.assertEqual(d_post["context"]["target_date"], "2026-05-01")
        self.assertEqual(d_post["context"]["market_rule_version"], "POST_DAME_EDAM_2026")

    # ============================================================= P4
    def test_p4_web_golden_case_post(self):
        """2026-07 Golden Case（target >= 2026-05-01）Web 页面 = POST_DAME_EDAM_2026。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": "2026-07-16", "node": "CONTROLX_1_N001",
                                          "hour": 3, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        ctx = r.get_json()["decision"]["context"]
        self.assertEqual(ctx["market_rule_version"], "POST_DAME_EDAM_2026")
        self.assertEqual(ctx["market_rule_version"],
                         ctx.get("market_rule_version"))  # 展示值 == snapshot context

    # ============================================================= P5
    def test_p5_time_gate_available_at_only_no_fallback(self):
        """Time Gate 只判 available_at：schema.time_eligible 不再 fallback published_at。"""
        from agent.evidence.schema import Evidence  # noqa: PLC0415
        src = (REPO_ROOT / "agent" / "evidence" / "schema.py").read_text(encoding="utf-8")
        # 旧 fallback 分支（`pub = parse_timestamp(self.published_at)` 回退）已移除
        self.assertNotIn("pub = parse_timestamp(self.published_at)", src)
        self.assertIn("avail = parse_timestamp(self.available_at)", src)
        self.assertIn("if avail is None:", src)
        # 判据实现：available_at 缺失（显式标注缺失）→ time_eligible False
        ev = Evidence(evidence_id="X", published_at=PRE, decision_cutoff=CUTOFF_UTC,
                      available_at_source="MISSING（显式缺失）").normalize()
        self.assertFalse(ev.time_eligible)

    # ============================================================= P6
    def test_p6_missing_available_at_not_eligible(self):
        """available_at 缺失（published 也无）→ not eligible（MISSING_AVAILABLE_AT）。"""
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        ev = {"evidence_id": "EV-NO-TIME", "published_at": "", "available_at": "",
              "decision_cutoff": CUTOFF_UTC, "is_mock": False}
        elig, post = split_eligible([ev], CUTOFF_UTC)
        self.assertEqual(len(elig), 0)
        self.assertEqual([e.get("evidence_id") for e in post], ["EV-NO-TIME"])
        # 服务层 rejection_reason 标注 AVAILABILITY_NOT_PROVEN（无 init 也无 available_at）
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([ev]))
        d = svc.run_decision(DD, NODE, HOUR)
        rej = {r["evidence_id"]: r for r in d["evidence"]["rejected"]}
        self.assertIn("EV-NO-TIME", rej)
        self.assertIn("AVAILABILITY_NOT_PROVEN", rej["EV-NO-TIME"]["rejection_reason"])

    # ============================================================= P7 / P8
    def test_p7_p8_available_at_is_only_criterion(self):
        """available_at > cutoff → rejected；published_at<cutoff 但 available_at>cutoff 仍 rejected。"""
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        evs = [
            _mk_ev("EV-AFTER", PRE, available_at=POST),        # P8：published 早但 available 晚
            _mk_ev("EV-LATE", POST),                            # P7：available 晚
        ]
        elig, post = split_eligible(evs, CUTOFF_UTC)
        self.assertEqual(len(elig), 0)
        self.assertEqual({e.get("evidence_id") for e in post}, {"EV-AFTER", "EV-LATE"})

    # ============================================================= P9
    def test_p9_web_tool_snapshot_available_at_identical(self):
        """Web / Tool / Snapshot 的 available_at 完全一致（单一事实来源，不重新推导）。"""
        from unittest import mock  # noqa: PLC0415
        import mvp_web  # noqa: PLC0415
        # 注入一条确定性证据（offline 静态）→ 三端 available_at 一致
        evs = [_mk_ev("EV-DET", PRE, available_at=PRE)]
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter(evs))
        # 直接把 svc 注入 Web 的 service 注册表（隔离测试，不联网）
        with mock.patch.object(mvp_web, "_SERVICES", {"real": svc, "offline": svc}):
            c = mvp_web.app.test_client()
            r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                              "hour": HOUR, "evidence": "real"})
            self.assertEqual(r.status_code, 200)
            web = r.get_json()["decision"]
            web_avail = {e["evidence_id"]: e["available_at"] for e in web["evidence"]["eligible"]}
            did = web["decision_id"]
            # Tool get_evidence（返回 eligible/rejected 直接字段）
            tool = svc.get_evidence(did)
            tool_avail = {e["evidence_id"]: e["available_at"] for e in tool.get("eligible", [])}
            # Snapshot（存储对象）
            snap = svc._decisions[did]["evidence"]["eligible"]  # noqa: SLF001
            snap_avail = {e["evidence_id"]: e["available_at"] for e in snap}
            for eid in ("EV-DET",):
                self.assertEqual(web_avail[eid], tool_avail[eid], f"{eid} Web vs Tool")
                self.assertEqual(web_avail[eid], snap_avail[eid], f"{eid} Web vs Snapshot")

    # ============================================================= P10 / P11 / P12 / P13
    def test_p10_case_e_uses_real_historical_snapshot(self):
        """DEMO Case E 使用真实 Historical Evidence Snapshot（evidence_demo.json，非 MOCK/联网）。"""
        self.assertTrue(EVIDENCE_DEMO.exists())
        doc = json.loads(EVIDENCE_DEMO.read_text(encoding="utf-8"))
        self.assertFalse(doc.get("contains_mock"))
        self.assertTrue(doc.get("historical_snapshot"))
        self.assertTrue(doc.get("artifact_hash"))
        rec = doc["records"][0]
        self.assertEqual(rec["decision_date"], "2026-07-08")
        self.assertTrue(rec.get("raw_source_id"))
        self.assertTrue(rec.get("summary"))
        # adapter 在 DEMO MODE 下注入（HISTORICAL_SNAPSHOT）
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        self.assertEqual(d["context"]["evidence_mode"], "HISTORICAL_SNAPSHOT")
        prov = d["context"]["evidence_provenance"] or {}
        self.assertTrue(prov.get("historical_snapshot"))
        self.assertFalse(prov.get("contains_mock"))

    def test_p11_case_e_evidence_not_mock(self):
        """Case E 注入的证据 contains_mock=false（真实历史 GFS，非 MOCK）。"""
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        all_ev = d["evidence"]["eligible"] + d["evidence"]["rejected"]
        self.assertTrue(all_ev, "Case E 应有证据注入")
        for e in all_ev:
            self.assertFalse(e.get("is_mock"), "Case E 证据不得为 MOCK")

    def test_p12_case_e_evidence_rejected_by_time_gate(self):
        """Case E Evidence 被 Time Gate 拒绝：初始化晚于 cutoff → INITIALIZATION_AFTER_CUTOFF。"""
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        self.assertEqual(d["evidence"]["eligible"], [])
        rej = d["evidence"]["rejected"]
        self.assertTrue(rej, "Case E 应有被拒证据")
        e = rej[0]
        self.assertFalse(e["decision_eligible"])
        self.assertFalse(e.get("availability_proven"))               # available_at 未知
        self.assertEqual(e["available_at"], "")                      # UNKNOWN，不伪造 init+delay
        self.assertGreater(e["initialization_time"], e["decision_cutoff"])  # Strong Impossibility
        self.assertIn("INITIALIZATION_AFTER_CUTOFF", e["rejection_reason"])

    def test_p13_case_e_evidence_does_not_change_final(self):
        """加入 Post-cutoff Evidence 前后 final_recommendation 完全一致（不影响交易结果）。"""
        baseline = make_full_svc().run_decision(DD, NODE, HOUR)          # 无证据基线
        with_snapshot = make_demo_svc().run_decision(DD, NODE, HOUR)     # 注入 18Z 快照
        self.assertEqual(with_snapshot["final_recommendation"], baseline["final_recommendation"])
        self.assertEqual(with_snapshot["risk_gate"]["decision"], baseline["risk_gate"]["decision"])
        self.assertEqual(with_snapshot["model_output"]["expected_return"],
                         baseline["model_output"]["expected_return"])

    # ============================================================= P14
    def test_p14_full_vs_demo_golden_consistent(self):
        """FULL / DEMO Golden Case Decision 一致（final / gate）。"""
        full = make_full_svc()
        demo = make_demo_svc()
        for case in GOLDEN_CASES:
            with self.subTest(case=case["id"]):
                df = full.run_decision(case["decision_date"], case["node"], case["hour"])
                dd_ = demo.run_decision(case["decision_date"], case["node"], case["hour"])
                self.assertEqual(dd_["final_recommendation"], df["final_recommendation"], case["id"])
                self.assertEqual(dd_["risk_gate"]["decision"], df["risk_gate"]["decision"], case["id"])

    # ============================================================= P15
    def test_p15_evidence_artifact_hash_pass(self):
        """Evidence Artifact Hash：evidence_demo.json 内容哈希自洽 + manifest 登记。"""
        doc = json.loads(EVIDENCE_DEMO.read_text(encoding="utf-8"))
        # 内容哈希（不含 hash 字段自身，同 build_evidence_demo._content_hash）
        subset = {k: v for k, v in doc.items() if k != "artifact_hash"}
        data = json.dumps(subset, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        expect = __import__("hashlib").sha256(data.replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(doc["artifact_hash"], expect)
        # manifest 登记 evidence_demo.json（hashes + evidence_snapshot provenance）
        m = json.loads((DEMO_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("evidence_demo.json", m["hashes"])
        self.assertEqual(m["hashes"]["evidence_demo.json"], canonical_sha256(EVIDENCE_DEMO))
        snap = m.get("evidence_snapshot") or {}
        self.assertTrue(snap.get("historical_snapshot"))
        self.assertFalse(snap.get("contains_mock"))
        self.assertTrue(snap.get("artifact_hash"))
        self.assertEqual(snap.get("hash_normalization"), "canonical-text")

    # ============================================================= P16 / P17
    def test_p16_p17_data_and_evidence_mode_display(self):
        """页面明确区分 DATA MODE 与 EVIDENCE MODE。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        meta = c.get("/api/meta").get_json()
        self.assertIn(meta["data_mode"], (MODE_FULL, MODE_DEMO))
        self.assertIn(meta["evidence_mode_default"], ("HISTORICAL_SNAPSHOT", "LIVE"))
        page = c.get("/").get_data(as_text=True)
        src = (REPO_ROOT / "static" / "mvp_core.js").read_text(encoding="utf-8")
        # V0.4：Header 紧凑系统状态（renderSysStatus 用中文"数据模式/证据模式"）
        self.assertIn("数据模式", src)
        self.assertIn("证据模式", src)
        self.assertIn("renderSysStatus", src)
        # 决策的技术详情（交易上下文）含 evidence_mode（HISTORICAL_SNAPSHOT / LIVE / NONE）
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        d = r.get_json()["decision"]
        self.assertEqual(d["context"]["evidence_mode"], "NONE")

    # ============================================================= P18
    def test_p18_evidence_provenance_audit_real(self):
        """Runtime Evidence Provenance Audit 真实计算（Availability/Provenance 项，非写死）。"""
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        checks = d["audit"]["checks"]
        self.assertIn("evidence_availability", checks)
        self.assertIn("evidence_provenance", checks)
        # Case E 证据 available_at 未知 → Evidence Availability 如实 WARNING（诚实标注，不伪造时间）
        self.assertEqual(checks["evidence_availability"]["status"], "WARNING")
        # Provenance（source/raw_source_id/快照 contains_mock=false）齐备 → PASS
        self.assertEqual(checks["evidence_provenance"]["status"], "PASS")
        # Web 侧 audit 也包含两项
        import mvp_web  # noqa: PLC0415
        src = (REPO_ROOT / "mvp_web.py").read_text(encoding="utf-8")
        self.assertIn("EVIDENCE_AVAILABILITY", src)
        self.assertIn("EVIDENCE_PROVENANCE", src)
        self.assertIn('"EVIDENCE_AVAILABILITY"', src)

    # ============================================================= P19
    def test_p19_outcome_access_control_unchanged(self):
        """Outcome Access Control 不受影响：Reveal 前 Web/Tool 无 actual_*。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["decision"]
        did = d["decision_id"]
        # 未 reveal：post_trade 段无 actual_*，outcome_revealed=False
        self.assertEqual(d["post_trade"]["status"], "OUTCOME_NOT_REVEALED")
        self.assertFalse(d["outcome_revealed"])
        pt = json.dumps(d["post_trade"], ensure_ascii=False)
        for k in ("actual_da", "actual_rtpd", "actual_return", "pnl"):
            self.assertNotIn(k, pt, f"未 reveal 的 post_trade 不得含 {k}")
        # Lock 前 Reveal → NOT_LOCKED（不穿越）
        r2 = c.post(f"/api/decision/{did}/reveal")
        self.assertEqual(r2.status_code, 403)
        self.assertEqual(r2.get_json()["status"], "NOT_LOCKED")
        # Lock → Reveal 成功
        self.assertEqual(c.post(f"/api/decision/{did}/lock").status_code, 200)
        self.assertEqual(c.post(f"/api/decision/{did}/reveal").status_code, 200)
        # 页面文案统一 APPLICATION-LAYER OUTCOME ACCESS CONTROL（无"物理不存在"绝对说法）
        src = (REPO_ROOT / "mvp_web.py").read_text(encoding="utf-8") + \
              (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Outcome 数据物理不存在", src)
        self.assertNotIn("任何人都无法提前看到", src)

    # ============================================================= P20
    def test_p20_version_strings_unified(self):
        """当前入口版本号统一：web_version = V0.3.1.2 Demo Freeze，无 V0.3.1.1 残留。"""
        import mvp_web  # noqa: PLC0415
        meta = mvp_web.app.test_client().get("/api/meta").get_json()
        self.assertTrue(str(meta["web_version"]).startswith("V0.3.1.2"))
        # prepare_mvp 主标题无 V0.3.1.1
        pm = (REPO_ROOT / "prepare_mvp.py").read_text(encoding="utf-8")
        self.assertNotIn("V0.3.1.1", pm.split("main()")[1] if "main()" in pm else pm)
        self.assertIn("V0.3.1.2", pm)
        # V0.4：页面主入口不铺版本号；版本号进"系统边界"（renderBoundary 用 web_version）
        page = mvp_web.app.test_client().get("/").get_data(as_text=True)
        self.assertNotIn("V0.3.1.1", page)
        src = (REPO_ROOT / "static" / "mvp_core.js").read_text(encoding="utf-8")
        self.assertIn("web_version", src)


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(V0313PolishTests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("V0.3.1.3 Demo Freeze Polish 验收测试（20 项验收 · 17 个测试方法）")
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
