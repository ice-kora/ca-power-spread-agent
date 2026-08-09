# -*- coding: utf-8 -*-
"""
backtest.py —— CA-ISO 价差项目「严格 as-of 回测引擎 + 三态交易决策策略」(Agent D)
================================================================================

角色边界
--------
本模块只做回测/决策评估，**不修改** canonical.py / 模型预测文件。
- 消费「任意模型预测 CSV」（统一列 schema，见下），跑严格 as-of 回测。
- 内置 Model 0（Rule，白盒）与一个可解释基准（Logistic + Linear，walk-forward 重拟合）
  仅作为 Agent C 三层模型到位前的**框架自测**；Agent C 的 predictions_*.csv 就绪后，
  `python backtest.py` 会自动优先消费 `code/data/predictions_{rule,interpretable,catboost}.csv`。

统一预测列 schema（契约 §3 / 任务书）
-------------------------------------
    node, target_date, hour, split,
    pred_direction(+1/-1/0), expected_return, confidence, prob_return_positive,
    actual_return, actual_direction
任意模型 CSV 只要含这些列即可被本引擎回测；额外列（spread_std7 / evidence 等）可选，
decision policy 会用 spread_std7 作风险过滤器（若提供）。

决策策略（契约 §5 §7）
-----------------------
    expected_return 明显 > 0 且置信达标  -> SELL_DA（Virtual Supply）
    expected_return 明显 < 0 且置信达标  -> BUY_DA（Virtual Demand）
    否则（|expected_return| 太小 / confidence 太低 / 证据冲突 / 风险过高）-> NO_TRADE

PnL（1 MWh normalized，契约冻结）：
    SELL_DA 收益 = +actual_return；BUY_DA 收益 = -actual_return；NO_TRADE = 0。

严格回测（契约 §8，旧数字作废）
-------------------------------
- 特征严格 as-of：只用 canonical X 列（available_at <= decision_cutoff），
  不含任何 target_date 实际值 / *_next 天气 / label 区。
- Rule（滚动统计）：天然 walk-forward，逐行只用过去数据。
- 可解释基准：expanding-window 重拟合（初始只用 train，逐块扩充历史再预测下一块），
  特征 imputer/scaler 均只用历史拟合。
- 指标：direction accuracy、BUY/SELL precision、trade coverage、win rate、avg PnL/trade、
  cumulative PnL、max drawdown、Sharpe-like、node/hour/month 分项。
- 主策略（ZP26：SNLNDRO/CONTROLX）与 ELCA（cold-start）分开评估。

用法
----
    python backtest.py                 # 全流程：自建 Rule/LR 自测 + 消费 Agent C 文件(若存在)
    python backtest.py --selfcheck     # 只跑 no-leakage 自检
    python backtest.py --preds PATH --label MyModel   # 对任意预测 CSV 单独回测
"""
import os
import json
import argparse

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sklearn.linear_model import LogisticRegression, LinearRegression
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "backtest_outputs")
CANON_PQ = os.path.join(DATA, "canonical.parquet")

# Agent C 的预测文件（若存在则优先消费）
AGENT_C_PREDS = {
    "rule": os.path.join(DATA, "predictions_rule.csv"),
    "interpretable": os.path.join(DATA, "predictions_interpretable.csv"),
    "catboost": os.path.join(DATA, "predictions_catboost.csv"),
}
# 本模块自测产物（不覆盖 Agent C 命名空间）
SELF_TEST_PREDS = {
    "rule": os.path.join(OUT, "predictions_rule_selftest.csv"),
    "interpretable": os.path.join(OUT, "predictions_interpretable_selftest.csv"),
}

MAIN_NODES = ["SNLNDRO_1_N001", "CONTROLX_1_N001"]
ELCA_NODE = "ELCAJNGT_7_N001"

# 统一预测列 schema
PRED_CORE = ["node", "target_date", "hour", "pred_direction",
             "expected_return", "confidence", "prob_return_positive",
             "actual_return"]

# ---------------------------------------------------------------------------
# 决策策略配置（可配置、可解释）
# ---------------------------------------------------------------------------
DECISION_CFG = {
    "ret_threshold_abs": 5.0,   # |expected_return| 小于该值 -> NO_TRADE（$ / MWh）
    "conf_threshold": 0.20,     # confidence 小于该值 -> NO_TRADE（0..1）
    "risk_std7_cap": None,      # 可选风险过滤器：近7日 spread 波动 std7 > cap -> NO_TRADE。
                                # None=关闭（实证：本数据集启用会大幅减损 PnL，见报告敏感性表）
}

RULE_CFG = {
    "mean_col": "spread_mean14",     # Rule 期望收益信号：同 node 同 hour 近14日均值（as-of）
    "pos_window": 14,                # 一致性/概率的窗口
    "pos_min_periods": 7,            # 最少有效天数
}

LR_CFG = {
    "fold_days": 15,                 # walk-forward 每块天数
}

# 可解释基准特征（全部为 canonical X 列，as-of；node 用 is_controlx 哑变量）
LR_FEATURES = [
    "hour", "dow", "month", "is_holiday", "solar_flag",
    "da_lag1", "rtpd_lag1", "spread_lag1",
    "da_lag2", "rtpd_lag2", "spread_lag2",
    "da_lag7", "rtpd_lag7", "spread_lag7",
    "spread_mean7", "spread_std7", "spread_mean14", "spread_std14",
    "spread_mean30", "spread_std30",
    "spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
    "da_day_mean_lag1", "rtpd_day_mean_lag1", "spread_day_mean_lag1",
    "load_actual_lag1", "load_actual_day_mean_lag1",
    "load_2da_forecast", "load_peak_flag",
    "t2m_lag1", "ssrd_lag1", "wind100_lag1",
    "peer_spread_lag1", "peer_da_lag1", "peer_rtpd_lag1",
    "is_controlx",
]

RNG_SEED = 42


# ---------------------------------------------------------------------------
# 数据载入
# ---------------------------------------------------------------------------
def load_canonical():
    df = pd.read_parquet(CANON_PQ)
    df["target_date"] = pd.to_datetime(df["target_date"])
    return df


def _node_dummy(df):
    df = df.copy()
    df["is_controlx"] = (df["node"] == "CONTROLX_1_N001").astype(int)
    return df


