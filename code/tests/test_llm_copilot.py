# -*- coding: utf-8 -*-
"""
code/tests/test_llm_copilot.py

Trading Decision Copilot（Agent D）单元测试（unittest）。

覆盖（任务要求）：
  - 无 API Key → 降级：answer 含 "LLM NOT CONFIGURED"，但预置路由仍执行工具（核心流程可运行）
  - 预设问题路由自动选 Tool（为什么不卖/为什么 NO_TRADE/最重要的特征/天气来源/类似案例/数据 10 点前可见/赚了吗）
  - get_post_trade_review 未 reveal 时，LLM 拿不到 actual（工具结果 + LLM 消息均无 actual_*）
  - 原生 tool calling：tool 返回数字不可被 LLM 覆盖（数字/方向完整性守卫拦截）
  - 受控 JSON Tool Router：LLM 输出 JSON 计划，由程序执行 Tool
  - 证据不足 → UNCERTAIN / INSUFFICIENT EVIDENCE
  - mock provider 下完成 decision explanation / provenance / similar case

运行：python -m unittest code.tests.test_llm_copilot -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from code.decision_service import (  # noqa: E402
    DecisionService,
    StaticEvidenceAdapter,
)
from code.llm_copilot import (  # noqa: E402
    LLMCopilot,
    MockLlmClient,
    TOOL_SCHEMAS,
    copilot_status,
)

DD = "2026-07-08"
NODE = "CONTROLX_1_N001"
HOUR = 2


def make_svc() -> DecisionService:
    return DecisionService(evidence_adapter=StaticEvidenceAdapter())


def make_did(svc: DecisionService, reveal: bool = False) -> str:
    return svc.run_decision(DD, NODE, HOUR, reveal=reveal)["decision_id"]


def ctx(**kw) -> dict:
    base = {"decision_date": DD, "node": NODE, "hour": HOUR}
    base.update(kw)
    return base


class TestLlmCopilot(unittest.TestCase):

    def setUp(self):
        self.svc = make_svc()
        self.did = make_did(self.svc)

    # ------------------------------------------------------------- 降级
    def test_degraded_no_key_returns_llm_not_configured(self):
        cp = LLMCopilot(service=self.svc, provider="openai", api_key=None,
                        model="gpt-x", env={})
        out = cp.ask("为什么不卖", decision_id=self.did, trace=True)
        self.assertTrue("LLM NOT CONFIGURED" in out["answer"], out["answer"])
        self.assertEqual(out["status"], "degraded")
        self.assertTrue(out["degraded"])
        # 核心流程可运行：预置路由仍执行了工具，返回真实结构化结果
        self.assertTrue(any(t["tool"] == "get_decision" for t in out["tools_called"]))
        summary = json.loads(out["tools_called"][0]["result_summary"])
        self.assertEqual(summary["status"], "ok")
        self.assertIn("final_recommendation", summary)

    def test_degraded_unknown_provider_is_degraded(self):
        cp = LLMCopilot(service=self.svc, provider="nope", api_key="secret",
                        model="m", env={})
        out = cp.ask("类似案例", decision_id=self.did)
        self.assertEqual(out["status"], "degraded")
        self.assertIn("LLM NOT CONFIGURED", out["answer"])

    # ------------------------------------------------------------- 预设路由
    def test_preset_routes_select_expected_tool(self):
        cases = [
            ("为什么不卖", "get_decision"),
            ("为什么 NO_TRADE", "get_decision"),
            ("最重要的特征", "get_feature_explanation"),
            ("天气来源", "get_data_provenance"),
            ("类似案例", "get_similar_cases"),
            ("数据 10 点前可见", "get_data_provenance"),
            ("赚了吗", "get_post_trade_review"),
        ]
        for q, tool in cases:
            with self.subTest(question=q):
                cp = LLMCopilot(service=self.svc, provider="mock",
                                model="mock-model", env={})
                out = cp.ask(q, decision_id=self.did, trace=True)
                self.assertEqual(out["status"], "ok", q)
                tools = [t["tool"] for t in out["tools_called"]]
                self.assertIn(tool, tools, q)
                # Trace 包含 route → tool 阶段
                stages = [s["stage"] for s in out["trace"]["steps"]]
                self.assertIn("route", stages)
                self.assertIn("tool", stages)

    def test_preset_route_get_decision_with_context_no_id(self):
        cp = LLMCopilot(service=self.svc, provider="mock", env={})
        out = cp.ask("为什么不卖", context=ctx(), trace=False)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["tools_called"][0]["tool"], "get_decision")
        # get_decision 参数来自 context
        self.assertEqual(out["tools_called"][0]["args"]["node"], NODE)
        self.assertEqual(out["tools_called"][0]["args"]["hour"], HOUR)

    def test_tools_called_contract_shape(self):
        cp = LLMCopilot(service=self.svc, provider="mock", env={})
        out = cp.ask("类似案例", decision_id=self.did)
        for t in out["tools_called"]:
            self.assertEqual(set(t.keys()), {"tool", "args", "result_summary"})

    # ------------------------------------------------------------- Post-trade 防泄漏
    def test_post_trade_not_revealed_no_actual_leak(self):
        svc2 = make_svc()
        did_unrevealed = make_did(svc2, reveal=False)
        leaked = {"found": False}

        def responder(client, messages, tools):
            joined = "\n".join(str(m.get("content", "")) for m in messages)
            for key in ("actual_return", "actual_da", "actual_rtpd"):
                if key in joined:
                    leaked["found"] = True
            return {"text": "结果尚未揭晓，无法判断赚亏。"}

        cp = LLMCopilot(service=svc2, provider="mock",
                        llm_client=MockLlmClient(responder=responder), env={})
        out = cp.ask("赚了吗", decision_id=did_unrevealed, trace=True)
        self.assertEqual(out["tools_called"][0]["tool"], "get_post_trade_review")
        summary = out["tools_called"][0]["result_summary"]
        self.assertIn("OUTCOME_NOT_REVEALED", summary)
        for key in ("actual_return", "actual_da", "actual_rtpd"):
            self.assertNotIn(key, summary)
        self.assertFalse(leaked["found"], "LLM 消息中不得出现 actual_*")

    def test_post_trade_after_reveal_has_actual_in_tool_only(self):
        svc2 = make_svc()
        did_revealed = make_did(svc2, reveal=True)

        def responder(client, messages, tools):
            return {"text": "复盘结论见工具结果。"}

        cp = LLMCopilot(service=svc2, provider="mock",
                        llm_client=MockLlmClient(responder=responder), env={})
        out = cp.ask("赚了吗", decision_id=did_revealed)
        summary = out["tools_called"][0]["result_summary"]
        self.assertIn("REVEALED", summary)
        self.assertIn("actual_return", summary)  # reveal 后 actual 来自工具（真实数据）

    # ------------------------------------------------------------- 原生 tool calling：数字不可被 LLM 覆盖
    def test_native_tool_numbers_not_overridable(self):
        calls = {"n": 0}

        def responder(client, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"tool_calls": [{"id": "call_1", "tool": "get_decision",
                                        "arguments": {"decision_date": DD, "node": NODE,
                                                      "hour": HOUR}}]}
            return {"text": "The expected_return is 999 and the final recommendation is BUY_DA."}

        cp = LLMCopilot(service=self.svc, provider="mock",
                        llm_client=MockLlmClient(responder=responder), env={})
        out = cp.ask("tell me about this market", trace=True)  # 非预置 → 原生循环
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["tools_called"][0]["tool"], "get_decision")
        true_er = json.loads(out["tools_called"][0]["result_summary"])["model_output"]["expected_return"]
        self.assertNotEqual(true_er, 999.0)
        # LLM 声称的 999 不得出现在 answer 或工具结果里（trace 如实记录被拦截的原文，属审计信息）
        self.assertNotIn("999", out["answer"])
        self.assertNotIn("999", json.dumps(out["tools_called"], ensure_ascii=False))
        self.assertIn("数字/方向完整性校验未通过", out["answer"])
        # 守卫 trace 记录 BLOCKED
        self.assertTrue(any(s["stage"] == "guard" and s["status"] == "BLOCKED"
                            for s in out["trace"]["steps"]))

    def test_native_loop_quotes_tool_numbers_verbatim_passes_guard(self):
        calls = {"n": 0}
        truth = {}
        start_svc = make_svc()

        def responder(client, messages, tools):
            nonlocal truth
            calls["n"] += 1
            if calls["n"] == 1:
                return {"tool_calls": [{"id": "call_1", "tool": "get_decision",
                                        "arguments": {"decision_date": DD, "node": NODE,
                                                      "hour": HOUR}}]}
            return {"text": "The expected_return is %.6f and the final recommendation is %s." % (
                truth["expected_return"], truth["final"])}

        cp = LLMCopilot(service=start_svc, provider="mock",
                        llm_client=MockLlmClient(responder=responder), env={})
        # 先执行一次拿到真实值，作为 responder 引用的基准
        probe = cp._execute_tool("get_decision", {"decision_date": DD, "node": NODE, "hour": HOUR})
        truth["expected_return"] = probe["model_output"]["expected_return"]
        truth["final"] = probe["final_recommendation"]

        out = cp.ask("hello there, give me an overview of this market", trace=False)
        self.assertEqual(out["status"], "ok")
        self.assertNotIn("UNCERTAIN", out["answer"])

    # ------------------------------------------------------------- JSON Tool Router
    def test_router_json_fallback_program_executes_tool(self):
        calls = {"n": 0}

        def responder(client, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"text": json.dumps({"tool": "get_similar_cases",
                                            "arguments": {"decision_id": self.did}})}
            return {"text": "基于工具结果的回答，共找到案例。"}

        cp = LLMCopilot(service=self.svc, provider="mock",
                        llm_client=MockLlmClient(responder=responder),
                        use_router=True, env={})
        out = cp.ask("compare this to history", trace=True)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["tools_called"][0]["tool"], "get_similar_cases")
        summary = json.loads(out["tools_called"][0]["result_summary"])
        self.assertEqual(summary["status"], "ok")
        # 程序执行了 Tool：case 来自真实案例库
        self.assertIn("cases", summary)
        stages = [s["stage"] for s in out["trace"]["steps"]]
        self.assertIn("llm", stages)

    def test_router_rejects_unknown_tool_name(self):
        calls = {"n": 0}

        def responder(client, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"text": json.dumps({"tool": "not_a_real_tool",
                                            "arguments": {"decision_id": self.did}})}
            return {"text": "最终回答。"}

        cp = LLMCopilot(service=self.svc, provider="mock",
                        llm_client=MockLlmClient(responder=responder),
                        use_router=True, env={})
        out = cp.ask("anything", trace=True)
        # 非法 tool 名 → _parse_router_json 拒绝 → 视为最终回答
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["tools_called"], [])

    # ------------------------------------------------------------- 证据不足
    def test_insufficient_evidence_without_decision(self):
        svc_empty = make_svc()  # 未运行任何决策
        cp = LLMCopilot(service=svc_empty, provider="mock", env={})
        out = cp.ask("为什么不卖", trace=True)
        self.assertEqual(out["status"], "insufficient_evidence")
        self.assertIn("INSUFFICIENT", out["answer"].upper())

    # ------------------------------------------------------------- mock provider 完成三问
    def test_mock_provider_decision_provenance_similar(self):
        cp = LLMCopilot(service=self.svc, provider="mock", env={})
        # 1) decision explanation
        out = cp.ask("请解释这个决策", decision_id=self.did)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["answer"])
        self.assertTrue(any(t["tool"] == "get_decision" for t in out["tools_called"]))
        # 2) provenance question
        out = cp.ask("天气来源", decision_id=self.did)
        self.assertTrue(any(t["tool"] == "get_data_provenance" for t in out["tools_called"]))
        # 3) similar case question
        out = cp.ask("类似案例", decision_id=self.did)
        self.assertTrue(any(t["tool"] == "get_similar_cases" for t in out["tools_called"]))

    # ------------------------------------------------------------- 状态 / Trace 结构
    def test_copilot_status_contract(self):
        st = copilot_status(service=self.svc, provider="mock", env={})
        self.assertTrue(st["configured"])
        self.assertFalse(st["degraded"])
        self.assertEqual(st["provider"], "mock")
        self.assertIn("get_decision", st["tools"])
        self.assertFalse(st["system_prompt_constraints"]["llm_decides_trade"])

    def test_trace_has_no_private_cot(self):
        cp = LLMCopilot(service=self.svc, provider="mock", env={})
        out = cp.ask("为什么不卖", decision_id=self.did, trace=True)
        steps = out["trace"]["steps"]
        for s in steps:
            self.assertIn(s["stage"], {"route", "tool", "llm", "guard"})
        # 没有 “reasoning”/“thought” 之类字段
        blob = json.dumps(out["trace"], ensure_ascii=False).lower()
        self.assertNotIn("chain-of-thought", blob)
        self.assertNotIn("reasoning", blob)

    def test_trace_disabled_returns_none(self):
        cp = LLMCopilot(service=self.svc, provider="mock", env={})
        out = cp.ask("为什么不卖", decision_id=self.did, trace=False)
        self.assertIsNone(out["trace"])

    def test_tool_schemas_imported(self):
        for name in ("get_decision", "get_feature_explanation", "get_evidence",
                     "get_similar_cases", "get_data_provenance", "get_post_trade_review"):
            self.assertIn(name, TOOL_SCHEMAS)


if __name__ == "__main__":
    unittest.main()
