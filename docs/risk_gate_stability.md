# Risk Gate 跨窗口稳定性验证（V0.1 empirical guardrail）（Agent C）

> 作者：Agent C（校准与 Risk Gate 稳定性工程师）｜ 日期：2026-08-09
> 分析脚本：`code/tmp/agent_c_gate_stability.py`
> 只读数据；未调 CatBoost/LightGBM 参数；未联网；test 零调参。

---

## 0. TL;DR（结论先行）

| 规则 | 语义 | 跨窗口分类 | 一句话 |
|---|---|---|---|
| **R7a** CONTROLX BUY REJECT | `BUY_ON_POSITIVE_DRIFT_NODE` | **REGIME_DEPENDENT** | train/val2/test 被拒集负 EV（拒绝有价值），但 **val1（2026-01-02~03-15）被拒集为正 EV（+1,338）**——该子窗口 CONTROLX 漂移转负，BUY 是对的；把 val 整体视为一个窗口会掩盖这一点 |
| **R7b** ELCA SELL REJECT | `SELL_ON_NEGATIVE_DRIFT_NODE` | **STABLE**（可用窗口内） | train / test 被拒集均为负 EV（−2,490 / −12,145）；val 窗口无 ELCA 样本，**不可验证** |
| **R6** 低样本 REJECT | `LOW_SAMPLE_SUPPORT` | **INSUFFICIENT_DATA**（且方向混杂） | 仅 test 有可签署 PnL；test 内被拒集 = ELCA SELL（−14,963，拦对了）+ ELCA BUY（**+731，误伤**） |

**核心诚实结论**：
1. **V0.1 guardrail 不是 test 窗口过拟合**（与 `calibrate.py` 结论一致），但 R7a 是
   **regime 依赖**的——它不是「CONTROLX BUY 永远负 EV」，而是「在 CONTROLX 漂移为正
   的窗口负 EV」。val1 曾出现 CONTROLX 漂移转负（−0.76），BUY 转正。
2. **R7a 是 DATA-DERIVED TEMPORARY GUARDRAIL，这个「临时」标签必须保留**：一旦
   CONTROLX 漂移转负，R7a 会拒绝正 EV 交易。
3. **R6 的价值与方向高度耦合**：它拦掉 ELCA SELL（负 EV）的同时误伤 ELCA BUY（+731）。
   「低样本 = 不可靠」是结构事实，但「低样本交易都是坏交易」不成立。
4. **任何 V0.1 guardrail 都不能直接晋级长期 Production Rule**；晋级前必须绑定
   「漂移方向/样本量」的结构复核条件（已在 config 中留 `threshold_source` 与
   `DATA-DERIVED TEMPORARY GUARDRAIL` 标注）。

---

## 1. 窗口定义与候选口径

### 1.1 窗口

| 窗口 | 定义 | 节点 | 行数 |
|---|---|---|---|
| train | canonical split=='train'（2025-04-03 ~ 2026-06-01） | CONTROLX / SNLNDRO / ELCA | 15,311 |
| val1 | split=='val' 且 target_date ≤ 2026-03-15 | CONTROLX / SNLNDRO | 3,500 |
| val2 | split=='val' 且 target_date > 2026-03-15 | CONTROLX / SNLNDRO | 3,744 |
| test | split=='test'（2026-06-02 ~ 2026-08-05） | 全节点 | 4,678 |

### 1.2 候选口径

- **val1 / val2 / test**：model-driven。候选 = v2 预测行，`direction = BUY(er<0) / SELL(er>0) / FLAT(er==0)`，
  `PnL = SELL:+actual_return；BUY:−actual_return；FLAT:0`（1 MWh/仓）。与
  `code/risk_gate/calibrate.py` 完全同口径。
- **train**：**无 v2 预测**（v2 只出了 val/test）。用 canonical **无条件结构证据**——
  R7a 以「CONTROLX 全行视为 BUY（PnL=−return）」、R7b 以「ELCA 全行视为 SELL（PnL=+return）」
  评估；这与 guardrail 推导时的证据口径一致。train 行标注 `structural-unconditional`。

