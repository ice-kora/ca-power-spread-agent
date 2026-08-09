# -*- coding: utf-8 -*-
"""
code/tests/test_v040_phase2.py —— V0.4 Phase 2 验收测试（需求 31 · 23 项）
============================================================================
Agent D · Testing：只写本文件，不改任何生产代码 / 其它测试，交易核心冻结。

页面源码 = 服务端 "/" HTML + 页面引用的 static/*.js + static/mvp.css
（V0.4 把业务渲染拆分到 mvp_core.js / mvp_agent.js / mvp_evidence.js，故页面断言
 读组合源码）。决策一律用 StaticEvidenceAdapter（offline）离线、确定性；
Agent streaming 的 LLM 最终回答用 mock 拦截（确定性、不依赖外部 API），
Tool 事件来自真实执行。

覆盖（需求 31 · 23 项）：
  R01  Recommendation Hero 正确（页面含 卖出日前/买入日前/不交易 + final 数据正确）
  R02  BUY UI（文案"买入日前"）
  R03  SELL UI（"卖出日前"）
  R04  NO_TRADE UI（"不交易"）
  R05  SELL 颜色 != error/loss 颜色（static/mvp.css：--sell 与 --err 色值不同；
       SELL=brick/深橙 #c2410c，REJECT/LOSS=真红 #dc2626）
  R06  expected spread 位于 Hero（页面含"预计 DA − RT"且 /api/decision expected_return 一致）
  R07  system status 不再占第一屏（Header 无巨大 MVP STATUS；仅紧凑 sys-strip）
  R08  S1/S2 等旧模块标题不存在于业务主界面
  R09  technical fields 默认隐藏（原始字段名不做主文案展示标签，只在 details 折叠内）
  R10  audit 默认折叠（"查看审计详情" details）
  R11  不默认展示 raw JSON（Raw JSON 只在折叠 details 内）
  R12  Agent streaming（/api/ask/stream SSE：agent_status/tool_start/tool_result/
       answer_delta/answer_done 事件序列）
  R13  真实 Tool trace（tool_start 的 tool 来自真实 plan）
  R14  预设问题路由（为什么/特征/证据/案例/来源/复盘 → 6 个 Tool）
  R15  Guard 仍生效（mock 覆盖数字 → BLOCKED）
  R16  Case E 时间轴（页面含"报价截止 10:00 PT"与 timeline/tg-grid 时间轴结构）
  R17  Case E NOT USED（"未参与决策 NOT USED" + "无法证明"）
  R18  Lock 状态机
  R19  Reveal 状态机 + REVEALED
  R20  复盘四问（发生了什么/为什么/有没有偷看未来/教训）
  R21  Golden Cases 不变（5 个 Golden 的 expected_return/final + meta 中文 label）
  R22  1440 无横向滚动（CSS：.wrap max-width + margin auto；body 无 overflow-x:auto/scroll）
  R23  1920 无横向滚动（CSS：.wrap 居中 max-width；.layout minmax(0,1fr)；响应式规则）
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

GOLDEN = [
    {"id": "B",  "decision_date": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3, "final": "SELL_DA",  "er": 59.7937},
    {"id": "C1", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "er": -76.1581},
    {"id": "C2", "decision_date": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10, "final": "NO_TRADE", "er": None},
    {"id": "D",  "decision_date": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20, "final": "SELL_DA",  "er": None},
    {"id": "E",  "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2, "final": "NO_TRADE", "er": -76.1581},
]


def _page_source() -> str:
    """页面源码 = 服务端 "/" HTML + 页面引用的 static/*.js + static/mvp.css。"""
    import mvp_web  # noqa: PLC0415
    html = mvp_web.app.test_client().get("/").get_data(as_text=True)
    parts = [html]
    for m in re.finditer(r'(?:src|href)="(/static/[^"]+)"', html):
        rel = m.group(1)
        p = REPO_ROOT / "static" / Path(rel).name
        if p.exists():
            parts.append("\n/* %s */\n" % rel + p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _read_css() -> str:
    return (REPO_ROOT / "static" / "mvp.css").read_text(encoding="utf-8")


