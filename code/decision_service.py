# -*- coding: utf-8 -*-
"""
code/decision_service.py —— DecisionService + 6 个结构化 Tool（Agent C）
=========================================================================

把现有白盒决策链打包成可复用的 DecisionService 与 6 个结构化 Tool，
供 LLM Copilot（Agent D 的 tool calling）与 Web 前端（Agent E）调用。

【冻结交易核心】本模块只做工程封装，**不改变**任何模型 / 规则 / 阈值 / PnL /
evidence / case 逻辑。全部数值来自真实数据文件（canonical.parquet /
predictions_v2.csv / stage3/risk_features.parquet / cases*.json / GFS 证据），
禁止 LLM / 外部调用方覆盖。

决策语义（与 code/risk_gate/constants.py 及 business_contract 一致）：
  BUY_DA / SELL_DA / NO_TRADE
  PnL（1 MWh/仓）：BUY = RTPD−DA（= −actual_return）；SELL = DA−RTPD（= +actual_return）；NO_TRADE = 0
  decision_cutoff = 决策日 D 10:00 PT（DAM bid cutoff）

6 个 Tool（全部返回可 json.dumps 的结构化 dict）：
  1. get_decision(decision_date, node, hour)
       → model_output / risk_gate / rule_engine / final_recommendation / reason_codes
  2. get_feature_explanation(decision_id)
       → top features + values + z-contributions + source + availability
  3. get_evidence(decision_id)
       → eligible / rejected evidence + source/published/available/severity/eligible/rejection_reason
  4. get_similar_cases(decision_id)
       → top 3 相似案例（date/node/hour/decision/outcome/PnL/lesson）+ case_available_at 证明 as-of
  5. get_data_provenance(decision_id, feature_name=None)
       → source/raw_file/source_type/target_time/available_at/is_mock/backtest_eligible/production_eligible
  6. get_post_trade_review(decision_id)
       → 仅当 outcome_revealed=True 可调用；否则 status=OUTCOME_NOT_REVEALED。
         reveal 后输出 actual DA/RTPD/PnL/error/review_category/lessons

铁律：
  * actual_* 只在 reveal=True（或调用 reveal_decision()）后输出；否则
    post_trade.status = OUTCOME_NOT_REVEALED，绝不穿越。
  * decision_id 生成并保存（内存注册表 + 可选 JSON 文件持久化），供按 id 查询。
  * 决策证据一律经 Evidence Time Gate（agent/evidence/time_gate.py）程序裁决，
    晚于 cutoff 的证据只进复盘（rejected），绝不进入 Risk Gate / Rule Engine。

设计说明（供 Agent D / Agent E）：
  - DecisionService 是唯一入口：run_decision() 生成完整结构化决策对象并存档。
  - 6 个 Tool 既可作 service 方法调用，也有模块级独立函数（使用默认单例）。
  - TOOL_SCHEMAS 提供 OpenAPI 风格参数/返回描述，可直接转成 LLM function calling
    定义或 Web 表单 schema。
"""

from __future__ import annotations

import json
import math
import os
import socket
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from data_mode import (  # noqa: E402
    MODE_DEMO,
    MODE_FULL,
    DEMO_DIR_NAME,
    resolve_data_mode,
)

from code.data_acquisition.schemas import (  # noqa: E402
    AVAILABILITY_BASIS_KEY,
    HAS_PRECISE_PUBLISH_TIME_KEY,
    LATEST_POSSIBLE_AVAILABLE_AT_KEY,
    NODE_REGION,
    SOURCE_TARGET_DATE_KEY,
    infer_source_type,
    latest_available_bound,
    make_decision_cutoff,
    pt_naive_to_utc_naive,
    target_time_pt_to_utc,
    feature_available_at_display,
    feature_decision_eligible,
)
from code.risk_gate.gate import RiskGate  # noqa: E402
from code.risk_gate.case_adapter import match_similar_tail_cases  # noqa: E402
from code.risk_gate.evidence_adapter import evidence_direction_context  # noqa: E402
from code.decision.rule_engine import RuleEngine  # noqa: E402
from code.decision.audit import run_runtime_audit  # noqa: E402
from agent.evidence.fetcher import fetch_evidence  # noqa: E402
from agent.evidence.gfs_forecast import build_gfs_evidence  # noqa: E402
from agent.evidence.time_gate import split_eligible  # noqa: E402
from agent.case_library.policy import decision_time_for, is_retrievable  # noqa: E402

try:  # pragma: no cover - canonical 依赖完整 artifact；缺失时 provenance 走回退
    from code.canonical import availability_map as _availability_map
except Exception:  # pragma: no cover
    _availability_map = None

# ---------------------------------------------------------------------------
# 版本常量（单一事实来源：尽量复用 mvp_demo.py；失败则本地兜底副本）
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 取决于 mvp_demo 是否可导入
    from mvp_demo import (  # noqa: F401
        ALPHA_LABEL as _ALPHA_LABEL,
        CASE_LIBRARY_VERSION as _CL_VERSION,
        EVIDENCE_TIME_GATE_VERSION as _ETG_VERSION,
        MARKET_RULE_VERSION as _MRV,
        MODEL_VERSION as _MODEL_VERSION,
        RISK_GATE_VERSION as _RG_VERSION,
        RULE_ENGINE_VERSION as _RE_VERSION,
        SCHEMA_VERSION as _SCHEMA_VERSION,
        classify_post_trade as _classify_post_trade,
        similar_cases as _similar_cases,
        top_feature_contributions as _top_feature_contributions,
    )
except Exception:  # pragma: no cover
    _top_feature_contributions = None
    _similar_cases = None
    _classify_post_trade = None
    _MRV = "CAISO-DAM/BPM-demo-0.2（DAM bid cutoff = 10:00 PT）"
    _MODEL_VERSION = "V0.2 (model_v2.py / predictions_v2.csv)"
    _RE_VERSION = "0.2 (code/decision/rule_engine.py)"
    _RG_VERSION = "0.2 (code/risk_gate)"
    _ETG_VERSION = "0.2 (agent/evidence/time_gate.py)"
    _CL_VERSION = "0.2 (agent/case_library, 337 条：auto 319 + manual 18)"
    _SCHEMA_VERSION = "asof_v1 (code/data_acquisition/schemas.py)"
    _ALPHA_LABEL = "WEAK"

MARKET_RULE_VERSION = _MRV
MODEL_VERSION = _MODEL_VERSION
RULE_ENGINE_VERSION = _RE_VERSION
RISK_GATE_VERSION = _RG_VERSION
EVIDENCE_TIME_GATE_VERSION = _ETG_VERSION
CASE_LIBRARY_VERSION = _CL_VERSION
SCHEMA_VERSION = _SCHEMA_VERSION
ALPHA_LABEL = _ALPHA_LABEL
DECISION_CUTOFF_DESC = "10:00 PT（DAM Market Close / bid cutoff，官方 BPM）"
OUTCOME_NOT_REVEALED = "OUTCOME_NOT_REVEALED"

# 候选交易方向常量（与 risk_gate.constants 一致）
DIR_SELL, DIR_BUY, DIR_FLAT = "SELL", "BUY", "FLAT"

#: 非特征列（不计入 feature 统计）
_NON_FEATURE_COLS = {
    "node", "zone", "target_date", "decision_date", "hour", "split", "has_label",
    "actual_da", "actual_rtpd", "actual_return", "direction",
}

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _num(x) -> Optional[float]:
    """数值化；None / NaN / 不可转 → None。"""
    if x is None:
        return None
    try:
        f = float(x)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _f2(x) -> Optional[float]:
    """同 _num（命名与 mvp_demo 对齐）。"""
    return _num(x)


def _sign_dir(er: Optional[float]) -> str:
    if er is None or er != er:
        return DIR_FLAT
    if er > 0:
        return DIR_SELL
    if er < 0:
        return DIR_BUY
    return DIR_FLAT


