# -*- coding: utf-8 -*-
"""Refined final classification + aggregation stats for the report."""
import pandas as pd, numpy as np, os, json

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

def node_prev7_max(node, td):
    w = master[(master['node'] == node) & (master['date'] >= pd.Timestamp(td) - pd.Timedelta(days=7)) &
               (master['date'] <= pd.Timestamp(td) - pd.Timedelta(days=2))]
    return w['spread'].abs().max() if len(w) else np.nan

t = pd.read_csv(OUT_CSV)
t['target_date'] = pd.to_datetime(t['target_date'])

def classify(r):
    worst = r['worst_model']; action = r['action']
    conflict = r['agreement'] == 'conflict'
    lag1pct = r['lag1_pct']
    extreme_state = bool(pd.notna(lag1pct) and (lag1pct < 0.05 or lag1pct > 0.95))
    low_sample = r['hist_n'] < 200
    broken = bool(worst in ('interpretable', 'catboost') and action == 'BUY' and
                  buy_break_date.get(worst) is not None and r['target_date'] >= buy_break_date[worst])
    beyond = bool(pd.notna(r['realized_pct']) and (r['realized_pct'] <= 0.001 or r['realized_pct'] >= 0.999))
    vol_ratio = r['vol30'] / r['hist_std'] if pd.notna(r['vol30']) and pd.notna(r['hist_std']) and r['hist_std'] > 0 else np.nan
    ext30 = r['extreme_count_30'] if pd.notna(r['extreme_count_30']) else 0
    prev7 = node_prev7_max(r['node'], r['target_date'])
    calm = (not pd.notna(vol_ratio)) or (vol_ratio < 1.5 and ext30 < 3 and (not pd.notna(prev7) or prev7 < 800))

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
    if beyond and calm:
        return 'B', 'beyond_history(realized_pct=%.3f), calm_pre_trade' % r['realized_pct']
    return 'C', 'tail_draw_without_specific_warning(vol_ratio=%.2f, ext30=%d)' % (vol_ratio if pd.notna(vol_ratio) else -1, ext30)

rows = []
for _, r in t.iterrows():
    tp, reason = classify(r)
    vol_ratio = r['vol30'] / r['hist_std'] if pd.notna(r['vol30']) and pd.notna(r['hist_std']) and r['hist_std'] > 0 else np.nan
    rows.append({
        'target_date': str(r['target_date'].date()), 'hour': int(r['hour']), 'node': r['node'],
        'action': r['action'], 'worst_model': r['worst_model'], 'worst_pnl': round(float(r['worst_pnl']), 2),
        'agreement': r['agreement'],
        'rule_dir': int(r['rule_dir']), 'interp_dir': int(r['interpretable_dir']), 'cat_dir': int(r['catboost_dir']),
        'actual_da': round(float(r['actual_da']), 2), 'actual_rtpd': round(float(r['actual_rtpd']), 2),
        'actual_return': round(float(r['actual_return']), 2),
        'expected_spread_worst': round(float(r['%s_conf' % r['worst_model']]), 2),
        'spread_lag1': round(float(r['spread_lag1']), 2), 'spread_lag2': round(float(r['spread_lag2']), 2),
        'spread_std7': round(float(r['spread_std7']), 2), 'spread_std14': round(float(r['spread_std14']), 2),
        'spread_std30': round(float(r['spread_std30']), 2),
        'hist_p95': round(float(r['hist_p95']), 2), 'hist_p99': round(float(r['hist_p99']), 2),
        'hist_p995': round(float(r['hist_p995']), 2), 'hist_max': round(float(r['hist_max']), 2),
        'hist_min': round(float(r['hist_min']), 2), 'cvar95': round(float(r['cvar95']), 2),
        'cvar99': round(float(r['cvar99']), 2), 'hist_std': round(float(r['hist_std']), 2),
        'vol7': round(float(r['vol7']), 2) if pd.notna(r['vol7']) else None,
        'vol30': round(float(r['vol30']), 2) if pd.notna(r['vol30']) else None,
        'vol_ratio': round(float(vol_ratio), 2) if pd.notna(vol_ratio) else None,
        'extreme_count_30': int(r['extreme_count_30']) if pd.notna(r['extreme_count_30']) else None,
        'lag1_pct': round(float(r['lag1_pct']), 3) if pd.notna(r['lag1_pct']) else None,
        'realized_pct': round(float(r['realized_pct']), 3) if pd.notna(r['realized_pct']) else None,
        'node_bias_mean': round(float(r['node_bias_mean']), 2) if pd.notna(r['node_bias_mean']) else None,
        'node_vol7': round(float(r['node_vol7']), 2) if pd.notna(r['node_vol7']) else None,
        'node_max_abs_prev7': round(float(r['node_max_abs_prev7']), 2) if pd.notna(r['node_max_abs_prev7']) else None,
        'hist_n': int(r['hist_n']) if pd.notna(r['hist_n']) else None,
        'type': tp, 'type_reason': reason,
    })
res = pd.DataFrame(rows)
print('=== Final refined classification ===')
print(res['type'].value_counts().to_string())
print()
print(res[['target_date','hour','action','worst_model','worst_pnl','type']].to_string(index=False))

# aggregation
print()
print('=== Aggregation ===')
# Top 20 vs Top 50
for n in [20, 50]:
    sub = res.head(n)
    print('Top %d: A=%d C=%d B=%d' % (n, (sub['type']=='A').sum(), (sub['type']=='C').sum(), (sub['type']=='B').sum()))
print()
print('By node:'); print(res.groupby('node')['worst_pnl'].agg(['count','sum','min']).round(1).to_string())
print()
print('By action:'); print(res.groupby('action')['worst_pnl'].agg(['count','sum','min']).round(1).to_string())
print()
print('By worst_model:'); print(res.groupby('worst_model')['worst_pnl'].agg(['count','sum','min']).round(1).to_string())
print()
print('By agreement:'); print(res.groupby('agreement')['worst_pnl'].agg(['count','sum','min']).round(1).to_string())
print()
print('Hour distribution:'); print(res.groupby('hour')['worst_pnl'].agg(['count','sum']).round(1).sort_values('sum').to_string())
print()
print('Month distribution:'); print(res.groupby(pd.to_datetime(res['target_date']).dt.month)['worst_pnl'].agg(['count','sum']).round(1).to_string())
print()
print('By type x action:'); print(pd.crosstab(res['type'], res['action']).to_string())
print()
print('By type x worst_model:'); print(pd.crosstab(res['type'], res['worst_model']).to_string())
print()
print('By type x agreement:'); print(pd.crosstab(res['type'], res['agreement']).to_string())

# save refined
res.to_csv(os.path.join(DATA, 'stage3', 'top_loss_classification.csv'), index=False)
