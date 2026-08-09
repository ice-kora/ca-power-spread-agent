# -*- coding: utf-8 -*-
"""Agent B — deep-dive diagnostics v2 (fixed)."""
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

# ---------------- Per-model top50 lists & union ----------------
per_top50 = {}
for name, df in frames.items():
    tr = df[df["pred_direction"] != 0].sort_values("pnl", ascending=False)
    per_top50[name] = tr.head(50)
merged = pd.concat([per_top50[m] for m in frames]).drop_duplicates(
    subset=["node", "target_date", "hour"]).sort_values("pnl", ascending=False).head(50)
print("Merged top50 sum:", f"{merged['pnl'].sum():,.1f}")
print("Merged top50 unique dates:", merged["target_date"].nunique())

# Use the saved CSV (has all feature context columns) as canonical top50 source
top = pd.read_csv(os.path.join(DATA, "stage3", "top_profit_events.csv"))
top["target_date"] = pd.to_datetime(top["target_date"])
assert len(top) == 50 and top["pnl"].sum() == merged["pnl"].sum()

# Best-pnl-per-row union (for concentration reference)
def best_pnl_per_row():
    rows = []
    for name, df in frames.items():
        tr = df[df["pred_direction"] != 0][["node", "target_date", "hour", "pnl"]].assign(model=name)
        rows.append(tr.assign(model=name))
    allr = pd.concat(rows)
    best = allr.groupby(["node", "target_date", "hour"])["pnl"].max().reset_index()
    return best
best = best_pnl_per_row()
btot = best["pnl"].sum()
print("\nBest-pnl-per-row union: n_rows=", len(best), "total=", f"{btot:,.1f}")
for n in [20, 50, 100]:
    s = best.nlargest(n, "pnl")["pnl"].sum()
    print(f"  best-row top{n}: {s:,.0f}  share={s/btot:.3f}")

# ---------------- Feature context for top events ----------------
print("\n" + "=" * 80)
print("Pre-trade feature context: merged top50 vs CONTROLX test baseline")
# top already loaded from CSV
ctrl_test = canon[(canon["node"] == "CONTROLX_1_N001") & (canon["split"] == "test")]
feat_cols = ["spread_lag1", "spread_lag7", "spread_mean7", "spread_std7", "spread_mean14",
             "spread_mean30", "spread_day_range_lag1", "da_day_mean_lag1", "rtpd_day_mean_lag1",
             "load_2da_forecast", "load_actual_day_mean_lag1", "t2m_lag1", "ssrd_lag1",
             "wind100_lag1", "peer_spread_lag1"]
base_sel = ctrl_test.set_index(["node", "target_date", "hour"]).loc[
    list(zip(top["node"], top["target_date"], top["hour"]))]
print("\n  Feature | top50 median | CONTROLX-test median | test std | pctile(top50 med)")
for c in feat_cols:
    if c in base_sel.columns:
        tv = base_sel[c]
        cv = ctrl_test[c]
        pct = (cv < tv.median()).mean() if cv.std() > 0 else np.nan
        print(f"  {c:26s} {tv.median():10.2f}  {cv.median():10.2f}  {cv.std():10.2f}  {pct:8.3f}")

# percentile distribution of top50 features within CONTROLX test
print("\n  Percentile of each top50 event feature within CONTROLX-test (median / min / max)")
for c in feat_cols:
    if c in base_sel.columns and ctrl_test[c].std() > 0:
        pcts = base_sel[c].map(lambda x: (ctrl_test[c] < x).mean())
        print(f"  {c:26s} med={pcts.median():.3f}  min={pcts.min():.3f}  max={pcts.max():.3f}")

# ---------------- Q2: was there pre-trade signal? ----------------
print("\n" + "=" * 80)
print("Q2 check: pre-trade spread level/trend before the extreme episodes")
# look at lagged & rolling spreads for the top events, split SELL vs BUY
# top already loaded from CSV
for act, sub in top.groupby("action"):
    print(f"\n  [{act}] n={len(sub)}  median spread_lag1={sub['spread_lag1'].median():.1f}  "
          f"median spread_mean7={sub['spread_mean7'].median():.1f}  median spread_mean30={sub['spread_mean30'].median():.1f}  "
          f"median spread_std7={sub['spread_std7'].median():.1f}")
    print(f"       median da_day_mean_lag1={sub['da_day_mean_lag1'].median():.1f} rtpd_day_mean_lag1={sub['rtpd_day_mean_lag1'].median():.1f}")

