# -*- coding: utf-8 -*-
"""Agent A: extreme loss event analysis for CAISO spread trading."""
import pandas as pd, numpy as np, os

BASE = r'D:\code\pyCode\CA-电力交易预测\code'
DATA = os.path.join(BASE, 'data')
OUT_CSV = os.path.join(DATA, 'stage3', 'top_loss_events.csv')
OUT_MD = r'D:\code\pyCode\CA-电力交易预测\docs\stage3\top_loss_event_analysis.md'

pd.set_option('display.width', 250)

# ---------------- load ----------------
preds = {}
for name in ['rule', 'interpretable', 'catboost']:
    df = pd.read_csv(os.path.join(DATA, 'predictions_%s.csv' % name))
    df['target_date'] = pd.to_datetime(df['target_date'])
    df['pnl'] = 0.0
    df.loc[df['pred_direction'] > 0, 'pnl'] = df.loc[df['pred_direction'] > 0, 'actual_return']
    df.loc[df['pred_direction'] < 0, 'pnl'] = -df.loc[df['pred_direction'] < 0, 'actual_return']
    preds[name] = df

canon = pd.read_parquet(os.path.join(DATA, 'canonical.parquet'))
canon['target_date'] = pd.to_datetime(canon['target_date'])
master = pd.read_csv(os.path.join(DATA, 'master.csv'), parse_dates=['date'])
master = master.drop_duplicates(subset=['node', 'date', 'hour']).reset_index(drop=True)

canon_rows = canon.set_index(['node', 'target_date', 'hour'])

FEAT_COLS = ['spread_lag1','spread_lag2','spread_lag7','spread_mean7','spread_std7',
             'spread_mean14','spread_std14','spread_mean30','spread_std30',
             'spread_day_std_lag1','spread_day_range_lag1','spread_day_max_lag1',
             'da_day_mean_lag1','rtpd_day_mean_lag1','spread_day_mean_lag1',
             'load_actual_lag1','load_actual_day_mean_lag1','load_2da_forecast',
             'load_peak_flag','t2m_lag1','ssrd_lag1','wind100_lag1',
             'peer_spread_lag1','peer_da_lag1','peer_rtpd_lag1','dow','month']

# ---------------- per-model top losses ----------------
per_model_top = {}
for name, df in preds.items():
    tr = df[df['pred_direction'] != 0].copy()
    per_model_top[name] = tr.nsmallest(50, 'pnl')

union = pd.concat(per_model_top.values())
u = union.sort_values('pnl').drop_duplicates(subset=['node','target_date','hour']).reset_index(drop=True)
merged_top50 = u.head(50).copy()

# ---------------- historical stats ----------------
def hist_stats(node, target_date, hour):
    td = pd.Timestamp(target_date)
    h = master[(master['node']==node) & (master['hour']==hour) &
               (master['date'] <= td - pd.Timedelta(days=2)) & master['spread'].notna()]
    n = len(h)
    if n == 0:
        return None
    s = h['spread']
    n95 = max(1, int(0.05*n)); n99 = max(1, int(0.01*n))
    rec30 = h[h['date'] >= td - pd.Timedelta(days=32)]
    rec7 = h[h['date'] >= td - pd.Timedelta(days=9)]
    out = {
        'hist_n': n,
        'hist_median': s.median(),
        'hist_p90': s.quantile(.90), 'hist_p95': s.quantile(.95),
        'hist_p99': s.quantile(.99), 'hist_p995': s.quantile(.995),
        'hist_min': s.min(), 'hist_max': s.max(),
        'hist_abs_p95': s.abs().quantile(.95),
        'cvar95': s.nsmallest(n95).mean(), 'cvar99': s.nsmallest(n99).mean(),
        'hist_std': s.std(),
    }
    out['vol7'] = rec7['spread'].std() if len(rec7) >= 3 else np.nan
    out['vol30'] = rec30['spread'].std() if len(rec30) >= 5 else np.nan
    thr = out['hist_abs_p95']
    out['extreme_count_30'] = int((rec30['spread'].abs() > thr).sum())
    out['max_abs_30'] = rec30['spread'].abs().max() if len(rec30) else np.nan
    return out

