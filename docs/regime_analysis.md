# Regime 分析 —— 白盒状态划分与"模型逆漂移"归因（Agent B）

> 生成时间：2026-08-09 ｜ 数据：`code/data/canonical.parquet` 全历史 + `predictions_v2.csv`（test）+ `predictions_v2_val.csv`（val）
> 方法：完全白盒、无训练。对每个 node×hour 的日序列计算 as-of rolling（`shift(1)`，只依赖 t-1 及以前）。
> 表数据：`code/data/v021_regime_full.csv`（逐行 rolling）、`v021_regime_by_split.csv`、`v021_regime_model_direction.csv`、`v021_regime_gate_ev.csv`、`v021_gate_node_dir_window_ev.csv`。

---

## 1. Regime 定义（规则透明，阈值可复核）

对每个 (node, hour) 序列按 target_date 排序：

- `roll_mean_7d/14d/30d/60d` = 该 node×hour 的 actual_return 近 7/14/30/60 个交易日的滚动均值（`shift(1)`，as-of）。
- `roll_std_7d/30d` = 近 7/30 日滚动标准差（`shift(1)`）。
- `hist_std` = 该 node×hour 全历史 std。漂移阈值 `th = max(0.15 × hist_std, 1.0)`（按节点缩放，透明）。

状态（每行取其一，互斥）：

| regime_drift | 条件 |
|---|---|
| POSITIVE_SPREAD_REGIME | roll_mean_30d > th |
| NEGATIVE_SPREAD_REGIME | roll_mean_30d < −th |
| NEUTRAL_REGIME | 其余（\|roll_mean_30d\| ≤ th） |

另给一个正交标记：`regime_vol` = HIGH_VOLATILITY 当 roll_std_30d > 该 node×hour 历史 75 分位，否则 NORMAL_VOL。

---

## 2. 当前 test 窗口属什么 regime？（v021_regime_by_split.csv）

| node | split | n | mean_roll30 | POSITIVE 占比 | NEGATIVE 占比 | NEUTRAL 占比 | HIGH_VOL 占比 | mean_actual_return | mean_roll_std30 |
|---|---|---|---|---|---|---|---|---|---|
| CONTROLX_1_N001 | train | 6576 | +8.9 | 19.3% | 4.4% | 76.2% | 29.4% | +9.3 | 131 |
| CONTROLX_1_N001 | val | 3622 | +10.7 | 30.4% | 15.5% | 54.1% | 45.1% | +10.3 | 190 |
| CONTROLX_1_N001 | **test** | 1559 | **+85.9** | **66.3%** | 8.1% | 25.6% | **80.9%** | **+84.0** | 318 |
| ELCAJNGT_7_N001 | train | 2159 | −0.8 | 6.1% | 10.9% | 83.0% | 13.6% | −1.2 | 28 |
| ELCAJNGT_7_N001 | **test** | 1560 | −8.2 | 2.2% | **48.6%** | 49.2% | 35.1% | −8.3 | 46 |
| SNLNDRO_1_N001 | train | 6576 | +2.2 | 47.7% | 11.3% | 40.9% | 22.2% | +2.7 | 15 |
| SNLNDRO_1_N001 | val | 3622 | +3.5 | 60.5% | 3.4% | 36.1% | 31.4% | +2.5 | 15 |
| SNLNDRO_1_N001 | **test** | 1559 | +0.7 | 31.9% | 11.7% | 56.4% | 20.0% | +1.2 | 11 |

**结论**：
- **test 主窗口对 CONTROLX 是 POSITIVE_SPREAD_REGIME（66% 行）且 HIGH_VOLATILITY（81% 行）**；对 ELCA 是 NEUTRAL/NEGATIVE 混合（48.6% NEGATIVE，主要来自 7 月）；对 SNLNDRO 是 NEUTRAL。
- CONTROLX test 的 mean_roll30 = +85.9 vs train +8.9 / val +10.7 —— **漂移水平量级翻了约 9 倍，高波动占比 29%→81%**。
- 具体到月：2026-06 CONTROLX mean actual_return **+170**（7 月 +12、8 月前 5 天 −12）。正漂移几乎由 **6 月极端稀缺定价事件**贡献。

