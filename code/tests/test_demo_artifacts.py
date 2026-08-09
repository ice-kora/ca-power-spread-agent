# -*- coding: utf-8 -*-
"""
code/tests/test_demo_artifacts.py —— Demo Artifacts 验收测试（Agent A）
================================================================================

覆盖（任务 5 要求的 3 项 + 一致性补充）：
  D1  clean demo artifacts 可启动（模拟 clean clone：无 code/data，仅 demo_artifacts）
  D2  Demo Mode != Mock（DEMO 是真实历史切片：决策对象无任何 is_mock 项，可真实推荐）
  D3  manifest contains_mock==false（data_mode==DEMO / contains_future_outcome==true /
      hashes / row_counts 齐全）
  D4  FULL vs DEMO 一致性（仅当完整 artifacts 存在时运行；clean clone 自动跳过）

全部测试离线、确定性：决策用 StaticEvidenceAdapter（不联网）。
运行（仓库根目录，推荐）：
    python code/tests/test_demo_artifacts.py
    python -m unittest code.tests.test_demo_artifacts -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.artifact_hash import (  # noqa: E402
    HASH_ALGORITHM,
    HASH_NORMALIZATION,
    canonical_sha256,
)
from code.decision_service import DecisionService, StaticEvidenceAdapter  # noqa: E402
from data_mode import (  # noqa: E402
    DEMO_DIR_NAME,
    MODE_DEMO,
    MODE_FULL,
    MODE_MISSING,
    resolve_data_mode,
)

#: Golden Cases（与 docs/mvp_demo_cases.md / build_demo_artifacts.py 一致）
GOLDEN_CASES = [
    {"id": "B",  "decision_date": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3, "final": "SELL_DA",  "gate": "PASS"},
    {"id": "C1", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "gate": "REJECT"},
    {"id": "C2", "decision_date": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10, "final": "NO_TRADE", "gate": "WARNING"},
    {"id": "D",  "decision_date": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20, "final": "SELL_DA",  "gate": "WARNING"},
    {"id": "E",  "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "gate": "REJECT"},
]

DEMO_DIR = REPO_ROOT / DEMO_DIR_NAME


def _make_svc(data_dir: Path) -> DecisionService:
    return DecisionService(data_dir=data_dir, evidence_adapter=StaticEvidenceAdapter([]))


class DemoArtifactsTests(unittest.TestCase):

    # ------------------------------------------------------------------ D1
    def test_d1_clean_clone_demo_can_start(self):
        """模拟 clean clone（无 code/data，仅 demo_artifacts）→ DEMO 模式可启动并跑通 5 个 Golden Cases。"""
        self.assertTrue(DEMO_DIR.exists(), "demo_artifacts/ 应存在（先运行 python build_demo_artifacts.py）")
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp)
            shutil.copytree(DEMO_DIR, clone / DEMO_DIR_NAME)
            info = resolve_data_mode(repo_root=clone)
            self.assertEqual(info.mode, MODE_DEMO,
                             "clean clone（仅 demo_artifacts）应自动解析为 DEMO")
            # 不依赖 code/data 任何文件
            self.assertFalse((clone / "code" / "data" / "canonical.parquet").exists())

            svc = _make_svc(clone / DEMO_DIR_NAME)
            self.assertEqual(svc.data_mode, MODE_DEMO)
            for case in GOLDEN_CASES:
                with self.subTest(case=case["id"]):
                    dec = svc.run_decision(case["decision_date"], case["node"], case["hour"], reveal=True)
                    self.assertEqual(dec["final_recommendation"], case["final"],
                                     f"{case['id']} final 应等于 golden 值")
                    self.assertEqual(dec["risk_gate"]["decision"], case["gate"],
                                     f"{case['id']} risk_gate 应等于 golden 值")
                    # actual_* 必须在 reveal 后才可见（快照 outcome 语义）
                    self.assertEqual(dec["post_trade"]["status"], "REVEALED")

    # ------------------------------------------------------------------ D2
    def test_d2_demo_is_not_mock(self):
        """DEMO 是真实历史切片，不是 MOCK：决策对象无任何 is_mock 项，且给出真实推荐。"""
        svc = _make_svc(DEMO_DIR)
        self.assertEqual(svc.data_mode, MODE_DEMO)
        for case in GOLDEN_CASES:
            with self.subTest(case=case["id"]):
                dec = svc.run_decision(case["decision_date"], case["node"], case["hour"], reveal=False)
                mock_feats = [f.get("feature") for f in dec.get("top_features", []) if f.get("is_mock")]
                self.assertEqual(mock_feats, [], f"{case['id']} top_features 不应含 is_mock 项")
                for ev in dec.get("evidence", {}).get("eligible", []):
                    self.assertFalse(ev.get("is_mock"), f"{case['id']} eligible 证据不应含 is_mock")
                self.assertIn(dec["final_recommendation"], ("BUY_DA", "SELL_DA", "NO_TRADE"))
                self.assertEqual(dec["audit"]["meta"]["data_mode"], MODE_DEMO)
                # 有真实模型输出（不是 MOCK 占位）
                mo = dec.get("model_output", {})
                self.assertIsNotNone(mo.get("expected_return"))

    # ------------------------------------------------------------------ D3
    def test_d3_manifest_contains_mock_false(self):
        """manifest.json：data_mode==DEMO、contains_mock==false、contains_future_outcome==true、哈希与行数齐全。"""
        manifest_path = DEMO_DIR / "manifest.json"
        self.assertTrue(manifest_path.exists(), "manifest.json 应存在")
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(m["data_mode"], MODE_DEMO)
        self.assertFalse(m["contains_mock"])
        self.assertTrue(m["contains_future_outcome"])
        self.assertIn("artifact_version", m)
        self.assertIn("generated_at", m)
        self.assertIn("source_commit", m)
        self.assertEqual(len(m.get("golden_cases", [])), 5)
        self.assertIn("source_files", m)
        self.assertIn("row_counts", m)
        self.assertIn("hashes", m)
        # V0.3.1.2：manifest 声明跨平台 canonical 哈希语义
        self.assertEqual(m.get("hash_algorithm"), HASH_ALGORITHM)
        self.assertEqual(m.get("hash_normalization"), HASH_NORMALIZATION)
        # 每个输出文件哈希必须与磁盘一致（可复现性；canonical：CRLF/LF 均可）
        for fname, h in m["hashes"].items():
            self.assertTrue((DEMO_DIR / fname).exists(), f"输出文件 {fname} 应存在")
            digest = canonical_sha256(DEMO_DIR / fname)
            self.assertEqual(digest, h, f"{fname} 哈希应匹配 manifest（canonical）")

    # ------------------------------------------------------------------ D4
    def test_d4_full_vs_demo_consistency(self):
        """FULL vs DEMO 一致（5/5）。仅当完整 artifacts 存在时运行；clean clone 自动跳过。"""
        if resolve_data_mode().mode != MODE_FULL:
            self.skipTest("完整 artifacts 不存在（clean clone）——一致性需在完整数据机验证（docs/v0311_consistency.md）")
        from check_demo_consistency import run_consistency
        results, summary = run_consistency(REPO_ROOT)
        self.assertTrue(summary["manifest_ok"])
        self.assertEqual(summary["n_pass"], summary["n_total"], summary["overall"])
        for r in results:
            self.assertTrue(r["all_ok"], f"Case {r['case_id']} 存在不一致字段")
            self.assertTrue(r["demo_no_mock"], f"Case {r['case_id']} DEMO 混入 MOCK")


def _suite() -> unittest.TestSuite:
    return unittest.defaultTestLoader.loadTestsFromTestCase(DemoArtifactsTests)


if __name__ == "__main__":
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(_suite())
    n_pass = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    print("\n" + "=" * 60)
    print(f"Demo Artifacts: TOTAL={result.testsRun} PASSED={n_pass} "
          f"FAILED={len(result.failures)} ERRORS={len(result.errors)} SKIPPED={len(result.skipped)}")
    print("=" * 60)
    sys.exit(1 if (result.failures or result.errors) else 0)