def _json_safe(value: Any) -> Any:
    """递归把 numpy 标量 / NaN / ±Inf / Timestamp 规约为可 JSON 化的值。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, float):
        return None if (value != value or math.isinf(value)) else value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        import numpy as np  # 惰性
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            f = float(value)
            return None if (f != f or math.isinf(f)) else f
        if isinstance(value, np.ndarray):
            return [_json_safe(v) for v in value.tolist()]
    except Exception:
        pass
    return value


# ---------------------------------------------------------------------------
# Feature Provenance 注册表（as-of 特征来源 / 可用性；口径与 mvp_demo 一致）
# ---------------------------------------------------------------------------
#: feature → (source 标签, raw_file, source_type, avail_kind)
#: avail_kind: D_CLOSE=D 23:59 PT | D_DAM_PUB=D 13:00 PT | D1_LOAD=(D−1) 10:00 PT
#:             | D1_WEATHER=(D−1) 23:59 PT | STATIC=D 00:00 PT
_FEATURE_BASE: Dict[str, Tuple[str, str, str, str]] = {
    # ---- 价格（PRICE）----
    "spread_lag1": ("canonical/价格数据（as-of）", "价格数据/*.xlsx (DA−RTPD Return)", "PRICE", "D_CLOSE"),
    "da_lag1": ("canonical/价格数据（DAM 结果 13:00 发布）", "价格数据/*.xlsx (DA LMP)", "PRICE", "D_DAM_PUB"),
    "rtpd_lag1": ("canonical/价格数据（RT 逐小时结算）", "价格数据/*.xlsx (RTPD LMP)", "PRICE", "D_CLOSE"),
    "spread_lag2": ("canonical/价格数据（as-of）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "da_lag2": ("canonical/价格数据（as-of）", "价格数据/*.xlsx", "PRICE", "D_DAM_PUB"),
    "rtpd_lag2": ("canonical/价格数据（as-of）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_lag7": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "da_lag7": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_DAM_PUB"),
    "rtpd_lag7": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_mean7": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_std7": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_mean14": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_std14": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_mean30": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_std30": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_day_std_lag1": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_day_range_lag1": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_day_max_lag1": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "da_day_mean_lag1": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "rtpd_day_mean_lag1": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    "spread_day_mean_lag1": ("canonical/价格数据（历史滚动）", "价格数据/*.xlsx", "PRICE", "D_CLOSE"),
    # ---- 负荷（LOAD）----
    "load_2da_forecast": ("load_CA_ISO_TAC_2DA.csv（ASSUMED_AVAILABLE）", "load_CA_ISO_TAC_2DA.csv", "LOAD", "D1_LOAD"),
    "load_actual_lag1": ("load_CA_ISO_TAC_ACTUAL.csv（历史滞后）", "load_CA_ISO_TAC_ACTUAL.csv", "LOAD", "D_CLOSE"),
    "load_actual_day_mean_lag1": ("load_CA_ISO_TAC_ACTUAL.csv（历史滞后）", "load_CA_ISO_TAC_ACTUAL.csv", "LOAD", "D_CLOSE"),
    "load_peak_flag": ("load_CA_ISO_TAC_2DA.csv（ASSUMED_AVAILABLE）", "load_CA_ISO_TAC_2DA.csv", "LOAD", "D1_LOAD"),
    # ---- 天气（WEATHER）----
    "t2m_lag1": ("zone_weather_hourly.csv（历史滞后；无真实 as-of 预报归档）", "zone_weather_hourly.csv", "WEATHER", "D1_WEATHER"),
    "ssrd_lag1": ("zone_weather_hourly.csv（历史滞后；无真实 as-of 预报归档）", "zone_weather_hourly.csv", "WEATHER", "D1_WEATHER"),
    "wind100_lag1": ("zone_weather_hourly.csv（历史滞后；无真实 as-of 预报归档）", "zone_weather_hourly.csv", "WEATHER", "D1_WEATHER"),
    # ---- 同区节点联动（PRICE / peer）----
    "peer_spread_lag1": ("canonical/节点联动（peer）", "canonical.parquet (peer 派生，同区节点)", "PRICE", "D_CLOSE"),
    "peer_da_lag1": ("canonical/节点联动（peer）", "canonical.parquet (peer 派生，同区节点)", "PRICE", "D_CLOSE"),
    "peer_rtpd_lag1": ("canonical/节点联动（peer）", "canonical.parquet (peer 派生，同区节点)", "PRICE", "D_CLOSE"),
    # ---- 日历/静态（STATIC）----
    "dow": ("日历/静态（dow 星期几）", "static/calendar", "STATIC", "STATIC"),
    "month": ("日历/静态（month 月份）", "static/calendar", "STATIC", "STATIC"),
    "is_holiday": ("日历/静态（holiday 节假日）", "static/calendar", "STATIC", "STATIC"),
    "solar_flag": ("日历/静态（solar 时段标志）", "static/calendar", "STATIC", "STATIC"),
}

#: 无显式登记特征时的回退 provenance
_FEATURE_BASE_FALLBACK = ("canonical.parquet（as-of 特征）", "canonical.parquet", "UNKNOWN", "D_CLOSE")

#: 特征展示顺序（无足够样本算 z 时，按此顺序回退展示可用特征）
_FEAT_DISPLAY_ORDER = [
    "spread_lag1", "spread_mean7", "spread_std7", "da_lag1", "rtpd_lag1",
    "load_2da_forecast", "load_actual_lag1", "t2m_lag1", "wind100_lag1", "peer_spread_lag1",
]

#: 特征统计 z-score 池（与 mvp_demo 一致）
_FEAT_POOL = [
    "spread_lag1", "spread_lag2", "spread_lag7",
    "spread_mean7", "spread_std7", "spread_mean14", "spread_std14",
    "spread_mean30", "spread_std30", "spread_day_std_lag1", "spread_day_range_lag1",
    "da_lag1", "rtpd_lag1", "da_day_mean_lag1", "rtpd_day_mean_lag1", "spread_day_mean_lag1",
    "load_actual_lag1", "load_2da_forecast", "load_peak_flag",
    "t2m_lag1", "ssrd_lag1", "wind100_lag1",
    "peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1",
]


def _avail_pt(kind: str, d: str, d1: str) -> str:
    """可用性（PT naive ISO 字符串）。仅用于未在 canonical availability_map
    登记的兜底特征；已登记特征统一走 availability_map + schemas（单一事实来源）。"""
    if kind == "D_DAM_PUB":
        return f"{d} 13:00:00"
    if kind == "D1_LOAD":
        return f"{d1} 10:00:00"
    if kind == "D1_WEATHER":
        return f"{d1} 23:59:00"
    if kind == "STATIC":
        return f"{d} 00:00:00"
    return f"{d} 23:59:00"  # D_CLOSE


def _feature_provenance(feature: str, dd: str, target_date: str, hour: int,
                        cutoff_utc: Optional[str] = None) -> Dict[str, Any]:
    """单个 canonical 特征的 provenance（as-of 工程元数据，非交易逻辑）。

    P0-2 统一口径（展示 == Time Gate 判定）：已登记特征以 canonical
    availability_map + schemas.feature_decision_eligible / latest_available_bound /
    feature_available_at_display 为**单一事实来源**——available_at 展示的就是
    Time Gate 判定用的同一个上界（UTC naive），绝不出现"显示 23:59 却说
    decision_eligible"的矛盾；未登记特征保守回退（可证上界 <= cutoff 才 ELIGIBLE）。
    """
    d = str(dd)[:10]
    d1 = (pd.Timestamp(d) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    base = _FEATURE_BASE.get(feature)
    if base is None:
        st = infer_source_type(feature, feature)
        base = (_FEATURE_BASE_FALLBACK[0], _FEATURE_BASE_FALLBACK[1], st, _FEATURE_BASE_FALLBACK[3])
    src, raw, stype, kind = base

    meta: Dict[str, Any] = {}
    if _availability_map is not None:
        meta = _availability_map().get(feature) or {}

    if meta:
        basis = str(meta.get(AVAILABILITY_BASIS_KEY, "UNKNOWN"))
        source_target_date = str(meta.get(SOURCE_TARGET_DATE_KEY, ""))
        has_precise = bool(meta.get(HAS_PRECISE_PUBLISH_TIME_KEY, False))
        latest_bound = latest_available_bound(meta, d) or ""
        avail_display = feature_available_at_display(meta, d) or ""
        eligible = bool(feature_decision_eligible(meta, d, cutoff_utc)) if cutoff_utc else True
        return {
            "feature": feature,
            "source": src,
            "raw_file": raw,
            "source_type": stype,
            "target_time": target_time_pt_to_utc(str(target_date)[:10], int(hour)) or "",
            "available_at": avail_display,               # 展示 == Time Gate 判定上界
            "available_at_display": avail_display,
            "available_at_utc": latest_bound,
            AVAILABILITY_BASIS_KEY: basis,
            SOURCE_TARGET_DATE_KEY: source_target_date,
            HAS_PRECISE_PUBLISH_TIME_KEY: has_precise,
            LATEST_POSSIBLE_AVAILABLE_AT_KEY: latest_bound,
            "decision_cutoff": cutoff_utc or "",
            "is_mock": False,
            "backtest_eligible": bool(eligible),
            "production_eligible": bool(eligible),
            "decision_eligible": bool(eligible),
        }

    # ---- 未登记特征：保守回退（可用上界 = PT naive → UTC naive，再与 cutoff 比较）----
    avail = _avail_pt(kind, d, d1)
    avail_utc = pt_naive_to_utc_naive(avail) or ""
    eligible = True
    if cutoff_utc and avail_utc:
        try:
            eligible = pd.Timestamp(avail_utc) <= pd.Timestamp(cutoff_utc)
        except Exception:
            eligible = False
    display = f"{avail[:16]} PT" if avail else "UNKNOWN"
    return {
        "feature": feature,
        "source": src,
        "raw_file": raw,
        "source_type": stype,
        "target_time": target_time_pt_to_utc(str(target_date)[:10], int(hour)) or "",
        "available_at": display,
        "available_at_display": display,
        "available_at_utc": avail_utc,
        AVAILABILITY_BASIS_KEY: "UNKNOWN",
        SOURCE_TARGET_DATE_KEY: "",
        HAS_PRECISE_PUBLISH_TIME_KEY: False,
        LATEST_POSSIBLE_AVAILABLE_AT_KEY: avail_utc,
        "decision_cutoff": cutoff_utc or "",
        "is_mock": False,
        "backtest_eligible": bool(eligible),
        "production_eligible": bool(eligible),
        "decision_eligible": bool(eligible),
    }


# ---------------------------------------------------------------------------
# 兜底实现（mvp_demo 不可导入时的本地副本；逻辑与 mvp_demo 完全一致）
# ---------------------------------------------------------------------------
def _fallback_top_feature_contributions(canon: pd.DataFrame, node: str, decision_date: str,
                                        canon_row: Dict[str, Any], topn: int = 5) -> List[Dict[str, Any]]:
    hist = canon[(canon["node"] == node) & (canon["target_date"] < pd.Timestamp(decision_date))]
    if len(hist) < 30:  # 冷启动节点兜底
        hist = canon[canon["target_date"] < pd.Timestamp(decision_date)]
    out: List[Dict[str, Any]] = []
    for feat in _FEAT_POOL:
        v = canon_row.get(feat)
        if v is None or (isinstance(v, float) and v != v):
            continue
        s = hist[feat].dropna()
        if len(s) < 20:
            continue
        mu, sd = float(s.mean()), float(s.std(ddof=1))
        if sd == 0 or sd != sd:
            continue
        out.append({"feature": feat, "value": float(v), "hist_mean": mu, "hist_std": sd,
                    "z": float((float(v) - mu) / sd)})
    out.sort(key=lambda r: abs(r["z"]), reverse=True)
    return out[:topn]


def _fallback_similar_cases(cases: List[Dict[str, Any]], node: str, target_date: str, hour: int,
                            direction: str, topn: int = 3) -> List[Dict[str, Any]]:
    dt = decision_time_for(target_date)
    cand: List[Dict[str, Any]] = []
    for window in (3, 6):
        for c in cases:
            if c.get("node") != node:
                continue
            if not is_retrievable(c, dt):
                continue
            try:
                c_hour = int(c.get("hour", -1))
            except (TypeError, ValueError):
                continue
            if abs(c_hour - hour) > window:
                continue
            pred = str(c.get("model_prediction", "")).upper()
            if direction != DIR_FLAT and pred not in (direction,):
                continue
            cand.append(c)
        if cand:
            break
    seen, uniq = set(), []
    for c in cand:
        key = (c.get("case_id"), c.get("decision_date"), c.get("node"), c.get("hour"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    uniq.sort(key=lambda c: abs(float(c.get("PnL", 0.0) or 0.0)), reverse=True)
    return uniq[:topn]


def _fallback_classify_post_trade(*, decision: str, direction: str,
                                  expected_return: Optional[float], ms: Optional[float],
                                  uncertainty: Optional[float], actual_return: Optional[float],
                                  pnl: Optional[float], gate_decision: str,
                                  post_evidence_present: bool) -> Dict[str, Any]:
    primary: List[str] = []
    notes: List[str] = []
    ar = _f2(actual_return)
    hyp_buy = -ar if ar is not None else None
    hyp_sell = ar
    hyp_pnl = None
    if decision in ("SELL_DA", "BUY_DA"):
        pnl_f = _f2(pnl)
        direction_correct = bool(ar is not None and
                                 ((decision == "SELL_DA" and ar > 0) or (decision == "BUY_DA" and ar < 0)))
        if pnl_f is not None and pnl_f > 0:
            primary.append("NORMAL_PROFIT")
            if direction_correct:
                primary.append("MODEL_DIRECTION_CORRECT")
        else:
            if not direction_correct:
                if ms is not None and ms >= 0.5:
                    primary.append("MODEL_ERROR")
                    notes.append(f"模型信号强度较高（{ms:.2f}）却判错方向 → 模型/特征侧问题")
                elif uncertainty is not None and uncertainty >= 0.7:
                    primary.append("HIGH_UNCERTAINTY")
                    notes.append(f"模型不确定度很高（{uncertainty:.2f}），方向判错属高不确定区域")
                else:
                    primary.append("NORMAL_UNCERTAINTY")
                    notes.append("方向判错但信号强度低 / 不确定度中等 → 正常市场噪声")
            else:
                primary.append("UNFAVORABLE_REALIZATION")
                notes.append("方向判对但亏损（幅度预测偏差）")
    else:  # NO_TRADE
        if gate_decision == "DATA_MISSING":
            primary.append("DATA_MISSING_NO_TRADE")
            notes.append("该候选无模型输出（不在预测窗口 / 数据缺失），系统保守不交易")
        elif gate_decision == "REJECT":
            if direction == DIR_SELL:
                hyp_pnl = hyp_sell
            elif direction == DIR_BUY:
                hyp_pnl = hyp_buy
            if hyp_pnl is not None and hyp_pnl < 0:
                primary.append("RISK_GATE_SUCCESS")
                notes.append(f"闸门拒绝的假设交易事后为负（{hyp_pnl:+.1f} $/MWh）：闸门避免了亏损")
            elif hyp_pnl is not None:
                primary.append("RISK_GATE_OPPORTUNITY_COST")
                notes.append(f"闸门拒绝的假设交易事后为正（{hyp_pnl:+.1f} $/MWh）：保守放过了盈利机会（诚实代价）")
            else:
                primary.append("NO_TRADE")
        else:
            primary.append("NO_TRADE_RULE_THRESHOLD")
            notes.append("未触发交易阈值（|er|<5 或信号强度<0.20 等），未建仓")
    if post_evidence_present:
        primary.append("UNFORESEEABLE_EVENT")
        notes.append("存在 post-decision 真实证据（GFS 18Z，决策时点不可得）；"
                     "若实际结果与该后验信息相关，属不可预见事件，无法事前规避")
    return {"primary": primary, "notes": notes, "hypothetical_pnl": hyp_pnl}


def _top_feature_contributions_impl(canon: pd.DataFrame, node: str, decision_date: str,
                                    canon_row: Dict[str, Any], topn: int = 5) -> List[Dict[str, Any]]:
    fn = _top_feature_contributions or _fallback_top_feature_contributions
    return fn(canon, node, decision_date, canon_row, topn)


def _similar_cases_impl(cases: List[Dict[str, Any]], node: str, target_date: str, hour: int,
                        direction: str, topn: int = 3) -> List[Dict[str, Any]]:
    fn = _similar_cases or _fallback_similar_cases
    return fn(cases, node, target_date, hour, direction, topn)


def _classify_post_trade_impl(**kwargs) -> Dict[str, Any]:
    fn = _classify_post_trade or _fallback_classify_post_trade
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Evidence Adapter（统一证据接口；Agent A 正在改造 weather_gfs 源）
# ---------------------------------------------------------------------------
@dataclass
class EvidenceBundle:
    """一次决策时点的证据包（经 Evidence Time Gate 程序裁决后）。"""

    eligible: List[Dict[str, Any]] = field(default_factory=list)      # decision_eligible=True
    post_decision: List[Dict[str, Any]] = field(default_factory=list)  # 只进复盘
    gate_note: str = ""


class EvidenceAdapter(ABC):
    """证据适配器接口：run_decision 经它取证据（解耦天气源实现细节）。"""

    @abstractmethod
    def gather(self, node: str, decision_date: str, cutoff_utc: str) -> EvidenceBundle:
        raise NotImplementedError


class DefaultEvidenceAdapter(EvidenceAdapter):
    """默认适配器：复用 agent/evidence（真实 GFS 12Z 可用 + 18Z 复盘演示）。

    网络失败 → 诚实降级为空（不编造证据）。MOCK 一律不进 eligible。
    """

    def gather(self, node: str, decision_date: str, cutoff_utc: str) -> EvidenceBundle:
        socket.setdefaulttimeout(12)
        real_eligible: List[Dict[str, Any]] = []
        try:
            evs = fetch_evidence(
                node=node, decision_date=decision_date,
                include_placeholders=False, include_real_sources=True,
            )
            real_eligible = list(evs)
        except Exception as exc:
            print(f"[DecisionService] 真实证据获取失败（诚实降级为空）: {exc}")

        post_real: List[Dict[str, Any]] = []
        try:
            ev18 = build_gfs_evidence(node, decision_date, cycle="18Z")
            if ev18:
                post_real = [ev18]
        except Exception as exc:
            print(f"[DecisionService] 18Z 演示证据获取失败: {exc}")

        all_evs = real_eligible + post_real
        eligible, post = split_eligible(all_evs, cutoff_utc)
        if not eligible and not post:
            return EvidenceBundle([], [], "NO ELIGIBLE EXTERNAL EVIDENCE")
        gate_note = (
            "Evidence Time Gate（程序计算 decision_eligible=published_at<=decision_cutoff）："
            f"决策放行 {len(eligible)} 条，隔离 {len(post)} 条。"
            "隔离项只进 Post-trade Review，绝不进入 Risk Gate / Rule Engine。"
        )
        return EvidenceBundle(list(eligible), list(post), gate_note)


class StaticEvidenceAdapter(EvidenceAdapter):
    """确定性证据适配器（供测试 / 离线 / Web 演示注入）。

    注意：即使传入 dict 自带 decision_eligible，本适配器仍用 split_eligible
    按传入 cutoff 重算——Time Gate 永远是程序裁决，禁止外部覆盖。
    """

    def __init__(self, raw_evidence: Optional[Sequence[Dict[str, Any]]] = None):
        self.raw_evidence = list(raw_evidence or [])

    def gather(self, node: str, decision_date: str, cutoff_utc: str) -> EvidenceBundle:
        evs = []
        for ev in self.raw_evidence:
            ev = dict(ev)
            ev["decision_cutoff"] = cutoff_utc  # 对齐本次决策 cutoff（程序口径）
            if not ev.get("node"):
                ev["node"] = node
            evs.append(ev)
        eligible, post = split_eligible(evs, cutoff_utc)
        gate_note = (
            f"Evidence Time Gate（静态注入，程序重算）：放行 {len(eligible)} 条，"
            f"隔离 {len(post)} 条。"
        )
        return EvidenceBundle(list(eligible), list(post), gate_note)


# ---------------------------------------------------------------------------
# 证据行 / 拒绝原因（工程元数据）
# ---------------------------------------------------------------------------
def _ev_row(ev: Dict[str, Any]) -> Dict[str, Any]:
    """把一条证据转成展示行（工程元数据）。

    时间语义保持原始五字段：initialization_time / published_at / available_at /
    retrieved_at / decision_cutoff。**available_at 展示 = Time Gate 真正用的那个**
    （优先 available_at，缺失回退 published_at —— 与 Evidence.time_eligible 完全一致），
    禁止把 available_at 直接合并成 published_at。decision_eligible 由调用方按
    Time Gate 切分结果写入（不重算、不覆盖）。
    """
    pub = ev.get("published_at", "")
    avail = ev.get("available_at", "") or pub   # 展示 == Time Gate 判据
    return {
        "evidence_id": ev.get("evidence_id", ""),
        "event_type": ev.get("event_type", "OTHER"),
        "severity": ev.get("severity", "INFO"),
        "source": ev.get("source", ""),
        "source_type": ev.get("source_type", ""),
        "is_mock": bool(ev.get("is_mock", False)),
        "summary": ev.get("summary", ""),
        "initialization_time": ev.get("initialization_time", "") or ev.get("model_run_time", ""),
        "published_at": pub,
        "available_at": avail,                            # Time Gate 真正用的 available_at
        "available_at_source": "available_at" if ev.get("available_at") else "published_at",
        "retrieved_at": ev.get("retrieved_at", ""),
        "decision_cutoff": ev.get("decision_cutoff", ""),
        "decision_eligible": bool(ev.get("decision_eligible", False)),
        "directional_effect": ev.get("directional_effect", "UNCERTAIN"),
        "confidence": _num(ev.get("confidence", 0.0)),
    }


def _rejection_reason(row: Dict[str, Any]) -> str:
    """被隔离证据的拒绝原因（程序计算，非交易逻辑）。

    判据 = Time Gate 真正用的 available_at（= row 已展示的 available_at），
    保证"显示 16:00 / cutoff 17:00"与拒绝原因绝不矛盾。
    """
    if row.get("is_mock"):
        return "MOCK_DATA_NOT_ELIGIBLE（is_mock=True，仅测试/演示，R7 硬隔离）"
    avail = str(row.get("available_at") or "").strip()
    if not avail:
        return "MISSING_AVAILABLE_AT（可用时刻缺失，宁保守不穿越）"
    cutoff = str(row.get("decision_cutoff") or "").strip()
    try:
        avail_ts = pd.Timestamp(avail)
        cut_ts = pd.Timestamp(cutoff)
        if avail_ts.tz is not None:
            avail_ts = avail_ts.tz_localize(None)
        if cut_ts.tz is not None:
            cut_ts = cut_ts.tz_localize(None)
    except Exception:
        return "UNPARSEABLE_TIME（时间不可解析）"
    if avail_ts > cut_ts:
        return ("AVAILABLE_AFTER_CUTOFF（POST_DECISION_EVIDENCE："
                "available_at 晚于 decision_cutoff，只进复盘）")
    return "NOT_DECISION_ELIGIBLE"


# ---------------------------------------------------------------------------
# DecisionSnapshot（统一决策快照：CLI / Web / LLM Tool 全部消费它，禁止各自算）
# ---------------------------------------------------------------------------
@dataclass
class DecisionSnapshot:
    """一次决策的统一结构化快照。

    canonical 字段（单一事实来源，run_decision 只算一次）：
      context / features / prediction / evidences / cases / risk_gate /
      rule_engine / final_recommendation / reason_codes / audit / locked /
      outcome_revealed / outcome / post_trade_review / post_inputs(内部)。

    to_dict() 额外输出**兼容别名**（top_features/model_output/evidence/top_cases/
    post_trade/_post_inputs），指向同一数据（非重算），供既有 Web/测试平滑消费。
    """

    decision_id: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    features: List[Dict[str, Any]] = field(default_factory=list)
    prediction: Dict[str, Any] = field(default_factory=dict)
    evidences: Dict[str, Any] = field(default_factory=dict)
    cases: List[Dict[str, Any]] = field(default_factory=list)
    risk_gate: Dict[str, Any] = field(default_factory=dict)
    rule_engine: Dict[str, Any] = field(default_factory=dict)
    final_recommendation: str = "NO_TRADE"
    reason_codes: List[str] = field(default_factory=list)
    audit: Dict[str, Any] = field(default_factory=dict)
    locked: bool = False
    outcome_revealed: bool = False
    outcome: Optional[Dict[str, Any]] = None          # actual_*（仅 reveal 后）
    post_trade_review: Optional[Dict[str, Any]] = None
    post_inputs: Dict[str, Any] = field(default_factory=dict, repr=False)  # 内部，无 actual_*

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "context": self.context,
            "features": self.features,
            "prediction": self.prediction,
            "evidences": self.evidences,
            "cases": self.cases,
            "risk_gate": self.risk_gate,
            "rule_engine": self.rule_engine,
            "final_recommendation": self.final_recommendation,
            "reason_codes": list(self.reason_codes),
            "audit": self.audit,
            "locked": bool(self.locked),
            "outcome_revealed": bool(self.outcome_revealed),
            "outcome": self.outcome,
            "post_trade_review": self.post_trade_review,
            # ---- 兼容别名（同一数据；供既有 Web / 测试继续消费）----
            "top_features": self.features,
            "model_output": self.prediction,
            "evidence": self.evidences,
            "top_cases": self.cases,
            "post_trade": self._post_trade(),
            "_post_inputs": self.post_inputs,
        }

    def _post_trade(self) -> Dict[str, Any]:
        """兼容旧 post_trade 段：未 reveal 返回 OUTCOME_NOT_REVEALED，reveal 后合并 outcome+review。"""
        if not self.outcome_revealed:
            return {
                "status": OUTCOME_NOT_REVEALED,
                "message": "actual_* 未揭晓：调用 run_decision(..., reveal=True) 或 "
                           "lock_decision() 后 reveal_decision(decision_id) 才可复盘。",
            }
        d = dict(self.outcome or {})
        d["status"] = "REVEALED"
        d["decision"] = self.final_recommendation
        d["review"] = dict(self.post_trade_review or {})
        return d

    def as_jsonable(self) -> Dict[str, Any]:
        return _json_safe(self.to_dict())


# ---------------------------------------------------------------------------
# DecisionService
# ---------------------------------------------------------------------------
class DecisionService:
    """白盒决策链的工程封装：run_decision + 6 个 Tool + decision_id 存档。"""

    def __init__(
        self,
        data_dir: Optional[os.PathLike] = None,
        evidence_adapter: Optional[EvidenceAdapter] = None,
        store_path: Optional[os.PathLike] = None,
        mode: str = "BACKTEST",
        verbose: bool = False,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else resolve_data_mode().data_dir
        self.mode = mode
        self.verbose = verbose
        # ---- 数据模式（FULL / DEMO）与文件路径解析（单一来源 data_mode.py）----
        if (self.data_dir / "canonical_demo.parquet").exists():
            self.data_mode = MODE_DEMO
            self._canon_path = self.data_dir / "canonical_demo.parquet"
            self._pred_path = self.data_dir / "predictions_demo.csv"
            self._risk_path = self.data_dir / "risk_features_demo.parquet"
            self._cases_demo_path = self.data_dir / "cases_demo.json"
        else:
            self.data_mode = MODE_FULL
            self._canon_path = self.data_dir / "canonical.parquet"
            self._pred_path = self.data_dir / "predictions_v2.csv"
            self._risk_path = self.data_dir / "stage3" / "risk_features.parquet"
            self._cases_demo_path = None
        self.canon = self._load_canon()
        self.pred = self._load_pred()
        self.risk = self._load_risk()
        self.cases = self._load_cases()
        self.feature_cols = [c for c in self.canon.columns if c not in _NON_FEATURE_COLS]
        self.evidence_adapter = evidence_adapter or DefaultEvidenceAdapter()

        self._decisions: Dict[str, Dict[str, Any]] = {}
        self._by_key: Dict[Tuple[str, str, int], str] = {}
        self._seq = 0
        self.store_path = Path(store_path) if store_path else None
        if self.store_path is not None and self.store_path.exists():
            self._load_store()

    # ------------------------------------------------------------- 数据装载
    def _load_canon(self) -> pd.DataFrame:
        df = pd.read_parquet(self._canon_path)
        for c in ("target_date", "decision_date"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c]).dt.normalize()
        return df

    def _load_pred(self) -> pd.DataFrame:
        df = pd.read_csv(self._pred_path)
        df["target_date"] = pd.to_datetime(df["target_date"]).dt.normalize()
        return df

    def _load_risk(self) -> pd.DataFrame:
        df = pd.read_parquet(self._risk_path)
        df["target_date"] = pd.to_datetime(df["target_date"]).dt.normalize()
        return df

    def _load_cases(self) -> List[Dict[str, Any]]:
        """案例库：DEMO 模式用 demo_artifacts/cases_demo.json（真实切片）；否则用 agent/case_library。"""
        if self._cases_demo_path is not None and self._cases_demo_path.exists():
            with open(self._cases_demo_path, encoding="utf-8") as f:
                data = json.load(f)
            cases = data.get("cases", data) if isinstance(data, dict) else data
            return list(cases)
        out: List[Dict[str, Any]] = []
        for name in ("cases.json", "cases_auto.json"):
            p = Path(REPO_ROOT) / "agent" / "case_library" / name
            if not p.exists():
                continue
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            cases = data.get("cases", data) if isinstance(data, dict) else data
            out.extend(list(cases))
        return out

    # ------------------------------------------------------------- 存档
    def _make_decision_id(self, dd: str, node: str, hour: int) -> str:
        self._seq += 1
        return f"DEC-{str(dd)[:10]}-{node}-H{int(hour)}-{self._seq:04d}-{uuid.uuid4().hex[:6]}"

    def _save_store(self) -> None:
        if self.store_path is None:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(self._decisions, f, ensure_ascii=False, indent=2, default=str)

    def _load_store(self) -> None:
        with open(self.store_path, encoding="utf-8") as f:  # type: ignore
            data = json.load(f)
        for k, v in data.items():
            self._decisions[k] = v
            ctx = v.get("context", {})
            key = (str(ctx.get("decision_date", ""))[:10], ctx.get("node"), int(ctx.get("hour", -1) or -1))
            self._by_key[key] = k

    # ------------------------------------------------------------- 主流程
    def run_decision(self, decision_date: str, node: str, hour: int, reveal: bool = False) -> Dict[str, Any]:
        """运行一次完整白盒决策并归档，返回结构化决策对象。

        reveal=False（默认）：post_trade.status=OUTCOME_NOT_REVEALED，绝不输出 actual_*。
        reveal=True：post_trade 含 actual DA/RTPD/PnL/error/review（仅当 canonical 有事后结算值）。
        """
        dd = str(decision_date)[:10]
        hour = int(hour)
        if node not in NODE_REGION:
            raise ValueError(f"未知节点 {node!r}（可用: {sorted(NODE_REGION)}）")
        if not (1 <= hour <= 24):
            raise ValueError(f"hour 越界: {hour}（要求 1~24）")
        target_date = (pd.Timestamp(dd) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        cutoff_utc = make_decision_cutoff(dd) or ""

        # ---- 定位数据行 ----
        canon_row = self.canon[(self.canon["node"] == node) &
                               (self.canon["target_date"] == pd.Timestamp(target_date)) &
                               (self.canon["hour"] == hour)]
        pred_row = self.pred[(self.pred["node"] == node) &
                             (self.pred["target_date"] == pd.Timestamp(target_date)) &
                             (self.pred["hour"] == hour)]
        risk_row = self.risk[(self.risk["node"] == node) &
                             (self.risk["target_date"] == pd.Timestamp(target_date)) &
                             (self.risk["hour"] == hour)]
        if canon_row.empty:
            raise ValueError(f"canonical 无 {node} {target_date} H{hour} 行（可能超出数据范围）")

        cr = canon_row.iloc[0]
        use_pred = not pred_row.empty
        pr = pred_row.iloc[0] if use_pred else None
        rr = risk_row.iloc[0] if not risk_row.empty else None

        # ---- 模型输出 ----
        if use_pred:
            er = _num(pr["expected_return"])
            prob_pos = _num(pr["prob_positive"])
            prob_neg = _num(pr["prob_negative"])
            ms = _num(pr["confidence"])       # predictions_v2.confidence ≡ model_signal_strength
            unc = _num(pr["uncertainty"])
            direction = _sign_dir(er)
            if direction == DIR_SELL:
                dir_prob = prob_pos
            elif direction == DIR_BUY:
                dir_prob = prob_neg
            else:
                dir_prob = max(prob_pos or 0.0, prob_neg or 0.0)
            model_out: Dict[str, Any] = {
                "node": node, "target_date": target_date, "hour": hour,
                "expected_return": er, "prob_positive": prob_pos, "prob_negative": prob_neg,
                "direction_probability": dir_prob, "model_signal_strength": ms,
                "uncertainty": unc, "direction": direction,
                "model_version": MODEL_VERSION,
            }
        else:
            er = prob_pos = prob_neg = ms = unc = None
            direction = DIR_FLAT
            model_out = {"node": node, "target_date": target_date, "hour": hour,
                         "note": "不在 test 预测窗口，模型输出不可用（数据缺失）",
                         "model_version": MODEL_VERSION}

        # ---- 证据（经 Adapter + Time Gate）----
        evidence = self.evidence_adapter.gather(node, dd, cutoff_utc)
        gate_note = evidence.gate_note

        # ---- Top 特征（含 provenance）----
        cr_dict = _json_safe(cr.to_dict())
        top_features = self._build_top_features(cr_dict, node, dd, target_date, hour, cutoff_utc)

        # ---- 相似案例（as-of）----
        similar = _similar_cases_impl(self.cases, node, target_date, hour, direction) if use_pred else []

        # ---- Risk Gate → Rule Engine ----
        if use_pred:
            ev_ctx = evidence_direction_context(list(evidence.eligible), cutoff_utc)
            tail_loss = match_similar_tail_cases(
                {"node": node, "target_date": target_date, "hour": hour, "direction": direction},
                cases=self.cases, tail_threshold=-300.0, hour_window=3, max_cases=5, as_of=True,
            )
            risk_fields: Dict[str, Any] = {}
            if rr is not None:
                for k in ("hist_n", "cvar99", "rcvar99", "vol_ratio", "node_drift"):
                    risk_fields[k] = _f2(rr[k])
            verdict, decision = self._run_gate_and_rule(
                model_out, risk_fields, ev_ctx, tail_loss, cutoff_utc,
                features_used=top_features,
                eligible_evidence=list(evidence.eligible),
            )
            risk_gate_out = verdict.to_dict()
            rule_out = decision.to_dict()
            final = decision.decision
            reason_codes = list(verdict.risk_reasons) + list(decision.reasons)
            gate_decision = verdict.decision
        else:
            risk_gate_out = {"decision": "N/A (DATA_MISSING)", "risk_reasons": [], "rules_hit": [], "details": {}}
            rule_out = {"decision": "NO_TRADE", "reasons": ["DATA_MISSING"], "rules_hit": ["R-B"]}
            final = "NO_TRADE"
            reason_codes = ["DATA_MISSING"]
            gate_decision = "DATA_MISSING"

        # ---- Post-trade ----
        post_inputs = {
            "decision_label": final,
            "direction": direction,
            "expected_return": er,
            "ms": ms,
            "uncertainty": unc,
            "gate_decision": gate_decision,
        }
        post_evidence_present = len(evidence.post_decision) > 0
        if reveal:
            post_trade = self._compute_post_trade(cr_dict, post_inputs, post_evidence_present)
            outcome_revealed = post_trade.get("status") == "REVEALED"
            outcome = {k: post_trade.get(k) for k in
                       ("actual_da", "actual_rtpd", "actual_return", "pnl",
                        "model_prediction_error", "direction_correct")}
            post_trade_review = post_trade.get("review")
        else:
            post_trade = {"status": OUTCOME_NOT_REVEALED,
                          "message": "actual_* 未揭晓：调用 run_decision(..., reveal=True) 或 "
                                     "lock_decision() 后 reveal_decision(decision_id) 才可复盘。"}
            outcome_revealed = False
            outcome = None
            post_trade_review = None

        # ---- 组装统一决策快照（DecisionSnapshot）----
        context = {
            "decision_date": dd,
            "target_date": target_date,
            "hour": hour,
            "node": node,
            "zone": NODE_REGION.get(node, "?"),
            "decision_cutoff_pt": f"{dd} 10:00 PT",
            "decision_cutoff_utc": cutoff_utc,
            "market_rule_version": MARKET_RULE_VERSION,
            "as_of_banner": "AVAILABLE INFORMATION ONLY AS OF 10:00 PT",
        }
        evidence_section = self._evidence_section(evidence.eligible, evidence.post_decision, gate_note)

        snapshot = DecisionSnapshot(
            context=context,
            features=top_features,
            prediction=model_out,
            evidences=evidence_section,
            cases=similar,
            risk_gate=risk_gate_out,
            rule_engine=rule_out,
            final_recommendation=final,
            reason_codes=reason_codes,
            locked=False,
            outcome_revealed=outcome_revealed,
            outcome=outcome,
            post_trade_review=post_trade_review,
            post_inputs=post_inputs,
        )
        snapshot_obj = snapshot.to_dict()

        # ---- 运行时审计（真实运行检查；OVERALL 由结果计算，不硬编码）----
        audit = run_runtime_audit(
            features=top_features,
            evidence_section=evidence_section,
            cases=similar,
            decision_time=decision_time_for(target_date),
            decision_cutoff=cutoff_utc,
            decision_obj=snapshot_obj,
            outcome_revealed=bool(outcome_revealed),
            rule_engine_out=rule_out,
        )
        audit["decision_cutoff_pt"] = f"{dd} 10:00 PT"
        audit["decision_cutoff_utc"] = cutoff_utc
        # ---- 兼容旧 audit 键（由真实检查结果派生，不写死）----
        audit["data_leakage_check"] = audit["overall"]                    # 5 项 OVERALL 的真实结论
        audit["mock_data_used"] = "NONE" if audit["checks"]["mock_data"]["status"] == "PASS" else "FOUND"
        audit["evidence_time_gate"] = (f"{len(evidence.eligible)} eligible / "
                                       f"{len(evidence.post_decision)} post")
        audit["backtest_safe_features"] = f"{len(top_features)}/{len(self.feature_cols)}"
        audit["meta"] = {
            "generator": "code/decision_service.py",
            "data_mode": self.data_mode,
            "data_mode_note": (
                "DEMO：真实历史最小切片（非 MOCK，可真实推荐）；actual_* 仅 Reveal 后经 service/tool 可访问"
                if self.data_mode == MODE_DEMO
                else "FULL：完整数据"
            ),
            "market_rule_version": MARKET_RULE_VERSION,
            "model_version": MODEL_VERSION,
            "rule_engine_version": RULE_ENGINE_VERSION,
            "risk_gate_version": RISK_GATE_VERSION,
            "evidence_time_gate_version": EVIDENCE_TIME_GATE_VERSION,
            "case_library_version": CASE_LIBRARY_VERSION,
            "schema_version": SCHEMA_VERSION,
            "honest_labels": [
                "MODEL SIGNAL IS EXPERIMENTAL / CURRENT ALPHA = WEAK",
                "MVP ≠ 已验证盈利系统",
                "决策路径无 MOCK；所有数据真实且 as-of",
            ],
        }
        snapshot.audit = audit
        decision_obj = snapshot.as_jsonable()
        decision_id = self._make_decision_id(dd, node, hour)
        decision_obj["decision_id"] = decision_id
        self._decisions[decision_id] = decision_obj
        self._by_key[(dd, node, hour)] = decision_id
        self._save_store()
        return decision_obj

    # ------------------------------------------------------------- 内部
    def _evidence_section(self, eligible: Sequence[Dict[str, Any]],
                          post_decision: Sequence[Dict[str, Any]],
                          gate_note: str) -> Dict[str, Any]:
        """把 Time Gate 切分后的证据转成决策对象 evidence 段（含 rejected + rejection_reason）。"""
        elig_rows = []
        for e in eligible:
            r = _ev_row(e)
            r["eligible"] = True
            r["decision_eligible"] = True
            r["rejection_reason"] = ""
            elig_rows.append(r)
        rej_rows = []
        for e in post_decision:
            r = _ev_row(e)
            r["eligible"] = False
            r["decision_eligible"] = False
            r["rejection_reason"] = _rejection_reason(r)
            rej_rows.append(r)
        return {
            "eligible": elig_rows,
            "post_decision": rej_rows,     # 兼容 mvp_demo 命名
            "rejected": rej_rows,          # 任务命名（eligible/rejected）
            "gate_note": gate_note,
        }

    def _build_top_features(self, cr_dict: Dict[str, Any], node: str, dd: str,
                            target_date: str, hour: int, cutoff_utc: Optional[str],
                            topn: int = 5) -> List[Dict[str, Any]]:
        try:
            contribs = _top_feature_contributions_impl(self.canon, node, dd, cr_dict, topn=topn)
        except Exception:
            contribs = []
        if not contribs:  # 冷启动 / 无足够历史：回退展示可用特征
            contribs = [{"feature": f, "value": cr_dict.get(f), "hist_mean": None,
                         "hist_std": None, "z": None}
                        for f in _FEAT_DISPLAY_ORDER
                        if _num(cr_dict.get(f)) is not None]
        rows: List[Dict[str, Any]] = []
        for c in contribs:
            prov = _feature_provenance(c["feature"], dd, target_date, hour, cutoff_utc)
            rows.append({
                "feature": c["feature"],
                "value": _num(c["value"]),
                "hist_mean": _num(c.get("hist_mean")),
                "hist_std": _num(c.get("hist_std")),
                "z": _num(c.get("z")),
                **prov,
                "availability": "ELIGIBLE",
            })
        return rows

    def _run_gate_and_rule(self, pred: Dict[str, Any], risk_fields: Dict[str, Any],
                           ev_ctx: Dict[str, Any], tail_loss: List[Dict[str, Any]],
                           cutoff_utc: str,
                           features_used: Optional[Sequence[Dict[str, Any]]] = None,
                           eligible_evidence: Optional[Sequence[Dict[str, Any]]] = None):
        candidate = {
            "node": pred["node"],
            "target_date": pred["target_date"],
            "hour": pred["hour"],
            "expected_return": pred["expected_return"],
            "confidence": pred["model_signal_strength"],
            "uncertainty": pred["uncertainty"],
            "direction": pred["direction"],
            "hist_n": risk_fields.get("hist_n"),
            "cvar99": risk_fields.get("cvar99"),
            "rcvar99": risk_fields.get("rcvar99"),
            "vol_ratio": risk_fields.get("vol_ratio"),
            "node_drift": risk_fields.get("node_drift"),
            "similar_tail_loss_cases": tail_loss,
            "evidence_direction_context": ev_ctx,
        }
        gate = RiskGate()
        verdict = gate.evaluate(candidate, verbose=False)
        engine = RuleEngine()
        # 运行时审计真正喂给 Rule Engine：features_used（Feature Time Gate 校验结果）
        # 与 evidences（Time Gate 放行的 eligible 证据）。RuleEngine 内部再做一次
        # P0-2 一致性校验 + evidence 过滤，不一致直接抛错（防展示/判定漂移）。
        decision = engine.evaluate(
            {
                "node": pred["node"],
                "target_date": pred["target_date"],
                "hour": pred["hour"],
                "expected_return": pred["expected_return"],
                "confidence": pred["model_signal_strength"],
                "uncertainty": pred["uncertainty"],
                "prob_positive": pred["prob_positive"],
                "prob_negative": pred["prob_negative"],
            },
            gate_verdict=verdict,
            evidences=list(eligible_evidence or []),   # Time Gate 放行的 eligible 证据
            decision_cutoff=cutoff_utc,
            features_used=list(features_used or []),   # 特征展示 == Time Gate 判定口径
        )
        return verdict, decision

    def _compute_post_trade(self, cr_dict: Dict[str, Any], pi: Dict[str, Any],
                            post_evidence_present: bool) -> Dict[str, Any]:
        actual_da = _num(cr_dict.get("actual_da"))
        actual_rtpd = _num(cr_dict.get("actual_rtpd"))
        actual_return = _num(cr_dict.get("actual_return"))
        if actual_return is None:
            return {"status": "ACTUALS_UNAVAILABLE",
                    "note": "canonical 该行无事后结算值（actual_* 缺失），无法复盘。"}
        label = pi["decision_label"]
        if label == "SELL_DA":
            pnl = actual_return
        elif label == "BUY_DA":
            pnl = -actual_return
        else:
            pnl = 0.0
        expected_return = pi.get("expected_return")
        pred_err = (actual_return - expected_return) if expected_return is not None else None
        if label in ("SELL_DA", "BUY_DA"):
            dir_correct = bool((label == "SELL_DA" and actual_return > 0) or
                               (label == "BUY_DA" and actual_return < 0))
        else:
            dir_correct = None
        review = _classify_post_trade_impl(
            decision=label, direction=pi.get("direction") or DIR_FLAT,
            expected_return=expected_return, ms=pi.get("ms"), uncertainty=pi.get("uncertainty"),
            actual_return=actual_return, pnl=pnl, gate_decision=pi.get("gate_decision", ""),
            post_evidence_present=post_evidence_present,
        )
        return {
            "status": "REVEALED",
            "decision": label,
            "actual_da": actual_da,
            "actual_rtpd": actual_rtpd,
            "actual_return": actual_return,
            "pnl": pnl,
            "model_prediction_error": pred_err,
            "direction_correct": dir_correct,
            "review": review,
        }

    # ------------------------------------------------------------- 查询
    def _require(self, decision_id: str) -> Dict[str, Any]:
        dec = self._decisions.get(decision_id)
        if dec is None:
            raise KeyError(f"decision_id 不存在: {decision_id!r}（先用 run_decision / get_decision 生成）")
        return dec

    @staticmethod
    def _not_found(tool: str, decision_id: str) -> Dict[str, Any]:
        return {"status": "NOT_FOUND", "tool": tool, "decision_id": decision_id,
                "message": f"decision_id 不存在: {decision_id!r}（先用 run_decision / get_decision 生成）"}

    def lock_decision(self, decision_id: str) -> Dict[str, Any]:
        """锁定决策（Outcome Access Control 的 Service 层前置门槛）。

        Lock 前任何 actual_* 都不可见：get_post_trade_review 返回
        OUTCOME_NOT_REVEALED，reveal_decision 返回 NOT_LOCKED。
        """
        dec = self._require(decision_id)
        if not dec.get("locked"):
            dec["locked"] = True
            self._save_store()
        return {"status": "LOCKED", "decision_id": decision_id, "locked": True}

    def reveal_decision(self, decision_id: str) -> Dict[str, Any]:
        """对**已锁定**的决策做 Post-trade 揭晓（Service 层强制 Lock 前置）。

        actual_* 只在 Lock 后、经本方法显式揭晓才出现；Lock 前调用返回
        NOT_LOCKED（不穿越）。揭晓后更新统一快照的 outcome / post_trade_review /
        outcome_revealed 及兼容字段 post_trade。
        """
        dec = self._require(decision_id)
        if dec.get("outcome_revealed"):
            return dec.get("post_trade", {})
        if not dec.get("locked"):
            return {
                "status": "NOT_LOCKED",
                "decision_id": decision_id,
                "message": "必须先 lock_decision(decision_id) 才能 reveal（Lock 前禁止显示实际结果）。",
            }
        ctx = dec["context"]
        dd = str(ctx["decision_date"])[:10]
        node = ctx["node"]
        target_date = str(ctx["target_date"])[:10]
        hour = int(ctx["hour"])
        canon_row = self.canon[(self.canon["node"] == node) &
                               (self.canon["target_date"] == pd.Timestamp(target_date)) &
                               (self.canon["hour"] == hour)]
        if canon_row.empty:
            return {"status": "ACTUALS_UNAVAILABLE", "note": "canonical 无该行，无法复盘。"}
        cr_dict = _json_safe(canon_row.iloc[0].to_dict())
        post_evidence_present = len(dec.get("evidences", dec.get("evidence", {})).get("post_decision", [])) > 0
        post_inputs = dec.get("_post_inputs", {})
        post_trade = self._compute_post_trade(cr_dict, post_inputs, post_evidence_present)
        revealed = post_trade.get("status") == "REVEALED"
        dec["post_trade"] = post_trade
        dec["outcome_revealed"] = bool(revealed)
        if revealed:
            dec["outcome"] = {k: post_trade.get(k) for k in
                              ("actual_da", "actual_rtpd", "actual_return", "pnl",
                               "model_prediction_error", "direction_correct")}
            dec["post_trade_review"] = post_trade.get("review")
        else:
            dec["outcome"] = None
            dec["post_trade_review"] = None
        self._save_store()
        return dec["post_trade"]

    # ------------------------------------------------------------- 6 个 Tool
    def get_decision(self, decision_date: str, node: str, hour: int) -> Dict[str, Any]:
        """Tool 1：模型输出 / 风控 / 规则引擎 / 最终建议 / reason_codes。"""
        dd = str(decision_date)[:10]
        hour = int(hour)
        key = (dd, node, hour)
        decision_id = self._by_key.get(key)
        if decision_id is None:
            decision_id = self.run_decision(dd, node, hour, reveal=False)["decision_id"]
        dec = self._decisions[decision_id]
        prediction = dec.get("prediction", dec.get("model_output"))
        rule_engine = dict(dec.get("rule_engine", {}))
        rule_engine.pop("features_used", None)   # 工具输出保持紧凑；完整 features_used 在 snapshot 与 get_feature_explanation
        return {
            "status": "ok",
            "tool": "get_decision",
            "decision_id": decision_id,
            "context": dec["context"],
            "model_output": prediction,           # 兼容旧 schema；内容 = snapshot.prediction
            "risk_gate": dec["risk_gate"],
            "rule_engine": rule_engine,
            "final_recommendation": dec["final_recommendation"],
            "reason_codes": dec["reason_codes"],
        }

    def get_feature_explanation(self, decision_id: str) -> Dict[str, Any]:
        """Tool 2：Top 特征 + 值 + z 贡献 + 来源 + 可用性。"""
        try:
            dec = self._require(decision_id)
        except KeyError:
            return self._not_found("get_feature_explanation", decision_id)
        rows = [{
            "feature": f["feature"],
            "value": f.get("value"),
            "contribution_z": f.get("z"),
            "hist_mean": f.get("hist_mean"),
            "hist_std": f.get("hist_std"),
            "source": f.get("source"),
            "raw_file": f.get("raw_file"),
            "available_at": f.get("available_at"),
            "available_at_display": f.get("available_at_display"),
            "availability": f.get("availability", "ELIGIBLE"),
            "decision_eligible": bool(f.get("decision_eligible", True)),
        } for f in dec.get("features", dec.get("top_features", []))]
        return {
            "status": "ok",
            "tool": "get_feature_explanation",
            "decision_id": decision_id,
            "context": dec["context"],
            "top_features": rows,
            "note": "特征统计 z-score（as-of，非 SHAP），仅解释参考。",
        }

    def get_evidence(self, decision_id: str) -> Dict[str, Any]:
        """Tool 3：eligible / rejected 证据 + 来源/发布时间/可用性/严重度/eligible/rejection_reason。"""
        try:
            dec = self._require(decision_id)
        except KeyError:
            return self._not_found("get_evidence", decision_id)
        ev = dec.get("evidences", dec.get("evidence", {}))
        return {
            "status": "ok",
            "tool": "get_evidence",
            "decision_id": decision_id,
            "gate_note": ev.get("gate_note", ""),
            "eligible": [dict(r) for r in ev.get("eligible", [])],
            "rejected": [dict(r) for r in ev.get("rejected", ev.get("post_decision", []))],
        }

    def get_similar_cases(self, decision_id: str) -> Dict[str, Any]:
        """Tool 4：Top 3 相似案例 + case_available_at 证明 as-of。"""
        try:
            dec = self._require(decision_id)
        except KeyError:
            return self._not_found("get_similar_cases", decision_id)
        ctx = dec["context"]
        target_date = str(ctx.get("target_date", ""))[:10]
        decision_time = decision_time_for(target_date) if target_date else ""
        rows = []
        for c in dec.get("cases", dec.get("top_cases", []))[:3]:
            lesson = ""
            lessons = c.get("lessons") or []
            if lessons:
                lesson = str(lessons[0])
            elif c.get("why_correct_or_wrong"):
                lesson = str(c.get("why_correct_or_wrong", ""))
            rows.append({
                "case_id": c.get("case_id"),
                "decision_date": str(c.get("decision_date", ""))[:10],
                "node": c.get("node"),
                "hour": int(c.get("hour", -1) or -1),
                "decision": c.get("model_prediction"),
                "expected_return": _num(c.get("expected_return")),
                "outcome": _num(c.get("actual_Return")),
                "PnL": _num(c.get("PnL")),
                "lesson": lesson,
                "case_available_at": c.get("case_available_at", ""),
                "case_created_at": c.get("case_created_at", ""),
                "as_of_verified": bool(is_retrievable(c, decision_time)) if decision_time else False,
            })
        return {
            "status": "ok",
            "tool": "get_similar_cases",
            "decision_id": decision_id,
            "retrieval_rule": f"同 node × 小时窗 |Δh|≤3/6，按 |PnL| 排序，as_of: case_available_at <= {decision_time}",
            "cases": rows,
        }

    def get_data_provenance(self, decision_id: str, feature_name: Optional[str] = None) -> Dict[str, Any]:
        """Tool 5：特征 provenance（source/raw_file/source_type/target_time/available_at/is_mock/eligibility）。"""
        try:
            dec = self._require(decision_id)
        except KeyError:
            return self._not_found("get_data_provenance", decision_id)
        ctx = dec["context"]
        features = dec.get("features", dec.get("top_features", []))
        if feature_name is not None:
            hit = next((f for f in features if f["feature"] == feature_name), None)
            if hit is None:
                hit = _feature_provenance(feature_name, str(ctx["decision_date"])[:10],
                                          str(ctx["target_date"])[:10], int(ctx["hour"]),
                                          ctx.get("decision_cutoff_utc"))
            prov = self._prov_from_feature(hit)
            return {"status": "ok", "tool": "get_data_provenance", "decision_id": decision_id,
                    "feature_name": feature_name, "provenance": prov}
        provs = [self._prov_from_feature(f) for f in features]
        return {"status": "ok", "tool": "get_data_provenance", "decision_id": decision_id,
                "provenance": provs, "n": len(provs)}

    def get_post_trade_review(self, decision_id: str) -> Dict[str, Any]:
        """Tool 6：Post-trade 复盘；仅当 outcome_revealed=True 可调用，否则拒绝。

        Outcome Access Control（Service 层）：未 reveal（Lock 前 / Lock 后 Reveal
        前）一律返回 OUTCOME_NOT_REVEALED，绝不输出 actual_*；Reveal 后才 REVEALED。
        """
        try:
            dec = self._require(decision_id)
        except KeyError:
            return self._not_found("get_post_trade_review", decision_id)
        if not dec.get("outcome_revealed"):
            return {
                "status": OUTCOME_NOT_REVEALED,
                "tool": "get_post_trade_review",
                "decision_id": decision_id,
                "message": "actual_* 未揭晓：需 run_decision(..., reveal=True) 或 "
                           "lock_decision() 后 reveal_decision(decision_id) 才可复盘。",
            }
        outcome = dec.get("outcome") or {}
        review = dec.get("post_trade_review") or {}
        return {
            "status": "REVEALED",
            "tool": "get_post_trade_review",
            "decision_id": decision_id,
            "decision": dec.get("final_recommendation"),
            "actual_da": outcome.get("actual_da"),
            "actual_rtpd": outcome.get("actual_rtpd"),
            "actual_return": outcome.get("actual_return"),
            "pnl": outcome.get("pnl"),
            "model_prediction_error": outcome.get("model_prediction_error"),
            "direction_correct": outcome.get("direction_correct"),
            "review_category": list(review.get("primary", [])),
            "lessons": list(review.get("notes", [])),
            "review": review,
        }

    @staticmethod
    def _prov_from_feature(f: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "feature": f.get("feature"),
            "source": f.get("source"),
            "raw_file": f.get("raw_file"),
            "source_type": f.get("source_type"),
            "target_time": f.get("target_time"),
            "available_at": f.get("available_at"),
            "available_at_display": f.get("available_at_display"),
            "is_mock": bool(f.get("is_mock", False)),
            "backtest_eligible": bool(f.get("backtest_eligible", False)),
            "production_eligible": bool(f.get("production_eligible", False)),
            "decision_eligible": bool(f.get("decision_eligible", False)),
        }

    # ------------------------------------------------------------- 内省
    def list_decisions(self) -> List[Dict[str, Any]]:
        """返回全部已存档决策的轻量索引（decision_id + context + final）。"""
        out = []
        for k, v in self._decisions.items():
            out.append({
                "decision_id": k,
                "context": v.get("context", {}),
                "final_recommendation": v.get("final_recommendation"),
                "locked": bool(v.get("locked", False)),
                "outcome_revealed": bool(v.get("outcome_revealed", False)),
            })
        return out


# ---------------------------------------------------------------------------
# 模块级独立函数（默认单例；供 Agent D / Agent E 直接调用）
# ---------------------------------------------------------------------------
_default_service: Optional[DecisionService] = None


def default_service() -> DecisionService:
    global _default_service
    if _default_service is None:
        _default_service = DecisionService()
    return _default_service


def run_decision(decision_date: str, node: str, hour: int, reveal: bool = False,
                 service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).run_decision(decision_date, node, hour, reveal=reveal)


def get_decision(decision_date: str, node: str, hour: int,
                 service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).get_decision(decision_date, node, hour)


def get_feature_explanation(decision_id: str, service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).get_feature_explanation(decision_id)


def get_evidence(decision_id: str, service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).get_evidence(decision_id)


def get_similar_cases(decision_id: str, service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).get_similar_cases(decision_id)


def get_data_provenance(decision_id: str, feature_name: Optional[str] = None,
                        service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).get_data_provenance(decision_id, feature_name)


def get_post_trade_review(decision_id: str, service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).get_post_trade_review(decision_id)


def lock_decision(decision_id: str, service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).lock_decision(decision_id)


def reveal_decision(decision_id: str, service: Optional[DecisionService] = None) -> Dict[str, Any]:
    return (service or default_service()).reveal_decision(decision_id)


# ---------------------------------------------------------------------------
# Tool Schemas（供 Agent D 转成 LLM function calling / Agent E 转成 Web 表单）
# ---------------------------------------------------------------------------
TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "get_decision": {
        "name": "get_decision",
        "description": "获取/运行一个 CAISO 价差交易决策的模型输出、风控闸门、规则引擎与最终建议（不穿越，不含实际价格）。返回 decision_id 供后续工具查询。",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_date": {"type": "string", "description": "决策日期 D（ISO YYYY-MM-DD，目标日=D+1）"},
                "node": {"type": "string", "enum": sorted(NODE_REGION), "description": "目标节点"},
                "hour": {"type": "integer", "minimum": 1, "maximum": 24, "description": "目标小时 H1~H24"},
            },
            "required": ["decision_date", "node", "hour"],
        },
        "returns": {
            "decision_id": "str，后续工具按此查询",
            "model_output": "dict：expected_return/prob_positive/prob_negative/direction_probability/model_signal_strength/uncertainty/direction",
            "risk_gate": "dict：decision(PASS/WARNING/REJECT)/risk_reasons/rules_hit",
            "rule_engine": "dict：decision(BUY_DA/SELL_DA/NO_TRADE)/reasons/rules_hit",
            "final_recommendation": "str",
            "reason_codes": "list[str]",
        },
    },
    "get_feature_explanation": {
        "name": "get_feature_explanation",
        "description": "按 decision_id 返回决策的 Top 特征解释：特征值 + z 贡献 + 来源 + 可用性。",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "get_decision 返回的 decision_id"},
            },
            "required": ["decision_id"],
        },
        "returns": {
            "top_features": "list[{feature, value, contribution_z, hist_mean, hist_std, source, raw_file, available_at, availability}]",
        },
    },
    "get_evidence": {
        "name": "get_evidence",
        "description": "按 decision_id 返回决策时点的外部证据：eligible（可用）与 rejected（被 Time Gate 隔离）证据及其来源/发布时间/严重度/拒绝原因。",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "get_decision 返回的 decision_id"},
            },
            "required": ["decision_id"],
        },
        "returns": {
            "eligible": "list[{evidence_id, source, published_at, available_at, severity, eligible:True}]",
            "rejected": "list[{source, published_at, available_at, severity, eligible:False, rejection_reason}]",
        },
    },
    "get_similar_cases": {
        "name": "get_similar_cases",
        "description": "按 decision_id 返回历史相似案例（top 3，as-of）：date/node/hour/decision/outcome/PnL/lesson + case_available_at。",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "get_decision 返回的 decision_id"},
            },
            "required": ["decision_id"],
        },
        "returns": {
            "cases": "list[{case_id, decision_date, node, hour, decision, expected_return, outcome, PnL, lesson, case_available_at, as_of_verified}]",
        },
    },
    "get_data_provenance": {
        "name": "get_data_provenance",
        "description": "按 decision_id（可选 feature_name）返回特征数据的 provenance：source/raw_file/source_type/target_time/available_at/is_mock/backtest_eligible/production_eligible。",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "get_decision 返回的 decision_id"},
                "feature_name": {"type": "string", "description": "特征名（可选；缺省返回全部 Top 特征 provenance）"},
            },
            "required": ["decision_id"],
        },
        "returns": {
            "provenance": "list[{feature, source, raw_file, source_type, target_time, available_at, is_mock, backtest_eligible, production_eligible, decision_eligible}]",
        },
    },
    "get_post_trade_review": {
        "name": "get_post_trade_review",
        "description": "按 decision_id 返回 Post-trade 复盘（actual DA/RTPD/PnL/error/review_category/lessons）。仅当该决策 outcome_revealed=True 可调用；否则返回 OUTCOME_NOT_REVEALED。",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "get_decision 返回的 decision_id"},
            },
            "required": ["decision_id"],
        },
        "returns": {
            "status": "REVEALED | OUTCOME_NOT_REVEALED",
            "actual_da/actual_rtpd/actual_return/pnl": "float（仅 REVEALED 时存在）",
            "model_prediction_error": "float|None",
            "direction_correct": "bool|None",
            "review_category": "list[str]",
            "lessons": "list[str]",
        },
    },
}


# ---------------------------------------------------------------------------
# 自检演示（CLI：消费统一 DecisionSnapshot，展示 canonical 字段 + 运行时审计）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        svc = DecisionService(evidence_adapter=StaticEvidenceAdapter())
    except Exception as exc:  # pragma: no cover
        print(f"[DecisionService] 初始化失败: {exc}")
        raise
    dd, node, hour = "2026-07-08", "CONTROLX_1_N001", 2
    dec = svc.run_decision(dd, node, hour, reveal=True)
    print(json.dumps({
        "decision_id": dec["decision_id"],
        "context": dec["context"],
        "prediction": dec["prediction"],
        "final_recommendation": dec["final_recommendation"],
        "reason_codes": dec["reason_codes"],
        "audit": dec["audit"],
        "locked": dec["locked"],
        "outcome_revealed": dec["outcome_revealed"],
        "outcome": dec["outcome"],
        "post_trade_review": dec["post_trade_review"],
    }, ensure_ascii=False, indent=2, default=str))
    print("tools: get_decision ->",
          json.dumps(svc.get_decision(dd, node, hour)["final_recommendation"], ensure_ascii=False))
    # Outcome Access Control 演示：Lock 前 reveal 被拒
    did2 = svc.run_decision(dd, node, hour, reveal=False)["decision_id"]
    before = svc.reveal_decision(did2)
    print("reveal before lock ->", json.dumps(before, ensure_ascii=False))
    svc.lock_decision(did2)
    after = svc.reveal_decision(did2)
    print("reveal after lock  ->", after.get("status"))
