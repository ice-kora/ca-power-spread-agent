# -*- coding: utf-8 -*-
"""
code/analysis/agent_f_reconcile.py —— 公司数据 vs CAISO 官方（OASIS）逐小时对账（Agent F）

只读对账：不修改模型/回测/采集器。产出对账统计 JSON + 口径差异定位。

对账数据窗口：target 交易日 2026-07-01 .. 2026-07-07（7 天 × 3 节点）。

数据类型：
  A. DA LMP（PRC_LMP, market_run_id=DAM）——官方节点价 ↔ 公司 master.da_price
     （LMP_TYPE=LMP 总价；同时解析 MCE/MCC/MCL/MGHG 分量校验官方内部口径）
  B. 系统 DA 负荷预报（SLD_FCST, TAC_AREA_NAME='CA ISO-TAC'）——官方 MW ↔ 公司 master.load_2da

方法：
  - 官方 DA LMP 用 PRC_LMP 直接拉取（docs/caiso_oasis_sources.md §2.1，node 过滤，
    25h UTC 窗口覆盖 PT 运营日 H1..H24）。每 node×day 只发 1 个请求（一次解析总价+分量）。
  - 官方负荷用既有采集器 CAISOLoadForecastCollector（data_acquisition/caiso_oasis.py）。
  - 公司数据从 code/data/master.csv 读取。
  - 原始响应按 query 键落盘 code/data/recon_oasis_cache.json 可断点续跑；
    429 限流自动退避重试。

统计量（per 字段/节点）：
  n_expected / n_official / n_company / missing_rate
  match_rate（阈值 tol：绝对 0.5 或相对 0.1%）
  mean_abs_diff / max_diff / corr
  时区/小时错位诊断 hour_alignment_diag（date_shift × hour_shift 网格试错）

输出：code/data/recon_official.json（统计摘要）+ 终端打印。
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.request
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Windows GBK 控制台无法打印 ⇒ 等字符：强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from code.data_acquisition.caiso_oasis import CAISOLoadForecastCollector  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TARGET_START = "2026-07-01"
TARGET_END = "2026-07-07"
NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001", "ELCAJNGT_7_N001"]
_OASIS = "http://oasis.caiso.com/oasisapi/SingleZip"
_UA = "caiso-recon/0.1 (Agent F)"
OUT_JSON = Path(REPO) / "code" / "data" / "recon_official.json"
CACHE_JSON = Path(REPO) / "code" / "data" / "recon_oasis_cache.json"

TOL_ABS = 0.5          # 绝对容差（$ 或 MW）
TOL_REL = 0.001        # 相对容差
OASIS_SLEEP_S = 6.0    # 基础请求间隔（OASIS 限流严格，实测 2s 触发 429）
MAX_RETRY = 4          # 429/5xx 重试次数
RETRY_BACKOFF = [10.0, 25.0, 60.0, 120.0]


def add_days(d: str, n: int) -> str:
    return (datetime.strptime(d[:10], "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def daterange(start: str, end: str) -> List[str]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d = add_days(d, 1)
    return out


# ---------------------------------------------------------------------------
# 官方拉取（带 429 退避 + 磁盘缓存，可断点续跑）
# ---------------------------------------------------------------------------
_cache: Dict[str, Any] = {}
if CACHE_JSON.exists():
    try:
        _cache = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    except Exception:
        _cache = {}


def _save_cache() -> None:
    CACHE_JSON.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


def _http_get(url: str) -> bytes:
    """带 429/5xx 指数退避重试的 GET。"""
    for attempt in range(MAX_RETRY + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRY:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                print(f"  [retry {attempt + 1}/{MAX_RETRY}] HTTP {exc.code} after {wait}s")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def _fetch_oasis_rows(cache_key: str, url: str) -> List[Dict[str, str]]:
    """按 cache_key 缓存原始 CSV 行；命中则直接返回。"""
    if cache_key in _cache:
        return _cache[cache_key]
    time.sleep(OASIS_SLEEP_S)
    body = _http_get(url)
    z = zipfile.ZipFile(io.BytesIO(body))
    names = [n for n in z.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise RuntimeError(f"no csv in zip: {z.namelist()[:3]}")
    text = z.read(names[0]).decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(text.splitlines()))
    _cache[cache_key] = rows
    _save_cache()
    return rows


def _prc_lmp_url(target_date: str, node: str) -> str:
    start = f"{target_date.replace('-', '')}T07:00-0000"
    end = f"{add_days(target_date, 1).replace('-', '')}T08:00-0000"
    return (
        f"{_OASIS}?queryname=PRC_LMP&startdatetime={start}&enddatetime={end}"
        f"&market_run_id=DAM&version=1&node={node}&resultformat=6"
    )


def fetch_da_lmp_full(target_date: str, node: str) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]]]:
    """PRC_LMP（DAM）一次请求 → ( {hour: LMP 总价}, {hour: {MCE,MCC,MCL,MGHG}} )。
    只保留 OPR_DT == target_date 的行；价格取响应 'MW' 列。"""
    url = _prc_lmp_url(target_date, node)
    rows = _fetch_oasis_rows(f"PRC_LMP|{node}|{target_date}", url)
    lmp: Dict[int, float] = {}
    comps: Dict[int, Dict[str, float]] = {}
    for r in rows:
        if r.get("OPR_DT", "")[:10] != target_date:
            continue
        try:
            h = int(r.get("OPR_HR"))
        except (TypeError, ValueError):
            continue
        v = r.get("MW")
        if v in (None, ""):
            continue
        t = r.get("LMP_TYPE")
        if t == "LMP":
            lmp[h] = float(v)
        elif t in ("MCE", "MCC", "MCL", "MGHG"):
            comps.setdefault(h, {})[t] = float(v)
    return lmp, comps


def _rtpd_url(target_date: str, node: str) -> str:
    start = f"{target_date.replace('-', '')}T07:00-0000"
    end = f"{add_days(target_date, 1).replace('-', '')}T08:00-0000"
    return (
        f"{_OASIS}?queryname=PRC_RTPD_LMP&startdatetime={start}&enddatetime={end}"
        f"&market_run_id=RTPD&version=1&node={node}&resultformat=6"
    )


def fetch_rtpd_hourly(target_date: str, node: str) -> Dict[int, float]:
    """PRC_RTPD_LMP（FMM 15-min）→ 按小时聚合 4 个 15-min 区间求均值 → {hour1..24: price}。
    价格列 = PRC；OPR_INTERVAL=1..4。"""
    url = _rtpd_url(target_date, node)
    rows = _fetch_oasis_rows(f"PRC_RTPD_LMP|{node}|{target_date}", url)
    hour_vals: Dict[int, List[float]] = {}
    for r in rows:
        if r.get("OPR_DT", "")[:10] != target_date:
            continue
        if r.get("LMP_TYPE") != "LMP":
            continue
        try:
            h = int(r.get("OPR_HR"))
        except (TypeError, ValueError):
            continue
        v = r.get("PRC")
        if v in (None, ""):
            continue
        hour_vals.setdefault(h, []).append(float(v))
    out: Dict[int, float] = {}
    for h, vals in hour_vals.items():
        if 1 <= h <= 24 and len(vals) >= 3:  # 允许部分缺失，但至少 3/4 区间
            out[h] = round(float(np.mean(vals)), 4)
    return out


def fetch_sld_fcst(target_date: str) -> Dict[int, float]:
    """既有采集器 CAISOLoadForecastCollector（SLD_FCST, CA ISO-TAC）→ {hour1..24: MW}。"""
    key = f"SLD_FCST|{target_date}"
    if key in _cache:
        # 注意：JSON 缓存往返后 hour key 变字符串，归一为 int
        return {int(k): v for k, v in _cache[key].items() if v is not None}
    time.sleep(OASIS_SLEEP_S)
    coll = CAISOLoadForecastCollector(resource="CA ISO-TAC", market_run="DAM")
    res = coll.run(add_days(target_date, -1), save=False, use_cache=True)
    out: Dict[int, float] = {}
    import re as _re
    for r in res.records:
        m = _re.search(r"OPR_HR=(\d+)", str(r.get("raw_source_id", "")))
        if not m:
            continue
        h = int(m.group(1))
        if r.get("value") is None or not (1 <= h <= 24):
            continue
        out[h] = float(r["value"])
    _cache[key] = out
    _save_cache()
    return out


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if (np.isfinite(f)) else None


def stats_pair(company: Dict[Tuple[str, int], float],
               official: Dict[Tuple[str, int], float],
               expected_keys: List[Tuple[str, int]]) -> Dict:
    keys = expected_keys
    n_company = int(sum(1 for k in keys if _num(company.get(k)) is not None))
    n_official = int(sum(1 for k in keys if _num(official.get(k)) is not None))
    both = [(company[k], official[k]) for k in keys
            if _num(company.get(k)) is not None and _num(official.get(k)) is not None]
    missing_rate = round(1.0 - len(both) / len(keys), 4) if keys else None
    base = {
        "n_expected": len(keys), "n_company": n_company, "n_official": n_official,
        "n_both": len(both), "missing_rate": missing_rate, "match_rate": None,
        "mean_abs_diff": None, "max_diff": None, "corr": None,
    }
    if not both:
        return base
    c = np.array([x[0] for x in both], dtype=float)
    o = np.array([x[1] for x in both], dtype=float)
    adiff = np.abs(c - o)
    rel = adiff / np.maximum(np.abs(o), 1e-9)
    match = (adiff <= TOL_ABS) | (rel <= TOL_REL)
    corr = round(float(np.corrcoef(c, o)[0, 1]), 4) if len(both) > 2 and np.std(o) > 0 else None
    return {
        "n_expected": len(keys), "n_company": n_company, "n_official": n_official,
        "n_both": len(both), "missing_rate": missing_rate,
        "match_rate": round(float(match.mean()), 4),
        "mean_abs_diff": round(float(adiff.mean()), 4),
        "max_diff": round(float(adiff.max()), 4),
        "corr": corr,
    }


def _shift_key(key: Tuple, shift_d: int, shift_h: int) -> Optional[Tuple]:
    """对 (date, hour) 或 (node, date, hour) 做日期/小时平移，归一 H∈1..24。"""
    if len(key) == 3:
        node, d, h = key
    else:
        d, h = key
    nd = add_days(d, shift_d)
    nh = h + shift_h
    if nh == 0:
        nd, nh = add_days(nd, -1), 24
    elif nh == 25:
        nd, nh = add_days(nd, 1), 1
    elif nh < 1 or nh > 24:
        return None
    return (node, nd, nh) if len(key) == 3 else (nd, nh)


def hour_shift_diag(company: Dict[Tuple[str, int], float],
                    official: Dict[Tuple[str, int], float],
                    expected_keys: List[Tuple[str, int]]) -> Dict:
    """时区/小时错位诊断：对 company 试 date_shift ∈ {-1,0,+1} × hour_shift ∈ {-3..+3}，
    求使 match_rate 最大的对齐（用逐小时散点，不要求 key 全重合）。"""
    def align(shift_d: int, shift_h: int) -> float:
        c_map: Dict[Tuple, float] = {}
        for k in expected_keys:
            v = _num(company.get(k))
            if v is None:
                continue
            sk = _shift_key(k, shift_d, shift_h)
            if sk is None:
                continue
            c_map[sk] = v
        pairs = []
        for k in expected_keys:
            if k in c_map and _num(official.get(k)) is not None:
                pairs.append((c_map[k], official[k]))
        if len(pairs) < 20:
            return -1.0
        c = np.array([x[0] for x in pairs]); o = np.array([x[1] for x in pairs])
        adiff = np.abs(c - o)
        match = (adiff <= TOL_ABS) | (adiff / np.maximum(np.abs(o), 1e-9) <= TOL_REL)
        return round(float(match.mean()), 4)

    best = (-1.0, None)
    for sd in (0, 1, -1):
        for sh in range(-3, 4):
            score = align(sd, sh)
            if score > best[0]:
                best = (score, {"date_shift": sd, "hour_shift": sh, "match_rate": score})
    return {
        "best_match_rate": best[0],
        "best_alignment": best[1],
        "base_alignment_match_rate": align(0, 0),
        "tol_abs": TOL_ABS, "tol_rel": TOL_REL,
        "note": "hour_shift=+1 & date_shift=0 ⇒ 公司 hour 较官方 OPR_HR 后移 1（hour-ending 嫌疑）；"
                "date_shift=+1 ⇒ 公司 date 较官方 OPR_DT 晚一日（时区/日期错位）",
    }


def worst_examples(company: Dict[Tuple, float], official: Dict[Tuple, float],
                   expected_keys: List[Tuple], n: int = 5) -> List[Dict]:
    ex = []
    for k in expected_keys:
        c, o = _num(company.get(k)), _num(official.get(k))
        if c is None or o is None:
            continue
        ex.append((round(abs(c - o), 3), k, round(c, 3), round(o, 3)))
    ex.sort(key=lambda x: -x[0])
    return [{"key": str(k), "diff": d, "company": c, "official": o} for d, k, c, o in ex[:n]]


def verify_c_files() -> Dict:
    """公司 `-c` 价格文件 ↔ 官方 MCC 阻塞分量（openpyxl 直读，无网络）。
    返回 {node: {DA: stats, RTPD: stats}}；-c DA ↔ PRC_LMP MCC，-c RTPD ↔ PRC_RTPD_LMP MCC(15min均值)。"""
    import openpyxl as _oxl
    from datetime import date as _date
    days = daterange(TARGET_START, TARGET_END)
    day_set = {_date(2026, 7, d) for d in range(1, 8)}

    def read_c_market(path: str, market: str) -> Dict[Tuple, float]:
        wb = _oxl.load_workbook(path, read_only=True)
        ws = wb["Sheet1"]
        out: Dict[Tuple, float] = {}
        cur = None
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is not None:
                cur = pd.Timestamp(row[0]).date()
            mk = row[1]
            if cur is None or mk is None or mk != market or cur not in day_set:
                continue
            for h in range(1, 25):
                v = row[4 + h]
                if v is not None:
                    out[(cur, h)] = float(v)
        return out

    res: Dict[str, Dict] = {}
    for node in NODES:
        c_da = read_c_market(str(Path(REPO) / "价格数据" / f"{node}-c.xlsx"), "DA")
        c_rt = read_c_market(str(Path(REPO) / "价格数据" / f"{node}-c.xlsx"), "RTPD")
        mcc_da: Dict[Tuple, float] = {}
        mcc_rt: Dict[Tuple, float] = {}
        for d in days:
            dd = _date(*map(int, d.split("-")))
            for r in _cache.get(f"PRC_LMP|{node}|{d}", []):
                if r.get("OPR_DT", "")[:10] == d and r.get("LMP_TYPE") == "MCC":
                    v = r.get("MW")
                    if v not in (None, ""):
                        mcc_da[(dd, int(r["OPR_HR"]))] = float(v)
            hv: Dict[int, List[float]] = {}
            for r in _cache.get(f"PRC_RTPD_LMP|{node}|{d}", []):
                if r.get("OPR_DT", "")[:10] == d and r.get("LMP_TYPE") == "MCC":
                    v = r.get("PRC")
                    if v in (None, ""):
                        continue
                    hv.setdefault(int(r["OPR_HR"]), []).append(float(v))
            for h, vals in hv.items():
                mcc_rt[(dd, h)] = float(np.mean(vals))
        res[node] = {
            "DA_c_vs_MCC": stats_pair({k: v for k, v in c_da.items()},
                                      mcc_da, list(c_da.keys())),
            "RTPD_c_vs_MCC": stats_pair({k: v for k, v in c_rt.items()},
                                        mcc_rt, list(c_rt.keys())),
        }
    return res


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def load_master() -> pd.DataFrame:
    df = pd.read_csv(Path(REPO) / "code" / "data" / "master.csv")
    df["date"] = df["date"].astype(str)
    return df


def main() -> None:
    days = daterange(TARGET_START, TARGET_END)
    master = load_master()

    result: Dict = {
        "window": {"start": TARGET_START, "end": TARGET_END},
        "tol": {"abs": TOL_ABS, "rel": TOL_REL},
        "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # ---------- A. DA LMP ----------
    lmp_company: Dict[Tuple, float] = {}
    lmp_official: Dict[Tuple, float] = {}
    lmp_comps: Dict[Tuple, Dict[str, float]] = {}
    for node in NODES:
        for d in days:
            sub = master[(master.node == node) & (master.date == d)]
            for _, row in sub.iterrows():
                v = _num(row["da_price"])
                if v is not None:
                    lmp_company[(node, d, int(row["hour"]))] = v
            try:
                off, comps = fetch_da_lmp_full(d, node)
            except Exception as exc:
                print(f"[warn] DA LMP fetch fail {node} {d}: {exc}")
                off, comps = {}, {}
            for h, v in off.items():
                lmp_official[(node, d, h)] = v
            for h, c in comps.items():
                lmp_comps[(node, d, h)] = c

    expected_lmp = [(n, d, h) for n in NODES for d in days for h in range(1, 25)]
    lmp_overall = stats_pair(lmp_company, lmp_official, expected_lmp)
    lmp_by_node = {n: stats_pair(lmp_company, lmp_official,
                                 [k for k in expected_lmp if k[0] == n]) for n in NODES}
    lmp_diag = hour_shift_diag(lmp_company, lmp_official, expected_lmp)

    # 官方内部一致性：LMP 总价 vs MCE+MCC+MCL+MGHG
    comp_internal = {"n": 0, "n_match": 0, "max_abs_diff": 0.0}
    for k, c in lmp_comps.items():
        lmp_v = lmp_official.get(k)
        if lmp_v is None:
            continue
        s = sum(c.get(t, 0.0) for t in ("MCE", "MCC", "MCL", "MGHG"))
        comp_internal["n"] += 1
        if abs(s - lmp_v) <= max(TOL_ABS, TOL_REL * abs(lmp_v)):
            comp_internal["n_match"] += 1
        comp_internal["max_abs_diff"] = max(comp_internal["max_abs_diff"], abs(s - lmp_v))
    comp_internal["match_rate"] = round(comp_internal["n_match"] / comp_internal["n"], 4) \
        if comp_internal["n"] else None
    lmp_overall["official_internal_LMP_eq_MCE+MCC+MCL+MGHG"] = comp_internal

    result["DA_LMP"] = {
        "overall": lmp_overall,
        "by_node": lmp_by_node,
        "hour_alignment_diag": lmp_diag,
        "worst_examples": worst_examples(lmp_company, lmp_official, expected_lmp),
        "official_query": "PRC_LMP (market_run_id=DAM, LMP_TYPE=LMP, price col=MW)",
    }

    # ---------- B. Load Forecast ----------
    ld_company: Dict[Tuple, float] = {}
    ld_official: Dict[Tuple, float] = {}
    for d in days:
        seen = set()
        sub = master[master.date == d]
        for _, row in sub.iterrows():
            h = int(row["hour"])
            if h in seen:
                continue
            seen.add(h)
            v = _num(row["load_2da"])
            if v is not None:
                ld_company[(d, h)] = v
        try:
            off = fetch_sld_fcst(d)
        except Exception as exc:
            print(f"[warn] SLD_FCST fetch fail {d}: {exc}")
            off = {}
        for h, v in off.items():
            ld_official[(d, h)] = v

    expected_ld = [(d, h) for d in days for h in range(1, 25)]
    ld_overall = stats_pair(ld_company, ld_official, expected_ld)
    ld_diag = hour_shift_diag(ld_company, ld_official, expected_ld)
    result["LOAD_FCST"] = {
        "overall": ld_overall,
        "hour_alignment_diag": ld_diag,
        "worst_examples": worst_examples(ld_company, ld_official, expected_ld),
        "official_query": "SLD_FCST (TAC_AREA_NAME=CA ISO-TAC, SYS_FCST_DA_MW)",
    }

    # ---------- C. RTPD（FMM 15-min → 小时均值） ----------
    rt_company: Dict[Tuple, float] = {}
    rt_official: Dict[Tuple, float] = {}
    for node in NODES:
        for d in days:
            sub = master[(master.node == node) & (master.date == d)]
            for _, row in sub.iterrows():
                v = _num(row["rtpd_price"])
                if v is not None:
                    rt_company[(node, d, int(row["hour"]))] = v
            try:
                off = fetch_rtpd_hourly(d, node)
            except Exception as exc:
                print(f"[warn] RTPD fetch fail {node} {d}: {exc}")
                off = {}
            for h, v in off.items():
                rt_official[(node, d, h)] = v

    rt_overall = stats_pair(rt_company, rt_official, expected_lmp)
    rt_by_node = {n: stats_pair(rt_company, rt_official,
                                [k for k in expected_lmp if k[0] == n]) for n in NODES}
    rt_diag = hour_shift_diag(rt_company, rt_official, expected_lmp)
    result["RTPD_FMM"] = {
        "overall": rt_overall,
        "by_node": rt_by_node,
        "hour_alignment_diag": rt_diag,
        "worst_examples": worst_examples(rt_company, rt_official, expected_lmp),
        "official_query": "PRC_RTPD_LMP (market_run_id=RTPD, LMP_TYPE=LMP, price col=PRC); "
                          "hour = mean(4×15min OPR_INTERVAL)",
        "aggregation_note": "公司 rtpd_price 为小时值；官方 15-min → 小时取 4 区间算术均值。",
    }

    # ---------- D. -c 价格文件 ↔ 官方 MCC 阻塞分量 ----------
    result["C_FILES_MCC"] = verify_c_files()

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("=== DA LMP overall ===")
    print(json.dumps(lmp_overall, ensure_ascii=False, indent=1))
    print("=== DA LMP by_node ===")
    print(json.dumps(lmp_by_node, ensure_ascii=False, indent=1))
    print("=== DA LMP hour_diag ===")
    print(json.dumps(lmp_diag, ensure_ascii=False, indent=1))
    print("=== LOAD_FCST overall ===")
    print(json.dumps(ld_overall, ensure_ascii=False, indent=1))
    print("=== LOAD_FCST hour_diag ===")
    print(json.dumps(ld_diag, ensure_ascii=False, indent=1))
    print("=== RTPD_FMM overall ===")
    print(json.dumps(rt_overall, ensure_ascii=False, indent=1))
    print("=== RTPD_FMM by_node ===")
    print(json.dumps(rt_by_node, ensure_ascii=False, indent=1))
    print("=== RTPD_FMM hour_diag ===")
    print(json.dumps(rt_diag, ensure_ascii=False, indent=1))
    print("=== C_FILES_MCC ===")
    print(json.dumps(result["C_FILES_MCC"], ensure_ascii=False, indent=1))
    print("saved:", OUT_JSON)


if __name__ == "__main__":
    main()
