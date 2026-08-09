# 极端盈利事件分析（Stage 3 · Agent B）

> 2026-08-09 ｜ 严格 as-of 回测（无泄漏，契约冻结口径）｜ 三模型：Rule / Interpretable(LR) / CatBoost
> PnL 口径：SELL 收益 = `actual_return`；BUY 收益 = `−actual_return`；NO_TRADE = 0；交易子集 = `pred_direction != 0` 的 test 行。
> 数据：`code/data/predictions_{rule,interpretable,catboost}.csv`（各 4,678 行 test）+ `canonical.parquet`。

---

## 0. 诚实摘要（先说结论）

**合并 Top 50 盈利事件 = 100% CONTROLX_1_N001 一个节点的极端负价格事件。这些收益是"彩票式抓到极端事件"，不是模型真识别。**

证据链（详见各节）：
1. **50/50 全部是 CONTROLX**；13 个交易日、4 个极端负价格时段贡献全部合并 Top 50（合计 +47,847）。
2. **幅度全部来自极端价格**：Top 50 的 `actual_return` 全部落在 CONTROLX test 分布的极端尾部（正侧 p95=795 / p99=906 以外，最高 +2216；负侧 p1=−915 以外，最低 −1305；节点 std 仅 344）；其中 18 笔 `actual_da < −500`、32 笔 `actual_rtpd < −500`（RTPD 最低 −2365，DA 最低 −1325）。
3. **事前特征已极端（能"上车"，不能"选日"）**：Top 50 事件前一天 `spread_lag1` 中位数 692（CONTROLX test 第 89 百分位）、`spread_mean7` 241（P80）、`spread_std7` 396（P78）、前日 RTPD 均值 −470（P15）——极端负价格**已经持续了数天**。但负荷预报/天气/peer 均无异常（见 §4），没有任何基本面信号指向"哪一天、多大"。
4. **高 confidence 是"行情持续"的机械结果，不是识别力**：Rule 的 confidence = 近 14 日同向比例，负价格行情持续多日 → 机械接近 1；Interpretable 在 06-30 的 BUY confidence 0.93–0.99，但这正是同一批模型在全样本巨亏（CONTROLX −124k/−131k）的来源。
5. **收益高度集中，且与巨亏同源**：Rule 的利润 **99% 来自 |return|>500 的极端行**（77,861 / 78,645），Top 50 占其总利润 53.8%、Top 100 ≈ 100%；同一批 CONTROLX node/hour 既大赚又大亏（Rule 重叠 7/20，Interpretable 12/20，CatBoost 11/20）。Rule 在最大 BUY 日 06-30 上反而亏 −14,965。

---

## 1. 方法口径

- 三模型 test 交易子集 PnL 计算（1 MWh/仓）：
  | 模型 | 交易数 | 总 PnL | 胜率(交易) | 总 PnL 方向 |
  |---|---|---|---|---|
  | Rule | 996 | **+79,171** | 61.5% | 唯一盈利 |
  | Interpretable | 3,133 | −134,250 | 64.8% | 巨亏 |
  | CatBoost | 2,781 | −138,162 | 63.5% | 巨亏 |
- 每模型取 Top 20 / Top 50（按 PnL 降序），合并去重（`node,target_date,hour`，保留该行最高 PnL）→ **合并 Top 50**（合计 +47,847，13 个交易日）。
- 每笔事件输出：node / target_date / hour / action / actual_DA / actual_RTPD / actual_Return / PnL / pred_direction / confidence / expected_return / 三模型方向与一致性 / 事前特征（spread lag·rolling、历史分位数、负荷预报、天气滞后、日历、node bias）。
- 全量明细：`code/data/stage3/top_profit_events.csv`（50 行 × 56 列，utf-8-sig）。每模型 Top 50 另存 `code/data/stage3/top50_{rule,interpretable,catboost}.csv`。

---

## 2. 合并 Top 50 概览

### 2.1 事件构成
| 维度 | 值 |
|---|---|
| 节点 | **CONTROLX_1_N001 × 50**（100%） |
| action | SELL × 32（+28,437）/ BUY × 18（+19,410） |
| 胜出模型 | Rule（SELL 32 笔，+28,437）；Interpretable（BUY 18 笔，+19,410）；CatBoost 无一笔独占（与 Interpretable 同事件且 PnL 相同） |
| 日期 | 13 天，全部集中在 **2026-06-16 ~ 07-10**（48/50 在 6 月） |
| hour 带 | evening 18–24 × 21；morning 6–12 × 16；earlyAM 1–5 × 7；afternoon 13–17 × 6 |
| confidence | 中位 0.917（Interpretable BUY：0.925–0.994；Rule SELL：0.707–1.0） |

