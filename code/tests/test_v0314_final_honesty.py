# -*- coding: utf-8 -*-
"""
code/tests/test_v0314_final_honesty.py —— V0.3.1.4 Final Honesty Patch 15 项验收（Agent D）
============================================================================================

覆盖（需求十四 · 15 项）：
  H1  initialization_time > decision_cutoff → not eligible（Strong Impossibility）
  H2  reason = INITIALIZATION_AFTER_CUTOFF
  H3  available_at UNKNOWN 不会被伪造成 init + delay（快照与 adapter 均保持空）
  H4  available_at unknown（无 init 判定）→ AVAILABILITY_NOT_PROVEN
  H5  published_at 不参与 fallback（published<=cutoff 但 available 缺失 → 不 eligible）
  H6  CLI 不用 published_at 冒充 available_at
  H7  Web 不用 published_at 冒充 available_at
  H8  LLM Tool 不用 published_at 冒充 available_at
  H9  HistoricalSnapshotEvidenceAdapter 不计算 fake available_at
  H10 build_evidence_demo 不写 init + 6h
  H11 Case E contains_mock = false（真实历史，非 MOCK）
  H12 Case E Final Recommendation 不变（Before == After Patch）
  H13 Evidence Artifact canonical hash PASS
  H14 backtest metadata 2026-05-01+ 不再标 PRE（POST_DAME_EDAM_2026）
  H15 所有 Golden Cases 交易结果不变（final / gate）

全部测试离线、确定性：决策用 StaticEvidenceAdapter([]) 或 HistoricalSnapshotEvidenceAdapter（本地文件，不联网）。
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
from data_mode import resolve_data_mode  # noqa: E402

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
CUTOFF = "2026-07-08T17:00:00"
INIT_LATE = "2026-07-08T18:00:00"     # > cutoff
PRE = "2026-07-08T09:00:00"

DEMO_DIR = REPO_ROOT / "demo_artifacts"
EVIDENCE_DEMO = DEMO_DIR / "evidence_demo.json"


def make_full_svc() -> DecisionService:
    return DecisionService(evidence_adapter=StaticEvidenceAdapter([]))


def make_demo_svc() -> DecisionService:
    return DecisionService(data_dir=DEMO_DIR, evidence_adapter=HistoricalSnapshotEvidenceAdapter())


class V0314FinalHonestyTests(unittest.TestCase):
    """V0.3.1.4 Final Honesty Patch 15 项验收。"""

    @classmethod
    def setUpClass(cls):
        info = resolve_data_mode()
        if not info.is_ready:
            raise unittest.SkipTest("数据不可用（mode=%s）：请先运行 prepare_mvp.py" % info.mode)
        if not EVIDENCE_DEMO.exists():
            raise unittest.SkipTest("缺少 demo_artifacts/evidence_demo.json：先运行 python build_evidence_demo.py")

    # ============================================================= H1 / H2
    def test_h1_h2_init_after_cutoff_reject(self):
        """initialization_time > decision_cutoff → not eligible，reason=INITIALIZATION_AFTER_CUTOFF。"""
        from agent.evidence.schema import evidence_from_dict  # noqa: PLC0415
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        # 即使 available_at<=cutoff，只要 init>cutoff → Strong Impossibility 提前拒绝
        ev = {"evidence_id": "EV-INIT-LATE", "initialization_time": INIT_LATE,
              "available_at": PRE, "decision_cutoff": CUTOFF, "is_mock": False}
        obj = evidence_from_dict(ev)
        self.assertFalse(obj.time_eligible, "init>cutoff 应提前判不可用")
        elig, post = split_eligible([ev], CUTOFF)
        self.assertEqual(len(elig), 0)
        self.assertEqual([e.get("evidence_id") for e in post], ["EV-INIT-LATE"])
        # 服务层 reason
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([ev]))
        d = svc.run_decision(DD, NODE, HOUR)
        rej = {r["evidence_id"]: r for r in d["evidence"]["rejected"]}
        self.assertIn("EV-INIT-LATE", rej)
        self.assertIn("INITIALIZATION_AFTER_CUTOFF", rej["EV-INIT-LATE"]["rejection_reason"])

    # ============================================================= H3
    def test_h3_available_at_not_faked_as_init_plus_delay(self):
        """available_at UNKNOWN 不会被伪造成 init+delay：快照与 adapter 均保持空。"""
        doc = json.loads(EVIDENCE_DEMO.read_text(encoding="utf-8"))
        rec = doc["records"][0]
        self.assertEqual(rec["available_at"], "")
        self.assertEqual(rec["available_at_source"], "NOT_PROVEN")
        self.assertFalse(rec.get("availability_proven"))
        # adapter 注入后 available_at 仍为空（不补 init+delay）
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        e = d["evidence"]["rejected"][0]
        self.assertEqual(e["available_at"], "")
        self.assertFalse(e.get("availability_proven"))
        self.assertTrue(e["initialization_time"])

    # ============================================================= H4
    def test_h4_available_at_unknown_availability_not_proven(self):
        """available_at unknown（无 init 判定）→ AVAILABILITY_NOT_PROVEN（普通情况）。"""
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        ev = {"evidence_id": "EV-UNKNOWN", "published_at": "", "available_at": "",
              "available_at_source": "NOT_PROVEN", "decision_cutoff": CUTOFF, "is_mock": False}
        elig, post = split_eligible([ev], CUTOFF)
        self.assertEqual(len(elig), 0)
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([ev]))
        d = svc.run_decision(DD, NODE, HOUR)
        rej = {r["evidence_id"]: r for r in d["evidence"]["rejected"]}
        self.assertIn("AVAILABILITY_NOT_PROVEN", rej["EV-UNKNOWN"]["rejection_reason"])

    # ============================================================= H5
    def test_h5_published_at_not_fallback(self):
        """published_at<=cutoff 但 available_at 缺失 → 仍不 eligible（published 不参与 fallback）。"""
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        ev = {"evidence_id": "EV-PUB-ONLY", "published_at": PRE, "available_at": "",
              "available_at_source": "NOT_PROVEN", "decision_cutoff": CUTOFF, "is_mock": False}
        elig, post = split_eligible([ev], CUTOFF)
        self.assertEqual(len(elig), 0, "published_at 不得 fallback 成可用")
        self.assertEqual([e.get("evidence_id") for e in post], ["EV-PUB-ONLY"])

    # ============================================================= H6 / H7 / H8
    def test_h6_h7_h8_no_published_fallback_in_display(self):
        """CLI / Web / LLM Tool 均不用 published_at 冒充 available_at。"""
        # CLI：mvp_demo.py 不得含 available_at or published_at 回退
        cli = (REPO_ROOT / "mvp_demo.py").read_text(encoding="utf-8")
        self.assertNotIn("r.get('available_at') or r.get('published_at')", cli)
        self.assertNotIn("available_at or published_at", cli)
        # Web：mvp_web.py 审计只判 available_at；模板 S4 用 UNKNOWN/NOT PROVEN
        web = (REPO_ROOT / "mvp_web.py").read_text(encoding="utf-8")
        self.assertNotIn("e.get(\"available_at\") or e.get(\"published_at\")", web)
        html = "\n".join([
            (REPO_ROOT / "templates" / "mvp_index.html").read_text(encoding="utf-8"),
            (REPO_ROOT / "static" / "mvp_core.js").read_text(encoding="utf-8"),
            (REPO_ROOT / "static" / "mvp_agent.js").read_text(encoding="utf-8"),
            (REPO_ROOT / "static" / "mvp_evidence.js").read_text(encoding="utf-8"),
        ])
        self.assertNotIn("available_at || e.published_at", html)
        self.assertIn("UNKNOWN / NOT PROVEN", html)
        # LLM Tool：get_evidence 返回的 available_at 与 snapshot 一致（不 fallback）
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        did = d["decision_id"]
        tool = svc.get_evidence(did)
        t_rej = tool["rejected"][0]
        self.assertEqual(t_rej["available_at"], "")     # Tool 不把 published 显示成 available
        self.assertIn("INITIALIZATION_AFTER_CUTOFF", t_rej["rejection_reason"])

    # ============================================================= H9
    def test_h9_adapter_no_fake_available_at(self):
        """HistoricalSnapshotEvidenceAdapter 只映射，不计算 fake available_at（init+delay）。"""
        # 源码不包含"推算可用时刻"逻辑
        src = (REPO_ROOT / "code" / "decision_service.py").read_text(encoding="utf-8")
        self.assertNotIn("Timedelta(hours=6)", src)
        self.assertNotIn("init + 6", src)
        self.assertNotIn("initialization_time + pd.Timedelta", src)
        # 行为：adapter 输出 available_at 为空
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        self.assertEqual(d["evidence"]["rejected"][0]["available_at"], "")

    # ============================================================= H10
    def test_h10_build_evidence_demo_no_init_plus_6h(self):
        """build_evidence_demo 不写 init+6h（不推算可用时刻）。"""
        src = (REPO_ROOT / "build_evidence_demo.py").read_text(encoding="utf-8")
        self.assertNotIn("Timedelta(hours=6)", src)
        self.assertNotIn("+ 6h", src)
        self.assertIn("INITIALIZATION_AFTER_CUTOFF", src)
        # 生成的快照 available_at 为空
        doc = json.loads(EVIDENCE_DEMO.read_text(encoding="utf-8"))
        self.assertEqual(doc["records"][0]["available_at"], "")
        self.assertFalse(doc.get("available_at_proven"))

    # ============================================================= H11
    def test_h11_case_e_contains_mock_false(self):
        """Case E contains_mock=false（真实历史 GFS，非 MOCK）。"""
        doc = json.loads(EVIDENCE_DEMO.read_text(encoding="utf-8"))
        self.assertFalse(doc.get("contains_mock"))
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        for e in d["evidence"]["eligible"] + d["evidence"]["rejected"]:
            self.assertFalse(e.get("is_mock"))

    # ============================================================= H12
    def test_h12_case_e_final_unchanged(self):
        """Case E Final Recommendation 不变（Before == After Patch）。"""
        before = make_full_svc().run_decision(DD, NODE, HOUR)      # 无证据基线
        after = make_demo_svc().run_decision(DD, NODE, HOUR)       # 注入 18Z 快照
        self.assertEqual(after["final_recommendation"], before["final_recommendation"])
        self.assertEqual(after["risk_gate"]["decision"], before["risk_gate"]["decision"])
        self.assertEqual(after["model_output"]["expected_return"],
                         before["model_output"]["expected_return"])
        self.assertEqual(after["final_recommendation"], "NO_TRADE")  # golden 不变

    # ============================================================= H13
    def test_h13_evidence_artifact_canonical_hash_pass(self):
        """Evidence Artifact canonical hash PASS：内容哈希自洽 + manifest 登记。"""
        doc = json.loads(EVIDENCE_DEMO.read_text(encoding="utf-8"))
        subset = {k: v for k, v in doc.items() if k != "artifact_hash"}
        data = json.dumps(subset, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        expect = __import__("hashlib").sha256(data.replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(doc["artifact_hash"], expect)
        m = json.loads((DEMO_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["hashes"]["evidence_demo.json"], canonical_sha256(EVIDENCE_DEMO))
        snap = m.get("evidence_snapshot") or {}
        self.assertTrue(snap.get("historical_snapshot"))
        self.assertFalse(snap.get("contains_mock"))
        self.assertFalse(snap.get("available_at_proven"))   # V0.3.1.4：不伪造可用时刻

    # ============================================================= H14
    def test_h14_backtest_metadata_not_pre(self):
        """backtest metadata 2026-05-01+ 不再标 PRE（POST_DAME_EDAM_2026）。"""
        from code.backtest_v2 import _window_market_rule_version  # noqa: PLC0415
        meta = _window_market_rule_version()
        self.assertEqual(meta["market_rule_version"], "POST_DAME_EDAM_2026")

    # ============================================================= H15
    def test_h15_all_golden_cases_unchanged(self):
        """所有 Golden Cases 交易结果不变（final / gate）。"""
        svc = make_full_svc()
        for case in GOLDEN_CASES:
            with self.subTest(case=case["id"]):
                d = svc.run_decision(case["decision_date"], case["node"], case["hour"])
                self.assertEqual(d["final_recommendation"], case["final"], case["id"])
                self.assertEqual(d["risk_gate"]["decision"], case["gate"], case["id"])


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(V0314FinalHonestyTests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("V0.3.1.4 Final Honesty Patch 验收测试（15 项验收 · 12 个测试方法）")
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
