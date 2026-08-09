# DecisionPipeline · V0.1 Baseline 决策流水线架构

> 状态：已实现（第一版 White-box Risk Gate）｜ 代码：`code/backtest.py`（三态决策策略）、`code/tmp/agent_d_gate.py`（Risk Gate）｜ 关联：`docs/business_contract.md` §5–§8、`docs/stage3/risk_gate_design.md`

## 1. 端到端决策流水线

```
数据层 → 特征层 → 三层模型 → 模型集成(Committee) → Risk Gate → 三态决策 → PnL 结算 → 复盘
```

| 阶段 | 组件 | 说明 |
|---|---|---|
| 数据层 | `canonical.parquet` | 无泄漏层；X 特征全部 as-of，label 区（`actual_da/actual_rtpd/actual_return/direction`）决策时点不可见 |
| 特征层 | `docs/feature_availability_matrix.md` | 只允许 `available_at <= decision_cutoff` 且 CONFIRMED/ASSUMED_AVAILABLE 的特征；`t2m_next`/`ssrd_next`/`wind100_next` 默认禁用 |
| 模型层 | Rule / Interpretable / CatBoost | 输出统一 schema 预测：`pred_direction / expected_return / confidence / prob_return_positive` |
| 集成层 | Model Committee | 2/3 多数一致出方向；仅 3/3 或 Rule 参与的信号可信（双 ML 同向 = 相关误差放大，非独立确认） |
| 风险层 | **Risk Gate** | 不预测方向，只答"是否允许入场"：PASS / REJECT / PASS_WITH_WARNING + reason_code |
| 决策层 | 三态策略 | SELL_DA / BUY_DA / NO_TRADE |
| 结算 | PnL | SELL=`+actual_return`，BUY=`−actual_return`，NO_TRADE=0（1 MWh/仓） |
| 复盘 | backtest_report.md / decision snapshot / 反事实 | 指标矩阵、真实历史 snapshot、gate 反事实（`risk_gate_counterfactual.md`） |

## 2. 输入

| 输入 | 说明 |
|---|---|
| `node` | 目标节点（ZP26: SNLNDRO/CONTROLX；ELCA 为 cold-start 单独评估） |
| `target_date` | 交付日 D+1 |
| `hour` | 目标小时 H1–H24 |
| 决策时点 | **D-1 13:00 前**（DA bid cutoff 前）可见信息：历史价格滞后、负荷预测/实际、天气滞后、日历特征、node 历史统计（滞后约定 lag1→target_date-2，宁保守不泄漏） |

## 3. 第一版 White-box Risk Gate

**职责**：不预测方向、不创造 alpha；只做二值放行判定——"这笔候选交易是否允许入场"。纯 pandas/numpy 白盒，无任何学习/拟合；规则与阈值全部由 **train+val** 校准，test 零调参。

**输出**：`PASS / REJECT / PASS_WITH_WARNING` + `reason_code`。

reason_code 全集（本版实际使用加粗）：`BUY_ON_POSITIVE_DRIFT_NODE`、`SELL_ON_NEGATIVE_DRIFT_NODE`、`MODEL_DISAGREEMENT`、`LOW_SAMPLE_SUPPORT`、`EXTREME_TAIL_NODE`（警告级）。已删除：`LOW_CONFIDENCE`、`HIGH_VOLATILITY`、`EXPECTED_EDGE_TOO_SMALL`。

## 4. 规则清单（3 条有效 + 1 条降级 + 3 条删除）

### 有效规则

| rule_id | 判定 | reason_code | 证据（train+val，阈值未用 test） |
|---|---|---|---|
| **R7a** | CONTROLX 且 BUY → REJECT | `BUY_ON_POSITIVE_DRIFT_NODE` | CONTROLX 无条件漂移 **+9.68**、BUY mean −9.68、maxloss −3,656、cvar99 −916；赔率倒挂（命中 +100 / 做错 −450） |
| **R7b** | ELCA 且 SELL → REJECT | `SELL_ON_NEGATIVE_DRIFT_NODE` | ELCA 漂移 **−1.15**、SELL mean −1.15、maxloss −357 |
| **R6** | hist_n < 150 → REJECT | `LOW_SAMPLE_SUPPORT` | ELCA train hist_n 中位 44、test 中位 121（<150）；主节点 ≥878 |
| **R2** | CONTROLX BUY 且 interpretable_dir<0 且 catboost_dir<0 → 并集 reason | `MODEL_DISAGREEMENT` | 双 ML 同向 BUY = 同一错误二次确认（2,273 笔 cum −138,648）；不独立新增拦截，仅提供原因码（单模型视角下 rule_dir 置 NaN） |

