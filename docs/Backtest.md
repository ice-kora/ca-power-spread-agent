# Backtest · V0.1 Baseline 回测引擎架构

> 状态：已实现（Agent D）｜ 代码：`code/backtest.py` ｜ 关联：`docs/business_contract.md` §8、`docs/backtest_outputs/backtest_report.md`、`docs/stage3/risk_gate_*.md`

## 1. 回测引擎：严格 as-of、walk-forward、模型通用

| 属性 | 说明 |
|---|---|
| 消费对象 | 任意模型预测 CSV（统一 schema，8 个核心列）：`node, target_date, hour, split, pred_direction, expected_return, confidence, prob_return_positive, actual_return`（`actual_direction` 缺失时由 `sign(actual_return)` 派生；`spread_std7`/`evidence` 等为可选列） |
| as-of 铁律 | 只用 decision_cutoff（D-1 10:00 PT，DAM Market Close / bid cutoff）可见信息；特征仅取 canonical X 列；不含 target_date 实际值 / `*_next` 天气 / label 区 |
| Rule（Model 0） | 天然 walk-forward：`expected_return` = 同 node 同 hour 近 14 日均值 `spread_mean14`（shift2，仅 target_date-2..-15）；`prob_return_positive` = 近 14 个同 hour 交付日 spread>0 占比（向 0.5 收缩）；`confidence` = 方向一致性 \|prob−0.5\|×2 |
| Interpretable（Model 1） | Logistic（方向）+ Linear（幅度），expanding-window 每 15 天用已实现标签重拟合；imputer/scaler 只由历史拟合 |
| CatBoost（Model 2） | Agent C 正式预测，按统一 schema 直接消费 |
| 评估窗口 | Agent C 文件：test 2026-06-02 ~ 2026-08-05（65 天）；框架自测：val+test（216 天）；主节点 ZP26（SNLNDRO/CONTROLX）与 ELCA（cold-start）分开评估 |
| 通用性 | `python backtest.py --preds PATH --label MyModel` 可对任意模型 CSV 单独回测 |

split 说明（诚实）：train 2025-04-03→2026-06-01、val 2026-01-02→2026-06-01、test 2026-06-02→2026-08-05；**val 是 train 的尾部子窗口**（用于阈值校准，非独立分区）。无泄漏自检：Rule/LR 特征 ⊂ canonical X 列、X 与 label 不相交、`spread_mean14` 与 master 重算一致（抽查 5 行）、预测 `actual_return` 与 canonical label 一致。

## 2. PnL 约定（1 MWh normalized，契约冻结）

| 决策 | 收益 |
|---|---|
| SELL_DA（Virtual Supply） | `+actual_return` |
| BUY_DA（Virtual Demand） | `−actual_return` |
| NO_TRADE | 0 |

`actual_return` = DA − RTPD（label，决策时点不可见，仅事后结算）。

## 3. 指标清单

| 指标 | 字段名 | 说明 |
|---|---|---|
| 方向准确率 | `dir_acc_all` / `dir_acc_traded` | 全样本 / 交易子集：`sign(pred_direction)==sign(actual_direction)` 占比 |
| 买卖精度 | `SELL_precision` / `BUY_precision` | SELL 行 `actual_return>0` 占比 / BUY 行 `<0` 占比 |
| 覆盖率 | `trade_coverage` | 交易数 / 样本数 |
| 胜率 | `win_rate` | 交易子集 `pnl>0` 占比 |
| 均值 / 中位数 PnL | `mean_pnl_per_trade` / median PnL | 单笔均值（核心引擎）；中位数由 stage3 回测补充 |
| 累计 PnL | `total_pnl` / `cum_final` | 1 MWh/仓美元额，非资本收益率 |
| 最大回撤 | `max_drawdown` | 日 PnL 累计曲线 peak-to-trough |
| 最差单笔 | `worst trade` | 最小单笔 PnL（stage3 补充） |
| 下行偏差 | `downside_dev` | 负收益日波动（stage3 补充） |
| CVaR / ES | `CVaR(5%)` / `CVaR(1%)` | 条件期望损失，尾部风险度量（stage3 补充） |
| 盈亏比 | `profit factor` | 总盈利 / 总亏损（stage3 补充） |
| Sharpe | `sharpe_daily` | mean(daily_pnl)/std(daily_pnl)×√252（日频年化） |
| 分项 | node / hour / month | 按节点、小时、月份聚合（`breakdown_node/hour/month`） |

