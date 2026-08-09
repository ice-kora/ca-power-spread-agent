# -*- coding: utf-8 -*-
"""
code/data_acquisition/base.py —— 采集器基类（Agent E）

统一采集流水线（单一事实来源）：
    run(query_date)
      ├─ 1. fetch 真实 API（network_enabled=False 或失败 → 跳过）
      ├─ 2. 失败降级：读缓存 raw（provenance=CACHE）→ 仍无 → 确定性 MOCK（is_mock=True）
      ├─ 3. 落盘 raw response（JSON envelope：_meta + data，可复现）
      ├─ 4. normalize → List[AsOfRecord]（decision_eligible 由 schemas 程序计算）
      ├─ 5. 落盘 normalized（含全部时间戳 + validation 结果）
      └─ 6. 返回 CollectionResult
  时间戳三件套：published_at / available_at / retrieved_at（一律 UTC naive ISO）。
  降级声明：MOCK / CACHE 数据在 metadata.provenance 与 validation 中明确标注，
  绝不冒充真实预报；生产需接真实 API。

依赖：code/data_acquisition/schemas.py（AsOfRecord / resolve_available_at / 时间工具）。
"""

from __future__ import annotations

import json
import math
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.schemas import (  # noqa: E402
    MODE_BACKTEST,
    AsOfRecord,
    _coerce_bool,
    resolve_available_at,
)