### 1.3 列定义

| 列 | 含义 |
|---|---|
| trade_count | 该窗口该规则拒绝的交易笔数（被拒集大小） |
| original_pnl | 该窗口全候选累计 PnL（规则关闭时的基线） |
| rejected_pnl | 被该规则拒绝的子集累计 PnL（负 = 拒绝有价值） |
| remaining_pnl | 该窗口剩余候选累计 PnL（= original − rejected） |
| win_rate | 剩余集（remaining）的胜率 P(PnL>0) |

---

## 2. 跨窗口表

### 2.1 R7a —— CONTROLX BUY REJECT

| window | candidate_scope | trade_count | original_pnl | rejected_pnl | remaining_pnl | win_rate(remaining) |
|---|---|---|---|---|---|---|
| train | structural-unconditional | 6,576 | −61,369 | **−61,369** | 0（全拒） | — |
| val1 | model-driven | 1,750 | +4,971 | **+1,338** | +3,634 | 0.642 |
| val2 | model-driven | 1,872 | −37,188 | **−38,693** | +1,505 | 0.548 |
| test | model-driven | 1,267 | −153,961 | **−138,190** | −15,771 | 0.567 |

> CONTROLX 无条件漂移（actual_return 均值）按窗口：train **+9.33** / val1 **−0.76** /
> val2 **+20.67** / test **+84.0**。val1 漂移为负 → BUY（赌 Return<0）在该窗口是正 EV；
> R7a 在 val1 会拒绝正 EV 交易。

### 2.2 R7b —— ELCA SELL REJECT

| window | candidate_scope | trade_count | original_pnl | rejected_pnl | remaining_pnl | win_rate(remaining) |
|---|---|---|---|---|---|---|
| train | structural-unconditional | 2,159 | −2,490 | **−2,490** | 0（全拒） | — |
| val1 | model-driven | **0**（v2 val 无 ELCA） | +4,971 | — | — | 0.703 |
| val2 | model-driven | **0**（v2 val 无 ELCA） | −37,188 | — | — | 0.629 |
| test | model-driven | 1,455 | −153,961 | **−12,145** | −141,817 | 0.557 |

### 2.3 R6 —— 低样本（hist_n < 150）REJECT

| window | candidate_scope | trade_count | original_pnl | rejected_pnl | remaining_pnl | win_rate(remaining) |
|---|---|---|---|---|---|---|
| train | structural-coverage | 2,159（ELCA 全行；hist_n<150 = 2,112） | —（方向不可签署） | — | — | — |
| val1 | model-driven | **0**（CONTROLX/SNLNDRO hist_n ≥ 729，不触发） | +4,971 | — | — | 0.703 |
| val2 | model-driven | **0** | −37,188 | — | — | 0.629 |
| test | model-driven | 1,464 | −153,961 | **−14,231** | −139,730 | 0.571 |

test 被拒集细分（全为 ELCA）：

| 方向 | n | cum PnL | mean | 说明 |
|---|---|---|---|---|
| ELCA SELL | 1,359 | **−14,963** | −11.01 | 拦对了（ELCA test 漂移 −8.25，SELL 逆漂移负 EV） |
| ELCA BUY | 105 | **+731** | +6.97 | **误伤**：小样本 BUY 是正 EV（与 `risk_gate_v02_rules.md` §6 记录一致） |

---

## 3. 分类判定

| 规则 | 判定依据（被拒集 cum 符号） | 分类 |
|---|---|---|
| R7a | train −61,369 / val1 **+1,338** / val2 −38,693 / test −138,190 | **REGIME_DEPENDENT** |
| R7b | train −2,490 / test −12,145（val1/val2 无样本） | **STABLE**（可用窗口内；val 不可验证） |
| R6 | 仅 test −14,231 有符号 PnL；train 仅覆盖证据；val 无样本 | **INSUFFICIENT_DATA** |

### 3.1 对既有 `calibrate.py`「NOT_OVERFIT」结论的修正

