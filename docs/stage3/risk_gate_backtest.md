# CA-ISO 价差交易 · Risk Gate 回测比较（Agent D）

> 生成时间：2026-08-09
> 评估窗口：test 2026-06-02 ~ 2026-08-05（Agent C 正式预测，4,678 行/模型）
> 时间顺序：模型由 train 训练 → val 用于规则/阈值校准 → **test 只做最终验证**
> 口径：SELL 收益 = `actual_return`；BUY 收益 = `−actual_return`；NO_TRADE = 0（1 MWh/仓，$ / MWh）
> 回测引擎：`code/tmp/agent_d_backtest.py`（严格 as-of：gate 只用 ≤ target_date-2 的风险特征）

---

## 0. 结论先行（诚实）

**Risk Gate 实质性降低了尾部风险与最大回撤，但没有创造 alpha，且对已经安全的 Rule 无增益。**

| 策略 | 交易数 | coverage | cum PnL | max drawdown | worst trade | CVaR(1%) | Sharpe(日) | profit factor |
|---|---|---|---|---|---|---|---|---|
| **Model Committee** | 1,523 | 48.8% | **−130,042** | **−147,957** | −2,216 | −1,056 | −4.87 | 0.39 |
| **Committee + Risk Gate** | 339 | 10.9% | **+962** | **−654** | −113 | −74 | +2.82 | 1.83 |
| 原 Rule（对照） | 824 | 26.4% | +79,485 | −15,691 | −1,175 | −1,122 | +4.21 | 3.29 |
| 原 Rule + Risk Gate | 824 | 26.4% | +79,485 | −15,691 | −1,175 | −1,122 | +4.21 | 3.29 |

> committee 的 −130k 几乎全部来自其 1,184 笔 CONTROLX BUY（ML 双模型一致看 BUY，逆 +84 漂移，被 −2,216 尾部打穿）。Risk Gate 把这 1,184 笔全部拦截后，committee 只剩 339 笔 SNLNDRO 交易（+962）。**gate 的作用 = 移除系统性负期望的 CONTROLX BUY，不是发现新机会。**

---

## 1. 主策略（ZP26 = SNLNDRO + CONTROLX）指标矩阵

> coverage = 交易数 / 该窗口样本行数（ZP26 共 3,119 行）；All Trade[x] 为该模型 `pred_direction != 0` 的全部交易。

| 策略 | n_traded | coverage | dir_acc | win_rate | mean PnL | median PnL | cum PnL | max DD | worst | downside_dev | CVaR(5%) | CVaR(1%) | PF | Sharpe |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| All Trade[rule] | 824 | 0.264 | 0.604 | 0.604 | +96.46 | +3.03 | +79,485 | −15,691 | −1,175 | 3,083 | −552 | −1,122 | 3.29 | +4.21 |
| All Trade[interpretable] | 1,876 | 0.602 | 0.662 | 0.662 | −66.14 | +14.37 | −124,072 | −147,833 | −2,251 | 10,148 | −872 | −1,103 | 0.45 | −4.61 |
| All Trade[catboost] | 1,683 | 0.540 | 0.629 | 0.629 | −76.94 | +8.57 | −129,484 | −147,633 | −2,216 | 10,829 | −862 | −1,045 | 0.40 | −4.85 |
| **原 Rule** | 824 | 0.264 | 0.604 | 0.604 | +96.46 | +3.03 | +79,485 | −15,691 | −1,175 | 3,083 | −552 | −1,122 | 3.29 | +4.21 |
| **原 Rule + Risk Gate** | 824 | 0.264 | 0.604 | 0.604 | +96.46 | +3.03 | +79,485 | −15,691 | −1,175 | 3,083 | −552 | −1,122 | 3.29 | +4.21 |
| Interpretable | 1,876 | 0.602 | 0.662 | 0.662 | −66.14 | +14.37 | −124,072 | −147,833 | −2,251 | 10,148 | −872 | −1,103 | 0.45 | −4.61 |
| **Interpretable + Risk Gate** | 400 | 0.128 | 0.665 | 0.665 | +1.54 | +3.69 | +615 | −1,009 | −171 | 185 | −56 | −143 | 1.35 | +1.45 |
| CatBoost | 1,683 | 0.540 | 0.629 | 0.629 | −76.94 | +8.57 | −129,484 | −147,633 | −2,216 | 10,829 | −862 | −1,045 | 0.40 | −4.85 |
| **CatBoost + Risk Gate** | 470 | 0.151 | 0.660 | 0.660 | +2.87 | +3.17 | +1,350 | −627 | −113 | 122 | −32 | −68 | 1.96 | +3.84 |
| **Model Committee** | 1,523 | 0.488 | 0.628 | 0.628 | −85.39 | +10.40 | −130,042 | −147,957 | −2,216 | 10,841 | −870 | −1,056 | 0.39 | −4.87 |
| **Committee + Risk Gate** | 339 | 0.109 | 0.667 | 0.667 | +2.84 | +3.42 | +962 | −654 | −113 | 146 | −37 | −74 | 1.83 | +2.82 |