def _strip_details(src: str) -> str:
    """去掉 <details>…</details> 折叠块后的源码（主文案模板）。"""
    return re.sub(r"<details\b.*?</details>", "", src, flags=re.S)


def _strip_comments(src: str) -> str:
    """去掉 HTML 注释（非渲染内容）。"""
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


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


def _css_var(css: str, name: str) -> str:
    m = re.search(r"--" + re.escape(name) + r"\s*:\s*([^;]+);", css)
    return m.group(1).strip() if m else ""


def _css_block(css: str, selector_fragment: str) -> str:
    """返回第一个选择器包含 fragment 的规则声明块（按文件顺序）。"""
    for part in css.split("}"):
        head, _, body = part.partition("{")
        if selector_fragment in head:
            return body
    return ""


def _body_rules(css: str):
    """返回选择器以 html/body 为目标的所有规则 [(selector, declarations), ...]。"""
    out = []
    for part in css.split("}"):
        head, _, body = part.partition("{")
        sel = head.strip()
        if not sel:
            continue
        targets = {t.strip() for t in sel.split(",")}
        if targets & {"html", "body"}:
            out.append((sel, body))
    return out


def _hex_rgb(v: str):
    v = str(v).strip().lower()
    if not v.startswith("#"):
        return None
    h = v[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    return None


class V040Phase2Tests(unittest.TestCase):
    """V0.4 Phase 2（需求 31）23 项验收。"""

    @classmethod
    def setUpClass(cls):
        info = resolve_data_mode()
        if not info.is_ready:
            raise unittest.SkipTest("数据不可用（mode=%s）：请先运行 prepare_mvp.py" % info.mode)

    # ================================================================= R01 ~ R04
    def test_r01_hero_recommendation_correct(self):
        """Recommendation Hero：页面含 卖出日前/买入日前/不交易，且 /api/decision final 正确。"""
        import mvp_web  # noqa: PLC0415
        src = _page_source()
        for zh in ("卖出日前", "买入日前", "不交易"):
            self.assertIn(zh, src, f"Hero 缺少中文动作 {zh}")
        self.assertIn('class="hero"', src, "缺 Hero 容器")
        self.assertIn("hero-spread", src, "缺 Hero Expected Spread 区")
        # Case B → SELL_DA，数字一致
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": "2026-07-16",
                                          "node": "CONTROLX_1_N001", "hour": 3, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["decision"]
        self.assertEqual(d["final_recommendation"], "SELL_DA")
        self.assertAlmostEqual(d["model_output"]["expected_return"], 59.7937, places=2)

    def test_r02_buy_ui(self):
        """BUY UI：主文案含"买入日前"，且 BUY_DA→买入日前 映射存在。"""
        src = _page_source()
        self.assertIn("买入日前", src, "BUY UI 缺中文文案")
        self.assertIsNotNone(re.search(r'BUY_DA\s*:\s*"买入日前"', src), "BUY_DA→买入日前 映射缺失")

    def test_r03_sell_ui(self):
        """SELL UI：主文案含"卖出日前"，且 SELL_DA→卖出日前 映射存在。"""
        src = _page_source()
        self.assertIn("卖出日前", src, "SELL UI 缺中文文案")
        self.assertIsNotNone(re.search(r'SELL_DA\s*:\s*"卖出日前"', src), "SELL_DA→卖出日前 映射缺失")

    def test_r04_no_trade_ui(self):
        """NO_TRADE UI：主文案含"不交易"，且 NO_TRADE→不交易 映射存在。"""
        src = _page_source()
        self.assertIn("不交易", src, "NO_TRADE UI 缺中文文案")
        self.assertIsNotNone(re.search(r'NO_TRADE\s*:\s*"不交易"', src), "NO_TRADE→不交易 映射缺失")

    # ================================================================= R05
    def test_r05_sell_color_differs_from_error(self):
        """SELL 颜色 != error/loss 颜色：--sell 与 --err 色值不同；SELL=brick/深橙系。"""
        css = _read_css()
        sell = _css_var(css, "sell")
        err = _css_var(css, "err")
        self.assertTrue(sell, "CSS 缺 --sell")
        self.assertTrue(err, "CSS 缺 --err")
        # 核心断言：--sell 与 --err 色值不同（大小写无关）
        self.assertNotEqual(sell.lower(), err.lower(), "--sell 与 --err 不得使用相同色值")
        rgb_sell = _hex_rgb(sell)
        rgb_err = _hex_rgb(err)
        self.assertIsNotNone(rgb_sell, f"--sell 非合法 hex: {sell}")
        self.assertIsNotNone(rgb_err, f"--err 非合法 hex: {err}")
        self.assertNotEqual(rgb_sell, rgb_err, "--sell 与 --err RGB 不得相同")
        # 视觉上必须可区分（不是仅 1 unit 的色差）
        dist = sum((a - b) ** 2 for a, b in zip(rgb_sell, rgb_err)) ** 0.5
        self.assertGreater(dist, 30.0, "SELL 与 REJECT/LOSS 颜色必须视觉可区分")
        # SELL=brick/深橙（暖色系，G>B），REJECT/LOSS=真红（G≈B）：防止 SELL 退回纯红
        _r_s, g_s, b_s = rgb_sell
        self.assertGreater(g_s, b_s, "--sell 应为 brick/深橙暖色（非纯红）")
        # 用途分离：SELL 动作色走 --sell；REJECT / LOSS 状态色走 --err
        self.assertIn("var(--sell)", _css_block(css, ".hero-rec.SELL"), "SELL Hero 应使用 --sell")
        self.assertIn("var(--err)", _css_block(css, ".pill.REJECT"), "REJECT pill 应使用 --err")
        self.assertIn("var(--err-bg)", _css_block(css, ".result-box.loss"), "LOSS 结果背景应使用 --err-bg")
        self.assertIn("var(--err)", _css_block(css, ".result-box.loss .r-title"), "LOSS 结果标题应使用 --err")

    # ================================================================= R06
    def test_r06_expected_spread_in_hero(self):
        """expected spread 位于 Hero：页面含"预计 DA − RT"，且与 /api/decision expected_return 一致。"""
        import mvp_web  # noqa: PLC0415
        src = _page_source()
        self.assertIn("预计 DA − RT", src, "Hero 缺 Expected Spread 标签")
        c = mvp_web.app.test_client()
        r = c.post("/api/decision", json={"decision_date": "2026-07-16",
                                          "node": "CONTROLX_1_N001", "hour": 3, "evidence": "offline"})
        d = r.get_json()["decision"]
        er = d["model_output"]["expected_return"]
        self.assertIsNotNone(er)
        self.assertAlmostEqual(er, 59.7937, places=2)
        # Hero 的价差统计块引用同一字段（页面从同一 decision 对象渲染）
        self.assertIn("mo.expected_return", src, "Hero 应引用 mo.expected_return")

    # ================================================================= R07
    def test_r07_system_status_compact(self):
        """system status 不再占第一屏：Header 无巨大 MVP STATUS；仅紧凑 sys-strip。"""
        src = _page_source()
        visible = _strip_comments(src)
        self.assertIsNone(re.search(r"\bMVP\s*STATUS\b", visible, re.I),
                          "不应出现 MVP STATUS 横幅（任意大小写，排除注释）")
        self.assertIn('class="sys-strip"', src, "应有紧凑系统状态条")
        self.assertIn("数据模式", src, "紧凑条内应有数据模式状态项")

    # ================================================================= R08
    def test_r08_no_legacy_module_titles(self):
        """S1/S2 等旧模块标题不存在于业务主界面。"""
        src = _page_source()
        for old in ("Decision Context", "Predictive Model"):
            self.assertNotIn(old, src, f"业务主界面不应存在旧模块标题 {old}")
        self.assertIsNone(re.search(r"\bS\d\b", src), "不应存在 S1/S2 等旧模块编号标题")
        # 新业务主界面标题存在（正向控制）
        for zh in ("为什么这样建议", "本次决策依据", "外部信息", "类似历史案例",
                   "锁定本次决策", "审计状态", "问交易 Agent"):
            self.assertIn(zh, src, f"缺新业务主界面标题 {zh}")

    # ================================================================= R09
    def test_r09_technical_fields_collapsed(self):
        """technical fields 默认隐藏：原始字段名不做主文案展示标签，只在 details 折叠内。"""
        src = _page_source()
        self.assertGreater(src.count('<details class="detail"'), 0, "应有默认折叠的 details")
        self.assertNotIn("<details open", src, "details 必须默认折叠（无 open 属性）")
        for anchor in ("查看数据血缘", "查看审计详情", "查看案例审计信息", "查看技术详情",
                       "模型原始输出", "技术详情"):
            self.assertIn(anchor, src, f"缺技术详情入口 {anchor}")
        main = _strip_details(src)  # 去掉 details 后的主文案模板
        for field in ("expected_return", "prob_positive", "prob_negative",
                      "direction_probability", "model_signal_strength", "uncertainty"):
            self.assertNotIn(">" + field + "<", main, f"{field} 不应作为主文案展示标签")
            self.assertNotIn(field + "</div>", main, f"{field} 不应作为主文案展示标签")

    # ================================================================= R10
    def test_r10_audit_collapsed(self):
        """Audit 默认折叠：只显示汇总，"查看审计详情" 位于 details 内。"""
        src = _page_source()
        self.assertIn("审计状态", src, "缺审计状态区块")
        self.assertIn('<details class="detail" style="margin:0"><summary>查看审计详情</summary>',
                      src, "审计详情必须位于默认折叠 details 内")

    # ================================================================= R11
    def test_r11_no_raw_json_default(self):
        """不默认展示 raw JSON：Raw JSON 只作为折叠 details 的摘要，不进主文案。"""
        src = _page_source()
        self.assertNotIn("arguments JSON", src)
        self.assertNotIn("result JSON", src)
        main = _strip_details(src)
        self.assertNotIn("<pre", main, "Raw JSON 的 <pre> 不应进入默认主文案")
        self.assertNotIn("Raw JSON（", main, "Raw JSON 摘要只应在折叠 details 内")

    # ================================================================= R12
    def test_r12_ask_stream_events(self):
        """/api/ask/stream SSE 事件序列：agent_status/tool_start/tool_result/answer_delta/answer_done。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        did = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                            "hour": HOUR, "evidence": "offline"}).get_json()["decision"]["decision_id"]
        # 拦截 LLM 最终回答以保证离线确定性；Tool 事件仍来自真实执行
        with mock.patch("code.llm_copilot.LLMCopilot._call_final_answer", return_value="确定性回答"):
            r = c.post("/api/ask/stream", json={"question": "为什么建议 SELL？", "decision_id": did})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/event-stream")
        events = _parse_sse(r.get_data(as_text=True))
        types = [e for e, _ in events]
        for t in ("agent_status", "tool_start", "tool_result", "answer_delta", "answer_done"):
            self.assertIn(t, types, f"缺少 SSE 事件 {t}")
        # 顺序：agent_status → tool_start → tool_result → answer_delta → answer_done
        self.assertLess(types.index("agent_status"), types.index("tool_start"))
        self.assertLess(types.index("tool_start"), types.index("tool_result"))
        self.assertLess(types.index("tool_result"), types.index("answer_delta"))
        self.assertEqual(types[-1], "answer_done", "流应以 answer_done 收尾")
        deltas = [d for e, d in events if e == "answer_delta"]
        self.assertGreaterEqual(len(deltas), 1, "应有 answer_delta 输出")

    # ================================================================= R13
    def test_r13_tool_trace_from_real_plan(self):
        """真实 Tool trace：tool_start 的 tool 来自真实 plan（工具真实执行）。"""
        from code.llm_copilot import LLMCopilot, MockLlmClient  # noqa: PLC0415
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([]))
        did = svc.run_decision(DD, NODE, HOUR)["decision_id"]
        cp = LLMCopilot(service=svc, llm_client=MockLlmClient(), env={})  # 离线确定性 mock LLM
        plan = cp._build_plan_for_route("get_decision", did, None)  # noqa: SLF001
        plan = plan if isinstance(plan, list) else [{"tool": "get_decision"}]
        evs = list(cp.ask_stream("为什么建议 SELL？", decision_id=did))
        starts = [d.get("tool") for e, d in evs if e == "tool_start"]
        results = [d.get("tool") for e, d in evs if e == "tool_result"]
        self.assertTrue(starts, "应有 tool_start")
        self.assertEqual(starts[0], plan[0]["tool"], "tool_start 必须来自真实 plan")
        self.assertEqual(starts, results, "tool_start 与 tool_result 应一一对应")

    # ================================================================= R14
    def test_r14_preset_tool_routing(self):
        """预设问题正确路由到 6 个 Tool（真实路由，非都调 get_decision）。"""
        from code.llm_copilot import _match_preset  # noqa: PLC0415
        cases = {
            "为什么建议 SELL？": "get_decision",
            "最重要的特征是什么？": "get_feature_explanation",
            "用了哪些外部证据？": "get_evidence",
            "有没有类似历史案例？": "get_similar_cases",
            "这些数据的来源是什么？": "get_data_provenance",
            "这笔交易为什么亏？": "get_post_trade_review",
        }
        for q, expect in cases.items():
            with self.subTest(question=q):
                self.assertEqual(_match_preset(q), expect, f"{q} 应路由到 {expect}")

    # ================================================================= R15
    def test_r15_streaming_guard_active(self):
        """Streaming 不绕过数字 Guard：LLM 覆盖最终数字 → BLOCKED。"""
        from code.llm_copilot import LLMCopilot, MockLlmClient  # noqa: PLC0415
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter([]))
        did = svc.run_decision(DD, NODE, HOUR)["decision_id"]
        cp = LLMCopilot(service=svc, llm_client=MockLlmClient(), env={})
        with mock.patch.object(cp, "_call_final_answer",
                               return_value="最终建议 SELL，expected_return = 9999.99"):  # 覆盖数字
            evs = list(cp.ask_stream("为什么建议 SELL？", decision_id=did))
        guard = [d for e, d in evs if e == "guard"]
        self.assertTrue(guard, "应有 guard 事件")
        self.assertEqual(guard[0].get("status"), "BLOCKED", "覆盖数字必须被拦截")
        deltas = "".join(d.get("text", "") for e, d in evs if e == "answer_delta")
        self.assertIn("一致性检查", deltas, "被拦截时应给出业务化提示")

    # ================================================================= R16
    def test_r16_case_e_time_axis(self):
        """Case E 时间轴：页面含"报价截止 10:00 PT"与 timeline/tg-grid 时间轴结构。"""
        src = _page_source()
        self.assertIn("报价截止", src, "缺报价截止锚点")
        self.assertIn("报价截止 10:00 PT", src, "缺报价截止时间轴标签")
        self.assertIn("timeline", src, "缺时间轴容器")
        self.assertIn("tg-grid", src, "缺时间轴网格结构")
        self.assertIn("它来晚了，所以没有使用", src, "缺 Case E 大字结论")

    # ================================================================= R17
    def test_r17_case_e_not_used(self):
        """Case E NOT USED：页面含"未参与决策 NOT USED"与"无法证明"。"""
        src = _page_source()
        self.assertIn("未参与决策 NOT USED", src, "缺 NOT USED 标签")
        self.assertIn("无法证明", src, "缺 AVAILABILITY_NOT_PROVEN 说明")

    # ================================================================= R18
    def test_r18_lock_state_machine(self):
        """Lock 状态机：锁定后 lock.locked=True。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        did = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                            "hour": HOUR, "evidence": "offline"}).get_json()["decision"]["decision_id"]
        self.assertIn("锁定本次决策", _page_source(), "页面缺锁定 CTA")
        r = c.post(f"/api/decision/{did}/lock")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "LOCKED")
        d = c.get(f"/api/decision/{did}").get_json()["decision"]
        self.assertTrue(d["lock"]["locked"], "锁定后决策应显示 lock.locked=True")

    # ================================================================= R19
    def test_r19_reveal_state_machine(self):
        """Reveal 状态机：锁定前拒绝（NOT_LOCKED）；锁定后 REVEALED。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        did = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                            "hour": HOUR, "evidence": "offline"}).get_json()["decision"]["decision_id"]
        # 未锁定先 reveal → 403 NOT_LOCKED
        r = c.post(f"/api/decision/{did}/reveal")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["status"], "NOT_LOCKED")
        # lock 后 reveal → REVEALED
        c.post(f"/api/decision/{did}/lock")
        r = c.post(f"/api/decision/{did}/reveal")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["post_trade"]["status"], "REVEALED")
        self.assertIn("揭晓真实结果", _page_source(), "页面缺揭晓 CTA")

    # ================================================================= R20
    def test_r20_review_four_questions(self):
        """复盘四问：发生了什么 / 为什么 / 有没有偷看未来信息 / 教训。"""
        src = _page_source()
        for q in ("发生了什么？", "为什么？", "有什么教训？"):
            self.assertIn(q, src, f"复盘缺四问之一: {q}")
        self.assertIn("系统当时有没有", src, "复盘缺'有没有偷看/使用未来信息'一问")

    # ================================================================= R21
    def test_r21_golden_cases_unchanged(self):
        """Golden Cases 不变：5 个 Golden 的 expected_return/final 不变，meta 中文 label 案例A~E。"""
        import mvp_web  # noqa: PLC0415
        c = mvp_web.app.test_client()
        meta = c.get("/api/meta").get_json()
        self.assertEqual(len(meta["golden_cases"]), 5, "应有 5 个 Golden Case")
        for g in meta["golden_cases"]:
            self.assertIn("案例", g.get("label", ""), f"Golden 缺中文 label: {g.get('id')}")
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

    # ================================================================= R22
    def test_r22_no_horizontal_scroll_1440(self):
        """1440 无横向滚动：.wrap max-width + margin auto；body 不强制横向滚动条。"""
        css = _read_css()
        self.assertIn("@media (max-width: 1100px)", css, "应有响应式规则")
        wrap = _css_block(css, ".wrap")
        self.assertIn("max-width", wrap, ".wrap 应使用 max-width 约束")
        self.assertIn("margin: 0 auto", wrap, ".wrap 应居中（margin auto）")
        # 页面级（html/body）不得设置强制横向滚动条（overflow-x hidden 属防溢出保护，允许）
        rules = _body_rules(css)
        self.assertTrue(rules, "CSS 应有 html/body 规则")
        for sel, body in rules:
            self.assertNotIn("overflow-x: auto", body, f"{sel} 不得强制横向滚动(auto)")
            self.assertNotIn("overflow-x: scroll", body, f"{sel} 不得强制横向滚动(scroll)")

    # ================================================================= R23
    def test_r23_no_horizontal_scroll_1920(self):
        """1920 无横向滚动：.wrap 居中 max-width；.layout 用 minmax(0,1fr) 防溢出。"""
        css = _read_css()
        wrap = _css_block(css, ".wrap")
        self.assertIn("max-width", wrap, ".wrap 应有 max-width（大屏被约束居中）")
        self.assertIn("margin: 0 auto", wrap, ".wrap 在 1920 下应居中")
        layout = _css_block(css, ".layout")
        self.assertIn("minmax(0", layout, ".layout 主列应允许收缩（minmax(0,…)）防 grid 溢出")
        self.assertIn("@media (max-width: 1100px)", css, "应有响应式降级规则")


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(V040Phase2Tests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("V0.4 Phase 2 验收测试（需求 31 · 23 项）")
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
