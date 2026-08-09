# -*- coding: utf-8 -*-
"""
prepare_mvp.py —— V0.3.1.1 启动前自检脚本（业务人员可运行）
=================================================================

一键检查 Web + LLM Agent MVP 启动所需的"数据 artifact"与"Python 依赖"是否就绪。
不需要手工跑五六个脚本：只要本脚本报告 artifacts ready，就可以直接：

    python mvp_web.py

数据模式（data_mode.py 单一来源，自动探测）：
  FULL   完整 artifacts（code/data/*）存在 → 走完整数据。
  DEMO   code/data 缺失（clean clone），但 demo_artifacts/ 存在
         （真实历史最小切片，只覆盖 5 个 Golden Cases）→ 走 demo 数据。
         DEMO ≠ MOCK：DEMO 是真实历史切片，可真实推荐；MOCK 永不参与真实推荐。
  MISSING 两者都缺 → 输出明确缺失信息与重建命令。

环境变量覆盖：
  MVP_DATA_MODE=demo|full   强制指定模式（data_mode.py 单一来源）。
  DATA_MODE=demo|full       旧别名（兼容历史版本）。

用法：
    python prepare_mvp.py            # 自动探测模式
    python prepare_mvp.py --quick    # 只查核心 artifact（秒级）
    MVP_DATA_MODE=demo python prepare_mvp.py   # 强制 DEMO 检查
退出码：0 = 数据 artifact 就绪；1 = 缺失（需工程师介入）。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from data_mode import (  # noqa: E402
    DATA_MODE_ENV,
    DEMO_DIR_NAME,
    MODE_DEMO,
    MODE_FULL,
    MODE_MISSING,
    resolve_data_mode,
)

#: 完整模式核心 artifact —— 缺失则无法运行完整决策（必须有）
CORE_ARTIFACTS = [
    ("code/data/canonical.parquet", "特征 + 实际结算（canonical 数据集）"),
    ("code/data/predictions_v2.csv", "模型预测（expected_return / prob / confidence）"),
    ("code/data/stage3/risk_features.parquet", "Risk Gate 历史风险特征"),
    ("agent/case_library/cases.json", "历史案例库（手动维护）"),
    ("agent/case_library/cases_auto.json", "历史案例库（自动生成）"),
]

#: DEMO 模式核心 artifact —— 只覆盖 5 个 Golden Cases 决策所需行
DEMO_ARTIFACTS = [
    ("demo_artifacts/canonical_demo.parquet", "Golden Case 特征 + 实际结算切片"),
    ("demo_artifacts/predictions_demo.csv", "Golden Case 模型预测切片"),
    ("demo_artifacts/risk_features_demo.parquet", "Golden Case Risk Gate 风险特征切片"),
    ("demo_artifacts/cases_demo.json", "Golden Case 案例库切片（真实，as-of）"),
    ("demo_artifacts/manifest.json", "manifest（artifact_version / hashes / contains_mock=false）"),
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


def _mode_override() -> str:
    """有效模式覆盖（MVP_DATA_MODE 主 / DATA_MODE 旧别名）；无则空串。"""
    v = str(os.environ.get(DATA_MODE_ENV, "") or os.environ.get("DATA_MODE", "") or "").strip().upper()
    return v if v in (MODE_FULL, MODE_DEMO) else ""


def _check_artifacts(items: list) -> list:
    return [rel for rel, _desc in items if not (REPO_ROOT / rel).exists()]


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
    print("  CAISO Trading Decision Agent MVP · V0.3.1.2 Demo Freeze 启动自检")
    print("=" * 72)

    quick = "--quick" in sys.argv
    override = _mode_override()
    info = resolve_data_mode(override=override or None)
    mode = info.mode

    print(f"\n[0] 数据模式 : DATA MODE = {mode}")
    if override:
        print(f"    （由环境变量 {DATA_MODE_ENV or 'DATA_MODE'} = {override} 强制指定）")
    if mode == MODE_FULL:
        print("    完整数据 artifacts 存在 → FULL 模式（走 code/data/*）")
    elif mode == MODE_DEMO:
        print(f"    {info.reason} → DEMO 模式（走 {DEMO_DIR_NAME}/）")
        print("    ⚠ DEMO ≠ MOCK：DEMO 是真实历史最小切片（只覆盖 5 个 Golden Cases），可真实推荐；"
              "MOCK 永不参与真实推荐。")
    else:
        print(f"    {info.reason}")

    # ---- 按模式检查核心 artifact ----
    if mode == MODE_FULL:
        core_items = CORE_ARTIFACTS
        missing_core = _check_artifacts(CORE_ARTIFACTS)
        missing_opt = _check_artifacts(OPTIONAL_ARTIFACTS)
    elif mode == MODE_DEMO:
        core_items = DEMO_ARTIFACTS
        missing_core = _check_artifacts(DEMO_ARTIFACTS)
        missing_opt = []
    else:
        core_items = DEMO_ARTIFACTS
        missing_core = _check_artifacts(DEMO_ARTIFACTS)
        missing_opt = []

    print("\n[1] 核心数据 artifact（当前模式）")
    for rel, desc in core_items:
        ok = rel not in missing_core
        print(f"    [{'OK' if ok else 'X'}] {rel:<46} {desc}")

    if not quick and missing_opt:
        print("\n[2] 辅助 artifact（缺失仅提示）")
        for rel in missing_opt:
            print(f"    [SKIP] {rel}   （可选，不影响决策）")

    if not quick:
        missing_mods = _check_modules()
        if missing_mods:
            print("\n[3] Python 依赖")
            for name in missing_mods:
                print(f"    [X] {name}   （请运行：pip install -r requirements.txt）")
    else:
        missing_mods = []

    print()
    if missing_core:
        print("X 核心 artifact 缺失 —— 需要先准备数据：")
        if mode == MODE_DEMO or mode == MODE_MISSING:
            print("    （方案 A，推荐）在【完整数据机】上生成 demo 切片并提交：")
            print("        python build_demo_artifacts.py")
            print("        生成后把 demo_artifacts/（含 manifest.json）随代码一起提交，")
            print("        clean clone 即可在 DEMO 模式直接跑 Golden Case Demo。")
        if mode != MODE_MISSING:
            print("\n    （方案 B）重建完整数据层（工程师）：")
        for rel in missing_core:
            hint = REBUILD_HINTS.get(rel, "（联系工程师确认生成脚本）")
            if hint and mode != MODE_MISSING:
                print(f"        - {rel}")
                print(f"            建议执行：{hint}")
        print("\n    （业务人员无需手工复跑；请把以上输出转发给工程师，或运行提示脚本后重试。）")
        print("=" * 72)
        return 1

    print("[OK] ARTIFACTS READY —— 可直接启动 Web MVP：")
    print()
    print("    pip install -r requirements.txt")
    print("    python mvp_web.py            # 打开 http://127.0.0.1:5000")
    print("    python mvp_web.py --offline  # 不取外部 GFS 证据，纯本地演示")
    if mode == MODE_DEMO:
        print()
        print("    当前为 DEMO 模式（真实历史最小切片，只覆盖 5 个 Golden Cases）：")
        for rel, _desc in DEMO_ARTIFACTS[:4]:
            print(f"      · {rel}")
        print("    12 项验收测试：python code/tests/test_mvp_v031.py")
        print("    Demo 一致性 / 隔离测试：python code/tests/test_demo_artifacts.py")
    else:
        print()
        print("    12 项验收测试：python code/tests/test_mvp_v031.py")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