---

## 3. train / val / test 是否发生 regime shift？—— 是，显著

1. **CONTROLX：NEUTRAL（train 76%）→ POSITIVE（test 66%）**，高波动 29%→81%。均值漂移 +9→+84，std 170→344（近 2 倍）。
2. **ELCA：NEUTRAL（train 83%）→ NEGATIVE（test 48.6%）**，7 月 mean −17 是主因；train 里几乎没有负漂移。
3. **SNLNDRO：相对稳定**（train/val/test 都在 NEUTRAL/POSITIVE 之间摆动，均值 +1~+3）。
4. **方向（关键）**：CONTROLX 的 shift 方向与 test 内节点漂移一致（train+val 已现 +9~+10 的正漂移，test 放大到 +84）；但**幅度与波动率是 test 独有的量级**，train+val 没有等价的极端段。所以「CONTROLX 正漂移方向」train+val 可见，**「test 的极强正漂移」从内部滞后特征在决策时点不可见**。

---

## 4. Static SELL 为什么在 test 表现特别好？

test 内全节点静态 SELL（每行 +actual_return）= **+119,869**；其中主策略节点 ZP26 = **+132,746**（CONTROLX +130,931 + SNLNDRO +1,814；ELCA −12,876）。

原因链：
1. test 窗口对 CONTROLX 是**强 POSITIVE_SPREAD_REGIME**（mean +84/行），SELL = 顺势吃漂移 → +130,931。
2. CONTROLX 右尾极值（best +3656、6 月密集 +500~+1000 事件）全部落在 SELL 的盈利侧。
3. SNLNDRO 温和正漂移（+1.2/行）锦上添花。
4. ELCA 逆漂移（−8.3/行）拖累 −12,876，但被 CONTROLX 主导。

**诚实结论**：static SELL 的收益 = **市场 regime 本身（正漂移），不是任何模型的 alpha**。模型/策略跑赢 static 才算有真 alpha；而本窗口 v2 模型（−118k~−120k）**大幅跑输 static SELL**。

---

## 5. Predictive Model 是否没识别 regime shift？—— 是，两级失败

### 5.1 决策时点（test 开始）regime 不可见

2026-06-02（test 首日）CONTROLX 的 as-of roll_mean_30d = **−12（NEGATIVE 状态）**、roll_mean_60d = +7。**正漂移 regime 在 6 月初从滞后数据里尚未显现**——6 月事件是外生的（稀缺定价），训练特征（滞后 spread/负荷/气温）没有等价先例。

### 5.2 regime 显现后模型仍不更新（更严重）

即便 regime 已在滚动窗口内转为强正（roll_mean_30d 从 6/17 起 +50 → +145 → +196），模型对 CONTROLX 6 月**全部 696 行仍 100% 预测 BUY（er<0）**：

| week | n | n_buy | mean_er | mean_actual_return | positive_ratio(actual) |
|---|---|---|---|---|---|
| 2026-06-01 ~ 06-07 | 144 | 144 | −23.5 | −10.4 | 0.09 |
| 2026-06-08 ~ 06-14 | 168 | 168 | −41.8 | +15.8 | 0.25 |
| **2026-06-15 ~ 06-21** | 168 | 168 | −44.7 | **+622.2** | **0.96** |
| **2026-06-22 ~ 06-28** | 168 | 168 | −53.0 | +228.2 | 0.61 |
| 2026-06-29 ~ 07-05 | 168 | 168 | −44.6 | −186.8 | 0.08 |