`code/risk_gate/calibrate.py` 把 R7a 判定为 NOT_OVERFIT，依据是 **整个 val 窗口**
（2026-01-02~06-01）被拒集 cum = −37,355 与 test 同为负。本次把 val 按时间拆成
val1 / val2 两个子窗口后，发现 **val1 被拒集为 +1,338（正 EV）**：

- val1（2026-01-02 ~ 03-15）是 CONTROLX 漂移转负的短窗口（drift −0.76，正收益小时占比
  仅 0.235），模型看空的 BUY 押注在该窗口正确。
- 「整个 val 为负」是 val1 的小幅正（+1,338）被 val2 的大幅负（−38,693）淹没的结果。

**修正后的诚实表述**：R7a「不是 test 过拟合」成立（test 负 EV 是真实的），但它是
**regime 依赖**规则——其有效性依赖「CONTROLX 漂移为正」这一结构事实，而该事实在 val1
曾短暂转负。因此 R7a **不能**当作无条件长期 Production Rule。

### 3.2 组合 Gate 净效果（model-driven 窗口）

| 窗口 | 全候选 | 被拒集（组合；val1/val2 实际仅 R7a 触发） | 保留集 | win_rate(保留) |
|---|---|---|---|---|
| val1 | +4,971 | +1,338（R7a） | +3,634 | 0.642 |
| val2 | −37,188 | −38,693（R7a） | +1,505 | 0.548 |
| test | −153,961 | −149,603（R7a+R7b+R6） | −4,358 | 0.525 |

> 与 `risk_gate_v02_calibration.json` 的 val/test 汇总数字完全一致（val 全候选 −32,216、
> 被拒 −37,355、保留 +5,139；test 全候选 −153,961、被拒 −149,603、保留 −4,358）。
> 新增信息只在子窗口粒度：**val1 上组合 Gate 是净负贡献**（从 +4,971 降到 +3,634）。

---

## 4. 是否进入长期 Production Rule

| 规则 | 能否晋级 | 条件 |
|---|---|---|
| R7a | **否（保持临时）** | 必须绑定「CONTROLX node_drift > 0」复核条件；若漂移转负立即停用。晋级前需在更多 regime 验证 |
| R7b | **暂不（临时）** | train/test 稳定，但 val 无 ELCA 样本（cold-start 节点在 v2 训练中缺失），样本积累后必须复核 |
| R6 | **否（保持临时）** | 价值与方向耦合：只该在「低样本 + 逆漂移方向」时拦截，不应无条件拒绝 ELCA BUY（test 已证明误伤 +731） |

**通用原则**：三条 guardrail 都带 `DATA-DERIVED TEMPORARY GUARDRAIL` 标注与
`threshold_source` 证据，均以「漂移方向 / 样本量」这类可能随 regime 改变的结构事实为条件。
在本数据上：
- 结构事实「CONTROLX 漂移为正」在 train(+9.33)/val2(+20.67)/test(+84.0) 成立，**val1 曾转负**；
- 结构事实「ELCA 漂移为负」在 train(−1.15)/test(−8.25) 成立，val 无法验证；
- 结构事实「ELCA 样本不足」在 train/test 成立，但样本积累（>150）后 R6 自动失效。

因此三者都应继续标注 TEMPORARY，并由「漂移符号 + 样本量」的可审计条件触发复核，
**不得**因本窗口表现良好就晋级为无条件 Production Rule。

---

## 5. 诚实声明

- train 窗口用的是无条件结构证据（无 v2 模型预测），不是 model-driven 候选；因此
  R7a/R7b 的 train 行回答的是「结构上该方向是否负 EV」，不是「v2 模型在该窗口会产生多少
  被拒交易」。val/test 行是 model-driven，口径与 `calibrate.py` 一致。
- val1 的 R7a 被拒集 +1,338 绝对值不大（mean +0.76/笔），但符号为正且样本量 1,750，
  足以推翻「CONTROLX BUY 恒负 EV」的强主张；分类定为 REGIME_DEPENDENT 而非 UNSTABLE。
- 本分析只覆盖 2025-04-03 ~ 2026-08-05 约 16 个月的数据，且 2026-06 后为强正漂移 regime；
  跨 regime 泛化结论只能由未来更多窗口验证。