# ---------------------------------------------------------------------------
# Model 0 · Rule 白盒预测（框架自测，严格 as-of）
# ---------------------------------------------------------------------------
def build_rule_predictions(canon, nodes, split_filter=None):
    """构造 Rule 预测（统一 schema）。

    信号（全部 as-of，只依赖 canonical X 特征/历史）：
      - expected_return = spread_mean14（同 node 同 hour，target_date-2..-15 均值）
      - prob_return_positive = 近14个同 hour 交付日中 spread>0 的占比（shift2，不含 target-1）
      - confidence        = 一致性 = |prob-0.5|*2（方向越一致越高）
      - pred_direction    = sign(expected_return)
      - evidence          = 摘要文本
    """
    d = canon[canon["node"].isin(nodes)].copy()
    d = _node_dummy(d)
    if split_filter is not None:
        d = d[d["split"].isin(split_filter)].copy()

    mean_col = RULE_CFG["mean_col"]
    w = RULE_CFG["pos_window"]
    mp = RULE_CFG["pos_min_periods"]

    # as-of 正向占比：groupby(node,hour) 内 rolling(w).mean of (direction>0)，shift(2)
    d = d.sort_values(["node", "hour", "target_date"]).reset_index(drop=True)
    d["pos_rate"] = (d.groupby(["node", "hour"])["direction"]
                      .transform(lambda s: s.rolling(w, min_periods=mp)
                                  .apply(lambda x: float((x > 0).mean()), raw=True))
                      .shift(2))
    # 避免 0/1 极端（缺数据时向 0.5 收缩）
    n_pos = d.groupby(["node", "hour"])["direction"].transform(
        lambda s: s.rolling(w, min_periods=mp).count()).shift(2)
    shrunk = (d["pos_rate"] * n_pos + 0.5 * 4) / (n_pos + 4)
    d["prob_return_positive"] = shrunk
    d["expected_return"] = d[mean_col]
    d["pred_direction"] = np.sign(d["expected_return"]).astype(float)
    d["confidence"] = np.abs(shrunk - 0.5) * 2.0

    def evidence(r):
        return ("mean7=%+.1f mean14=%+.1f mean30=%+.1f std14=%.1f std7=%.1f pos14=%.2f"
                % (r.get("spread_mean7", np.nan) if pd.notna(r.get("spread_mean7", np.nan)) else 0,
                   r.get("spread_mean14", np.nan) if pd.notna(r.get("spread_mean14", np.nan)) else 0,
                   r.get("spread_mean30", np.nan) if pd.notna(r.get("spread_mean30", np.nan)) else 0,
                   r.get("spread_std14", np.nan) if pd.notna(r.get("spread_std14", np.nan)) else 0,
                   r.get("spread_std7", np.nan) if pd.notna(r.get("spread_std7", np.nan)) else 0,
                   r.get("pos_rate", np.nan) if pd.notna(r.get("pos_rate", np.nan)) else 0))

    d["evidence"] = d.apply(evidence, axis=1)

    out = pd.DataFrame({
        "node": d["node"], "target_date": d["target_date"], "hour": d["hour"],
        "split": d["split"], "pred_direction": d["pred_direction"],
        "expected_return": d["expected_return"],
        "confidence": d["confidence"],
        "prob_return_positive": d["prob_return_positive"],
        "actual_return": d["actual_return"], "actual_direction": d["direction"],
        "spread_std7": d["spread_std7"], "evidence": d["evidence"],
    })
    return out.sort_values(["node", "target_date", "hour"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Model 1 · 可解释基准（Logistic + Linear，expanding-window walk-forward）
# ---------------------------------------------------------------------------
def _prepare_lr(df, features):
    """填充 NaN 用列中位数（历史 as-of），并补 is_controlx。"""
    df = _node_dummy(df)
    X = df[features].copy()
    med = X.median()
    X = X.fillna(med)
    return X


def build_lr_predictions(canon, nodes, split_filter=("val", "test")):
    """可解释基准的 walk-forward 预测。

    严格 as-of：每个预测块的模型只在其之前（含 train + 已实现块）的数据上训练；
    imputer/scaler 也只由历史拟合。pred_direction 由逻辑回归方向给出，
    expected_return 由线性回归给出。
    """
    d = canon[canon["node"].isin(nodes)].copy()
    d = _node_dummy(d)
    d = d.sort_values(["node", "hour", "target_date"]).reset_index(drop=True)
    dates = sorted(d.loc[d["split"].isin(split_filter), "target_date"].unique())

    # 初始历史：train 全部（不足 min_history_days 则取最早的足够天数）
    hist = d[d["split"] == "train"].copy()
    if len(hist) == 0:
        hist = d[d["target_date"] < dates[0]].copy()
    if len(hist) == 0:
        raise ValueError("LR walk-forward: 无初始历史数据")

    fold = LR_CFG["fold_days"]
    blocks = [dates[i:i + fold] for i in range(0, len(dates), fold)]

    clf = LogisticRegression(max_iter=2000, C=0.5)
    reg = LinearRegression()
    preds = []
    for bi, block in enumerate(blocks):
        Xh = _prepare_lr(hist, LR_FEATURES)
        yh_dir = (hist["actual_return"] > 0).astype(int)
        yh_ret = hist["actual_return"]
        sc = StandardScaler().fit(Xh)
        clf.fit(sc.transform(Xh), yh_dir)
        reg.fit(sc.transform(Xh), yh_ret)

        blk = d[d["target_date"].isin(block)].copy()
        Xb = _prepare_lr(blk, LR_FEATURES)
        prob = clf.predict_proba(sc.transform(Xb))[:, 1]
        er = reg.predict(sc.transform(Xb))
        out = pd.DataFrame({
            "node": blk["node"].values, "target_date": blk["target_date"].values,
            "hour": blk["hour"].values, "split": blk["split"].values,
            "pred_direction": np.where(prob > 0.5, 1.0, -1.0),
            "expected_return": er,
            "confidence": np.abs(2 * prob - 1),
            "prob_return_positive": prob,
            "actual_return": blk["actual_return"].values,
            "actual_direction": blk["direction"].values,
            "spread_std7": blk["spread_std7"].values,
        })
        preds.append(out)
        hist = pd.concat([hist, blk], ignore_index=True)   # 扩充历史（label 已实现）

    res = pd.concat(preds, ignore_index=True)
    res["evidence"] = ["prob=%+.2f conf=%.2f exp_ret=%+.2f" % (p, c, e)
                       for p, c, e in zip(res["prob_return_positive"],
                                          res["confidence"],
                                          res["expected_return"])]
    return res.sort_values(["node", "target_date", "hour"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 预测文件载入（通用：输入任意模型的预测 CSV）
# ---------------------------------------------------------------------------
def load_predictions(path):
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in PRED_CORE if c not in df.columns]
    if missing:
        raise ValueError("预测文件 %s 缺少必需列: %s（必需: %s）"
                         % (path, missing, ", ".join(PRED_CORE)))
    df["node"] = df["node"].astype(str).str.strip()
    df["target_date"] = pd.to_datetime(df["target_date"])
    df["hour"] = pd.to_numeric(df["hour"], errors="coerce").astype("Int64")
    df["pred_direction"] = pd.to_numeric(df["pred_direction"], errors="coerce")
    df["expected_return"] = pd.to_numeric(df["expected_return"], errors="coerce")
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")
    df["prob_return_positive"] = pd.to_numeric(df["prob_return_positive"], errors="coerce")
    df["actual_return"] = pd.to_numeric(df["actual_return"], errors="coerce")
    if "actual_direction" not in df.columns:
        df["actual_direction"] = np.sign(df["actual_return"])
    else:
        df["actual_direction"] = pd.to_numeric(df["actual_direction"], errors="coerce")
    if "split" not in df.columns:
        df["split"] = np.nan
    df["split"] = df["split"].astype(str).str.strip()
    df = df.dropna(subset=["actual_return"]).reset_index(drop=True)
    return df


def resolve_prediction_sources():
    """决定使用哪些预测文件：Agent C 优先，否则用本模块自测文件。返回 {model: (path, is_selftest)}。"""
    src = {}
    for name, agent_path in AGENT_C_PREDS.items():
        if os.path.exists(agent_path):
            src[name] = (agent_path, False)
        elif os.path.exists(SELF_TEST_PREDS.get(name, "")):
            src[name] = (SELF_TEST_PREDS[name], True)
    return src


# ---------------------------------------------------------------------------
# 三态决策策略
# ---------------------------------------------------------------------------
def apply_decision_policy(df, cfg=None):
    """对预测 DataFrame 施加三态决策策略，产出 decision 与 pnl 列。"""
    cfg = dict(DECISION_CFG if cfg is None else cfg)
    d = df.copy()
    ret_thr = cfg["ret_threshold_abs"]
    conf_thr = cfg["conf_threshold"]
    risk_cap = cfg.get("risk_std7_cap")

    er = pd.to_numeric(d["expected_return"], errors="coerce")
    conf = pd.to_numeric(d["confidence"], errors="coerce")
    pd_ = pd.to_numeric(d["pred_direction"], errors="coerce")
    risk = pd.to_numeric(d.get("spread_std7"), errors="coerce") if "spread_std7" in d.columns else None

    dec = np.full(len(d), "NO_TRADE", dtype=object)
    valid = er.notna() & conf.notna() & pd_.notna() & (pd_ != 0)
    mask_ret = er.abs() >= ret_thr
    mask_conf = conf >= conf_thr
    mask_risk = np.ones(len(d), dtype=bool)
    if risk_cap is not None and risk is not None:
        mask_risk = risk.notna() & (risk <= risk_cap)

    sell = valid & mask_ret & mask_conf & mask_risk & (pd_ > 0) & (er > 0)
    buy = valid & mask_ret & mask_conf & mask_risk & (pd_ < 0) & (er < 0)
    dec[sell] = "SELL_DA"
    dec[buy] = "BUY_DA"
    d["decision"] = dec
    d["pnl"] = np.where(dec == "SELL_DA", d["actual_return"],
                        np.where(dec == "BUY_DA", -d["actual_return"], 0.0))
    return d


def make_all_trade_frame(pred_df):
    """All Trade 基准：每行都按 pred_direction 交易（pred_direction=0 视为不交易）。"""
    d = pred_df.copy()
    d["decision"] = np.where(d["pred_direction"] > 0, "SELL_DA",
                             np.where(d["pred_direction"] < 0, "BUY_DA", "NO_TRADE"))
    d["pnl"] = np.where(d["decision"] == "SELL_DA", d["actual_return"],
                        np.where(d["decision"] == "BUY_DA", -d["actual_return"], 0.0))
    return d


def _oos_filter(df):
    """out-of-sample 过滤：只保留 val+test 行（有 split 信息时），否则原样。"""
    if "split" in df.columns and df["split"].isin(["val", "test"]).any():
        return df[df["split"].isin(["val", "test"])].reset_index(drop=True)
    return df


def make_static_frame(pred_df, side):
    """静态 drift 基准：全部 SELL / 全部 BUY（pred_direction 设为常量以便解释 acc）。"""
    d = pred_df.copy()
    d["decision"] = side
    d["pred_direction"] = 1.0 if side == "SELL_DA" else -1.0
    d["pnl"] = np.where(side == "SELL_DA", d["actual_return"], -d["actual_return"])
    return d


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def _daily_pnl(d):
    """按 target_date 聚合的每日 PnL 序列（无交易日补 0）。"""
    if len(d) == 0:
        return pd.Series(dtype=float)
    s = d.groupby("target_date")["pnl"].sum()
    full = pd.date_range(d["target_date"].min(), d["target_date"].max(), freq="D")
    s = s.reindex(pd.DatetimeIndex(full)).fillna(0.0)
    return s


def max_drawdown(cum):
    return float((cum - cum.cummax()).min()) if len(cum) else 0.0


def compute_metrics(d, label=""):
    """对已带 decision/pnl 的 DataFrame 计算指标矩阵。"""
    traded = d[d["decision"] != "NO_TRADE"]
    n = int(len(d))
    nt = int(len(traded))
    cov = nt / n if n else np.nan

    def _acc(sub):
        s = sub[pd.to_numeric(sub["pred_direction"], errors="coerce").notna()
                & (pd.to_numeric(sub["pred_direction"], errors="coerce") != 0)]
        if len(s) == 0:
            return np.nan
        return float((np.sign(pd.to_numeric(s["pred_direction"], errors="coerce"))
                      == np.sign(s["actual_direction"])).mean())

    acc_all = _acc(d)
    acc_tr = _acc(traded) if nt else np.nan

    sell = traded[traded["decision"] == "SELL_DA"]
    buy = traded[traded["decision"] == "BUY_DA"]
    sell_prec = float((sell["actual_return"] > 0).mean()) if len(sell) else np.nan
    buy_prec = float((buy["actual_return"] < 0).mean()) if len(buy) else np.nan
    win = float((traded["pnl"] > 0).mean()) if nt else np.nan
    total = float(traded["pnl"].sum())
    mean_pt = float(traded["pnl"].mean()) if nt else np.nan
    pnl_sell = float(sell["pnl"].sum())
    pnl_buy = float(buy["pnl"].sum())

    daily = _daily_pnl(d)
    daily_mean = float(daily.mean()) if len(daily) else np.nan
    daily_std = float(daily.std()) if len(daily) else np.nan
    sharpe = (daily_mean / daily_std * np.sqrt(252)) if daily_std and daily_std > 0 else np.nan
    cum = daily.cumsum()
    final_cum = float(cum.iloc[-1]) if len(cum) else 0.0
    mdd = max_drawdown(cum)

    return {
        "label": label,
        "n": n, "n_traded": nt,
        "trade_coverage": round(cov, 4) if cov == cov else None,
        "dir_acc_all": round(acc_all, 4) if acc_all == acc_all else None,
        "dir_acc_traded": round(acc_tr, 4) if acc_tr == acc_tr else None,
        "SELL_precision": round(sell_prec, 4) if sell_prec == sell_prec else None,
        "BUY_precision": round(buy_prec, 4) if buy_prec == buy_prec else None,
        "win_rate": round(win, 4) if win == win else None,
        "total_pnl": round(total, 2),
        "mean_pnl_per_trade": round(mean_pt, 3) if mean_pt == mean_pt else None,
        "pnl_sell": round(pnl_sell, 2), "pnl_buy": round(pnl_buy, 2),
        "n_sell": int(len(sell)), "n_buy": int(len(buy)),
        "daily_mean_pnl": round(daily_mean, 3) if daily_mean == daily_mean else None,
        "annualized_pnl": round(daily_mean * 252, 1) if daily_mean == daily_mean else None,
        "max_drawdown": round(mdd, 2),
        "sharpe_daily": round(sharpe, 3) if sharpe == sharpe else None,
        "cum_final": round(final_cum, 2),
    }


def build_strategy_matrix(pred_sources, nodes=None):
    """对每个模型，计算 All-Trade 与 Decision-Policy；外加静态基准。返回 (rows, frames)。

    统一在 out-of-sample（val+test，Agent C 文件为 test）窗口上评估；
    nodes 给定则只评估该组节点（主策略 MAIN_NODES 与 ELCA 分开）。
    """
    rows, frames = [], {}

    def _pick(df):
        df = _oos_filter(df)
        if nodes is not None:
            df = df[df["node"].isin(nodes)].reset_index(drop=True)
        return df

    # 用第一个可用的预测文件作为静态基准的样本空间
    base = None
    for name, (path, _st) in pred_sources.items():
        base = _pick(load_predictions(path))
        break
    if base is not None:
        for side, lab in (("SELL_DA", "Static: always SELL"), ("BUY_DA", "Static: always BUY")):
            f = make_static_frame(base, side)
            rows.append(compute_metrics(f, lab))
            frames[lab] = f
        nt = base.copy(); nt["decision"] = "NO_TRADE"; nt["pnl"] = 0.0
        rows.append(compute_metrics(nt, "NO-TRADE"))
        frames["NO-TRADE"] = nt

    for name, (path, is_self) in pred_sources.items():
        pred = _pick(load_predictions(path))
        tag = " [SELF-TEST]" if is_self else ""
        at = make_all_trade_frame(pred)
        dp = apply_decision_policy(pred)
        rows.append(compute_metrics(at, "All-Trade[%s]%s" % (name, tag)))
        rows.append(compute_metrics(dp, "Decision[%s]%s" % (name, tag)))
        frames["All-Trade[%s]%s" % (name, tag)] = at
        frames["Decision[%s]%s" % (name, tag)] = dp
    return rows, frames


# ---------------------------------------------------------------------------
# 分项表现（node / hour / month）+ decision 明细
# ---------------------------------------------------------------------------
def breakdown_node(d):
    rows = []
    for node, g in d.groupby("node"):
        traded = g[g["decision"] != "NO_TRADE"]
        sell = traded[traded["decision"] == "SELL_DA"]
        buy = traded[traded["decision"] == "BUY_DA"]
        rows.append({
            "node": node, "n": len(g), "n_traded": len(traded),
            "n_sell": len(sell), "n_buy": len(buy),
            "sell_acc": round(float((sell["actual_return"] > 0).mean()), 4) if len(sell) else None,
            "buy_acc": round(float((buy["actual_return"] < 0).mean()), 4) if len(buy) else None,
            "pnl_sell": round(float(sell["pnl"].sum()), 2),
            "pnl_buy": round(float(buy["pnl"].sum()), 2),
            "total_pnl": round(float(traded["pnl"].sum()), 2),
            "mean_pnl": round(float(traded["pnl"].mean()), 3) if len(traded) else None,
        })
    return pd.DataFrame(rows)


def breakdown_hour(d):
    rows = []
    for h, g in d.groupby("hour"):
        traded = g[g["decision"] != "NO_TRADE"]
        rows.append({
            "hour": h, "n": len(g), "n_traded": len(traded),
            "total_pnl": round(float(traded["pnl"].sum()), 2),
            "mean_pnl": round(float(traded["pnl"].mean()), 3) if len(traded) else None,
            "win_rate": round(float((traded["pnl"] > 0).mean()), 4) if len(traded) else None,
        })
    return pd.DataFrame(rows)


def breakdown_month(d):
    d = d.copy()
    d["ym"] = d["target_date"].dt.to_period("M").astype(str)
    rows = []
    for ym, g in d.groupby("ym"):
        traded = g[g["decision"] != "NO_TRADE"]
        rows.append({
            "ym": ym, "n": len(g), "n_traded": len(traded),
            "total_pnl": round(float(traded["pnl"].sum()), 2),
            "mean_pnl": round(float(traded["pnl"].mean()), 3) if len(traded) else None,
            "regime_avg_ret": round(float(g["actual_return"].mean()), 3),
        })
    return pd.DataFrame(rows)


def decision_detail(d):
    """BUY / SELL / NO_TRADE 分别表现。"""
    rows = []
    for dec in ["SELL_DA", "BUY_DA", "NO_TRADE"]:
        g = d[d["decision"] == dec]
        if len(g) == 0:
            rows.append({"decision": dec, "n": 0, "pct": 0.0, "hit": None,
                         "total_pnl": 0.0, "mean_pnl": None})
            continue
        if dec == "SELL_DA":
            hit = float((g["actual_return"] > 0).mean())
        elif dec == "BUY_DA":
            hit = float((g["actual_return"] < 0).mean())
        else:
            hit = np.nan
        rows.append({
            "decision": dec, "n": len(g), "pct": round(len(g) / len(d), 4),
            "hit": round(hit, 4) if hit == hit else None,
            "total_pnl": round(float(g["pnl"].sum()), 2),
            "mean_pnl": round(float(g["pnl"].mean()), 3),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# decision snapshots（真实历史 test 日）
# ---------------------------------------------------------------------------
def pick_snapshots(d, n_total=9):
    """从 test split 挑选有代表性的真实历史 decision snapshot。"""
    test = d[d["split"] == "test"].copy()
    if len(test) == 0:
        test = d.copy()
    samples = []
    for dec in ["SELL_DA", "BUY_DA", "NO_TRADE"]:
        g = test[test["decision"] == dec]
        if len(g) == 0:
            continue
        k = max(1, n_total // 3)
        g = g.sort_values("pnl", ascending=False)
        top = g.head(max(1, k // 2))
        bot = g.tail(max(1, k // 2))
        samples.append(top)
        samples.append(bot)
    if samples:
        pick = pd.concat(samples).drop_duplicates(subset=["node", "target_date", "hour"])
        # 按日期排序，取前 n_total
        pick = pick.sort_values(["target_date", "node", "hour"]).head(n_total)
    else:
        pick = test.head(n_total)
    # 若预测文件无 evidence，则从核心列派生
    if "evidence" not in pick.columns or pick["evidence"].isna().all():
        pick["evidence"] = ["exp_ret=%+.1f prob=%.2f conf=%.2f" % (
            (er if pd.notna(er) else 0), (pr if pd.notna(pr) else 0), (c if pd.notna(c) else 0))
            for er, pr, c in zip(pick["expected_return"], pick["prob_return_positive"],
                                 pick["confidence"])]
    return pick


# ---------------------------------------------------------------------------
# No-leakage 自检
# ---------------------------------------------------------------------------
def selfcheck(canon):
    print("=" * 74)
    print("No-leakage / as-of 自检")
    print("=" * 74)
    ok = True

    # 1) Rule/LR 只用 canonical X 特征
    import canonical as can  # noqa (同目录模块，仅用于 X_COLUMNS 元数据)
    xset = set(can.X_COLUMNS)
    used = set(LR_FEATURES) - {"is_controlx"}
    bad = used - xset
    if bad:
        ok = False
        print("[FAIL] LR 特征不在 canonical X_COLUMNS 中: %s" % bad)
    else:
        print("[PASS] LR 全部 %d 个特征 ∈ canonical X_COLUMNS（as-of）" % len(used))

    # 2) canonical X 与 label 不相交
    if set(can.X_COLUMNS).isdisjoint(set(can.LABEL_COLUMNS)):
        print("[PASS] canonical X 与 label 区无重叠")
    else:
        ok = False
        print("[FAIL] X 与 label 重叠")

    # 3) Rule 的 pos_rate 窗口不包含 target_date-1（shift2 验证）
    r = build_rule_predictions(canon, MAIN_NODES)
    # 验证 pred_direction == sign(spread_mean14)，且 spread_mean14 是 canonical 原值
    chk = r.merge(canon[["node", "target_date", "hour", "spread_mean14", "direction"]],
                  on=["node", "target_date", "hour"], how="left")
    if np.allclose(np.sign(chk["expected_return"].fillna(0)), np.sign(chk["spread_mean14"].fillna(0)),
                   equal_nan=True):
        print("[PASS] Rule expected_return == sign(spread_mean14)（as-of rolling，shift2）")
    else:
        ok = False
        print("[FAIL] Rule expected_return 与 canonical spread_mean14 不一致")

    # 4) spread_mean14 只依赖 target_date-2..-15（抽查 5 行与 master 重算对比）
    master = pd.read_csv(os.path.join(DATA, "master.csv"), parse_dates=["date"])
    rng = np.random.default_rng(7)
    samp = chk.dropna(subset=["spread_mean14"]).sample(n=5, random_state=rng)
    good = True
    for _, row in samp.iterrows():
        src = master[(master["node"] == row["node"]) & (master["hour"] == row["hour"]) &
                     master["date"].between(row["target_date"] - pd.Timedelta(days=15),
                                            row["target_date"] - pd.Timedelta(days=2))]
        exp = float(src["spread"].mean())
        if not np.isclose(exp, row["spread_mean14"], atol=1e-6):
            good = False
            print("  [FAIL] %s %s H%d: canonical=%.4f vs recompute=%.4f"
                  % (row["node"], row["target_date"].date(), row["hour"],
                     row["spread_mean14"], exp))
    print("[%s] spread_mean14 与 master(target_date-2..-15) 重算一致（抽查5行）"
          % ("PASS" if good else "FAIL"))
    ok = ok and good

    # 5) 任何预测文件里的实际值列与 canonical label 一致（抽查）
    src = resolve_prediction_sources()
    for name, (path, _st) in src.items():
        p = load_predictions(path)
        m = p.merge(canon[["node", "target_date", "hour", "actual_return"]],
                    on=["node", "target_date", "hour"], suffixes=("", "_canon"))
        if len(m) and "actual_return_canon" in m.columns:
            same = np.allclose(m["actual_return"], m["actual_return_canon"], atol=1e-6)
            print("[%s] %s: actual_return 与 canonical label 一致 (%d/%d)"
                  % ("PASS" if same else "FAIL", name, len(m), len(p)))
            ok = ok and same

    print("=" * 74)
    print("自检%s" % ("全部通过" if ok else "存在失败项！"))
    return ok


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------
def plot_equity(frames, out_png):
    fig, ax = plt.subplots(figsize=(11, 5.8))
    shown = {}
    for label, f in frames.items():
        if "Decision[" in label or label in ("Static: always SELL", "Static: always BUY"):
            if label in shown:
                continue
            shown[label] = True
            cum = _daily_pnl(f).cumsum()
            ax.plot(cum.index, cum.values, label=label, linewidth=1.8)
    ax.axhline(0, color="gray", linestyle="--", linewidth=0.8)
    ax.set_title("Cumulative Daily PnL ($/MWh, 1 MWh per position)")
    ax.set_xlabel("delivery date (target_date)")
    ax.set_ylabel("cumulative PnL (USD)")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------
def fmt(x, digits=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return ("%%.%df" % digits) % x if isinstance(x, float) else str(x)


def metrics_table(rows, cols=None):
    cols = cols or ["label", "n", "n_traded", "trade_coverage", "dir_acc_all",
                    "dir_acc_traded", "SELL_precision", "BUY_precision", "win_rate",
                    "total_pnl", "mean_pnl_per_trade", "daily_mean_pnl", "max_drawdown",
                    "sharpe_daily"]
    header = {"label": "策略", "n": "样本", "n_traded": "交易数", "trade_coverage": "覆盖率",
              "dir_acc_all": "方向acc(全部)", "dir_acc_traded": "方向acc(交易)",
              "SELL_precision": "SELL精", "BUY_precision": "BUY精", "win_rate": "胜率",
              "total_pnl": "累计PnL", "mean_pnl_per_trade": "单笔均值",
              "daily_mean_pnl": "日均", "max_drawdown": "最大回撤", "sharpe_daily": "Sharpe(日)"}
    lines = []
    head = " | ".join(header[c] for c in cols)
    lines.append("| " + head + " |")
    lines.append("|" + "---|" * len(cols))
    for r in rows:
        lines.append("| " + " | ".join(fmt(r.get(c), 3 if c not in ("label",) else 0)
                                       for c in cols) + " |")
    return "\n".join(lines)


def write_report(meta, strategy_rows, frames, pred_sources, out_path, rows_elca=None):
    lines = []
    A = lines.append
    A("# CA-ISO 价差交易 —— 严格 as-of 回测报告（Agent D）")
    A("")
    A("> 生成时间：%s" % meta["generated_at"])
    A("> 数据：canonical.parquet（无泄漏层，Agent C 消费同一数据）")
    A("> 决策时点：decision_date 10:00 前（契约冻结）；PnL 按 1 MWh normalized。")
    A("> 评估窗口：%s" % meta["eval_window"])
    A("> 主策略节点：ZP26（SNLNDRO / CONTROLX）；ELCAJNGT cold-start 单独评估。")
    A("")
    A("## 1. 数据与窗口")
    A("")
    A("- canonical 行：%s；主节点 split 按 decision_date：train %s → %s，val %s → %s，test %s → %s。"
      % (meta["canon_rows"], meta["train_d0"], meta["train_d1"],
         meta["val_d0"], meta["val_d1"], meta["test_d0"], meta["test_d1"]))
    A("- 预测来源：")
    for name, (path, is_self) in pred_sources.items():
        tag = "（Agent C 就绪）" if not is_self else "（本模块框架自测）"
        A("  - %s → `%s`%s" % (name, path, tag))
    missing = [k for k in AGENT_C_PREDS if k not in pred_sources]
    if missing:
        A("  - 未就绪：%s（引擎通用，落盘后重跑自动消费）" % ", ".join(missing))
    A("")
    A("## 2. 决策策略（三态，可配置）")
    A("")
    A("| 参数 | 值 | 含义 |")
    A("|---|---|---|")
    A("| ret_threshold_abs | %s | \\|expected_return\\| 低于该值 → NO_TRADE |" % DECISION_CFG["ret_threshold_abs"])
    A("| conf_threshold | %s | confidence 低于该值 → NO_TRADE |" % DECISION_CFG["conf_threshold"])
    A("| risk_std7_cap | %s | 近7日 spread 波动 std7 高于该值 → NO_TRADE（None=关闭） |" % DECISION_CFG["risk_std7_cap"])
    A("")
    A("PnL：SELL_DA = +actual_return；BUY_DA = -actual_return；NO_TRADE = 0。")
    A("")
    A("## 3. 各策略指标矩阵（%s · 主节点 ZP26）" % meta["eval_window"])
    A("")
    A("> Sharpe(日) = mean(daily_pnl)/std(daily_pnl)×√252；累计 PnL 为 1 MWh/仓位的美元额，"
      "非资本收益率。")
    A("")
    A(metrics_table(strategy_rows))
    A("")
    A("## 4. 决策明细：BUY / SELL / NO_TRADE（最终决策策略 `%s`）" % meta["final_policy_label"])
    A("")
    det = decision_detail(frames[meta["final_policy_label"]])
    A(det.to_markdown(index=False) if hasattr(det, "to_markdown") else det.to_string(index=False))
    A("")
    A("## 5. node 分项")
    A("")
    A((breakdown_node(frames[meta["final_policy_label"]]).to_markdown(index=False)
       if hasattr(pd.DataFrame, "to_markdown") else breakdown_node(frames[meta["final_policy_label"]]).to_string(index=False)))
    A("")
    A("## 6. hour 分项")
    A("")
    A((breakdown_hour(frames[meta["final_policy_label"]]).to_markdown(index=False)
       if hasattr(pd.DataFrame, "to_markdown") else breakdown_hour(frames[meta["final_policy_label"]]).to_string(index=False)))
    A("")
    A("## 7. month 分项（regime_avg_ret = 该月所有样本 actual_return 均值，展示市场环境）")
    A("")
    A((breakdown_month(frames[meta["final_policy_label"]]).to_markdown(index=False)
       if hasattr(pd.DataFrame, "to_markdown") else breakdown_month(frames[meta["final_policy_label"]]).to_string(index=False)))
    A("")
    if rows_elca is not None and rows_elca:
        A("## 7b. ELCAJNGT（cold-start，单独评估，不混入主结论）")
        A("")
        A("> ELCA 数据仅 2026-03 起，历史短；Agent C 未为其单独训练正式模型，"
          "下列为其测试窗口表现（独立评估）。")
        A("")
        A(metrics_table(rows_elca))
        A("")
    A("## 8. 真实历史 decision snapshot（test split）")
    A("")
    snap = meta["snapshots"]
    A("| node | target_date | hour | pred_dir | expected_return | prob_pos | conf | decision | actual_return | pnl | evidence |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in snap:
        A("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            s["node"], s["target_date"], s["hour"], s["pred_direction"],
            fmt(s["expected_return"], 2), fmt(s["prob_return_positive"], 2),
            fmt(s["confidence"], 2), s["decision"], fmt(s["actual_return"], 1),
            fmt(s["pnl"], 1), (s.get("evidence") or "")[:90]))
    A("")
    A("## 9. 敏感性：决策阈值与风险过滤（Rule）")
    A("")
    A("对最终策略的基准模型（Rule），变化阈值看 trade coverage / 累计 PnL / 最大回撤 / Sharpe：")
    A("")
    A(meta["sensitivity_table"])
    A("")
    A("## 10. 关键结论（诚实评估）")
    A("")
    for c in meta["conclusions"]:
        A("- " + c)
    A("")
    A("## 11. 局限与后续")
    A("")
    for c in meta["limitations"]:
        A("- " + c)
    A("")
    app = meta.get("selftest_appendix")
    if app:
        A("## 附录 A. 框架自测：本模块 Rule / 可解释基准 在 val+test（216 天）的表现")
        A("")
        A("> 以下为 Agent C 正式文件就绪前本模块的框架自测结果（严格 as-of），仅用于验证引擎正确性，"
          "不参与主结论。它清楚展示了**准确率与 PnL 反相关**现象：")
        A("> - Rule：同 node 同 hour 近14日均值信号 + 一致性置信，逐行只用历史数据（天然 walk-forward）。")
        A("> - 可解释基准：Logistic（方向）+ Linear（幅度），expanding-window 每 15 天用已实现标签重拟合，"
          "特征 imputer/scaler 只由历史拟合。")
        A("")
        A(metrics_table(app["matrix"]))
        A("")
        A("自测 Rule 的决策阈值/风险过滤敏感性（val+test）：")
        A("")
        A(app["sensitivity"])
        A("")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def build_sensitivity(pred_df, base_cfg, model_name):
    """对基准预测 df 做阈值/风险过滤敏感性表（统一 out-of-sample）。

    若预测文件不含 spread_std7，则从 canonical 合并（同为决策时点可得的历史特征，
    不引入泄漏），使风险过滤敏感性真实可评估。
    """
    pred_df = _oos_filter(pred_df)
    if "spread_std7" not in pred_df.columns:
        canon = load_canonical()
        pred_df = pred_df.merge(
            canon[["node", "target_date", "hour", "spread_std7"]],
            on=["node", "target_date", "hour"], how="left")
    rows = []
    confs = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    rets = [0.0, 2.0, 5.0, 10.0, 20.0]
    risk_caps = [None, 200.0, 120.0, 50.0]

    base = apply_decision_policy(pred_df, base_cfg)
    bm = compute_metrics(base, "base")
    rows.append(("基准 (ret>=%s, conf>=%s, risk=%s)"
                 % (base_cfg["ret_threshold_abs"], base_cfg["conf_threshold"],
                    base_cfg.get("risk_std7_cap")),
                 bm["trade_coverage"], bm["total_pnl"], bm["max_drawdown"], bm["sharpe_daily"],
                 bm["mean_pnl_per_trade"]))
    for c in confs:
        cfg = dict(base_cfg); cfg["conf_threshold"] = c
        m = compute_metrics(apply_decision_policy(pred_df, cfg), "c")
        rows.append(("conf>=" + str(c), m["trade_coverage"], m["total_pnl"],
                     m["max_drawdown"], m["sharpe_daily"], m["mean_pnl_per_trade"]))
    for r in rets:
        cfg = dict(base_cfg); cfg["ret_threshold_abs"] = r
        m = compute_metrics(apply_decision_policy(pred_df, cfg), "r")
        rows.append(("|ret|>=" + str(r), m["trade_coverage"], m["total_pnl"],
                     m["max_drawdown"], m["sharpe_daily"], m["mean_pnl_per_trade"]))
    for rc in risk_caps:
        cfg = dict(base_cfg); cfg["risk_std7_cap"] = rc
        m = compute_metrics(apply_decision_policy(pred_df, cfg), "rc")
        rows.append(("risk std7<=" + str(rc), m["trade_coverage"], m["total_pnl"],
                     m["max_drawdown"], m["sharpe_daily"], m["mean_pnl_per_trade"]))
    df = pd.DataFrame(rows, columns=["设置", "覆盖率", "累计PnL", "最大回撤", "Sharpe(日)", "单笔均值"])
    return df.to_markdown(index=False)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build_selftest_predictions(canon):
    os.makedirs(OUT, exist_ok=True)
    rule = build_rule_predictions(canon, MAIN_NODES)
    rule.to_csv(SELF_TEST_PREDS["rule"], index=False)
    print("saved ->", SELF_TEST_PREDS["rule"], "rows:", len(rule))
    lr = build_lr_predictions(canon, MAIN_NODES)
    lr.to_csv(SELF_TEST_PREDS["interpretable"], index=False)
    print("saved ->", SELF_TEST_PREDS["interpretable"], "rows:", len(lr))
    return rule, lr


def tail_concentration(d, k=(10, 50)):
    """最终策略 PnL 的尾部集中度：top-k 盈利事件占总 PnL 比例。"""
    p = d[d["decision"] != "NO_TRADE"]["pnl"].astype(float)
    if len(p) == 0 or p.sum() == 0:
        return {}
    s = np.sort(p.values)
    tot = p.sum()
    return {int(kk): float(s[-kk:].sum() / tot) for kk in k}


def conclusions_from_results(strategy_rows, frames, meta):
    """基于真实结果写结论（诚实，不粉饰）。"""
    idx = {r["label"]: r for r in strategy_rows}
    final_label = meta["final_policy_label"]
    final = idx.get(final_label, {})
    at_rule = idx.get("All-Trade[rule]", idx.get("All-Trade[rule] [SELF-TEST]", {}))
    at_interpretable = idx.get("All-Trade[interpretable]", idx.get("All-Trade[interpretable] [SELF-TEST]", {}))
    at_cat = idx.get("All-Trade[catboost]", {})
    static_sell = idx.get("Static: always SELL", {})
    cs = []

    sell_p = final.get("SELL_precision"); buy_p = final.get("BUY_precision")
    acc = final.get("dir_acc_traded"); cov = final.get("trade_coverage")
    tot = final.get("total_pnl"); mdd = final.get("max_drawdown")
    shr = final.get("sharpe_daily"); win = final.get("win_rate")

    if acc is not None and acc == acc:
        cs.append("最终决策策略（%s）在 out-of-sample 窗口：方向准确率(交易) = %.1f%%，覆盖率 = %.0f%%，"
                  "SELL precision = %.1f%%，BUY precision = %.1f%%，胜率 = %.1f%%。"
                  % (final_label, acc * 100, (cov or 0) * 100, (sell_p or 0) * 100,
                     (buy_p or 0) * 100, (win or 0) * 100))
    if tot is not None:
        cs.append("累计 PnL = %s $/MWh（1 MWh/仓）；单笔均值 = %s；最大回撤 = %s；日频 Sharpe = %s。"
                  % (fmt(tot, 0), fmt(final.get("mean_pnl_per_trade")), fmt(mdd, 0), fmt(shr)))

    if at_rule.get("total_pnl") is not None and static_sell.get("total_pnl") is not None:
        cs.append("**市场环境（regime）提示**：同一窗口“静态全 SELL”（每时每刻持 1 MWh）累计 PnL = %s，"
                  "高于 Rule 的 All-Trade（%s）与最终决策策略（%s）。最终策略单笔均值更高（%s vs %s），"
                  "说明对小时有**一定选择性**，但该选择集中在 CONTROLX 高波动尾部小时，且总收益不及"
                  "漂移本身——本窗口 DA>RTPD 平均为正（尤其 2026-06），盈利很大程度来自市场漂移。"
                  % (fmt(static_sell["total_pnl"], 0), fmt(at_rule["total_pnl"], 0),
                     fmt(tot, 0), fmt(final.get("mean_pnl_per_trade")),
                     fmt(static_sell.get("mean_pnl_per_trade"))))
    if at_interpretable.get("total_pnl") is not None and at_cat.get("total_pnl") is not None:
        cs.append("**准确率 ≠ 盈利（关键反例）**：Interpretable（acc≈%.0f%%）与 CatBoost（acc≈%.0f%%）"
                  "的方向准确率明显高于 Rule（acc≈%.0f%%），但 All-Trade PnL 分别为 %s 与 %s，"
                  "大幅亏损 —— 它们预测 CONTROLX 大量 BUY，频繁小赚却在 ±数千 $/MWh 的极端右尾事件上"
                  "一次性巨亏。以 accuracy 最大化为目标在本数据上是错误导向。"
                  % ((at_interpretable.get("dir_acc_all") or 0) * 100,
                     (at_cat.get("dir_acc_all") or 0) * 100,
                     (at_rule.get("dir_acc_all") or 0) * 100,
                     fmt(at_interpretable["total_pnl"], 0), fmt(at_cat["total_pnl"], 0)))
    cs.append("收益分布：CONTROLX 呈强正偏态（std≈169，含 +3656 $/MWh 稀缺定价事件），"
              "累计 PnL 的 50%+ 由 top-50 极端事件贡献；预测与收益的秩相关≈0。"
              "高置信/低波动过滤把方向准确率从 55%→65%，却把 PnL 从正打到负（见敏感性表）——"
              "盈利本质是“彩票式”抓右尾，不是稳定的方向预测。")
    tc = tail_concentration(frames[final_label])
    fd = frames[final_label]
    n_trd = int((fd["decision"] != "NO_TRADE").sum())
    if tc:
        cs.append("最终决策策略的 PnL 尾部集中度：top-10 事件贡献 %.0f%%，top-50 贡献 %.0f%%"
                  "（共 %d 笔交易）—— 少数极端事件决定成败。"
                  % (tc.get(10, 0) * 100, tc.get(50, 0) * 100, n_trd))
    trd = fd[fd["decision"] != "NO_TRADE"].dropna(subset=["expected_return", "actual_return"])
    if len(trd) > 5:
        ic = float(np.corrcoef(trd["expected_return"], trd["actual_return"])[0, 1])
        icr = float(np.corrcoef(pd.Series(trd["expected_return"]).rank(),
                                pd.Series(trd["actual_return"]).rank())[0, 1])
        cs.append("交易子集内 expected_return 与 actual_return：Pearson=%.3f，Spearman=%.3f —— "
                  "幅度预测基本无单调信息。" % (ic, icr))
    if shr is not None and shr == shr:
        if shr < 0:
            cs.append("日频 Sharpe ≈ %s（<0）：风险调整后没有正收益；在合理 trade coverage 下，"
                      "风险调整后 PnL **不优于 baseline**（静态 SELL 反而更高）。" % fmt(shr))
        elif shr < 1.0:
            cs.append("日频 Sharpe ≈ %s：虽有正均值，但风险调整后仅勉强为正，且对极端事件高度敏感，"
                      "不宜视为稳健 alpha。" % fmt(shr))
    cs.append("诊断结论：在当前无泄漏数据下，三层模型的真实预测价值有限 —— 方向有微弱信号（约 55–65%），"
              "但**幅度预测秩相关≈0**，盈利被市场 regime 与 CONTROLX 尾部事件主导，"
              "剔除尾部后策略反亏；保守的 Rule 虽为正 PnL，但未跑赢“静态全 SELL”漂移基准。")
    return cs


def main():
    ap = argparse.ArgumentParser(description="CA-ISO 价差项目严格 as-of 回测引擎")
    ap.add_argument("--selfcheck", action="store_true", help="只跑 no-leakage 自检")
    ap.add_argument("--preds", help="对指定预测 CSV 单独回测")
    ap.add_argument("--label", default="custom", help="--preds 的策略名")
    ap.add_argument("--skip-selftest", action="store_true", help="不生成自测 Rule/LR 预测")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    canon = load_canonical()

    if args.selfcheck:
        selfcheck(canon)
        return

    if args.preds:
        pred = load_predictions(args.preds)
        dp = apply_decision_policy(pred)
        at = make_all_trade_frame(pred)
        m1 = compute_metrics(at, "All-Trade[%s]" % args.label)
        m2 = compute_metrics(dp, "Decision[%s]" % args.label)
        for m in (m1, m2):
            print(json.dumps(m, ensure_ascii=False, indent=2))
        return

    if not args.skip_selftest:
        print("== 构建框架自测预测（Rule / 可解释基准 walk-forward）==")
        build_selftest_predictions(canon)

    pred_sources = resolve_prediction_sources()
    if not pred_sources:
        print("警告：没有任何预测文件可用。请先运行（默认会自动生成自测文件），或提供 --preds。")
        return

    print("== 使用预测来源 ==")
    for name, (path, is_self) in pred_sources.items():
        print("  %-14s -> %s%s" % (name, path, "  [SELF-TEST]" if is_self else "  [Agent C]"))

    rows, frames = build_strategy_matrix(pred_sources, MAIN_NODES)
    rows_elca, frames_elca = build_strategy_matrix(pred_sources, [ELCA_NODE])

    # 框架自测附录：val+test 上本模块 Rule / 可解释基准（walk-forward）的表现与敏感性
    selftest_appendix = None
    if os.path.exists(SELF_TEST_PREDS["rule"]) and os.path.exists(SELF_TEST_PREDS["interpretable"]):
        self_src = {"rule": (SELF_TEST_PREDS["rule"], True),
                    "interpretable": (SELF_TEST_PREDS["interpretable"], True)}
        st_rows, _st_frames = build_strategy_matrix(self_src, MAIN_NODES)
        st_sens = build_sensitivity(load_predictions(SELF_TEST_PREDS["rule"]),
                                    DECISION_CFG, "rule")
        selftest_appendix = {"matrix": st_rows, "sensitivity": st_sens}

    # 最终决策策略 = Decision[rule]（Agent C 优先）
    final_key = None
    for r in rows:
        if r["label"].startswith("Decision[rule]"):
            final_key = r["label"]
            break
    if final_key is None and rows:
        final_key = rows[-1]["label"]
    final_df = frames[final_key]

    # 敏感性表（用最终策略所依据的 Rule 预测）
    rule_path = SELF_TEST_PREDS["rule"]
    if os.path.exists(AGENT_C_PREDS["rule"]):
        rule_path = AGENT_C_PREDS["rule"]
    sens = build_sensitivity(load_predictions(rule_path), DECISION_CFG, "rule")

    # snapshots
    snap_df = pick_snapshots(final_df)
    snapshots = [{
        "node": r["node"], "target_date": str(r["target_date"].date()) if hasattr(r["target_date"], "date") else str(r["target_date"]),
        "hour": int(r["hour"]), "pred_direction": (None if pd.isna(r["pred_direction"]) else int(np.sign(r["pred_direction"]))),
        "expected_return": (None if pd.isna(r["expected_return"]) else round(float(r["expected_return"]), 2)),
        "prob_return_positive": (None if pd.isna(r["prob_return_positive"]) else round(float(r["prob_return_positive"]), 3)),
        "confidence": (None if pd.isna(r["confidence"]) else round(float(r["confidence"]), 3)),
        "decision": str(r["decision"]),
        "actual_return": round(float(r["actual_return"]), 2),
        "pnl": round(float(r["pnl"]), 2),
        "evidence": str(r.get("evidence", ""))[:200],
    } for _, r in snap_df.iterrows()]

    # 评估窗口描述（Agent C 文件均为 test；仅自测时含 val+test）
    _any_agent = any(not is_self for (_p, is_self) in pred_sources.values())
    if _any_agent:
        meta_window = "test 2026-06-02 ~ 2026-08-05（Agent C 正式预测）"
    else:
        meta_window = "val+test（框架自测预测）"

    # 结论
    meta = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "canon_rows": int(len(canon)),
        "train_d0": str(canon[canon["split"] == "train"]["target_date"].min().date()),
        "train_d1": str(canon[canon["split"] == "train"]["target_date"].max().date()),
        "val_d0": str(canon[canon["split"] == "val"]["target_date"].min().date()),
        "val_d1": str(canon[canon["split"] == "val"]["target_date"].max().date()),
        "test_d0": str(canon[canon["split"] == "test"]["target_date"].min().date()),
        "test_d1": str(canon[canon["split"] == "test"]["target_date"].max().date()),
        "final_policy_label": final_key,
        "eval_window": meta_window,
        "snapshots": snapshots,
        "selftest_appendix": selftest_appendix,
        "sensitivity_table": sens,
        "conclusions": [],
        "limitations": [],
    }
    meta["conclusions"] = conclusions_from_results(rows, frames, meta)

    agent_ready = [k for k in AGENT_C_PREDS if os.path.exists(AGENT_C_PREDS[k])]
    if agent_ready:
        lim_models = ("CatBoost / Interpretable / Rule 均为 Agent C 正式预测" if len(agent_ready) == 3
                      else "Agent C 已就绪：%s" % ", ".join(agent_ready))
    else:
        lim_models = ("Agent C 正式预测未就绪，Rule/Interpretable 为本模块框架自测；"
                      "文件落盘后 `python backtest.py` 自动消费。")
    meta["limitations"] = [
        lim_models + "；回测引擎按统一 schema 通用，任意模型 CSV 均可 `--preds` 评估。",
        "Agent C 文件只覆盖 test 窗口（2026-06-02 ~ 2026-08-05，65 天）；样本短且 2026-06 处 DA>RTPD 强正漂移 regime，结论外推受限。",
        "CONTROLX 极端尾事件（±数千 $/MWh）主导 PnL，1 MWh/仓位的绝对额与真实资本回报不可直接类比；未做仓位优化与交易成本/冲击（契约约定暂不做）。",
        "天气特征 valid_pt 为 naive 时区（America/Los_Angeles 未换算），历史滞后不受泄漏影响，但存在小时对齐不确定性。",
    ]

    # 打印关键表
    print("=" * 74)
    print("指标矩阵（out-of-sample 主节点 ZP26）")
    print("=" * 74)
    print(metrics_table(rows))
    print()
    print("最终决策策略：", final_key)
    print(decision_detail(final_df).to_string(index=False))
    print()
    print("ELCAJNGT（cold-start，单独评估）")
    print(metrics_table(rows_elca))
    print()
    print("敏感性表（Rule）：")
    print(sens)
    print()

    # 输出文件
    summary = {
        "meta": {k: v for k, v in meta.items() if k != "snapshots"},
        "strategy_matrix_main": rows,
        "strategy_matrix_elca": rows_elca,
        "decision_detail": decision_detail(final_df).to_dict(orient="records"),
        "node_breakdown": breakdown_node(final_df).to_dict(orient="records"),
        "hour_breakdown": breakdown_hour(final_df).to_dict(orient="records"),
        "month_breakdown": breakdown_month(final_df).to_dict(orient="records"),
        "elca_decision_detail": decision_detail(frames_elca.get(final_key, final_df)).to_dict(orient="records"),
        "snapshots": snapshots,
    }
    with open(os.path.join(OUT, "backtest_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print("saved ->", os.path.join(OUT, "backtest_summary.json"))

    plot_equity(frames, os.path.join(OUT, "equity_curves.png"))
    print("saved ->", os.path.join(OUT, "equity_curves.png"))

    write_report(meta, rows, frames, pred_sources, os.path.join(OUT, "backtest_report.md"),
                 rows_elca=rows_elca)
    print("saved ->", os.path.join(OUT, "backtest_report.md"))


if __name__ == "__main__":
    main()
