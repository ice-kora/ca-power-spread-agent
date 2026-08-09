# -*- coding: utf-8 -*-
"""
run_tests.py —— 全仓库唯一测试入口（V0.3.1.2 P0 · 唯一测试计数）
================================================================

问题
----
V0.3.1.1 之前各测试文件各自运行并打印自己的 TOTAL/PASSED/FAILED/SKIPPED，
汇总时报 "300（code 220 + agent 65 + hardening 15）" 属分段相加的**重复计数**，
且数字写死会随测试增减漂移。

本脚本做**一次** unittest discovery（全仓库 test_*.py），运行一遍，输出唯一
TOTAL / PASSED / FAILED / SKIPPED。不硬编码任何数字；测试数随仓库真实变化。

语义
----
  * PASSED = testsRun − FAILED − SKIPPED（一次运行的结果，无分段相加）。
  * SKIPPED 单独计数（如 clean clone 上 FULL/DEMO 一致性自动跳过），不算 FAILED。
  * 下划线前缀文件（如 _clean_clone_simulation.py）不属于测试套件，不收集。

用法（仓库根目录）
------------------
    python run_tests.py              # 一次发现 + 一次运行 + 唯一计数
    python run_tests.py --verbose    # 逐条显示测试名
    python run_tests.py --list       # 只列出将收集的测试（不运行），用于核对发现范围
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def collect(start_dir: str = ".") -> unittest.TestSuite:
    """一次 discovery：全仓库 test_*.py（不含下划线前缀文件）。"""
    loader = unittest.TestLoader()
    return loader.discover(start_dir=start_dir, pattern="test_*.py",
                           top_level_dir=start_dir)


def _flatten(suite) -> list:
    """把 discover 返回的嵌套 TestSuite 递归展开成单个测试对象列表。"""
    out = []
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            out.extend(_flatten(t))
        else:
            out.append(t)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="全仓库唯一测试入口（一次发现，唯一计数）")
    ap.add_argument("--verbose", action="store_true", help="逐条显示测试名")
    ap.add_argument("--list", action="store_true", help="只列出将收集的测试（不运行）")
    args = ap.parse_args()

    suite = collect(str(REPO_ROOT))
    if args.list:
        for t in _flatten(suite):
            print(t.id())
        print(f"（共 {len(_flatten(suite))} 项测试）")
        return 0

    runner = unittest.TextTestRunner(stream=sys.stdout,
                                     verbosity=2 if args.verbose else 1)
    result = runner.run(suite)

    n_total = result.testsRun
    n_failed = len(result.failures) + len(result.errors)
    n_skipped = len(result.skipped)
    n_passed = n_total - n_failed - n_skipped

    print("=" * 70)
    print("全仓库唯一测试计数（一次 discovery，无重复计数）")
    print(f"TOTAL   : {n_total}")
    print(f"PASSED  : {n_passed}")
    print(f"FAILED  : {n_failed}")
    print(f"SKIPPED : {n_skipped}")
    if result.failures or result.errors:
        print("-" * 70)
        for t, tb in result.failures + result.errors:
            print(f"  FAILED  {t.id()}")
            print(f"          {tb.splitlines()[-1]}")
    print("=" * 70)
    return 1 if n_failed else 0


if __name__ == "__main__":
    sys.exit(main())