- 在最强的正 regime 周（6/15~6/21，mean +622、96% 小时为正），模型仍**满仓 BUY 且零 SELL**。
- **根因**：模型预测的是条件**中位数**（`expected_return` = LightGBM quantile(alpha=0.5)）。6 月即便均值 +622，逐小时中位数仍为负（test 全月 CONTROLX median −8.8）→ 模型的 er 恒为负 → BUY。**模型「多数小时对」恰恰是「期望值错」**：它押的是中位数，PnL 按均值结算。

### 5.3 test 整体模型与真实方向的关系

| node | sign_acc | spearman(er, actual) | pearson(er, actual) |
|---|---|---|---|
| CONTROLX_1_N001 | 0.527 | **−0.059** | **−0.125** |
| ELCAJNGT_7_N001 | 0.617 | +0.099 | −0.050 |
| SNLNDRO_1_N001 | 0.595 | +0.165 | +0.125 |

CONTROLX 上模型符号正确率 52.7%（≈掷硬币），且 expected_return 与 actual_return **负相关**——模型在最大节点上不是"无信息"，而是**系统性反向**。

### 5.4 Model 方向 vs regime（test，v021_regime_model_direction.csv）

| node | regime | n | actual_mean | n_buy | BUY cum | BUY mean | n_sell | SELL cum | SELL mean |
|---|---|---|---|---|---|---|---|---|---|
| CONTROLX | POSITIVE | 1034 | +105.3 | 859 | **−113,243** | −131.8 | 175 | −4,322 | −24.7 |
| CONTROLX | NEUTRAL | 399 | +44.1 | 311 | −19,590 | −63.0 | 88 | −1,993 | −22.6 |
| CONTROLX | NEGATIVE | 126 | +35.0 | 97 | −5,357 | −55.2 | 29 | −943 | −32.5 |
| ELCA | POSITIVE | 34 | −7.6 | 0 | — | — | 34 | −259 | −7.6 |
| ELCA | NEUTRAL | 768 | −9.1 | 65 | +576 | +8.9 | 703 | −6,409 | −9.1 |
| ELCA | NEGATIVE | 758 | −7.4 | 40 | +156 | +3.9 | 718 | −5,476 | −7.6 |
| SNLNDRO | POSITIVE | 497 | +3.7 | 81 | −601 | −7.4 | 416 | +1,250 | +3.0 |
| SNLNDRO | NEUTRAL | 880 | +0.3 | 443 | +783 | +1.8 | 437 | +1,030 | +2.4 |
| SNLNDRO | NEGATIVE | 182 | −1.6 | 100 | +361 | +3.6 | 82 | +77 | +0.9 |

- CONTROLX 在 POSITIVE regime 下，模型 **859 笔 BUY 亏 −113,243**（占 test BUY 总亏 96%）。模型"在最强正 regime 满仓 BUY"是亏损主因。
- 即使 CONTROLX 落在 NEGATIVE regime（126 行），**BUY 仍亏**（−55/笔）——因为该窗口实际均值仍 +35（右尾事件与 regime 标签并存）。这进一步说明**模型在 CONTROLX 上的逆漂移不是个别 regime 造成的，而是全程性的**。

---

## 6. Risk Gate 节点规则是否只是 regime-specific？—— 否（全 regime 负 EV）

用全历史 + 白盒 regime 反事实（无条件在某 regime 内固定做某方向，v021_regime_gate_ev.csv）：

