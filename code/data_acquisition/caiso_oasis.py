# -*- coding: utf-8 -*-
"""
code/data_acquisition/caiso_oasis.py —— CAISO 官方 DA 负荷预报采集器（Agent E）

数据源：CAISO OASIS `SLD_FCST`（系统日前负荷预报，`SYS_FCST_DA_MW`，MW）。
  - 端点：http://oasis.caiso.com/oasisapi/SingleZip（`resultformat=6` → ZIP 内 CSV）
  - 返回行含 `OPR_DT`（PT 运营日）+ `OPR_HR`（PT 小时 1..24）——直接对齐项目
    hour∈1..24 约定（H1=00:00–01:00 PT），无需手动 UTC 换算。
  - 默认取 `TAC_AREA_NAME == "CA ISO-TAC"`（系统全口径 DA 负荷预报；
    其余区域资源如 PGE-TAC / SCE-TAC 可配置）。

as-of 口径（保守，标注 NOT_BACKTEST_SAFE）：
  - 目标日 T（=决策日 D+1）的 SLD_FCST（OPR_DT=T）是 DAM 的**输入**，
    必然在 D 日 10:00 PT 市场收盘前发布。
  - 但 OASIS 响应**不暴露逐日发布时间戳**（只有检索时刻）→ 无法 pin 精确 vintage，
    故 published_at 取最晚必然可用时点 = D 10:00 PT（== cutoff，边界 eligible），
    并标记 not_backtest_safe=True（禁止用于严格 as-of 回测）。
  - decision_eligible 由 schemas 程序计算；mode=BACKTEST 时
    available_at=published_at=cutoff → eligible。

降级：网络失败 → 读缓存 raw → 确定性 MOCK（is_mock=True，明确标注）。
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.data_acquisition.base import Collector, FetchError  # noqa: E402
from code.data_acquisition.schemas import (  # noqa: E402
    AsOfRecord,
    target_time_pt_to_utc,
)

_OASIS_URL = "http://oasis.caiso.com/oasisapi/SingleZip"
_UA = "caiso-data-acquisition-poc/0.1 (OASIS SLD_FCST collector)"


class CAISOLoadForecastCollector(Collector):
    """CAISO OASIS SLD_FCST 系统 DA 负荷预报采集器（骨架 + 真实可用）。"""

    source_name = "CAISO_OASIS_SLD_FCST"
    not_backtest_safe = True   # OASIS 不暴露逐日发布时间戳 → 严格回测不安全

    def __init__(
        self,
        resource: str = "CA ISO-TAC",
        market_run: str = "DAM",
        cache_dir: Optional[Path] = None,
        mode: str = "BACKTEST",
        network_enabled: bool = True,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        super().__init__(cache_dir=cache_dir, mode=mode, network_enabled=network_enabled)
        self.resource = resource
        self.market_run = market_run
        self.node = "CAISO_TAC"
        self.field_name = "load_2da"
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    # ------------------------------------------------------------------ fetch
    def _build_url(self, query_date: str) -> str:
        from code.data_acquisition.base import add_days
        t = self.target_date(str(query_date)[:10])   # T = D+1（PT 运营日）
        # OASIS SLD_FCST 按 INTERVALSTARTTIME_GMT 过滤。OPR_DT=T 的 24 小时
        # 区间在 UTC 覆盖 [TT07:00Z（PDT H1）/ TT08:00Z（PST H1）,
        #                     (T+1)T06:00Z（PDT H24）/ (T+1)T07:00Z（PST H24）]。
        # 用 25h 窗口 [T 07:00Z, T+1 08:00Z] 对冬夏令时都覆盖 H1..H24。
        # 注意：OASIS 要求 startdatetime/enddatetime 为 'YYYYMMDDTHH:MM-0000'
        # （日期无连字符）；ISO 'YYYY-MM-DDTHH:MM-0000' 会返回 INVALID_REQUEST。
        start = f"{t.replace('-', '')}T07:00-0000"
        end = f"{add_days(t, 1).replace('-', '')}T08:00-0000"
        return (
            f"{_OASIS_URL}?queryname=SLD_FCST&startdatetime={start}&enddatetime={end}"
            f"&market_run_id={self.market_run}&version=1&resultformat=6"
        )

    def _fetch_raw(self, query_date: str) -> Dict[str, Any]:
        url = self._build_url(query_date)
        body: Optional[bytes] = None
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    body = resp.read()
                break
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
        if body is None:
            raise FetchError(f"OASIS 请求失败 {query_date}（{self.max_retries} 次）: "
                             f"{type(last_exc).__name__}: {last_exc}")
        try:
            z = zipfile.ZipFile(io.BytesIO(body))
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise FetchError(f"OASIS 返回无 CSV 条目: {z.namelist()[:3]}")
            text = z.read(names[0]).decode("utf-8-sig", errors="replace")
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError(f"OASIS ZIP/CSV 解析失败: {type(exc).__name__}: {exc}") from exc
        rows = list(csv.DictReader(text.splitlines()))
        if not rows:
            raise FetchError(f"OASIS SLD_FCST 无数据行: {url}")
        return {
            "_request_url": url,
            "query_date": str(query_date)[:10],
            "target_date": self.target_date(str(query_date)[:10]),
            "rows": rows,
        }

    # ------------------------------------------------------------------ normalize
    def _normalize(
        self,
        payload: Dict[str, Any],
        query_date: str,
        *,
        provenance: str,
        is_mock: bool,
        retrieved_at: str,
    ) -> List[AsOfRecord]:
        target = self.target_date(query_date)
        rows = (payload or {}).get("rows", []) or []
        cutoff = self.decision_cutoff(query_date)
        # 保守口径：published_at = 最晚必然可用时点 = D 10:00 PT（== cutoff，边界 eligible）
        published_at = cutoff
        records: List[AsOfRecord] = []
        seen_target: set = set()
        for r in rows:
            if str(r.get("OPR_DT", "")).strip()[:10] != target:
                continue
            if r.get("TAC_AREA_NAME") != self.resource:
                continue
            try:
                h = int(r.get("OPR_HR"))
            except (TypeError, ValueError):
                continue
            if not (1 <= h <= 24):
                continue
            tt = target_time_pt_to_utc(target, h)
            if not tt or tt in seen_target:
                # DST 回拨日（25h）同一 OPR_HR 会重复：保留首个，避免重复主键
                continue
            seen_target.add(tt)
            raw_mw = r.get("MW")
            value = None
            try:
                if raw_mw not in (None, ""):
                    value = float(raw_mw)
            except (TypeError, ValueError):
                value = None
            records.append(self._make_record(
                query_date,
                field_name=self.field_name,
                target_time=tt or "",
                value=value,
                unit="MW",
                node=self.node,
                region="SYSTEM",
                latitude=None,
                longitude=None,
                forecast_run=f"DAM-{target}",
                issue_time=published_at,
                published_at=published_at,
                retrieved_at=retrieved_at,
                raw_source_id=(
                    f"OPR_DT={target}&OPR_HR={h}&TAC={self.resource}"
                    f"&iv={str(r.get('INTERVALSTARTTIME_GMT'))[:19]}"
                ),
                is_mock=bool(is_mock),
            ))
        return records

    # ------------------------------------------------------------------ mock
    def _mock_raw(self, query_date: str) -> Dict[str, Any]:
        """确定性合成 SLD_FCST raw（仅离线演示）：日负荷曲线，明显非真实，标注 mock。"""
        target = self.target_date(query_date)
        rows: List[Dict[str, str]] = []
        for h in range(1, 25):
            mw = _diurnal_load_mw(h)
            rows.append({
                "OPR_DT": target,
                "OPR_HR": str(h),
                "TAC_AREA_NAME": self.resource,
                "LABEL": "Demand Forecast Day Ahead (MOCK)",
                "XML_DATA_ITEM": "SYS_FCST_DA_MW",
                "MW": f"{mw:.2f}",
                "MARKET_RUN_ID": self.market_run,
                "EXECUTION_TYPE": "DAM",
            })
        return {"mock": True, "rows": rows,
                "query_date": str(query_date)[:10], "target_date": target}


def _diurnal_load_mw(h: int) -> float:
    """确定性日负荷曲线：夜间 23~26GW，午后峰 34GW（24h 循环，无随机性）。"""
    base = 24000.0
    peak = 10000.0 * max(0.0, math.sin(math.pi * (h - 6) / 16.0))
    return round(base + peak + 500.0 * math.sin(2 * math.pi * h / 24.0), 2)
