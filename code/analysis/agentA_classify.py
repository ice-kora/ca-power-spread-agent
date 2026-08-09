# -*- coding: utf-8 -*-
"""Agent A: classification of top loss events."""
import pandas as pd, numpy as np, os

BASE = r'D:\code\pyCode\CA-电力交易预测\code'
DATA = os.path.join(BASE, 'data')
OUT_CSV = os.path.join(DATA, 'stage3', 'top_loss_events.csv')

pd.set_option('display.width', 300)
pd.set_option('display.max_columns', 80)

t = pd.read_csv(OUT_CSV)
t['target_date'] = pd.to_datetime(t['target_date'])

# ---------- model calibration on CONTROLX ----------
preds = {}
for name in ['rule', 'interpretable', 'catboost']:
    df = pd.read_csv(os.path.join(DATA, 'predictions_%s.csv' % name))
    df['target_date'] = pd.to_datetime(df['target_date'])
    df['pnl'] = 0.0
    df.loc[df['pred_direction'] > 0, 'pnl'] = df.loc[df['pred_direction'] > 0, 'actual_return']
    df.loc[df['pred_direction'] < 0, 'pnl'] = -df.loc[df['pred_direction'] < 0, 'actual_return']
    preds[name] = df

print('=== Model calibration on CONTROLX (trades by confidence bucket) ===')
for name, df in preds.items():
    tr = df[(df['pred_direction'] != 0) & (df['node'] == 'CONTROLX_1_N001')].copy()
    tr['action'] = np.where(tr['pred_direction'] > 0, 'SELL', 'BUY')
    print('---', name, 'n_trades:', len(tr))
    for act in ['BUY', 'SELL']:
        g = tr[tr['action'] == act]
        if len(g) == 0:
            continue
        for lo, hi in [(0.0,0.4),(0.4,0.6),(0.6,0.8),(0.8,1.0)]:
            gg = g[(g['confidence'] >= lo) & (g['confidence'] < hi)]
            if len(gg) == 0:
                continue
            print('  %-4s conf[%.1f,%.1f) n=%4d win=%5.1f%% mean_pnl=%8.2f sum_pnl=%10.1f' % (
                act, lo, hi, len(gg), 100*(gg['pnl']>0).mean(), gg['pnl'].mean(), gg['pnl'].sum()))

# ---------- classification signals ----------
def classify(row):
    """Return (type, reason). Decision tree with documented rules."""
    reasons = []
    low_sample = row['hist_n'] < 200
    conflict = row['agreement'] == 'conflict'
    n_model = int(str(row['agreement']).split('/')[0]) if '/' in str(row['agreement']) else 0
    extreme_30 = row['extreme_count_30']
    node_prev7 = row['node_max_abs_prev7']
    vol30 = row['vol30']; hist_std = row['hist_std']
    lag1_pct = row['lag1_pct']; realized_pct = row['realized_pct']
    hist_p99 = row['hist_p99']
    worst_model = row['worst_model']; action = row['action']
    conf = row['%s_conf' % worst_model]

    vol_elevated = (pd.notna(vol30) and pd.notna(hist_std) and hist_std > 0 and vol30 > 1.5 * hist_std)
    regime_extreme = extreme_30 >= 3 or (pd.notna(node_prev7) and node_prev7 >= 800) or vol_elevated
    extreme_state = pd.notna(lag1_pct) and (lag1_pct < 0.05 or lag1_pct > 0.95)
    known_tail = pd.notna(hist_p99) and hist_p99 >= 500
    beyond = pd.notna(realized_pct) and (realized_pct == 0.0 or realized_pct == 1.0)

    # A1: low sample
    if low_sample:
        reasons.append('low_sample(hist_n=%d)' % row['hist_n'])
        return 'A', '; '.join(reasons)
    # A2: model conflict
    if conflict:
        reasons.append('model_conflict(%s)' % row['agreement'])
        if regime_extreme:
            reasons.append('extreme_regime')
            return 'A', '; '.join(reasons)
        if extreme_state:
            reasons.append('extreme_state(lag1_pct=%.2f)' % lag1_pct)
            return 'A', '; '.join(reasons)
        if known_tail:
            reasons.append('known_fat_tail')
            return 'A', '; '.join(reasons)
    # A3: single-model trade while others abstain (weak conflict) + regime
    if n_model == 1:
        reasons.append('only_1_of_3_traded')
        if regime_extreme or extreme_state or known_tail:
            reasons.append('regime/extreme/tail present')
            return 'A', '; '.join(reasons)
    # A4: extreme regime or extreme state
    if regime_extreme:
        reasons.append('extreme_regime(ext30=%d,prev7=%.0f,vol_elev=%s)' % (extreme_30, node_prev7 or 0, vol_elevated))
        return 'A', '; '.join(reasons)
    if extreme_state:
        reasons.append('extreme_state(lag1_pct=%.2f)' % lag1_pct)
        return 'A', '; '.join(reasons)
    # A5: calibration issue
    # (computed separately below; placeholder)
    # C: known tail, realized within history, no warning
    if known_tail and not beyond:
        reasons.append('known_tail_within_history')
        return 'C', '; '.join(reasons)
    # B: beyond history, calm pre-trade
    if beyond and not regime_extreme:
        reasons.append('beyond_history(realized_pct=%.2f)' % realized_pct)
        reasons.append('no_extreme_regime_pre_trade')
        return 'B', '; '.join(reasons)
    return 'U', '; '.join(reasons) if reasons else 'no_signal_evidence'

rows = []
for _, r in t.iterrows():
    tp, reason = classify(r)
    rows.append({'node': r['node'], 'target_date': str(r['target_date'].date()), 'hour': r['hour'],
                 'action': r['action'], 'worst_model': r['worst_model'], 'worst_pnl': r['worst_pnl'],
                 'agreement': r['agreement'], 'type': tp, 'reason': reason})
res = pd.DataFrame(rows)
print()
print('=== Classification summary ===')
print(res['type'].value_counts().to_string())
print()
print(res.to_string(index=False))
