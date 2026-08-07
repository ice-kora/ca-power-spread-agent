# -*- coding: utf-8 -*-
"""
evaluate.py —— CA-ISO 电价价差预测的「业务导向」评估
===================================================

输入（train.py 生成的测试集预测）:
    code/data/test_predictions.csv
    列: node, date(目标日 D+1), hour,
        spread_q10, spread_q50, spread_q90, spread_actual,
        da_pred, rtpd_pred, da_actual, rtpd_actual,
        direction_pred(由 spread_q50 符号得到 1/-1),
        decision_pred(由三态规则得到 "buy"/"sell"/"hold")

三态规则（与建模约定一致）:
    spread_q90 < 0   -> "buy"   (价差为负、RTPD 更高 -> 日前买、实时卖)
    spread_q10 > 0   -> "sell"  (价差为正 -> 日前卖、实时买)
    否则             -> "hold"  (区间跨零 -> 观望)

评估内容:
    1) 数值精度 : spread / da / rtpd 的 MAE、RMSE（整体 + 按 node）
    2) 方向准确率: sign(spread_q50) vs sign(spread_actual)，整体 + 按 node，
                 以及「观望过滤后」(仅 buy/sell 样本)的准确率
    3) 决策质量 : buy/sell/hold 占比 + 每种决策下的平均单笔收益；
                 并检验 hold 是否避开了大额亏损时段
    4) 模拟套利收益(核心): 见下方 pnl 推导；对比 全交易/反向/不交易 三个基准，
                 画累计收益曲线
    5) 输出     : stdout 格式化表格 + code/data/evaluation_summary.json
                 + code/data/arb_curve.png(累计收益曲线，图例英文)

套利单笔收益推导（业务口径，spread = DA - RTPD）:
    spread < 0 (RTPD 更高) -> 日前买、实时卖，赚 RTPD - DA = -spread > 0
    spread > 0 (DA 更高)   -> 日前卖、实时买，赚 DA - RTPD = +spread > 0
    故: 方向正确 -> 收益 = |spread|，方向错误 -> 收益 = -|spread|
    即: 单笔收益 = sign(spread_q50) * spread_actual，decision=hold 时记为 0。

用法:
    python evaluate.py [--input CSV] [--json PATH] [--png PATH]
"""
import os
import json
import argparse

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")  # 无界面后端，避免交互窗口
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(HERE, "data", "test_predictions.csv")
DEFAULT_JSON = os.path.join(HERE, "data", "evaluation_summary.json")
DEFAULT_PNG = os.path.join(HERE, "data", "arb_curve.png")

REQUIRED_COLS = [
    "node", "date", "hour",
    "prob_sell", "spread_std7", "spread_q10", "spread_q50", "spread_q90", "spread_actual",
    "da_pred", "rtpd_pred", "da_actual", "rtpd_actual",
    "direction_pred", "decision_pred",
]


# ---------------------------------------------------------------------------
# 数据载入与派生列
# ---------------------------------------------------------------------------
def load_predictions(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            "预测文件不存在: %s\n请先运行 train.py 生成 test_predictions.csv。" % path)
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError("test_predictions.csv 缺少必需列: %s" % missing)
    df["node"] = df["node"].astype(str).str.strip()
    df["date"] = df["date"].astype(str).str.strip()
    df["decision_pred"] = df["decision_pred"].astype(str).str.strip()
    num_cols = [c for c in REQUIRED_COLS if c not in ("node", "date", "decision_pred")]
    n_before = len(df)
    df = df.dropna(subset=num_cols).reset_index(drop=True)
    if len(df) < n_before:
        print("[warn] 丢弃 %d 行无真实值/NaN 的样本（末段目标日超出价格数据），在 %d 行有效样本上评估"
              % (n_before - len(df), len(df)))
    return df


