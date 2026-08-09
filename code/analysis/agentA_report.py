# -*- coding: utf-8 -*-
"""Generate docs/stage3/top_loss_event_analysis.md from top_loss_events.csv."""
import pandas as pd, numpy as np, os

BASE = r'D:\code\pyCode\CA-电力交易预测\code'
DATA = os.path.join(BASE, 'data')
STAGE3 = os.path.join(DATA, 'stage3')
OUT_CSV = os.path.join(STAGE3, 'top_loss_events.csv')
OUT_MD = r'D:\code\pyCode\CA-电力交易预测\docs\stage3\top_loss_event_analysis.md'

t = pd.read_csv(OUT_CSV)
t['target_date'] = pd.to_datetime(t['target_date'])

# ---- helpers ----
def f(x, digits=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return '-'
    return ('%.{0}f'.format(digits)) % x

def s(x):
    return '' if (x is None or (isinstance(x, float) and np.isnan(x))) else str(x)

# per-model stats
preds = {}
for name in ['rule', 'interpretable', 'catboost']:
    df = pd.read_csv(os.path.join(DATA, 'predictions_%s.csv' % name))
    df['target_date'] = pd.to_datetime(df['target_date'])
    df['pnl'] = 0.0
    df.loc[df['pred_direction'] > 0, 'pnl'] = df.loc[df['pred_direction'] > 0, 'actual_return']
    df.loc[df['pred_direction'] < 0, 'pnl'] = -df.loc[df['pred_direction'] < 0, 'actual_return']
    preds[name] = df

L = []
A = L.append
A('# CA-ISO 价差交易 · 极端亏损事件逐笔分析（Agent A）')
A('')
A('> 分析日期：2026-08-09')
A('> 输入：`predictions_rule.csv` / `predictions_interpretable.csv` / `predictions_catboost.csv`（test 窗口 2026-06-02 ~ 2026-08-05，共 4,678 行/模型）+ `canonical.parquet` + `master.csv`')
A('> 口径：PnL = SELL_DA 取 `+actual_return`，BUY_DA 取 `−actual_return`，NO_TRADE = 0；交易子集 = `pred_direction != 0` 的 test 行。')
A('> 严格 as-of：所有"补算"历史统计只用 `target_date-2` 及更早的数据（决策截止为 decision_date 10:00 PT，滞后约定见 canonical.py）；不使用任何未来值。')
A('')
A('## 0. 结论先行')
A('')
A('- 合并三模型去重后，**Top 50 亏损全部集中在 CONTROLX_1_N001（49 笔）与 ELCAJNGT_7_N001（1 笔）**；SNLNDRO 无上榜（其 spread 幅度过小，std≈14）。Top 50 合计亏损 **≈ −50,348 $/MWh**。')
A('- 两条主要亏损机制：')
A('  - **interpretable / catboost 的 CONTROLX BUY 亏损（35 笔）**：模型押 Return<0（BUY_DA），但实时价（RTPD）深度暴跌至 −880 ~ −2365，Return 大幅转正（+840 ~ +2251），BUY 一次性巨亏。这是"真实市场深夜/清晨负电价（可再生能源过剩）"的反复重演。')
A('  - **rule 的 CONTROLX SELL 亏损（14 笔）**：rule 押 Return>0（SELL_DA），但日前价（DA）暴跌至 −1052 ~ −1313（负电价），Return 大幅转负（−850 ~ −1175），SELL 巨亏。')
A('  - ELCA 1 笔：RTPD 晚间尖峰 +1102（气电稀缺），Return 转负 −914。')
A('- **事前可识别性**：Top 50 中 **44 笔为 Type A（PRE-TRADE DETECTABLE）**、**6 笔为 Type C（RESIDUAL_TAIL_RISK）**、**0 笔为 Type B（NEEDS_EXTERNAL_INFORMATION）**。')
A('  - 44 笔 A 类均可凭交易前可见的内部信号识别：三模型方向冲突（29 笔）、spread 处于历史极值水平（29 笔）、interpretable/catboost 的 CONTROLX BUY 在 test 窗口第 2 周起累计均值即转负（可 walk-forward 观察到，属系统性策略缺陷，覆盖 35 笔中的多数）、ELCA 低样本（1 笔）。')
A('  - 6 笔 C 类集中在 2026-06-14/15 的"第一波"尾部（H8/H10/H11/H13/H24）：当时尚无同小时密集极端前例、无模型冲突、spread 状态不极端、模型累计战绩尚未转负——属已知肥尾节点上的残余随机尾部。')
A('  - **没有任何一笔需要"外部信息才能事前看出危险"（Type B）**：CA-ISO 的负电价/极端尾部是历史（2024-2026，含 ±1000~3656）反复出现的现象；test 窗口正是该现象的集中爆发期，事前内部数据已给出足够警告。')
A('')

# ---------------- Section 1: methodology ----------------
A('## 1. 数据与口径')
A('')
A('### 1.1 模型与 PnL')
A('')
A('| 模型 | 交易数 | 累计 PnL | Top-20 亏损合计 | Top-50 亏损合计 | BUY/SELL 数 |')
A('|---|---|---|---|---|---|')
for name, lab in [('rule', 'rule'), ('interpretable', 'interpretable'), ('catboost', 'catboost')]:
    df = preds[name]
    tr = df[df['pred_direction'] != 0]
    t20 = tr.nsmallest(20, 'pnl'); t50 = tr.nsmallest(50, 'pnl')
    nb = int((tr['pred_direction'] < 0).sum()); ns = int((tr['pred_direction'] > 0).sum())
    A('| %s | %d | %s | %s | %s | BUY=%d / SELL=%d |' % (
        lab, len(tr), f(tr['pnl'].sum(), 0), f(t20['pnl'].sum(), 0), f(t50['pnl'].sum(), 0), nb, ns))
A('')
A('> Rule 为保守白盒（几乎全 SELL，累计 +79k）；interpretable 与 catboost 的 ML 方向预测在 CONTROLX 上系统性做 BUY，累计 −134k / −138k。Top-50 亏损合计（−47k / −46k / −25k）相当于两 ML 模型总亏损的三分之一左右。')
A('')
A('### 1.2 亏损行如何选出')
A('')
A('1. 对每模型交易子集按 PnL 升序取 Top 20 / Top 50（负 PnL 最深）。')
A('2. 三模型 Top-50 取并集并按 (node, target_date, hour) 去重；每行的 worst_pnl = 三个模型中最深亏损（若某模型不交易该行则 pnl=0 不计入）。')
A('3. 合并集按 worst_pnl 升序取前 50 行为"合并 Top 50 亏损"。')
A('')
A('### 1.3 交易前可见特征与历史补算')
A('')
A('- 直接取 `canonical.parquet` 对应行的 X 特征（全部 as-of）：`spread_lag1/2/7`、`spread_mean7/14/30`、`spread_std7/14/30`、`spread_day_std/range/max_lag1`、`da_day_mean_lag1`、`load_2da_forecast`、`t2m_lag1/ssrd_lag1/wind100_lag1`、`peer_*`、`dow/month/hour`。')
A('- 由 `master.csv`（≤ target_date−2）补算：同 node×hour 历史 spread 分位数（p90/95/99/99.5、min/max）、CVaR95/99、hist_std、近 7/30 日波动 vol7/vol30、近 30 日极端事件计数 `extreme_count_30`（|spread|>历史 p95 的天数）、`lag1_pct`（决策时 spread 在历史分布中的分位）、`realized_pct`（实际 Return 在历史分布中的分位）、node 历史 bias、node 近 7 日波动与最大 |spread|。')
A('')
A('## 2. 三模型各自的 Top-20 亏损')
A('')
for name, lab in [('rule', 'rule'), ('interpretable', 'interpretable'), ('catboost', 'catboost')]:
    df = preds[name]
    tr = df[df['pred_direction'] != 0]
    t20 = tr.nsmallest(20, 'pnl')
    A('### 2.%s %s 的 Top-20 亏损' % ({'rule': '1', 'interpretable': '2', 'catboost': '3'}[name], lab))
    A('')
    A('| # | node | target_date | hour | action | expected_return | confidence | actual_return | pnl |')
    A('|---|---|---|---|---|---|---|---|---|')
    for i, (_, r) in enumerate(t20.iterrows(), 1):
        act = 'SELL' if r['pred_direction'] > 0 else 'BUY'
        A('| %d | %s | %s | %d | %s | %s | %s | %s | **%s** |' % (
            i, r['node'], r['target_date'].date(), r['hour'], act,
            f(r['expected_return'], 1), f(r['confidence'], 2), f(r['actual_return'], 1), f(r['pnl'], 1)))
    A('')

# ---------------- Section 3: merged top-50 main table ----------------
A('## 3. 合并 Top-50 亏损 · 逐笔主表（含三模型输出与 Type）')
A('')
A('字段：action=worst 模型的实际动作；agreement=三模型一致性（3/3、2/3、conflict=方向冲突、1/3=仅一模型交易）；`rule/interp/catboost` 各列=方向(概率)，0=该模型观望不交易；conf/exp_ret=worst 模型的置信度/期望收益；actual_* 为目标日实际价格。')
A('')
A('| # | node | date | H | action | worst | worst_pnl | rule | interp | catboost | agree | conf | exp_ret | DA | RTPD | Return | hist_p99 | lag1_pct | Type |')
A('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
for i, (_, r) in enumerate(t.iterrows(), 1):
    def dp(d, p):
        d = int(d)
        if d == 0:
            return '0'
        return '%d(%.2f)' % (d, p)
    A('| %d | %s | %s | %d | %s | %s | **%s** | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | **%s** |' % (
        i, r['node'].replace('_1_N001', ''), r['target_date'].strftime('%Y-%m-%d'), r['hour'],
        r['action'], r['worst_model'], f(r['worst_pnl'], 0),
        dp(r['rule_dir'], r['rule_prob']), dp(r['interpretable_dir'], r['interpretable_prob']),
        dp(r['catboost_dir'], r['catboost_prob']), r['agreement'],
        f(r['worst_conf'], 2), f(r['worst_er'], 1), f(r['actual_da'], 0), f(r['actual_rtpd'], 0), f(r['actual_return'], 0),
        f(r['hist_p99'], 0), f(r['lag1_pct'], 2), r['type']))
A('')
A('> 说明：`realized_pct` 显示绝大多数行实际 Return 都落在该 node×hour 历史分布的最尾端（=0.0 或 1.0，即超过历史所见范围）。CONTROLX 历史 p99≈550–850、历史最极端 ±900~3656，肥尾是常态而非个例。')
A('')
A('### 3.1 逐笔 Type 与判定理由')
A('')
A('| # | date | H | action | worst | pnl | Type | 判定理由 |')
A('|---|---|---|---|---|---|---|---|')
for i, (_, r) in enumerate(t.iterrows(), 1):
    A('| %d | %s | %d | %s | %s | %s | **%s** | %s |' % (
        i, r['target_date'].strftime('%Y-%m-%d'), r['hour'], r['action'], r['worst_model'],
        f(r['worst_pnl'], 0), r['type'], r['type_reason']))
A('')

# ---------------- Section 4: feature table ----------------
A('## 4. 合并 Top-50 · 交易前可见特征明细')
A('')
A('价格与波动（$/MWh）；`lag1_pct`/`realized_pct` 为分位（0–1）；`extreme_count_30`=近 30 日同 node×hour 超历史 p95 天数；`vol_ratio`=vol30/hist_std。')
A('')
A('| # | date | H | action | spread_lag1 | spread_lag2 | spread_lag7 | mean7 | mean14 | mean30 | std7 | std14 | std30 | da_day_mean_lag1 | load_2da | t2m_lag1 | dow | |')
A('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
for i, (_, r) in enumerate(t.iterrows(), 1):
    A('| %d | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | |' % (
        i, r['target_date'].strftime('%Y-%m-%d'), r['hour'], r['action'],
        f(r['spread_lag1'], 0), f(r['spread_lag2'], 0), f(r['spread_lag7'], 0),
        f(r['spread_mean7'], 0), f(r['spread_mean14'], 0), f(r['spread_mean30'], 0),
        f(r['spread_std7'], 0), f(r['spread_std14'], 0), f(r['spread_std30'], 0),
        f(r['da_day_mean_lag1'], 0), f(r['load_2da_forecast'], 0), f(r['t2m_lag1'], 1), r['dow']))
A('')
A('| # | date | H | action | hist_p90 | hist_p95 | hist_p99 | hist_min | hist_max | cvar95 | cvar99 | hist_std | vol7 | vol30 | vol_ratio | extreme30 | lag1_pct | realized_pct | node_bias | node_vol7 | hist_n |')
A('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
for i, (_, r) in enumerate(t.iterrows(), 1):
    A('| %d | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
        i, r['target_date'].strftime('%Y-%m-%d'), r['hour'], r['action'],
        f(r['hist_p90'], 0), f(r['hist_p95'], 0), f(r['hist_p99'], 0),
        f(r['hist_min'], 0), f(r['hist_max'], 0), f(r['cvar95'], 0), f(r['cvar99'], 0),
        f(r['hist_std'], 0), f(r['vol7'], 0), f(r['vol30'], 0),
        f(r['vol30'] / r['hist_std'] if pd.notna(r['vol30']) and pd.notna(r['hist_std']) and r['hist_std'] else None, 2),
        int(r['extreme_count_30']) if pd.notna(r['extreme_count_30']) else '-',
        f(r['lag1_pct'], 2), f(r['realized_pct'], 2), f(r['node_bias_mean'], 1), f(r['node_vol7'], 1),
        int(r['hist_n']) if pd.notna(r['hist_n']) else '-'))
A('')
A('> 完整字段（含 `spread_lag7`、`spread_day_std/range/max_lag1`、`rtpd_day_mean_lag1`、`load_actual_lag1`、`ssrd_lag1`、`wind100_lag1`、`peer_*`、`rule_conf/prob`、`interpretable_conf/prob`、`catboost_conf/prob` 等）见 `code/data/stage3/top_loss_events.csv`。')
A('')

# ---------------- Section 5: classification ----------------
A('## 5. 事前可识别性分类方法')
A('')
A('### 5.1 判定规则（透明、固定阈值、不调参）')
A('')
A('**Type A（PRE-TRADE DETECTABLE）** —— 满足以下任一"具体、可事前观察"的信号：')
A('- `conflict`：rule 与 ML 模型在方向上直接冲突（一个 SELL 一个 BUY）。交易前可见，是方向不确定的硬证据。')
A('- `extreme_state`：决策时 `spread_lag1` 处于同 node×hour 历史分位 <5% 或 >95%（spread 已到历史极值，均值回归/崩溃风险高）。')
A('- `low_sample`：该 node×hour 历史样本 <200（ELCA 仅 141，cold-start）。')
A('- `broken_buy`：worst 模型为 interpretable/catboost 且做 CONTROLX BUY，且交易日在模型 CONTROLX BUY 的**walk-forward 累计均值 PnL 首次跌破 −50**之后（interpretable 06-16、catboost 06-16 起）。这是可滚动观察到的"该策略在 CONTROLX 上系统性亏损"信号。')
A('')
A('**Type B（NEEDS_EXTERNAL_INFORMATION）** —— 实际 Return 超出该 node×hour 全部历史范围（realized_pct≈0/1）**且**事前内部状态平静（vol_ratio<1.5、近30日极端事件<3、node 近7日最大|spread|<800），即"内部数据无任何预警、只能靠外部信息"。')
A('')
A('**Type C（RESIDUAL_TAIL_RISK）** —— 无任何 A 类信号、也不满足 B 的"平静+超历史"组合：即已知肥尾节点上的一次随机尾部落点。')
A('')
A('**区分度检验**：vol_ratio≥1.5 与 extreme_count_30≥3 在 test 窗口**全体** CONTROLX 行的命中率分别高达 75% 与 82%（该窗口整体就是极端 regime），因此**不作为** A 类的判定依据（避免把"CONTROLX 一直很波动"当成"这笔该避开"）。A 类用的 4 个信号均是与具体交易绑定的。')
A('')
A('### 5.2 逐笔分类结果')
A('')
A('| Type | 笔数 | 占比 | 含义 |')
A('|---|---|---|---|')
A('| A · PRE-TRADE DETECTABLE | 44 | 88% | 事前内部数据已有明确危险信号，可识别并规避/降仓 |')
A('| B · NEEDS_EXTERNAL_INFORMATION | 0 | 0% | 需外部信息才能事前识别（本数据集无此类） |')
A('| C · RESIDUAL_TAIL_RISK | 6 | 12% | 已知肥尾上的随机尾部，无具体事前信号 |')
A('')

# ---------------- Section 6: aggregation ----------------
A('## 6. 汇总')
A('')
A('### 6.1 Top-20 / Top-50 的 Type 构成')
A('')
A('| 范围 | A | B | C | 合计 |')
A('|---|---|---|---|---|')
for n in [20, 50]:
    sub = t.head(n)
    A('| Top-%d | %d | %d | %d | %d |' % (n, (sub['type']=='A').sum(), (sub['type']=='B').sum(), (sub['type']=='C').sum(), len(sub)))
A('')
A('### 6.2 集中度')
A('')
A('| 维度 | 分布（Top-50） |')
A('|---|---|')
A('| node | CONTROLX 49 笔（−49,434），ELCA 1 笔（−914），SNLNDRO 0 |')
A('| 方向 | BUY 35 笔（−34,630），SELL 15 笔（−15,718） |')
A('| worst 模型 | interpretable 36 笔（−35,544），rule 14 笔（−14,804），catboost 0 笔（catboost 亏损行与 interpretable 同行同损，worst 记 interpretable） |')
A('| 一致性 | conflict 29 笔（−29,339），2/3 一致 18 笔（−16,945），1/3（仅一模型交易）3 笔（−4,064） |')
A('| 小时 | 集中于 H18-H24 傍晚/深夜与 H3-H10 清晨（RTPD 负电价多发时段）；H20（5 笔 −4,880）最深 |')
A('| 月份 | 2026-06 40 笔（−37,855），2026-07 10 笔（−12,493） |')
A('')
A('### 6.3 主要亏损模式')
A('')
A('**模式 1 · ML 模型 CONTROLX BUY（35 笔）——"高置信 BUY + 真实 RTPD 负电价暴跌"**')
A('- interpretable/catboost 押 Return<0 做 BUY，confidence 多 >0.6；真实 RTPD 跌至 −880 ~ −2365（2026-06/07 深夜清晨可再生能源过剩），Return 大正（+840 ~ +2251），单笔巨亏。')
A('- 事前可识别：两模型 CONTROLX BUY 的 walk-forward 累计均值在 06-08/06-15 起转负、06-16 起稳定 < −50——**在 test 窗口第 3 周即已暴露系统性缺陷**；且多数行同时有模型冲突或 extreme_state。')
A('- 这解释了 backtest.py 中"方向准确率高 ≠ 盈利"：ML 模型在 CONTROLX 频繁 BUY 小赚，却在一日式负电价极端事件上一次性巨亏。')
A('')
A('**模式 2 · rule CONTROLX SELL（14 笔）——"spread 冲高 + 模型冲突 + DA 负电价崩塌"**')
A('- rule 押 Return>0 做 SELL（expected_return 大正），决策时 spread 多在 +400 ~ +900（历史 p95 之上，extreme_state）；interpretable/catboost 同场反手 BUY（conflict）。次日 DA 暴跌至 −1052 ~ −1313，Return 大负（−850 ~ −1175），SELL 巨亏。')
A('- 事前可识别：冲突 + extreme_state 双重信号。')
A('')
A('**模式 3 · ELCA 晚间 RTPD 尖峰（1 笔）**')
A('- 2026-07-24 H20：RTPD 尖峰 +1102 vs DA +188，Return −914；决策时 spread 已在历史最低（−682，lag1_pct=0），历史样本仅 141（低样本）。')
A('')
A('**模式 4 · 第一波残余尾部（6 笔，Type C）**')
A('- 2026-06-14/15 的 CONTROLX H8/H10/H11/H13/H24 BUY：当时该小时尚无密集极端前例（近30日极端 0–3 天）、无冲突、spread 状态不极端、模型累计战绩尚未转负——是 6 月中旬负电价 regime 的"第一波"落点。')
A('- 注：06-08 已有同小时 +623~806 前例（弱前兆），但单次前例不足以上升为 A 类的"具体信号"；如实记为残余尾部。')
A('')
A('**共性**：所有 50 笔亏损均发生在 CONTROLX/ELCA 的**极端负电价**（DA 或 RTPD 深度为负）事件上；事件本身在历史上有反复先例（CONTROLX 历史最值 +3656/−2295），**肥尾是已知属性，策略没有为尾部风险留任何对冲/止损空间**，且 ML 模型的 BUY 方向选择与 CA-ISO 深夜负电价的实际方向系统性相悖。')
A('')
A('## 7. 局限与诚实声明')
A('')
A('- 分类的 Type A"可识别"不等于"能精准预测哪一天、多大幅度"；它指的是**危险在事前是可见的**（方向冲突、极端状态、策略战绩转负、低样本），合理风控（如规避 CONTROLX BUY、spread 极值时止步、给尾部留止损）可以避开绝大部分亏损。')
A('- `broken_buy` 阈值（累计均值 −50）为分析员设定，未做网格调优；改变阈值会轻微移动 A/C 分界，但不影响主结论（ML 的 CONTROLX BUY 在 test 窗口为系统性负期望）。')
A('- "0 笔 Type B"反映的是：负电价/极端尾部在 CA-ISO 历史中反复出现、非黑天鹅；但这不否定**具体某天**的事件幅度（如 RTPD −2365）仍需实时/外部信息才能预知。')
A('- C 类 6 笔被保留为残余尾部，是承认"即便做了上述风控，仍会有漏网肥尾"；不应被解读为"这些不可规避"。')
A('')
A('## 附录 A · 关键数值')
A('')
A('| 项目 | 值 |')
A('|---|---|')
A('| 合并 Top-50 总亏损 | ≈ −50,348 $/MWh（1 MWh/仓） |')
A('| CONTROLX 历史 spread 区间 | −2,295 ~ +3,656；p99≈550–850；cvar95≈−190~−270（该 node 尾部极厚） |')
A('| interpretable CONTROLX BUY 战绩 | walk-forward 累计均值 06-08 首破 −20、06-16 首破 −50，期末 ≈ −85/trade |')
A('| catboost CONTROLX BUY 战绩 | 06-15 首破 −20、06-16 首破 −50，期末 ≈ −108/trade |')
A('| rule CONTROLX SELL 战绩 | 胜率 ≈ 53%，正期望（其亏损源于 06-23/06-30 两日 DA 负电价崩塌） |')
A('| 最大单笔 | 2026-07-17 H3 BUY（interpretable）−2,251（RTPD −2,300 vs DA −49） |')
A('')

with open(OUT_MD, 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
print('saved md ->', OUT_MD)
print('lines:', len(L))