class FetchError(Exception):
    """真实源拉取失败（网络 / HTTP / 解析）。触发降级路径。"""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def utc_now_naive() -> str:
    """当前墙钟 → UTC naive ISO（采集审计用 retrieved_at）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def add_days(date_str: str, n: int) -> str:
    """'YYYY-MM-DD' + n 天 → 'YYYY-MM-DD'。"""
    d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d") + timedelta(days=n)
    return d.strftime("%Y-%m-%d")


def json_safe(value: Any) -> Any:
    """递归把 float NaN/±Inf 转 None，确保 raw 落盘为严格 JSON。"""
    if value is None:
        return None
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# CollectionResult
# ---------------------------------------------------------------------------
@dataclass
class CollectionResult:
    """一次采集的完整结果（含落盘路径与审计字段）。"""

    source: str
    query_date: str
    target_date: str
    mode: str
    raw_path: Optional[Path]
    normalized_path: Optional[Path]
    records: List[Dict[str, Any]] = field(default_factory=list)
    timestamps: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    validation: List[Dict[str, str]] = field(default_factory=list)

    @property
    def n_records(self) -> int:
        return len(self.records)

    @property
    def n_eligible(self) -> int:
        return sum(1 for r in self.records if bool(r.get("decision_eligible")))

    @property
    def n_errors(self) -> int:
        return sum(1 for v in self.validation if v.get("level") == "ERROR")

    def summary(self) -> str:
        return (
            f"[{self.source}] {self.query_date} → {self.target_date} "
            f"records={self.n_records} eligible={self.n_eligible} "
            f"provenance={self.metadata.get('provenance')} "
            f"errors={self.n_errors}"
        )


# ---------------------------------------------------------------------------
# Collector 基类
# ---------------------------------------------------------------------------
class Collector(ABC):
    """采集器基类。子类实现 _fetch_raw / _normalize / _mock_raw。"""

    #: 数据源标识（写入 source 字段 / 缓存路径）
    source_name: str = "base"
    #: 默认缓存目录（cache/<source_name>/）
    default_cache_dir: Path = Path(__file__).resolve().parent / "cache"
    #: 目标日预期小时数（覆盖度校验用）
    expected_hours: int = 24
    #: 数据源是否严格 as-of backtest safe（CAISO=否，GFS=是）
    not_backtest_safe: bool = False

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        mode: str = MODE_BACKTEST,
        network_enabled: bool = True,
    ) -> None:
        """
        Args:
            cache_dir: 缓存/输出目录；缺省 cache/<source_name>/
            mode:      采集模式 BACKTEST / PRODUCTION（见 schemas.MODES）
            network_enabled: False = 强制离线（直接走缓存/MOCK 降级）
        """
        self.cache_dir = Path(cache_dir) if cache_dir else self.default_cache_dir / self.source_name
        self.mode = mode
        self.network_enabled = network_enabled

    # -- 主入口 -----------------------------------------------------------
    def run(
        self,
        query_date: str,
        save: bool = True,
        use_cache: bool = True,
    ) -> CollectionResult:
        """执行一次采集：fetch → 降级 → 落盘 → normalize → 校验。

        Args:
            query_date: 决策日 D（ISO YYYY-MM-DD）；目标日 T = D+1。
            save:       False 时不落盘（测试/预览用）。
            use_cache:  网络失败时是否允许读缓存（False = 直接 MOCK）。
        Returns:
            CollectionResult（records 为 AsOfRecord.to_dict() 列表）
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        query_date = str(query_date)[:10]
        target_date = self.target_date(query_date)
        retrieved_at = utc_now_naive()

        # 1) 真实源
        payload: Optional[Dict[str, Any]] = None
        provenance, is_mock, last_error = "LIVE", False, ""
        if self.network_enabled:
            try:
                payload = self._fetch_raw(query_date)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        else:
            last_error = "network_enabled=False (offline)"

        # 2) 降级：缓存 → MOCK
        if payload is None:
            cached = self._load_cached_raw(query_date) if use_cache else None
            if cached is not None:
                payload = json_safe(cached.get("data", cached))
                meta = cached.get("_meta", {}) if isinstance(cached, dict) else {}
                provenance = "CACHE"                      # 本次运行取自缓存
                is_mock = bool(meta.get("is_mock", False))  # 底层数据是否原本为 MOCK
                last_error = last_error or "cache fallback"
            else:
                payload = self._mock_raw(query_date)
                provenance, is_mock = "MOCK", True
                last_error = last_error or "no cache, deterministic MOCK"

        payload = json_safe(payload or {})

        # 3) 落盘 raw
        raw_path = None
        if save:
            raw_path = self._save_raw(payload, query_date, provenance, retrieved_at)

        # 4) normalize
        records = self._normalize(
            payload, query_date, provenance=provenance,
            is_mock=is_mock, retrieved_at=retrieved_at,
        )
        record_dicts = [r.to_dict() for r in records]

        # 5) 时间戳 / 元数据 / 校验
        timestamps = self._collect_timestamps(record_dicts, retrieved_at)
        metadata = self._make_metadata(
            query_date, target_date, payload, provenance, is_mock, last_error, retrieved_at)
        validation = validate_collection(self, record_dicts, query_date, metadata)

        # 6) 落盘 normalized
        normalized_path = None
        if save:
            normalized_path = self._save_normalized(
                query_date, record_dicts, timestamps, metadata, validation)

        return CollectionResult(
            source=self.source_name,
            query_date=query_date,
            target_date=target_date,
            mode=self.mode,
            raw_path=raw_path,
            normalized_path=normalized_path,
            records=record_dicts,
            timestamps=timestamps,
            metadata=metadata,
            validation=validation,
        )

    # -- 子类实现 ---------------------------------------------------------
    @abstractmethod
    def _fetch_raw(self, query_date: str) -> Dict[str, Any]:
        """拉取真实 raw response，返回可 JSON 序列化 dict。失败抛 FetchError。"""

    @abstractmethod
    def _normalize(
        self,
        payload: Dict[str, Any],
        query_date: str,
        *,
        provenance: str,
        is_mock: bool,
        retrieved_at: str,
    ) -> List[AsOfRecord]:
        """把 raw payload 转成 AsOfRecord 列表（decision_eligible 由 schemas 计算）。"""

    @abstractmethod
    def _mock_raw(self, query_date: str) -> Dict[str, Any]:
        """确定性合成 raw（is_mock=True，仅演示/离线降级，禁止冒充真实预报）。"""

    # -- 派生常量（子类可覆写）--------------------------------------------
    def target_date(self, query_date: str) -> str:
        """目标交付日 T = query_date(决策日) + 1。"""
        return add_days(query_date, 1)

    def decision_cutoff(self, query_date: str) -> str:
        """决策截止（UTC naive）：D 10:00 PT。复用 schemas.make_decision_cutoff。"""
        from code.data_acquisition.schemas import make_decision_cutoff
        return make_decision_cutoff(query_date) or ""

    def cache_slug(self, query_date: str) -> str:
        """缓存文件名主干（子类可加 cycle 等后缀）。"""
        return str(query_date)[:10]

    # -- 落盘 / 读缓存 -----------------------------------------------------
    def _save_raw(self, payload: Dict[str, Any], query_date: str,
                  provenance: str, retrieved_at: str) -> Path:
        envelope = {
            "_meta": {
                "source": self.source_name,
                "query_date": query_date,
                "target_date": self.target_date(query_date),
                "mode": self.mode,
                "provenance": provenance,
                "is_mock": provenance == "MOCK",
                "retrieved_at": retrieved_at,
            },
            "data": json_safe(payload),
        }
        path = self.cache_dir / f"{self.cache_slug(query_date)}.raw.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh, ensure_ascii=False, allow_nan=False)
        return path

    def _load_cached_raw(self, query_date: str) -> Optional[Dict[str, Any]]:
        path = self.cache_dir / f"{self.cache_slug(query_date)}.raw.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _save_normalized(
        self, query_date: str, records: List[Dict[str, Any]],
        timestamps: Dict[str, str], metadata: Dict[str, Any],
        validation: List[Dict[str, str]],
    ) -> Path:
        doc = {
            "schema_version": "asof_v1",
            "source": self.source_name,
            "query_date": query_date,
            "target_date": self.target_date(query_date),
            "mode": self.mode,
            "timestamps": timestamps,
            "metadata": metadata,
            "validation": validation,
            "records": records,
        }
        path = self.cache_dir / f"{self.cache_slug(query_date)}.normalized.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, allow_nan=False)
        return path

    # -- 组装辅助 ---------------------------------------------------------
    def _collect_timestamps(self, records: List[Dict[str, Any]],
                            retrieved_at: str) -> Dict[str, str]:
        if records:
            first = records[0]
            return {
                "published_at": str(first.get("published_at", "")),
                "available_at": str(first.get("available_at", "")),
                "retrieved_at": str(first.get("retrieved_at", "") or retrieved_at),
                "decision_cutoff": str(first.get("decision_cutoff", "")),
            }
        return {
            "published_at": "", "available_at": "",
            "retrieved_at": retrieved_at, "decision_cutoff": "",
        }

    def _make_metadata(
        self, query_date: str, target_date: str, payload: Dict[str, Any],
        provenance: str, is_mock: bool, last_error: str, retrieved_at: str,
    ) -> Dict[str, Any]:
        return {
            "source": self.source_name,
            "query_date": query_date,
            "target_date": target_date,
            "mode": self.mode,
            "provenance": provenance,
            "degraded": provenance in ("CACHE", "MOCK"),
            "is_mock": is_mock,
            "not_backtest_safe": self.not_backtest_safe,
            "last_error": last_error,
            "retrieved_at": retrieved_at,
        }

    def _make_record(
        self,
        query_date: str,
        *,
        field_name: str,
        target_time: str,
        value: Any,
        unit: str,
        node: str,
        region: str,
        latitude: Optional[float],
        longitude: Optional[float],
        forecast_run: str,
        model_run_time: str = "",
        issue_time: str,
        published_at: str,
        available_at: Optional[str] = None,
        retrieved_at: str,
        raw_source_id: str,
        is_mock: bool = False,
        source_type: str = "",
        not_backtest_safe: Optional[bool] = None,
    ) -> AsOfRecord:
        """构造一条 AsOfRecord：available_at 按模式解析，decision_eligible 程序计算。

        Provenance 字段（is_mock / not_backtest_safe / source_type）写入每条记录，
        保证进入模型/Risk Gate 前可逐字段追溯来源与降级状态。

        Args:
            model_run_time: 模型起报时刻（GFS run 起始 = initialization_time）。
                与 issue_time 同义但显式拆分（P0-1 五个时间概念），供时间门槛校验。
            available_at:   可选。显式给定则直接使用（不回退到模式解析）；
                采集器用于"无可靠 vintage 的 run"强制置空（None 保持默认解析）。
        """
        from code.data_acquisition.schemas import infer_source_type
        if available_at is None:
            available_at = resolve_available_at(published_at, retrieved_at, self.mode) or ""
        return AsOfRecord(
            source=self.source_name,
            source_type=source_type or infer_source_type(self.source_name, field_name),
            is_mock=_coerce_bool(is_mock),
            not_backtest_safe=(
                self.not_backtest_safe
                if not_backtest_safe is None
                else _coerce_bool(not_backtest_safe)
            ),
            field_name=field_name,
            forecast_run=forecast_run,
            model_run_time=model_run_time,
            issue_time=issue_time,
            published_at=published_at,
            available_at=available_at,
            retrieved_at=retrieved_at,
            target_time=target_time,
            node=node,
            region=region,
            latitude=latitude,
            longitude=longitude,
            value=value,
            decision_cutoff=self.decision_cutoff(query_date),
            raw_source_id=raw_source_id,
            mode=self.mode,
        ).normalize()


# 延迟 import，避免循环依赖（validation 需要 Collector 类型）
from code.data_acquisition.validation import validate_collection  # noqa: E402