def derive_columns(df):
    """计算评估用的派生列（含 pnl，业务口径见模块 docstring）。
    方向由方向分类器的概率决定（train.py 已写入 direction_pred）。"""
    df = df.copy()
    df["pred_sign"] = df["direction_pred"].astype(int)      # 预测方向（+1/-1）
    df["actual_sign"] = np.sign(df["spread_actual"]).astype(int)  # 实际方向
    df["dir_correct"] = df["pred_sign"] == df["actual_sign"]
    # 单笔收益 = sign(pred) * spread_actual；hold 不交易记 0
    df["pnl"] = (df["pred_sign"] * df["spread_actual"]).astype(float)
    df["pnl_trade"] = df["pnl"].where(df["decision_pred"] != "hold", 0.0)
    return df


def consistency_check(df):
    """校验 CSV 里已给的 direction_pred / decision_pred 与单边概率+波动规则是否一致。"""
    rule_decision = np.where(
        (df["prob_sell"] > 0.5) & (df["spread_std7"] <= 120.0), "sell", "hold")
    rule_direction = np.where(df["prob_sell"] > 0.5, 1, -1)
    n_dec_mismatch = int((rule_decision != df["decision_pred"]).sum())
    n_dir_mismatch = int(
        (rule_direction.astype(int) != df["direction_pred"].astype(int)).sum())
    return {"decision_mismatch": n_dec_mismatch,
            "direction_mismatch": n_dir_mismatch}


# ---------------------------------------------------------------------------
# 1) 数值精度
# ---------------------------------------------------------------------------
def mae(actual, pred):
    return float(np.mean(np.abs(np.asarray(actual) - np.asarray(pred))))


def rmse(actual, pred):
    return float(np.sqrt(np.mean((np.asarray(actual) - np.asarray(pred)) ** 2)))


def metrics_dict(actual, pred):
    return {"mae": round(mae(actual, pred), 4), "rmse": round(rmse(actual, pred), 4)}


def numerical_metrics(df):
    res = {}
    res["overall"] = {
        "spread": metrics_dict(df["spread_actual"], df["spread_q50"]),
        "da": metrics_dict(df["da_actual"], df["da_pred"]),
        "rtpd": metrics_dict(df["rtpd_actual"], df["rtpd_pred"]),
    }
    res["by_node"] = {}
    for node, sub in df.groupby("node"):
        res["by_node"][node] = {
            "spread": metrics_dict(sub["spread_actual"], sub["spread_q50"]),
            "da": metrics_dict(sub["da_actual"], sub["da_pred"]),
            "rtpd": metrics_dict(sub["rtpd_actual"], sub["rtpd_pred"]),
        }
    return res


# ---------------------------------------------------------------------------
# 2) 方向准确率
# ---------------------------------------------------------------------------
def direction_metrics(df):
    match = df["dir_correct"]
    res = {"overall": {"n": int(len(df)), "correct": int(match.sum()),
                       "accuracy": round(float(match.mean()), 4)}}
    res["by_node"] = {}
    for node, sub in df.groupby("node"):
        res["by_node"][node] = {"n": int(len(sub)),
                                "correct": int(sub["dir_correct"].sum()),
                                "accuracy": round(float(sub["dir_correct"].mean()), 4)}

    # 观望过滤后：只看 buy/sell 样本
    traded = df[df["decision_pred"] != "hold"]
    res["filtered_buy_sell"] = {"n": int(len(traded)),
                                "correct": int(traded["dir_correct"].sum()),
                                "accuracy": round(float(traded["dir_correct"].mean()), 4)
                                if len(traded) else 0.0}
    res["by_node_filtered"] = {}
    for node, sub in traded.groupby("node"):
        res["by_node_filtered"][node] = {"n": int(len(sub)),
                                         "correct": int(sub["dir_correct"].sum()),
                                         "accuracy": round(float(sub["dir_correct"].mean()), 4)}
    return res