# ---------------- assemble per-row details ----------------
rows_out = []
for _, r in merged_top50.iterrows():
    node, td, hour = r['node'], r['target_date'], int(r['hour'])
    row = {'node': node, 'target_date': str(td), 'hour': hour}
    # per-model outputs
    pidx = {}
    for name in ['rule','interpretable','catboost']:
        pidx[name] = preds[name].set_index(['node','target_date','hour'])
    for name in ['rule','interpretable','catboost']:
        try:
            pr = pidx[name].loc[(node, pd.Timestamp(td), hour)]
        except KeyError:
            row['%s_dir' % name] = np.nan; row['%s_conf' % name] = np.nan
            row['%s_prob' % name] = np.nan; row['%s_pnl' % name] = np.nan
            continue
        row['%s_dir' % name] = pr['pred_direction']; row['%s_conf' % name] = pr['confidence']
        row['%s_prob' % name] = pr['prob_return_positive']; row['%s_pnl' % name] = pr['pnl']
    # worst model
    pnl_cols = [row['%s_pnl' % n] for n in ['rule','interpretable','catboost']]
    worst_idx = int(np.nanargmin(pnl_cols))
    worst_model = ['rule','interpretable','catboost'][worst_idx]
    row['worst_model'] = worst_model
    row['worst_pnl'] = pnl_cols[worst_idx]
    row['action'] = 'SELL' if row['%s_dir' % worst_model] > 0 else 'BUY'
    # actuals
    c = canon_rows.loc[(node, pd.Timestamp(td), hour)]
    row['actual_da'] = c['actual_da']; row['actual_rtpd'] = c['actual_rtpd']
    row['actual_return'] = c['actual_return']
    # pre-trade features
    for col in FEAT_COLS:
        row[col] = c[col]
    # historical stats
    hs = hist_stats(node, td, hour)
    for kk, vv in (hs or {}).items():
        row[kk] = vv
    if hs:
        s_full = master[(master['node']==node) & (master['hour']==hour) &
                        (master['date'] <= pd.Timestamp(td) - pd.Timedelta(days=2)) & master['spread'].notna()]['spread']
        row['lag1_pct'] = float((s_full < row['spread_lag1']).mean()) if pd.notna(row['spread_lag1']) and len(s_full) else np.nan
        row['realized_pct'] = float((s_full < row['actual_return']).mean()) if len(s_full) else np.nan
    else:
        row['lag1_pct'] = np.nan; row['realized_pct'] = np.nan
    # node-level recent regime (all hours)
    node_hist = master[(master['node']==node) & (master['date'] <= pd.Timestamp(td) - pd.Timedelta(days=2)) & master['spread'].notna()]
    row['node_bias_mean'] = node_hist['spread'].mean() if len(node_hist) else np.nan
    node_win = master[(master['node']==node) &
                      (master['date'] >= pd.Timestamp(td) - pd.Timedelta(days=7)) &
                      (master['date'] <= pd.Timestamp(td) - pd.Timedelta(days=2))]
    row['node_vol7'] = node_win['spread'].std() if len(node_win) else np.nan
    row['node_max_abs_prev7'] = node_win['spread'].abs().max() if len(node_win) else np.nan
    # agreement
    dirs = [row['%s_dir' % n] for n in ['rule','interpretable','catboost']]
    nonzero = [d for d in dirs if pd.notna(d) and d != 0]
    n_nz = len(nonzero)
    if n_nz == 0:
        row['agreement'] = 'none'
    else:
        npos = sum(1 for d in nonzero if d > 0); nneg = sum(1 for d in nonzero if d < 0)
        if npos > 0 and nneg > 0:
            row['agreement'] = 'conflict'
        else:
            row['agreement'] = '%d/3' % n_nz
    rows_out.append(row)

tbl = pd.DataFrame(rows_out)
tbl['dow'] = pd.to_datetime(tbl['target_date']).dt.dayofweek
tbl['month'] = pd.to_datetime(tbl['target_date']).dt.month
for c in ['worst_pnl','actual_da','actual_rtpd','actual_return','spread_lag1','spread_lag2',
          'spread_lag7','spread_mean7','spread_std7','spread_mean14','spread_std14',
          'spread_mean30','spread_std30','da_day_mean_lag1','load_2da_forecast',
          't2m_lag1','ssrd_lag1','wind100_lag1','hist_p95','hist_p99','hist_p995',
          'hist_max','hist_min','cvar95','cvar99','hist_std','vol7','vol30',
          'lag1_pct','realized_pct','node_vol7','node_max_abs_prev7','node_bias_mean','hist_n']:
    if c in tbl.columns:
        tbl[c] = pd.to_numeric(tbl[c], errors='coerce')

tbl.to_csv(OUT_CSV, index=False)
print('saved csv ->', OUT_CSV, 'rows:', len(tbl))
print(tbl[['node','target_date','hour','action','worst_model','worst_pnl','agreement','actual_return']].to_string())
