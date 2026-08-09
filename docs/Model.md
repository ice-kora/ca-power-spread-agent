# 模型架构（V0.1 Baseline）—— 三层模型

> 实现：`code/model_c.py` ｜ 指标与口径：`code/data/model_notes.json` ｜ 契约：`docs/business_contract.md` §6/§7
> 本文为 V0.1 Baseline 架构评审用；三层模型只消费 canonical 的 38 个决策时点可见特征。

## 1. 训练口径（三层共享）

- **Shared Model + Node Feature**：不按节点单独建模，node/zone 作特征；ELCA 冷启动节点共同进 train，但**单独评估**。
- **严格时间切分**：train / val / test 按 decision_date 划分，无随机 split（train 15,311 / val 7,244 / test 4,678）。
- **预测目标**（契约 §7）：任务 A Return 方向分类 `pred_direction ∈ {+1 SELL, −1 BUY, 0 观望}`；任务 B 幅度回归 `expected_return`；输出 `confidence ∈ [0,1]` 与 `prob_return_positive`。
- **弱信号阈值** th = 2.0 $/MWh（虚拟报价交易成本代理）；分类概率 margin = 0.05（观望带 0.45–0.55）。

## 2. Model 0 · Rule Baseline（完全白盒）

估计型规则 R1–R6（加权平均产出 expected_return，权重=贡献）：
- `R1_mean7` `spread_mean7`（同节点同 hour 近 7 日价差均值）w=0.30
- `R2_lag1` `spread_lag1`（昨日 T−2 价差）w=0.24
- `R3_mean14` `spread_mean14` w=0.15 ｜ `R4_daymean` `spread_day_mean_lag1` w=0.10 ｜ `R5_mean30` `spread_mean30` w=0.11
- `R6_bias`（train 上 node×hour×dow×month 分层历史 bias，缺失回退更粗层）w=0.10

证据型规则 R7–R9（只给方向证据 ±1/0，不产幅度）：
- `R7_load` 负荷预测偏差 `sign(load_2da_forecast − load_actual_day_mean_lag1)`，|dev| > 2%·实际负荷才激活
- `R8_peer` 关联节点 spillover `sign(peer_spread_lag1)`（ELCA 无 peer → 0）
- `R9_momentum` 价差动量 `sign(spread_lag1 − spread_mean7)`，|diff| > th 才激活

决策：|expected| ≥ th 且主规则同号权重过半（D_main ≥ 0.5）→ 方向 = sign(est)；≥2 个非零证据且过半数相反 → 观望。

## 3. Model 1 · Interpretable Baseline

- 方向：**LogisticRegression**（C ∈ {0.1..5.0} 由 val AUC 选择）。
- 幅度：**HuberRegressor(epsilon=1.35)**（鲁棒线性回归，抗 Return 重尾；契约允许 Linear/Quantile，重尾下 OLS 被极端值主导）。
- 预处理：`SimpleImputer(median)` + `StandardScaler` **仅 fit 在 train**；zone 与 node 完全共线剔除，node 做 3 哑变量（保证 val 无 ELCA 时列一致）。
- 分类系数 top（标准缩放空间）：`da_day_mean_lag1 +0.273`、`node_CONTROLX −0.273`、`spread_day_max_lag1 −0.262`、`node_ELCA +0.238`、`load_2da_forecast −0.217`。
- 回归系数 top：`spread_std30 −8.13`、`spread_day_std_lag1 +6.50`、`da_day_mean_lag1 +6.35`、`rtpd_day_mean_lag1 +5.71`。

## 4. Model 2 · ML Challenger（仅对照，非最终 Agent）

- 方向：**CatBoostClassifier**（Logloss，AUC 在 val 上 early-stop，1200 iters 上限）。
- 幅度：**LightGBMRegressor(objective=quantile, alpha=0.5)**（中位数目标，抗重尾），val early-stop。
- 类别特征：`node/zone/dow/month`；缺失由树模型原生处理。
- 特征重要性 top（分类）：`node 25.8`、`da_lag2 10.3`、`spread_day_mean_lag1 4.6`、`month 4.4`、`solar_flag 4.3`。

## 5. test 指标对比（n=4,678，decision_date 2026-06-01..08-04）

| 模型 | coverage | dir_acc(traded) | SELL prec | BUY prec | SELL均PnL($/MWh) | BUY均PnL($/MWh) | AUC |
|---|---|---|---|---|---|---|---|
| Rule | 21.3% | 61.5% | 61.8% | 16.7% | +80.0 | −6.5 | 0.572 |
| Interpretable | 67.0% | 64.8% | 63.4% | 66.2% | −6.4 | −78.5 | 0.638 |
| CatBoost | 59.4% | 63.5% | 65.8% | 60.9% | −5.2 | −98.7 | 0.639 |

> SELL 均 PnL = traded SELL 行的 actual_return 均值；BUY 均 PnL = −actual_return 均值（1 MWh 归一化）。**未经风险调整，受重尾极端值主导**（test 最大 |Return| 2,251 $/MWh）。

## 6. 可解释性证据

- 特征重要性首位为 `node`（25.8），其次 `da_lag2`、`spread_day_mean_lag1` —— 价差水平与节点差异是方向信号的主载体；日级统计在回归侧权重高。
- 系数 / 重要性结构与经济直觉一致：负荷、日照窗口、节点 bias 进入 top；`da_lag2` 在分类与回归双侧均靠前。

## 7. 诚实结论（供架构评审）

- **方向信号真实但弱**：AUC 0.57–0.64，优于随机但远非强预测；CatBoost 相对 Logistic 提升有限（0.638 vs 0.639）。
- **准确率 ≠ 盈利**：Rule 的 SELL 均 PnL +80 由 CONTROLX 极端值拉动（该节点 MAE 216）；Interpretable / CatBoost 高覆盖、方向较准但 BUY 头寸大额亏损（CONTROLX test 几乎全 BUY、n_sell=0，BUY 均 PnL −78 / −99）。
- **ELCA 冷启动基本不可预测**：test AUC ≈ 0.49–0.51（Rule 0.506 / Interp 0.505 / CatBoost 0.490），单独评估、不做正式模型。
- 下一阶段重点：风险过滤与仓位（Agent D）、load_2da 发布时刻确认、重尾无关的 PnL 结算口径、消除 CONTROLX 单边退化。
