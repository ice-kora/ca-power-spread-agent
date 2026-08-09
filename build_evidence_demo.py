# -*- coding: utf-8 -*-
"""
build_evidence_demo.py —— 生成 Golden Case E 的真实历史 GFS Evidence Snapshot
=============================================================================

需求（V0.3.1.3）：Golden Case E 演示"Evidence 被 Time Gate 拒绝"，必须可重复、
不依赖现场网络。本脚本从 Open-Meteo Single Runs（NCEP GFS 0.25°）拉取一条**真实
历史 18Z GFS 预报**（decision_date=2026-07-08，target=2026-07-09，node=CONTROLX），
保存为 `demo_artifacts/evidence_demo.json`（真实历史快照：contains_mock=false，
historical_snapshot=true）。

语义（V0.3.1.4 Final Honesty Patch）
----
  - 18Z init = 2026-07-08 18:00 UTC，晚于 decision_cutoff = 2026-07-08 17:00 UTC。
  - **不推算 available_at**（绝不把 init+delay 当真实可用时刻）：该 run 连初始化都发生在
    cutoff 之后 → Strong Impossibility：available_at 必然更晚 → `INITIALIZATION_AFTER_CUTOFF`；
    真实 available_at 无法证明 → 保持 `UNKNOWN / NOT PROVEN`（`AVAILABILITY_NOT_PROVEN`）。
  - evidence_demo.json 由 `HistoricalSnapshotEvidenceAdapter` 在 DEMO MODE 加载，
    注入后仍由 Time Gate 程序裁决（initialization_time > cutoff → 提前拒绝），
    **绝不进入** Risk Gate / Rule Engine / Final。

字段（需求四清单）
------------------
  evidence_id / source / source_type / forecast_run / initialization_time /
  published_at / available_at（UNKNOWN）/ available_at_source / availability_proven /
  initialization_after_cutoff / retrieved_at / target_time / node / region /
  event_type / summary / severity / raw_source_id / decision_cutoff /
  decision_eligible / reason_code / rejection_reason + decision_date（adapter 匹配用）。

用法
----
    python build_evidence_demo.py            # 联网拉真实 18Z 并生成 evidence_demo.json
    python build_evidence_demo.py --check    # 校验已生成文件的 Time Gate 语义
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from code.artifact_hash import HASH_ALGORITHM, HASH_NORMALIZATION  # noqa: E402

#: Golden Case E 的决策参数（与 GOLDEN_CASES 一致）
DECISION_DATE = "2026-07-08"
NODE = "CONTROLX_1_N001"
CYCLE = "18Z"
CUTOFF_UTC = "2026-07-08T17:00:00"     # decision_date 10:00 PT → UTC（PDT=UTC−7）

OUT = REPO_ROOT / "demo_artifacts" / "evidence_demo.json"


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _init_from_raw(raw_source_id: str, fallback: str) -> str:
    m = re.search(r"run=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", str(raw_source_id))
    if m:
        return m.group(1)
    m2 = re.search(r"run=(\d{4}-\d{2}-\d{2})", str(raw_source_id))
    if m2:
        return f"{m2.group(1)}T18:00:00"
    return fallback


def _content_hash(doc: dict) -> str:
    """文档"内容"的 canonical 哈希（不含 artifact_hash 字段本身，避免自引用）。

    校验端用同一函数 → 单一实现，跨平台（CRLF/LF）可复现。
    """
    subset = {k: v for k, v in doc.items() if k != "artifact_hash"}
    data = json.dumps(subset, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    return __import__("hashlib").sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def build_snapshot(verbose: bool = True) -> dict:
    """联网拉真实 18Z GFS 证据并组装 Evidence Snapshot（真实历史，非 MOCK）。"""
    from agent.evidence.gfs_forecast import build_gfs_evidence  # noqa: PLC0415

    ev = build_gfs_evidence(NODE, DECISION_DATE, cycle=CYCLE, mode="BACKTEST")
    if not ev:
        raise SystemExit(
            "无法生成 evidence_demo.json：联网失败或未取到真实 18Z GFS 数据。"
            "请在有网环境重试（Open-Meteo Single Runs）。")
    if ev.get("is_mock"):
        raise SystemExit("底层返回 MOCK 降级，拒绝写入（evidence_demo 必须真实历史，非 MOCK）。")

    init = _init_from_raw(ev.get("raw_source_id", ""), f"{DECISION_DATE}T18:00:00")
    # V0.3.1.4：**不推算 available_at**（绝不把 init+delay 当真实可用时刻）。
    # 该 18Z run 初始化时刻（18:00 UTC）晚于决策 cutoff（17:00 UTC）→
    # Strong Impossibility：available_at 必然更晚 → INITIALIZATION_AFTER_CUTOFF；
    # 真实 available_at 无法证明 → 保持 UNKNOWN（AVAILABILITY_NOT_PROVEN / AVAILABLE_AT_UNKNOWN）。
    available_at = ""

    record = {
        "evidence_id": ev.get("evidence_id", "GFS-18Z-2026-07-08-CONTROLX_1_N001"),
        "event_type": ev.get("event_type", "WEATHER_FORECAST"),
        "source": ev.get("source", ""),
        "source_type": ev.get("source_type", "WEATHER"),
        "forecast_run": CYCLE,
        "initialization_time": init,
        "published_at": ev.get("published_at", ""),        # 保守发布上界（估算值，非历史真实观测）
        "published_at_is_estimate": True,                  # V0.3.1.5：明确标注估算，不冒充真实发布时刻
        "available_at": available_at,                      # UNKNOWN（不伪造）
        "available_at_source": "NOT_PROVEN",
        "availability_proven": False,
        "initialization_after_cutoff": True,
        "retrieved_at": ev.get("retrieved_at", ""),
        "target_time": ev.get("target_time", ""),
        "node": NODE,
        "region": ev.get("region", "ZP26"),
        "summary": ev.get("summary", ""),
        "severity": ev.get("severity", "INFO"),
        "raw_source_id": ev.get("raw_source_id", ""),
        "directional_effect": ev.get("directional_effect", "UNCERTAIN"),
        "confidence": float(ev.get("confidence", 0.0) or 0.0),
        "decision_date": DECISION_DATE,
        "decision_cutoff": CUTOFF_UTC,
        "is_mock": False,
        "historical_snapshot": True,
        "decision_eligible": False,
        "reason_code": "INITIALIZATION_AFTER_CUTOFF",
        "rejection_reason": (
            "INITIALIZATION_AFTER_CUTOFF（AVAILABLE_AT_UNKNOWN：该 GFS 18Z run 在决策 cutoff "
            "之后才开始初始化，available_at 必然更晚，因此不可能在 cutoff 前可用；"
            "其实际可用时间未知，不伪造）"),
    }
    doc = {
        "schema_version": "1.0",
        "generated_by": "build_evidence_demo.py",
        "generated_at": _iso_utc(),
        "source": record["source"],
        "source_timestamp": init,                          # 该快照对应的历史 run 初始化时刻
        "hash_algorithm": HASH_ALGORITHM,
        "hash_normalization": HASH_NORMALIZATION,
        "contains_mock": False,
        "historical_snapshot": True,
        "available_at_proven": False,                      # V0.3.1.4：不伪造可用时刻
        "raw_source_id": record["raw_source_id"],
        "note": (
            "真实历史 GFS 18Z forecast snapshot（Open-Meteo Single Runs 历史档案）。"
            "该 run 初始化时刻（18:00 UTC）晚于决策 cutoff（17:00 UTC）→ "
            "INITIALIZATION_AFTER_CUTOFF；真实 available_at 未知（AVAILABLE_AT_UNKNOWN），"
            "不伪造可用时刻。Demo 使用固定历史快照保证演示可重复。"),
        "records": [record],
    }
    # artifact_hash = 记录内容 canonical 哈希（跨平台可复现；不含 hash 字段自身）
    doc["artifact_hash"] = _content_hash(doc)
    if verbose:
        print(f"[1] 真实 18Z GFS Evidence（decision_date={DECISION_DATE}，target={pd.Timestamp(DECISION_DATE)+pd.Timedelta(days=1)}）")
        print(f"    evidence_id    : {record['evidence_id']}")
        print(f"    forecast_run   : {CYCLE}")
        print(f"    initialization : {init}")
        print(f"    published_at   : {record['published_at'] or '—'}")
        print(f"    available_at   : UNKNOWN / NOT PROVEN（不伪造 init+delay）")
        print(f"    decision_cutoff: {CUTOFF_UTC}")
        print(f"    summary        : {record['summary'][:80]}…")
        print(f"    → init({init}) > cutoff({CUTOFF_UTC}) → NOT ELIGIBLE → INITIALIZATION_AFTER_CUTOFF")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Golden Case E 真实历史 GFS Evidence Snapshot")
    ap.add_argument("--check", action="store_true", help="校验已生成文件的 Time Gate 语义（不联网）")
    args = ap.parse_args()

    if args.check:
        if not OUT.exists():
            print("evidence_demo.json 不存在 → 请先运行 python build_evidence_demo.py")
            return 1
        doc = json.loads(OUT.read_text(encoding="utf-8"))
        rec = doc["records"][0]
        # 内容哈希自洽（同一 _content_hash，跨平台可复现）
        h = _content_hash(doc)
        print(f"[check] artifact_hash 匹配: {'PASS' if h == doc.get('artifact_hash') else 'FAIL'}")
        print(f"[check] contains_mock=false: {'PASS' if not doc.get('contains_mock') else 'FAIL'}")
        print(f"[check] historical_snapshot=true: {'PASS' if doc.get('historical_snapshot') else 'FAIL'}")
        # Time Gate 语义：initialization_time > cutoff → NOT ELIGIBLE / INITIALIZATION_AFTER_CUTOFF
        from agent.evidence.time_gate import split_eligible  # noqa: PLC0415
        cutoff = rec.get("decision_cutoff") or "2026-07-08T17:00:00"
        elig, post = split_eligible([dict(rec)], cutoff)
        got = [e.get("evidence_id") for e in post]
        init = str(rec.get("initialization_time") or "")
        init_after = bool(init) and pd.Timestamp(init) > pd.Timestamp(cutoff)
        no_fake_avail = not str(rec.get("available_at") or "").strip()   # 不伪造 init+delay
        ok = (not elig) and got == [rec["evidence_id"]] and init_after and no_fake_avail \
             and "INITIALIZATION_AFTER_CUTOFF" in (rec.get("reason_code") or "")
        print(f"[check] available_at UNKNOWN 且 init({init}) > cutoff({cutoff}) → "
              f"NOT ELIGIBLE / INITIALIZATION_AFTER_CUTOFF: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    doc = build_snapshot()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"[2] -> {OUT}（{OUT.stat().st_size:,} bytes）")
    print("    记录字段：evidence_id / source / source_type / forecast_run / initialization_time /")
    print("      published_at / available_at / retrieved_at / target_time / node / region /")
    print("      event_type / summary / severity / raw_source_id / decision_cutoff / decision_eligible /")
    print("      rejection_reason")
    return 0


if __name__ == "__main__":
    sys.exit(main())
