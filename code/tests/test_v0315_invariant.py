# -*- coding: utf-8 -*-
"""
code/tests/test_v0315_invariant.py —— V0.3.1.5 Final Invariant Patch 15 项验收（Agent D）
==========================================================================================

覆盖（需求十四 · 15 项）：
  I1  Schema 不再自动 published_at → available_at（源码无迁移分支）
  I2  published_at < cutoff + available_at missing → NOT ELIGIBLE
  I3  reason = AVAILABILITY_NOT_PROVEN
  I4  initialization_time > cutoff → INITIALIZATION_AFTER_CUTOFF
  I5  Source Adapter 显式 mapping（SOURCE_PUBLISHED_AT_IS_PROVEN_AVAILABILITY）后可 eligible
  I6  Time Gate 本身不读取 published_at
  I7  DecisionService 不 fallback published_at
  I8  CLI 不 fallback published_at
  I9  Web 不 fallback published_at
  I10 LLM Tool 不 fallback published_at
  I11 Case E final recommendation 不变
  I12 Case E risk gate 不变
  I13 Case E pnl 不变
  I14 manifest 描述更新（不再说 available_at>cutoff）
  I15 Evidence Artifact canonical hash PASS

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

GOLDEN_E = {"id": "E", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001",
            "hour": 2, "final": "NO_TRADE", "gate": "REJECT"}

DD = "2026-07-08"
NODE = "CONTROLX_1_N001"
HOUR = 2
CUTOFF = "2026-07-08T17:00:00"
PRE = "2026-07-08T09:00:00"
INIT_LATE = "2026-07-08T18:00:00"

DEMO_DIR = REPO_ROOT / "demo_artifacts"
EVIDENCE_DEMO = DEMO_DIR / "evidence_demo.json"


def make_full_svc() -> DecisionService:
    return DecisionService(evidence_adapter=StaticEvidenceAdapter([]))


def make_demo_svc() -> DecisionService:
    return DecisionService(data_dir=DEMO_DIR, evidence_adapter=HistoricalSnapshotEvidenceAdapter())


class V0315InvariantTests(unittest.TestCase):
    """V0.3.1.5 Final Invariant Patch 15 项验收。"""

    @classmethod
    def setUpClass(cls):
        info = resolve_data_mode()
        if not info.is_ready:
            raise unittest.SkipTest("数据不可用（mode=%s）：请先运行 prepare_mvp.py" % info.mode)
        if not EVIDENCE_DEMO.exists():
            raise unittest.SkipTest("缺少 demo_artifacts/evidence_demo.json：先运行 python build_evidence_demo.py")

    # ============================================================= I1
    def test_i1_schema_no_auto_migration(self):
        """Schema 不再自动 published_at → available_at（源码无迁移分支）。"""
        src = (REPO_ROOT / "agent" / "evidence" / "schema.py").read_text(encoding="utf-8")
        self.assertNotIn("self.available_at = self.published_at", src)
        self.assertNotIn("published_at（显式迁移）", src)
        self.assertIn("SOURCE_PUBLISHED_AT_IS_PROVEN_AVAILABILITY", src)  # 唯一允许的显式源级映射
        # 行为：available_at 缺失保持 ""（不迁移）
        from agent.evidence.schema import evidence_from_dict  # noqa: PLC0415
        ev = evidence_from_dict({"evidence_id": "X", "published_at": PRE,
                                 "decision_cutoff": CUTOFF})
        self.assertEqual(ev.available_at, "")

    # ============================================================= I2 / I3
    def test_i2_i3_published_alone_never_eligible(self):
        """published_at < cutoff + available_at missing → NOT ELIGIBLE，reason=AVAILABILITY_NOT_PROVEN。"""
        from agent.evidence.schema import Evidence  # noqa: PLC0415
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        # 关键 regression：published=16:00<cutoff=17:00，init=15:00<cutoff，但 available 缺失
        ev = {"evidence_id": "EV-PUB-ONLY", "published_at": "2026-07-08T16:00:00",
              "available_at": "", "available_at_source": "NOT_PROVEN",
              "initialization_time": "2026-07-08T15:00:00",
              "decision_cutoff": CUTOFF, "is_mock": False}
        obj = Evidence(**{k: v for k, v in ev.items()}).normalize()
        self.assertEqual(obj.available_at, "")            # available_at is None / ""
        self.assertFalse(obj.decision_eligible)           # published<cutoff 也不能 eligible
        elig, post = split_eligible([ev], CUTOFF)
        self.assertEqual(len(elig), 0)
        # 服务层 reason
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([ev]))
        d = svc.run_decision(DD, NODE, HOUR)
        rej = {r["evidence_id"]: r for r in d["evidence"]["rejected"]}
        self.assertIn("EV-PUB-ONLY", rej)
        self.assertIn("AVAILABILITY_NOT_PROVEN", rej["EV-PUB-ONLY"]["rejection_reason"])

    # ============================================================= I4
    def test_i4_init_after_cutoff_reason(self):
        """initialization_time > cutoff → INITIALIZATION_AFTER_CUTOFF。"""
        from agent.evidence.schema import Evidence  # noqa: PLC0415
        ev = {"evidence_id": "EV-INIT-LATE", "initialization_time": INIT_LATE,
              "available_at": PRE, "decision_cutoff": CUTOFF, "is_mock": False}
        obj = Evidence(**{k: v for k, v in ev.items()}).normalize()
        self.assertFalse(obj.time_eligible)
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([ev]))
        d = svc.run_decision(DD, NODE, HOUR)
        rej = {r["evidence_id"]: r for r in d["evidence"]["rejected"]}
        self.assertIn("INITIALIZATION_AFTER_CUTOFF", rej["EV-INIT-LATE"]["rejection_reason"])

    # ============================================================= I5
    def test_i5_source_adapter_explicit_mapping_eligible(self):
        """Source Adapter 显式 mapping（SOURCE_PUBLISHED_AT_IS_PROVEN_AVAILABILITY）后可 eligible。"""
        from agent.evidence.schema import evidence_from_dict  # noqa: PLC0415
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        # Adapter 显式写出 available_at=published_at + 明确 source 语义
        ev = {"evidence_id": "EV-SRC-MAPPED", "published_at": PRE, "available_at": PRE,
              "available_at_source": "SOURCE_PUBLISHED_AT_IS_PROVEN_AVAILABILITY",
              "decision_cutoff": CUTOFF, "is_mock": False}
        obj = evidence_from_dict(ev)
        # Schema 保留 Adapter 显式提供的 available_at 与 source（不迁移、不删除）
        self.assertEqual(obj.available_at, PRE)
        self.assertEqual(obj.available_at_source, "SOURCE_PUBLISHED_AT_IS_PROVEN_AVAILABILITY")
        self.assertTrue(obj.time_eligible)
        elig, post = split_eligible([ev], CUTOFF)
        self.assertEqual(len(elig), 1)
        self.assertEqual(len(post), 0)
        # 是 Adapter 显式生成 available_at，不是 Time Gate 读 published_at

    # ============================================================= I6
    def test_i6_time_gate_does_not_read_published_at(self):
        """Time Gate 本身不读取 published_at（time_eligible 只判 available_at + init）。"""
        schema = (REPO_ROOT / "agent" / "evidence" / "schema.py").read_text(encoding="utf-8")
        tg = (REPO_ROOT / "agent" / "evidence" / "time_gate.py").read_text(encoding="utf-8")
        # time_eligible 判定源码不读取 published_at
        self.assertNotIn("self.published_at", schema.split("def time_eligible")[1].split("def backtest_eligible")[0])
        self.assertNotIn("published_at", tg.split("def is_available_before_cutoff")[1].split("def is_decision_eligible")[0])

    # ============================================================= I7
    def test_i7_decision_service_no_fallback(self):
        """DecisionService 不 fallback published_at（_ev_row / _rejection_reason 源码）。"""
        src = (REPO_ROOT / "code" / "decision_service.py").read_text(encoding="utf-8")
        self.assertNotIn("available_at or published_at", src)
        self.assertNotIn("ev.get(\"available_at\", \"\") or pub", src)
        self.assertIn("availability_proven", src)
        # 行为：Case E 证据 available_at 为空
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        self.assertEqual(d["evidence"]["rejected"][0]["available_at"], "")

    # ============================================================= I8 / I9 / I10
    def test_i8_i9_i10_no_published_fallback_in_three_endpoints(self):
        """CLI / Web / LLM Tool 均不 fallback published_at。"""
        cli = (REPO_ROOT / "mvp_demo.py").read_text(encoding="utf-8")
        self.assertNotIn("available_at or published_at", cli)
        web = (REPO_ROOT / "mvp_web.py").read_text(encoding="utf-8")
        self.assertNotIn("available_at or published_at", web)
        html = "\n".join([
            (REPO_ROOT / "templates" / "mvp_index.html").read_text(encoding="utf-8"),
            (REPO_ROOT / "static" / "mvp_core.js").read_text(encoding="utf-8"),
            (REPO_ROOT / "static" / "mvp_agent.js").read_text(encoding="utf-8"),
            (REPO_ROOT / "static" / "mvp_evidence.js").read_text(encoding="utf-8"),
        ])
        self.assertNotIn("e.available_at || e.published_at", html)
        self.assertIn("UNKNOWN / NOT PROVEN", html)   # 技术详情保留原始可用性表达
        self.assertIn("无法证明", html)                # 业务层：可用时间无法证明
        self.assertIn("未参与决策 NOT USED", html)     # Case E 明确未参与决策
        # LLM Tool：get_evidence 返回 available_at 不 fallback（Case E 为空）
        svc = make_demo_svc()
        d = svc.run_decision(DD, NODE, HOUR)
        tool = svc.get_evidence(d["decision_id"])
        t_rej = tool["rejected"][0]
        self.assertEqual(t_rej["available_at"], "")
        self.assertIn("INITIALIZATION_AFTER_CUTOFF", t_rej["rejection_reason"])

    # ============================================================= I11 / I12 / I13
    def test_i11_i12_i13_case_e_final_gate_pnl_unchanged(self):
        """Case E final / risk gate / pnl 完全不变（Before == After Patch）。"""
        before = make_full_svc()               # 无证据基线
        after = make_demo_svc()                # 注入 18Z 快照
        db = before.run_decision(GOLDEN_E["decision_date"], GOLDEN_E["node"], GOLDEN_E["hour"], reveal=True)
        da = after.run_decision(GOLDEN_E["decision_date"], GOLDEN_E["node"], GOLDEN_E["hour"], reveal=True)
        self.assertEqual(da["final_recommendation"], db["final_recommendation"])   # I11
        self.assertEqual(da["risk_gate"]["decision"], db["risk_gate"]["decision"])  # I12
        self.assertEqual(da["post_trade"]["pnl"], db["post_trade"]["pnl"])          # I13
        self.assertEqual(da["final_recommendation"], GOLDEN_E["final"])

    # ============================================================= I14
    def test_i14_manifest_description_updated(self):
        """manifest 不再错误描述 Case E 为 available_at>cutoff。"""
        m = json.loads((DEMO_DIR / "manifest.json").read_text(encoding="utf-8"))
        desc = m["files"]["evidence_demo.json"]
        self.assertNotIn("available_at>cutoff", desc)
        self.assertNotIn("available_at > cutoff", desc)
        self.assertIn("available_at not proven", desc)
        self.assertIn("INITIALIZATION_AFTER_CUTOFF", desc)
        # evidence_snapshot：available_at_proven=false
        self.assertFalse(m["evidence_snapshot"].get("available_at_proven"))

    # ============================================================= I15
    def test_i15_evidence_artifact_canonical_hash_pass(self):
        """Evidence Artifact canonical hash PASS（内容哈希自洽 + manifest 登记）。"""
        doc = json.loads(EVIDENCE_DEMO.read_text(encoding="utf-8"))
        subset = {k: v for k, v in doc.items() if k != "artifact_hash"}
        data = json.dumps(subset, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        expect = __import__("hashlib").sha256(data.replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(doc["artifact_hash"], expect)
        m = json.loads((DEMO_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["hashes"]["evidence_demo.json"], canonical_sha256(EVIDENCE_DEMO))
        self.assertFalse(m["evidence_snapshot"].get("available_at_proven"))
        self.assertEqual(m["evidence_snapshot"].get("hash_normalization"), "canonical-text")


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(V0315InvariantTests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("V0.3.1.5 Final Invariant Patch 验收测试（15 项验收 · 10 个测试方法）")
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