### 1.1 逐策略解读（诚实）

- **All Trade[rule] / 原 Rule**：唯一显著正 PnL 策略（+79,485）。本质是"CONTROLX 正漂移上几乎全 SELL + 抓右尾"。Risk Gate 对它 **0 改变**——Rule 本来就不做 CONTROLX BUY，gate 没有可拦的。
- **Interpretable / CatBoost**：−124k / −129k，全部来自 CONTROLX BUY。**gate 后 +615 / +1,350**，max DD 从 −148k 收到 −1k，worst 从 −2,251 收到 −171——**尾部被实质消除**。代价是 coverage 从 60%/54% 收到 13%/15%，PnL 从"巨额亏损"变为"微正"。
- **Model Committee**：2/3 多数一致，但 77.7% 的交易是 CONTROLX 双 ML BUY → −130k。
- **Committee + Risk Gate**：+962，max DD −654，worst −113。**PnL 转正、回撤消除**，但覆盖率只剩 10.9%，且全部交易在 SNLNDRO。

### 1.2 gate 实际拦截了什么（ZP26）

```
committee 交易：1,523
  ├─ REJECT：1,184（reason = BUY_ON_POSITIVE_DRIFT_NODE | MODEL_DISAGREEMENT）
  │     全部为 CONTROLX BUY（ML 双模型一致看 BUY）
  └─ 保留：339（300 SELL + 39 BUY，全在 SNLNDRO）
```

---

## 2. ELCA（cold-start，单独评估，不混入主结论）

| 策略 | n_traded | coverage | cum PnL | max DD | worst |
|---|---|---|---|---|---|
| All Trade[rule] | 172 | 0.110 | −314 | −1,319 | −339 |
| All Trade[interpretable] | 1,257 | 0.806 | −10,178 | −16,167 | −914 |
| All Trade[catboost] | 1,098 | 0.704 | −8,678 | −14,001 | −914 |
| Model Committee | 1,029 | 0.660 | −8,191 | −13,620 | −914 |
| **Committee + Risk Gate** | **0** | 0 | 0 | 0 | 0 |
| 原 Rule + Risk Gate | 0 | 0 | 0 | 0 | 0 |

**解读**：ELCA 的所有模型交易几乎全部是 SELL（interpretable 1,252 SELL / catboost 1,098 SELL / rule 170 SELL）。Risk Gate 以 `SELL_ON_NEGATIVE_DRIFT_NODE`（ELCA 负漂移 −1.15）+ `LOW_SAMPLE_SUPPORT`（ELCA hist_n test 中位 121、max 154，绝大多数 < 150）把 ELCA **全部关闭**（1,560 行中 1,554 REJECT，其余 6 行为 pred_direction=0 的观望行，本就不交易）。这与其说"改善 ELCA"，不如说是**弃用**——Agent C 已建议"ELCA 应押 BUY 或直接弃用（cold-start 数据太短）"，且 ELCA SELL 在 train+val 与 test 均负期望。**诚实声明：gate 对 ELCA 的裁决是"不交易"，不是"交易得更聪明"。**

