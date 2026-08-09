# -*- coding: utf-8 -*-
"""
code/tests/test_mvp_v031.py —— V0.3.1 Web + LLM Agent MVP 12 项验收测试
================================================================================

覆盖（V0.3.1 任务验收，共 12 项）：
  T1  Web Decision 与 CLI Decision（mvp_demo.py / DecisionService.run_decision）完全一致
  T2  Web 使用统一 GFS Collector（无第二套 GFS eligibility）
  T3  available_at 展示与 Time Gate 一致（P0-2 单一口径，无伪精确时间戳）
  T4  LLM 不可修改 final recommendation（完整性守卫拦截）
  T5  Tool 返回数字不可被 LLM 覆盖
  T6  Outcome 未 Reveal 时 post_trade tool 被拒绝（OUTCOME_NOT_REVEALED / 403）
  T7  MOCK 不可进入推荐（is_mock 证据硬隔离，绝不进 eligible）
  T8  Case 不可穿越（case_available_at <= decision_time）
  T9  Agent Tool Trace 正常生成（trace=True 有结构化 steps；trace=False 为 None）
  T10 无 API Key 时核心 Web Pipeline 仍可运行（决策 + Ask 降级）
  T11 有 LLM（mock provider）时 Ask Agent 完成 decision explanation / provenance / similar case
  T12 Web 页面路由可访问（Flask test client）

运行（仓库根目录，推荐）：
    python code/tests/test_mvp_v031.py            # 严格输出 TOTAL/PASSED/FAILED/SKIPPED
    python -m unittest code.tests.test_mvp_v031 -v

全部测试离线、确定性：决策用 StaticEvidenceAdapter（不联网），GFS 路径用 mock 验证。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from code.decision_service import (  # noqa: E402
    OUTCOME_NOT_REVEALED,
    DecisionService,
    DefaultEvidenceAdapter,
    StaticEvidenceAdapter,
)
from code.llm_copilot import (  # noqa: E402
    LLMCopilot,
    MockLlmClient,
)

DD = "2026-07-08"           # 决策日
NODE = "CONTROLX_1_N001"    # ZP26
HOUR = 2
TARGET_DATE = "2026-07-09"
CUTOFF_UTC = "2026-07-08T17:00:00"          # 10:00 PT → UTC（PDT=UTC−7）
PRE_CUTOFF = "2026-07-08T09:00:00"          # <= cutoff → eligible
POST_CUTOFF = "2026-07-08T18:00:00"         # > cutoff → rejected

# T1 多组 (decision_date, node, hour) 对比（覆盖 NO_TRADE / SELL_DA）
MATCH_CASES = [
    ("2026-07-08", "CONTROLX_1_N001", 2),
    ("2026-07-16", "CONTROLX_1_N001", 3),
    ("2026-07-10", "SNLNDRO_1_N001", 10),
    ("2026-07-20", "SNLNDRO_1_N001", 20),
]


def _mk_ev(eid: str, published_at: str, *, severity: str = "WATCH",
           is_mock: bool = False) -> dict:
    """测试用证据 dict（与现有 test_decision_service 口径一致）。"""
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
        "available_at": published_at,   # Source Adapter 显式：published 即真正可用时刻
        "retrieved_at": "2026-08-09T00:00:00",
        "target_time": "2026-07-09T02:00:00",
        "decision_cutoff": CUTOFF_UTC,
        "summary": f"test evidence {eid}",
        "directional_effect": "UNCERTAIN",
        "confidence": 0.5,
    }


def make_svc() -> DecisionService:
    return DecisionService(evidence_adapter=StaticEvidenceAdapter())


def make_did(svc: DecisionService, dd: str = DD, node: str = NODE,
             hour: int = HOUR, reveal: bool = False) -> str:
    return svc.run_decision(dd, node, hour, reveal=reveal)["decision_id"]


# ---------------------------------------------------------------------------
# T1 辅助：CLI 决策链（V0.3.1.2 起：CLI 即 DecisionService，无第二套
# RiskGate/RuleEngine；最终建议 / reasons / 数值均来自同一 DecisionSnapshot）
# ---------------------------------------------------------------------------
def _cli_decision(svc: DecisionService, dd: str, node: str, hour: int) -> dict:
    """CLI 的最终建议 == 同一 DecisionService 的 DecisionSnapshot（不重算）。"""
    dec = svc.run_decision(dd, node, hour)
    return {"final": dec["final_recommendation"],
            "rule_reasons": list(dec["rule_engine"]["reasons"]),
            "expected_return": dec["model_output"].get("expected_return")}


class TestMvpV031(unittest.TestCase):
    """V0.3.1 Web + LLM Agent MVP 验收（12 项）。"""

    @classmethod
    def setUpClass(cls):
        required = [
            REPO_ROOT / "code" / "data" / "canonical.parquet",
            REPO_ROOT / "code" / "data" / "predictions_v2.csv",
            REPO_ROOT / "code" / "data" / "stage3" / "risk_features.parquet",
            REPO_ROOT / "agent" / "case_library" / "cases.json",
            REPO_ROOT / "agent" / "case_library" / "cases_auto.json",
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            raise unittest.SkipTest(
                f"核心 artifact 缺失: {missing}；请先运行 python prepare_mvp.py")

    def setUp(self):
        self.svc = make_svc()
        self.did = make_did(self.svc)

    # ------------------------------------------------------------- T1
    def test_t1_web_decision_identical_to_cli_decision(self):
        for dd, node, hour in MATCH_CASES:
            with self.subTest(decision=(dd, node, hour)):
                cli = _cli_decision(self.svc, dd, node, hour)
                web = self.svc.run_decision(dd, node, hour, reveal=False)
                # 最终建议完全一致
                self.assertEqual(web["final_recommendation"], cli["final"],
                                 f"{dd} {node} H{hour}")
                # 规则引擎 reason 完全一致
                self.assertEqual(list(web["rule_engine"]["reasons"]), cli["rule_reasons"],
                                 f"{dd} {node} H{hour}")
                # 模型数值完全一致
                self.assertEqual(web["model_output"]["expected_return"], cli["expected_return"],
                                 f"{dd} {node} H{hour}")
                # Post-trade 语义（SELL=+actual_return / BUY=−actual_return / NO_TRADE=0）
                # —— 与 CLI（mvp_demo.main 同款 PnL 公式）一致
                if cli["final"] != "NO_TRADE":
                    web_revealed = self.svc.run_decision(dd, node, hour, reveal=True)
                    cr = self.svc.canon[
                        (self.svc.canon["node"] == node)
                        & (self.svc.canon["target_date"]
                           == pd.Timestamp(
                               (pd.Timestamp(dd) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")))
                        & (self.svc.canon["hour"] == hour)].iloc[0]
                    sign = +1.0 if cli["final"] == "SELL_DA" else -1.0
                    expect_pnl = sign * float(cr["actual_return"])
                    self.assertAlmostEqual(web_revealed["post_trade"]["pnl"],
                                           expect_pnl, places=6)

    # ------------------------------------------------------------- T2
    def test_t2_web_uses_unified_gfs_collector(self):
        # 1) 默认适配器就是统一 GFS 路径（无第二套实现）
        self.assertIsInstance(DecisionService().evidence_adapter, DefaultEvidenceAdapter)
        # 2) Fetcher 注册表：WEATHER_FORECAST 唯一源 = gfs_forecast adapter
        from agent.evidence import fetcher as ft
        self.assertEqual(
            ft.FETCHER_REGISTRY["WEATHER_FORECAST"],
            "agent.evidence.gfs_forecast:fetch_gfs_weather_evidence",
        )
        # 3) 单一事实来源转发：gfs_forecast 的时间/周期常量 == weather_gfs
        from agent.evidence import gfs_forecast as gf
        from code.data_acquisition import weather_gfs as wg
        self.assertEqual(gf.GFS_CYCLES_UTC, wg.GFS_CYCLES_UTC)
        self.assertEqual(gf.DEFAULT_CYCLE, wg.DEFAULT_CYCLE)
        # 4) fetch_gfs_weather_evidence 只经 build_gfs_evidence（Adapter），无第二套 eligibility
        with mock.patch.object(gf, "build_gfs_evidence", return_value=_mk_ev("EV-GFS", PRE_CUTOFF)) as m_build:
            evs = gf.fetch_gfs_weather_evidence(NODE, DD, cycle=wg.DEFAULT_CYCLE)
            m_build.assert_called_once()
            self.assertEqual(len(evs), 1)
        # 5) DefaultEvidenceAdapter.gather 只调用 fetch_evidence + build_gfs_evidence，
        #    并用 Time Gate（split_eligible）程序裁决 —— 不存在第二条 GFS 判定
        pre = _mk_ev("EV-PRE", PRE_CUTOFF)
        post = _mk_ev("EV-POST", POST_CUTOFF)
        with mock.patch("code.decision_service.fetch_evidence", return_value=[pre]) as m_fetch, \
             mock.patch("code.decision_service.build_gfs_evidence", return_value=post) as m_build18:
            bundle = DefaultEvidenceAdapter().gather(NODE, DD, CUTOFF_UTC)
            m_fetch.assert_called_once()
            m_build18.assert_called_once_with(NODE, DD, cycle="18Z")
            self.assertEqual([e["evidence_id"] for e in bundle.eligible], ["EV-PRE"])
            self.assertEqual([e["evidence_id"] for e in bundle.post_decision], ["EV-POST"])

    # ------------------------------------------------------------- T3
    def test_t3_available_at_display_matches_time_gate(self):
        import mvp_web
        from code.canonical import availability_map
        from code.data_acquisition.schemas import (
            AVAILABILITY_BASIS_STATIC,
            feature_available_at_display,
            feature_decision_eligible,
            make_decision_cutoff,
        )
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        dec = r.get_json()["decision"]
        cutoff = make_decision_cutoff(DD) or ""
        av_map = availability_map()
        feats = dec["top_features"]
        self.assertTrue(feats, "决策应返回 top_features")
        for f in feats:
            meta = av_map.get(f["feature"], {})
            basis = f.get("availability_basis", "UNKNOWN")
            if not meta:
                # 静态补齐特征（hour/node）→ STATIC 恒可用
                self.assertEqual(basis, AVAILABILITY_BASIS_STATIC, f["feature"])
                continue
            # 展示口径 == Time Gate 判定口径（同一 availability_map 单一事实来源）
            self.assertEqual(f["available_at_display"],
                             feature_available_at_display(meta, DD))
            self.assertEqual(f["has_precise_publish_time"],
                             bool(meta.get("has_precise_publish_time", False)))
            if basis in ("STRUCTURAL_LAG", "ASSUMED_AVAILABLE"):
                # 无精确发布时刻：显示最晚可证上界，绝不出现伪精确时间戳（如 23:59）
                self.assertFalse(f["has_precise_publish_time"])
                self.assertEqual(f["latest_possible_available_at"], "decision_date 00:00 PT")
                self.assertNotIn("23:59", str(f["available_at_display"]))
                # Time Gate 判定：该特征在决策时点确实可得
                self.assertTrue(
                    feature_decision_eligible(meta, DD, cutoff),
                    f"{f['feature']} 展示可用但 Time Gate 判定不可用",
                )

    # ------------------------------------------------------------- T4
    def test_t4_llm_cannot_override_final_recommendation(self):
        calls = {"n": 0}
        cp = LLMCopilot(service=self.svc, provider="mock",
                        llm_client=MockLlmClient(responder=None), env={})
        # 预取真实 final（工具数字是单一事实来源）
        probe = cp._execute_tool("get_decision",
                                 {"decision_date": DD, "node": NODE, "hour": HOUR})
        truth = probe["final_recommendation"]
        wrong = {"BUY_DA": "SELL_DA", "SELL_DA": "BUY_DA", "NO_TRADE": "BUY_DA"}[truth]

        def responder(client, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"tool_calls": [{"id": "c1", "tool": "get_decision",
                                        "arguments": {"decision_date": DD, "node": NODE,
                                                      "hour": HOUR}}]}
            return {"text": f"The final recommendation is {wrong}."}

        cp.client.responder = responder
        # 非预置问题 → 走原生 tool calling 循环，让 LLM 声称一个错误方向
        out = cp.ask("describe the trading situation in detail", trace=True)
        self.assertEqual(out["status"], "blocked")
        # LLM 声称的方向被拦截（guard 审计消息里可提到被拦截的错误方向，属诚实审计）
        self.assertIn("数字/方向完整性校验未通过", out["answer"])
        self.assertIn("已拦截", out["answer"])
        # 工具返回的真实方向保持原样（单一事实来源，未被 LLM 覆盖）
        tool_truth = json.loads(out["tools_called"][0]["result_summary"])["final_recommendation"]
        self.assertEqual(tool_truth, truth)
        self.assertTrue(any(s["stage"] == "guard" and s["status"] == "BLOCKED"
                            for s in out["trace"]["steps"]))

    # ------------------------------------------------------------- T5
    def test_t5_tool_numbers_not_overridable_by_llm(self):
        calls = {"n": 0}

        def responder(client, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"tool_calls": [{"id": "c1", "tool": "get_decision",
                                        "arguments": {"decision_date": DD, "node": NODE,
                                                      "hour": HOUR}}]}
            return {"text": "The expected_return is 999 and the final recommendation is BUY_DA."}

        cp = LLMCopilot(service=self.svc, provider="mock",
                        llm_client=MockLlmClient(responder=responder), env={})
        out = cp.ask("tell me about this market", trace=True)
        self.assertEqual(out["status"], "blocked")
        true_er = json.loads(out["tools_called"][0]["result_summary"])["model_output"]["expected_return"]
        self.assertNotEqual(true_er, 999.0)
        self.assertNotIn("999", out["answer"])
        self.assertNotIn("999", json.dumps(out["tools_called"], ensure_ascii=False))
        self.assertIn("数字/方向完整性校验未通过", out["answer"])

    # ------------------------------------------------------------- T6
    def test_t6_post_trade_refused_when_not_revealed(self):
        # Service 层：未 reveal → post_trade tool 拒绝
        out = self.svc.get_post_trade_review(self.did)
        self.assertEqual(out["status"], OUTCOME_NOT_REVEALED)
        dec = self.svc._decisions[self.did]
        self.assertFalse(dec["outcome_revealed"])
        self.assertEqual(dec["post_trade"]["status"], OUTCOME_NOT_REVEALED)
        # Web 层：未 LOCK → reveal 403；LOCK 后 → REVEALED
        import mvp_web
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        did = r.get_json()["decision"]["decision_id"]
        self.assertEqual(r.get_json()["decision"]["post_trade"]["status"], OUTCOME_NOT_REVEALED)
        r = c.post(f"/api/decision/{did}/reveal")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["status"], "NOT_LOCKED")
        r = c.post(f"/api/decision/{did}/lock")
        self.assertEqual(r.status_code, 200)
        r = c.post(f"/api/decision/{did}/reveal")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "REVEALED")
        # LLM 层：未 reveal 时"赚了吗"拿不到 actual_*
        svc2 = make_svc()
        did2 = make_did(svc2, reveal=False)
        leaked = {"found": False}

        def responder(client, messages, tools):
            joined = "\n".join(str(m.get("content", "")) for m in messages)
            for k in ("actual_return", "actual_da", "actual_rtpd"):
                if k in joined:
                    leaked["found"] = True
            return {"text": "结果尚未揭晓，无法判断赚亏。"}

        cp = LLMCopilot(service=svc2, provider="mock",
                        llm_client=MockLlmClient(responder=responder), env={})
        out = cp.ask("赚了吗", decision_id=did2, trace=True)
        self.assertEqual(out["tools_called"][0]["tool"], "get_post_trade_review")
        self.assertIn(OUTCOME_NOT_REVEALED, out["tools_called"][0]["result_summary"])
        self.assertFalse(leaked["found"], "LLM 消息中不得出现 actual_*")

    # ------------------------------------------------------------- T7
    def test_t7_mock_never_enters_recommendation(self):
        raw = [
            _mk_ev("EV-ELIG", PRE_CUTOFF),
            _mk_ev("EV-MOCK", PRE_CUTOFF, is_mock=True),   # 决策前发布但 is_mock=True
        ]
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter(raw))
        dec = svc.run_decision(DD, NODE, HOUR, reveal=False)
        elig_ids = {e["evidence_id"] for e in dec["evidence"]["eligible"]}
        rej_ids = {e["evidence_id"] for e in dec["evidence"]["rejected"]}
        self.assertIn("EV-ELIG", elig_ids)
        self.assertIn("EV-MOCK", rej_ids)
        self.assertNotIn("EV-MOCK", elig_ids)
        mock_rej = next(r for r in dec["evidence"]["rejected"] if r["evidence_id"] == "EV-MOCK")
        self.assertIn("MOCK_DATA_NOT_ELIGIBLE", mock_rej["rejection_reason"])
        # 决策路径无 MOCK：audit 自证 + 无任何 is_mock 特征/证据进入
        self.assertEqual(dec["audit"]["mock_data_used"], "NONE")
        self.assertTrue(all(not f["is_mock"] for f in dec["top_features"]))

    # ------------------------------------------------------------- T8
    def test_t8_cases_cannot_time_travel(self):
        from agent.case_library.policy import decision_time_for, is_retrievable
        did = self.did
        out = self.svc.get_similar_cases(did)
        decision_time = decision_time_for(TARGET_DATE)
        self.assertTrue(decision_time, "应能算出决策时点")
        for c in out["cases"]:
            self.assertTrue(c["case_available_at"], c)
            self.assertTrue(c["as_of_verified"],
                            f"case {c['case_id']} 穿越了决策时点")
            self.assertTrue(is_retrievable(c, decision_time), c["case_id"])
        # 构造一个晚于决策时点才可用的案例 → 一律不可检索
        late = {"case_id": "LATE", "case_available_at": "2026-07-10T09:00:00",
                "node": NODE, "hour": HOUR}
        self.assertFalse(is_retrievable(late, decision_time))
        # 缺 case_available_at → 保守不可检索
        self.assertFalse(is_retrievable({"case_id": "NOAVAIL", "node": NODE}, decision_time))

    # ------------------------------------------------------------- T9
    def test_t9_agent_tool_trace_generated(self):
        cp = LLMCopilot(service=self.svc, provider="mock", env={})
        out = cp.ask("为什么不卖", decision_id=self.did, trace=True)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["tools_called"])
        tr = out["trace"]
        self.assertEqual(tr["user_question"], "为什么不卖")
        self.assertIn(tr["mode"], {"preset", "native", "router"})
        self.assertTrue(tr["steps"])
        for i, s in enumerate(tr["steps"]):
            self.assertEqual(s["step"], i + 1)
            self.assertIn(s["stage"], {"route", "tool", "llm", "guard"})
        # trace=False → 不生成 trace 对象
        out2 = cp.ask("为什么不卖", decision_id=self.did, trace=False)
        self.assertIsNone(out2["trace"])

    # ------------------------------------------------------------- T10
    def test_t10_web_pipeline_runs_without_api_key(self):
        import mvp_web
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        dec = r.get_json()["decision"]
        self.assertIn(dec["final_recommendation"], ("BUY_DA", "SELL_DA", "NO_TRADE"))
        did = dec["decision_id"]
        # LOCK → REVEAL（完整生命周期在无 key 环境下可用）
        self.assertEqual(c.post(f"/api/decision/{did}/lock").status_code, 200)
        self.assertEqual(c.post(f"/api/decision/{did}/reveal").status_code, 200)
        # Ask 降级：核心 pipeline 照常，Ask 诚实提示 LLM NOT CONFIGURED
        with mock.patch.dict(os.environ,
                             {"LLM_API_KEY": "", "LLM_PROVIDER": "",
                              "LLM_MODEL": ""}, clear=False):
            r2 = c.post("/api/ask", json={"question": "为什么不卖", "decision_id": did})
        self.assertEqual(r2.status_code, 200)
        j = r2.get_json()
        # 核心 pipeline 可用；Ask 诚实降级（LLM NOT CONFIGURED）并如实标记 status=degraded
        self.assertEqual(j["status"], "degraded")
        self.assertTrue(j["degraded"])
        self.assertIn("LLM NOT CONFIGURED", j["answer"])

    # ------------------------------------------------------------- T11
    def test_t11_mock_llm_completes_explanation_provenance_similar(self):
        cp = LLMCopilot(service=self.svc, provider="mock", env={})
        # 1) decision explanation
        out = cp.ask("请解释这个决策", decision_id=self.did)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["answer"])
        self.assertTrue(any(t["tool"] == "get_decision" for t in out["tools_called"]))
        # 2) provenance
        out = cp.ask("天气来源", decision_id=self.did)
        self.assertTrue(any(t["tool"] == "get_data_provenance" for t in out["tools_called"]))
        # 3) similar case
        out = cp.ask("类似案例", decision_id=self.did)
        self.assertTrue(any(t["tool"] == "get_similar_cases" for t in out["tools_called"]))

    # ------------------------------------------------------------- T12
    def test_t12_web_page_routes_reachable(self):
        import mvp_web
        c = mvp_web.app.test_client()
        for path in ("/", "/data-sources", "/how-it-works", "/api/meta"):
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertTrue(r.data, f"{path} 返回空页面")
        meta = c.get("/api/meta").get_json()
        for key in ("alpha_label", "nodes", "golden_cases", "llm", "versions"):
            self.assertIn(key, meta)


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    import io
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestMvpV031)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("MVP V0.3.1 验收测试报告")
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