# ---------------------------------------------------------------------------
# 3) 决策质量
# ---------------------------------------------------------------------------
def decision_metrics(df):
    order = ["buy", "sell", "hold"]
    counts = df["decision_pred"].value_counts().to_dict()
    counts = {k: int(counts.get(k, 0)) for k in order}
    total = len(df)
    pct = {k: round(v / total, 4) for k, v in counts.items()}

    avg_pnl = df.groupby("decision_pred")["pnl"].mean().to_dict()
    avg_pnl = {k: round(float(avg_pnl.get(k, 0.0)), 4) for k in order}

    # hold 样本若没有观望、强行交易会产生的收益（观察是否避开大额亏损）
    hold = df[df["decision_pred"] == "hold"]
    hypothetical = hold["pnl"]
    hold_if_traded = {
        "count": int(len(hold)),
        "sum": round(float(hypothetical.sum()), 4),
        "mean": round(float(hypothetical.mean()), 4) if len(hold) else 0.0,
        "max_loss_avoided": round(float(hypothetical.min()), 4) if len(hold) else 0.0,
    }
    return {"counts": counts, "pct": pct, "avg_pnl": avg_pnl,
            "hold_if_traded": hold_if_traded}


# ---------------------------------------------------------------------------
# 4) 模拟套利收益（核心）
# ---------------------------------------------------------------------------
def summarize_pnl(series, dates):
    """对一条 pnl 序列汇总: 总额/日均/命中率/最大单笔亏损/累计末值。"""
    s = pd.Series(np.asarray(series, dtype=float))
    dts = pd.Series(np.asarray(dates, dtype=object))
    n = int(len(s))
    if n == 0:
        return {"n": 0, "total_pnl": 0.0, "n_days": 0, "daily_mean": 0.0,
                "hit_rate": 0.0, "max_single_loss": 0.0, "cum_final": 0.0}
    total = float(s.sum())
    hit = float((s > 0).mean())
    max_loss = float(s.min())
    daily = s.groupby(dts).sum()
    daily_mean = float(daily.mean())
    return {"n": n,
            "total_pnl": round(total, 4),
            "n_days": int(len(daily)),
            "daily_mean": round(daily_mean, 4),
            "hit_rate": round(hit, 4),
            "max_single_loss": round(max_loss, 4),
            "cum_final": round(float(s.cumsum().iloc[-1]), 4)}


def arbitrage_metrics(df):
    traded_mask = (df["decision_pred"] != "hold").values
    dates = df["date"].values

    strat_series = df["pnl_trade"].values        # 预测策略（hold 记 0）
    full_series = df["pnl"].values               # 全交易（无观望）
    rev_series = -full_series                    # 反向策略（每笔取负）
    none_series = np.zeros_like(full_series)     # 不交易

    res = {
        "strategy": summarize_pnl(strat_series[traded_mask], dates[traded_mask]),
        "full_trade": summarize_pnl(full_series, dates),
        "reverse": summarize_pnl(rev_series, dates),
        "no_trade": summarize_pnl(none_series, dates),
        # 保留累计收益序列供画图
        "cum_curve": {
            "strategy": np.cumsum(strat_series).tolist(),
            "full_trade": np.cumsum(full_series).tolist(),
            "reverse": np.cumsum(rev_series).tolist(),
        },
    }
    # 策略按交易日分解（供报告）
    res["strategy_daily"] = {
        k: round(float(v), 4)
        for k, v in pd.Series(strat_series[traded_mask]).groupby(dates[traded_mask]).sum().items()
    }
    return res


