# -*- coding: utf-8 -*-
"""
code/data_acquisition/run_acquisition.py —— 采集框架 CLI（Agent E · PoC）

示例：
    # 真实拉取 GFS（决策日 2026-07-08，默认节点 CONTROLX_1_N001）
    python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source gfs

    # 真实拉取 CAISO SLD_FCST 负荷预报
    python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source caiso

    # 两个源都拉
    python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source all

    # 离线降级（网络失败 / 缓存缺失时用确定性 MOCK，并明确标注）
    python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source all --offline

    # 生产模式（available_at = max(published, retrieved)）+ 不落盘
    python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source gfs --mode PRODUCTION --no-save

退出码：0=成功（含降级/警告）；2=ERROR 级校验失败（无记录 / 时间口径漂移等）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.base import CollectionResult  # noqa: E402
from code.data_acquisition.caiso_oasis import CAISOLoadForecastCollector  # noqa: E402
from code.data_acquisition.schemas import MODE_BACKTEST, MODE_PRODUCTION  # noqa: E402
from code.data_acquisition.weather_gfs import DEFAULT_CYCLE, GFSWeatherCollector  # noqa: E402


def _sp(x) -> None:
    """控制台安全打印（GBK 控制台对部分字符会炸）。"""
    try:
        print(x)
    except Exception:
        print(str(x).encode("ascii", "replace").decode("ascii"))


def _make_collectors(args):
    collectors = []
    modes = {"BACKTEST": MODE_BACKTEST, "PRODUCTION": MODE_PRODUCTION}
    mode = modes.get(args.mode.upper(), MODE_BACKTEST)
    if args.source in ("gfs", "all"):
        collectors.append(GFSWeatherCollector(
            node=args.node, cycle=args.cycle, cache_dir=args.cache_dir,
            mode=mode, network_enabled=not args.offline))
    if args.source in ("caiso", "all"):
        collectors.append(CAISOLoadForecastCollector(
            resource=args.resource, cache_dir=args.cache_dir,
            mode=mode, network_enabled=not args.offline))
    return collectors


def _print_result(res: CollectionResult) -> None:
    _sp(res.summary())
    _sp("  timestamps: " + json.dumps(res.timestamps, ensure_ascii=False))
    _sp("  metadata:   " + json.dumps({k: res.metadata.get(k) for k in (
        "provenance", "degraded", "is_mock", "not_backtest_safe", "last_error")},
        ensure_ascii=False))
    if res.raw_path:
        _sp(f"  raw_path:         {res.raw_path}")
    if res.normalized_path:
        _sp(f"  normalized_path:  {res.normalized_path}")
    _sp("  validation:")
    for v in res.validation:
        _sp(f"    [{v['level']}] {v['message']}")
    _sp("  sample records:")
    for r in res.records[:3]:
        _sp("    " + json.dumps({
            "field_name": r.get("field_name"), "target_time": r.get("target_time"),
            "value": r.get("value"), "published_at": r.get("published_at"),
            "available_at": r.get("available_at"), "decision_cutoff": r.get("decision_cutoff"),
            "decision_eligible": r.get("decision_eligible")}, ensure_ascii=False))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CAISO as-of 数据采集框架 PoC")
    p.add_argument("--date", default="2026-07-08", help="决策日 D (YYYY-MM-DD)，目标日 T=D+1")
    p.add_argument("--source", choices=["gfs", "caiso", "all"], default="all")
    p.add_argument("--node", default="CONTROLX_1_N001",
                   help="GFS 节点（SNLNDRO_1_N001 / CONTROLX_1_N001 / ELCAJNGT_7_N001）")
    p.add_argument("--cycle", default=DEFAULT_CYCLE,
                   help=f"GFS run 周期（00Z/06Z/12Z/18Z；默认 {DEFAULT_CYCLE}，"
                        f"仅 00Z/06Z 可严格回测）")
    p.add_argument("--resource", default="CA ISO-TAC",
                   help="OASIS TAC_AREA_NAME（系统全口径='CA ISO-TAC'）")
    p.add_argument("--mode", default="BACKTEST", choices=["BACKTEST", "PRODUCTION"])
    p.add_argument("--offline", action="store_true",
                   help="强制离线：走缓存/MOCK 降级（不联网）")
    p.add_argument("--no-save", action="store_true", help="不落盘 raw/normalized")
    p.add_argument("--cache-dir", type=Path, default=None,
                   help="缓存/输出目录（缺省 code/data_acquisition/cache/<source>/）")
    args = p.parse_args(argv)

    _sp(f"== data_acquisition PoC · date={args.date} mode={args.mode} "
        f"offline={args.offline} ==")
    n_error = 0
    for col in _make_collectors(args):
        try:
            res = col.run(args.date, save=not args.no_save)
        except Exception as exc:
            _sp(f"[{col.source_name}] 运行异常: {type(exc).__name__}: {exc}")
            n_error += 1
            continue
        _print_result(res)
        if res.n_errors:
            n_error += res.n_errors
    _sp("== done ==")
    return 0 if n_error == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