### 2.2 四个盈利时段（Episodes）——合并 Top 50 的全部来源
| 时段 | 日期 | 事件数 | PnL | 机制 |
|---|---|---|---|---|
| Ep1 | 06-16 ~ 06-23（8 天） | 30 | +25,701 | SELL：DA 触及 −150 地板，RTPD 深跌至 −900 ~ −1102 → return +800 ~ +952 |
| Ep2 | 06-26 ~ 06-27 | 2 | +1,644 | SELL：同上（RTPD −983/−1251） |
| Ep3 | 06-30 | 16 | +17,483 | BUY：DA 暴跌至 −1039 ~ −1325，RTPD 稳定在 −150 附近 → return −1004 ~ −1305，BUY 得利 |
| Ep4 | 07-09 ~ 07-10 | 2 | +3,019 | SELL：07-09 RTPD 创记录 −2365（return +2216，全样本最大单笔） |

> 所有 50 笔都嵌套在"DA 和/或 RTPD 深度为负"的时段内：32 笔 SELL 的收益来源是 **RTPD 比 DA 更深地贴地板**（DA≈−150 地板 vs RTPD −900~−2365）；18 笔 BUY 的收益来源是 **DA 单日崩盘**（DA −1000~−1325 vs RTPD≈−150）。

### 2.3 合并 Top 20 明细（全 50 行 × 56 列见 CSV）
| # | date | hour | act | DA | RTPD | Return | PnL | conf | 胜出模型 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 07-09 | 2 | SELL | −149 | −2365 | +2216 | +2216 | 0.82 | rule |
| 2 | 06-30 | 19 | BUY | −1295 | +10 | −1305 | +1305 | 0.95 | interp |
| 3 | 06-30 | 14 | BUY | −1325 | −150 | −1175 | +1175 | 0.97 | interp |
| 4 | 06-30 | 12 | BUY | −1316 | −150 | −1166 | +1166 | 0.99 | interp |
| 5 | 06-30 | 7 | BUY | −1313 | −150 | −1163 | +1163 | 0.93 | interp |
| 6 | 06-30 | 18 | BUY | −1231 | −72 | −1159 | +1159 | 0.95 | interp |
| 7 | 06-30 | 9 | BUY | −1298 | −150 | −1148 | +1148 | 0.99 | interp |
| 8 | 06-30 | 5 | BUY | −1293 | −150 | −1143 | +1143 | 0.94 | interp |
| 9 | 06-30 | 6 | BUY | −1256 | −150 | −1106 | +1106 | 0.95 | interp |
| 10 | 06-30 | 21 | BUY | −1084 | +22 | −1106 | +1106 | 0.94 | interp |
| 11 | 06-23 | 4 | BUY | −1052 | +24 | −1076 | +1076 | 0.98 | interp |
| 12 | 06-30 | 13 | BUY | −1211 | −150 | −1061 | +1061 | 0.97 | interp |
| 13 | 06-30 | 20 | BUY | −1039 | +18 | −1057 | +1057 | 0.93 | interp |
| 14 | 06-30 | 22 | BUY | −1057 | −20 | −1036 | +1036 | 0.92 | interp |
| 15 | 06-30 | 10 | BUY | −1166 | −150 | −1016 | +1016 | 0.99 | interp |
| 16 | 06-30 | 4 | BUY | −1175 | −170 | −1005 | +1005 | 0.94 | interp |
| 17 | 06-20 | 9 | SELL | −150 | −1102 | +952 | +952 | 0.71 | rule |
| 18 | 06-30 | 17 | BUY | −1095 | −151 | −944 | +944 | 0.95 | interp |
| 19 | 06-19 | 24 | SELL | −149 | −1091 | +942 | +942 | 0.77 | rule |
| 20 | 06-20 | 11 | SELL | −150 | −1088 | +938 | +938 | 0.77 | rule |

> 胜出模型 = 该 (node,date,hour) 上三模型 PnL 最高的模型（"rule"=Rule SELL；"interp"=Interpretable BUY，CatBoost 的 BUY 与 Interpretable 同事件同 PnL，无一笔独占）。每笔的完整事前特征（spread lag/rolling、负荷预报、天气滞后、日历、node bias）见 `code/data/stage3/top_profit_events.csv`。