# ---------------------------------------------------------------------------
# 画图: 累计收益曲线（图例英文，避免中文乱码）
# ---------------------------------------------------------------------------
def plot_cumulative_curve(cum_curve, out_png):
    x = np.arange(len(cum_curve["strategy"]))
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(x, cum_curve["strategy"], label="Strategy (3-state)", linewidth=2.2)
    ax.plot(x, cum_curve["full_trade"], label="Full trade (no hold)", linewidth=1.5, alpha=0.9)
    ax.plot(x, cum_curve["reverse"], label="Reverse strategy", linewidth=1.5, alpha=0.9)
    ax.axhline(0, color="gray", linestyle="--", linewidth=0.8)
    ax.set_title("Cumulative Arbitrage PnL (DA - RTPD spread)")
    ax.set_xlabel("Sample (sorted: node, date, hour)")
    ax.set_ylabel("Cumulative PnL (USD/MWh)")
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 打印
# ---------------------------------------------------------------------------
def print_metrics(df, numerical, direction, decision, arb, consistency, args):
    line = "=" * 74
    print(line)
    print("CA-ISO 价差预测 —— 业务导向评估")
    print(line)
    print("输入文件 : %s" % args.input)
    print("样本数   : %d 行, %d 个节点, %d 个交易日"
          % (len(df), df["node"].nunique(), df["date"].nunique()))
    print("日期范围 : %s ~ %s" % (df["date"].min(), df["date"].max()))
    print("节点     : %s" % ", ".join(sorted(df["node"].unique())))
    print("一致性   : decision_pred 与三态规则不一致 %d 行; direction_pred 与 sign(q50) 不一致 %d 行"
          % (consistency["decision_mismatch"], consistency["direction_mismatch"]))

    # ---- 1) 数值精度 ----
    print("\n[1] 数值精度 (q50 预测 vs 实际)  —— MAE / RMSE")
    tbl = {"指标": ["整体"] + sorted(df["node"].unique())}
    for key, label in [("spread", "spread"), ("da", "DA"), ("rtpd", "RTPD")]:
        tbl[label + " MAE"] = [numerical["overall"][key]["mae"]] + [
            numerical["by_node"][n][key]["mae"] for n in sorted(df["node"].unique())]
        tbl[label + " RMSE"] = [numerical["overall"][key]["rmse"]] + [
            numerical["by_node"][n][key]["rmse"] for n in sorted(df["node"].unique())]
    print(pd.DataFrame(tbl).to_string(index=False))

    # ---- 2) 方向准确率 ----
    print("\n[2] 方向准确率 sign(spread_q50) vs sign(spread_actual)")
    d_tbl = pd.DataFrame({
        "范围": ["整体", "过滤后(仅 buy/sell)"] + sorted(df["node"].unique()),
        "样本数": [direction["overall"]["n"], direction["filtered_buy_sell"]["n"]] + [
            direction["by_node"][n]["n"] for n in sorted(df["node"].unique())],
        "正确": [direction["overall"]["correct"], direction["filtered_buy_sell"]["correct"]] + [
            direction["by_node"][n]["correct"] for n in sorted(df["node"].unique())],
        "准确率": [direction["overall"]["accuracy"], direction["filtered_buy_sell"]["accuracy"]] + [
            direction["by_node"][n]["accuracy"] for n in sorted(df["node"].unique())],
    })
    print(d_tbl.to_string(index=False))
    filt_nodes = ", ".join(
        "%s=%.4f" % (n, direction["by_node_filtered"][n]["accuracy"])
        for n in direction["by_node_filtered"])
    print("过滤后按节点:", filt_nodes)

    # ---- 3) 决策质量 ----
    print("\n[3] 决策质量 (三态规则)")
    print("决策占比 : buy=%d(%.1f%%), sell=%d(%.1f%%), hold=%d(%.1f%%)"
          % (decision["counts"]["buy"], decision["pct"]["buy"] * 100,
             decision["counts"]["sell"], decision["pct"]["sell"] * 100,
             decision["counts"]["hold"], decision["pct"]["hold"] * 100))
    print("平均单笔收益(若该决策执行): buy=%.4f, sell=%.4f, hold=%.4f(不交易=0)"
          % (decision["avg_pnl"]["buy"], decision["avg_pnl"]["sell"],
             decision["avg_pnl"]["hold"]))
    h = decision["hold_if_traded"]
    print("hold 样本若强行交易: 共%d笔, 合计%.4f, 均值%.4f, 最大单笔可避免亏损 %.4f"
          % (h["count"], h["sum"], h["mean"], h["max_loss_avoided"]))

    # ---- 4) 模拟套利收益 ----
    print("\n[4] 模拟套利收益 (单笔 = sign(q50)*spread_actual, hold 计 0)")
    a_tbl = pd.DataFrame({
        "策略": ["预测策略(三态)", "基准A: 全交易(无观望)", "基准B: 反向策略", "基准C: 不交易"],
        "样本数": [arb["strategy"]["n"], arb["full_trade"]["n"],
                  arb["reverse"]["n"], arb["no_trade"]["n"]],
        "总收益": [arb["strategy"]["total_pnl"], arb["full_trade"]["total_pnl"],
                  arb["reverse"]["total_pnl"], arb["no_trade"]["total_pnl"]],
        "日均收益": [arb["strategy"]["daily_mean"], arb["full_trade"]["daily_mean"],
                   arb["reverse"]["daily_mean"], arb["no_trade"]["daily_mean"]],
        "命中率": [arb["strategy"]["hit_rate"], arb["full_trade"]["hit_rate"],
                 arb["reverse"]["hit_rate"], arb["no_trade"]["hit_rate"]],
        "最大单笔亏损": [arb["strategy"]["max_single_loss"],
                    arb["full_trade"]["max_single_loss"],
                    arb["reverse"]["max_single_loss"],
                    arb["no_trade"]["max_single_loss"]],
    })
    print(a_tbl.to_string(index=False))
    print("累计收益末值: 预测策略=%.4f, 全交易=%.4f, 反向=%.4f, 不交易=0"
          % (arb["strategy"]["cum_final"], arb["full_trade"]["cum_final"],
             arb["reverse"]["cum_final"]))
    print("预测策略逐日收益:", {k: round(v, 4) for k, v in arb["strategy_daily"].items()})
    print("\n累计收益曲线已保存: %s" % args.png)
    print("评估 JSON 已保存: %s" % args.json)


