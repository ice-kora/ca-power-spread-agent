# CA-ISO 价差交易 · Risk Gate 反事实分析（Agent D）

> 生成时间：2026-08-09
> 输入：`code/data/stage3/top_loss_events.csv`（Agent A 合并 Top50 亏损，worst_pnl 口径）
>        `code/data/stage3/top_profit_events.csv`（Agent B 合并 Top50 盈利，pnl 口径）
> 方法：逐笔事件，把 Risk Gate 应用到"worst 模型的 action"（BUY/SELL 方向），
>        用该事件行的 as-of 风险特征（cvar99/rcvar99/hist_n/node 漂移/三模型方向）判定
>        PASS / REJECT。REJECT = 该笔当时会被 gate 拦下。
> 口径注意：反事实针对"每笔事件"（按 worst 模型方向），不是全量交易流水；
>        prevented loss = 被拦事件 worst_pnl 之和（正数 = 避免的亏损）。

---

## 0. 结论先行（诚实）

**Top50 亏损中 36/50（72%）会被 Risk Gate 事前拦截，避免 35,544 $/MWh 亏损；但 Top50 盈利中有 18/50（36%）会被误伤，错过 19,410 盈利。避免的亏损 > 错过的盈利（净 +16,134），gate 在极端事件层面是净改善的。**

关键机制（一句话）：
- **被拦截的 36 笔 = 35 笔 CONTROLX BUY + 1 笔 ELCA SELL** —— 全部由 R7 方向门拦截（`BUY_ON_POSITIVE_DRIFT_NODE` / `SELL_ON_NEGATIVE_DRIFT_NODE`）。
- **漏网的 14 笔 = 全部是 Rule 的 CONTROLX SELL**（DA 暴跌日 2026-06-23/06-30 等）——gate 不拦 CONTROLX SELL，因为 train+val 证明无法用事前内部信号可靠识别该尾（见 design 报告 §3.2）。
- **误伤的 18 笔盈利 = 全部是 CONTROLX BUY**（Agent B 的 Ep3 06-30 DA 崩盘日 BUY +1,000~1,305，以及 06-23 两笔 BUY）——方向门是"一刀切"：CONTROLX BUY 全部拦，无论那一天是赚是亏。07-09/07-10 的 SELL 盈利（+2,216 等）**未被误伤**（gate 不拦 CONTROLX SELL）。

---

## 1. Top Loss 反事实主表

| 范围 | 事件数 | 被拦 | 避免亏损（$） | 拦截率 |
|---|---|---|---|---|
| Top 20 | 20 | 9 | 11,891 | 45% |
| Top 50 | 50 | **36** | **35,544** | **72%** |

按 Agent A 的 Type 拆分：

| Type | 事件数 | 被拦 | 避免亏损 |
|---|---|---|---|
| A · PRE-TRADE DETECTABLE | 44 | 30 | 30,151 |
| C · RESIDUAL_TAIL_RISK | 6 | **6** | 5,393 |
| B | 0 | 0 | 0 |

> **Type C（残余尾部）6 笔全部被拦**——因为它们恰好全是 CONTROLX BUY，被 R7 方向门无条件拦截。这印证 design 报告的判断：方向门比"具体危险信号"更通用（Agent A 的 Type C 是"无具体信号"，但方向本身就是危险）。

### 1.1 逐笔明细（Top20）

