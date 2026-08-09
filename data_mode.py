# -*- coding: utf-8 -*-
"""
data_mode.py —— 数据模式解析器（FULL / DEMO / MISSING）
=================================================================

单一事实来源：决定 Web / CLI / DecisionService 从哪套数据跑 Golden Case Demo。

三种模式：
  FULL   完整数据 artifacts 存在（code/data/canonical.parquet + predictions_v2.csv
         + stage3/risk_features.parquet）→ 走完整数据。
  DEMO   code/data 缺失（clean clone），但 demo_artifacts/ 存在（真实历史最小切片，
         只覆盖 5 个 Golden Cases 决策所需行）→ 走 demo 数据。DEMO ≠ MOCK：
         DEMO 是**真实历史记录**的子集，可给出真实推荐；MOCK 是编造数据，
         永不参与真实推荐。
  MISSING 两者都缺 → prepare_mvp.py 给出明确重建指引。

环境变量覆盖：MVP_DATA_MODE=demo|full（缺失/非法 → 自动探测）。
自动探测优先级：FULL 就绪 > DEMO 就绪 > MISSING。

用法：
    from data_mode import resolve_data_mode, MODE_FULL, MODE_DEMO
    info = resolve_data_mode()
    print(info.mode, info.data_dir)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent

#: 数据模式常量（字段名保留英文）
MODE_FULL = "FULL"
MODE_DEMO = "DEMO"
MODE_MISSING = "MISSING"

#: 环境变量名（覆盖自动探测）
DATA_MODE_ENV = "MVP_DATA_MODE"

#: demo 产物目录名
DEMO_DIR_NAME = "demo_artifacts"


@dataclass(frozen=True)
class DataModeInfo:
    """一次解析的结果：模式 + 各数据文件的绝对路径。

    字段名保留英文。DEMO 模式时 cases_path / metadata_path / manifest_path 非空。
    """

    mode: str                      # FULL | DEMO | MISSING
    reason: str                    # 人可读的判定原因
    data_dir: Path                 # 数据目录（FULL: code/data；DEMO: demo_artifacts）
    canon_path: Path               # canonical 数据集
    pred_path: Path                # 模型预测 predictions_v2
    risk_path: Path                # Risk Gate 历史风险特征
    cases_path: Optional[Path] = None      # DEMO: demo_artifacts/cases_demo.json
    metadata_path: Optional[Path] = None   # DEMO: demo_artifacts/metadata.json
    manifest_path: Optional[Path] = None   # DEMO: demo_artifacts/manifest.json

    @property
    def is_demo(self) -> bool:
        return self.mode == MODE_DEMO

    @property
    def is_full(self) -> bool:
        return self.mode == MODE_FULL

    @property
    def is_ready(self) -> bool:
        return self.mode in (MODE_FULL, MODE_DEMO)


# ---------------------------------------------------------------------------
# 就绪判定
# ---------------------------------------------------------------------------
def _full_paths(root: Path) -> dict:
    return {
        "canon": root / "code" / "data" / "canonical.parquet",
        "pred": root / "code" / "data" / "predictions_v2.csv",
        "risk": root / "code" / "data" / "stage3" / "risk_features.parquet",
    }


def _demo_paths(root: Path) -> dict:
    d = root / DEMO_DIR_NAME
    return {
        "data_dir": d,
        "canon": d / "canonical_demo.parquet",
        "pred": d / "predictions_demo.csv",
        "risk": d / "risk_features_demo.parquet",
        "cases": d / "cases_demo.json",
        "metadata": d / "metadata.json",
        "manifest": d / "manifest.json",
    }


def full_ready(repo_root: Optional[Path] = None) -> bool:
    root = Path(repo_root) if repo_root else REPO_ROOT
    p = _full_paths(root)
    return all(Path(v).exists() for v in p.values())


def demo_ready(repo_root: Optional[Path] = None) -> bool:
    root = Path(repo_root) if repo_root else REPO_ROOT
    p = _demo_paths(root)
    return all(Path(v).exists() for v in (p["canon"], p["pred"], p["risk"], p["cases"]))


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _info_full(root: Path) -> DataModeInfo:
    p = _full_paths(root)
    return DataModeInfo(
        mode=MODE_FULL,
        reason="完整数据 artifacts 存在（code/data/*）",
        data_dir=root / "code" / "data",
        canon_path=p["canon"],
        pred_path=p["pred"],
        risk_path=p["risk"],
    )


def _info_demo(root: Path) -> DataModeInfo:
    p = _demo_paths(root)
    return DataModeInfo(
        mode=MODE_DEMO,
        reason="完整数据缺失，使用 demo_artifacts/ 真实历史最小切片（DEMO ≠ MOCK）",
        data_dir=p["data_dir"],
        canon_path=p["canon"],
        pred_path=p["pred"],
        risk_path=p["risk"],
        cases_path=p["cases"],
        metadata_path=p["metadata"],
        manifest_path=p["manifest"],
    )


def _info_missing(root: Path, reason: str) -> DataModeInfo:
    full = _full_paths(root)
    return DataModeInfo(
        mode=MODE_MISSING,
        reason=reason,
        data_dir=root / "code" / "data",
        canon_path=full["canon"],
        pred_path=full["pred"],
        risk_path=full["risk"],
    )


def resolve_data_mode(
    repo_root: Optional[Path] = None,
    override: Optional[str] = None,
) -> DataModeInfo:
    """解析当前数据模式。

    Args:
        repo_root: 仓库根目录（缺省 = 本文件所在目录）。测试可传临时目录模拟 clean clone。
        override:  显式模式 "FULL" / "DEMO"；None → 读环境变量 MVP_DATA_MODE → 自动探测。
    """
    root = Path(repo_root) if repo_root else REPO_ROOT
    if override is None:
        override = os.environ.get(DATA_MODE_ENV, "") or ""
    override = str(override).strip().upper()

    if override not in ("", MODE_FULL, MODE_DEMO):
        override = ""

    if override == MODE_FULL:
        if full_ready(root):
            return _info_full(root)
        return _info_missing(root, f"环境变量 {DATA_MODE_ENV}=full，但完整 artifacts 缺失")

    if override == MODE_DEMO:
        if demo_ready(root):
            return _info_demo(root)
        return _info_missing(root, f"环境变量 {DATA_MODE_ENV}=demo，但 demo_artifacts 缺失")

    # 自动探测：FULL > DEMO > MISSING
    if full_ready(root):
        return _info_full(root)
    if demo_ready(root):
        return _info_demo(root)
    missing = [
        str(_full_paths(root)["canon"].relative_to(root)),
        str(_demo_paths(root)["canon"].relative_to(root)),
    ]
    return _info_missing(
        root,
        f"完整 artifacts 与 demo artifacts 均缺失（检查 {missing[0]} 或 {missing[1]}）",
    )


def require_ready(
    repo_root: Optional[Path] = None,
    override: Optional[str] = None,
) -> DataModeInfo:
    """解析并确保数据可用；不可用抛 RuntimeError（供运行入口使用）。"""
    info = resolve_data_mode(repo_root, override)
    if info.mode == MODE_MISSING:
        raise RuntimeError(
            "数据不可用：既无完整 artifacts（code/data/*）也无 demo artifacts"
            f"（{DEMO_DIR_NAME}/）。请运行 python build_demo_artifacts.py（在完整数据机上）"
            "或先重建完整数据层。"
        )
    return info


if __name__ == "__main__":
    import sys

    info = resolve_data_mode()
    print(f"DATA MODE: {info.mode}")
    print(f"  reason : {info.reason}")
    print(f"  data_dir : {info.data_dir}")
    print(f"  canon : {info.canon_path}")
    print(f"  pred  : {info.pred_path}")
    print(f"  risk  : {info.risk_path}")
    if info.cases_path:
        print(f"  cases : {info.cases_path}")
    print(f"  ready : {info.is_ready}")
    sys.exit(0 if info.is_ready else 1)