# For the CONTROLX extreme episodes, what were the daily extreme price levels?
print("\n  CONTROLX daily extremes in test period (actual_da min / actual_rtpd min per day)")
ctrl_test_daily = ctrl_test.groupby("target_date").agg(
    da_min=("actual_da", "min"), rtpd_min=("actual_rtpd", "min"),
    ret_min=("actual_return", "min"), ret_max=("actual_return", "max"))
ext_days = ctrl_test_daily[(ctrl_test_daily["da_min"] < -500) | (ctrl_test_daily["rtpd_min"] < -500) | (ctrl_test_daily["ret_min"] < -500) | (ctrl_test_daily["ret_max"] > 500)]
print(ext_days.round(1).to_string())

# ---------------- Q4: recurring patterns ----------------
print("\n" + "=" * 80)
print("Q4: pattern census in merged top50")
print("nodes:", top["node"].value_counts().to_dict())
print("action:", top["action"].value_counts().to_dict())
print("is_holiday:", top["is_holiday"].value_counts().to_dict())
print("month:", top["month"].value_counts().to_dict())
print("solar_flag:", top["solar_flag"].value_counts().to_dict())

# hour clustering: early AM (1-5) / midday / evening (18-24)
def hb(h):
    if h <= 5: return "earlyAM(1-5)"
    if h <= 12: return "morning(6-12)"
    if h <= 17: return "afternoon(13-17)"
    return "evening(18-24)"
top["hband"] = top["hour"].map(hb)
print("hour band:", top.groupby("hband").size().to_dict())

# ---------------- Q3 deeper: confidence vs pnl in profit bucket ----------------
print("\n" + "=" * 80)
print("Q3: confidence–pnl relation in profit buckets")
for name, df in frames.items():
    tr = df[df["pred_direction"] != 0].copy()
    wins = tr[tr["pnl"] > 0]
    # bin wins by confidence quartile
    try:
        q = pd.qcut(wins["confidence"], 4)
        g = wins.groupby(q, observed=False)["pnl"].agg(["mean", "sum", "count"])
        print(f"\n  [{name}] profit trades binned by confidence:")
        print(g.round(1).to_string())
    except Exception as e:
        print(f"  [{name}] qcut fail: {e}")

# ---------------- Why so profitable: magnitude decomposition ----------------
print("\n" + "=" * 80)
print("Q1: magnitude decomposition — merged top50 actual_da / actual_rtpd")
print(top[["actual_da", "actual_rtpd", "actual_return"]].describe().round(1).to_string())
print("\n  # with actual_da < -500:", int((top["actual_da"] < -500).sum()))
print("  # with actual_rtpd < -500:", int((top["actual_rtpd"] < -500).sum()))
print("  # both < -500:", int(((top["actual_da"] < -500) & (top["actual_rtpd"] < -500)).sum()))

# ---------------- Compare with loss analysis: which node/hour both big win & big loss ----------------
print("\n" + "=" * 80)
print("Q4b: do the same node/hour both big-win and big-loss? (per model)")
for name, df in frames.items():
    tr = df[df["pred_direction"] != 0].copy()
    bw = tr.nlargest(20, "pnl")
    bl = tr.nsmallest(20, "pnl")
    bw_k = set(zip(bw["node"], bw["hour"]))
    bl_k = set(zip(bl["node"], bl["hour"]))
    inter = bw_k & bl_k
    print(f"  [{name}] top20-win (node,hour) ∩ top20-loss (node,hour): {len(inter)}"
          + (f"  e.g. {list(inter)[:5]}" if inter else ""))

# node-level total pnl per model
print("\n  Per-model node totals:")
for name, df in frames.items():
    tr = df[df["pred_direction"] != 0]
    print(f"  [{name}] " + ", ".join(f"{n}={v:,.0f}" for n, v in tr.groupby("node")["pnl"].sum().items()))

# ---------------- Extreme census with split ----------------
print("\n" + "=" * 80)
print("Extreme returns census: |actual_return| > 500")
ext = canon[canon["actual_return"].abs() > 500]
print(ext.groupby(["node", "split"]).size().to_string())
print("\nExtreme test rows by node that the models traded:")
for name, df in frames.items():
    tr = df[df["pred_direction"] != 0]
    e = tr[tr["actual_return"].abs() > 500]
    print(f"  [{name}] traded extreme rows: {len(e)}  "
          f"correct side (pnl>0): {int((e['pnl']>0).sum())}  "
          f"wrong side (pnl<0): {int((e['pnl']<0).sum())}")