| # | date | H | action | worst | worst_pnl | Type | gate | reason_code |
|---|---|---|---|---|---|---|---|---|
| 1 | 07-17 | 3 | BUY | interpretable | −2,251 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE |
| 2 | 07-09 | 2 | BUY | interpretable | −2,216 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |
| 3 | 07-07 | 18 | BUY | interpretable | −1,196 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |
| 4 | 07-07 | 20 | BUY | interpretable | −1,181 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |
| 5 | 06-30 | 14 | SELL | rule | −1,175 | A | PASS | —（CONTROLX SELL，不拦） |
| 6 | 06-30 | 12 | SELL | rule | −1,166 | A | PASS | — |
| 7 | 06-30 | 7 | SELL | rule | −1,163 | A | PASS | — |
| 8 | 06-30 | 9 | SELL | rule | −1,148 | A | PASS | — |
| 9 | 06-30 | 5 | SELL | rule | −1,143 | A | PASS | — |
| 10 | 06-30 | 6 | SELL | rule | −1,106 | A | PASS | — |
| 11 | 06-23 | 4 | SELL | rule | −1,076 | A | PASS | — |
| 12 | 07-08 | 21 | BUY | interpretable | −1,076 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |
| 13 | 06-30 | 13 | SELL | rule | −1,061 | A | PASS | — |
| 14 | 06-30 | 20 | SELL | rule | −1,057 | A | PASS | — |
| 15 | 06-26 | 5 | BUY | interpretable | −1,039 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |
| 16 | 06-30 | 10 | SELL | rule | −1,016 | A | PASS | — |
| 17 | 06-30 | 4 | SELL | rule | −1,005 | A | PASS | — |
| 18 | 06-15 | 8 | BUY | interpretable | −1,002 | C | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |
| 19 | 07-07 | 21 | BUY | interpretable | −977 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |
| 20 | 06-20 | 9 | BUY | interpretable | −952 | A | REJECT | BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT |

> 全 50 行见 `code/data/stage3/risk_gate_counterfactual_loss.csv`。reason_code 说明：`BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT` 表示 R7 方向门为主因、R2 模型冲突同时触发（并集）；单码 `BUY_ON_POSITIVE_DRIFT_NODE` 出现在 rule 不参与该笔（如 interpretable 单侧 BUY）时。

### 1.2 未被拦截的 14 笔（gate 的诚实缺口）

全部是 **CONTROLX SELL（Rule 的 DA 暴跌日）**：2026-06-30（H4~H20 共 12 笔，DA 崩至 −1039~−1325，Return −851~−1175）、2026-06-23（H3/H4 两笔）。这些行决策时 spread_lag1 多为历史极值高位（lag1_pct 0.84~0.99），**看似"extreme_state 可识别"**——但 design 报告 §3.2 已证明：lag1_pct>0.95 信号在 train+val 是负 EV、test 是正 EV（regime 翻转），若按此拦截会砍掉 test 上 Rule 的 +45,509 利润。**这是本版 gate 的边界：为保住顺漂移 SELL 的右尾收益，放弃拦截其左尾崩塌。**

---

## 2. Top Profit 反事实（误伤检查 / false rejection）

| 范围 | 事件数 | 被误伤 | 错过的盈利（$） | 误伤率 |
|---|---|---|---|---|
| Top 20 | 20 | 16 | 17,665 | 80% |
| Top 50 | 50 | **18** | **19,410** | 36% |

### 2.1 误伤构成（全部为 CONTROLX BUY 的一刀切代价）

Agent B 的合并 Top50 盈利中：
- **16/20 Top 盈利**被拦——它们全是 CONTROLX BUY（Ep3 06-30 的 DA 崩盘日 BUY +1,000~1,305，interpretable 胜出）。
- 误伤的 18/50 = 18 笔 CONTROLX BUY（Top50 盈利的 36%），合计 19,410。

> 这些是"CONTROLX BUY 偶尔押对方向"的彩票式右尾收益（Agent B：盈利=CONTROLX 极端事件彩票）。方向门把它们与亏损一起拦下——**这正是 Risk Gate 的取舍：不要彩票，要确定性**。但必须如实记录：gate 让这批"本会赚钱的 CONTROLX BUY"变成 NO_TRADE。07-09/07-10 的 SELL 型盈利（Ep4，+3,019）未被误伤。

---

## 3. Avoided Loss vs Missed Profit（核心对比）

| 口径 | 金额（$ / MWh） |
|---|---|
| Top50 避免亏损 | **+35,544** |
| Top50 错过盈利 | **−19,410** |
| **净** | **+16,134** |

