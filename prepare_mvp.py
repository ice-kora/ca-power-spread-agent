# -*- coding: utf-8 -*-
"""
prepare_mvp.py —— V0.3.1 启动前自检脚本（业务人员可运行）
=================================================================

一键检查 Web + LLM Agent MVP 启动所需的"数据 artifact"与"Python 依赖"是否就绪。
不需要手工跑五六个脚本：只要本脚本报告 artifacts ready，就可以直接：

    python mvp_web.py

检查内容：
  1. 核心数据 artifact（决策必须）：
       code/data/canonical.parquet
       code/data/predictions_v2.csv
       code/data/stage3/risk_features.parquet
       agent/case_library/cases.json + cases_auto.json
  2. 辅助 artifact（可视化/卡片，缺失仅提示）：
       code/data/predictions_v2_val.csv
  3. 关键 Python 依赖（缺失会给出安装命令，不中断）。

若 artifact 缺失：脚本给出"需要重新生成"的具体命令，业务人员可把本输出转发给工程师。
若全部就绪：打印 "ARTIFACTS READY ✓" 与启动命令。

用法：
    python prepare_mvp.py            # 默认模式（全量检查）
    python prepare_mvp.py --quick    # 只查核心 artifact（秒级）
退出码：0 = 核心 artifact 就绪；1 = 核心 artifact 缺失（需工程师介入）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

#: 核心 artifact —— 缺失则无法运行决策（必须有）
CORE_ARTIFACTS = [
    ("code/data/canonical.parquet", "特征 + 实际结算（canonical 数据集）"),
    ("code/data/predictions_v2.csv", "模型预测（expected_return / prob / confidence）"),
    ("code/data/stage3/risk_features.parquet", "Risk Gate 历史风险特征"),
    ("agent/case_library/cases.json", "历史案例库（手动维护）"),
    ("agent/case_library/cases_auto.json", "历史案例库（自动生成）"),
]

#: 辅助 artifact —— 缺失不影响决策，仅影响可视化/校准脚本
OPTIONAL_ARTIFACTS = [
    ("code/data/predictions_v2_val.csv", "验证集预测（回测/校准用）"),
    ("code/data/feature_schema.json", "特征 schema（审计用）"),
]

#: 运行 Web 决策必需的关键依赖
REQUIRED_MODULES = [
    ("flask", "Web 服务"),
    ("pandas", "数据处理"),
    ("numpy", "数值计算"),
    ("pyarrow", "parquet 读取"),
]

#: 缺失 artifact 时的重建指引（只列出最小集合，工程师按需复跑）
REBUILD_HINTS = {
    "code/data/canonical.parquet": "python code/canonical.py",
    "code/data/predictions_v2.csv": "python code/model_v2.py",
    "code/data/stage3/risk_features.parquet": "python code/analysis/agent_d_features.py",
    "agent/case_library/cases.json": "python agent/case_library/init_cases.py",
    "agent/case_library/cases_auto.json": "python agent/case_library/auto_generate_cases.py",
}


def _check_artifacts() -> tuple:
    missing_core: list = []
    missing_opt: list = []
    for rel, _desc in CORE_ARTIFACTS:
        if not (REPO_ROOT / rel).exists():
            missing_core.append(rel)
    for rel, _desc in OPTIONAL_ARTIFACTS:
        if not (REPO_ROOT / rel).exists():
            missing_opt.append(rel)
    return missing_core, missing_opt


def _check_modules() -> list:
    missing: list = []
    for name, _desc in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    return missing


def main() -> int:
    print("=" * 72)
    print("  CAISO Trading Decision Agent · V0.3.1 启动自检")
    print("=" * 72)

    quick = "--quick" in sys.argv
    missing_core, missing_opt = _check_artifacts()
    missing_mods = _check_modules() if not quick else []

    print("\n[1] 核心数据 artifact")
    for rel, desc in CORE_ARTIFACTS:
        ok = rel not in missing_core
        print(f"    [{'OK' if ok else 'X'}] {rel:<42} {desc}")

    if not quick:
        print("\n[2] 辅助 artifact（缺失仅提示）")
        if missing_opt:
            for rel in missing_opt:
                print(f"    [SKIP] {rel}   （可选，不影响决策）")
        else:
            print("    [OK] 全部存在")

    if not quick and missing_mods:
        print("\n[3] Python 依赖")
        for name in missing_mods:
            print(f"    [X] {name}   （请运行：pip install -r requirements.txt）")

    print()
    if missing_core:
        print("X 核心 artifact 缺失 —— 需要先重建数据层：")
        for rel in missing_core:
            hint = REBUILD_HINTS.get(rel, "（联系工程师确认生成脚本）")
            print(f"    - {rel}")
            print(f"        建议执行：{hint}")
        print("\n    （业务人员无需手工复跑；请把以上输出转发给工程师，或直接运行提示脚本后重试。）")
        print("=" * 72)
        return 1

    print("[OK] ARTIFACTS READY —— 可直接启动 Web MVP：")
    print()
    print("    pip install -r requirements.txt")
    print("    python mvp_web.py            # 打开 http://127.0.0.1:5000")
    print("    python mvp_web.py --offline  # 不取外部 GFS 证据，纯本地演示")
    print()
    print("    12 项验收测试：python code/tests/test_mvp_v031.py")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
