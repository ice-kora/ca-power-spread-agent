# -*- coding: utf-8 -*-
"""
agent/case_library/init_cases.py

从 stage3 真实极端事件初始化 Case Library。

数据源（严格 as-of，均为 test 窗口 2026-06-02 ~ 2026-08-05 真实事件）：
  - code/data/stage3/top_loss_events.csv   合并 Top50 亏损事件（worst 模型逐笔）
  - code/data/stage3/top_profit_events.csv 合并 Top50 盈利事件
  - code/data/canonical.parquet            用于取 decision_date（D-1）

选样口径（至少 15 个，这里选 18 个，覆盖三类必须类型）：
  1) 最大亏损：worst_pnl 最深的数笔（含全样本最大单笔 -2251）；
  2) 大额盈利：pnl 最大的数笔（含全样本最大单笔 +2216）；
  3) 高置信但方向错误：worst_conf>=0.9 且亏损>800 的样本（含 rule 高置信 SELL 亏损）；
  另含：ELCA 低样本事件、Type C 残余尾部、同一 (node,date,hour) 多模型对侧的事件。

输出：agent/case_library/cases.json
说明：Case Library ≠ Rule Engine。这里只记录"历史发生了什么 + 教训"，不产出规则。

运行：python agent/case_library/init_cases.py   （或任何 cwd，路径自动解析）
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

# 保证从任意 cwd 运行都能 import agent.*
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from agent.case_library.case import Case  # noqa: E402
from agent.case_library.policy import settlement_available_at  # noqa: E402
from agent.evidence.fetcher import attach_uncertain_evidence  # noqa: E402

DATA_DIR = REPO_ROOT / "code" / "data"
STAGE3_DIR = DATA_DIR / "stage3"
OUT_PATH = REPO_ROOT / "agent" / "case_library" / "cases.json"

# 需要写入"决策时点可见信息"的特征子集（as-of，不含任何目标日实际值）
AS_OF_FEATURE_KEYS = [
    # 滞后价差
    "spread_lag1", "spread_lag2", "spread_lag7",
    # 滚动统计
    "spread_mean7", "spread_mean14", "spread_mean30",
    "spread_std7", "spread_std14", "spread_std30",
    "spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
    "da_day_mean_lag1", "rtpd_day_mean_lag1",
    # 负荷/天气
    "load_2da_forecast", "load_peak_flag", "t2m_lag1", "ssrd_lag1",
    "wind100_lag1", "peer_spread_lag1",
    # 日历
    "dow", "month",
    # 历史分布/风险（如 CSV 存在）
    "hist_n", "hist_p90", "hist_p95", "hist_p99", "hist_p995",
    "hist_min", "hist_max", "cvar95", "cvar99",
    "vol7", "vol30", "vol_ratio", "extreme_count_30", "max_abs_30",
    "lag1_pct", "realized_pct", "node_bias_mean", "node_vol7",
    "node_max_abs_prev7",
    # 分析元信息
    "agreement", "type", "type_reason",
]

# profit CSV 有而 loss CSV 没有的 as-of 特征
PROFIT_EXTRA_AS_OF_KEYS = [
    "ret_z_hist", "ret_pct_hist", "spread_lag1_pct_node",
    "da_lag1", "rtpd_lag1", "da_lag2", "rtpd_lag2", "da_lag7", "rtpd_lag7",
    "spread_day_mean_lag1", "load_actual_lag1", "load_actual_day_mean_lag1",
    "peer_da_lag1", "peer_rtpd_lag1", "is_holiday", "solar_flag", "zone",
]


# ---------------------------------------------------------------------------
# 选样（显式 key，避免依赖排序位置，抗 CSV 变动）
# ---------------------------------------------------------------------------

LOSS_SELECT: List[tuple] = [
    # (node, target_date, hour) —— 亏损事件
    ("CONTROLX_1_N001", "2026-07-17", 3),   # 全样本最大单笔 -2251（RTPD -2300）
    ("CONTROLX_1_N001", "2026-07-09", 2),   # -2216（RTPD -2365 记录深）
    ("CONTROLX_1_N001", "2026-07-07", 18),  # -1196
    ("CONTROLX_1_N001", "2026-06-30", 14),  # rule SELL 亏损 -1175（DA 崩塌）
    ("ELCAJNGT_7_N001", "2026-07-24", 20),  # ELCA RTPD 尖峰 -914（低样本）
    ("CONTROLX_1_N001", "2026-06-15", 8),   # Type C 残余尾部 -1002
    ("CONTROLX_1_N001", "2026-07-08", 21),  # 高置信 BUY(0.958) 错方向 -1076
    ("CONTROLX_1_N001", "2026-06-26", 5),   # 高置信 BUY(0.969) 错方向 -1039
    ("CONTROLX_1_N001", "2026-06-17", 16),  # 高置信 BUY(0.985) 错方向 -896
    ("CONTROLX_1_N001", "2026-06-30", 8),   # rule 高置信 SELL(0.940) 亏损 -894
    ("CONTROLX_1_N001", "2026-06-15", 10),  # Type C 残余尾部 -934
    ("CONTROLX_1_N001", "2026-07-07", 20),  # -1181（与 07-07 H18 同行情）
]

PROFIT_SELECT: List[tuple] = [
    # (node, target_date, hour) —— 盈利事件
    ("CONTROLX_1_N001", "2026-07-09", 2),   # 全样本最大单笔 +2216（rule SELL）
    ("CONTROLX_1_N001", "2026-06-30", 19),  # BUY +1305（DA -1295 崩盘）
    ("CONTROLX_1_N001", "2026-06-30", 14),  # BUY +1175（同一行 rule SELL 亏 -1175）
    ("CONTROLX_1_N001", "2026-06-23", 4),   # BUY +1076（DA -1052）
    ("CONTROLX_1_N001", "2026-06-20", 9),   # rule SELL +952（RTPD -1102）
    ("CONTROLX_1_N001", "2026-06-19", 24),  # rule SELL +942（RTPD -1091）
]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _f(row: pd.Series, key: str):
    """取数值特征，NaN 转 None（JSON 友好）。"""
    v = row.get(key, None)
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _s(row: pd.Series, key: str, default: str = "") -> str:
    v = row.get(key, None)
    return default if v is None or (isinstance(v, float) and v != v) else str(v)


def _row_with_keys(row: pd.Series, node: str, td: str, hour: int) -> pd.Series:
    """把 set_index 后成为索引的 node/target_date/hour 重新放回列，便于按列名读取。"""
    r = row.copy()
    r["node"] = node
    r["target_date"] = td
    r["hour"] = hour
    return r


def _json_clean(value: Any) -> Any:
    """把 numpy 标量转成原生 Python 类型，保证 json.dump 可序列化。"""
    if isinstance(value, (pd.Timestamp,)):
        return str(value)
    if hasattr(value, "item"):  # np.int64 / np.float64 / np.bool_ 等
        try:
            return value.item()
        except (TypeError, ValueError):
            return value
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_clean(v) for k, v in value.items()}
    return value


def _as_of_snapshot(row: pd.Series, extra_keys: List[str]) -> Dict[str, Any]:
    """从事件行抽取决策时点可见的信息快照（只读，不判方向）。"""
    snap: Dict[str, Any] = {}
    for k in AS_OF_FEATURE_KEYS + extra_keys:
        if k in row.index:
            v = row[k]
            if isinstance(v, float) and v != v:
                continue  # 跳过 NaN
            snap[k] = _json_clean(v)
    return snap


def _region_of(node: str) -> str:
    if "CONTROLX" in node or "SNLNDRO" in node:
        return "ZP26"
    if "ELCA" in node:
        return "SP15"
    return "UNKNOWN"


def _gate_note(row: pd.Series) -> str:
    """根据决策时点可见信息，说明该笔若应用 Risk Gate 会如何处理（引用式，不判方向）。"""
    node = _s(row, "node")
    action = _s(row, "action")
    agreement = _s(row, "agreement")
    hist_n = _f(row, "hist_n")
    notes = []
    if "CONTROLX" in node and action == "BUY":
        notes.append("会被 R7a REJECT（BUY_ON_POSITIVE_DRIFT_NODE）")
    if "ELCA" in node and hist_n is not None and hist_n < 200:
        notes.append("会被 R6 REJECT（LOW_SAMPLE_SUPPORT）")
    if agreement == "conflict":
        notes.append("会被 V0.1 R2 标记 MODEL_DISAGREEMENT（V0.2 已删除该三模型一致性投票残留，见 docs/DecisionPipeline.md §4）")
    if "CONTROLX" in node and action == "SELL":
        notes.append("R4 已验证无法事前识别 CONTROLX SELL 的 DA 崩塌尾（gate 不覆盖）")
    if not notes:
        notes.append("Risk Gate 对该笔无明确拦截（PASS 或 PASS_WITH_WARNING）")
    return "；".join(notes)


def _loss_why(row: pd.Series) -> str:
    """亏损机制解释（来源：top_loss_event_analysis.md 逐笔分析）。"""
    node = _s(row, "node")
    action = _s(row, "action")
    actual_da = _f(row, "actual_da")
    actual_rtpd = _f(row, "actual_rtpd")
    actual_return = _f(row, "actual_return")
    etype = _s(row, "type")
    why = ""
    if "ELCA" in node:
        why = (
            f"ELCA 晚间 RTPD 尖峰 {actual_rtpd:+.1f}（气电稀缺），Return 转负 "
            f"{actual_return:+.1f}；决策时 spread 已处于历史低位/样本仅 "
            f"{_f(row,'hist_n')}（低样本 cold-start）。"
        )
    elif "CONTROLX" in node and action == "SELL":
        why = (
            f"rule 押 Return>0 做 SELL_DA（expected_return 大正），但 DA 暴跌至 "
            f"{actual_da:+.1f}（负电价），Return 转负 {actual_return:+.1f}，SELL 巨亏。"
            f"机制：DA 单日负电价崩塌。"
        )
    else:  # CONTROLX BUY
        why = (
            f"模型押 Return<0 做 BUY_DA，但 RTPD 深跌至 {actual_rtpd:+.1f}"
            f"（负电价），Return 大幅转正 {actual_return:+.1f}，BUY 押反方向，单笔巨亏。"
            f"机制：深夜/清晨可再生能源过剩 → RTPD 深度负电价。"
        )
    if etype == "C":
        why += " 属 Type C（RESIDUAL_TAIL_RISK）：无具体事前信号，已知肥尾上的随机落点。"
    elif etype == "A":
        why += " 属 Type A（PRE-TRADE DETECTABLE）：事前内部数据已有明确危险信号。"
    return why


def _profit_why(row: pd.Series) -> str:
    """盈利机制解释（来源：top_profit_event_analysis.md）。"""
    action = _s(row, "action")
    actual_da = _f(row, "actual_da")
    actual_rtpd = _f(row, "actual_rtpd")
    actual_return = _f(row, "actual_return")
    if action == "SELL":
        return (
            f"DA 贴负地板 {actual_da:+.1f} 而 RTPD 深跌至 {actual_rtpd:+.1f}，"
            f"Return 大正 {actual_return:+.1f}，SELL_DA 得利。本质：负电价行情右侧，"
            f"RTPD 比 DA 更深地贴地板。"
        )
    return (
        f"DA 单日崩盘至 {actual_da:+.1f} 而 RTPD 保持 {actual_rtpd:+.1f}，"
        f"Return 大负 {actual_return:+.1f}，BUY_DA 得利。本质：DA 崩盘日单日事件。"
    )


def _loss_lessons(row: pd.Series) -> List[str]:
    node = _s(row, "node")
    action = _s(row, "action")
    lessons = []
    if "CONTROLX" in node and action == "BUY":
        lessons.append(
            "CONTROLX BUY 逆 +84 漂移，train+val 无条件负期望（R7a 拦截对象）"
        )
        lessons.append(
            "confidence>=0.9 在 CONTROLX 上与 PnL 反相关（R1 置信度门已删除）"
        )
        lessons.append(
            "深夜/清晨负电价 regime 下 RTPD 可深跌至 -1000~-2365，单笔尾部达千级"
        )
    elif "CONTROLX" in node and action == "SELL":
        lessons.append(
            "CONTROLX SELL 存在 DA 崩塌尾（如 06-30 DA -1313），Risk Gate 不覆盖"
        )
        lessons.append(
            "模型冲突（rule SELL vs ML BUY）是事前可见的硬信号（Type A）"
        )
    if "ELCA" in node:
        lessons.append("ELCA 历史样本 n<200，cold-start，统计不可靠（R6）")
    if _s(row, "type") == "C":
        lessons.append(
            "已知肥尾节点上的残余随机尾部，无具体事前信号；需尾部/CVaR 监控兜底"
        )
    return lessons


def _profit_lessons(row: pd.Series) -> List[str]:
    return [
        "收益 99% 来自 |return|>500 的极端行，彩票式抓尾部，不可复现",
        "盈利与亏损是同一肥尾分布的两侧：同一批 node/hour 既大赚又大亏",
        "高 confidence 是'行情持续'的机械产物，不是事件识别力",
    ]


def _related_rules(row: pd.Series, source: str) -> List[str]:
    node = _s(row, "node")
    action = _s(row, "action")
    rules = []
    if _s(row, "type") == "A":
        rules.append("Type A (PRE-TRADE DETECTABLE)")
    elif _s(row, "type") == "C":
        rules.append("Type C (RESIDUAL_TAIL_RISK)")
    if _s(row, "agreement") == "conflict":
        rules.append("R2(V0.1 已删除): MODEL_DISAGREEMENT（三模型一致性投票残留，历史参考）")
    if "CONTROLX" in node and action == "BUY":
        rules.append("R7a: CONTROLX BUY → REJECT")
    if "ELCA" in node:
        rules.append("R6: LOW_SAMPLE_SUPPORT")
    if source == "profit":
        rules.append("Profit 分析：彩票式尾部收益（不可外推）")
    return rules


def _human_decision(row: pd.Series, pnl: float, source: str) -> str:
    action = _s(row, "action")
    if pnl < 0:
        return (
            f"回测记录：按 {action} 执行，未做拦截，实际亏损 {pnl:+.1f} $/MWh。"
            f"若应用 Risk Gate：{_gate_note(row)}。"
        )
    return (
        f"回测记录：按 {action} 执行并盈利 {pnl:+.1f} $/MWh。"
        f"但该盈利依赖极端尾部行情，属不可复现的彩票结构，不构成可外推的预测能力。"
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_cases() -> List[Case]:
    loss = pd.read_csv(STAGE3_DIR / "top_loss_events.csv", encoding="utf-8-sig")
    profit = pd.read_csv(STAGE3_DIR / "top_profit_events.csv", encoding="utf-8-sig")
    canonical = pd.read_parquet(DATA_DIR / "canonical.parquet")

    # decision_date 查找表：loss/profit CSV 只有 target_date，用 canonical 对齐 D-1
    dd_lookup = {
        (r["node"], str(r["target_date"])[:10], int(r["hour"])): str(r["decision_date"])[:10]
        for _, r in canonical[["node", "target_date", "decision_date", "hour"]].iterrows()
    }

    cases: List[Case] = []
    seen: set = set()
    seq = 1

    # 1) 亏损事件
    loss_idx = loss.set_index(["node", "target_date", "hour"])
    for key in LOSS_SELECT:
        node, td, hour = key
        if key not in loss_idx.index:
            print(f"[skip-loss] 缺失 key: {key}")
            continue
        row = loss_idx.loc[key]
        if isinstance(row, pd.DataFrame):  # 理论不会重复，防御
            row = row.iloc[0]
        row = _row_with_keys(row, node, td, int(hour))
        dedupe_key = (node, td, hour, "loss")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        pnl = _f(row, "worst_pnl")
        _avail = settlement_available_at(td)  # case_available_at（目标日结算后）
        cases.append(Case(
            case_id=f"CASE-{seq:04d}",
            decision_date=dd_lookup.get(key, str((date.fromisoformat(td) - timedelta(days=1)))),
            node=node,
            hour=int(hour),
            model_prediction=_s(row, "action"),
            expected_return=_f(row, "worst_er") or 0.0,
            confidence=_f(row, "worst_conf") or 0.0,
            available_information_at_decision_time=_as_of_snapshot(row, []),
            event_evidence=attach_uncertain_evidence(node, dd_lookup.get(key, "")),
            human_decision=_human_decision(row, pnl, source="loss"),
            actual_DA=_f(row, "actual_da"),
            actual_RTPD=_f(row, "actual_rtpd"),
            actual_Return=_f(row, "actual_return"),
            PnL=pnl,
            why_correct_or_wrong=_loss_why(row),
            lessons=_loss_lessons(row),
            related_rules=_related_rules(row, source="loss"),
            case_created_at=_avail,
            case_available_at=_avail,
            review_completed_at="",
        ))
        seq += 1

    # 2) 盈利事件
    profit_idx = profit.set_index(["node", "target_date", "hour"])
    for key in PROFIT_SELECT:
        node, td, hour = key
        if key not in profit_idx.index:
            print(f"[skip-profit] 缺失 key: {key}")
            continue
        row = profit_idx.loc[key]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        row = _row_with_keys(row, node, td, int(hour))
        dedupe_key = (node, td, hour, "profit")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        pnl = _f(row, "pnl")
        _avail = settlement_available_at(td)
        cases.append(Case(
            case_id=f"CASE-{seq:04d}",
            decision_date=dd_lookup.get(key, str((date.fromisoformat(td) - timedelta(days=1)))),
            node=node,
            hour=int(hour),
            model_prediction=_s(row, "action"),
            expected_return=_f(row, "expected_return") or 0.0,
            confidence=_f(row, "confidence") or 0.0,
            available_information_at_decision_time=_as_of_snapshot(row, PROFIT_EXTRA_AS_OF_KEYS),
            event_evidence=attach_uncertain_evidence(node, dd_lookup.get(key, "")),
            human_decision=_human_decision(row, pnl, source="profit"),
            actual_DA=_f(row, "actual_da"),
            actual_RTPD=_f(row, "actual_rtpd"),
            actual_Return=_f(row, "actual_return"),
            PnL=pnl,
            why_correct_or_wrong=_profit_why(row),
            lessons=_profit_lessons(row),
            related_rules=_related_rules(row, source="profit"),
            case_created_at=_avail,
            case_available_at=_avail,
            review_completed_at="",
        ))
        seq += 1

    return cases


def main() -> None:
    cases = build_cases()
    payload = {
        "meta": {
            "module": "agent/case_library",
            "version": "0.1",
            "description": (
                "Case Library：历史极端事件档案（检索用），≠ Rule Engine。"
                "全部为 test 窗口 2026-06-02~08-05 真实事件；decision_date=target_date-1。"
                "Event evidence 全部 UNCERTAIN（当前无真实外部数据源）。"
            ),
            "source_files": [
                "code/data/stage3/top_loss_events.csv",
                "code/data/stage3/top_profit_events.csv",
                "code/data/canonical.parquet",
            ],
            "case_count": len(cases),
            "note": "LLM 仅做整理/解释，未做任何方向判断。",
        },
        "cases": [c.to_dict() for c in cases],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"cases.json written: {OUT_PATH}")
    print(f"case count: {len(cases)}")
    print("---")
    for c in cases:
        print(c.short_summary())


if __name__ == "__main__":
    main()
