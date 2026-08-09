# -*- coding: utf-8 -*-
"""Agent B — deep-dive diagnostics for the 5 questions."""
import pandas as pd
import numpy as np
import os, json

BASE = r"D:\code\pyCode\CA-电力交易预测"
DATA = os.path.join(BASE, "code", "data")
canon = pd.read_parquet(os.path.join(DATA, "canonical.parquet"))
canon["target_date"] = pd.to_datetime(canon["target_date"])

MODELS = {
    "rule": os.path.join(DATA, "predictions_rule.csv"),
    "interpretable": os.path.join(DATA, "predictions_interpretable.csv"),
    "catboost": os.path.join(DATA, "predictions_catboost.csv"),
}
frames = {}
for name, path in MODELS.items():
    df = pd.read_csv(path)
    df["target_date"] = pd.to_datetime(df["target_date"])
    df["pnl"] = np.where(df["pred_direction"] > 0, df["actual_return"],
                 np.where(df["pred_direction"] < 0, -df["actual_return"], 0.0))
    df["action"] = np.where(df["pred_direction"] > 0, "SELL",
                    np.where(df["pred_direction"] < 0, "BUY", "NO_TRADE"))
    frames[name] = df

# ---------------- Per-model distributions ----------------
print("=" * 80)
print("Q3: Confidence in profitable trades vs all trades")
for name, df in frames.items():
    tr = df[df["pred_direction"] != 0].copy()
    win = tr[tr["pnl"] > 0]
    lose = tr[tr["pnl"] <= 0]
    top50 = tr.nlargest(50, "pnl")
    print(f"\n[{name}] trades={len(tr)} win_rate={len(win)/len(tr):.3f}")
    print(f"  conf  all-trades: mean={tr['confidence'].mean():.3f}  win={win['confidence'].mean():.3f}  lose={lose['confidence'].mean():.3f}  top50={top50['confidence'].mean():.3f}")
    print(f"  |exp_ret|  win mean={win['expected_return'].abs().mean():.1f}  lose mean={lose['expected_return'].abs().mean():.1f}")
    # correlation confidence vs pnl among wins
    if len(win) > 5:
        print(f"  corr(conf, pnl) win-bucket: {win['confidence'].corr(win['pnl']):.3f}  all-trades: {tr['confidence'].corr(tr['pnl']):.3f}")

# ---------------- By node / hour / action ----------------
print("\n" + "=" * 80)
print("Top-50 union node/hour breakdown")
top = pd.read_csv(os.path.join(DATA, "stage3", "top_profit_events.csv"))
print("nodes:", top["node"].value_counts().to_dict())
print("action:", top["action"].value_counts().to_dict())
print("hour distribution:")
print(top.groupby("hour").size().to_string())
print("date clusters:")
print(top.groupby("target_date").agg(n=("pnl", "size"), pnl=("pnl", "sum")).sort_values("pnl", ascending=False).to_string())

# ---------------- Concentration ----------------
print("\n" + "=" * 80)
print("Q5: Concentration (per model, on their own trade sets)")
for name, df in frames.items():
    tr = df[df["pred_direction"] != 0].sort_values("pnl", ascending=False)
    total = tr["pnl"].sum()
    for n in [5, 10, 20, 50, 100]:
        s = tr["pnl"].head(n).sum()
        print(f"  {name}: top{n} sum={s:,.0f}  share_of_total={s/total:.3f}")
# union profitable subset concentration
union = pd.concat([frames[m] for m in frames])
union = union.drop_duplicates(subset=["node", "target_date", "hour"])
ut = union[union["pred_direction"] != 0].sort_values("pnl", ascending=False)
utot = ut["pnl"].sum()
print("\n  Union dedup trade set total:", f"{utot:,.0f}")
for n in [5, 10, 20, 50, 100]:
    s = ut["pnl"].head(n).sum()
    print(f"  union: top{n} sum={s:,.0f}  share_of_total={s/utot:.3f}")

# ---------------- Feature context for top events ----------------
print("\n" + "=" * 80)
print("Pre-trade feature context for merged top50 vs CONTROLX test baseline")
ctrl_test = canon[(canon["node"] == "CONTROLX_1_N001") & (canon["split"] == "test")]
feat_cols = ["spread_lag1", "spread_lag7", "spread_mean7", "spread_std7", "spread_mean14",
             "spread_mean30", "spread_day_range_lag1", "da_day_mean_lag1", "rtpd_day_mean_lag1",
             "load_2da_forecast", "load_actual_day_mean_lag1", "t2m_lag1", "ssrd_lag1",
             "wind100_lag1", "peer_spread_lag1"]
top_feats = top.set_index(["node", "target_date", "hour"])
base_feats = ctrl_test.set_index(["node", "target_date", "hour"])
rows = []
for _, r in top.iterrows():
    key = (r["node"], r["target_date"], r["hour"])
    if key in base_feats.index:
        rows.append(base_feats.loc[key])
base_sel = pd.concat(rows) if rows else pd.DataFrame()
print("\n  Feature | top50 median | top50 mean | CONTROLX-test median | CONTROLX-test std")
for c in feat_cols:
    if c in base_sel.columns:
        print(f"  {c:26s} {base_sel[c].median():10.2f}  {base_sel[c].mean():10.2f}  "
              f"{ctrl_test[c].median():10.2f}  {ctrl_test[c].std():10.2f}")

# percentile of top50 feature values within CONTROLX test distribution
print("\n  Percentile of each top50 event's feature within CONTROLX-test distribution (median pct)")
for c in feat_cols:
    if c in base_sel.columns and ctrl_test[c].std() > 0:
        pcts = base_sel[c].map(lambda x: (ctrl_test[c] < x).mean())
        print(f"  {c:26s} median_pct={pcts.median():.2f}  [range {pcts.min():.2f}-{pcts.max():.2f}]")

# ---------------- What was the model's expected_return vs actual ----------------
print("\n" + "=" * 80)
print("Model expected vs actual for merged top50")
for c in ["expected_return", "actual_return", "confidence", "prob_return_positive"]:
    print(f"  {c}: min={top[c].min():.2f} median={top[c].median():.2f} max={top[c].max():.2f}")

# ---------------- Node extreme-event census ----------------
print("\n" + "=" * 80)
print("Extreme return census in full canonical (all splits): |actual_return| > 500")
ext = canon[canon["actual_return"].abs() > 500]
print(ext.groupby(["node", "split"]).size().to_string())
print("\nBy node total extreme:")
print(ext.groupby("node")["actual_return"].agg(["count", "min", "max"]).to_string())
print("\nHow many of these extreme rows are in test?")
print(ext[ext["split"] == "test"].groupby("node").size().to_string())

# Save context json
diag = {
    "top50_nodes": top["node"].value_counts().to_dict(),
    "top50_action": top["action"].value_counts().to_dict(),
    "top50_hours": top["hour"].value_counts().sort_index().to_dict(),
    "top50_conf_mean": float(top["confidence"].mean()),
    "top50_expected_vs_actual_corr": float(top["expected_return"].corr(top["actual_return"])),
}
with open(os.path.join(DATA, "stage3", "top_profit_diag2.json"), "w", encoding="utf-8") as f:
    json.dump(diag, f, ensure_ascii=False, indent=2)
print("\nsaved diag2 json")
