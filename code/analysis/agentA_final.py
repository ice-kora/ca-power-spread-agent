# -*- coding: utf-8 -*-
"""Generate docs/stage3/top_loss_event_analysis.md and refresh the CSV with type + expected_return."""
import pandas as pd, numpy as np, os

BASE = r'D:\code\pyCode\CA-电力交易预测\code'
DATA = os.path.join(BASE, 'data')
STAGE3 = os.path.join(DATA, 'stage3')
OUT_CSV = os.path.join(STAGE3, 'top_loss_events.csv')
OUT_MD = r'D:\code\pyCode\CA-电力交易预测\docs\stage3\top_loss_event_analysis.md'
os.makedirs(r'D:\code\pyCode\CA-电力交易预测\docs\stage3', exist_ok=True)
os.makedirs(STAGE3, exist_ok=True)

# ------- reload and recompute everything in one place -------
preds = {}
for name in ['rule', 'interpretable', 'catboost']:
    df = pd.read_csv(os.path.join(DATA, 'predictions_%s.csv' % name))
    df['target_date'] = pd.to_datetime(df['target_date'])
    df['pnl'] = 0.0
    df.loc[df['pred_direction'] > 0, 'pnl'] = df.loc[df['pred_direction'] > 0, 'actual_return']
    df.loc[df['pred_direction'] < 0, 'pnl'] = -df.loc[df['pred_direction'] < 0, 'actual_return']
    preds[name] = df

# per-model top losses
per_top20, per_top50 = {}, {}
for name, df in preds.items():
    tr = df[df['pred_direction'] != 0]
    per_top20[name] = tr.nsmallest(20, 'pnl')
    per_top50[name] = tr.nsmallest(50, 'pnl')

union = pd.concat(per_top50.values())
u = union.sort_values('pnl').drop_duplicates(subset=['node', 'target_date', 'hour']).reset_index(drop=True)
merged_top50 = u.head(50).copy()

# walk-forward buy-break dates
buy_break_date = {}
for name in ['interpretable', 'catboost']:
    tr = preds[name][(preds[name]['pred_direction'] < 0) & (preds[name]['node'] == 'CONTROLX_1_N001')].copy()
    tr = tr.sort_values('target_date')
    tr['cm'] = tr['pnl'].cumsum() / np.arange(1, len(tr) + 1)
    neg = tr[tr['cm'] < -50]
    buy_break_date[name] = neg['target_date'].iloc[0] if len(neg) else None

master = pd.read_csv(os.path.join(DATA, 'master.csv'), parse_dates=['date'])
master = master.drop_duplicates(subset=['node', 'date', 'hour']).reset_index(drop=True)
canon = pd.read_parquet(os.path.join(DATA, 'canonical.parquet'))
canon['target_date'] = pd.to_datetime(canon['target_date'])
canon_rows = canon.set_index(['node', 'target_date', 'hour'])

FEAT_COLS = ['spread_lag1','spread_lag2','spread_lag7','spread_mean7','spread_std7',
             'spread_mean14','spread_std14','spread_mean30','spread_std30',
             'spread_day_std_lag1','spread_day_range_lag1','spread_day_max_lag1',
             'da_day_mean_lag1','rtpd_day_mean_lag1','spread_day_mean_lag1',
             'load_actual_lag1','load_actual_day_mean_lag1','load_2da_forecast',
             'load_peak_flag','t2m_lag1','ssrd_lag1','wind100_lag1',
             'peer_spread_lag1','peer_da_lag1','peer_rtpd_lag1','dow','month']

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
        'hist_n': n, 'hist_median': s.median(), 'hist_p90': s.quantile(.90),
        'hist_p95': s.quantile(.95), 'hist_p99': s.quantile(.99), 'hist_p995': s.quantile(.995),
        'hist_min': s.min(), 'hist_max': s.max(), 'hist_abs_p95': s.abs().quantile(.95),
        'cvar95': s.nsmallest(n95).mean(), 'cvar99': s.nsmallest(n99).mean(), 'hist_std': s.std(),
    }
    out['vol7'] = rec7['spread'].std() if len(rec7) >= 3 else np.nan
    out['vol30'] = rec30['spread'].std() if len(rec30) >= 5 else np.nan
    thr = out['hist_abs_p95']
    out['extreme_count_30'] = int((rec30['spread'].abs() > thr).sum())
    out['max_abs_30'] = rec30['spread'].abs().max() if len(rec30) else np.nan
    return out

