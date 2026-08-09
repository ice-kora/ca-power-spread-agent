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

V0.3.1.1（Agent C · Web 加固；交易核心冻结，不改 UI 大结构）：
  * 页面顶部 **MVP Status** 诚实状态栏：MODEL ALPHA=WEAK / PROFITABILITY VERIFIED=NO /
    DATA MODE=DEMO|FULL（Agent A 经环境变量 DATA_MODE 指定）/ LLM=CONNECTED|NOT CONFIGURED /
    AUTO TRADING=DISABLED / SETTLEMENT=SIMPLIFIED SIGNAL BACKTEST。不误导业务方。
  * **Audit Panel 显示真实运行审计**：消费 DecisionService 的 runtime audit（Feature /
    Evidence Time Gate / Mock / Case As-of / Outcome Leakage，每项 PASS/FAIL/WARNING +
    checked/failed）。若服务端已提供结构化 audit.runtime 则直接消费；否则按决策对象
    真实数据计算（`_compute_runtime_audit`）。OVERALL 一律由真实结果推导，**页面绝不写死 PASS**。
  * **错误处理**：所有 API 错误返回 `ERROR CODE + Human-readable message + Suggested action`，
    traceback 只写日志（app.logger.exception），绝不把 Python traceback 抛给业务人员。
    覆盖：Missing Artifact / Unsupported Date / Unsupported Node / No Prediction /
    Evidence Source Unavailable / LLM Unavailable 等。

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
import traceback
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
    MODEL_VERSION,
    MVP_LABEL,
    OUTCOME_NOT_REVEALED,
    RISK_GATE_VERSION,
    RULE_ENGINE_VERSION,
    SCHEMA_VERSION,
    DecisionService,
    HistoricalSnapshotEvidenceAdapter,
    StaticEvidenceAdapter,
)
from code.data_acquisition.schemas import (  # noqa: E402
    NODE_REGION,
    feature_available_at_display,
    latest_available_bound,
)
from data_mode import (  # noqa: E402
    DATA_MODE_ENV,
    MODE_DEMO,
    MODE_FULL,
    resolve_data_mode,
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

# 旧别名 DATA_MODE → MVP_DATA_MODE（data_mode.py 单一来源，供 DecisionService 读取）
if not os.environ.get(DATA_MODE_ENV) and os.environ.get("DATA_MODE", "").strip().upper() in (MODE_FULL, MODE_DEMO):
    os.environ[DATA_MODE_ENV] = os.environ["DATA_MODE"].strip().upper()


def _mode_override() -> Optional[str]:
    """有效数据模式覆盖：MVP_DATA_MODE（主）或旧别名 DATA_MODE；非法/空 → None（自动探测）。"""
    v = str(os.environ.get(DATA_MODE_ENV, "") or os.environ.get("DATA_MODE", "") or "").strip().upper()
    return v if v in (MODE_FULL, MODE_DEMO) else None


# ---------------------------------------------------------------------------
# 数据范围（决策日 = target_date − 1）
# ---------------------------------------------------------------------------
# 数据模式（FULL / DEMO）由 data_mode.py 自动探测；MVP_DATA_MODE / DATA_MODE 可覆盖。
# DEMO 模式 predictions 从 demo_artifacts/predictions_demo.csv 读取 → 日期范围
# 收敛到黄金案例窗口，页面只暴露 demo 可用的 decision_date。
_PRED_CSV = resolve_data_mode(override=_mode_override()).pred_path
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
    """按证据模式取（并缓存）DecisionService。

    - offline      → StaticEvidenceAdapter([])：不取外部证据（EVIDENCE MODE = NONE）。
    - real         → DEMO MODE 用 HistoricalSnapshotEvidenceAdapter（真实历史 GFS
      Evidence Snapshot，可重复、不依赖现场网络，EVIDENCE MODE = HISTORICAL_SNAPSHOT）；
      FULL / SHADOW 用 DefaultEvidenceAdapter（真实 Collector，EVIDENCE MODE = LIVE）。
    """
    key = "offline" if evidence in ("offline", "static") else "real"
    if key not in _SERVICES:
        if key == "offline":
            adapter = StaticEvidenceAdapter([])
        elif _data_mode() == MODE_DEMO:
            adapter = HistoricalSnapshotEvidenceAdapter()
        else:
            adapter = None          # DefaultEvidenceAdapter（FULL/LIVE 真实 Collector）
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
# 结构化错误（V0.3.1.1）：ERROR CODE + Human-readable message + Suggested action。
# traceback 只写日志（app.logger.exception），绝不返回给浏览器。
# ---------------------------------------------------------------------------
ERROR_CATALOG: Dict[str, Dict[str, str]] = {
    "INVALID_REQUEST": {
        "code": "INVALID_REQUEST",
        "message": "请求参数缺失或格式错误。",
        "action": "请补齐 decision_date / node / hour(1-24) 后重试。",
    },
    "INVALID_HOUR": {
        "code": "INVALID_HOUR",
        "message": "目标小时越界（要求 H1~H24）。",
        "action": "请从 TARGET HOUR 下拉框选择 H1~H24。",
    },
    "UNSUPPORTED_NODE": {
        "code": "UNSUPPORTED_NODE",
        "message": "请求的节点不受支持。",
        "action": "请从 NODE 下拉框选择合法节点。",
    },
    "UNSUPPORTED_DATE": {
        "code": "UNSUPPORTED_DATE",
        "message": "请求的决策日期超出支持的数据范围（test 窗口）。",
        "action": "请选择范围内日期，或改用 GOLDEN DEMO CASE。",
    },
    "MISSING_ARTIFACT": {
        "code": "MISSING_ARTIFACT",
        "message": "核心数据文件缺失或不可读，无法运行决策。",
        "action": "请运行 python prepare_mvp.py 查看缺失项并按提示重建（或联系工程师）。",
    },
    "NO_PREDICTION": {
        "code": "NO_PREDICTION",
        "message": "该日期×节点×小时不在模型预测窗口，无模型输出，按数据缺失保守处理。",
        "action": "请在 test 预测窗口内选择日期，或改用 GOLDEN DEMO CASE。",
    },
    "EVIDENCE_SOURCE_UNAVAILABLE": {
        "code": "EVIDENCE_SOURCE_UNAVAILABLE",
        "message": "实时外部证据源当前未返回可用证据（GFS 拉取失败 / 无缓存，或决策时点前确无真实证据）。",
        "action": "可重试；若需纯本地演示，请将 EVIDENCE 切换为“离线静态”。",
    },
    "LLM_UNAVAILABLE": {
        "code": "LLM_UNAVAILABLE",
        "message": "LLM Copilot 当前不可用（未配置 API Key 或调用失败）；交易决策流程不受影响。",
        "action": "配置 LLM_API_KEY / LLM_PROVIDER / LLM_MODEL 后重启即可启用解释；无 Key 时仍可查看工具结果与 Agent Trace。",
    },
    "NOT_FOUND": {
        "code": "NOT_FOUND",
        "message": "请求的资源不存在。",
        "action": "请检查参数或先运行一次决策再查询。",
    },
    "INTERNAL_ERROR": {
        "code": "INTERNAL_ERROR",
        "message": "系统内部错误，本次请求未能完成。",
        "action": "请稍后重试；若持续出现，请联系工程师并提供日志。",
    },
}


def _error(code: str, message: str, action: str, http_status: int = 400,
           detail: Optional[str] = None, **extra: Any):
    """构造统一结构化错误响应（jsonify + status）。"""
    payload: Dict[str, Any] = {
        "status": "error",
        "error": {"code": code, "message": message, "suggested_action": action},
    }
    if detail:
        payload["error"]["detail"] = detail
    if extra:
        payload.update(extra)
    return jsonify(payload), http_status


def _safe_detail(exc: Exception) -> Optional[str]:
    """给内部错误附一条单行细节（绝不包含 traceback；业务人员也可读）。"""
    try:
        msg = str(exc).strip()
    except Exception:  # noqa: BLE001
        msg = ""
    if len(msg) > 300:
        msg = msg[:300] + "…"
    return msg or None


def _classify_error(exc: Exception) -> Dict[str, str]:
    """把底层异常映射为业务可读的 ERROR CATALOG 条目。"""
    msg = str(exc or "")
    low = msg.lower()
    if isinstance(exc, FileNotFoundError) or "no such file" in low or "does not exist" in low \
            or "cannot find" in low or "not a valid path" in low:
        return ERROR_CATALOG["MISSING_ARTIFACT"]
    if "未知节点" in msg or "unknown node" in low:
        return ERROR_CATALOG["UNSUPPORTED_NODE"]
    if "canonical 无" in msg or "超出数据范围" in msg or "no canonical" in low \
            or "out of range" in low or "no row" in low:
        return ERROR_CATALOG["UNSUPPORTED_DATE"]
    if "hour 越界" in msg or "hour out of" in low:
        return ERROR_CATALOG["INVALID_HOUR"]
    return ERROR_CATALOG["INTERNAL_ERROR"]


def _data_mode() -> str:
    """运行数据模式：FULL | DEMO（Agent A 单一来源 data_mode.py 自动探测）。

    - 环境变量 MVP_DATA_MODE=demo|full 显式覆盖（与 data_mode.py 同源；兼容旧别名 DATA_MODE）。
    - 非法值 / 未设置 → 自动探测：完整 artifacts 存在=FULL，否则 demo_artifacts 存在=DEMO。
    - DEMO ≠ MOCK：DEMO 是真实历史最小切片，可真实推荐；MOCK 永不参与真实推荐。
    """
    return resolve_data_mode(override=_mode_override()).mode


def _log_exception(ctx: str) -> None:
    """把 traceback 写进日志（只写日志，不返回给页面）。"""
    try:
        app.logger.exception("[mvp_web] %s", ctx)
    except Exception:  # noqa: BLE001 - logger 失败不影响响应
        traceback.print_exc()


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


# ---------------------------------------------------------------------------
# Runtime Audit（V0.3.1.1）：消费 DecisionService 的真实运行审计，绝不页面写死 PASS。
#
# 消费契约（Agent B 可在 code/decision_service.py 直接产出同构结构）：
#   dec["audit"]["runtime"] = {
#     "generated_at": "<utc iso>",
#     "items": [
#       {"key": "FEATURE_AS_OF",      "name": "...", "status": "PASS|FAIL|WARNING",
#        "checked": <int>, "failed": <int>, "note": "..."},
#       {"key": "EVIDENCE_TIME_GATE", ...}, {"key": "NO_MOCK", ...},
#       {"key": "CASE_AS_OF", ...}, {"key": "OUTCOME_LEAKAGE", ...},
#     ],
#   }
# 若服务端未提供 / 结构不全 → 本模块按决策对象真实数据计算（_compute_runtime_audit）。
# OVERALL 一律由 items 的真实 status 推导（任一 FAIL → FAIL；否则任一 WARNING → WARNING；否则 PASS）。
# ---------------------------------------------------------------------------
_RUNTIME_AUDIT_ORDER = ["FEATURE_AS_OF", "EVIDENCE_TIME_GATE", "EVIDENCE_AVAILABILITY",
                        "EVIDENCE_PROVENANCE", "NO_MOCK", "CASE_AS_OF", "OUTCOME_LEAKAGE"]


def _ts(x) -> Optional[Any]:
    """把时间字符串解析为可比较对象；不可解析 → None。"""
    if not x:
        return None
    try:
        t = pd.Timestamp(x)
        if t.tz is not None:
            t = t.tz_localize(None)
        return t
    except Exception:  # noqa: BLE001
        return None


def _audit_item(key: str, name: str, checked: int, failed: int,
                note: str = "", warn_count: int = 0) -> Dict[str, Any]:
    """单条审计项：status 由真实计数推导（FAIL > WARNING > PASS），不写死。"""
    if failed > 0:
        status = "FAIL"
    elif warn_count > 0:
        status = "WARNING"
    else:
        status = "PASS"
    return {"key": key, "name": name, "status": status,
            "checked": int(checked), "failed": int(failed), "note": note}


def _overall_from_items(items: List[Dict[str, Any]]) -> str:
    statuses = {str(i.get("status", "")).upper() for i in items}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses:
        return "WARNING"
    return "PASS"


def _compute_runtime_audit(dec: Dict[str, Any]) -> Dict[str, Any]:
    """按真实决策对象计算 5 项运行审计（无任何写死的 PASS）。"""
    ctx = dec.get("context") or {}
    dd = str(ctx.get("decision_date", ""))[:10]
    target_date = str(ctx.get("target_date", ""))[:10]
    cutoff_utc = ctx.get("decision_cutoff_utc") or (dec.get("audit") or {}).get("decision_cutoff_utc") or ""
    items: List[Dict[str, Any]] = []

    # 1) Feature As-of Eligibility：每个进入决策的特征必须 decision_eligible=True；
    #    未登记 availability_basis 的可证性存疑 → WARNING（不直接判 FAIL）。
    feats = dec.get("top_features") or []
    f_fail, f_warn, f_notes = 0, 0, []
    for f in feats:
        if not bool(f.get("decision_eligible", True)):
            f_fail += 1
            f_notes.append(f"feature={f.get('feature')} decision_eligible=False")
        elif str(f.get("availability_basis", "")).upper() in ("", "UNKNOWN", "NONE"):
            f_warn += 1
            f_notes.append(f"feature={f.get('feature')} availability_basis 未登记，as-of 可证性存疑")
    items.append(_audit_item(
        "FEATURE_AS_OF", "Feature As-of Eligibility", len(feats), f_fail,
        note="；".join(f_notes[:4]) or "全部特征 decision_eligible=True", warn_count=f_warn))

    # 2) Evidence Time Gate（V0.3.1.3 available-at-only）：eligible 必须
    #    available_at <= cutoff 且非 MOCK（缺失 → FAIL）；rejected 非 MOCK 且
    #    available_at 晚于 cutoff（缺失已正确隔离 → WARNING）。逐条程序复验。
    ev = dec.get("evidence") or {}
    elig = list(ev.get("eligible") or [])
    rej = list(ev.get("rejected") or ev.get("post_decision") or [])
    g_fail, g_warn, g_notes, g_warns = 0, 0, [], []
    cut = _ts(cutoff_utc)
    for e in elig:
        if bool(e.get("is_mock")):
            g_fail += 1
            g_notes.append(f"eligible 含 MOCK: {e.get('evidence_id')}")
            continue
        avail = _ts(e.get("available_at"))          # 只判 available_at，不 fallback
        if cut is not None and (avail is None or avail > cut):
            g_fail += 1
            g_notes.append(f"eligible={e.get('evidence_id')} available_at 缺失或晚于 cutoff")
    for r in rej:
        if bool(r.get("is_mock")):
            continue  # MOCK 隔离属预期
        if not str(r.get("available_at") or "").strip():
            g_warn += 1
            g_warns.append(f"rejected={r.get('evidence_id')} 无已证 available_at（MISSING_AVAILABLE_AT，已隔离不进决策）")
        elif not str(r.get("rejection_reason") or "").strip():
            g_fail += 1
            g_notes.append(f"rejected={r.get('evidence_id')} 无 rejection_reason（应可判定）")
    items.append(_audit_item(
        "EVIDENCE_TIME_GATE", "Evidence Time Gate", len(elig) + len(rej), g_fail,
        note="；".join(g_notes[:3] + g_warns[:3]) or
             (f"{len(elig)} eligible / {len(rej)} rejected，与 Time Gate 判定一致" if (elig or rej)
              else "无外部证据（诚实降级为空）"), warn_count=g_warn))

    # 2b) Evidence Availability（V0.3.1.3）：每条证据必须有已证 available_at（Time Gate 唯一判据）。
    a_all = elig + rej
    a_missing = sum(1 for e in a_all if not str(e.get("available_at") or "").strip())
    items.append(_audit_item(
        "EVIDENCE_AVAILABILITY", "Evidence Availability", len(a_all), 0,
        note=(f"{a_missing} 条证据无已证 available_at（MISSING_AVAILABLE_AT / AVAILABILITY_NOT_PROVEN，已隔离不进决策）"
              if a_missing else f"全部 {len(a_all)} 条证据均有已证 available_at"),
        warn_count=a_missing))

    # 2c) Evidence Provenance（V0.3.1.3）：来源 / 类型 / 快照声明齐备且真实。
    p_fail, p_warn, p_notes = 0, 0, []
    for e in a_all:
        if not str(e.get("source") or "").strip():
            p_warn += 1
            p_notes.append(f"证据缺 source: {e.get('evidence_id')}")
        if not str(e.get("source_type") or "").strip():
            p_warn += 1
            p_notes.append(f"证据缺 source_type: {e.get('evidence_id')}")
    prov = dec.get("context", {}).get("evidence_provenance") or {}
    if prov.get("historical_snapshot"):
        if prov.get("contains_mock"):
            p_fail += 1
            p_notes.append("Historical Snapshot contains_mock=True（违反诚实声明）")
        if not prov.get("artifact_hash"):
            p_warn += 1
            p_notes.append("Historical Snapshot 缺 artifact_hash（无法核对完整性）")
    items.append(_audit_item(
        "EVIDENCE_PROVENANCE", "Evidence Provenance",
        len(a_all) + (1 if prov.get("historical_snapshot") else 0), p_fail,
        note="；".join(p_notes[:4]) or
             ("Historical Evidence Snapshot: VERIFIED（contains_mock=false）" if prov.get("historical_snapshot")
              else "证据来源 / 类型齐备"),
        warn_count=p_warn))

    # 3) No Mock Data：特征与 eligible 证据中不得出现 is_mock=True。
    m_checked = len(feats) + len(elig) + len(rej)
    m_fail = sum(1 for f in feats if bool(f.get("is_mock"))) + \
             sum(1 for e in elig if bool(e.get("is_mock")))
    m_mock_rejected = sum(1 for r in rej if bool(r.get("is_mock")))
    items.append(_audit_item(
        "NO_MOCK", "No Mock Data", m_checked, m_fail,
        note=(f"{m_mock_rejected} 条 MOCK 被 Time Gate 隔离（不进决策路径），符合预期" if m_mock_rejected
              else "决策路径无 MOCK（特征 + eligible 证据全部 is_mock=False）")))

    # 4) Case As-of：相似案例必须 case_available_at <= 决策时点，且存在可解析的决策时点。
    cases = list(dec.get("top_cases") or [])
    dt = None
    if target_date:
        try:
            from agent.case_library.policy import decision_time_for  # noqa: PLC0415
            dt = decision_time_for(target_date)
        except Exception:  # noqa: BLE001
            dt = None
    c_fail, c_notes = 0, []
    dts = _ts(dt)
    for c in cases:
        ca = c.get("case_available_at") or c.get("case_created_at") or ""
        if not str(ca).strip():
            c_fail += 1
            c_notes.append(f"case={c.get('case_id')} 缺 case_available_at（保守判失败）")
            continue
        if dts is not None:
            ca_ts = _ts(ca)
            if ca_ts is None or ca_ts > dts:
                c_fail += 1
                c_notes.append(f"case={c.get('case_id')} 晚于决策时点 {dt}")
    items.append(_audit_item(
        "CASE_AS_OF", "Case As-of", len(cases), c_fail,
        note="；".join(c_notes[:4]) or (f"{len(cases)} 条相似案例均 case_available_at <= {dt}"
                                        if cases and dt else "无相似案例命中（无穿越风险）")))

    # 5) Outcome Leakage：未 reveal 时不得在任何区块出现 actual_*（post_trade 除外）。
    pt = dec.get("post_trade") or {}
    revealed = bool(dec.get("outcome_revealed", False))
    leak = False
    if revealed:
        o_note = "outcome 已按流程揭晓（post_trade 含 actual_*，合法）"
    else:
        if pt.get("status") != "OUTCOME_NOT_REVEALED":
            leak = True
        for section_name in ("model_output", "evidence", "top_cases", "risk_gate",
                             "rule_engine", "top_features", "context"):
            sec = dec.get(section_name)
            if isinstance(sec, dict):
                if any("actual_" in str(k) for k in sec):
                    leak = True
                    break
            elif isinstance(sec, list):
                if any(isinstance(x, dict) and "actual_" in str(k) for x in sec for k in x):
                    leak = True
                    break
        o_note = ("未 reveal：任何区块未出现 actual_*（无泄漏）" if not leak
                  else "检测到 actual_* 出现在未揭晓决策区块（严重泄漏）")
    items.append(_audit_item("OUTCOME_LEAKAGE", "Outcome Leakage", 1, 1 if leak else 0, note=o_note))

    # 按固定顺序输出
    order = {k: i for i, k in enumerate(_RUNTIME_AUDIT_ORDER)}
    items.sort(key=lambda it: order.get(str(it.get("key")), 999))
    return {"generated_at": _now_iso(), "items": items,
            "overall": _overall_from_items(items)}


def _normalize_runtime_audit(rt: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """把服务端给出的 runtime audit 规约为前端标准结构（缺字段用真实计算兜底）。

    兼容两种服务端结构：
      A) audit.runtime.items = [{key/id, name/label, status, checked, failed, note}]
      B) audit.check_list = [{check_id, label, status, checked_count, failed_count, reason}]
        （或 audit.checks = {check_id: {...}}，Agent B 的 DecisionSnapshot 结构）
    """
    if not isinstance(rt, dict):
        return fallback
    raw_items = rt.get("items")
    if raw_items is None:
        raw_items = rt.get("check_list")
    if raw_items is None:
        checks = rt.get("checks")
        raw_items = list(checks.values()) if isinstance(checks, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        return fallback
    items: List[Dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        status = str(it.get("status", "")).upper()
        if status not in ("PASS", "FAIL", "WARNING"):
            continue
        items.append({
            "key": str(it.get("check_id") or it.get("key") or it.get("id") or "ITEM"),
            "name": str(it.get("label") or it.get("name") or it.get("check_id")
                       or it.get("key") or "审计项"),
            "status": status,
            "checked": int(it.get("checked_count", it.get("checked", it.get("total", 0))) or 0),
            "failed": int(it.get("failed_count", it.get("failed", it.get("failures", 0))) or 0),
            "note": str(it.get("reason") or it.get("note") or it.get("detail") or ""),
        })
    if not items:
        return fallback
    overall = str(rt.get("overall", "")).upper()
    if overall not in ("PASS", "FAIL", "WARNING"):
        overall = _overall_from_items(items)
    return {"generated_at": str(rt.get("generated_at") or _now_iso()),
            "items": items, "overall": overall}


def _runtime_audit(dec: Dict[str, Any]) -> Dict[str, Any]:
    """返回决策对象的 Runtime Audit（优先消费服务端真实审计，缺则按真实数据计算）。

    消费优先级：audit.runtime（显式容器）> audit.check_list / audit.checks
    （DecisionService run_runtime_audit 的直接产物）> 本地 _compute_runtime_audit。
    OVERALL 一律由真实结果推导，页面绝不写死 PASS。
    """
    audit = dec.get("audit") or {}
    fallback = _compute_runtime_audit(dec)
    rt = audit.get("runtime")
    if isinstance(rt, dict):
        norm = _normalize_runtime_audit(rt, fallback)
        if norm is not fallback:
            return norm
    if isinstance(audit.get("check_list"), list) or isinstance(audit.get("checks"), dict):
        norm = _normalize_runtime_audit(audit, fallback)
        if norm is not fallback:
            return norm
    return fallback


def _decision_warnings(dec: Dict[str, Any]) -> List[Dict[str, str]]:
    """对成功返回的决策补充结构化业务警告（ERROR CODE + message + suggested_action）。"""
    warnings: List[Dict[str, str]] = []
    mo = dec.get("model_output") or {}
    if mo.get("expected_return") is None:
        cat = ERROR_CATALOG["NO_PREDICTION"]
        warnings.append({"code": cat["code"], "message": cat["message"],
                         "suggested_action": cat["action"]})
    ev = dec.get("evidence") or {}
    # V0.3.1.3：仅 FULL/LIVE 模式"无证据"才提示源不可用（网络失败诚实降级）；
    # DEMO HISTORICAL_SNAPSHOT 无匹配快照、offline NONE 属用户选择，不误报。
    emode = str(dec.get("context", {}).get("evidence_mode", "")).upper()
    if emode == "LIVE" and not ev.get("eligible") and not ev.get("rejected"):
        cat = ERROR_CATALOG["EVIDENCE_SOURCE_UNAVAILABLE"]
        warnings.append({"code": cat["code"], "message": cat["message"],
                         "suggested_action": cat["action"]})
    return warnings


def _prepare_decision(dec: Dict[str, Any], evidence_mode: str = "real") -> Dict[str, Any]:
    """给前端返回的决策对象：剔除内部字段 + 补充展示元数据 + Runtime Audit。

    V0.3.1.3：context.evidence_mode 以 DecisionService（Adapter）算出的
    HISTORICAL_SNAPSHOT / LIVE / NONE 为准（Web 参数 real/offline 只用于选 adapter），
    此处**不覆盖**。
    """
    dec = dict(dec)
    dec.pop("_post_inputs", None)
    if dec.get("context"):
        dec["context"] = dict(dec["context"])
        # 保留 service 计算的 context.evidence_mode（Adapter 填写），不覆盖
        dec["context"]["evidence_mode"] = dec["context"].get("evidence_mode", evidence_mode)
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
    # Runtime Audit（V0.3.1.1）：消费服务端真实审计 / 按真实数据计算；OVERALL 绝不写死 PASS。
    audit = dec.get("audit") or {}
    audit["runtime"] = _runtime_audit(dec)
    audit["overall"] = audit["runtime"]["overall"]
    dec["audit"] = audit
    dec["warnings"] = _decision_warnings(dec)
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
        "status": "degraded",
        "degraded": True,
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
    llm = copilot_status()
    llm_connected = bool(llm.get("configured")) and not bool(llm.get("degraded"))
    return jsonify({
        "app": "CAISO Trading Decision Agent · Web MVP",
        "web_version": MVP_LABEL,
        "alpha_label": ALPHA_LABEL,
        "cutoff_desc": DECISION_CUTOFF_DESC,
        "data_mode": _data_mode(),
        "data_mode_meaning": (
            "DEMO：真实历史最小切片（非 MOCK，可真实推荐）；actual_* 仅 Reveal 后经 service/tool 可访问"
            if _data_mode() == MODE_DEMO
            else "FULL：完整数据"
        ),
        "nodes": {k: v for k, v in NODE_REGION.items()},
        "node_short": {k: k.replace("_1_N001", "") for k in NODE_REGION},
        "min_decision_date": _DD_MIN,
        "max_decision_date": _DD_MAX,
        "golden_cases": GOLDEN_CASES,
        "llm": llm,
        "default_evidence": DEFAULT_EVIDENCE,
        "evidence_modes": [
            {"id": "real", "label": "真实证据（DEMO=历史 GFS 快照 HISTORICAL_SNAPSHOT / FULL=实时 GFS LIVE）"},
            {"id": "offline", "label": "离线静态 NONE（不取外部证据，纯本地演示）"},
        ],
        "evidence_mode_default": ("HISTORICAL_SNAPSHOT" if _data_mode() == MODE_DEMO else "LIVE"),
        # MVP Status（V0.3.1.1 诚实状态栏，不误导业务方）
        "mvp_status": {
            "model_alpha": ALPHA_LABEL,
            "profitability_verified": "NO",
            "data_mode": _data_mode(),
            "llm": "CONNECTED" if llm_connected else "NOT CONFIGURED",
            "llm_provider": llm.get("provider", ""),
            "llm_model": llm.get("model", ""),
            "auto_trading": "DISABLED",
            "settlement": "SIMPLIFIED SIGNAL BACKTEST",
        },
        "trade_semantics": {
            "cutoff": "10:00 PT",
            "BUY": "RTPD − DA",
            "SELL": "DA − RTPD",
            "NO_TRADE": "0",
        },
        "versions": {
            # V0.3.1.3：不暴露固定 PRE/POST 值误导用户；单笔页面一律展示
            # DecisionSnapshot.context.market_rule_version（date-aware）。
            "market_rule": "MARKET_RULE_VERSIONING_ENABLED（date-aware，单笔见决策上下文）",
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
    if not dd or not node:
        return _error("INVALID_REQUEST", ERROR_CATALOG["INVALID_REQUEST"]["message"],
                      ERROR_CATALOG["INVALID_REQUEST"]["action"], 400)
    if not (1 <= hour <= 24):
        return _error("INVALID_HOUR", ERROR_CATALOG["INVALID_HOUR"]["message"],
                      ERROR_CATALOG["INVALID_HOUR"]["action"], 400)
    if node not in NODE_REGION:
        return _error("UNSUPPORTED_NODE",
                      f"未知节点 {node!r}（可用: {', '.join(sorted(NODE_REGION))}）。",
                      ERROR_CATALOG["UNSUPPORTED_NODE"]["action"], 400)
    if not (_DD_MIN <= dd <= _DD_MAX):
        return _error("UNSUPPORTED_DATE",
                      f"决策日期 {dd} 超出支持范围 {_DD_MIN} ~ {_DD_MAX}（test 窗口）。",
                      ERROR_CATALOG["UNSUPPORTED_DATE"]["action"], 400)
    try:
        svc = service(evidence)
    except Exception as exc:
        _log_exception(f"DecisionService 初始化失败（evidence={evidence}）")
        return _error("MISSING_ARTIFACT", ERROR_CATALOG["MISSING_ARTIFACT"]["message"],
                      ERROR_CATALOG["MISSING_ARTIFACT"]["action"], 500, detail=_safe_detail(exc))
    try:
        dec = svc.run_decision(dd, node, hour, reveal=False)
    except ValueError as exc:
        cls = _classify_error(exc)
        return _error(cls["code"], cls["message"], cls["action"], 400, detail=_safe_detail(exc))
    except Exception as exc:
        _log_exception(f"决策运行异常: {dd} {node} H{hour}")
        cls = _classify_error(exc)
        return _error(cls["code"], cls["message"], cls["action"], 500, detail=_safe_detail(exc))
    prep = _prepare_decision(dec, evidence)
    return jsonify({"status": "ok", "decision": prep, "warnings": prep.get("warnings", [])})


@app.get("/api/decision/<decision_id>")
def api_get_decision(decision_id: str):
    svc = _find_service(decision_id)
    if svc is None:
        return _error("NOT_FOUND", f"决策 {decision_id!r} 不存在（先运行一次决策）。",
                      ERROR_CATALOG["NOT_FOUND"]["action"], 404)
    dec = svc._decisions.get(decision_id)  # noqa: SLF001
    ev_key = _service_evidence_key(decision_id)
    return jsonify({"status": "ok", "decision": _prepare_decision(dec, ev_key)})


@app.post("/api/decision/<decision_id>/lock")
def api_lock_decision(decision_id: str):
    svc = _find_service(decision_id)
    if svc is None:
        return _error("NOT_FOUND", f"决策 {decision_id!r} 不存在（先运行一次决策）。",
                      ERROR_CATALOG["NOT_FOUND"]["action"], 404)
    # Service 层锁定（DecisionSnapshot.locked，Outcome Access Control 前置门槛）：
    # Web 的 _LOCKS 只记录界面锁状态；快照的 locked 由 DecisionService.lock_decision 设置，
    # 否则 reveal_decision 会以 NOT_LOCKED 拒绝（不穿越）。
    try:
        svc.lock_decision(decision_id)
    except Exception as exc:
        _log_exception(f"lock_decision 失败: {decision_id}")
        return _error("INTERNAL_ERROR", ERROR_CATALOG["INTERNAL_ERROR"]["message"],
                      ERROR_CATALOG["INTERNAL_ERROR"]["action"], 500, detail=_safe_detail(exc))
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
        return _error("NOT_FOUND", f"决策 {decision_id!r} 不存在（先运行一次决策）。",
                      ERROR_CATALOG["NOT_FOUND"]["action"], 404)
    lk = _lock_state(decision_id)
    if not lk["locked"]:
        return jsonify({"status": "NOT_LOCKED",
                        "error": {"code": "NOT_LOCKED",
                                  "message": "必须先 LOCK DECISION 才能揭晓 Actual Outcome（锁定前禁止显示实际结果）。",
                                  "suggested_action": "请先点击 LOCK DECISION，再执行 REVEAL。"}}), 403
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
        return _error("INVALID_REQUEST", "缺少问题（question 必填）。",
                      "请在 Ask Trading Agent 输入框输入问题后重试。", 400)
    # 若传入 decision_id，先校验存在
    if decision_id and _find_service(decision_id) is None:
        return _error("NOT_FOUND", f"决策 {decision_id!r} 不存在（先运行一次决策）。",
                      ERROR_CATALOG["NOT_FOUND"]["action"], 404)
    result = ask_copilot(question, decision_id)
    # LLM 不可用：保持既有降级契约（status=degraded / degraded=True），同时附结构化 ERROR CODE。
    degraded = result.get("llm_status") in ("NOT_CONFIGURED", "ERROR") \
        or result.get("status") in ("degraded", "blocked") \
        or not result.get("configured", True)
    if degraded:
        cat = ERROR_CATALOG["LLM_UNAVAILABLE"]
        result["error"] = {"code": cat["code"], "message": cat["message"],
                           "suggested_action": cat["action"]}
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
        return _error("INVALID_REQUEST", "缺少 decision_date（必填）。",
                      "请先选择 Decision Date 再生成简报。", 400)
    if node and node not in NODE_REGION:
        return _error("UNSUPPORTED_NODE", f"未知节点 {node}（可用: {', '.join(sorted(NODE_REGION))}）。",
                      ERROR_CATALOG["UNSUPPORTED_NODE"]["action"], 400)

    if all_hours:
        # 全量扫描：确保该日期 × 节点范围的全部 24 小时已生成决策（缺则运行）
        try:
            svc = service(evidence)
        except Exception as exc:
            _log_exception(f"DecisionService 初始化失败（brief, evidence={evidence}）")
            return _error("MISSING_ARTIFACT", ERROR_CATALOG["MISSING_ARTIFACT"]["message"],
                          ERROR_CATALOG["MISSING_ARTIFACT"]["action"], 500, detail=_safe_detail(exc))
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
# 全局错误处理（V0.3.1.1）：任何未捕获异常都不抛 traceback 给业务人员。
#  - /api/* 路由 → 结构化 JSON 错误（ERROR CODE + message + suggested action）。
#  - 页面路由 → 可读的错误页。
# traceback 一律只写日志。
# ---------------------------------------------------------------------------
def _render_page_error(http_status: int, title: str, message: str) -> Any:
    html = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
        "<title>%s · CAISO Trading Decision Agent</title>"
        "<link rel='stylesheet' href='/static/mvp.css'></head><body><div class='page'>"
        "<h1>%s</h1><div class='notice'><span class='tag'>ERROR</span><p>%s</p></div>"
        "<div class='back'><a href='/'>← 返回 Decision Workspace</a></div>"
        "</div></body></html>" % (esc_html(title), esc_html(title), esc_html(message))
    )
    return html, http_status


def esc_html(s: Any) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@app.errorhandler(404)
def _http_not_found(e):  # noqa: ANN001
    if request.path.startswith("/api/"):
        return _error("NOT_FOUND", f"API 路径不存在：{request.path}",
                      ERROR_CATALOG["NOT_FOUND"]["action"], 404)
    return _render_page_error(404, "页面不存在", "您访问的页面不存在或已移动，请返回首页继续。")


@app.errorhandler(405)
def _http_method_not_allowed(e):  # noqa: ANN001
    if request.path.startswith("/api/"):
        return _error("INVALID_REQUEST", f"该 API 不支持 {request.method} 方法。",
                      "请使用正确的 HTTP 方法重试。", 405)
    return _render_page_error(405, "请求方法不允许", "该地址不支持当前访问方式，请返回首页。")


@app.errorhandler(Exception)
def _unhandled_exception(e):  # noqa: ANN001
    _log_exception(f"未处理异常: {request.method} {request.path}")
    if request.path.startswith("/api/"):
        cls = _classify_error(e)
        return _error(cls["code"], cls["message"], cls["action"], 500, detail=_safe_detail(e))
    return _render_page_error(500, "系统内部错误",
                              "系统处理请求时遇到内部错误。请稍后重试；若持续出现，请联系工程师并提供日志。")


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
    llm_st = copilot_status()
    llm_ok = bool(llm_st.get("configured")) and not bool(llm_st.get("degraded"))
    print("=" * 78)
    print("  CAISO Trading Decision Agent · Web MVP")
    print(f"  URL  : http://{args.host}:{args.port}")
    print(f"  LLM  : {'CONNECTED' if llm_ok else 'NOT CONFIGURED (Ask 面板将诚实提示)'}")
    print(f"  MVP STATUS : MODEL ALPHA={ALPHA_LABEL} · PROFITABILITY VERIFIED=NO · "
          f"DATA MODE={_data_mode()} · AUTO TRADING=DISABLED · SETTLEMENT=SIMPLIFIED SIGNAL BACKTEST")
    print(f"  证据 : 默认 {'offline(不取外部)' if args.offline else 'real(实时 GFS，失败诚实降级)'}；页面内可切换")
    print(f"  数据 : decision_date {_DD_MIN} ~ {_DD_MAX}（test 窗口）")
    print("  约束 : LOCK 前不展示 actual；不造假证据；冻结交易核心；错误不抛 traceback 给业务方")
    print("=" * 78)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
