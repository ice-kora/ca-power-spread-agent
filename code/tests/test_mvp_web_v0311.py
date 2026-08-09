# -*- coding: utf-8 -*-
"""
code/tests/test_mvp_web_v0311.py —— V0.3.1.1 Web 加固验收测试（Agent C）
================================================================================

覆盖（V0.3.1.1 任务验收）：
  T1  MVP Status 渲染：/api/meta.mvp_status 六项诚实状态栏 + 首页包含状态容器。
  T2  Audit Panel 真实运行审计：消费 DecisionService 真实 runtime audit（5 项
      PASS/FAIL/WARNING + checked/failed），OVERALL 由真实结果推导，绝不写死 PASS。
  T3  错误路径：所有 API 错误返回 ERROR CODE + Human-readable message + Suggested action，
      响应中绝无 Python traceback。
  T4  No Prediction：模型无输出 → NO_PREDICTION 结构化警告（ERROR CODE + action）。
  T5  Evidence Source Unavailable：real 模式无可用证据 → EVIDENCE_SOURCE_UNAVAILABLE 警告。
  T6  LLM Unavailable：Ask 面板降级时附 LLM_UNAVAILABLE 结构化错误，不影响交易流程。
  T7  Data Sources 页面：诚实标注"对账过 OASIS ≠ 当前全部数据实时来自 OASIS"。

运行（仓库根目录）：
    python code/tests/test_mvp_web_v0311.py
    python -m unittest code.tests.test_mvp_web_v0311 -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mvp_web  # noqa: E402

DD = "2026-07-08"
NODE = "CONTROLX_1_N001"
HOUR = 2


class TestMvpWebV0311(unittest.TestCase):
    """V0.3.1.1 Web 加固验收。"""

    @classmethod
    def setUpClass(cls):
        try:
            from data_mode import resolve_data_mode
            info = resolve_data_mode()
            if not info.is_ready:
                raise unittest.SkipTest(f"数据不可用（mode={info.mode}）：请先运行 prepare_mvp.py")
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"data_mode 解析失败: {exc}")

    def setUp(self):
        self.client = mvp_web.app.test_client()

    # ------------------------------------------------------------- T1 MVP Status
    def test_t1_mvp_status_rendered_in_meta_and_page(self):
        meta = self.client.get("/api/meta").get_json()
        s = meta.get("mvp_status") or {}
        self.assertEqual(s.get("model_alpha"), "WEAK")
        self.assertEqual(s.get("profitability_verified"), "NO")
        self.assertIn(s.get("data_mode"), ("FULL", "DEMO"))
        self.assertIn(s.get("llm"), ("CONNECTED", "NOT CONFIGURED"))
        self.assertEqual(s.get("auto_trading"), "DISABLED")
        self.assertEqual(s.get("settlement"), "SIMPLIFIED SIGNAL BACKTEST")
        # V0.3.1.3：统一展示 Demo Freeze 版本（不再出现历史版本号）
        self.assertTrue(str(meta.get("web_version", "")).startswith("V0.3.1.2"))
        # 首页静态 HTML 包含 MVP Status 容器与渲染函数（值由 /api/meta 动态填充）
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="mvp-status"', page)
        self.assertIn('id="mvp-status-grid"', page)
        self.assertIn("renderMvpStatus", page)

    # ------------------------------------------------------------- T2 Runtime Audit
    def test_t2_audit_panel_consumes_real_runtime_audit(self):
        r = self.client.post("/api/decision", json={
            "decision_date": DD, "node": NODE, "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        dec = r.get_json()["decision"]
        rt = dec["audit"]["runtime"]
        items = rt["items"]
        # 5 项真实运行审计（Agent B 的 DecisionSnapshot check_list 结构）
        self.assertGreaterEqual(len(items), 5)
        keys = {it["key"] for it in items}
        self.assertTrue({"feature_eligibility", "evidence_time_gate", "mock_data",
                         "case_asof", "outcome_leakage"}.issubset(keys))
        for it in items:
            self.assertIn(it["status"], ("PASS", "FAIL", "WARNING"))
            self.assertIsInstance(it["checked"], int)
            self.assertIsInstance(it["failed"], int)
            self.assertGreaterEqual(it["failed"], 0)
        # OVERALL 由真实结果推导（与 _overall_from_items 一致），不写死 PASS
        self.assertEqual(rt["overall"], mvp_web._overall_from_items(items))
        self.assertIn(rt["overall"], ("PASS", "FAIL", "WARNING"))

    def test_t2b_audit_not_hardcoded_fail_on_mock(self):
        """构造含 MOCK 的决策 → NO_MOCK=FAIL，OVERALL=FAIL（证明不写死 PASS）。"""
        dec = {
            "context": {"decision_date": DD, "target_date": "2026-07-09",
                        "decision_cutoff_utc": "2026-07-08T17:00:00"},
            "top_features": [{"feature": "spread_lag1", "decision_eligible": True,
                              "availability_basis": "STRUCTURAL_LAG", "is_mock": False}],
            "evidence": {"eligible": [{"evidence_id": "E1", "is_mock": True,
                                       "published_at": "2026-07-08T09:00:00"}],
                         "rejected": []},
            "top_cases": [],
            "post_trade": {"status": "OUTCOME_NOT_REVEALED"},
            "outcome_revealed": False,
        }
        rt = mvp_web._compute_runtime_audit(dec)
        mock_item = next(i for i in rt["items"] if i["key"] == "NO_MOCK")
        self.assertEqual(mock_item["status"], "FAIL")
        self.assertEqual(mock_item["failed"], 1)
        self.assertEqual(rt["overall"], "FAIL")

    def test_t2c_audit_consumes_agent_b_checklist(self):
        """服务端提供 audit.check_list 时直接消费（不降级本地计算）。"""
        audit = {"overall": "WARNING", "check_list": [
            {"check_id": "feature_eligibility", "label": "Feature Eligibility",
             "status": "PASS", "checked_count": 3, "failed_count": 0, "reason": "ok"},
            {"check_id": "mock_data", "label": "Mock Data",
             "status": "WARNING", "checked_count": 5, "failed_count": 0, "reason": "1 mock rejected"},
        ]}
        dec = {"audit": audit, "context": {"decision_date": DD, "target_date": "2026-07-09",
                                           "decision_cutoff_utc": "2026-07-08T17:00:00"}}
        rt = mvp_web._runtime_audit(dec)
        self.assertEqual(rt["overall"], "WARNING")
        self.assertEqual(len(rt["items"]), 2)
        self.assertEqual(rt["items"][0]["key"], "feature_eligibility")
        self.assertEqual(rt["items"][1]["checked"], 5)

    # ------------------------------------------------------------- T3 Error paths
    def _assert_structured_error(self, r, expected_code, expected_http):
        self.assertEqual(r.status_code, expected_http)
        self.assertNotIn(b"Traceback", r.data)
        self.assertNotIn(b"File \"", r.data)
        j = r.get_json()
        self.assertEqual(j["status"], "error")
        err = j["error"]
        self.assertEqual(err["code"], expected_code)
        self.assertTrue(err["message"])
        self.assertTrue(err["suggested_action"])
        return err

    def test_t3_error_paths_structured_no_traceback(self):
        c = self.client
        # 缺失参数
        self._assert_structured_error(c.post("/api/decision", json={}),
                                      "INVALID_REQUEST", 400)
        # 非法小时
        self._assert_structured_error(
            c.post("/api/decision", json={"decision_date": DD, "node": NODE, "hour": 99}),
            "INVALID_HOUR", 400)
        # 未知节点
        self._assert_structured_error(
            c.post("/api/decision", json={"decision_date": DD, "node": "NOPE_N001", "hour": 2}),
            "UNSUPPORTED_NODE", 400)
        # 不支持日期
        self._assert_structured_error(
            c.post("/api/decision", json={"decision_date": "2025-01-01", "node": NODE, "hour": 2}),
            "UNSUPPORTED_DATE", 400)
        # 不存在的决策
        self._assert_structured_error(c.get("/api/decision/DEC-NOPE"), "NOT_FOUND", 404)
        # Ask 空问题
        self._assert_structured_error(c.post("/api/ask", json={"question": ""}),
                                      "INVALID_REQUEST", 400)
        # 未锁定即 reveal → NOT_LOCKED（带结构化 error）
        r = c.post("/api/decision", json={"decision_date": DD, "node": NODE,
                                          "hour": HOUR, "evidence": "offline"})
        did = r.get_json()["decision"]["decision_id"]
        r = c.post(f"/api/decision/{did}/reveal")
        self.assertEqual(r.status_code, 403)
        j = r.get_json()
        self.assertEqual(j["status"], "NOT_LOCKED")
        self.assertEqual(j["error"]["code"], "NOT_LOCKED")
        self.assertIn("LOCK", j["error"]["suggested_action"])

    def test_t3b_missing_artifact_error(self):
        with mock.patch.object(mvp_web, "service", side_effect=FileNotFoundError(
                "code/data/canonical.parquet")):
            r = self.client.post("/api/decision", json={
                "decision_date": DD, "node": NODE, "hour": HOUR})
        self._assert_structured_error(r, "MISSING_ARTIFACT", 500)

    def test_t3c_unhandled_exception_returns_readable_json(self):
        with mvp_web.app.test_request_context("/api/decision"):
            resp = mvp_web._unhandled_exception(RuntimeError("boom internal"))
        self.assertEqual(resp[1], 500)
        body = resp[0].get_json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("内部错误", body["error"]["message"])
        self.assertNotIn("Traceback", resp[0].get_data(as_text=True))

    # ------------------------------------------------------------- T4 No Prediction
    def test_t4_no_prediction_warning(self):
        dec = {
            "context": {"decision_date": DD},
            "model_output": {"node": NODE, "note": "不在 test 预测窗口"},
            "evidence": {"eligible": [{"evidence_id": "E1"}], "rejected": []},
        }
        warnings = mvp_web._decision_warnings(dec)
        codes = {w["code"] for w in warnings}
        self.assertIn("NO_PREDICTION", codes)
        np_w = next(w for w in warnings if w["code"] == "NO_PREDICTION")
        self.assertTrue(np_w["message"])
        self.assertTrue(np_w["suggested_action"])
        # 路由响应携带 warnings 字段
        r = self.client.post("/api/decision", json={
            "decision_date": DD, "node": NODE, "hour": HOUR, "evidence": "offline"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("warnings", r.get_json())

    # ------------------------------------------------------------- T5 Evidence source
    def test_t5_evidence_source_unavailable_warning(self):
        # offline（EVIDENCE MODE=NONE）：静态无证据属预期，不触发警告
        dec_none = {"context": {"decision_date": DD, "evidence_mode": "NONE"},
                    "model_output": {"expected_return": 1.0},
                    "evidence": {"eligible": [], "rejected": []}}
        self.assertEqual(mvp_web._decision_warnings(dec_none), [])
        # LIVE 模式（真实 GFS）无可用证据 → 网络失败诚实降级 → 结构化警告
        dec_live = {"context": {"decision_date": DD, "evidence_mode": "LIVE"},
                    "model_output": {"expected_return": 1.0},
                    "evidence": {"eligible": [], "rejected": []}}
        warnings = mvp_web._decision_warnings(dec_live)
        codes = {w["code"] for w in warnings}
        self.assertIn("EVIDENCE_SOURCE_UNAVAILABLE", codes)
        w = next(w for w in warnings if w["code"] == "EVIDENCE_SOURCE_UNAVAILABLE")
        self.assertTrue(w["suggested_action"])

    # ------------------------------------------------------------- T6 LLM unavailable
    def test_t6_llm_unavailable_structured(self):
        with mock.patch.object(mvp_web, "ask_copilot", return_value={
            "answer": "LLM NOT CONFIGURED",
            "status": "degraded",
            "degraded": True,
            "tools_called": [],
            "trace": [],
            "llm_status": "NOT_CONFIGURED",
        }):
            r = self.client.post("/api/ask", json={"question": "为什么不卖"})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j["status"], "degraded")
        self.assertTrue(j["degraded"])
        self.assertEqual(j["error"]["code"], "LLM_UNAVAILABLE")
        self.assertTrue(j["error"]["suggested_action"])
        self.assertIn("LLM NOT CONFIGURED", j["answer"])

    # ------------------------------------------------------------- T7 Data Sources
    def test_t7_data_sources_page_honest(self):
        r = self.client.get("/data-sources")
        self.assertEqual(r.status_code, 200)
        text = r.get_data(as_text=True)
        self.assertIn("对账过 OASIS", text)
        self.assertIn("Reconciliation / validation only", text)
        self.assertIn("不是当前主要生产特征源", text)
        self.assertIn("Unified GFS Collector", text)
        self.assertIn("Vintage limitation", text)
        self.assertIn("historical only", text)


# ---------------------------------------------------------------------------
# 严格报告：TOTAL / PASSED / FAILED / SKIPPED
# ---------------------------------------------------------------------------
def main() -> int:
    import io
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestMvpWebV0311)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
    result = runner.run(suite)
    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped
    print("=" * 64)
    print("MVP V0.3.1.1 Web 加固验收测试报告")
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
