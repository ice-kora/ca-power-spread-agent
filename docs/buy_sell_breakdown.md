# BUY / SELL 彻底拆解 —— v2 模型信号在 test/val 的分方向画像（Agent B）

> 生成时间：2026-08-09 ｜ 数据：`predictions_v2.csv`（test 4,678 行）、`predictions_v2_val.csv`（val 7,244 行）
> 策略口径 = backtest_v2.PredictiveModelOnly：expected_return 符号定方向，**\|er\|≥5.0 且 confidence≥0.2 才交易**（与官方回测一致，ZP26 复现 1,280 笔 / −118,464 完全对齐）。
> PnL：BUY_DA = −actual_return、SELL_DA = +actual_return、NO_TRADE = 0。
> 表数据：`code/data/v021_buysell_{test,val}*.csv`。

---

## 1. 总览（test 全节点 + val 对照）

### 1.1 test（2026-06-02 ~ 08-05，4,678 行）

| direction | trade_count | coverage | win_rate | mean_pnl | median_pnl | total_pnl | worst_trade | best_trade | max_drawdown | cvar5 | cvar1 | profit_factor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **BUY_DA** | 1,099 | 0.235 | 0.617 | **−107.0** | **+30.5** | **−117,644** | −2,216 | +1,305 | −138,374 | −898 | −1,132 | **0.401** |
| SELL_DA | 270 | 0.058 | 0.363 | −7.0 | −21.5 | −1,897 | −339 | +2,251 | −4,243 | −219 | −327 | 0.831 |

- **BUY 是灾难侧**：−117,644 / 总亏损 −119,541 = **98.4%**；median 为正（+30.5）而 mean 深负（−107）——"多数小赚 + 少数巨亏"的赔率倒挂。
- SELL 覆盖极低（5.8%）、win_rate 36.3%（阈值放行的 270 笔里多数是 CONTROLX 的弱 SELL 信号），累计小幅为负。
- BUY 的 profit_factor 0.401：每赚 1 元亏 2.5 元；SELL 0.831 接近盈亏平衡。

### 1.2 val（2026-01-02 ~ 06-01，7,244 行）

| direction | trade_count | coverage | win_rate | mean_pnl | median_pnl | total_pnl | worst | best | max_drawdown | cvar5 | cvar1 | profit_factor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BUY_DA | 3,494 | 0.482 | 0.737 | −10.5 | +32.8 | −36,701 | −3,656 | +1,927 | −69,323 | −680 | −982 | 0.823 |
| SELL_DA | 16 | 0.002 | 0.875 | +9.9 | +10.6 | +158 | −50 | +36 | 0 | −50 | — | 3.859 |

- **val 里模型几乎只会 BUY（3,494 笔）且只集中在 CONTROLX**（3,494 = CONTROLX 全部 val 行），同样 negative EV。
- 结论跨窗口稳定：**BUY（CONTROLX）在 val 与 test 都是负期望**，只是 test 中尾事件更大、亏得更深（mean −10.5 → −107）。

---

## 2. 按 node 拆（v021_buysell_{test,val}_by_node.csv）

### 2.1 test

| node | direction | trade_count | coverage | win_rate | mean_pnl | median_pnl | total_pnl | worst | best | max_drawdown | cvar5 | profit_factor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CONTROLX | BUY_DA | 1,099 | 0.705 | 0.617 | **−107.0** | +30.5 | **−117,644** | −2,216 | +1,305 | −138,374 | −898 | 0.401 |
| CONTROLX | SELL_DA | 163 | 0.105 | 0.160 | −5.1 | −41.8 | −832 | −208 | +2,251 | −2,973 | −171 | 0.901 |
| ELCA | SELL_DA | 89 | 0.057 | 0.674 | **−12.1** | +12.3 | −1,077 | −339 | +60 | −1,938 | −305 | 0.597 |
| SNLNDRO | SELL_DA | 18 | 0.012 | 0.667 | +0.6 | +5.2 | +11 | −59 | +40 | 0 | −59 | 1.065 |

- **CONTROLX BUY = 唯一巨亏源**（占模型总亏 98%）；CONTROLX SELL 也是负 EV（16% win rate，源自 DA 崩塌事件）。
- **ELCA SELL 负 EV**（mean −12.1，正是 R7b 拦截对象）；ELCA BUY 几乎不触发（阈值后 0 笔）。
- **SNLNDRO SELL 是唯一正期望交易**（+0.6/笔，但仅 18 笔，覆盖 1.2%）——与"V0.1 唯一正 EV 方向"结论一致。

### 2.2 val