| node | direction | regime | n | mean_pnl | median_pnl | total_pnl | win_rate | worst | cvar5 |
|---|---|---|---|---|---|---|---|---|---|
| CONTROLX | BUY | POSITIVE | 4339 | −42.9 | +34.2 | −186,248 | 0.677 | −2251 | −820 |
| CONTROLX | BUY | NEUTRAL | 17064 | −3.2 | +5.2 | −53,965 | 0.632 | −3656 | −391 |
| CONTROLX | BUY | NEGATIVE | 1342 | −26.1 | +19.6 | −34,971 | 0.669 | −975 | −706 |
| ELCA | SELL | POSITIVE | 166 | −2.6 | +1.2 | −428 | 0.584 | −112 | −77 |
| ELCA | SELL | NEUTRAL | 2560 | −4.0 | +3.2 | −10,125 | 0.645 | −579 | −139 |
| ELCA | SELL | NEGATIVE | 994 | −4.8 | +6.5 | −4,814 | 0.655 | −914 | −231 |
| SNLNDRO | SELL | POSITIVE | 9484 | +2.6 | +2.9 | +24,738 | 0.672 | −577 | −33 |
| SNLNDRO | SELL | NEUTRAL | 11039 | +1.5 | +2.0 | +16,748 | 0.593 | −933 | −46 |
| SNLNDRO | SELL | NEGATIVE | 2222 | −1.2 | +1.3 | −2,588 | 0.544 | −859 | −89 |

**结论**：
1. **CONTROLX BUY 在三种 drift regime 下都是负 EV**（POSITIVE −42.9 / NEUTRAL −3.2 / NEGATIVE −26.1）。注意即使在 NEGATIVE regime，BUY 均值仍为负——右尾大正事件（6 月、9 月、4 月都出现）在"滞后均值为负"的窗口内照样发生。**R7a（拒 CONTROLX BUY）不是 test 正 regime 的产物，而是全历史三种 regime 都成立的结构规则**。
2. **ELCA SELL 在三种 regime 下也都是负 EV**（−2.6 / −4.0 / −4.8）→ R7b 同样稳健。
3. **SNLNDRO SELL 是唯一随 regime 翻号的方向**：POSITIVE/NEUTRAL 正 EV（+2.6/+1.5），NEGATIVE 负 EV（−1.2），但幅度很小（不会产生灾难）。SNLNDRO 本身是低波动节点，不构成尾部风险。
4. **这些规则不是"无差别"的**：它们恰好命中 CONTROLX BUY / ELCA SELL 这两个全 regime 负 EV 的"方向×节点"组合；而对 SNLNDRO 这类 regime 依赖型方向，gate 没有一刀切（V0.1 已弃用泛化漂移规则）。

### 6.1 val/test 双窗口核验（与 Gate 校准口径一致）

| node | direction | split | n | mean_pnl | total_pnl |
|---|---|---|---|---|---|
| CONTROLX | BUY | val | 3622 | −10.3 | −37,355 |
| CONTROLX | BUY | test | 1559 | −84.0 | −130,931 |
| ELCA | SELL | test | 1560 | −8.3 | −12,876 |

CONTROLX BUY 在 val 与 test 两个独立窗口均负 → **非 test 过拟合**，与 `risk_gate_v02_rules.md` §5 结论一致。

---

## 7. 诚实结论

1. **test = CONTROLX 强正漂移 + 高波动 regime，ELCA 转负、SNLNDRO 中性**；train→val→test 发生显著 regime shift（方向未变、量级暴涨）。
2. **Static SELL 的高收益 = regime 本身，不是 alpha**。
3. **Predictive Model 两级失败**：(a) 决策时点 6 月初正 regime 尚不可见（外部事件）；(b) regime 显现后模型仍 100% BUY 不更新，且因预测中位数、在 CONTROLX 上 er 与 actual 负相关。**模型在 CONTROLX 上没有识别 regime，反而在每个 regime 都逆漂移。**
4. **Risk Gate 的 CONTROLX BUY / ELCA SELL 规则不是 regime-specific**：全历史三种 regime 下这两组合都负 EV，是结构性护栏而非 test 过拟合；但注意这是"临时护栏"，若 CONTROLX 漂移结构未来翻转需复核。
5. **存在 regime 下模型无 alpha（如实写）**：test 的 POSITIVE 与 NEUTRAL regime 下，模型 BUY 都亏、SELL 也仅 CONTROLX 微亏/SNLNDRO 微赢——**本窗口没有任何"模型跑赢静态 SELL"的 regime**。
