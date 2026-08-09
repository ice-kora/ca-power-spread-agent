# -*- coding: utf-8 -*-
"""
mvp_web.py —— CAISO Trading Decision Agent · Web MVP（Agent E）
=================================================================

浏览器可用的 Decision Workspace：一笔交易的完整生命周期
（Decision Context → Data & Provenance → Model → Evidence → Similar Cases
 → Risk Gate → Rule Engine → LOCK → REVEAL → PnL → Post-trade Review → Audit），
外加 Ask Trading Agent（LLM Copilot 追问 + Agent Trace）、GENERATE DAILY BRIEF、
Data Sources 页面、How It Works 页面。

【冻结交易核心】本模块只做 Web 封装，**不改动** code/decision_service.py 的任何
模型 / 规则 / 阈值 / PnL / evidence / case 逻辑。决策对象 100% 来自 DecisionService。
铁律：
  * actual_* 只在 LOCK DECISION 之后、经 REVEAL ACTUAL OUTCOME 才出现（服务端强制）。
  * 不造假证据 / 数字；证据经 Evidence Time Gate（agent/evidence/time_gate.py）程序裁决。
  * 无 API Key / 无 code/llm_copilot.py 时核心决策流程照常运行，Ask 面板诚实显示
    LLM NOT CONFIGURED。

启动：
    python mvp_web.py            # 默认 http://127.0.0.1:5000
    python mvp_web.py --port 8080
    python mvp_web.py --host 0.0.0.0 --offline   # --offline=默认不取外部 GFS 证据

路由清单：
    GET  /                         主页面（Decision Workspace）
    GET  /data-sources             Data Sources 页面
    GET  /how-it-works             How It Works 页面
    GET  /api/meta                 元信息（nodes / 黄金案例 / 日期范围 / 版本 / LLM 状态 / 翻译表）
    GET  /api/decisions            已生成决策轻量索引（含 lock 状态）
    POST /api/decision             运行决策 {decision_date,node,hour,evidence}
    GET  /api/decision/<id>        单个决策对象（含 lock 状态）
    POST /api/decision/<id>/lock   LOCK DECISION（锁定前禁止 reveal）
    POST /api/decision/<id>/reveal REVEAL ACTUAL OUTCOME（仅锁定后可调）
    POST /api/ask                  Ask Trading Agent（调 llm_copilot.ask；无 key → LLM NOT CONFIGURED）
    POST /api/brief                GENERATE DAILY BRIEF（扫描已生成决策汇总）

LLM Copilot 接口约定（Agent D 交付后自动生效）：
    from code import llm_copilot
    llm_copilot.ask(question, decision_id=None, trace=True)
        -> {"answer": str, "tools_called": [...], "trace": [...]}
    无 key → answer 含 "LLM NOT CONFIGURED"。本模块以防御式导入接入。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
from flask import Flask, abort, jsonify, render_template, request  # noqa: E402

from code.decision_service import (  # noqa: E402
    ALPHA_LABEL,
    CASE_LIBRARY_VERSION,
    DECISION_CUTOFF_DESC,
    EVIDENCE_TIME_GATE_VERSION,
    MARKET_RULE_VERSION,
    MODEL_VERSION,
    OUTCOME_NOT_REVEALED,
    RISK_GATE_VERSION,
    RULE_ENGINE_VERSION,
    SCHEMA_VERSION,
    DecisionService,
    StaticEvidenceAdapter,
)
from code.data_acquisition.schemas import (  # noqa: E402
    NODE_REGION,
    feature_available_at_display,
    latest_available_bound,
)

try:
    from code.canonical import availability_map as _availability_map
except Exception:  # pragma: no cover
    _availability_map = None

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["JSON_SORT_KEYS"] = False

_OFFLINE_DEFAULT = "--offline" in sys.argv  # 默认证据模式（离线则不取外部 GFS）
DEFAULT_EVIDENCE = "offline" if _OFFLINE_DEFAULT else "real"

# ---------------------------------------------------------------------------
# 数据范围（决策日 = target_date − 1）
# ---------------------------------------------------------------------------
_PRED_CSV = Path(REPO_ROOT) / "code" / "data" / "predictions_v2.csv"
try:
    _PRED_META = pd.read_csv(_PRED_CSV)
    _TARGET_MIN = pd.Timestamp(_PRED_META["target_date"].min()).normalize()
    _TARGET_MAX = pd.Timestamp(_PRED_META["target_date"].max()).normalize()
    _DD_MIN = (_TARGET_MIN - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    _DD_MAX = (_TARGET_MAX - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
except Exception:  # pragma: no cover
    _DD_MIN, _DD_MAX = "2026-06-01", "2026-08-04"


# ---------------------------------------------------------------------------
# 黄金案例（docs/mvp_demo_cases.md；全为真实 test 窗口数据）
# ---------------------------------------------------------------------------
GOLDEN_CASES: List[Dict[str, Any]] = [
    {"id": "B",  "label": "B · SELL 盈利（彩票右尾）",        "decision_date": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3},
    {"id": "C1", "label": "C1 · NO_TRADE 避险（RiskGate 成功）", "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
    {"id": "C2", "label": "C2 · NO_TRADE 弱信号",             "decision_date": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10},
    {"id": "D",  "label": "D · 模型 SELL 但错（诚实展示）",   "decision_date": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20},
    {"id": "E",  "label": "E · Evidence 被 Time Gate 拒（同 C1 参数，看隔离证据）",
                                                               "decision_date": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
]


# ---------------------------------------------------------------------------
# 业务翻译表（与 mvp_demo._gate_zh / _rule_zh 一致，供前端展示 reason_code）
# ---------------------------------------------------------------------------
GATE_ZH: Dict[str, str] = {
    "DATA_MISSING": "关键输入缺失（宁保守不穿越）",
    "BUY_ON_POSITIVE_DRIFT_NODE": "正漂移节点上做多（逆漂移）：该节点历史上 DA 持续高于 RTPD，做多长期负期望，被闸门拒绝",
    "SELL_ON_NEGATIVE_DRIFT_NODE": "负漂移节点上做空（逆漂移）：该节点历史上 DA 持续低于 RTPD，做空长期负期望，被闸门拒绝",
    "LOW_SAMPLE_SUPPORT": "同节点×小时历史样本不足（cold-start），统计不可靠，被闸门拒绝",
    "EXTREME_TAIL_NODE": "历史尾部风险深（cvar99/rcvar99 < −600），仅警告不拦截",
    "HIGH_VOLATILITY": "近 30 日波动 / 历史波动偏高，仅提示",
    "MODEL_UNSTABLE": "模型不确定度偏高（uncertainty > 0.95），仅提示",
    "SIMILAR_TAIL_LOSS_CASE": "命中历史相似亏损案例，提示交易员复核",
    "LOW_CONFIDENCE": "模型信号强度偏低（< 0.20），仅提示",
    "EXPECTED_RETURN_TOO_SMALL": "|预期收益| < 5 $/MWh，闸门仅提示（Rule Engine 负责转 NO_TRADE）",
    "EVIDENCE_CONFLICT": "可用证据方向与候选相反，仅提示",
    "EXTREME_STATE_EVIDENCE": "Pre-decision 证据出现极端状态（severity ≥ WARNING），保守拦截",
    "NO_CLEAR_DIRECTION": "方向不明",
}
RULE_ZH: Dict[str, str] = {
    "RISK_GATE_REJECTED": "风控闸门 REJECT → 保守放弃交易",
    "DATA_MISSING": "关键输入缺失",
    "EXPECTED_RETURN_TOO_SMALL": "预期收益幅度过小（< 5 $/MWh）",
    "LOW_CONFIDENCE": "模型信号强度过低（< 0.20）",
    "EVIDENCE_CONFLICT": "可用证据与候选方向冲突",
    "RISK_GATE_WARNING_ESCALATED": "闸门 WARNING 且配置升级拦截",
    "EXPECTED_RETURN_POSITIVE": "预期 Return > 0 → 卖出 DA（SELL_DA）",
    "EXPECTED_RETURN_NEGATIVE": "预期 Return < 0 → 买入 DA（BUY_DA）",
    "NO_CLEAR_DIRECTION": "预期 Return 无明确方向",
}


# ---------------------------------------------------------------------------
# DecisionService 工厂（按证据模式缓存；数据装载 ~0.2s，重建代价低）
# ---------------------------------------------------------------------------
_SERVICES: Dict[str, DecisionService] = {}
_LOCKS: Dict[str, Dict[str, Any]] = {}      # decision_id -> {locked, locked_at}
_LOCK_GUARD = threading.Lock()
_BRIEF_CACHE: Dict[str, Dict[str, Any]] = {}


def service(evidence: str = "real") -> DecisionService:
    """按证据模式取（并缓存）DecisionService。"""
    key = "offline" if evidence in ("offline", "static") else "real"
    if key not in _SERVICES:
        adapter = StaticEvidenceAdapter([]) if key == "offline" else None
        _SERVICES[key] = DecisionService(evidence_adapter=adapter)
    return _SERVICES[key]


def _find_service(decision_id: str) -> Optional[DecisionService]:
    for s in _SERVICES.values():
        if decision_id in s._decisions:  # noqa: SLF001 - 演示封装，读取内部注册表
            return s
    return None


def _service_evidence_key(decision_id: str) -> str:
    """返回持有该决策的 service 的 evidence 模式键（real/offline）。"""
    for key, s in _SERVICES.items():
        if decision_id in s._decisions:  # noqa: SLF001
            return key
    return "real"


def _lock_state(decision_id: str) -> Dict[str, Any]:
    with _LOCK_GUARD:
        lk = _LOCKS.get(decision_id, {"locked": False, "locked_at": None})
        return {"locked": bool(lk.get("locked")), "locked_at": lk.get("locked_at")}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 决策对象后处理（给前端展示增加工程元数据；不触碰交易逻辑）
# ---------------------------------------------------------------------------
def _feature_source_class(f: Dict[str, Any]) -> str:
    """Source 列枚举（COMPANY_FILE | CAISO_OASIS | NOAA_GFS/OPEN_METEO_GFS | DERIVED | MODEL | STATIC）。"""
    feat = str(f.get("feature", ""))
    raw = str(f.get("raw_file", ""))
    if f.get("source_type") == "STATIC" or feat in ("hour", "node", "dow", "month", "is_holiday", "solar_flag"):
        return "STATIC"
    if "peer" in feat:
        return "DERIVED（canonical 派生）"
    if "zone_weather" in raw:
        return "COMPANY_FILE（historical only）"
    if "load_" in raw:
        return "COMPANY_FILE"
    if "价格数据" in raw or raw.endswith(".xlsx"):
        return "COMPANY_FILE（已对账 CAISO OASIS）"
    return "CANONICAL"


def _feature_explain(f: Dict[str, Any]) -> str:
    """每行的"查看数据来源"说明（工程元数据，非交易逻辑）。"""
    feat = f.get("feature", "")
    if feat in ("hour", "node", "dow", "month", "is_holiday", "solar_flag"):
        return "静态日历 / 节点属性（STATIC），决策时点天然可得。"
    if "peer" in feat:
        return "同区节点联动特征：由 canonical.parquet 中同 Zone 节点在 T−2 的价格派生（DERIVED）。"
    if "load_2da" in feat or "load_peak" in feat:
        return ("负荷预测（2DA）：来自 load_CA_ISO_TAC_2DA.csv（公司口径）。"
                "该文件无 issue_time，按外部 BPM 证据约提前 2 日发布；严格回测 vintage 受限"
                "（availability_basis=ASSUMED_AVAILABLE），UI 以上界 decision_date 00:00 PT 表达，不显示伪精确时间戳。")
    if "load_actual" in feat:
        return "实际负荷（历史滞后）：来自 load_CA_ISO_TAC_ACTUAL.csv（公司口径），仅用 T−2 及更早的历史值。"
    if "t2m" in feat or "ssrd" in feat or "wind" in feat:
        return ("天气滞后（historical only）：来自 zone_weather_hourly.csv（公司口径，ERA5 风格再分析 + 合成段）。"
                "该文件无 run/issue 时间，**不可作为 D+1 天气预报**，只作历史 lag 使用；默认禁用 *_next 类特征。")
    if feat in ("spread",) or "spread" in feat or feat in ("da_lag", "rtpd_lag") or "price" in str(f.get("raw_file", "")).lower():
        return ("价格特征：来自 价格数据/*.xlsx（公司口径，已与 CAISO OASIS 对账一致，-c 后缀为阻塞分量/MCC）。"
                "lag1=T−2 交付日整日完整结算后可得；DAM 结果（da_*）约 T−1 13:00 PT 发布。")
    return f"特征 {feat} 的 raw 来源见上表（{f.get('raw_file', '?')}）。"


def _prepare_decision(dec: Dict[str, Any], evidence_mode: str) -> Dict[str, Any]:
    """给前端返回的决策对象：剔除内部字段 + 补充展示元数据。"""
    dec = dict(dec)
    dec.pop("_post_inputs", None)
    if dec.get("context"):
        dec["context"] = dict(dec["context"])
        dec["context"]["evidence_mode"] = evidence_mode
    av_map = _availability_map() if _availability_map else {}
    feats = []
    dd = str(dec.get("context", {}).get("decision_date", ""))[:10]
    for f in dec.get("top_features", []):
        f = dict(f)
        meta = av_map.get(f.get("feature"), {})
        f["availability_basis"] = meta.get("availability_basis", "UNKNOWN")
        f["latest_possible_available_at"] = meta.get("latest_possible_available_at", "")
        f["has_precise_publish_time"] = bool(meta.get("has_precise_publish_time", False))
        # P0-2 统一口径：展示 == Time Gate 判定。凡 canonical availability_map 有登记的
        # 特征，available_at 展示一律走 schemas.feature_available_at_display（同一上界；
        # 滞后/历史特征无精确发布时刻 → "≤ decision_date 00:00 PT"，不显示 23:59 伪精确时间戳）。
        if meta and f.get("feature") in av_map:
            disp = feature_available_at_display(meta, dd) or f.get("available_at_display")
            f["available_at_display"] = disp
            f["available_at"] = disp          # 展示字段（可空回退原值）
            f["available_at_utc"] = latest_available_bound(meta, dd) or ""
        f["source_class"] = _feature_source_class(f)
        f["explain"] = _feature_explain(f)
        feats.append(f)
    # 任务清单要求展示 hour / node 静态特征（若服务未覆盖则补齐）
    known = {f["feature"] for f in feats}
    ctx = dec.get("context", {})
    for sf in ("hour", "node"):
        if sf in known:
            continue
        meta = av_map.get(sf, {})
        disp = feature_available_at_display(meta, dd) if meta else "决策日 00:00 PT"
        feats.append({
            "feature": sf,
            "value": int(ctx.get("hour", 0)) if sf == "hour" else ctx.get("node", ""),
            "hist_mean": None, "hist_std": None, "z": None,
            "source": "静态（节点/小时属性）", "raw_file": "static/calendar",
            "source_type": "STATIC", "target_time": "", "available_at": disp,
            "available_at_display": disp, "available_at_utc": "",
            "is_mock": False, "backtest_eligible": True, "production_eligible": True,
            "decision_eligible": True, "availability": "ELIGIBLE",
            "availability_basis": "STATIC", "latest_possible_available_at": "",
            "has_precise_publish_time": False,
            "source_class": "STATIC", "explain": "静态属性，决策时点天然可得。",
        })
    dec["top_features"] = feats
    dec["lock"] = _lock_state(dec.get("decision_id", ""))
    return dec


# ---------------------------------------------------------------------------
# LLM Copilot（防御式接入；code/llm_copilot.py 由 Agent D 交付）
# ---------------------------------------------------------------------------
def copilot_status() -> Dict[str, Any]:
    try:
        import code.llm_copilot as _lc  # noqa: PLC0415
    except Exception:
        return {"configured": False, "module_present": False}
    status_fn = getattr(_lc, "copilot_status", None)
    if callable(status_fn):
        try:
            st = status_fn() or {}
            st["module_present"] = True
            return st
        except Exception as exc:  # pragma: no cover
            return {"configured": False, "module_present": True, "error": str(exc)}
    ask_fn = getattr(_lc, "ask", None)
    if ask_fn is None:
        obj = getattr(_lc, "llm_copilot", None)
        ask_fn = getattr(obj, "ask", None) if obj is not None else None
    return {"configured": callable(ask_fn), "module_present": True}


def _llm_not_configured(question: str, reason: str) -> Dict[str, Any]:
    return {
        "answer": (
            "LLM NOT CONFIGURED —— 未检测到 code/llm_copilot.py 的 ask()（或无 API Key）。\n"
            "交易决策流程不受影响（全部由白盒 DecisionService + 6 个 Tool 完成）。\n"
            f"状态：{reason}。配置后这里会显示 LLM 基于 6 个结构化 Tool 的回答与 Agent Trace。"
        ),
        "tools_called": [],
        "trace": [
            {"step": "user", "content": question},
            {"step": "tool", "tool_name": "(none)", "arguments": {},
             "result_summary": "LLM NOT CONFIGURED：未调用任何工具", "status": "skipped"},
        ],
        "llm_status": "NOT_CONFIGURED",
    }


def ask_copilot(question: str, decision_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        import code.llm_copilot as _lc  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        return _llm_not_configured(question, f"code/llm_copilot.py 尚未交付（ImportError: {exc}）")
    ask_fn = getattr(_lc, "ask", None)
    if ask_fn is None:
        obj = getattr(_lc, "llm_copilot", None)
        ask_fn = getattr(obj, "ask", None) if obj is not None else None
    if ask_fn is None:
        return _llm_not_configured(question, "code.llm_copilot 无 ask(question, decision_id=None, trace=True)")
    # 关键：直接用 make_copilot 绑定持有 decision_id 的 DecisionService 实例。
    # 不能走模块级 ask() / default_copilot()：它内部会缓存一个绑定 default_service()
    # 的 copilot，且 copilot_status() 在 /api/meta 时已触发该缓存 → 忽略传入 service。
    svc = _find_service(decision_id) if decision_id else (
        _SERVICES.get("real") or next(iter(_SERVICES.values()), None)
    )
    make_fn = getattr(_lc, "make_copilot", None)
    if callable(make_fn):
        cp = make_fn(service=svc)
    else:  # 兜底：老接口
        cp = _lc.default_copilot(service=svc)
    try:
        result = cp.ask(question=question, decision_id=decision_id, trace=True)
        if not isinstance(result, dict):
            result = {"answer": str(result), "tools_called": [], "trace": []}
        return result
    except Exception as exc:
        return {"answer": f"LLM ERROR: {type(exc).__name__}: {exc}",
                "tools_called": [], "trace": [], "llm_status": "ERROR"}


# ---------------------------------------------------------------------------
# 路由：页面
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("mvp_index.html")


@app.get("/data-sources")
def data_sources():
    return render_template("mvp_sources.html")


@app.get("/how-it-works")
def how_it_works():
    return render_template("mvp_how.html")


# ---------------------------------------------------------------------------
# 路由：元信息
# ---------------------------------------------------------------------------
@app.get("/api/meta")
def api_meta():
    return jsonify({
        "app": "CAISO Trading Decision Agent · Web MVP",
        "alpha_label": ALPHA_LABEL,
        "cutoff_desc": DECISION_CUTOFF_DESC,
        "nodes": {k: v for k, v in NODE_REGION.items()},
        "node_short": {k: k.replace("_1_N001", "") for k in NODE_REGION},
        "min_decision_date": _DD_MIN,
        "max_decision_date": _DD_MAX,
        "golden_cases": GOLDEN_CASES,
        "llm": copilot_status(),
        "default_evidence": DEFAULT_EVIDENCE,
        "evidence_modes": [
            {"id": "real", "label": "实时 GFS（真实证据，需网络；失败诚实降级为空）"},
            {"id": "offline", "label": "离线静态（不取外部证据，纯本地演示）"},
        ],
        "trade_semantics": {
            "cutoff": "10:00 PT",
            "BUY": "RTPD − DA",
            "SELL": "DA − RTPD",
            "NO_TRADE": "0",
        },
        "versions": {
            "market_rule": MARKET_RULE_VERSION,
            "model": MODEL_VERSION,
            "rule_engine": RULE_ENGINE_VERSION,
            "risk_gate": RISK_GATE_VERSION,
            "evidence_time_gate": EVIDENCE_TIME_GATE_VERSION,
            "case_library": CASE_LIBRARY_VERSION,
            "schema": SCHEMA_VERSION,
        },
        "reason_translations": {"gate": GATE_ZH, "rule": RULE_ZH},
    })


# ---------------------------------------------------------------------------
# 路由：决策生命周期
# ---------------------------------------------------------------------------
@app.get("/api/decisions")
def api_decisions():
    rows: List[Dict[str, Any]] = []
    for s in _SERVICES.values():
        for r in s.list_decisions():
            ctx = r.get("context", {})
            rows.append({
                "decision_id": r.get("decision_id"),
                "decision_date": str(ctx.get("decision_date", ""))[:10],
                "node": ctx.get("node"),
                "hour": ctx.get("hour"),
                "final_recommendation": r.get("final_recommendation"),
                "outcome_revealed": bool(r.get("outcome_revealed", False)),
                "lock": _lock_state(r.get("decision_id", "")),
            })
    rows.sort(key=lambda r: (str(r["decision_date"]), str(r["node"]), int(r.get("hour", 0) or 0)))
    return jsonify({"status": "ok", "n": len(rows), "decisions": rows})


@app.post("/api/decision")
def api_run_decision():
    data = request.get_json(force=True, silent=True) or {}
    dd = str(data.get("decision_date", "") or "")[:10]
    node = str(data.get("node", "") or "")
    try:
        hour = int(data.get("hour", 0))
    except (TypeError, ValueError):
        hour = 0
    evidence = str(data.get("evidence", DEFAULT_EVIDENCE) or "real")
    if not dd or not node or not (1 <= hour <= 24):
        return jsonify({"status": "error", "message": "decision_date / node / hour(1-24) 必填"} ), 400
    if node not in NODE_REGION:
        return jsonify({"status": "error", "message": f"未知节点 {node}（可用: {sorted(NODE_REGION)}）"}), 400
    if not (_DD_MIN <= dd <= _DD_MAX):
        return jsonify({"status": "error",
                        "message": f"decision_date {dd} 超出数据范围 {_DD_MIN} ~ {_DD_MAX}（test 窗口）"}), 400
    try:
        svc = service(evidence)
        dec = svc.run_decision(dd, node, hour, reveal=False)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:  # pragma: no cover
        return jsonify({"status": "error", "message": f"决策运行失败: {type(exc).__name__}: {exc}"}), 500
    return jsonify({"status": "ok", "decision": _prepare_decision(dec, evidence)})


@app.get("/api/decision/<decision_id>")
def api_get_decision(decision_id: str):
    svc = _find_service(decision_id)
    if svc is None:
        return jsonify({"status": "error", "message": f"decision_id 不存在: {decision_id!r}"}), 404
    dec = svc._decisions.get(decision_id)  # noqa: SLF001
    ev_key = _service_evidence_key(decision_id)
    return jsonify({"status": "ok", "decision": _prepare_decision(dec, ev_key)})


@app.post("/api/decision/<decision_id>/lock")
def api_lock_decision(decision_id: str):
    svc = _find_service(decision_id)
    if svc is None:
        return jsonify({"status": "error", "message": f"decision_id 不存在: {decision_id!r}"}), 404
    with _LOCK_GUARD:
        if decision_id not in _LOCKS:
            _LOCKS[decision_id] = {"locked": True, "locked_at": _now_iso()}
        else:
            _LOCKS[decision_id]["locked"] = True
            _LOCKS[decision_id].setdefault("locked_at", _now_iso())
    return jsonify({"status": "LOCKED", "decision_id": decision_id,
                    "locked_at": _LOCKS[decision_id]["locked_at"],
                    "message": "决策已锁定。锁定前系统不展示任何 actual/outcome。"})


@app.post("/api/decision/<decision_id>/reveal")
def api_reveal_decision(decision_id: str):
    svc = _find_service(decision_id)
    if svc is None:
        return jsonify({"status": "error", "message": f"decision_id 不存在: {decision_id!r}"}), 404
    lk = _lock_state(decision_id)
    if not lk["locked"]:
        return jsonify({"status": "NOT_LOCKED",
                        "message": "必须先 LOCK DECISION 才能揭晓 Actual Outcome（锁定前禁止显示实际结果）。"}), 403
    pt = svc.reveal_decision(decision_id)
    return jsonify({"status": "REVEALED", "decision_id": decision_id,
                    "post_trade": pt, "locked_at": lk.get("locked_at")})


# ---------------------------------------------------------------------------
# 路由：Ask Trading Agent
# ---------------------------------------------------------------------------
@app.post("/api/ask")
def api_ask():
    data = request.get_json(force=True, silent=True) or {}
    question = str(data.get("question", "") or "").strip()
    decision_id = (data.get("decision_id") or None)
    if not question:
        return jsonify({"status": "error", "message": "question 必填"}), 400
    # 若传入 decision_id，先校验存在
    if decision_id and _find_service(decision_id) is None:
        return jsonify({"status": "error", "message": f"decision_id 不存在: {decision_id!r}"}), 404
    result = ask_copilot(question, decision_id)
    return jsonify({"status": "ok", **result})


# ---------------------------------------------------------------------------
# 路由：GENERATE DAILY BRIEF
# ---------------------------------------------------------------------------
@app.post("/api/brief")
def api_brief():
    data = request.get_json(force=True, silent=True) or {}
    dd = str(data.get("decision_date", "") or "")[:10]
    node = data.get("node") or None
    all_hours = bool(data.get("all_hours", False))
    evidence = str(data.get("evidence", DEFAULT_EVIDENCE) or "real")
    if not dd:
        return jsonify({"status": "error", "message": "decision_date 必填"}), 400
    if node and node not in NODE_REGION:
        return jsonify({"status": "error", "message": f"未知节点 {node}"}), 400

    if all_hours:
        # 全量扫描：确保该日期 × 节点范围的全部 24 小时已生成决策（缺则运行）
        svc = service(evidence)
        nodes = [node] if node else list(NODE_REGION)
        for n in nodes:
            for h in range(1, 25):
                try:
                    svc.get_decision(dd, n, h)
                except Exception as exc:  # pragma: no cover
                    print(f"[brief] {dd} {n} H{h} 失败: {exc}")

    # 汇总：扫描各 service 注册表（按证据模式去重）
    rows: List[Dict[str, Any]] = []
    seen = set()
    for s in _SERVICES.values():
        for r in s.list_decisions():
            ctx = r.get("context", {})
            if str(ctx.get("decision_date", ""))[:10] != dd:
                continue
            if node and ctx.get("node") != node:
                continue
            key = (str(ctx.get("decision_date", ""))[:10], ctx.get("node"), int(ctx.get("hour", 0) or 0))
            if key in seen:
                continue
            seen.add(key)
            full = s._decisions.get(r.get("decision_id"), {})  # noqa: SLF001
            rows.append({
                "node": ctx.get("node"),
                "hour": int(ctx.get("hour", 0) or 0),
                "final": r.get("final_recommendation"),
                "outcome_revealed": bool(r.get("outcome_revealed", False)),
                "lock": _lock_state(r.get("decision_id", "")),
                "expected_return": _safe_num(full.get("model_output", {}).get("expected_return")),
                "signal_strength": _safe_num(full.get("model_output", {}).get("model_signal_strength")),
                "direction": full.get("model_output", {}).get("direction"),
                "risk_gate": full.get("risk_gate", {}).get("decision"),
                "risk_reasons": list(full.get("risk_gate", {}).get("risk_reasons", [])),
                "reason_codes": list(full.get("reason_codes", [])),
                "decision_id": r.get("decision_id"),
            })

    rows.sort(key=lambda r: (str(r["node"]), int(r["hour"])))
    # ---- 汇总 ----
    finals: Dict[str, int] = {"BUY_DA": 0, "SELL_DA": 0, "NO_TRADE": 0}
    gates: Dict[str, int] = {"PASS": 0, "WARNING": 0, "REJECT": 0}
    for r in rows:
        finals[r["final"]] = finals.get(r["final"], 0) + 1
        gates[r["risk_gate"]] = gates.get(r["risk_gate"], 0) + 1
    trade_rows = [r for r in rows if r["final"] in ("BUY_DA", "SELL_DA")]
    trade_rows.sort(key=lambda r: -(abs(r["expected_return"] or 0.0)))
    reject_rows = [r for r in rows if r["risk_gate"] == "REJECT"]
    reject_rows.sort(key=lambda r: -(abs(r["expected_return"] or 0.0)))
    # Top opportunities（仅 as-of 信息：|expected_return| 降序）
    opportunities = [{
        "node": r["node"], "hour": r["hour"], "final": r["final"],
        "expected_return": r["expected_return"], "signal_strength": r["signal_strength"],
        "decision_id": r["decision_id"],
    } for r in trade_rows[:3]]
    # Top risks（RiskGate REJECT / WARNING+尾损）
    risks = []
    for r in reject_rows[:3]:
        risks.append({
            "node": r["node"], "hour": r["hour"], "final": r["final"],
            "expected_return": r["expected_return"],
            "risk_reasons": [GATE_ZH.get(c, c) for c in r["risk_reasons"]],
            "decision_id": r["decision_id"],
        })
    return jsonify({
        "status": "ok",
        "decision_date": dd,
        "node_scope": node or "全部节点",
        "evidence_mode": evidence,
        "n_candidates": len(rows),
        "summary": {"BUY_DA": finals["BUY_DA"], "SELL_DA": finals["SELL_DA"],
                    "NO_TRADE": finals["NO_TRADE"],
                    "risk_gate": {"PASS": gates["PASS"], "WARNING": gates["WARNING"], "REJECT": gates["REJECT"]}},
        "top_opportunities": opportunities,
        "top_risks": risks,
        "rows": rows,
    })


def _safe_num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="CAISO Trading Decision Agent · Web MVP")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--offline", action="store_true",
                    help="默认证据模式=离线（不取外部 GFS 证据，纯本地演示）")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    print("=" * 78)
    print("  CAISO Trading Decision Agent · Web MVP")
    print(f"  URL  : http://{args.host}:{args.port}")
    print(f"  LLM  : {'configured' if copilot_status()['configured'] else 'NOT CONFIGURED (Ask 面板将诚实提示)'}")
    print(f"  证据 : 默认 {'offline(不取外部)' if args.offline else 'real(实时 GFS，失败诚实降级)'}；页面内可切换")
    print(f"  数据 : decision_date {_DD_MIN} ~ {_DD_MAX}（test 窗口）")
    print("  约束 : LOCK 前不展示 actual；不造假证据；冻结交易核心")
    print("=" * 78)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