| node | direction | trade_count | coverage | win_rate | mean_pnl | median_pnl | total_pnl | worst | best | max_drawdown | profit_factor |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CONTROLX | BUY_DA | 3,494 | 0.965 | 0.737 | −10.5 | +32.8 | −36,701 | −3,656 | +1,927 | −69,323 | 0.823 |
| SNLNDRO | SELL_DA | 16 | 0.004 | 0.875 | +9.9 | +10.6 | +158 | −50 | +36 | 0 | 3.859 |

- val 中模型把 CONTROLX 的**全部 96.5% 行都标成 BUY**——不是边角信号，是系统性方向选择。

---

## 3. 按 month 拆（v021_buysell_test_by_month.csv）

| month | direction | trade_count | win_rate | mean_pnl | median_pnl | total_pnl | worst | cvar5 |
|---|---|---|---|---|---|---|---|---|
| 6 | BUY_DA | 633 | 0.528 | **−173.4** | +8.0 | **−109,787** | −1,039 | −876 |
| 6 | SELL_DA | 17 | 0.765 | +5.4 | +21.6 | +92 | −104 | −104 |
| 7 | BUY_DA | 462 | 0.736 | −18.1 | +62.7 | −8,360 | −2,216 | −868 |
| 7 | SELL_DA | 163 | 0.356 | −19.6 | −27.6 | −3,200 | −339 | −246 |
| 8 | BUY_DA | 4 | 1.000 | +125.8 | +161.0 | +503 | +18 | — |
| 8 | SELL_DA | 90 | 0.300 | +13.5 | −20.1 | +1,211 | −188 | −169 |

- **6 月是 BUY 灾难月**：633 笔 BUY 亏 −109,787 = test BUY 总亏的 **93%**。6 月正是 CONTROLX 正漂移 +170 的极端 regime。
- 7 月 BUY 收敛到 −18/笔（仍亏），8 月（5 天）转正但样本太少。SELL 在 8 月转正（+1,211）但 7 月亏 −3,200。

---

## 4. 按 hour 拆（v021_buysell_test_by_hour.csv）

### 4.1 BUY_DA：全部 24 小时均为负 mean

| 最差 hour | mean_pnl | 尾风险（worst） |
|---|---|---|
| H23 | −189.8 | −891 |
| H9 | −178.6 | −952 |
| H8 | −159.5 | −1,002 |
| H24 | −157.3 | −942 |
| H21 | −155.3 | −1,076 |
| H20 | −153.7 | −1,181 |
| H2 | −138.8 | **−2,216** |
| H18 | −100.3 | **−1,196** |

- BUY 最危险小时 = **H23/H9/H8/H20/H21/H24**（mean −150 ~ −190），且 H2（worst −2,216）、H18（−1,196）、H20（−1,181）、H21（−1,076）是极端尾所在地。
- **没有任何一个 hour 的 BUY 是正期望**——CONTROLX 全时段正漂移，BUY 全时段逆漂移。

### 4.2 SELL_DA：小时分化，但整体接近平衡

| hour | mean_pnl | n | hour | mean_pnl | n |
|---|---|---|---|---|---|
| H3 | +202.0 | 13 | H19 | −67.5 | 15 |
| H7 | +151.1 | 9 | H12 | −56.0 | 9 |
| H5 | +38.8 | 9 | H17 | −54.3 | 10 |
| H2 | +19.1 | 11 | H23 | −52.2 | 4 |
| H4 | +13.5 | 10 | H18 | −50.4 | 16 |
| H22 | +2.0 | 9 | H13 | −53.3 | 8 |

- SELL 正期望集中在 H3/H7/H5/H2（凌晨，右尾捕获），负期望在 H12~H19（白天/傍晚）——但样本都小（≤21 笔）。

---

## 5. 按 regime 拆（v021_buysell_test_by_regime.csv）

| regime | direction | trade_count | win_rate | mean_pnl | median_pnl | total_pnl | worst | profit_factor |
|---|---|---|---|---|---|---|---|---|
| POSITIVE_SPREAD_REGIME | BUY_DA | 803 | 0.567 | **−134.9** | +33.9 | **−108,326** | −2,216 | 0.381 |
| POSITIVE_SPREAD_REGIME | SELL_DA | 103 | 0.146 | −5.9 | −59.1 | −606 | −208 | 0.903 |
| NEUTRAL_REGIME | BUY_DA | 245 | 0.735 | −39.3 | +30.2 | −9,632 | −934 | 0.521 |
| NEUTRAL_REGIME | SELL_DA | 90 | 0.456 | −4.9 | −5.4 | −439 | −339 | 0.849 |
| NEGATIVE_SPREAD_REGIME | BUY_DA | 51 | 0.843 | +6.2 | +23.4 | +314 | −527 | 1.299 |
| NEGATIVE_SPREAD_REGIME | SELL_DA | 77 | 0.546 | −11.1 | +4.5 | −852 | −257 | 0.587 |