---

## 3. 五个问题

### Q1 为什么这些交易赚很多？（幅度来源：正常波动 vs 极端事件）

**极端事件，不是正常波动。**
- CONTROLX test 的 `actual_return` 均值 84、中位数 −34、标准差 **344**、p95 = 795、p99 = 906、p1 = −915。而合并 Top 50 的 |return| = 803 ~ **2216**——全部处于节点的极端尾部：正侧在 p95/p99 之外（最高 +2216，z=+13），负侧在 p1 之外（最低 −1305，z=−7.8）。
- 幅度分解：18/50 笔 `actual_da < −500`（最深 −1325），32/50 笔 `actual_rtpd < −500`（最深 **−2365**）。DA 与 RTPD 同时为负，但一深一浅，产生 ±800~2200 的价差。正常时段 CONTROLX 价差仅 ±几十 $/MWh。
- 全样本极端事件普查（|return|>500）：CONTROLX test 有 **279 行**（占其 test 小时 17.9%）、train 225、val 188——CONTROLX 的极端负价格在样本期是**持续数月的高频现象**（不是偶然一天）。
- **Rule 的利润来源被极端行完全主导**：Rule 在 CONTROLX 交易 362 笔、其中 149 笔命中 |return|>500 极端行，这 149 笔贡献 **+77,861，占 Rule CONTROLX 总利润的 99.0%**。

### Q2 是模型真识别，还是碰巧抓到极端事件？

**是"碰巧/顺势上车"，不是"识别出具体事件"。事前状态已极端（能判断行情在、不能判断哪天、多大）。**

- 事前特征（决策时可见，全部 as-of）在 Top 50 事件前**已经高度极端**：
  | 事前特征 | Top 50 中位数 | CONTROLX-test 中位数 | Top 50 所在百分位 |
  |---|---|---|---|
  | spread_lag1 | 692 | −33 | **P89** |
  | spread_mean7 | 241 | 7 | **P80** |
  | spread_std7 | 396 | 215 | **P78** |
  | spread_mean14 | 246 | 33 | **P83** |
  | spread_mean30 | 124 | 76 | P64 |
  | spread_day_range_lag1 | 889 | 380 | P69 |
  | rtpd_day_mean_lag1 | −470 | −24 | **P15**（前日 RTPD 已深负） |
  | da_day_mean_lag1 | −148 | −51 | P26 |
  | load_2da_forecast | 23,739 | 26,351 | P28（负荷低于常态，无紧张信号） |
  | load_actual_day_mean_lag1 | 28,162 | 29,653 | P35 |
  | t2m_lag1 / ssrd_lag1 / wind100_lag1 | 21.4 / 98 / 3.0 | 22.2 / 158 / 2.9 | P43 / P47 / P48（天气无异常） |
  | peer_spread_lag1 | 0.7 | 2.1 | P43 |

- 直接计数：Top 50 中 **46/50** `|spread_lag1| > 100`、**43/50** `spread_std7 > 300`、**47/50** `rtpd_day_mean_lag1 < −300`——事件前极端状态已持续至少 1–2 周。
- **但没有任何独立基本面触发信号**：负荷预报处于低百分位（P28，低于常态）、天气/peer 全在中位附近。模型无法解释"为什么 06-30 DA 会崩到 −1325"或"为什么 07-09 RTPD 会到 −2365"。Rule 在 06-30（最大 BUY 日）反而全面看 SELL、亏损 **−14,965**——**最赚的 BUY 事件恰是 Rule 判断相反的同一事件**。
- 幅度预测无信息（与 Lead 阶段结论一致）：全交易子集 `expected_return` 与 `actual_return` 的相关系数 Rule 0.29 / Interp −0.21 / CatBoost −0.20；在合并 Top 50 内的 0.91 是**按极端结果选择后的虚假相关**（selection on the outcome）。

**结论：三模型捕捉到的是"CONTROLX 正处于极端负价格行情"这一持续状态（Persistence），而非"明天 RTPD 会到 −2365"这种事件识别。收益来自站在行情的正确一侧把尾部吃掉，恰好这批交易押对了方向。**

### Q3 当时 confidence 是否真的高？在盈利桶里 confidence 与盈利关系如何？