rows_out = []
for _, r in merged_top50.iterrows():
    node, td, hour = r['node'], r['target_date'], int(r['hour'])
    row = {'node': node, 'target_date': str(td), 'hour': hour}
    pidx = {name: preds[name].set_index(['node','target_date','hour']) for name in preds}
    for name in ['rule','interpretable','catboost']:
        try:
            pr = pidx[name].loc[(node, pd.Timestamp(td), hour)]
        except KeyError:
            row['%s_dir' % name] = np.nan; row['%s_conf' % name] = np.nan
            row['%s_prob' % name] = np.nan; row['%s_pnl' % name] = np.nan
            row['%s_er' % name] = np.nan
            continue
        row['%s_dir' % name] = pr['pred_direction']; row['%s_conf' % name] = pr['confidence']
        row['%s_prob' % name] = pr['prob_return_positive']; row['%s_pnl' % name] = pr['pnl']
        row['%s_er' % name] = pr['expected_return']
    pnl_cols = [row['%s_pnl' % n] for n in ['rule','interpretable','catboost']]
    worst_idx = int(np.nanargmin(pnl_cols))
    worst_model = ['rule','interpretable','catboost'][worst_idx]
    row['worst_model'] = worst_model; row['worst_pnl'] = pnl_cols[worst_idx]
    row['action'] = 'SELL' if row['%s_dir' % worst_model] > 0 else 'BUY'
    row['worst_er'] = row['%s_er' % worst_model]
    row['worst_conf'] = row['%s_conf' % worst_model]
    c = canon_rows.loc[(node, pd.Timestamp(td), hour)]
    row['actual_da'] = c['actual_da']; row['actual_rtpd'] = c['actual_rtpd']; row['actual_return'] = c['actual_return']
    for col in FEAT_COLS:
        row[col] = c[col]
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
    node_hist = master[(master['node']==node) & (master['date'] <= pd.Timestamp(td) - pd.Timedelta(days=2)) & master['spread'].notna()]
    row['node_bias_mean'] = node_hist['spread'].mean() if len(node_hist) else np.nan
    node_win = master[(master['node']==node) &
                      (master['date'] >= pd.Timestamp(td) - pd.Timedelta(days=7)) &
                      (master['date'] <= pd.Timestamp(td) - pd.Timedelta(days=2))]
    row['node_vol7'] = node_win['spread'].std() if len(node_win) else np.nan
    row['node_max_abs_prev7'] = node_win['spread'].abs().max() if len(node_win) else np.nan
    dirs = [row['%s_dir' % n] for n in ['rule','interpretable','catboost']]
    nonzero = [d for d in dirs if pd.notna(d) and d != 0]
    n_nz = len(nonzero)
    if n_nz == 0:
        row['agreement'] = 'none'
    else:
        npos = sum(1 for d in nonzero if d > 0); nneg = sum(1 for d in nonzero if d < 0)
        row['agreement'] = 'conflict' if (npos > 0 and nneg > 0) else ('%d/3' % n_nz)
    rows_out.append(row)

tbl = pd.DataFrame(rows_out)

# -------- classification --------
def classify(r):
    worst = r['worst_model']; action = r['action']
    conflict = r['agreement'] == 'conflict'
    lag1pct = r['lag1_pct']
    extreme_state = bool(pd.notna(lag1pct) and (lag1pct < 0.05 or lag1pct > 0.95))
    low_sample = r['hist_n'] < 200
    broken = bool(worst in ('interpretable', 'catboost') and action == 'BUY' and
                  buy_break_date.get(worst) is not None and pd.Timestamp(r['target_date']) >= buy_break_date[worst])
    beyond = bool(pd.notna(r['realized_pct']) and (r['realized_pct'] <= 0.001 or r['realized_pct'] >= 0.999))
    vol_ratio = r['vol30'] / r['hist_std'] if pd.notna(r['vol30']) and pd.notna(r['hist_std']) and r['hist_std'] > 0 else np.nan
    ext30 = r['extreme_count_30'] if pd.notna(r['extreme_count_30']) else 0
    prev7 = r['node_max_abs_prev7']
    calm = (not pd.notna(vol_ratio)) or (vol_ratio < 1.5 and ext30 < 3 and (not pd.notna(prev7) or prev7 < 800))
    reasons = []
    if conflict:
        reasons.append('conflict')
    if extreme_state:
        reasons.append('extreme_state(lag1pct=%.2f)' % lag1pct)
    if low_sample:
        reasons.append('low_sample(n=%d)' % r['hist_n'])
    if broken:
        reasons.append('broken_buy(%s, conf=%.2f)' % (worst, r['worst_conf']))
    if reasons:
        return 'A', '; '.join(reasons)
    if beyond and calm:
        return 'B', 'beyond_history(realized_pct=%.3f), calm_pre_trade' % r['realized_pct']
    return 'C', 'tail_draw_no_specific_warning'

tbl['type'] = [classify(r)[0] for _, r in tbl.iterrows()]
tbl['type_reason'] = [classify(r)[1] for _, r in tbl.iterrows()]
# ensure target_date string without time
tbl['target_date'] = pd.to_datetime(tbl['target_date']).dt.strftime('%Y-%m-%d')
tbl['dow'] = pd.to_datetime(tbl['target_date']).dt.dayofweek
tbl['month'] = pd.to_datetime(tbl['target_date']).dt.month
tbl.to_csv(OUT_CSV, index=False)
print('CSV saved with type:', OUT_CSV, 'rows:', len(tbl))
print(tbl['type'].value_counts().to_dict())
