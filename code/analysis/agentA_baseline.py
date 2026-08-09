# -*- coding: utf-8 -*-
"""Baseline distribution of pre-trade risk signals over the whole test window,
to determine which signals discriminate the top-loss trades from the general population."""
import pandas as pd, numpy as np, os

BASE = r'D:\code\pyCode\CA-电力交易预测\code'
DATA = os.path.join(BASE, 'data')

master = pd.read_csv(os.path.join(DATA, 'master.csv'), parse_dates=['date'])
master = master.drop_duplicates(subset=['node', 'date', 'hour']).reset_index(drop=True)

# same hist_stats as main script
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
        'hist_n': n, 'hist_std': s.std(),
        'hist_p95': s.quantile(.95), 'hist_p99': s.quantile(.99),
        'hist_abs_p95': s.abs().quantile(.95),
        'cvar95': s.nsmallest(n95).mean(), 'cvar99': s.nsmallest(n99).mean(),
        'hist_min': s.min(), 'hist_max': s.max(),
    }
    out['vol7'] = rec7['spread'].std() if len(rec7) >= 3 else np.nan
    out['vol30'] = rec30['spread'].std() if len(rec30) >= 5 else np.nan
    thr = out['hist_abs_p95']
    out['extreme_count_30'] = int((rec30['spread'].abs() > thr).sum())
    return out

# base population: all test rows for CONTROLX (the node with the losses)
test_dates = sorted(master[(master['node']=='CONTROLX_1_N001') & (master['date']>='2026-06-02')]['date'].unique())
rows = []
for td in test_dates:
    for hour in range(1, 25):
        hs = hist_stats('CONTROLX_1_N001', td, hour)
        if hs is None:
            continue
        # lag1_pct
        s_full = master[(master['node']=='CONTROLX_1_N001') & (master['hour']==hour) &
                        (master['date'] <= pd.Timestamp(td) - pd.Timedelta(days=2)) & master['spread'].notna()]['spread']
        lag1 = master[(master['node']=='CONTROLX_1_N001') & (master['hour']==hour) & (master['date']==pd.Timestamp(td)-pd.Timedelta(days=2))]['spread']
        lag1 = float(lag1.iloc[0]) if len(lag1) else np.nan
        lag1_pct = float((s_full < lag1).mean()) if pd.notna(lag1) and len(s_full) else np.nan
        rows.append({'td': td, 'hour': hour, 'ext30': hs['extreme_count_30'],
                     'vol30': hs['vol30'], 'hist_std': hs['hist_std'],
                     'vol_ratio': hs['vol30']/hs['hist_std'] if hs['hist_std']>0 else np.nan,
                     'lag1_pct': lag1_pct,
                     'hist_p99': hs['hist_p99'], 'cvar99': hs['cvar99']})
base = pd.DataFrame(rows)
print('=== CONTROLX test-window baseline (all node-hours) ===')
for c in ['ext30','vol_ratio','lag1_pct','hist_p99','cvar99']:
    print(c, base[c].describe().round(3).to_dict())
print()
# thresholds and hit rates
for thr, name in [(3,'ext30>=3'), (5,'ext30>=5'), (8,'ext30>=8'), (2,'ext30>=2')]:
    print('%s: %.3f of all CONTROLX test rows' % (name, (base['ext30']>=thr).mean()))
for thr, name in [(1.5,'vol_ratio>=1.5'), (2.0,'vol_ratio>=2.0'), (2.5,'vol_ratio>=2.5')]:
    print('%s: %.3f' % (name, (base['vol_ratio']>=thr).mean()))
print('lag1_pct<0.05 or >0.95: %.3f' % ((base['lag1_pct']<0.05)|(base['lag1_pct']>0.95)).mean())
print()
# now the top-loss rows comparison
t = pd.read_csv(os.path.join(DATA, 'stage3', 'top_loss_events.csv'))
t['target_date'] = pd.to_datetime(t['target_date'])
t['td'] = pd.to_datetime(t['target_date'])
tl = t.merge(base, on=['td','hour'], how='left', suffixes=('','_base'))
print('=== top-loss rows: distribution of signals ===')
for c in ['ext30','vol_ratio','lag1_pct']:
    print(c, tl[c].describe().round(3).to_dict())
print('ext30>=3 share in top-loss:', (tl['ext30']>=3).mean().round(3))
print('vol_ratio>=1.5 share in top-loss:', (tl['vol_ratio']>=1.5).mean().round(3))
print('lag1_pct extreme share in top-loss:', ((tl['lag1_pct']<0.05)|(tl['lag1_pct']>0.95)).mean().round(3))