---

## 3. gate 触发分布

| 场景 | REJECT 数 | reason |
|---|---|---|
| Committee（ZP26） | 1,184 | `BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT` |
| Interpretable（ZP26） | 1,476 | `BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT` |
| CatBoost（ZP26） | 1,213 | `BUY_ON_POSITIVE_DRIFT_NODE\|MODEL_DISAGREEMENT` |
| Committee（ELCA） | 1,554* | `SELL_ON_NEGATIVE_DRIFT_NODE\|LOW_SAMPLE_SUPPORT`（939）/ `LOW_SAMPLE_SUPPORT`（525）/ `SELL_ON_NEGATIVE_DRIFT_NODE`（90） |

\* ELCA gate 在 1,560 样本行上判定（committee 交易 1,029 + 观望行 531 中低样本者），全部 REJECT。

---

## 4. 目标达成评估（按任务优先级）

| 优先级 | 指标 | Committee | Committee+Gate | 判定 |
|---|---|---|---|---|
| ① 减少尾部亏损 | CVaR(1%) | −1,056 | **−74** | ✅ 显著（−93%） |
| ① 减少尾部亏损 | worst trade | −2,216 | **−113** | ✅ 显著（−95%） |
| ② 降低 max drawdown | max DD | −147,957 | **−654** | ✅ 显著（−99.6%） |
| ③ 保留合理 coverage | coverage | 48.8% | 10.9% | ⚠️ 牺牲大，但保留的是唯一正 EV 节点 |
| ④ PnL | cum PnL | −130,042 | **+962** | ✅ 由巨额亏损转正 |

**综合判定**：任务四项优先级中，前两项（尾部、回撤）实现**数量级改善**；PnL 由负转正；coverage 是显式代价。**Risk Gate 达成设计目标。**

---

## 5. 与"静态漂移基准"的对照（避免高估）

- test 窗口静态全 SELL（每时刻持 1 MWh）累计 PnL ≈ **+132,746**（阶段二口径）。Rule（+79,485）、Committee+Gate（+962）均跑不赢静态全 SELL——本窗口盈利很大程度来自市场正漂移本身。
- **Risk Gate 没有把 committee 变成比 Rule 更好的策略**：Rule +79,485 ≫ Committee+Gate +962。gate 的价值是**风控护栏**（把灾难性组合转为可接受的小正），不是收益引擎。

---

## 6. 敏感性 / 稳健性说明

1. **gate 阈值全由 train+val 决定，test 零调参**：R7 基于 CONTROLX/ELCA 漂移符号（train+val 结构事实）；R6 基于 ELCA 样本量（train+val ≈90、test ≈120）；R4/R1/R3/R5 经 train+val 扫描后删除/降级。test 数字是"新鲜"验证。
2. **方向门对漂移的依赖**：若未来 CONTROLX 漂移转负、或 ELCA 数据积累到样本充足，R7a/R7b/R6 需复核（白盒规则，可审计）。
3. **gate 后 coverage 集中在 SNLNDRO**：SNLNDRO 是三节点中唯一 std≈14、无 >300 事件、三模型全正 PnL 的节点（C 报告）——gate 收敛到"只交易真正安全的节点"，符合风控目标。

---

## 7. 交付物

- 本报告：`docs/stage3/risk_gate_backtest.md`
- 指标 JSON：`code/data/stage3/risk_gate_backtest_metrics.json`、`risk_gate_all_results.json`
- 反事实：`code/data/stage3/risk_gate_counterfactual.json` + `_loss.csv` + `_profit.csv`
- 脚本：`code/tmp/agent_d_*.py`
