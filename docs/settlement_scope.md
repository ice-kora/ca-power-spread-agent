# Settlement Scope —— 本项目结算口径边界说明（Agent C · Provenance + 版本工程）

> 作者：Agent C（Provenance + 版本工程师）｜ 日期：2026-08-09
> 状态：**信号/策略 MVP，不是完整的 CAISO Settlement Simulator**（见 §0 声明）
> 配套：`code/market_rules.py`（market_rule_version 单一事实来源）、`docs/company_vs_official_reconciliation.md`（对账报告）、`docs/v0.2_backtest.md`（回测口径）、`code/backtest_v2.py`、`agent/case_library/policy.py`（Post-trade Review 记录）

---

## 0. 顶层声明（务必先读）

> **THIS IS A SIGNAL/STRATEGY MVP, NOT A FULL CAISO SETTLEMENT SIMULATOR.**
> 本项目评估的是"决策信号 / 策略本身的价值"，**不是**把每一笔虚拟报价（Virtual Bid）按
> CAISO 官方结算规则跑出真实资金流。所有 PnL 数字都是"假设每笔 1 MWh 执行、按价格差
> 线性结算"的**信号级近似**，绝不代表真实账户盈亏。

---

## 1. 已确认的口径（可直接用于严格回测/对账）

### 1.1 DA（Day-Ahead）LMP

| 项 | 结论 | 依据 |
|---|---|---|
| 公司 `da_price` == 官方 OASIS `PRC_LMP`（DA LMP 总价） | 逐小时**完全相等**（504/504，max_diff ≤ $0.0001） | `docs/company_vs_official_reconciliation.md` §3.1 |
| LMP 构成 | `LMP = MCE + MCC + MCL + MGHG`（504/504，max_diff=2e-5）→ 公司 `da_price` 是**含 GHG 的全口径 LMP 总价** | 同上 |
| 发布时间 | D-1 日 13:00 PT（DAM 出清结果发布，**晚于 bid cutoff 10:00 PT**） | `docs/market_timeline.md` §1 第 4 行 |
| 本项目角色 | **仅作 label / 结算基准**；决策时点不可见，绝不作特征输入 | `docs/business_contract.md` §2 |

### 1.2 RTPD（Real-Time Pre-Dispatch）—— 本项目 RT 口径

| 项 | 结论 | 依据 |
|---|---|---|
| 公司小时 `rtpd_price` == 官方 `PRC_RTPD_LMP`（FMM 15-min）按小时 4 区间**算术均值** | 逐小时完全相等（504/504，max_diff ≤ $0.0001） | 对账报告 §3.2 |
| 结论 | **本项目 RT 口径 = RTPD/FMM（15-min 聚合），不是 RT 5-min（`PRC_INTVL_LMP`）**；无加权 / 取首 / 取末等其它聚合 | 同上 |
| 发布时间 | 每个 15-min 区间开始前 ≤22.5 min 发布；整日完整于 T 日深夜 | `docs/market_timeline.md` §1 第 8 行 |
| 事后修正 | OASIS 无 RTPD 版本历史，终值可能被价格修正改写；严格 as-of 需结合 Price Correction 报告（当前回测接受"终值≈发布值"） | 对账报告 §7.3 |

### 1.3 阻塞分量（`-c` 文件悬案已解开）

| 项 | 结论 |
|---|---|
| 原始 `价格数据/*-c.xlsx` 的 DA/RTPD 列 | == 官方 **MCC 阻塞分量**（`LMP_CONG_PRC`），完全相等（168/168，max_diff ≤ $0.0001） |
| 佐证 | `-c` 文件第三行市场名为 `DARTPD Cong Spread`（主文件为 `DARTPD Return`） |

### 1.4 价差（Return）定义（契约冻结）

- `Return = DA − RTPD`（`$/MWh`，每 node × hour）。
- 两个分量均与官方完全一致 ⇒ **价差标签本身可直接严格回测**。
- 本项目 `canonical.py` 的 `actual_return = actual_da − actual_rtpd`（由真实 DA/RTPD 重算，非文件第三行原值）。

### 1.5 市场规则版本（本次新增）

- 当前全部数据采集 / 对账 / 回测 / 决策按 **`PRE_DAME_EDAM_2026`**（legacy DAM + RTPD/FMM 口径）执行。
- 版本标记写入：Decision Context（`code/decision/rule_engine.py`）、Backtest Record（`code/backtest_v2.py` summary）、Decision Card（`agent/explanation/decision_card.py`）、Post-trade Review / Case（`agent/case_library/policy.py` + `case.py`）。
- 常量单一事实来源：`code/market_rules.py`。

---

## 2. 当前简化的内容（信号级近似）

| 环节 | 本项目做法 | 真实 CAISO Settlement |
|---|---|---|
| 执行规模 | **每笔按 1 MWh normalized** | 真实虚拟报价有 MW 数量级与竞价单位 |
| PnL 计算 | `SELL_DA → +actual_return`；`BUY_DA → −actual_return`；`NO_TRADE → 0` | 按 award 量 × 出清价差 × 结算权重（$ / MWh）逐小时结算 |
| 仓位优化 | 无（每笔独立，无组合/风险预算） | 交易员做组合层仓位与风险控制 |
| 交易成本 | 无 bid/ask 差价、无佣金、无市场冲击 | 真实执行有成本与滑点 |
| 时间价值 | 忽略资金占用 / 保证金 / 逐日盯市 | CAISO 结算有结算周期与现金流时点 |
| 决策模型 | 三态建议 BUY_DA / SELL_DA / NO_TRADE | 真实还需报价价格（bid price）与是否成交（award） |