> 核心引擎计算方向 acc、precision、coverage、win rate、mean/cum PnL、max DD、Sharpe 与 node/hour/month 分项；median PnL、worst、downside_dev、CVaR、profit factor 由 stage3 回测（`code/tmp/agent_d_backtest.py`）补充。

## 4. 回测比较表（test · ZP26 · 1 MWh/仓）

| 策略 | 交易数 | coverage | 方向acc(交易) | 累计PnL | 单笔均值 | 最大回撤 | Sharpe(日) |
|---|---|---|---|---|---|---|---|
| Static: always SELL（漂移基准） | 3,118 | 1.000 | 48.3% | **+132,746** | +42.57 | −30,859 | +4.87 |
| All-Trade[rule] | 824 | 0.264 | 60.4% | +79,485 | +96.46 | −15,691 | +4.21 |
| Decision[rule]（最终策略） | 542 | 0.174 | 58.7% | +79,202 | +146.13 | −15,877 | +4.19 |
| All-Trade[interpretable] | 1,876 | 0.602 | 66.2% | −124,072 | −66.14 | −147,833 | −4.61 |
| All-Trade[catboost] | 1,683 | 0.540 | 62.9% | −129,484 | −76.94 | −147,633 | −4.85 |

补充（诚实）：静态全 BUY 为镜像 −132,746；Decision[rule] 明细 = 540 SELL（+79,205）+ 2 BUY（−3），**BUY precision 0%**（两笔 BUY 全亏）；方向 acc 用交易子集口径。ELCA 单独评估（cold-start）：All-Trade[rule] −314、Decision[rule] +7，ML 模型均大幅亏损，不混入主结论。

## 5. Risk Gate 结果（test · ZP26 · Committee 口径）

| 策略 | 交易数 | coverage | cum PnL | max DD | worst | CVaR(1%) | Sharpe | PF |
|---|---|---|---|---|---|---|---|---|
| Model Committee | 1,523 | 48.8% | −130,042 | −147,957 | −2,216 | −1,056 | −4.87 | 0.39 |
| **Committee + Risk Gate** | 339 | 10.9% | **+962** | **−654** | **−113** | **−74** | +2.82 | 1.83 |
| 原 Rule（对照） | 824 | 26.4% | +79,485 | −15,691 | −1,175 | −1,122 | +4.21 | 3.29 |
| 原 Rule + Risk Gate | 824 | 26.4% | +79,485 | −15,691 | −1,175 | −1,122 | +4.21 | 3.29 |

gate 的全部作用 = 拦截 1,184 笔 CONTROLX BUY（ML 双模型一致看 BUY，逆 +84 漂移，被 −2,216 尾部打穿）；对 Rule **0 改变**（Rule 本不做 CONTROLX BUY）。Gate 后保留的 339 笔全部在 SNLNDRO。规则与阈值详见 `docs/stage3/risk_gate_design.md`。

## 6. 反事实（极端事件层面）

| 口径 | 结果 |
|---|---|
| Top50 亏损 | 拦 **36/50（72%）**、避免亏损 **35,544**；漏网 14 笔全是 Rule 的 CONTROLX SELL（gate 边界，train+val 证明不可事前识别） |
| Top50 盈利 | 误伤 **18/50（36%）**、错过盈利 **19,410**（全部为 CONTROLX BUY 彩票收益） |
| 净 | **+16,134**；全量 CONTROLX BUY 口径净 ≈ +236k |

## 7. 诚实结论

1. **盈利主要来自漂移、非预测**：静态全 SELL（+132,746）> All-Trade[rule]（+79,485）≈ Decision[rule]（+79,202）；2026-06 DA>RTPD 强正漂移 regime 主导，剔除尾部后策略反亏。
2. **准确率 ≠ 盈利（关键反例）**：Interpretable（acc 66%）、CatBoost（acc 63%）方向更准，All-Trade 却巨亏 −124k/−129k——CONTROLX 大量 BUY 在 ±数千 $/MWh 极端右尾上一次性巨亏；以 accuracy 最大化是错误导向。
3. **Gate 拦灾难、不创造 alpha**：把 Committee 从 −130k 转为 +962、max DD 从 −148k 收到 −654，但 coverage 降至 10.9%、只交易唯一正 EV 的 SNLNDRO；Rule 本身无增益。
4. **幅度预测无单调信息**：交易子集内 `expected_return` 与 `actual_return` Pearson=0.195 / Spearman=0.054；PnL top-50 贡献 54%（彩票式抓右尾）。
5. 局限：test 仅 65 天且 2026-06 强正漂移，外推受限；未做仓位优化与交易成本/冲击；天气时区 naive；confidence 未校准。
