# -*- coding: utf-8 -*-
"""Agent B — extreme profit event analysis (read-only)."""
import os
import pandas as pd
import numpy as np

BASE = r"D:\code\pyCode\CA-电力交易预测"
DATA = os.path.join(BASE, "code", "data")
MODELS = {
    "rule": os.path.join(DATA, "predictions_rule.csv"),
    "interpretable": os.path.join(DATA, "predictions_interpretable.csv"),
    "catboost": os.path.join(DATA, "predictions_catboost.csv"),
}
CANON = os.path.join(DATA, "canonical.parquet")

canon = pd.read_parquet(CANON)
canon["target_date"] = pd.to_datetime(canon["target_date"])

# label + feature columns from canonical
LABELS = ["actual_da", "actual_rtpd", "actual_return"]
FEATURES = [
    "dow", "month", "is_holiday", "solar_flag",
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
]

# ---------------- Load predictions & PnL ----------------
frames = {}
for name, path in MODELS.items():
    df = pd.read_csv(path)
    df["target_date"] = pd.to_datetime(df["target_date"])
    # PnL convention: SELL (+1) -> +actual_return; BUY (-1) -> -actual_return; 0 -> 0
    df["pnl"] = np.where(df["pred_direction"] > 0, df["actual_return"],
                 np.where(df["pred_direction"] < 0, -df["actual_return"], 0.0))
    # action label
    df["action"] = np.where(df["pred_direction"] > 0, "SELL",
                    np.where(df["pred_direction"] < 0, "BUY", "NO_TRADE"))
    frames[name] = df
    print(f"{name}: trades={int((df['pred_direction']!=0).sum())} "
          f"total_pnl={df['pnl'].sum():.1f} n_trades={int((df['pnl']!=0).sum())}")

# ---------------- Merge canonical labels + features ----------------
feat_map = canon.set_index(["node", "target_date", "hour"])[FEATURES + ["actual_da", "actual_rtpd", "zone"]]
for name, df in frames.items():
    f = feat_map.loc[list(zip(df["node"], df["target_date"], df["hour"]))].reset_index(drop=True)
    # sanity: actual_return == actual_da - actual_rtpd
    assert np.isclose(f["actual_da"] - f["actual_rtpd"], df["actual_return"]).all(), name
    frames[name] = pd.concat([df.reset_index(drop=True), f[FEATURES + ["actual_da", "actual_rtpd", "zone"]]], axis=1)

# ---------------- Historical context per node ----------------
# Characterize how extreme the realized return is within the node's own distribution
# (full sample diagnostic; actual_return is the realized outcome, not an input feature).
full = canon[["node", "target_date", "hour", "actual_return", "spread_lag1"]].copy()
node_dist = {}
for node, g in full.groupby("node"):
    node_dist[node] = {
        "ret_mean": g["actual_return"].mean(),
        "ret_std": g["actual_return"].std(),
        "ret_vals": g["actual_return"].values,
        "spread_lag1_vals": g["spread_lag1"].dropna().values,
    }

for name, df in frames.items():
    df["ret_z_hist"] = np.nan
    df["ret_pct_hist"] = np.nan
    df["spread_lag1_pct_node"] = np.nan
    for node, g in df.groupby("node"):
        d = node_dist[node]
        mu, sd = d["ret_mean"], d["ret_std"]
        df.loc[g.index, "ret_z_hist"] = (g["actual_return"] - mu) / (sd if sd else np.nan)
        df.loc[g.index, "ret_pct_hist"] = g["actual_return"].map(lambda x: (d["ret_vals"] < x).mean())
        sl = g["spread_lag1"].copy()
        df.loc[g.index, "spread_lag1_pct_node"] = sl.map(lambda x: (d["spread_lag1_vals"] < x).mean())

# ---------------- Three-model consistency ----------------
# For each (node,date,hour), gather pred_direction from all 3 models.
dirs = pd.DataFrame({name: df.set_index(["node", "target_date", "hour"])["pred_direction"]
                     for name, df in frames.items()}).reset_index()
dirs.columns = ["node", "target_date", "hour"] + [f"{m}_dir" for m in frames]
# agreement among non-zero
def agreement(row):
    v = [row[f"{m}_dir"] for m in frames]
    nz = [x for x in v if x != 0]
    if not nz:
        return np.nan
    return max(nz.count(1), nz.count(-1)) / len(nz)
dirs["agree_nz"] = dirs.apply(agreement, axis=1)
dirs["n_models_trading"] = (dirs[[f"{m}_dir" for m in frames]] != 0).sum(axis=1)
for name, df in frames.items():
    df = df.merge(dirs, on=["node", "target_date", "hour"], how="left", suffixes=("", "_x"))
    df = df.drop(columns=[c for c in df.columns if c.endswith("_dir_x")], errors="ignore")
    frames[name] = df

# ---------------- Per-model Top-20 / Top-50 ----------------
trades = {name: df[df["pred_direction"] != 0].sort_values("pnl", ascending=False)
          for name, df in frames.items()}