### 降级 / 删除规则（验证无效，证据见 design §3.2–3.5）

| rule_id | 决策 | 原因 |
|---|---|---|
| **R4** Tail Gate | **降级为 PASS_WITH_WARNING**（`EXTREME_TAIL_NODE`） | 历史 cvar99 阈值扫不到最大 SELL 亏损（−2,066 行事前 cvar99 仅 −276）；lag1_pct>0.95 信号 **regime 翻转**（train+val 负 EV −6,487 / test 正 EV +45,509），REJECT 会误砍 test 顺漂移利润 |
| **R1** Confidence Gate | **删除** | confidence 与 PnL **反相关**（conf 桶 mean +2.06 → −19.29）；C 报告 CONFIDENCE NOT CALIBRATED（>0.80 桶 accuracy 反最低 60.3%） |
| **R3** Volatility Gate | **删除** | vol_ratio 分层无单调性（>3.0 段反而 mean +10.49）；高波动是 CONTROLX 常态，非"这笔危险"信号 |
| **R5** Expected Edge | **删除** | \|expected_return\| 阈值不改善 cvar99（稳定 ≈ −880~−930），只打薄 coverage（≥150 时仅 1.9%）；幅度预测秩相关≈0 |

## 5. 三态决策判定条件

| 决策 | 条件（AND） |
|---|---|
| SELL_DA | `pred_direction>0` 且 `expected_return ≥ +5.0` 且 `confidence ≥ 0.20`（且 risk_std7_cap 未超，默认 None=关闭） |
| BUY_DA | `pred_direction<0` 且 `expected_return ≤ −5.0` 且 `confidence ≥ 0.20` |
| NO_TRADE | 其余：\|expected_return\| 太小 / confidence 太低 / Gate REJECT / 证据冲突 |

> 阈值可配置（`DECISION_CFG`: ret_threshold_abs=5.0, conf_threshold=0.20, risk_std7_cap=None）。敏感性显示：启用 risk std7 过滤在本数据上会把 Rule PnL 从 79k 打到 2k（顺漂移尾被误伤），故默认关闭。Gate REJECT 时无论三态策略如何均降级为 NO_TRADE。

## 6. 决策输出示例（decision snapshot，test 真实历史）

| node | target_date | hour | pred_dir | expected_return | prob_pos | conf | decision | actual_return | pnl | evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| CONTROLX_1_N001 | 2026-06-30 | 14 | 1 | +185.88 | 0.80 | 0.77 | SELL_DA | −1175.1 | −1175.1 | exp_ret=+185.9 prob=0.80 conf=0.77 |
| CONTROLX_1_N001 | 2026-07-09 | 2 | 1 | +180.32 | 0.83 | 0.82 | SELL_DA | 2216.3 | 2216.3 | exp_ret=+180.3 prob=0.83 conf=0.82 |
| SNLNDRO_1_N001 | 2026-08-03 | 15 | −1 | −8.68 | 0.65 | 0.64 | BUY_DA | 1.2 | −1.2 | exp_ret=−8.7 prob=0.65 conf=0.64 |
| CONTROLX_1_N001 | 2026-06-02 | 1 | 0 | −14.77 | 0.52 | 0.29 | NO_TRADE | −54.8 | 0.0 | exp_ret=−14.8 prob=0.52 conf=0.29 |

## 7. 已知局限（如实标注）

1. **confidence 未校准**：高置信 = 行情持续的机械产物 + 与尾部同源，既非概率也非风险度量；需 **quantile/EVT 尾部校准**替换（stage3 已列为下一阶段优先级）。
2. **ELCA 被 gate 弃用**：R7b + R6 把 ELCA 全部 REJECT（test 1,554/1,560 行），裁决是"不交易"而非"交易得更聪明"；cold-start 样本积累后需复核。
3. **gate 边界**：14 笔 Rule 的 CONTROLX SELL（DA 崩塌，2026-06-23/06-30）无法用任何事前内部信号可靠识别，本版不拦截。
4. **方向门依赖漂移符号稳定**：CONTROLX 漂移 train+val +9.7 / test +84 均为正，方向门成立；若 regime 翻转需复核 R7a（白盒可审计）。
5. 未做仓位优化与交易成本/冲击（契约约定暂不做）；test 窗口仅 65 天、2026-06 强正漂移，外推受限；天气 `valid_pt` 时区 naive，存在小时对齐不确定性。