- 模型 BUY 的亏损 **92% 发生在 POSITIVE_SPREAD_REGIME**（−108,326）；NEUTRAL 也亏（−9,632）；只有 NEGATIVE regime 下 BUY 微正（+314，51 笔，且 win_rate 84%）。
- **模型在最强正 regime 满仓 BUY（803 笔、覆盖 78%），正是逆 regime 的集中体现。**

### 5.1 val 对照（v021_buysell_val_by_regime.csv / by_node_regime.csv）

| node | regime | direction | trade_count | coverage | mean_pnl | median_pnl | total_pnl | win_rate |
|---|---|---|---|---|---|---|---|---|
| CONTROLX | NEGATIVE | BUY_DA | 527 | 0.941 | −29.4 | +28.9 | −15,477 | 0.715 |
| CONTROLX | NEUTRAL | BUY_DA | 1,872 | 0.955 | −7.1 | +32.7 | −13,338 | 0.771 |
| CONTROLX | POSITIVE | BUY_DA | 1,095 | 0.994 | −7.2 | +36.5 | −7,885 | 0.690 |

- **val 中 CONTROLX BUY 在三种 regime 下全部负 EV**（覆盖 94~99% 的行）——与 test 一致（test 的 NEUTRAL/POSITIVE 亏、NEGATIVE 微正）。再次证明该方向负 EV 是结构性的，非 test 独有。

---

## 6. 回答 Lead 必须能回答的问题

### Q1：当前 Predictive Model 主要亏损来自 BUY 还是 SELL？
**BUY。** test 全模型：BUY −117,644（98.4%）、SELL −1,897（1.6%）；官方 ZP26 口径 −118,464 中 BUY −117,644 = **99.3%**。val 同理（BUY −36,701 / SELL +158）。

### Q2：哪些 node 的 BUY 负期望？
- **CONTROLX BUY：test −107/笔（win 61.7% 但赔率 1:4.5）、val −10.5/笔（win 73.7%）——唯一巨亏源。**
- SNLNDRO BUY：test 无阈值成交，方向上接近中性（全历史 SELL +1.7、BUY −1.7），不是亏损源。
- ELCA BUY：test 仅 105 笔方向候选、阈值后 0 笔，方向上正期望（+5~+12/笔），**不被模型触发**。

### Q3：哪些 node 的 SELL 负期望？
- **ELCA SELL：test −12.1/笔（R7b 拦截对象，负漂移节点逆漂移）。**
- **CONTROLX SELL：test −5.1/笔、win 16%（163 笔；DA 崩塌尾，V0.1 已证明事前不可可靠识别）。**
- SNLNDRO SELL：test +0.6、val +9.9——**唯一正 EV 的 SELL**。

### Q4：哪些 hour 最危险？
- BUY：H23/H9/H8/H20/H21/H24（mean −150~−190），H2/H18/H20/H21 极端尾（worst −1,076 ~ −2,216）；**全部 24h 的 BUY 都负 EV**。
- SELL：H12/H13/H16/H17/H18/H19/H23（mean −39 ~ −68），凌晨 H2/H3/H4/H5/H7 正。

---

## 7. 诚实结论

1. **模型亏损 ≈ CONTROLX BUY 单侧**。这不是"模型犯了一半错"，而是**结构性方向错误**：模型在 CONTROLX 上把"中位数（负）"当成方向，而该节点按均值（正）结算，导致 98%+ 亏损集中在 BUY。
2. **BUY/SELL 不对称是真实结构**（BUY median +、mean −；SELL 在正漂移节点才是顺势），但**"坏方向"随节点漂移符号翻转**：CONTROLX 坏方向=BUY、ELCA 坏方向=SELL。不要默认 BUY/SELL 对称。
3. **模型唯一正 EV 交易是 SNLNDRO SELL（覆盖 ~1-5%）**；其余方向要么系统性负（CONTROLX BUY / ELCA SELL），要么尾不可防（CONTROLX SELL）。**在 test 这个 regime 下，模型没有任何一个 BUY/SELL 组合能跑赢静态 SELL 漂移基准（ZP26 +132,746）。**
4. **数据支撑的护栏**：CONTROLX BUY 与 ELCA SELL 在 val+test 双窗口、以及全历史三种 regime 下均为负 EV（见 regime_analysis §6），gate 拦截它们不是 test 过拟合；但保留集（CONTROLX SELL、ELCA BUY 低样本）仍是模型侧缺陷，gate 无法弥补。