**解释与诚实边界：**
- **避免的亏损是"确定避免了"**（这些交易被拦 → 不再发生 → 不再亏损）；**错过的盈利是"本可能赚"**（同一批 CONTROLX BUY 事件，若未拦，其在历史上的实际 PnL 是 +19,410）。两者口径都是"如果 gate 当时已启用"的反事实，但一个是把负变 0、一个是把正变 0。
- **但 Top50 亏损与 Top50 盈利不是同一批交易的全集**。更接近真相的口径是全量流水：test 上所有 CONTROLX BUY（Interpretable 1,578 + CatBoost 1,323 + 部分重叠 ≈ 2,900 行）累计 **−254,947**（Agent C：merged BUY cum −254,947），而其中少数押对的事件赚 +19,410。**方向门在全部 CONTROLX BUY 上避免了约 −255k 的累计亏损，代价是放弃了 +19k 的彩票收益——净 ≈ +236k（全量口径）**。
- Top50 反事实（+16,134）只是"极端事件子集"的净效果，**全量流水的净效果大得多**，因为 Top50 之外还有大量小幅 CONTROLX BUY 亏损被一同拦下。

### 3.1 反向检查：gate 误伤的最深盈利

| # | date | H | action | pnl | gate |
|---|---|---|---|---|---|
| Top-profit #2 | 06-30 | 19 | BUY | +1,305 | REJECT |
| Top-profit #3 | 06-30 | 14 | BUY | +1,175 | REJECT |
| Top-profit #4 | 06-30 | 12 | BUY | +1,166 | REJECT |
| … | 06-30 全 BUY 时段 | | | ≈ +17,483 合计 | 全部 REJECT |

> 单日 06-30 是 Agent B 的 Ep3（最大盈利日，BUY +17,483），恰是 Risk Gate 拦截最密集的一天——**因为该日 DA 崩盘到 −1325，模型在 CONTROLX 上大量 BUY，而 BUY 是 R7 无条件拦截方向**。gate 让这一天"完全不交易"，同时避免了 06-30 Rule SELL 侧的 −14,965（见 Agent A）与 interpretable BUY 侧的同批亏损。

---

## 4. 逐笔 Type 与拦截的一致性检验

| Agent A Type | 定义 | Top50 数 | 被拦 | 一致解读 |
|---|---|---|---|---|
| A（pre-trade detectable） | 冲突 / extreme_state / low_sample / broken_buy | 44 | 30 | 30 笔方向门命中；14 笔漏网全是 CONTROLX SELL（gate 设计边界） |
| C（residual tail） | 无具体事前信号 | 6 | 6 | 恰好全为 CONTROLX BUY，被方向门兜住 |
| B（需外部信息） | — | 0 | 0 | — |

> 一致性说明：Agent A 用 4 类"具体信号"（冲突/extreme_state/low_sample/broken_buy）判定可识别性，得 44/50 A 类；Risk Gate 用"方向 × 节点"这一更粗的信号，拦下 36/50（其中含全部 6 笔 C 类）。**方向门比 A 的具体信号更广（多拦 6 笔 C 类），但漏掉 A 类中 14 笔 CONTROLX SELL（gate 边界）**。两者正交互补。

---

## 5. 结论与诚实声明

1. **Risk Gate 在极端事件层面是净改善**：Top50 亏损拦 36/50（72%、避免 35,544），Top50 盈利误伤 18/50（36%、错过 19,410），净 +16,134；全量 CONTROLX BUY 口径净 ≈ +236k。
2. **代价是放弃彩票收益**：所有 CONTROLX BUY 被无条件拦截，包括 Agent B 的 18 笔 Top 盈利（+19,410）。这符合任务设定的风控优先级（尾部/回撤优先），但**必须如实告知：gate 会让 06-30 这类"押对方向的极端日"颗粒无收**。
3. **14 笔 Rule CONTROLX SELL 亏损无法被本版 gate 拦截**：train+val 证明事前内部信号（cvar99、lag1_pct 极值）无法可靠识别该尾；若强行拦截会误伤 test 上 Rule 的 +45,509 顺漂移利润。这是本版 Risk Gate 的明确边界。
4. **Type C 被全部拦截的再确认**：这 6 笔（06-14/15 第一波尾）是"无具体信号"的残余尾，但都是 CONTROLX BUY——方向门证明"在 CONTROLX 上，BUY 本身就是危险信号"。

---

## 6. 交付物

- 本报告：`docs/stage3/risk_gate_counterfactual.md`
- 逐笔反事实（loss 50 行）：`code/data/stage3/risk_gate_counterfactual_loss.csv`
- 逐笔反事实（profit 50 行）：`code/data/stage3/risk_gate_counterfactual_profit.csv`
- 汇总 JSON：`code/data/stage3/risk_gate_counterfactual.json`
