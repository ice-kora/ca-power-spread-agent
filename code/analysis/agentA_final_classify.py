# -*- coding: utf-8 -*-
"""Final classification of top-loss events with a transparent, defensible decision tree.
Primary signals (specific to each trade, discriminating vs baseline):
  conflict        : rule vs ML models predict OPPOSITE directions
  extreme_state   : spread_lag1 at historical extreme (<p5 or >p95) for that node-hour
  low_sample      : hist_n < 200 (ELCA)
  broken_buy      : executing model is interpretable/catboost BUY on CONTROLX, after that
                    model's CONTROLX BUY walk-forward cumulative mean PnL dropped below -50
Secondary context (not discriminating alone, noted as caveat): vol elevation, ext30.
Residual -> C (tail draw without specific warning) or B (beyond-history + calm pre-trade).
"""
import pandas as pd, numpy as np, os

BASE = r'D:\code\pyCode\CA-电力交易预测\code'
DATA = os.path.join(BASE, 'data')
OUT_CSV = os.path.join(DATA, 'stage3', 'top_loss_events.csv')

preds = {}
for name in ['rule', 'interpretable', 'catboost']:
    df = pd.read_csv(os.path.join(DATA, 'predictions_%s.csv' % name))
    df['target_date'] = pd.to_datetime(df['target_date'])
    df['pnl'] = 0.0
    df.loc[df['pred_direction'] > 0, 'pnl'] = df.loc[df['pred_direction'] > 0, 'actual_return']
    df.loc[df['pred_direction'] < 0, 'pnl'] = -df.loc[df['pred_direction'] < 0, 'actual_return']
    preds[name] = df

# ---- walk-forward: first date each model's CONTROLX BUY cum_mean < -50 ----
buy_break_date = {}
for name in ['interpretable', 'catboost']:
    tr = preds[name][(preds[name]['pred_direction'] < 0) & (preds[name]['node'] == 'CONTROLX_1_N001')].copy()
    tr = tr.sort_values('target_date')
    tr['cm'] = tr['pnl'].cumsum() / np.arange(1, len(tr) + 1)
    neg = tr[tr['cm'] < -50]
    d = neg['target_date'].iloc[0] if len(neg) else None
    buy_break_date[name] = d
    print('%s CONTROLX BUY first cum_mean<-50: %s' % (name, d.date() if d is not None else None))

t = pd.read_csv(OUT_CSV)
t['target_date'] = pd.to_datetime(t['target_date'])

def classify(r):
    worst = r['worst_model']; action = r['action']
    conflict = r['agreement'] == 'conflict'
    lag1pct = r['lag1_pct']
    extreme_state = bool(pd.notna(lag1pct) and (lag1pct < 0.05 or lag1pct > 0.95))
    low_sample = r['hist_n'] < 200
    broken = False
    if worst in ('interpretable', 'catboost') and action == 'BUY':
        bd = buy_break_date[worst]
        broken = (bd is not None) and (r['target_date'] >= bd)
    # realized beyond historical envelope?
    beyond = bool(pd.notna(r['realized_pct']) and (r['realized_pct'] <= 0.001 or r['realized_pct'] >= 0.999))
    # context
    vol_ratio = r['vol30'] / r['hist_std'] if pd.notna(r['vol30']) and pd.notna(r['hist_std']) and r['hist_std'] > 0 else np.nan

    reasons = []
    if conflict:
        reasons.append('conflict')
    if extreme_state:
        reasons.append('extreme_state(lag1pct=%.2f)' % lag1pct)
    if low_sample:
        reasons.append('low_sample(n=%d)' % r['hist_n'])
    if broken:
        reasons.append('broken_buy(%s)' % worst)
    if reasons:
        return 'A', '; '.join(reasons)
    # residual
    if beyond:
        return 'B', 'beyond_history(realized_pct=%.3f)' % r['realized_pct']
    return 'C', 'no_specific_pre_trade_warning(vol_ratio=%.2f)' % (vol_ratio if pd.notna(vol_ratio) else -1)

rows = []
for _, r in t.iterrows():
    tp, reason = classify(r)
    rows.append({'target_date': str(r['target_date'].date()), 'hour': r['hour'],
                 'node': r['node'], 'action': r['action'], 'worst_model': r['worst_model'],
                 'worst_pnl': round(r['worst_pnl'], 1), 'agreement': r['agreement'],
                 'conflict': r['agreement'] == 'conflict', 'extreme_state': bool(
                     pd.notna(r['lag1_pct']) and (r['lag1_pct'] < 0.05 or r['lag1_pct'] > 0.95)),
                 'type': tp, 'reason': reason})
res = pd.DataFrame(rows)
print()
print('=== Final classification ===')
print(res['type'].value_counts().to_string())
print()
print(res.to_string(index=False))
res.to_csv(os.path.join(DATA, 'stage3', 'top_loss_classification.csv'), index=False)
print('saved classification csv')
