# -*- coding: utf-8 -*-
"""
code/tests/test_v0320_biz_ui.py —— V0.3.2 Business Demo UI Redesign 20 项验收（Agent Lead）
=============================================================================================

覆盖（需求 34 · 20 项）：
  U1  BUY/SELL/NO_TRADE Hero Card 逻辑正确（页面含中文动作 + final 数据正确）
  U2  中文业务标签正确（页面主语言为中文，专业术语加中文备注）
  U3  原始字段默认折叠（details 折叠 + 技术详情入口）
  U4  Agent streaming 正常（/api/ask/stream 事件序列）
  U5  answer_delta 逐步显示（多块 answer_delta）
  U6  Tool status 与真实 Tool 调用一致（tool_start 来自真实 plan）
  U7  Agent 不展示 private chain-of-thought
  U8  不默认展示 raw JSON（默认业务化，技术 JSON 折叠）
  U9  "为什么建议" → get_decision
  U10 特征问题 → get_feature_explanation
  U11 证据问题 → get_evidence
  U12 相似案例 → get_similar_cases
  U13 Provenance → get_data_provenance
  U14 Reveal 后复盘 → get_post_trade_review
  U15 Reveal 前 post_trade tool 仍拒绝（OUTCOME_NOT_REVEALED）
  U16 Streaming 不绕过数字 Guard（LLM 覆盖数字 → BLOCKED）
  U17 Case E 清楚显示 NOT USED
  U18 Audit 默认折叠
  U19 Lock → Reveal 正常
  U20 重构前后 Golden Case 数字一致

全部测试离线、确定性（决策用 StaticEvidenceAdapter / 页面静态断言）。
"""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.decision_service import DecisionService, StaticEvidenceAdapter  # noqa: E402
from data_mode import resolve_data_mode  # noqa: E402

DD = "2026-07-08"
NODE = "CONTROLX_1_N001"
HOUR = 2
CUTOFF = "2026-07-08T17:00:00"

