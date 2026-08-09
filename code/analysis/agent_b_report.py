# -*- coding: utf-8 -*-
"""Agent B — final report-support computations."""
import pandas as pd
import numpy as np
import os, json

BASE = r"D:\code\pyCode\CA-电力交易预测"
DATA = os.path.join(BASE, "code", "data")
canon = pd.read_parquet(os.path.join(DATA, "canonical.parquet"))
canon["target_date"] = pd.to_datetime(canon["target_date"])
MODELS = ["rule", "interpretable", "catboost"]

frames = {}
for name in MODELS:
    df = pd.read_csv(os.path.join(DATA, f"predictions_{name}.csv"))
    df["target_date"] = pd.to_datetime(df["target_date"])
    df["pnl"] = np.where(df["pred_direction"] > 0, df["actual_return"],
                 np.where(df["pred_direction"] < 0, -df["actual_return"], 0.0))
    frames[name] = df

top = pd.read_csv(os.path.join(DATA, "stage3", "top_profit_events.csv"))
top["target_date"] = pd.to_datetime(top["target_date"])

# ---------------- Winning model attribution ----------------
key = list(zip(top["node"], top["target_date"], top["hour"]))
win_model = []
win_pnl = []
for i, (n, d, h) in enumerate(key):
    best_m, best_p = None, -np.inf
    for name in MODELS:
        row = frames[name].set_index(["node", "target_date", "hour"]).loc[(n, d, h)]
        p = row["pnl"]
        if p > best_p:
            best_p, best_m = p, name
    win_model.append(best_m)
    win_pnl.append(best_p)
top["win_model"] = win_model
print("Winning model attribution of merged top50:")
print(top.groupby("win_model")["pnl"].agg(["count", "sum"]).round(0).to_string())
print("\nSELL vs BUY by win_model:")
print(top.groupby(["win_model", "action"])["pnl"].agg(["count", "sum"]).round(0).to_string())

# ---------------- Episode grouping ----------------
print("\nEpisodes (consecutive-date clusters) in merged top50:")
top = top.sort_values("target_date")
ep = []
cur = []
prev = None
for _, r in top.iterrows():
    if prev is None or (r["target_date"] - prev).days <= 1:
        cur.append(r)
    else:
        ep.append(cur)
        cur = [r]
    prev = r["target_date"]
ep.append(cur)
for i, e in enumerate(ep):
    d0, d1 = e[0]["target_date"], e[-1]["target_date"]
    acts = pd.Series([x["action"] for x in e]).value_counts().to_dict()
    print(f"  Ep{i+1}: {d0.date()}..{d1.date()}  n={len(e)}  pnl={sum(x['pnl'] for x in e):,.0f}  action={acts}")

# ---------------- Rule model's own pnl on 06-30 ----------------
print("\nRule model on 2026-06-30 (DA=-1300 day):")
r30 = frames["rule"][(frames["rule"]["target_date"] == "2026-06-30") & (frames["rule"]["node"] == "CONTROLX_1_N001")]
print("  rule trades:", len(r30[r30['pred_direction'] != 0]), "pnl:", f"{r30['pnl'].sum():,.0f}",
      "  dir dist:", r30["pred_direction"].value_counts().to_dict())
print("  rule would have lost on the day the BUY models profited most.")

# ---------------- Rule model by direction ----------------
print("\nRule SELL vs BUY totals:")
tr = frames["rule"][frames["rule"]["pred_direction"] != 0].copy()
tr["action"] = np.where(tr["pred_direction"] > 0, "SELL", "BUY")
print(tr.groupby("action")["pnl"].agg(["count", "sum", "mean"]).round(1).to_string())

# ---------------- expected_return context for merged top50 ----------------
print("\nMerged top50 expected_return (winning model's view) stats:")
print(top["expected_return"].describe().round(1).to_string())
print("corr(expected_return, actual_return) in merged top50:",
      round(top["expected_return"].corr(top["actual_return"]), 3))
print("corr(expected_return, pnl):", round(top["expected_return"].corr(top["pnl"]), 3))

# ---------------- Confidence summary by win_model ----------------
print("\nConfidence by winning model:")
print(top.groupby("win_model")["confidence"].agg(["mean", "min", "max"]).round(3).to_string())

# ---------------- What fraction of rule's CONTROLX pnl comes from extreme-return rows ----------------
rc = frames["rule"][frames["rule"]["node"] == "CONTROLX_1_N001"]
ext = rc[rc["actual_return"].abs() > 500]
print("\nRule CONTROLX: total trades", len(rc[rc['pred_direction']!=0]),
      "pnl", f"{rc['pnl'].sum():,.0f}")
print("  extreme rows traded:", len(ext[ext['pred_direction']!=0]),
      "pnl from extreme:", f"{ext['pnl'].sum():,.0f}",
      f"share={ext['pnl'].sum()/rc['pnl'].sum():.3f}")

# ---------------- How many top50 events were 'regime-persistent' (spread_lag1 abs > 100) ----------------
print("\nPre-trade regime persistence in merged top50:")
print("  # with |spread_lag1| > 100:", int((top['spread_lag1'].abs() > 100).sum()))
print("  # with spread_std7 > 300:", int((top['spread_std7'] > 300).sum()))
print("  # with rtpd_day_mean_lag1 < -300:", int((top['rtpd_day_mean_lag1'] < -300).sum()))

# ---------------- Save enriched csv ----------------
top[["node","target_date","hour","action","pnl","win_model"]].to_csv(
    os.path.join(DATA, "stage3", "top_profit_winmodel.csv"), index=False)
print("\nsaved top_profit_winmodel.csv")