**合并 Top 50 的 confidence 确实高（中位 0.92），但它是"行情持续"的机械产物，且高 confidence 的极端押注同时是巨亏的来源。**

- 合并 Top 50：confidence 0.707–1.0，中位 0.917；Interpretable BUY 组 0.925–0.994（均值 0.959）、Rule SELL 组 0.707–1.0（均值 0.836）。
- 盈利桶内 confidence 与 PnL 关系（按 confidence 四分位分桶，只看 pnl>0 的交易）：
  | 模型 | Q1(低 conf) 均 PnL | Q2 | Q3 | Q4(高 conf) 均 PnL |
  |---|---|---|---|---|
  | Rule | 25.7 | 232.3 | 258.8 | 238.6 |
  | Interpretable | 20.5 | 48.0 | 40.8 | **122.7** |
  | CatBoost | 11.1 | 20.9 | 55.4 | **139.4** |
  → 高 confidence 桶确实盈利均值更高（尤其 Interp/CatBoost 的 Q4 是 Q1 的 6–12 倍）。
- **但要警惕**：Rule 的 confidence = 近 14 日同向比例。负价格行情连续多日 → confidence 机械飙到 0.7–1.0。**高 confidence 编码的是"过去两周都在涨/跌"，不是"明天会极端"**。Interp/CatBoost 的 0.9+ confidence 出现在 06-30 的 BUY——而同一模型在 CONTROLX 其他高 confidence 极端押注上被尾部打穿（Interp −124,687 / CatBoost −130,834，均为 CONTROLX 贡献）。
- 即：**高 confidence 与高盈利的相关只存在于"押对极端行情一侧"的子集里；一旦押错一侧，同样的高 confidence 直接对应最大亏损。** confidence 对"方向"有一点点校准力，对"幅度/风险"完全无保护。

### Q4 是否存在重复出现的可解释模式（node / hour / 滞后状态）？

**有，但模式是"CONTROLX 的极端负价格时段"，不是稳定的 node/hour/滞后规则。**

- **node 极度单一**：50/50 = CONTROLX（SNLNDRO 0 笔，ELCA 0 笔）。CONTROLX 是样本内唯一拥有极端负价格右尾的节点（test 279 行 |return|>500；SNLNDRO 仅 train 2 行）。
- **时间高度聚集**：48/50 在 2026-06（06-16~06-30），2/50 在 07-09~10。4 个连续时段贡献全部。
- **hour 分布**：覆盖全部小时，晚间略多（evening 18–24 × 21，占 42%；按 24 小时均匀应为 29%）。但昼夜不是强区分——行情持续时几乎每个小时都极端。
- **滞后状态**（可复现的前兆）：事件前 `spread_lag1` 高位（P89）、`spread_std7` 高位（P78）、前日 `rtpd_day_mean_lag1` 深负（P15）——**"过去已有极端价差 + 高波动"是持续模式**，但该模式在 CONTROLX test 中很常见（18% 的小时 |return|>500），并不能把"明天会出 −2365"挑出来。
- 日历：is_holiday 4/50、solar_flag 13/50，无明显驱动。

### Q5 收益是否高度依赖极少数彩票式事件？

**是，极端集中。**
| 口径 | Top20 占总额 | Top50 占总额 | Top100 占总额 |
|---|---|---|---|
| Rule（自身交易集，+79,171） | 23.6% | **53.8%** | **≈99.9%** |
| 三模型逐行取最高 PnL（best-per-row union，+89,234） | 25.5% | **53.6%** | 96.4% |
- 合并 Top 50（+47,847）只覆盖 **13 个交易日 / 4 个时段**，其中单日 06-30 就贡献 16 笔 +17,483（合并 Top 50 的 36.5%）。
- **Rule 的全部利润 = 少数极端行**：149 笔 |return|>500 的 CONTROLX 交易贡献 99.0%；Top 100 笔 ≈ 全部利润。样本期若删除这几周，Rule 基本归零。
- 对 Interp/CatBoost 而言，Top 20/50 只是肥尾右端（各自 +31k 左右），相对其 −124k/−131k 总亏损微不足道——**同一批极端事件既制造了合并 Top 50 的"神话"，也制造了全样本的"巨亏"**。

---

## 4. 汇总

