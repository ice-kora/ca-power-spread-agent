# 业务契约（第二阶段 · 团队统一口径）

> 任何模块不得自行修改以下业务定义。这是并行开发的单一事实来源。

## 1. 场景
本项目按 **CAISO Convergence / Virtual Bidding**（虚拟报价套利）处理。

## 2. 决策时点与数据可见性（铁律）

交易员在 **Day-Ahead Market bid cutoff 前**（D-1 日 10:00，DA bid 截止（官方 BPM）前）提交 D+1 的虚拟报价。

在这个决策时点：
- ❌ D+1 实际 DA clearing price：**尚未产生，不能作输入**
- ❌ D+1 RTPD：**尚未产生，不能作输入**
- ❌ D+1 Return = DA − RTPD：**尚未产生，只能作事后 label**
- ✅ 可用：决策时点之前真实可获得的信息（历史价格、负荷预测、天气预报、节点、时间、历史统计）

**禁止采用**"已知 D+1 DA → 预测 RTPD"作为真实交易模型（那会用到未来 DA）。

## 3. ML 任务（重新定义）

在 Day-Ahead bid cutoff 前，利用当时真实可见信息，预测 **D+1 每个 node × hour** 的：
1. **Return = DA − RTPD 的方向**（分类）
2. **Return 的预期幅度**（回归）
3. **预测置信度**

服务决策：**SELL_DA / Virtual Supply**、**BUY_DA / Virtual Demand**、**NO_TRADE**。

实际 DA / RTPD / Return **只能在训练时作 label / 回测结算**，不能作目标日输入特征。

## 4. 特征可用性规则

- 任何特征若 `available_at > decision_cutoff` → **禁止进入训练/推理**。
- 任何 `UNKNOWN` 特征，在未确认前 **默认禁用**（Conservative Mode）。
- 建立 `docs/feature_availability_matrix.md`，逐特征标注可用性。

## 5. 交易动作（三态恢复）

| 判断 | 动作 | 含义 |
|---|---|---|
| DA − RTPD 明显 > 0 | **SELL_DA** | Virtual Supply：DA 卖出、RT 反向买回 |
| DA − RTPD 明显 < 0 | **BUY_DA** | Virtual Demand：DA 买入、RT 反向卖回 |
| spread 太小 / 置信太低 / 证据冲突 / 风险高 | **NO_TRADE** | 观望 |

每个 DA position 必须有对应 RT liquidation。**当前按 1 MWh normalized PnL 计算**，仓位优化以后再做。

## 6. 模型架构（三层）

1. **Model 0 · Rule Baseline**（完全白盒）：同节点同 hour 历史 spread、7/14/30d spread、波动率、load forecast deviation、weather forecast deviation、hour/weekday/season、node 历史 bias。输出 pred_direction / expected_spread / confidence / evidence。
2. **Model 1 · Interpretable Baseline**：Logistic Regression / Linear or Quantile Regression / Shallow Decision Tree。要求可解释。
3. **Model 2 · ML Challenger**：CatBoost / LightGBM。**只作效果对照，不是最终 Agent。**

**默认不采用"一节点一模型"**：Shared Model + Node Feature（node/zone 作特征）。ELCA（窗口明显不同）作为 **cold-start / short-history node 单独评估**，不强行混入、不另训正式模型。暂不做 LSTM / Transformer、不做"未来多天预测"。horizon **固定 D+1 交易日**，历史 7/14/30 天用 lag/rolling 编码。

## 7. 预测目标（不只预测 RTPD）

核心目标 = **Return = DA − RTPD**：
- 任务 A：direction classification（Return > +threshold / < −threshold / 中间弱信号）
- 任务 B：spread magnitude regression（expected_return）
- Decision Policy 使用 `pred_direction + expected_return + confidence + risk filters`，**不只凭分类概率**。

## 8. 严格回测（旧数字全部作废）

Walk-forward / expanding window，指标至少含：direction accuracy、BUY precision、SELL precision、trade coverage、win rate、avg PnL/trade、cumulative PnL、max drawdown、Sharpe-like、node/hour/month performance。比较 All Trade / Rule / Interpretable / CatBoost / Decision Policy。**不以 accuracy 最大为优**，看风险调整后 PnL 是否优于 baseline。

## 9. 本轮禁止

LLM Agent、联网搜索、新闻 Agent、LangGraph、多 Agent 产品逻辑、炫酷 UI、LSTM、Transformer、未来 7 天 RTPD 预测、自动真实下单、仓位优化。

## 10. 交付验收

业务时间线（修复后）、feature_availability_matrix.md、leakage_report.md、canonical dataset schema、修复前后数据量变化、删除特征及原因、三层模型各自结果、严格回测结果、BUY/SELL/NO_TRADE 分别表现、node/hour performance、5~10 条真实历史 decision snapshot、仍 UNKNOWN 字段、下一阶段 Agent 化建议。

**最终 Lead 判断**：在不使用任何未来信息条件下，这套策略到底有没有真实预测价值。**不粉饰结果，亏损就写亏损，准确率下降就解释原模型有多少效果来自泄漏。**