GOLDEN = [
    {"id": "B",  "decision_date": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3, "final": "SELL_DA",  "er": 59.7937},
    {"id": "C1", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "er": -76.1581},
    {"id": "C2", "decision_date": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10, "final": "NO_TRADE", "er": None},
    {"id": "D",  "decision_date": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20, "final": "SELL_DA",  "er": None},
    {"id": "E",  "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "er": -76.1581},
]


def _page() -> str:
    """V0.4 拆分架构：页面源码 = templates/mvp_index.html + static/*.js 拼接。"""
    import mvp_web  # noqa: PLC0415
    parts = [mvp_web.app.test_client().get("/").get_data(as_text=True)]
    for js in ("mvp_core.js", "mvp_agent.js", "mvp_evidence.js"):
        p = REPO_ROOT / "static" / js
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _parse_sse(body: str):
    events = []
    for block in re.split(r"\n\n+", body):
        ev, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if ev != "message" or data:
            try:
                events.append((ev, json.loads(data) if data else {}))
            except Exception:
                events.append((ev, {"_raw": data}))
    return events


class V0320BizUITests(unittest.TestCase):
    """V0.3.2 Business Demo UI Redesign 20 项验收。"""

    @classmethod
    def setUpClass(cls):
        info = resolve_data_mode()
        if not info.is_ready:
            raise unittest.SkipTest("数据不可用（mode=%s）：请先运行 prepare_mvp.py" % info.mode)

    # ============================================================= U1 / U2 / U3
    def test_u1_hero_card_logic_and_data(self):
        """Hero Card：页面含中文动作（卖出日前/买入日前/不交易），且 /api/decision 数据正确。"""
        import mvp_web  # noqa: PLC0415
        html = _page()
        for zh in ("卖出日前", "买入日前", "不交易", "预计 DA − RT", "风险检查", "模型信号", "截止前可用证据"):
            self.assertIn(zh, html, f"Hero 缺少中文标签 {zh}")
        # Case B → SELL_DA，数字一致
        r = mvp_web.app.test_client().post(
            "/api/decision", json={"decision_date": "2026-07-16", "node": "CONTROLX_1_N001",
                                   "hour": 3, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["decision"]
        self.assertEqual(d["final_recommendation"], "SELL_DA")
        self.assertAlmostEqual(d["model_output"]["expected_return"], 59.7937, places=2)

    def test_u2_chinese_business_labels(self):
        """主界面以中文业务语言为主，不默认满屏字段名。"""
        html = _page()
        for zh in ("生成交易建议", "为什么这样建议", "本次决策依据", "外部信息",
                   "类似历史案例", "锁定本次决策", "问交易 Agent", "审计状态", "系统边界"):
            self.assertIn(zh, html, f"缺中文业务标签 {zh}")
        # 旧的英文模块平铺标题不应作为默认主标题
        for old in ("Decision Context（决策上下文）", "Predictive Model（预测模型"):
            self.assertNotIn(old, html, f"不应默认使用旧英文模块标题 {old}")

    def test_u3_raw_fields_collapsed_by_default(self):
        """原始技术字段默认折叠（details）；不默认铺开 expected_return 等。"""
        html = _page()
        self.assertGreater(html.count("<details class=\"detail\""), 0, "应有默认折叠的 details")
        for anchor in ("模型原始输出", "查看数据血缘", "查看审计详情", "查看案例审计信息", "查看技术 Trace"):
            self.assertIn(anchor, html, f"缺技术详情入口 {anchor}")
        # 技术字段仅存在于 JS 数据引用，不作为默认平铺大标题
        self.assertNotIn("model_signal_strength</h2>", html)

    # ============================================================= U4 / U5 / U6 / U7
    def test_u4_u5_streaming_events_and_delta(self):
        """Agent streaming：/api/ask/stream 返回 SSE 事件序列，answer_delta 多块逐步。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        did = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                            "hour": HOUR, "evidence": "offline"}).get_json()["decision"]["decision_id"]
        r = c.post("/api/ask/stream", json={"question": "为什么建议 SELL？", "decision_id": did})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/event-stream")
        body = r.get_data(as_text=True)
        events = _parse_sse(body)
        ev_types = [e for e, _ in events]
        self.assertIn("agent_status", ev_types)
        self.assertIn("tool_start", ev_types)
        self.assertIn("tool_result", ev_types)
        self.assertIn("answer_done", ev_types)
        deltas = [d for e, d in events if e == "answer_delta"]
        self.assertGreaterEqual(len(deltas), 3, "answer_delta 应分多块逐步输出")

    def test_u6_tool_status_matches_real_plan(self):
        """Tool status 与真实 Tool 调用一致（tool_start.tool 来自确定性 plan）。"""
        from code.llm_copilot import LLMCopilot  # noqa: PLC0415
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([]))
        did = svc.run_decision(DD, NODE, HOUR)["decision_id"]
        cp = LLMCopilot(service=svc)
        # 预置问题 → 确定性 plan（带 decision_id）
        plan = cp._build_plan_for_route("get_decision", did, None)  # noqa: SLF001
        plan = plan if isinstance(plan, list) else [{"tool": "get_decision"}]
        evs = list(cp.ask_stream("为什么建议 SELL？", decision_id=did))
        starts = [d.get("tool") for e, d in evs if e == "tool_start"]
        self.assertTrue(starts, "应有 tool_start")
        self.assertEqual(starts[0], plan[0]["tool"], "tool_start 必须来自真实 plan")
        # 每个 tool_start 有对应的 tool_result
        results = [d.get("tool") for e, d in evs if e == "tool_result"]
        self.assertEqual(starts, results, "tool_start 与 tool_result 应一一对应")

    def test_u7_no_private_chain_of_thought(self):
        """Agent 不展示 LLM private chain-of-thought（事件无 thinking/CoT 字段；页面仅作声明）。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        did = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                            "hour": HOUR, "evidence": "offline"}).get_json()["decision"]["decision_id"]
        body = c.post("/api/ask/stream", json={"question": "为什么建议 SELL？", "decision_id": did}).get_data(as_text=True)
        for bad in ("thinking", "chain_of_thought", "private"):
            self.assertNotIn(bad, body.lower(), f"不应暴露 private CoT 字段 {bad}")
        # 页面只声明"不展示私有思考过程"，不展示任何推理内容
        html = _page()
        self.assertIn("不展示模型私有思考过程", html)

    # ============================================================= U8
    def test_u8_no_raw_json_by_default(self):
        """不默认展示 raw JSON（技术 JSON 折叠在 details 内，默认业务化）。"""
        html = _page()
        self.assertNotIn("arguments JSON", html)
        self.assertNotIn("result JSON", html)
        self.assertIn("查看数据血缘", html)     # 技术详情折叠在 details 内

    # ============================================================= U9 ~ U14
    def test_u9_u14_preset_tool_routing(self):
        """预设问题正确路由到对应 Tool（真实路由，非都调 get_decision）。"""
        from code.llm_copilot import _match_preset  # noqa: PLC0415
        cases = {
            "为什么建议 SELL？": "get_decision",
            "最重要的特征是什么？": "get_feature_explanation",
            "用了哪些外部证据？": "get_evidence",
            "有没有类似历史案例？": "get_similar_cases",
            "这些数据的来源是什么？": "get_data_provenance",
        }
        for q, expect in cases.items():
            with self.subTest(q=q):
                self.assertEqual(_match_preset(q), expect, f"{q} 应路由到 {expect}")
        # Reveal 后复盘 → get_post_trade_review
        self.assertEqual(_match_preset("这笔为什么亏？"), "get_post_trade_review")

    # ============================================================= U15
    def test_u15_post_trade_tool_refused_before_reveal(self):
        """Reveal 前 get_post_trade_review 仍拒绝（OUTCOME_NOT_REVEALED）。"""
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([]))
        did = svc.run_decision(DD, NODE, HOUR)["decision_id"]
        out = svc.get_post_trade_review(did)
        self.assertEqual(out.get("status"), "OUTCOME_NOT_REVEALED")
        self.assertNotIn("actual_da", out)

    # ============================================================= U16
    def test_u16_streaming_guard_not_bypassed(self):
        """Streaming 不绕过数字 Guard：LLM 覆盖最终数字 → BLOCKED。"""
        from code.llm_copilot import LLMCopilot, MockLlmClient  # noqa: PLC0415
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([]))
        did = svc.run_decision(DD, NODE, HOUR)["decision_id"]
        cp = LLMCopilot(service=svc, llm_client=MockLlmClient(), env={})
        with mock.patch.object(cp.client, "chat_stream", create=True,
                               return_value=iter(["最终建议 SELL，expected_return = 9999.99"])):  # 覆盖数字
            evs = list(cp.ask_stream("为什么建议 SELL？", decision_id=did))
            guard = [d for e, d in evs if e in ("guard", "guard_result")]
            self.assertTrue(guard, "应有 guard 事件")
            self.assertEqual(guard[0].get("status"), "BLOCKED", "覆盖数字必须被拦截")
            deltas = "".join(d.get("text", "") for e, d in evs if e == "answer_delta")
            self.assertTrue(deltas, "应有 answer_delta（被拦截内容由前端替换为提示）")

    # ============================================================= U17 / U18
    def test_u17_case_e_not_used(self):
        """Case E 清楚显示 NOT USED（页面含"未参与决策 NOT USED" + "无法证明"）。"""
        html = _page()
        self.assertIn("未参与决策 NOT USED", html)
        self.assertIn("无法证明", html)
        self.assertIn("报价截止", html)

    def test_u18_audit_collapsed(self):
        """Audit 默认折叠：只显示汇总，详情在 details 内。"""
        html = _page()
        self.assertIn("查看审计详情", html)
        self.assertIn("审计状态", html)

    # ============================================================= U19
    def test_u19_lock_reveal_normal(self):
        """Lock → Reveal 完整流程正常（页面含按钮 + 状态机数据正确）。"""
        import mvp_web  # noqa: PLC0415
        html = _page()
        self.assertIn("锁定本次决策", html)
        self.assertIn("揭晓真实结果", html)
        c = mvp_web.app.test_client()
        did = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                            "hour": HOUR, "evidence": "offline"}).get_json()["decision"]["decision_id"]
        self.assertEqual(c.post(f"/api/decision/{did}/lock").status_code, 200)
        r = c.post(f"/api/decision/{did}/reveal")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["post_trade"]["status"], "REVEALED")

    # ============================================================= U20
    def test_u20_golden_case_numbers_unchanged(self):
        """重构前后 Golden Case 数字一致（expected_return / final 不变）。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        for g in GOLDEN:
            with self.subTest(case=g["id"]):
                r = c.post("/api/decision", json={"decision_date": g["decision_date"],
                                                  "node": g["node"], "hour": g["hour"],
                                                  "evidence": "offline"})
                self.assertEqual(r.status_code, 200)
                d = r.get_json()["decision"]
                self.assertEqual(d["final_recommendation"], g["final"], g["id"])
                if g["er"] is not None:
                    self.assertAlmostEqual(d["model_output"]["expected_return"], g["er"], places=2, msg=g["id"])


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(V0320BizUITests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("V0.3.2 Business Demo UI Redesign 验收测试（20 项）")
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
