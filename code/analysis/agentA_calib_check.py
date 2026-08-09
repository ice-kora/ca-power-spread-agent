# -*- coding: utf-8 -*-
"""Walk-forward check: was interpretable/catboost CONTROLX BUY a pre-detectable calibration failure?
Also compute how many top-loss rows had specific pre-trade flags."""
import pandas as pd, numpy as np, os

BASE = r'D:\code\pyCode\CA-电力交易预测\code'
DATA = os.path.join(BASE, 'data')

preds = {}
for name in ['rule', 'interpretable', 'catboost']:
    df = pd.read_csv(os.path.join(DATA, 'predictions_%s.csv' % name))
    df['target_date'] = pd.to_datetime(df['target_date'])
    df['pnl'] = 0.0
    df.loc[df['pred_direction'] > 0, 'pnl'] = df.loc[df['pred_direction'] > 0, 'actual_return']
    df.loc[df['pred_direction'] < 0, 'pnl'] = -df.loc[df['pred_direction'] < 0, 'actual_return']
    preds[name] = df

for name in ['interpretable', 'catboost']:
    tr = preds[name][(preds[name]['pred_direction'] < 0) & (preds[name]['node'] == 'CONTROLX_1_N001')].copy()
    tr = tr.sort_values('target_date')
    tr['cum_pnl'] = tr['pnl'].cumsum()
    tr['cum_n'] = np.arange(1, len(tr) + 1)
    tr['cum_mean'] = tr['cum_pnl'] / tr['cum_n']
    tr['cum_win'] = tr['pnl'].cumsum() / tr['pnl'].abs().cumsum()  # pnl>0 weighted
    print('=== %s: CONTROLX BUY trades, cumulative mean PnL by date ===' % name)
    # print key checkpoints
    for _, r in tr.iloc[::max(1, len(tr)//15)].iterrows():
        print('  %s n=%3d cum_mean=%7.1f cum_sum=%10.1f' % (str(r['target_date'].date()), r['cum_n'], r['cum_mean'], r['cum_pnl']))
    print('  final: n=%d cum_mean=%.1f cum_sum=%.1f' % (len(tr), tr['cum_mean'].iloc[-1], tr['cum_pnl'].iloc[-1]))
    # when did cumulative mean first go below -20 and stay?
    neg = tr[tr['cum_mean'] < -20]
    if len(neg):
        print('  first date cum_mean<-20: %s (n=%d, cum_mean=%.1f)' % (str(neg['target_date'].iloc[0].date()), neg['cum_n'].iloc[0], neg['cum_mean'].iloc[0]))
    print()

# -------- which top-loss rows had which SPECIFIC flags --------
t = pd.read_csv(os.path.join(DATA, 'stage3', 'top_loss_events.csv'))
t['target_date'] = pd.to_datetime(t['target_date'])
flags = []
for _, r in t.iterrows():
    conflict = r['agreement'] == 'conflict'
    n_model = int(str(r['agreement']).split('/')[0]) if '/' in str(r['agreement']) else 0
    only1 = n_model == 1
    lag1pct = r['lag1_pct']
    extreme_state = pd.notna(lag1pct) and (lag1pct < 0.05 or lag1pct > 0.95)
    worst = r['worst_model']
    # is the executing model a CONTROLX BUY calibration failure?
    calib = (worst in ('interpretable','catboost')) and (r['action'] == 'BUY')
    flags.append({'target_date': str(r['target_date'].date()), 'hour': r['hour'], 'action': r['action'],
                  'worst_model': worst, 'worst_pnl': r['worst_pnl'], 'agreement': r['agreement'],
                  'conflict': conflict, 'only1': only1, 'extreme_state': extreme_state,
                  'calib_buy': calib})
f = pd.DataFrame(flags)
print('=== Specific pre-trade flags in top-50 losses ===')
for c in ['conflict', 'only1', 'extreme_state', 'calib_buy']:
    print('  %-12s: %d / 50' % (c, int(f[c].sum())))
print('  conflict OR extreme_state OR calib_buy: %d / 50' % int((f['conflict']|f['extreme_state']|f['calib_buy']).sum()))
print()
print(f.to_string(index=False))