# ---------------------------------------------------------------------------
# 汇总 JSON
# ---------------------------------------------------------------------------
def build_json(df, numerical, direction, decision, arb, consistency, args):
    return {
        "meta": {
            "input_file": args.input,
            "rows": int(len(df)),
            "nodes": sorted(df["node"].unique()),
            "date_min": str(df["date"].min()),
            "date_max": str(df["date"].max()),
            "n_days": int(df["date"].nunique()),
            "pnl_formula": "pnl = sign(spread_q50) * spread_actual; hold -> 0",
            "decision_rule": "spread_q90<0 -> buy; spread_q10>0 -> sell; else hold",
            "consistency": consistency,
        },
        "numerical": numerical,
        "direction": direction,
        "decision": decision,
        "arbitrage": {k: v for k, v in arb.items() if k != "cum_curve"},
    }


def save_json(obj, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="CA-ISO 价差预测业务导向评估")
    ap.add_argument("--input", default=DEFAULT_INPUT, help="test_predictions.csv 路径")
    ap.add_argument("--json", default=DEFAULT_JSON, help="评估摘要 JSON 输出路径")
    ap.add_argument("--png", default=DEFAULT_PNG, help="累计收益曲线 PNG 输出路径")
    args = ap.parse_args()

    df = load_predictions(args.input)
    df = derive_columns(df)
    consistency = consistency_check(df)

    numerical = numerical_metrics(df)
    direction = direction_metrics(df)
    decision = decision_metrics(df)
    arb = arbitrage_metrics(df)

    plot_cumulative_curve(arb["cum_curve"], args.png)
    summary = build_json(df, numerical, direction, decision, arb, consistency, args)
    save_json(summary, args.json)

    print_metrics(df, numerical, direction, decision, arb, consistency, args)


if __name__ == "__main__":
    main()