### 4.1 盈利集中度
- **节点**：100% CONTROLX（50/50）。CONTROLX 是唯一带极端负价格尾部的节点。
- **事件**：13 天 / 4 时段贡献全部合并 Top 50；06-30 一天占 36.5%。
- **尾部依赖**：Rule 99.0% 利润来自 |return|>500 极端行；Top 100 交易 ≈ 100% 总利润；Top 50 = 53.8%。
- **方向**：Rule 全部利润来自 SELL（990 SELL +79,210 / 6 BUY −39）；合并 Top 50 中 32 SELL（Rule）+ 18 BUY（Interpretable）。

### 4.2 盈利模式
- **SELL 模式（Ep1/2/4）**：DA 贴 −150 地板，RTPD 深跌至 −900 ~ −2365 → `actual_da − actual_rtpd` 巨大正价差，虚拟供电 SELL 每 MWh 赚 800~2200。本质是"负价格时段买入 RTPD 的负价 + 在 DA 地板价卖出"的价差套利，由持续数周的极端负价行情驱动。
- **BUY 模式（Ep3）**：06-30 DA 单日崩盘至 −1000 ~ −1325 而 RTPD 保持 −150 → 巨大负价差，虚拟购电 BUY 每 MWh 赚 1000~1300。本质是"DA 崩盘日"的单日事件。
- 两者同根：**CONTROLX 深度负价格**（CAISO 春季/初夏太阳能盈余 + 负电价），模型只是站在了价差大的方向，且运气上选对了边。

### 4.3 与亏损分析的对比（同一批 node/hour 既大赚又大亏？）
**是的，高度重叠。**
- 各模型 Top20 盈利 ∩ Top20 亏损 的 (node,hour) 重叠：Rule **7/20**、Interpretable **12/20**、CatBoost **11/20**。重叠小时（H4/H8/H11/H14/H20/H21/H24 等）都是 CONTROLX 出现 ±800~2000 $/MWh 摆动的时段。
- 模型级：Rule 在 CONTROLX +78,645 而 Interp −124,687、CatBoost −130,834。**同一个节点、同一批小时，仅凭一天方向选对/选错，就是 +1000 或 −1000。** 盈利与亏损是同一肥尾分布的两侧，不是两套不同的事件。
- 单日反例：06-30 是 Interp/CatBoost 最大盈利日（+17,483），Rule 同日亏损 **−14,965**——最大盈利事件与最大亏损事件发生在完全相同的行情上，完全取决于模型的方向。

---

## 5. 诚实结论（不粉饰）

> **合并 Top 50 盈利事件是"彩票式抓到极端事件"，不是模型真识别。**

1. **收益真实但来源是尾部**：严格 as-of、无泄漏，PnL 数正确；但 100% 来自 CONTROLX 一个节点的极端负价格事件，99% 的 Rule 利润来自 |return|>500 的极端行。
2. **事前有"状态信号"，无"事件信号"**：事件前 spread 水平/波动/前日 RTPD 均已极端（P78–P89），说明**极端行情已持续数天**——模型能感知"在行情里"，但不能识别"哪一天、多大"。负荷/天气/peer 无任何基本面预示（负荷预报反而在低百分位）。
3. **高 confidence 是行情持续的机械产物**：Rule 的 confidence = 14 日同向比例，行情持续则机械接近 1；这解释不了具体事件，也不保护幅度风险。
4. **极端集中 = 彩票结构**：Top 50 只覆盖 13 天/4 时段，单日 06-30 占 36.5%；Top 100 ≈ 全部利润。删掉这几周，Rule 归零。
5. **盈利与亏损同源**：同一批 CONTROLX node/hour 既产生合并 Top 50 又产生 Interp/CatBoost 的 −124k/−131k 巨亏；Rule 在最大盈利日（06-30）反向亏损 −14,965。

**对考核方的一句话**：这套模型在样本期的盈利 ≈"在一个持续负电价的节点上站对了方向 + 赌中了几天极端行情"，是右尾彩票，不是可复现、可风控、可外推的预测能力；若不接受这种彩票结构，应视同不可实盘。

---

## 附：交付物
- 本报告：`docs/stage3/top_profit_event_analysis.md`
- 合并 Top 50 全量明细（56 列，含全部事前特征）：`code/data/stage3/top_profit_events.csv`
- 每模型 Top 50：`code/data/stage3/top50_{rule,interpretable,catboost}.csv`
- 模型归属标注：`code/data/stage3/top_profit_winmodel.csv`
- 诊断数值：`code/data/stage3/top_profit_diagnostics.json`
