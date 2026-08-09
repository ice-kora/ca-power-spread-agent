# -*- coding: utf-8 -*-
"""
code/tests/test_v0312_freeze.py —— V0.3.1.2 Final Demo Freeze Patch 15 项验收（Agent D）
=========================================================================================

覆盖（V0.3.1.2 封板补丁 · 5 个确认问题 + 8 个 Lead 问题的可测断言）：
  F1  跨平台 canonical 哈希：.gitattributes 声明 + code/artifact_hash（CRLF/LF 等价）
  F2  唯一测试计数：run_tests.py 单次 discovery（不硬编码、不重复计数）
  F3  单一 DecisionService：mvp_demo.py 不 import RiskGate/RuleEngine/…（无第二套决策链）
  F4  CLI 主流程调用 DecisionService.run_decision（决策链单一来源）
  F5  CLI / Web / Tool 最终建议一致（同一 DecisionSnapshot 语义）
  F6  market_rule_version_for date-aware 单源（2026-05-01 边界）
  F7  DecisionService context.market_rule_version 按 target_date date-aware
  F8  2026-05-01+ 案例标 POST（Case.from_dict 按 decision_date 推断，无需改数据）
  F9  available_at-only eligibility（证据判据 available_at<=cutoff；缺失不可决策，不 fallback）
  F10 Web 审计复验用 available_at（源码含 available_at or published_at）
  F11 交易核心冻结：5 个 Golden Case final/gate 与 V0.3.1.1 完全一致
  F12 decision_service 不再 import mvp_demo（打破循环依赖，单一实现）
  F13 CLI 不携带硬编码 MARKET_RULE_VERSION 字符串（从 snapshot.context 读）
  F14 market_rule_version 单源：mvp_web /api/meta versions.market_rule == CURRENT
  F15 run_tests 报告公式由 result.testsRun 推导（无写死数字、无分段相加）

全部测试离线、确定性：
  * 决策一律用 StaticEvidenceAdapter([])（不联网）。
  * F5 通过 DecisionService.run_decision 对比（不依赖网络）。
  * F8 需要 agent/case_library/cases_auto.json 存在（demo_artifacts 已含其切片；
    完整数据机上还有完整案例库）。
  * F11 需要数据（FULL 或 DEMO 均可；Golden 值恒定）。

运行（仓库根目录，推荐统一入口）：
    python run_tests.py
    python code/tests/test_v0312_freeze.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.decision_service import (  # noqa: E402
    DecisionService,
    StaticEvidenceAdapter,
)
from data_mode import MODE_MISSING, resolve_data_mode  # noqa: E402

#: Golden Cases（与 docs/mvp_demo_cases.md / build_demo_artifacts.py 一致；
#:   值来自 V0.3.1.1 —— 交易核心冻结的回归基准）
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


def make_svc() -> DecisionService:
    """离线确定性 DecisionService：静态空证据（不联网）。"""
    return DecisionService(evidence_adapter=StaticEvidenceAdapter([]))


class V0312FreezeTests(unittest.TestCase):
    """V0.3.1.2 Final Demo Freeze Patch 15 项验收。"""

    # ============================================================= F1
    def test_f1_cross_platform_canonical_hash(self):
        """跨平台 canonical 哈希：.gitattributes 声明 + artifact_hash（CRLF/LF 等价）。"""
        ga = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("text eol=lf", ga)          # 文本统一 LF
        self.assertIn("*.py", ga) and self.assertIn("*.json", ga)
        self.assertIn("*.parquet", ga) and self.assertIn("-text", ga)  # 二进制保持
        from code.artifact_hash import canonical_sha256, is_text_file  # noqa: PLC0415
        self.assertTrue(is_text_file("a.json"))
        self.assertFalse(is_text_file("a.parquet"))
        # CRLF / LF 两个版本产生同一 canonical 哈希（跨平台可复现）
        lf = b'{"a": 1}\n{"b": 2}\n'
        crlf = lf.replace(b"\n", b"\r\n")
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "x.json"
            p2 = Path(tmp) / "y.json"
            p1.write_bytes(lf)
            p2.write_bytes(crlf)
            self.assertEqual(canonical_sha256(p1), canonical_sha256(p2),
                             "CRLF 与 LF 版本的 canonical 哈希必须一致")

    # ============================================================= F2
    def test_f2_run_tests_single_discovery(self):
        """唯一测试计数：run_tests.py 单次 discovery，报告数由 result.testsRun 推导。"""
        rt = (REPO_ROOT / "run_tests.py").read_text(encoding="utf-8")
        self.assertEqual(rt.count("loader.discover"), 1, "应只有一次测试发现")
        self.assertIn('pattern="test_*.py"', rt)
        self.assertIn("n_total = result.testsRun", rt)          # 动态，非写死
        self.assertIn("n_passed = n_total - n_failed - n_skipped", rt)  # 唯一计数公式
        for hardcoded in ("n_total = 300", "n_total = 285", "n_total = 280"):
            self.assertNotIn(hardcoded, rt, "不得硬编码测试总数")

    # ============================================================= F3
    def test_f3_cli_has_no_second_decision_chain(self):
        """单一 DecisionService：mvp_demo.py 无第二套 RiskGate / RuleEngine / 证据链。"""
        src = (REPO_ROOT / "mvp_demo.py").read_text(encoding="utf-8")
        banned = (
            "from code.risk_gate.gate import",
            "from code.risk_gate.case_adapter import",
            "from code.risk_gate.evidence_adapter import",
            "from code.decision.rule_engine import",
            "from agent.evidence.fetcher import",
            "from agent.evidence.gfs_forecast import",
            "from agent.evidence.time_gate import split_eligible",
            "RiskGate()",
            "RuleEngine()",
            "match_similar_tail_cases(",
            "evidence_direction_context(",
        )
        for token in banned:
            self.assertNotIn(token, src, f"mvp_demo.py 不应含 {token}")
        # 决策链只存在于 decision_service（真凶仍在，只是集中一处）
        ds = (REPO_ROOT / "code" / "decision_service.py").read_text(encoding="utf-8")
        self.assertIn("RiskGate()", ds)
        self.assertIn("RuleEngine()", ds)

    # ============================================================= F4
    def test_f4_cli_main_uses_run_decision(self):
        """CLI 主流程调用 DecisionService.run_decision（决策链单一来源）。"""
        src = (REPO_ROOT / "mvp_demo.py").read_text(encoding="utf-8")
        self.assertIn("DecisionService(", src)
        self.assertIn("svc.run_decision(dd, node, hour", src)
        self.assertIn("lock_decision(did)", src)   # 走 Outcome Access Control 生命周期
        self.assertIn("reveal_decision(did)", src)

    # ============================================================= F5
    def test_f5_cli_web_tool_same_snapshot(self):
        """CLI / Web / Tool 最终建议一致（同一 DecisionSnapshot 语义）。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        web = r.get_json()["decision"]
        web_final, web_er = web["final_recommendation"], web["model_output"]["expected_return"]
        # Tool（同一 service 实例，复用 decision_id）
        svc = mvp_web._SERVICES["offline"]  # noqa: SLF001
        tool = svc.get_decision(DD, NODE, HOUR)
        self.assertEqual(tool["final_recommendation"], web_final)
        self.assertEqual(tool["model_output"]["expected_return"], web_er)
        # CLI（即同一 DecisionService 语义，不重算）
        cli = svc.run_decision(DD, NODE, HOUR)
        self.assertEqual(cli["final_recommendation"], web_final)
        self.assertEqual(cli["model_output"]["expected_return"], web_er)

    # ============================================================= F6
    def test_f6_market_rule_version_for_date_aware(self):
        """market_rule_version_for date-aware 单源（2026-05-01 边界）。"""
        from code.market_rules import (  # noqa: PLC0415
            CURRENT_MARKET_RULE_VERSION,
            MARKET_RULE_VERSION_POST_DAME_EDAM_2026,
            MARKET_RULE_VERSION_PRE_DAME_EDAM_2026,
            market_rule_version_for,
        )
        self.assertEqual(market_rule_version_for("2026-04-30"), MARKET_RULE_VERSION_PRE_DAME_EDAM_2026)
        self.assertEqual(market_rule_version_for("2026-05-01"), MARKET_RULE_VERSION_POST_DAME_EDAM_2026)
        self.assertEqual(market_rule_version_for("2026-07-17"), MARKET_RULE_VERSION_POST_DAME_EDAM_2026)
        self.assertEqual(market_rule_version_for(""), CURRENT_MARKET_RULE_VERSION)
        self.assertEqual(market_rule_version_for(None), CURRENT_MARKET_RULE_VERSION)

    # ============================================================= F7
    def test_f7_decision_context_market_version_date_aware(self):
        """DecisionService 的 context.market_rule_version 按 target_date date-aware。"""
        svc = make_svc()
        d = svc.run_decision("2026-07-08", NODE, HOUR)   # target_date 2026-07-09 >= 2026-05-01
        self.assertEqual(d["context"]["market_rule_version"], "POST_DAME_EDAM_2026")
        self.assertEqual(d["audit"]["meta"]["market_rule_version"], "POST_DAME_EDAM_2026")

    # ============================================================= F8
    def test_f8_case_from_dict_date_aware(self):
        """2026-05-01+ 案例标 POST：Case.from_dict 无字段时按 decision_date 推断。"""
        from agent.case_library.case import Case  # noqa: PLC0415
        from code.market_rules import (  # noqa: PLC0415
            MARKET_RULE_VERSION_POST_DAME_EDAM_2026,
            MARKET_RULE_VERSION_PRE_DAME_EDAM_2026,
        )
        c1 = Case.from_dict({"case_id": "X1", "decision_date": "2026-07-08", "node": NODE, "hour": 2})
        self.assertEqual(c1.market_rule_version, MARKET_RULE_VERSION_POST_DAME_EDAM_2026)
        c2 = Case.from_dict({"case_id": "X2", "decision_date": "2026-04-30", "node": NODE, "hour": 2})
        self.assertEqual(c2.market_rule_version, MARKET_RULE_VERSION_PRE_DAME_EDAM_2026)
        # 显式标注保留（不被推断覆盖）
        c3 = Case.from_dict({"case_id": "X3", "decision_date": "2026-07-08",
                             "market_rule_version": "PRE_DAME_EDAM_2026"})
        self.assertEqual(c3.market_rule_version, MARKET_RULE_VERSION_PRE_DAME_EDAM_2026)
        # 实际案例库（2026-06-04 ~ 08-04，全部 2026-05-01 后）→ 全部 POST
        data = json.loads((REPO_ROOT / "agent" / "case_library" / "cases_auto.json")
                          .read_text(encoding="utf-8"))
        cases = data.get("cases", data) if isinstance(data, dict) else data
        self.assertTrue(cases)
        for raw in cases[:60]:
            cc = Case.from_dict(raw)
            self.assertEqual(cc.market_rule_version, MARKET_RULE_VERSION_POST_DAME_EDAM_2026,
                             f"{raw.get('case_id')} 2026-05-01+ 案例应标 POST")

    # ============================================================= F9
    def test_f9_available_at_only_eligibility(self):
        """available_at-only eligibility：判据 = available_at <= cutoff（非 published_at）。"""
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        from code.tests.test_v0311_hardening import CUTOFF_UTC, POST_CUTOFF, PRE_CUTOFF, _mk_ev  # noqa: PLC0415
        evs = [
            _mk_ev("EV-LATE", PRE_CUTOFF, available_at=POST_CUTOFF),    # published<=cutoff 但 available>cutoff → rejected
            _mk_ev("EV-EARLY", POST_CUTOFF, available_at=PRE_CUTOFF),   # published>cutoff 但 available<=cutoff → eligible
        ]
        elig, post = split_eligible(evs, CUTOFF_UTC)
        self.assertEqual({e.get("evidence_id") for e in elig}, {"EV-EARLY"})
        self.assertEqual({e.get("evidence_id") for e in post}, {"EV-LATE"})
        # 注释清理：核心源码不再用 published_at 作为唯一判据表述
        tg = (REPO_ROOT / "agent" / "evidence" / "time_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("published_at <= decision_cutoff 才能", tg)
        self.assertIn("available_at <= decision_cutoff", tg)
        schema = (REPO_ROOT / "agent" / "evidence" / "schema.py").read_text(encoding="utf-8")
        self.assertNotIn("必须满足 published_at <= decision_cutoff", schema)

    # ============================================================= F10
    def test_f10_web_audit_reuses_available_at(self):
        """Web 审计复验**只判 available_at**（V0.3.1.3，不 fallback published_at）——与 Time Gate 判据一致。"""
        src = (REPO_ROOT / "mvp_web.py").read_text(encoding="utf-8")
        self.assertNotIn('e.get("available_at") or e.get("published_at")', src)
        self.assertIn('e.get("available_at")', src)

    # ============================================================= F11
    def test_f11_trading_core_frozen_golden_unchanged(self):
        """交易核心冻结：5 个 Golden Case final/gate 与 V0.3.1.1 完全一致。"""
        svc = make_svc()
        for case in GOLDEN_CASES:
            with self.subTest(case=case["id"]):
                d = svc.run_decision(case["decision_date"], case["node"], case["hour"])
                self.assertEqual(d["final_recommendation"], case["final"],
                                 f"{case['id']} final 应保持不变（交易核心冻结）")
                self.assertEqual(d["risk_gate"]["decision"], case["gate"],
                                 f"{case['id']} risk_gate 应保持不变")

    # ============================================================= F12
    def test_f12_decision_service_not_import_mvp_demo(self):
        """decision_service 不再 import mvp_demo（打破循环依赖，单一实现）。"""
        ds = (REPO_ROOT / "code" / "decision_service.py").read_text(encoding="utf-8")
        self.assertNotIn("from mvp_demo", ds)
        self.assertNotIn("import mvp_demo", ds)
        # 可独立导入（不依赖 CLI 渲染层）
        import importlib  # noqa: PLC0415
        import code.decision_service as mod  # noqa: PLC0415
        importlib.reload(mod)

    # ============================================================= F13
    def test_f13_cli_reads_market_version_from_snapshot(self):
        """CLI 不再硬编码 MARKET_RULE_VERSION：渲染从 snapshot.context 读取。"""
        src = (REPO_ROOT / "mvp_demo.py").read_text(encoding="utf-8")
        self.assertNotIn('"CAISO-DAM/BPM-demo-0.2', src, "旧硬编码版本字符串已移除")
        self.assertIn("ctx['market_rule_version']", src)

    # ============================================================= F14
    def test_f14_web_versions_market_rule_single_source(self):
        """market_rule_version 单源：Web /api/meta 不暴露固定 PRE/POST（改为启用标记）；单笔页面用 snapshot context。"""
        import mvp_web  # noqa: PLC0415
        meta = mvp_web.app.test_client().get("/api/meta").get_json()
        self.assertIn("MARKET_RULE_VERSIONING_ENABLED", meta["versions"]["market_rule"])
        # 单笔决策：S1 展示值 == DecisionSnapshot.context.market_rule_version（date-aware）
        r = mvp_web.app.test_client().post(
            "/api/decision", json={"decision_date": "2026-07-16",
                                   "node": "CONTROLX_1_N001", "hour": 3,
                                   "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["decision"]
        self.assertEqual(d["context"]["market_rule_version"], "POST_DAME_EDAM_2026")

    # ============================================================= F15
    def test_f15_run_tests_single_count_no_hardcode(self):
        """run_tests 报告公式由 result.testsRun 推导：无写死数字、无分段相加。"""
        import run_tests  # noqa: PLC0415
        suite = run_tests.collect(str(REPO_ROOT))
        n = len(list(run_tests._flatten(suite)))  # noqa: SLF001
        self.assertGreater(n, 0, "应收集到测试")
        src = (REPO_ROOT / "run_tests.py").read_text(encoding="utf-8")
        self.assertIn("n_total = result.testsRun", src)
        self.assertIn("n_passed = n_total - n_failed - n_skipped", src)
        self.assertNotIn("n_total = 300", src)
        self.assertNotIn("n_total = 285", src)


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(V0312FreezeTests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("V0.3.1.2 Final Demo Freeze Patch 验收测试报告（15 项）")
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
