# -*- coding: utf-8 -*-
"""
code/data_acquisition/validation.py —— 数据质量校验（Agent E）

采集后校验（validate_collection），输出 [{level, message}]：
  1. 记录非空
  2. decision_cutoff 与程序期望一致（D 10:00 PT → UTC）
  3. 每条记录 eligibility 四字段（time/backtest/production/decision）复算 == 存储值（防漂移）
  4. (target_time, field_name) 重复检测
  5. 每字段覆盖度 / 缺失率（缺目标小时 → WARNING）
  6. 值域 plausibility（温度 / 风 / 辐照 / 负荷）
  7. MOCK / not_backtest_safe 声明 + 硬规则（Agent B）：
     - is_mock=True 禁止声明 backtest_eligible / production_eligible（R7）
     - not_backtest_safe=True 禁止声明 backtest_eligible（R8）
  8. 时区 / DST 检查：schemas.pt_naive_to_utc_naive 与 zoneinfo 独立换算一致

只做"结构/口径/取值"校验，不做"事实真假"——真假由数据源负责。
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.schemas import asof_from_dict  # noqa: E402

#: 字段 → (下限, 上限)；None 表示不限。值越界 → WARNING（不是 ERROR，容忍边界）。
PLAUSIBLE_RANGES: Dict[str, tuple] = {
    "t2m": (-60.0, 60.0),          # °C
    "wind100": (0.0, 60.0),        # m/s
    "ssrd": (0.0, 1500.0),         # W/m²
    "load_2da": (5000.0, 80000.0), # MW（CAISO 系统负荷合理区间）
}


def expected_cutoff_utc_zoneinfo(decision_date: str) -> Optional[str]:
    """独立口径：D 10:00 PT → UTC（用 zoneinfo，不依赖 schemas 的启发式）。"""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(f"{str(decision_date)[:10]}T10:00:00")
        aware = dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        return aware.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def check_dst(decision_date: str, actual_cutoff: str) -> List[Dict[str, str]]:
    """DST 检查：actual_cutoff 是否等于 zoneinfo 独立换算的 D 10:00 PT → UTC。

    Returns:
        空列表 = 通过；否则含 ERROR（时区换算 bug 或存储口径漂移）。
    """
    expected = expected_cutoff_utc_zoneinfo(decision_date)
    if expected is None:
        return [{"level": "WARNING",
                 "message": "zoneinfo 不可用，无法独立校验 DST 换算"}]
    if actual_cutoff != expected:
        return [{"level": "ERROR",
                 "message": f"decision_cutoff DST 不符: stored={actual_cutoff} "
                            f"expected(zoneinfo)={expected} (D 10:00 PT → UTC)"}]
    return []


def validate_collection(collector, records: List[Dict[str, Any]],
                        query_date: str, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    """对一次采集结果做质量校验（collector 为鸭子类型：source_name/expected_hours/...）。

    Returns:
        [{level: INFO|WARNING|ERROR, message}]
    """
    msgs: List[Dict[str, str]] = []

    if not records:
        return [{"level": "ERROR",
                 "message": f"{getattr(collector, 'source_name', '?')}: 0 records for {query_date}"}]

    source = getattr(collector, "source_name", "?")
    expected_cutoff = collector.decision_cutoff(query_date)

    # 1) decision_cutoff 一致性
    stored_cutoffs = {str(r.get("decision_cutoff", "")) for r in records}
    if stored_cutoffs != {expected_cutoff}:
        msgs.append({"level": "ERROR",
                     "message": f"{source}: decision_cutoff 不一致 stored={sorted(stored_cutoffs)} "
                                f"expected={expected_cutoff}"})

    # 2) 逐条 eligibility 四字段复算（防漂移）+ 硬规则（R7/R8）
    for r in records:
        try:
            rec = asof_from_dict(r)
        except Exception as exc:
            msgs.append({"level": "ERROR",
                         "message": f"{source}: 记录不可解析 {r.get('target_time')}: {exc}"})
            continue
        for key in ("time_eligible", "backtest_eligible", "production_eligible", "decision_eligible"):
            recomputed = bool(getattr(rec, key))
            stored = bool(r.get(key))
            if stored != recomputed:
                msgs.append({"level": "ERROR",
                             "message": f"{source}: {key} 漂移 target_time="
                                        f"{r.get('target_time')} field={r.get('field_name')} "
                                        f"stored={stored} recomputed={recomputed} "
                                        f"(available_at={r.get('available_at')}, "
                                        f"cutoff={r.get('decision_cutoff')})"})
        # R7：MOCK 数据禁止声明 backtest/production 可用
        if rec.is_mock and (bool(r.get("backtest_eligible")) or bool(r.get("production_eligible"))):
            msgs.append({"level": "ERROR",
                         "message": f"{source}: 硬规则 R7 is_mock=True 却声明可用 "
                                    f"target_time={r.get('target_time')} field={r.get('field_name')}"})
        # R8：not_backtest_safe 数据禁止声明 backtest 可用
        if rec.not_backtest_safe and bool(r.get("backtest_eligible")):
            msgs.append({"level": "ERROR",
                         "message": f"{source}: 硬规则 R8 not_backtest_safe=True 却声明 "
                                    f"backtest_eligible target_time={r.get('target_time')} "
                                    f"field={r.get('field_name')}"})

    # 3) 重复检测
    dup = Counter((r.get("target_time"), r.get("field_name")) for r in records)
    dups = [k for k, c in dup.items() if c > 1]
    if dups:
        msgs.append({"level": "ERROR",
                     "message": f"{source}: 重复 (target_time, field_name): {dups[:10]} x{len(dups)}"})

    # 4) 覆盖度 / 缺失率（按 field）
    fields = sorted({r.get("field_name") for r in records})
    expected_hours = getattr(collector, "expected_hours", 24)
    for f in fields:
        present = {r.get("target_time") for r in records
                   if r.get("field_name") == f and r.get("value") is not None}
        n = len(present)
        if n < expected_hours:
            rate = 1.0 - n / expected_hours
            msgs.append({"level": "WARNING",
                         "message": f"{source}: {f} 覆盖 {n}/{expected_hours} 小时，"
                                    f"缺失率 {rate:.1%}"})

    # 5) 值域 plausibility
    for r in records:
        v = r.get("value")
        f = r.get("field_name")
        lo, hi = PLAUSIBLE_RANGES.get(f, (None, None))
        if v is not None and lo is not None:
            try:
                fv = float(v)
                if not (lo <= fv <= hi):
                    msgs.append({"level": "WARNING",
                                 "message": f"{source}: {f} 值越界 target={r.get('target_time')} "
                                            f"value={fv} (期望 {lo}~{hi})"})
            except (TypeError, ValueError):
                msgs.append({"level": "WARNING",
                             "message": f"{source}: {f} 非数值 target={r.get('target_time')} "
                                        f"value={v!r}"})

    # 6) 降级声明
    if metadata.get("is_mock") or metadata.get("provenance") == "MOCK":
        msgs.append({"level": "WARNING",
                     "message": f"{source}: MOCK 数据（provenance=MOCK），仅演示采集流程，"
                                f"禁止用于生产/回测。生产需接真实 API。"})
    if metadata.get("not_backtest_safe") or getattr(collector, "not_backtest_safe", False):
        msgs.append({"level": "WARNING",
                     "message": f"{source}: not_backtest_safe=True：源未暴露逐日发布时间戳，"
                                f"禁止用于严格 as-of 回测。"})

    # 7) DST 检查（仅当有记录时）
    if expected_cutoff:
        msgs.extend(check_dst(query_date, expected_cutoff))

    return msgs
