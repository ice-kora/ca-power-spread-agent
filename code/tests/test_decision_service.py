# -*- coding: utf-8 -*-
"""
code/tests/test_decision_service.py

DecisionService + 6 个 Tool 的单元测试（unittest）。

覆盖（任务要求）：
  - 6 个 Tool 的基本行为
  - get_post_trade_review 在未 reveal 时拒绝（OUTCOME_NOT_REVEALED）
  - 数字来自真实数据（predictions_v2.csv / canonical.parquet）
  - decision_id 生成 / 存档 / 查询 / 持久化
  - 未知名 decision_id 返回结构化 NOT_FOUND

运行：python -m unittest code.tests.test_decision_service -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd  # noqa: E402

from code.decision_service import (  # noqa: E402
    DecisionService,
    OUTCOME_NOT_REVEALED,
    StaticEvidenceAdapter,
)

DD = "2026-07-08"           # 决策日
NODE = "CONTROLX_1_N001"    # ZP26
HOUR = 2                    # 目标小时 H2（target = 2026-07-09）
TARGET_DATE = "2026-07-09"

# 决策 cutoff（2026-07-08 10:00 PT → UTC，PDT=UTC−7）：17:00 UTC
CUTOFF_UTC = "2026-07-08T17:00:00"

PRE_CUTOFF = "2026-07-08T09:00:00"     # ≤ cutoff → eligible
POST_CUTOFF = "2026-07-08T18:00:00"    # > cutoff → rejected


def _mk_ev(eid: str, published_at: str, *, severity: str = "WATCH",
           is_mock: bool = False) -> dict:
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


def _service(store_path=None) -> DecisionService:
    """离线确定性服务：注入静态证据（1 eligible + 1 post-decision + 1 MOCK）。"""
    raw = [
        _mk_ev("EV-ELIG-1", PRE_CUTOFF, severity="WATCH"),
        _mk_ev("EV-POST-1", POST_CUTOFF, severity="WARNING"),
        _mk_ev("EV-MOCK-1", PRE_CUTOFF, severity="WATCH", is_mock=True),  # R7: MOCK 恒隔离
    ]
    return DecisionService(evidence_adapter=StaticEvidenceAdapter(raw), store_path=store_path)


class TestDecisionService(unittest.TestCase):

    def setUp(self):
        self.svc = _service()

    # ------------------------------------------------------------- Tool 1
    def test_get_decision_returns_expected_fields(self):
        out = self.svc.get_decision(DD, NODE, HOUR)
        self.assertEqual(out["tool"], "get_decision")
        self.assertEqual(out["status"], "ok")
        for key in ("model_output", "risk_gate", "rule_engine",
                    "final_recommendation", "reason_codes"):
            self.assertIn(key, out)
        self.assertIn("decision_id", out)
        self.assertEqual(out["context"]["node"], NODE)
        self.assertEqual(out["context"]["target_date"], TARGET_DATE)
        # 决策语义三态
        self.assertIn(out["final_recommendation"], ("BUY_DA", "SELL_DA", "NO_TRADE"))

    def test_get_decision_model_output_from_real_data(self):
        pred = pd.read_csv("code/data/predictions_v2.csv")
        pred["target_date"] = pd.to_datetime(pred["target_date"]).dt.normalize()
        row = pred[(pred["node"] == NODE) & (pred["target_date"] == pd.Timestamp(TARGET_DATE))
                   & (pred["hour"] == HOUR)].iloc[0]
        out = self.svc.get_decision(DD, NODE, HOUR)
        mo = out["model_output"]
        self.assertAlmostEqual(mo["expected_return"], float(row["expected_return"]), places=6)
        self.assertAlmostEqual(mo["prob_positive"], float(row["prob_positive"]), places=6)
        self.assertAlmostEqual(mo["model_signal_strength"], float(row["confidence"]), places=6)
        self.assertAlmostEqual(mo["uncertainty"], float(row["uncertainty"]), places=6)
        # 方向语义：expected_return<0 → BUY
        self.assertEqual(mo["direction"], "BUY")

    # ------------------------------------------------------------- Tool 2
    def test_get_feature_explanation_from_real_data(self):
        did = self.svc.run_decision(DD, NODE, HOUR)["decision_id"]
        out = self.svc.get_feature_explanation(did)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(len(out["top_features"]) >= 1)
        canon = pd.read_parquet("code/data/canonical.parquet")
        canon["target_date"] = pd.to_datetime(canon["target_date"]).dt.normalize()
        cr = canon[(canon["node"] == NODE) & (canon["target_date"] == pd.Timestamp(TARGET_DATE))
                   & (canon["hour"] == HOUR)].iloc[0]
        top = out["top_features"][0]
        # 数值来自真实数据（canonical 该行对应特征列）
        self.assertAlmostEqual(top["value"], float(cr[top["feature"]]), places=6)
        for key in ("feature", "value", "source", "available_at_display", "availability"):
            self.assertIn(key, top)

    # ------------------------------------------------------------- Tool 3
    def test_get_evidence_eligible_rejected_split(self):
        did = self.svc.run_decision(DD, NODE, HOUR)["decision_id"]
        out = self.svc.get_evidence(did)
        self.assertEqual(out["status"], "ok")
        elig = {r["evidence_id"]: r for r in out["eligible"]}
        rej = {r["evidence_id"]: r for r in out["rejected"]}
        self.assertIn("EV-ELIG-1", elig)
        self.assertTrue(elig["EV-ELIG-1"]["eligible"])
        self.assertEqual(elig["EV-ELIG-1"]["rejection_reason"], "")
        # 晚于 cutoff → rejected
        self.assertIn("EV-POST-1", rej)
        self.assertFalse(rej["EV-POST-1"]["eligible"])
        self.assertIn("POST_DECISION_EVIDENCE", rej["EV-POST-1"]["rejection_reason"])
        # MOCK 硬隔离（R7）→ rejected
        self.assertIn("EV-MOCK-1", rej)
        self.assertIn("MOCK_DATA_NOT_ELIGIBLE", rej["EV-MOCK-1"]["rejection_reason"])

    # ------------------------------------------------------------- Tool 4
    def test_get_similar_cases_shape_and_as_of(self):
        did = self.svc.run_decision(DD, NODE, HOUR)["decision_id"]
        out = self.svc.get_similar_cases(did)
        self.assertEqual(out["status"], "ok")
        self.assertLessEqual(len(out["cases"]), 3)
        for c in out["cases"]:
            for key in ("case_id", "decision_date", "node", "hour", "decision",
                        "outcome", "PnL", "lesson", "case_available_at", "as_of_verified"):
                self.assertIn(key, c)
            # as-of 硬约束：可检索的 case 必须带 case_available_at
            self.assertTrue(c["case_available_at"])
            self.assertTrue(c["as_of_verified"])

    # ------------------------------------------------------------- Tool 5
    def test_get_data_provenance_single_and_all(self):
        did = self.svc.run_decision(DD, NODE, HOUR)["decision_id"]
        single = self.svc.get_data_provenance(did, feature_name="spread_lag1")
        self.assertEqual(single["status"], "ok")
        p = single["provenance"]
        self.assertEqual(p["feature"], "spread_lag1")
        self.assertEqual(p["source_type"], "PRICE")
        self.assertFalse(p["is_mock"])
        self.assertTrue(p["backtest_eligible"])
        self.assertTrue(p["production_eligible"])
        self.assertTrue(p["decision_eligible"])
        self.assertTrue(p["target_time"])
        self.assertTrue(p["available_at"])
        # 全部
        allp = self.svc.get_data_provenance(did)
        self.assertEqual(allp["status"], "ok")
        self.assertGreaterEqual(allp["n"], 1)
        self.assertIn("source", allp["provenance"][0])

    def test_get_data_provenance_missing_feature_falls_back(self):
        did = self.svc.run_decision(DD, NODE, HOUR)["decision_id"]
        out = self.svc.get_data_provenance(did, feature_name="da_lag7")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["feature_name"], "da_lag7")
        self.assertEqual(out["provenance"]["feature"], "da_lag7")

    # ------------------------------------------------------------- Tool 6
    def test_post_trade_review_refused_when_not_revealed(self):
        did = self.svc.run_decision(DD, NODE, HOUR, reveal=False)["decision_id"]
        out = self.svc.get_post_trade_review(did)
        self.assertEqual(out["status"], OUTCOME_NOT_REVEALED)
        # 未 reveal 的决策对象里不允许出现当前交易 actual_*（历史 case 的 outcome 除外）
        dec = self.svc._decisions[did]
        self.assertFalse(dec["outcome_revealed"])
        self.assertEqual(dec["post_trade"]["status"], OUTCOME_NOT_REVEALED)

    def test_post_trade_review_after_reveal_true(self):
        did = self.svc.run_decision(DD, NODE, HOUR, reveal=True)["decision_id"]
        out = self.svc.get_post_trade_review(did)
        self.assertEqual(out["status"], "REVEALED")
        # actual 来自真实数据（canonical actual_return）
        canon = pd.read_parquet("code/data/canonical.parquet")
        canon["target_date"] = pd.to_datetime(canon["target_date"]).dt.normalize()
        cr = canon[(canon["node"] == NODE) & (canon["target_date"] == pd.Timestamp(TARGET_DATE))
                   & (canon["hour"] == HOUR)].iloc[0]
        self.assertAlmostEqual(out["actual_return"], float(cr["actual_return"]), places=6)
        self.assertAlmostEqual(out["actual_da"], float(cr["actual_da"]), places=6)
        self.assertAlmostEqual(out["actual_rtpd"], float(cr["actual_rtpd"]), places=6)
        # PnL 语义：NO_TRADE → 0
        self.assertEqual(out["pnl"], 0.0)
        self.assertIn("review_category", out)
        self.assertIn("lessons", out)

    def test_post_trade_review_after_late_reveal(self):
        did = self.svc.run_decision(DD, NODE, HOUR, reveal=False)["decision_id"]
        self.assertEqual(self.svc.get_post_trade_review(did)["status"], OUTCOME_NOT_REVEALED)
        # Service 层强制 Lock 前置：Lock 前 reveal 被拒（NOT_LOCKED），Lock 后才 REVEALED
        locked = self.svc.reveal_decision(did)
        self.assertEqual(locked["status"], "NOT_LOCKED")
        self.svc.lock_decision(did)
        out = self.svc.reveal_decision(did)
        self.assertEqual(out["status"], "REVEALED")
        out2 = self.svc.get_post_trade_review(did)
        self.assertEqual(out2["status"], "REVEALED")
        self.assertIsNotNone(out2["actual_return"])

    # ------------------------------------------------------------- 决策 ID / 存档
    def test_decision_id_unique_and_queryable(self):
        did1 = self.svc.run_decision(DD, NODE, HOUR)["decision_id"]
        did2 = self.svc.run_decision(DD, NODE, HOUR)["decision_id"]
        self.assertNotEqual(did1, did2)
        self.assertTrue(did1.startswith("DEC-"))
        # 同一参数 get_decision 复用最新已存档决策
        g = self.svc.get_decision(DD, NODE, HOUR)
        self.assertEqual(g["decision_id"], did2)
        # 按 id 查询各工具
        self.assertEqual(self.svc.get_feature_explanation(did1)["status"], "ok")
        self.assertEqual(self.svc.get_evidence(did1)["status"], "ok")
        self.assertEqual(self.svc.get_similar_cases(did1)["status"], "ok")
        self.assertEqual(self.svc.get_data_provenance(did1)["status"], "ok")

    def test_unknown_decision_id_returns_not_found(self):
        for tool in ("get_feature_explanation", "get_evidence", "get_similar_cases",
                     "get_data_provenance", "get_post_trade_review"):
            out = getattr(self.svc, tool)("DEC-NOPE")
            self.assertEqual(out["status"], "NOT_FOUND", tool)
            self.assertEqual(out["tool"], tool)

    def test_store_persistence_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "decisions.json")
            svc1 = _service(store_path=store)
            did = svc1.run_decision(DD, NODE, HOUR)["decision_id"]
            # 新实例从文件恢复
            svc2 = _service(store_path=store)
            self.assertIn(did, [d["decision_id"] for d in svc2.list_decisions()])
            self.assertEqual(svc2.get_feature_explanation(did)["status"], "ok")

    # ------------------------------------------------------------- 运行参数校验
    def test_invalid_node_and_hour_raise(self):
        with self.assertRaises(ValueError):
            self.svc.run_decision(DD, "BAD_NODE", HOUR)
        with self.assertRaises(ValueError):
            self.svc.run_decision(DD, NODE, 0)
        with self.assertRaises(ValueError):
            self.svc.run_decision(DD, NODE, 25)

    def test_reveal_false_object_has_no_actual_keys(self):
        dec = self.svc.run_decision(DD, NODE, HOUR, reveal=False)
        self.assertNotIn("actual_da", dec["post_trade"])
        self.assertNotIn("actual_return", dec["post_trade"])
        self.assertEqual(dec["post_trade"]["status"], OUTCOME_NOT_REVEALED)


if __name__ == "__main__":
    unittest.main()
