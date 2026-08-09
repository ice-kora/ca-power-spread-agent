# -*- coding: utf-8 -*-
"""
build_demo_artifacts.py —— 从完整本地数据抽取 Golden Case 最小真实切片（Agent A）
=================================================================================

目标：让"别人 clean clone 后不用完整研究数据也能跑 Golden Case Demo"。

本脚本在【完整数据机】上运行（需要 code/data/canonical.parquet + predictions_v2.csv
+ stage3/risk_features.parquet + agent/case_library/cases*.json），只抽真实数据，
不造数据、不改 PnL / prediction / decision。输出到 demo_artifacts/：

  canonical_demo.parquet      Golden Cases 的 node×date×hour + 特征前置窗口（真实切片）
  predictions_demo.csv        Golden Cases 的模型预测切片（真实值）
  risk_features_demo.parquet  Golden Cases 的 Risk Gate 风险特征切片（真实值）
  cases_demo.json             Golden Cases 可检索案例切片（真实，as-of 硬约束保序）
  metadata.json               生成信息 / 源文件哈希 / 行数 / 诚实声明
  manifest.json               artifact_version / generated_at / source_commit /
                              golden_cases / source_files / row_counts / hashes /
                              data_mode=DEMO / contains_mock=false / contains_future_outcome=true

一致性铁律（FULL vs DEMO）：
  * canonical_demo 只做行切片（保留全列），Golden Case 行的 expected_return /
    direction_probability / signal_strength / risk_gate / rule_engine / final /
    actual_da / actual_rtpd / pnl 与 FULL 完全一致（可复现，见 docs/v0311_consistency.md）。
  * cases_demo.json 是"任一 Golden Case 决策时点可检索案例"的**超集**（按
    case_available_at <= 最晚 Golden 决策时点过滤）——as-of 检索硬约束在运行时
    再过滤，故 R8（SIMILAR_TAIL_LOSS_CASE）与相似案例展示与 FULL 完全一致。
  * 证据（GFS）不打包：FULL 与 DEMO 走同一实时证据路径（severity=INFO /
    UNCERTAIN → 不参与 gate/rule），网络可用性不影响决策一致性。

用法：
    python build_demo_artifacts.py            # 生成 demo_artifacts/ + manifest.json
    python build_demo_artifacts.py --check    # 生成后跑 FULL vs DEMO 一致性并写 docs/v0311_consistency.md

字段名保留英文。诚实：只抽真实切片，不伪造。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from data_mode import (  # noqa: E402
    MODE_DEMO,
    resolve_data_mode,
)
from code.artifact_hash import (  # noqa: E402
    HASH_ALGORITHM,
    HASH_NORMALIZATION,
    canonical_sha256,
)

#: 输出目录
OUT_DIR = REPO_ROOT / "demo_artifacts"

#: 黄金案例（决策日 D / 节点 / 小时 H；目标日 T = D+1）——与 docs/mvp_demo_cases.md 一致
GOLDEN_CASES: List[Dict[str, Any]] = [
    {"id": "B",  "label": "B · SELL 盈利（彩票右尾）",            "decision_date": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3},
    {"id": "C1", "label": "C1 · NO_TRADE 避险（RiskGate 成功）",  "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
    {"id": "C2", "label": "C2 · NO_TRADE 弱信号",                "decision_date": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10},
    {"id": "D",  "label": "D · 模型 SELL 但错（诚实展示）",       "decision_date": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20},
    {"id": "E",  "label": "E · Evidence 被 Time Gate 拒（同 C1 参数）", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
]

#: 特征 z-score 展示所需的前置历史天数（canonical_demo 从最早决策日回看该窗口）
HISTORY_PRE_DAYS = 90

#: 案例切片过滤的"最晚 Golden 决策时点"（PT naive，与 policy.decision_time_for 同口径）
LATEST_DECISION_DATE = "2026-07-20"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    """跨平台 canonical 哈希：文本文件 CRLF→LF 归一化后 SHA-256；二进制原样。

    配合 .gitattributes `text eol=lf`：Windows（CRLF）/ Linux（LF）上同一文件
    的 canonical 哈希一致，clean clone 校验不因换行符 FAIL（V0.3.1.2 P0）。
    """
    return canonical_sha256(path)


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _require_full() -> Dict[str, Path]:
    """校验完整 artifacts 存在，返回 {canon, pred, risk, cases_manual, cases_auto}。"""
    info = resolve_data_mode(override="full")
    missing = []
    for label, p in (("canon", info.canon_path), ("pred", info.pred_path), ("risk", info.risk_path)):
        if not Path(p).exists():
            missing.append(str(p))
    cases = [REPO_ROOT / "agent" / "case_library" / "cases.json",
             REPO_ROOT / "agent" / "case_library" / "cases_auto.json"]
    for p in cases:
        if not p.exists():
            missing.append(str(p))
    if missing:
        raise SystemExit(
            "完整 artifacts 缺失，无法抽取 demo 切片。请先在完整数据机上重建：\n  "
            + "\n  ".join(missing)
        )
    return {
        "canon": Path(info.canon_path), "pred": Path(info.pred_path), "risk": Path(info.risk_path),
        "cases_manual": cases[0], "cases_auto": cases[1],
    }


# ---------------------------------------------------------------------------
# 切片
# ---------------------------------------------------------------------------
def _target_dates() -> List[str]:
    """Golden Cases 的目标日（T = D+1），去重排序。"""
    seen: List[str] = []
    for c in GOLDEN_CASES:
        t = (pd.Timestamp(c["decision_date"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if t not in seen:
            seen.append(t)
    return sorted(seen)


def _golden_nodes() -> List[str]:
    return sorted({c["node"] for c in GOLDEN_CASES})


def build_canon_slice(canon: pd.DataFrame, out_path: Path) -> int:
    """canonical_demo：Golden nodes × [最早决策日−HISTORY_PRE_DAYS, 最晚目标日] 全部小时。

    只做行切片（保留全列）→ Golden Case 行的 lag/滚动特征、actual_* 与 FULL 逐值一致。
    """
    nodes = _golden_nodes()
    target_dates = _target_dates()
    start = (pd.Timestamp(target_dates[0]) - pd.Timedelta(days=HISTORY_PRE_DAYS)).normalize()
    end = pd.Timestamp(target_dates[-1]).normalize()
    sub = canon[
        (canon["node"].isin(nodes))
        & (canon["target_date"] >= start)
        & (canon["target_date"] <= end)
    ]
    if sub.empty:
        raise SystemExit(f"canonical 切片为空（nodes={nodes} {start}~{end}）")
    sub.to_parquet(out_path, index=False)
    return len(sub)


def build_pred_slice(pred: pd.DataFrame, out_path: Path) -> int:
    """predictions_demo：Golden nodes × Golden 目标日（全部小时，便于演示窗口内探索）。"""
    nodes = _golden_nodes()
    target_dates = _target_dates()
    sub = pred[
        (pred["node"].isin(nodes))
        & (pred["target_date"].dt.strftime("%Y-%m-%d").isin(target_dates))
    ]
    if sub.empty:
        raise SystemExit("predictions 切片为空")
    sub.to_csv(out_path, index=False)
    return len(sub)


def build_risk_slice(risk: pd.DataFrame, out_path: Path) -> int:
    """risk_features_demo：Golden nodes × Golden 目标日（全部小时）。"""
    nodes = _golden_nodes()
    target_dates = _target_dates()
    sub = risk[
        (risk["node"].isin(nodes))
        & (risk["target_date"].dt.strftime("%Y-%m-%d").isin(target_dates))
    ]
    if sub.empty:
        raise SystemExit("risk_features 切片为空")
    sub.to_parquet(out_path, index=False)
    return len(sub)


def _case_retrievable_before(c: Dict[str, Any], decision_time: str, decision_date: str) -> bool:
    """案例在'最晚 Golden 决策时点'前可检索（超集过滤；运行时 as-of 再精过滤）。

    带 case_available_at 的案例：case_available_at <= decision_time（精确到分钟，同 policy）。
    旧案例（无 case_available_at）：decision_date < 候选决策日（旧口径兼容，仅保守更严）。
    """
    avail = str(c.get("case_available_at") or "").strip()
    if avail:
        try:
            return str(avail)[:16] <= decision_time[:16]
        except Exception:
            return False
    cd = str(c.get("decision_date") or "")[:10]
    return bool(cd) and cd < decision_date


def build_cases_slice() -> List[Dict[str, Any]]:
    """cases_demo.json：合并 manual+auto 后按"最晚 Golden 决策时点"过滤（真实，as-of 超集）。"""
    paths = [REPO_ROOT / "agent" / "case_library" / "cases.json",
             REPO_ROOT / "agent" / "case_library" / "cases_auto.json"]
    out: List[Dict[str, Any]] = []
    for p in paths:
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        cases = data.get("cases", data) if isinstance(data, dict) else data
        out.extend(list(cases))
    decision_time = f"{LATEST_DECISION_DATE}T10:00:00"
    filtered = [c for c in out if _case_retrievable_before(c, decision_time, LATEST_DECISION_DATE)]
    filtered.sort(key=lambda c: (str(c.get("decision_date", "")), str(c.get("node", "")), int(c.get("hour", 0) or 0)))
    return filtered


# ---------------------------------------------------------------------------
# 元数据 / manifest
# ---------------------------------------------------------------------------
def _golden_meta() -> List[Dict[str, Any]]:
    return [
        {
            "id": c["id"],
            "label": c["label"],
            "decision_date": c["decision_date"],
            "target_date": (pd.Timestamp(c["decision_date"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            "node": c["node"],
            "hour": c["hour"],
        }
        for c in GOLDEN_CASES
    ]


def _write_metadata(info: Dict[str, Path], row_counts: Dict[str, int],
                    out_hashes: Dict[str, str], src_hashes: Dict[str, str],
                    cases: List[Dict[str, Any]], canon_start: str, canon_end: str) -> None:
    meta = {
        "schema_version": "1.0",
        "data_mode": MODE_DEMO,
        "hash_algorithm": HASH_ALGORITHM,
        "hash_normalization": HASH_NORMALIZATION,
        "description": (
            "真实历史数据的最小切片，只覆盖 5 个 Golden Cases 决策所需行 + 特征前置窗口。"
            "不造数据、不改 PnL/prediction/decision。"
        ),
        "generated_by": "build_demo_artifacts.py",
        "generated_at": _iso_utc(),
        "source_commit": _git_head(),
        "golden_cases": _golden_meta(),
        "data_window": {"canon_start": canon_start, "canon_end": canon_end,
                        "history_pre_days": HISTORY_PRE_DAYS},
        "nodes": _golden_nodes(),
        "target_dates": _target_dates(),
        "contains_mock": False,
        "contains_future_outcome": True,
        "future_outcome_note": (
            "actual_da / actual_rtpd / actual_return / pnl 为事后结算值；"
            "仅在决策 LOCK 之后经 Reveal（Post-trade / Tool 6 get_post_trade_review）可访问，"
            "绝不回灌决策（Evidence Time Gate / as-of 硬约束）。"
        ),
        "source_files": src_hashes,
        "output_files": out_hashes,
        "row_counts": row_counts,
        "cases_demo_n": len(cases),
        "honest_notes": [
            "DEMO ≠ MOCK：DEMO 是真实历史切片，可真实推荐；MOCK 永不参与真实推荐。",
            "canonical_demo 只做行切片（保留全列），Golden Case 行数值与 FULL 逐值一致。",
            "cases_demo.json 为任一 Golden Case 决策时点可检索案例的超集；运行时 as-of 再精过滤。",
            "证据（GFS）不打包：FULL 与 DEMO 走同一实时证据路径，网络可用性不影响决策一致性。",
        ],
    }
    (OUT_DIR / "metadata.json").write_bytes(
        json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))


def _evidence_demo_meta() -> Optional[Dict[str, Any]]:
    """读取 evidence_demo.json 的 Provenance 元数据（V0.3.1.3 需求六）。

    manifest 登记 evidence_demo.json：source / source_timestamp / artifact_hash /
    hash_normalization / contains_mock=false / historical_snapshot=true /
    raw_source_id。artifact_hash 取文档内部"内容哈希"（不含 hash 字段自身）。
    """
    p = OUT_DIR / "evidence_demo.json"
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "source": str(doc.get("source", "")),
        "source_timestamp": str(doc.get("source_timestamp", "")),
        "artifact_hash": str(doc.get("artifact_hash", "")),
        "hash_algorithm": str(doc.get("hash_algorithm", HASH_ALGORITHM)),
        "hash_normalization": str(doc.get("hash_normalization", HASH_NORMALIZATION)),
        "contains_mock": bool(doc.get("contains_mock", False)),
        "historical_snapshot": bool(doc.get("historical_snapshot", False)),
        "available_at_proven": bool(doc.get("available_at_proven", False)),   # V0.3.1.4：不伪造可用时刻
        "raw_source_id": str(doc.get("raw_source_id", "")),
        "records_n": len(doc.get("records", []) or []),
    }


def _write_manifest(row_counts: Dict[str, int], out_hashes: Dict[str, str],
                    src_hashes: Dict[str, str], cases: List[Dict[str, Any]]) -> None:
    manifest = {
        "artifact_version": "1.0",
        "hash_algorithm": HASH_ALGORITHM,
        "hash_normalization": HASH_NORMALIZATION,
        "generated_at": _iso_utc(),
        "source_commit": _git_head(),
        "data_mode": MODE_DEMO,
        "contains_mock": False,
        "contains_future_outcome": True,
        "future_outcome_note": (
            "canonical_demo 含 actual_da/actual_rtpd/actual_return（事后结算值）。"
            "它们在决策时点不可见：仅当决策 LOCK 后经 Reveal（run_decision(reveal=True) / "
            "reveal_decision / Tool 6 get_post_trade_review）才输出，绝不穿越。"
        ),
        "golden_cases": _golden_meta(),
        "source_files": src_hashes,
        "row_counts": row_counts,
        "hashes": out_hashes,
        "files": {
            "canonical_demo.parquet": "Golden Cases 特征 + 实际结算切片",
            "predictions_demo.csv": "Golden Cases 模型预测切片",
            "risk_features_demo.parquet": "Golden Cases Risk Gate 风险特征切片",
            "cases_demo.json": "Golden Cases 案例库切片（真实，as-of）",
            "metadata.json": "生成信息 / 哈希 / 行数 / 诚实声明",
            "evidence_demo.json": "Golden Case E 真实历史 GFS Evidence Snapshot（18Z initialization_time > cutoff，available_at not proven → NOT ELIGIBLE / INITIALIZATION_AFTER_CUTOFF）",
        },
        "evidence_snapshot": _evidence_demo_meta(),
        "note": "在完整数据机上运行 python build_demo_artifacts.py 生成；clean clone 可直接以 DEMO 模式启动。",
    }
    (OUT_DIR / "manifest.json").write_bytes(
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="抽取 Golden Case 最小真实切片到 demo_artifacts/")
    ap.add_argument("--check", action="store_true",
                    help="生成后跑 FULL vs DEMO 一致性，并写 docs/v0311_consistency.md")
    args = ap.parse_args()

    print("=" * 72)
    print("  build_demo_artifacts.py · 抽取 Golden Case 最小真实切片")
    print("=" * 72)

    info = _require_full()
    print("\n[1] 源文件（完整数据）")
    for label, p in info.items():
        print(f"    · {label:<12} {p}")

    canon = pd.read_parquet(info["canon"])
    pred = pd.read_csv(info["pred"])
    risk = pd.read_parquet(info["risk"])
    for c in ("target_date", "decision_date"):
        if c in canon.columns:
            canon[c] = pd.to_datetime(canon[c]).dt.normalize()
    pred["target_date"] = pd.to_datetime(pred["target_date"]).dt.normalize()
    risk["target_date"] = pd.to_datetime(risk["target_date"]).dt.normalize()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[2] 生成切片（只抽真实数据）")
    n_canon = build_canon_slice(canon, OUT_DIR / "canonical_demo.parquet")
    print(f"    · canonical_demo.parquet       {n_canon} 行")
    n_pred = build_pred_slice(pred, OUT_DIR / "predictions_demo.csv")
    print(f"    · predictions_demo.csv         {n_pred} 行")
    n_risk = build_risk_slice(risk, OUT_DIR / "risk_features_demo.parquet")
    print(f"    · risk_features_demo.parquet   {n_risk} 行")
    cases = build_cases_slice()
    (OUT_DIR / "cases_demo.json").write_bytes(
        json.dumps({"meta": {"generator": "build_demo_artifacts.py", "n": len(cases)},
                    "cases": cases}, ensure_ascii=False, indent=1).encode("utf-8"))
    print(f"    · cases_demo.json              {len(cases)} 条案例（as-of 超集）")

    row_counts = {
        "canonical_demo": n_canon,
        "predictions_demo": n_pred,
        "risk_features_demo": n_risk,
        "cases_demo": len(cases),
    }

    # ---- 哈希（源文件 + 输出文件）----
    print("\n[3] 哈希")
    src_files = {
        "canonical.parquet": info["canon"],
        "predictions_v2.csv": info["pred"],
        "stage3/risk_features.parquet": info["risk"],
        "agent/case_library/cases.json": info["cases_manual"],
        "agent/case_library/cases_auto.json": info["cases_auto"],
    }
    src_hashes = {k: _sha256(Path(v)) for k, v in src_files.items()}
    out_files = {
        "canonical_demo.parquet": OUT_DIR / "canonical_demo.parquet",
        "predictions_demo.csv": OUT_DIR / "predictions_demo.csv",
        "risk_features_demo.parquet": OUT_DIR / "risk_features_demo.parquet",
        "cases_demo.json": OUT_DIR / "cases_demo.json",
    }
    ev_demo = OUT_DIR / "evidence_demo.json"
    if ev_demo.exists():                       # V0.3.1.3：Golden Case E 真实 GFS Snapshot
        out_files["evidence_demo.json"] = ev_demo
    out_hashes = {k: _sha256(Path(v)) for k, v in out_files.items()}
    for k, h in src_hashes.items():
        print(f"    · source {k:<34} {h[:16]}…")
    for k, h in out_hashes.items():
        print(f"    · output {k:<34} {h[:16]}…")

    # ---- 元数据 + manifest ----
    target_dates = _target_dates()
    canon_start = (pd.Timestamp(target_dates[0]) - pd.Timedelta(days=HISTORY_PRE_DAYS)).strftime("%Y-%m-%d")
    canon_end = target_dates[-1]
    _write_metadata(info, row_counts, out_hashes, src_hashes, cases, canon_start, canon_end)
    _write_manifest(row_counts, out_hashes, src_hashes, cases)
    print(f"\n[4] manifest.json + metadata.json -> {OUT_DIR}")

    # ---- Golden Case 行自检：切片值与 FULL 源值逐位一致 ----
    print("\n[5] Golden Case 行自检（DEMO 切片 == FULL 源值）")
    n_checks = 0
    for c in GOLDEN_CASES:
        t = (pd.Timestamp(c["decision_date"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        td = pd.Timestamp(t)
        src = canon[(canon["node"] == c["node"]) & (canon["target_date"] == td) & (canon["hour"] == c["hour"])]
        sub = pd.read_parquet(OUT_DIR / "canonical_demo.parquet")
        sub["target_date"] = pd.to_datetime(sub["target_date"]).dt.normalize()
        got = sub[(sub["node"] == c["node"]) & (sub["target_date"] == td) & (sub["hour"] == c["hour"])]
        if src.empty or got.empty:
            print(f"    · {c['id']}  {c['node']} {t} H{c['hour']}  <-- 行缺失！")
            continue
        a, b = src.iloc[0], got.iloc[0]
        diffs = []
        for col in ("actual_da", "actual_rtpd", "actual_return", "spread_lag1", "spread_mean7"):
            va, vb = float(a[col]), float(b[col])
            if (va != va and vb != vb):
                continue
            if abs(va - vb) > 1e-9:
                diffs.append(f"{col}: {va} vs {vb}")
        pred_src = pred[(pred["node"] == c["node"]) & (pred["target_date"] == td) & (pred["hour"] == c["hour"])]
        pred_sub = pd.read_csv(OUT_DIR / "predictions_demo.csv")
        pred_sub["target_date"] = pd.to_datetime(pred_sub["target_date"]).dt.normalize()
        pg = pred_sub[(pred_sub["node"] == c["node"]) & (pred_sub["target_date"] == td) & (pred_sub["hour"] == c["hour"])]
        if not pred_src.empty and not pg.empty:
            for col in ("expected_return", "prob_positive", "prob_negative", "confidence", "uncertainty"):
                if abs(float(pred_src.iloc[0][col]) - float(pg.iloc[0][col])) > 1e-9:
                    diffs.append(f"pred.{col}")
        status = "OK" if not diffs else f"DIFF: {diffs}"
        print(f"    · {c['id']}  {c['node']} {t} H{c['hour']}  {status}")
        n_checks += 1
    print(f"\n    Golden 行自检通过：{n_checks}/{len(GOLDEN_CASES)}")

    print("\n[6] 完成。demo_artifacts/ 已生成：")
    for p in sorted(OUT_DIR.glob("*")):
        print(f"    · {p.name}  ({p.stat().st_size:,} bytes)")

    if args.check:
        print("\n[7] 运行 FULL vs DEMO 一致性检查（写 docs/v0311_consistency.md）...")
        from check_demo_consistency import run_and_write_consistency_doc
        run_and_write_consistency_doc(repo_root=REPO_ROOT)
    print("\nOK —— 现在可以：python prepare_mvp.py  然后  python mvp_web.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