for name, t in trades.items():
    print(f"\n### {name} trades={len(t)}")
    print("Top20 pnl range:", t["pnl"].head(20).min(), "..", t["pnl"].head(20).max())
    print("Top20 sum:", t["pnl"].head(20).sum())
    print("Top50 sum:", t["pnl"].head(50).sum())
    print("total sum:", t["pnl"].sum())

# union top50
top20_sets = {name: set(t["pnl"].head(20).index) for name, t in trades.items()}
union_top50 = pd.concat([t.head(50) for t in trades.values()]).drop_duplicates(subset=["node", "target_date", "hour"]).sort_values("pnl", ascending=False).head(50)

# consolidated output frame
out_cols = ["node", "target_date", "hour", "action", "actual_da", "actual_rtpd", "actual_return",
            "pnl", "pred_direction", "confidence", "expected_return", "prob_return_positive",
            "rule_dir", "interpretable_dir", "catboost_dir", "agree_nz", "n_models_trading",
            "ret_z_hist", "ret_pct_hist", "spread_lag1_pct_node",
            "da_lag1", "rtpd_lag1", "spread_lag1", "da_lag2", "rtpd_lag2", "spread_lag2",
            "da_lag7", "rtpd_lag7", "spread_lag7",
            "spread_mean7", "spread_std7", "spread_mean14", "spread_std14", "spread_mean30", "spread_std30",
            "spread_day_std_lag1", "spread_day_range_lag1", "spread_day_max_lag1",
            "da_day_mean_lag1", "rtpd_day_mean_lag1", "spread_day_mean_lag1",
            "load_actual_lag1", "load_actual_day_mean_lag1", "load_2da_forecast", "load_peak_flag",
            "t2m_lag1", "ssrd_lag1", "wind100_lag1",
            "peer_da_lag1", "peer_rtpd_lag1", "peer_spread_lag1",
            "dow", "month", "is_holiday", "solar_flag", "zone"]

def build_table(df, topn):
    d = df.sort_values("pnl", ascending=False).head(topn)
    return d[out_cols]

per_model_top20 = {name: build_table(t, 20) for name, t in trades.items()}
per_model_top50 = {name: build_table(t, 50) for name, t in trades.items()}
merged_top50 = union_top50[out_cols]

# ---------------- Save ----
os.makedirs(os.path.join(DATA, "stage3"), exist_ok=True)
merged_top50.to_csv(os.path.join(DATA, "stage3", "top_profit_events.csv"), index=False, encoding="utf-8-sig")
for name, t in per_model_top50.items():
    t.to_csv(os.path.join(DATA, "stage3", f"top50_{name}.csv"), index=False, encoding="utf-8-sig")

# ---------------- Aggregate diagnostics ----------------
print("\n========== SUMMARY ==========")
agg = {}
for name, t in trades.items():
    total = t["pnl"].sum()
    agg[name] = {
        "n_trades": len(t),
        "total_pnl": total,
        "top20_sum": t["pnl"].head(20).sum(),
        "top50_sum": t["pnl"].head(50).sum(),
        "top20_share": t["pnl"].head(20).sum() / total if total else np.nan,
        "top50_share": t["pnl"].head(50).sum() / total if total else np.nan,
        "n_top20_events_unique_dates": t["pnl"].head(20).index.nunique() if False else t.head(20)["target_date"].nunique(),
    }
    print(name, agg[name])

# merged top50 concentration vs union total (union of all profitable trades? or total trades pnl)
merged_trades = pd.concat([t for t in trades.values()]).drop_duplicates(subset=["node", "target_date", "hour"])
merged_trades["pnl"] = np.where(merged_trades["pred_direction"] > 0, merged_trades["actual_return"],
                     np.where(merged_trades["pred_direction"] < 0, -merged_trades["actual_return"], 0.0))
mt = merged_trades[merged_trades["pred_direction"] != 0]
print("\nUnion trade set (dedup by node/date/hour):", len(mt), "total pnl:", mt["pnl"].sum())
print("Union top20 share:", mt["pnl"].nlargest(20).sum() / mt["pnl"].sum())
print("Union top50 share:", mt["pnl"].nlargest(50).sum() / mt["pnl"].sum())
print("Union top100 share:", mt["pnl"].nlargest(100).sum() / mt["pnl"].sum())

# How many merged top50 have extreme z? confidence distribution?
print("\nMerged top50 stats:")
print(merged_top50[["pnl", "confidence", "ret_z_hist", "ret_pct_hist", "agree_nz"]].describe().round(3).to_string())

# Save diagnostics json
import json
with open(os.path.join(DATA, "stage3", "top_profit_diagnostics.json"), "w", encoding="utf-8") as f:
    json.dump(agg, f, ensure_ascii=False, indent=2, default=str)

print("\nSaved: code/data/stage3/top_profit_events.csv, top50_{rule,interpretable,catboost}.csv, top_profit_diagnostics.json")
