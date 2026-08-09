# -*- coding: utf-8 -*-
"""
code/tests/test_agent_memory.py

Session Memory Layer（Agent C · code/agent_memory.py）单元测试（unittest）。

覆盖（任务要求）：
  - create / append_message / get / list（updated_at 倒序）/ delete + JSON 持久化
  - 决策隔离：新 decision_id → 默认新会话；同 decision_id 复用；无跨交易污染
  - 阈值压缩（P3）：>12 条 / 总字符 >8000 触发；保留最近 6~8 条；rolling_summary 生成
  - 压缩 LLM 回调成功 / 失败降级（确定性结构化摘要）；不丢小历史
  - build_context 四段结构 + recent_messages 6~8 条完整
  - 安全边界：未 reveal 绝不注入 outcome；reveal 后才注入
  - to_dict / from_dict 序列化往返；模块级单例
  - decision_context_from_snapshot 便利助手（reveal 门控）

运行：python -m unittest code.tests.test_agent_memory -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from code.agent_memory import (  # noqa: E402
    AGENT_MEMORY_KEEP_RECENT,
    AGENT_MEMORY_KEEP_RECENT_MAX,
    AGENT_MEMORY_KEEP_RECENT_MIN,
    AGENT_MEMORY_MAX_CHARS,
    AGENT_MEMORY_MAX_MESSAGES,
    SessionManager,
    decision_context_from_snapshot,
    get_session_manager,
)


def make_sessions_dir(parent: str, name: str = "sessions") -> str:
    return os.path.join(parent, name)


class TestAgentMemory(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = make_sessions_dir(self._tmp.name)
        self.mgr = SessionManager(store_dir=self.store)

    def tearDown(self):
        self._tmp.cleanup()

    # ------------------------------------------------------------- CRUD
    def test_create_append_get(self):
        conv = self.mgr.create("DEC-A", title="A")
        self.assertEqual(conv["decision_id"], "DEC-A")
        self.assertEqual(conv["messages"], [])
        self.assertEqual(conv["rolling_summary"], "")
        self.assertEqual(conv["title"], "A")

        self.mgr.append_message(conv["conversation_id"],
                                {"role": "user", "content": "为什么不卖"})
        self.mgr.append_message(conv["conversation_id"],
                                {"role": "assistant", "content": "工具结果：当前建议不交易",
                                 "status": "ok", "trace": [{"step": 1}]})
        got = self.mgr.get(conv["conversation_id"])
        self.assertEqual(len(got["messages"]), 2)
        self.assertEqual(got["messages"][0]["role"], "user")
        self.assertEqual(got["messages"][1]["status"], "ok")
        self.assertEqual(got["messages"][1]["trace"], [{"step": 1}])
        # 自动字段
        self.assertIn("id", got["messages"][0])
        self.assertIn("created_at", got["messages"][0])

    def test_append_role_content_kwargs(self):
        conv = self.mgr.create("DEC-A2", title="kw")
        self.mgr.append_message(conv["conversation_id"], role="user", content="你好")
        got = self.mgr.get(conv["conversation_id"])
        self.assertEqual(got["messages"][0]["role"], "user")
        self.assertEqual(got["messages"][0]["content"], "你好")

    def test_append_invalid_role_raises(self):
        conv = self.mgr.create("DEC-A3", title="bad")
        with self.assertRaises(ValueError):
            self.mgr.append_message(conv["conversation_id"], {"role": "system", "content": "x"})

    def test_append_missing_conversation_raises(self):
        with self.assertRaises(KeyError):
            self.mgr.append_message("NOPE", {"role": "user", "content": "x"})

    def test_persistence_file(self):
        conv = self.mgr.create("DEC-X", title="X")
        self.mgr.append_message(conv["conversation_id"], {"role": "user", "content": "天气来源"})
        p = os.path.join(self.store, conv["conversation_id"] + ".json")
        self.assertTrue(os.path.exists(p))
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["messages"][0]["content"], "天气来源")
        self.assertEqual(data["decision_id"], "DEC-X")

    def test_list_sorted_by_updated_at_desc(self):
        c1 = self.mgr.create("DEC-1", title="one")
        c2 = self.mgr.create("DEC-2", title="two")
        self.mgr.append_message(c2["conversation_id"], {"role": "user", "content": "晚"})
        ids = [c["conversation_id"] for c in self.mgr.list()]
        self.assertEqual(ids, [c2["conversation_id"], c1["conversation_id"]])

    def test_delete(self):
        conv = self.mgr.create("DEC-D", title="del")
        self.assertTrue(self.mgr.delete(conv["conversation_id"]))
        self.assertIsNone(self.mgr.get(conv["conversation_id"]))
        self.assertFalse(self.mgr.delete(conv["conversation_id"]))

    # ------------------------------------------------------------- 决策隔离
    def test_decision_isolation(self):
        cA = self.mgr.get_or_create("DEC-A", title="A")
        self.mgr.append_message(cA["conversation_id"], {"role": "user", "content": "A 的问题"})
        cB = self.mgr.get_or_create("DEC-B", title="B")
        self.assertNotEqual(cB["conversation_id"], cA["conversation_id"])
        self.mgr.append_message(cB["conversation_id"], {"role": "user", "content": "B 的问题"})
        # A 会话不受 B 污染
        gotA = self.mgr.get(cA["conversation_id"])
        self.assertEqual(len(gotA["messages"]), 1)
        self.assertIn("A 的问题", gotA["messages"][0]["content"])
        # 同 decision_id 再次访问 → 复用原会话
        cA2 = self.mgr.get_or_create("DEC-A")
        self.assertEqual(cA2["conversation_id"], cA["conversation_id"])
        self.assertEqual(len(cA2["messages"]), 1)

    def test_bind_decision_changes_bound_object(self):
        conv = self.mgr.create("DEC-A", title="bind")
        conv = self.mgr.bind_decision(conv["conversation_id"], "DEC-NEW",
                                      {"node": "CONTROLX_1_N001", "hour": 2})
        self.assertEqual(conv["decision_id"], "DEC-NEW")
        self.assertEqual(conv["metadata"]["decision_context"]["node"], "CONTROLX_1_N001")

    # ------------------------------------------------------------- 阈值压缩
    def test_compress_by_message_count(self):
        conv = self.mgr.create("DEC-C", title="compress")
        n = AGENT_MEMORY_MAX_MESSAGES + 3   # 15 条 → 超 12 触发
        for i in range(n):
            self.mgr.append_message(
                conv["conversation_id"],
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"})
        got = self.mgr.get(conv["conversation_id"])
        # 压缩后窗口落在 6~8 条
        self.assertGreaterEqual(len(got["messages"]), AGENT_MEMORY_KEEP_RECENT_MIN)
        self.assertLessEqual(len(got["messages"]), AGENT_MEMORY_KEEP_RECENT_MAX)
        # rolling_summary 生成（含被压缩旧消息主题）
        self.assertTrue(got["rolling_summary"])
        self.assertIn("msg", got["rolling_summary"])
        # 最新消息完整保留
        self.assertEqual(got["messages"][-1]["content"], f"msg {n - 1}")
        # 最早旧消息已被压缩删除
        self.assertNotIn("msg 0", [m["content"] for m in got["messages"]])

    def test_compress_by_char_threshold(self):
        conv = self.mgr.create("DEC-CC", title="chars")
        msgs = ["s0", "长" * 9000, "s2", "s3", "s4", "s5", "s6", "s7"]
        for m in msgs:
            self.mgr.append_message(conv["conversation_id"],
                                    {"role": "user", "content": m})
        got = self.mgr.get(conv["conversation_id"])
        # 字符超限触发压缩；保留最近 6~8 条；旧消息进入 rolling_summary
        self.assertLessEqual(len(got["messages"]), AGENT_MEMORY_KEEP_RECENT_MAX)
        self.assertGreaterEqual(len(got["messages"]), AGENT_MEMORY_KEEP_RECENT_MIN)
        self.assertTrue(got["rolling_summary"])
        self.assertNotIn("s0", [m["content"] for m in got["messages"]])
        self.assertEqual(got["messages"][-1]["content"], "s7")

    def test_compress_does_not_destroy_small_recent_history(self):
        conv = self.mgr.create("DEC-CCC", title="small")
        big = "长" * AGENT_MEMORY_MAX_CHARS
        self.mgr.append_message(conv["conversation_id"], {"role": "user", "content": big})
        self.mgr.append_message(conv["conversation_id"], {"role": "assistant", "content": big})
        got = self.mgr.get(conv["conversation_id"])
        # 仅 2 条且 ≤ 6：无旧消息可压缩 → 不丢任何内容
        self.assertEqual(len(got["messages"]), 2)
        self.assertEqual(got["messages"][0]["content"], big)

    def test_compress_llm_callback(self):
        mgr = SessionManager(store_dir=make_sessions_dir(self._tmp.name, "s2"),
                             summary_callback=lambda c, old: "LLM 摘要：用户关注天气来源与证据可用性。")
        conv = mgr.create("DEC-L", title="llm")
        for i in range(AGENT_MEMORY_MAX_MESSAGES + 1):
            mgr.append_message(conv["conversation_id"],
                               {"role": "user", "content": f"主题{i}"})
        self.assertIn("LLM 摘要", mgr.get(conv["conversation_id"])["rolling_summary"])

    def test_compress_callback_failure_falls_back(self):
        def bad_cb(c, old):
            raise RuntimeError("LLM down")
        mgr = SessionManager(store_dir=make_sessions_dir(self._tmp.name, "s3"),
                             summary_callback=bad_cb)
        conv = mgr.create("DEC-F", title="fallback")
        for i in range(AGENT_MEMORY_MAX_MESSAGES + 1):
            mgr.append_message(conv["conversation_id"],
                               {"role": "user", "content": f"主题{i}"})
        rs = mgr.get(conv["conversation_id"])["rolling_summary"]
        self.assertIn("用户正在分析", rs)
        self.assertIn("已确认", rs)
        # 摘要带非权威前缀，不冒充交易事实
        self.assertIn("[会话记忆摘要", rs)

    # ------------------------------------------------------------- build_context
    def test_build_context_structure(self):
        conv = self.mgr.create("DEC-K", title="ctx")
        self.mgr.append_message(conv["conversation_id"], {"role": "user", "content": "q1"})
        self.mgr.append_message(conv["conversation_id"], {"role": "assistant", "content": "a1"})
        ctx = self.mgr.build_context(self.mgr.get(conv["conversation_id"]))
        self.assertEqual(set(ctx.keys()), {
            "system_constraints", "decision_context_summary",
            "rolling_summary", "recent_messages"})
        self.assertEqual(len(ctx["recent_messages"]), 2)
        self.assertTrue(ctx["system_constraints"])

    def test_build_context_recent_clamp(self):
        conv = self.mgr.create("DEC-R", title="recent")
        for i in range(10):
            self.mgr.append_message(conv["conversation_id"],
                                    {"role": "user" if i % 2 == 0 else "assistant",
                                     "content": f"m{i}"})
        ctx = self.mgr.build_context(self.mgr.get(conv["conversation_id"]))
        self.assertEqual(len(ctx["recent_messages"]), AGENT_MEMORY_KEEP_RECENT)
        self.assertEqual(ctx["recent_messages"][-1]["content"], "m9")
        # 请求超过上限 → 夹取到 8
        ctx8 = self.mgr.build_context(self.mgr.get(conv["conversation_id"]), recent=99)
        self.assertEqual(len(ctx8["recent_messages"]), AGENT_MEMORY_KEEP_RECENT_MAX)
        # 请求低于下限 → 夹取到 6
        ctx1 = self.mgr.build_context(self.mgr.get(conv["conversation_id"]), recent=1)
        self.assertEqual(len(ctx1["recent_messages"]), AGENT_MEMORY_KEEP_RECENT_MIN)

    # ------------------------------------------------------------- 安全边界
    def test_unrevealed_never_injects_outcome(self):
        conv = self.mgr.create("DEC-S", title="safety")
        dctx = {
            "node": "CONTROLX_1_N001", "target_date": "2026-07-09", "hour": 2,
            "zone": "ZP26", "final_recommendation": "BUY_DA",
            "outcome_revealed": False,
            # 即使误传 outcome，也不得注入
            "outcome": {"actual_da": 999.0, "actual_rtpd": 998.0, "pnl": 123.0},
            "actual_da": 999.0, "pnl": 123.0,
        }
        self.mgr.set_decision_context(conv["conversation_id"], dctx)
        ctx = self.mgr.build_context(self.mgr.get(conv["conversation_id"]))
        summary = ctx["decision_context_summary"]
        self.assertIn("决策对象", summary)
        self.assertIn("BUY_DA", summary)
        self.assertIn("OUTCOME_NOT_REVEALED", summary)
        self.assertNotIn("999", summary)
        self.assertNotIn("123", summary)

    def test_revealed_injects_outcome(self):
        conv = self.mgr.create("DEC-RV", title="revealed")
        dctx = {
            "node": "CONTROLX_1_N001", "target_date": "2026-07-09", "hour": 2,
            "zone": "ZP26", "final_recommendation": "BUY_DA",
            "outcome_revealed": True,
            "outcome": {"actual_da": 999.0, "actual_rtpd": 998.0, "pnl": 123.0},
        }
        self.mgr.set_decision_context(conv["conversation_id"], dctx)
        summary = self.mgr.build_context(self.mgr.get(conv["conversation_id"]))[
            "decision_context_summary"]
        self.assertIn("999", summary)
        self.assertIn("123", summary)
        self.assertNotIn("OUTCOME_NOT_REVEALED", summary)

    # ------------------------------------------------------------- 序列化 / 单例
    def test_to_dict_from_dict(self):
        c1 = self.mgr.create("DEC-T1", title="one")
        self.mgr.append_message(c1["conversation_id"], {"role": "user", "content": "q"})
        c2 = self.mgr.create("DEC-T2", title="two")
        data = self.mgr.to_dict()
        self.assertIn(c1["conversation_id"], data)
        self.assertIn(c2["conversation_id"], data)

        mgr2 = SessionManager(store_dir=make_sessions_dir(self._tmp.name, "s4"))
        self.assertEqual(mgr2.from_dict(data), 2)
        got = mgr2.get(c1["conversation_id"])
        self.assertEqual(len(got["messages"]), 1)
        self.assertEqual(got["messages"][0]["content"], "q")

    def test_singleton(self):
        a = get_session_manager()
        b = get_session_manager()
        self.assertIs(a, b)

    def test_decision_context_from_snapshot_gate(self):
        snap = {
            "context": {"decision_date": "2026-07-08", "target_date": "2026-07-09",
                        "hour": 2, "node": "CONTROLX_1_N001", "zone": "ZP26"},
            "final_recommendation": "NO_TRADE",
            "outcome_revealed": False,
            "outcome": {"actual_da": 100.0, "pnl": -5.0},
        }
        dctx = decision_context_from_snapshot(snap)
        self.assertFalse(dctx["outcome_revealed"])
        self.assertNotIn("actual_da", dctx)   # 未 reveal → 不透传 actual_*

        snap["outcome_revealed"] = True
        dctx2 = decision_context_from_snapshot(snap)
        self.assertTrue(dctx2["outcome_revealed"])
        self.assertEqual(dctx2["actual_da"], 100.0)
        self.assertEqual(dctx2["pnl"], -5.0)


if __name__ == "__main__":
    unittest.main()