### 2.1 这为什么合理（MVP 边界）

- 本项目目标（`docs/business_contract.md`）：评估"在不使用未来信息条件下，决策信号是否真有预测价值"。
- **方向 / 幅度预测的排序价值**在 1 MWh 归一化下即可检验；乘上真实仓位只会放大或缩小同一信号，不改变"信号本身有没有用"。
- 所有回测报告均明确标注"Signal / Strategy Backtest，NOT real Convergence Bidding PnL"（见 `docs/v0.2_backtest.md` §0、`docs/backtest_v2_backtest.md`、`code/backtest_v2.py` meta）。

---

## 3. 未实现的生产级 Settlement 细节（明确 NOT 覆盖）

以下内容**本轮明确不实现**，任何解读不得把本项目的 PnL 数字当作真实结算：

1. **Award / 成交**：虚拟报价不会自动成交。真实世界需经 IFM（Integrated Forward Market）出清，成交与否、成交多少（MW）取决于报价曲线与市场出清价。本项目隐含"报价即成交、1 MWh 全成交"。
2. **Bid price / quantity**：本项目只有方向（BUY/SELL）+ 期望价差，没有报什么价、报多少量。CAISO 虚拟报价是一组 (price, quantity) 段。
3. **Fees / 费用**：未包含任何 CAISO 费用（grid management charge、bid fees、scheduling coordinator fees、taxes 等）。
4. **Settlement Charge Codes**：CAISO 结算使用大量 charge codes（CC 6000+ 系列：如 CC 6100 等），按计量与市场规则逐项计费。本项目完全未建模。
5. **Position 与 RT liquidation**：`business_contract` §5 要求"每个 DA position 必须有对应 RT liquidation"——本项目按 `BUY→RT 反向卖回 / SELL→RT 反向买回` 的 1:1 假设线性处理，未建模实际 RT 报价/成交过程。
6. **DAME / EDAM（Extended Day-Ahead Market）结算**：DAME（Day-Ahead Market Enhancements）/ EDAM 上线后，日前/实时市场结构、结算规则、可能的新 charge codes 均会变化。本项目只通过 `market_rule_version` **标记**版本（`POST_DAME_EDAM_2026`），**不做任何 DAME/EDAM 适配**。

---

## 4. 对 PnL 数字的解读约束

1. **横向可比**：各策略（Rule / Model / Model+Gate / Full Pipeline）在同一 1 MWh 归一化口径下横向可比，比较的是**相对优劣**。
2. **纵向不可外推为真实盈亏**：任何 `cum_pnl`、`max_drawdown`、`cvar` 数字是"每 1 MWh × 交易次数"的信号级绝对额，**不能**乘上仓位当真实资金回报。
3. **正漂移 regime 警告**：`docs/v0.2_backtest.md` 已注明本窗口 DA>RTPD 强正漂移，静态全 SELL 即赚；盈利主要来自市场 regime 而非模型 alpha。
4. **尾部低估风险**：信号级线性 PnL 未考虑真实结算中的保证金 / 现金流 / 费用放大，真实交易的极端尾部通常比信号回测更差。

---

## 5. 何时需要升级为完整 Settlement 引擎（后续里程碑）

若进入真实交易，需在以下顺序逐步补齐（本轮均不开发）：

1. **报价层**：bid price / quantity 生成（从 expected_return + confidence 到报价段）；
2. **成交层**：对接 IFM 出清模拟（或真实 OASIS award 数据）判断成交与量；
3. **结算层**：按 CAISO BPM（Settlement Guide / charge codes）逐小时计算结算金额，含 fees；
4. **风险层**：仓位优化、资金约束、保证金、组合尾部控制；
5. **DAME/EDAM 适配**：`market_rule_version=POST_DAME_EDAM_2026` 落地后重做 1–4。

升级前，本项目的定位始终是：**验证决策信号价值的信号级回测框架**。

---

## 6. 相关文件索引

| 文件 | 角色 |
|---|---|
| `code/market_rules.py` | market_rule_version 常量单一事实来源 |
| `code/data_acquisition/schemas.py` | 输入侧 AsOfRecord / FeatureSnapshot provenance（source_type / is_mock / raw_source_id / eligibility 三拆 / market_rule_version） |
| `agent/evidence/schema.py` | Evidence provenance（同字段集） |
| `code/backtest_v2.py` | Signal Backtest（meta 含 market_rule_version + boundary 声明） |
| `code/decision/rule_engine.py` | Decision Context（Decision.market_rule_version） |
| `agent/explanation/decision_card.py` | Decision Card（market_rule_version 字段 + 渲染） |
| `agent/case_library/policy.py` / `case.py` | Post-trade Review / Case（DecisionRecord / Case 带 market_rule_version） |
| `docs/company_vs_official_reconciliation.md` | 对账口径（DA/RTPD/FMM/MCC 一致性证据） |
| `docs/market_timeline.md` | 官方时间线（cutoff 10:00 / DA 13:00 发布 / RT 时间线） |
| `docs/v0.2_backtest.md` | 回测结果与边界声明 |
