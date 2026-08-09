# Confidence → model_signal_strength 改名 + 概率校准（Agent C）

> 作者：Agent C（校准与 Risk Gate 稳定性工程师）｜ 日期：2026-08-09
> 分析脚本：`code/tmp/agent_c_calibration.py`
> 图表：`code/data/v021_reliability_val.png`、`code/data/v021_reliability_test.png`、`code/data/v021_bin_hitrate_val_test.png`、`code/data/v021_conf_vs_prob.png`
> 只读数据；未调任何 CatBoost/LightGBM 参数；未联网。

---

## 0. TL;DR（结论先行）

1. **`confidence` 不是概率，改名 `model_signal_strength`（模型信号强度）**。它是
   `0.5·prob_strength + 0.5·magnitude_certainty` 的显式组合量（`model_v2.py` 公式），
   由「方向概率强度」与「幅度相对不确定度」各半加权。未校准前**不得**把它当概率用，
   也不得恢复 "confidence" 命名。
2. **`prob_positive`（CatBoost 二分类概率）也未校准**：
   - 模型输出被压缩在 **0.33 ~ 0.72**，**从不说 80-90%**（0.80-0.90 / 0.90-1.00 桶在
     val 与 test 均为 0 行）。
   - val 分桶命中率与声称概率偏差大：0.50-0.60 桶声称 0.56、实际 **0.65**；0.60-0.70 桶
     声称 0.62、实际 **0.75**（低估正概率）。ECE(8-bin) = **0.094**。
   - 偏差是 **regime 依赖**的：val 与 test 的 base rate 从 0.465 翻到 0.530，0.40-0.50 桶
     的偏差方向由 +0.050 翻到 −0.068；val 拟合的 Isotonic 应用到 test 反而劣化
     （Brier 0.2310 → 0.2403）。
3. **结论：`confidence` 保持 `model_signal_strength` 命名；`prob_positive` 同样未校准，
   在完成可靠的概率校准（且跨窗口 transfer 验证通过）之前，两者都不得作为概率口径对外**。
4. 需要改名的位置清单见 §3（数据列 / 代码 / 文档 / Decision Card / UI）。

---

## 1. 为什么改名：confidence 是什么

V0.2 模型输出 `confidence`（`code/model_v2.py` `confidence_and_uncertainty()`）：

```
confidence = clip( 0.5·prob_strength + 0.5·magnitude_certainty, 0, 1 )
  prob_strength       = 2·|prob_positive − 0.5|        # 方向概率强度（0.5 处为 0）
  magnitude_certainty = |q50| / (|q50| + iqr/2)        # 幅度相对不确定度（信噪比式）
```

- 它是**信号强度**，不是 P(方向正确)、也不是 P(盈利)、也不是风险度量。
- 实测与 `prob_positive` 呈**负相关**（val corr **−0.70**、test corr **−0.51**，
  见 `code/data/v021_conf_vs_prob.png`）：本数据上当模型看正方向时，幅度信噪比往往偏低，
  导致 `confidence` 值整体被压缩在 **0.00 ~ 0.44**（val max 0.429 / test max 0.435），
  分布与概率口径完全不可比。
- 现有文档已多次记录它未校准（`docs/DecisionPipeline.md §7`、
  `docs/stage3/confidence_calibration_analysis.md` 结论 CONFIDENCE NOT CALIBRATED、
  `docs/risk_gate_v02_rules.md §8`）。本次改名是把这个事实固化到命名里。

**命名约定（校准完成前）**：

| 字段 | 旧称 | 新称 | 中文 | 语义 |
|---|---|---|---|---|
| 模型方向概率 | prob_positive | prob_positive | 正方向概率（模型原始输出） | P(Return>0)，CatBoost 二分类 Logloss 概率 |
| 组合信号强度 | confidence | **model_signal_strength** | 模型信号强度 | 概率强度×幅度信噪比的组合量，**非概率** |
| 不确定度 | uncertainty | uncertainty | 不确定度 | q10/q90 区间宽 / 分布尺度 |

> 只有 `prob_positive` 完成概率校准（且跨窗口 transfer 验证通过）后，才允许把
> `model_signal_strength` 重新表述为可校准的 `confidence`；在此之前一律用
> `model_signal_strength`。

---

## 2. Probability Calibration（prob_positive vs 真实 direction）

### 2.1 口径与数据

- 样本：`predictions_v2_val.csv`（val，n=7244，CONTROLX+SNLNDRO）、`predictions_v2.csv`（test，n=4678，全节点）。
- **未含 train 尾部**：v2 模型只输出 val/test 两窗（`model_v2.py` 设计如此）；本分析只读现有
  预测文件，不重训模型，因此没有 train 的 `prob_positive`。val 是本次校准主窗口，test 作
  独立跨窗口复核（val→test 的 base rate 从 0.465 翻到 0.530，本身就是一次严格的 regime
  迁移检验）。
- label：`y = (actual_return > 0)`。val base rate **0.4648**；test base rate **0.5301**（regime 偏移）。
- 评估：Brier Score / Reliability Curve / ECE / 分桶命中率（0.50-0.60 … 0.90-1.00）。

### 2.2 指标

| 窗口 | n | base_rate | mean(prob_positive) | Brier | ECE(8-bin) |
|---|---|---|---|---|---|
| val | 7244 | 0.4648 | 0.4594 | **0.2146** | **0.0935** |
| test | 4678 | 0.5301 | 0.5044 | **0.2310** | **0.0408** |

> 参考：常数预测（=base rate）的 Brier ≈ 0.249；模型 Brier 0.215/0.231 略优于常数 →
> 有弱 skill（与 AUC≈0.63 一致），但远称不上好。

### 2.3 Reliability Curve / 分桶命中率

完整分桶（`v021_reliability_val.png` / `v021_reliability_test.png`）：

| 桶 | val n | val mean_pred | val observed(命中率) | test n | test mean_pred | test observed |
|---|---|---|---|---|---|---|
| <0.30 | 7 | 0.296 | 0.000 | 11 | 0.298 | 0.455 |
| 0.30-0.40 | 3607 | 0.352 | **0.265** | 1534 | 0.350 | 0.338 |
| 0.40-0.50 | 348 | 0.485 | 0.535 | 250 | 0.480 | 0.412 |
| 0.50-0.60 | 2464 | 0.560 | **0.655** | 1630 | 0.560 | **0.637** |
| 0.60-0.70 | 818 | 0.620 | **0.751** | 1252 | 0.628 | 0.649 |
| 0.70-0.80 | 0 | — | — | 1 | 0.717 | 1.000 |
| 0.80-0.90 | 0 | — | — | 0 | — | — |
| 0.90-1.00 | 0 | — | — | 0 | — | — |

任务指定 5 桶命中率（每桶 = 模型声称该区间概率时，真实 Return>0 的比例）：

| 声称概率桶 | val 命中率 | test 命中率 |
|---|---|---|
| 0.50-0.60 | **0.655**（n=2464） | **0.637**（n=1630） |
| 0.60-0.70 | **0.751**（n=818） | **0.649**（n=1252） |
| 0.70-0.80 | 无样本（n=0） | 1.000（n=1，不可靠） |
| 0.80-0.90 | **0 行** | **0 行** |
| 0.90-1.00 | **0 行** | **0 行** |

### 2.4 必须回答的问题

> **「模型说 80-90% 概率为正时，真实 Return>0 的比例是多少？」**

**答案：没有这样的样本。** 模型输出的 `prob_positive` 最大约 0.72，**从未**给出 80-90%
的声称概率（val 与 test 的 [0.80, 0.90) 桶均为 0 行，[0.90, 1.00] 桶也为 0 行）。
模型在最接近的「声称 0.60-0.70」桶里：val 声称均值 0.62、真实 0.75；test 声称 0.63、真实 0.65。
换句话说，模型在「看正」的方向上**系统性低估**了正概率，且它的概率分布被过度压缩，
无法支撑任何高概率断言。

### 2.5 是否需要 calibration？—— 明确：需要，且目前未校准

1. **分桶命中率与声称概率偏差大**（val 尤甚）：
   - val 0.30-0.40 桶声称 0.35、实际 0.27（高估负概率）；
   - val 0.50-0.60 / 0.60-0.70 桶实际 0.65 / 0.75，比声称高出 0.09 / 0.13（低估正概率）。
   - ECE(8-bin) val 0.094，不可忽略。
2. **偏差是 regime 依赖的**：val base rate 0.465 → test 0.530；0.40-0.50 桶偏差由
   val 的 +0.050 翻到 test 的 −0.068。**val 上拟合的 Isotonic 应用到 test，Brier 反而从
   0.2310 劣化到 0.2403** —— 简单单调重标定跨窗口不迁移。
3. **高概率区间完全未被覆盖**：模型从不输出 >0.72，任何校准都无法凭空给出 80-90% 的
   可信概率；这条尾巴必须靠数据/模型改进（如温度缩放、概率平滑、或换损失函数）先撑开。

**结论：`prob_positive` 未校准；`confidence` 改名 `model_signal_strength` 成立且必须保持。**

### 2.6 与 V0.1 `confidence_calibration_analysis.md` 的关系

V0.1 那篇校准的是老三模型的 `confidence`（Rule 一致性 / |2p−1| / 混合式），结论
「CONFIDENCE NOT CALIBRATED」。本文校的是 V0.2 的 `prob_positive`（真正的模型概率），
结论是「同样未校准，且概率分布被压缩在 0.33~0.72」。两篇互为补充：老 confidence 既非概率，
新 prob_positive 也还没到「校准概率」的标准。

---

## 3. 需要改名的位置清单（confidence → model_signal_strength）

> 本清单是「改名计划」而非已执行的批量替换。由于 `confidence` 深嵌在回测/规则/测试里，
> 批量改名会破坏回归对齐与单测；建议按以下分层推进，先改对外口径，后改内部实现。

### 3.1 数据文件（predictions CSV 列名）

| 文件 | 改动 |
|---|---|
| `code/data/predictions_v2.csv` | 列 `confidence` → `model_signal_strength` |
| `code/data/predictions_v2_val.csv` | 同上 |
| `code/data/model_v2_notes.json` | `confidence` 键与说明 → `model_signal_strength`（`confidence_uncertainty_formulas` 段） |
| `code/data/stage3/risk_gate_v02_calibration.json` | `confidence_uncertainty_stability` 段标注为 signal strength |
| `agent/explanation/decision_cards.json` | Decision Card 的 `confidence` 字段 → `model_signal_strength` |

### 3.2 模型 / 决策代码

| 文件 | 位置（行） | 改动 |
|---|---|---|
| `code/model_v2.py` | 10, 81, 189-215, 222, 231, 262-288, 443-450, 466 | 函数 `confidence_and_uncertainty` → `model_signal_strength_and_uncertainty`；列名与 notes 键同步；docstring 与 `calibration_observations` 标注「信号强度非概率」 |
| `code/decision/rule_engine.py` | Decision.confidence 字段、`min_confidence`、R-D/R-B | 阈值语义改为 `min_signal_strength`；reason_code 可保留 `LOW_CONFIDENCE`（历史码）但 message 标注 signal strength |
| `code/backtest_v2.py` | 90, 261, 406, 422, 440, 523, 759 | `DECISION_CFG.conf_threshold` → `signal_threshold`；读列名同步；evidence 字符串 `conf=` 改 `sig=` |
| `code/risk_gate/config.py` | 103, 129-130 | `required_fields` 与 `min_confidence` → `model_signal_strength`（保留 LOW_CONFIDENCE 兼容） |
| `code/risk_gate/rules.py` | 296-312 | `rule_low_confidence` 读 `model_signal_strength`；reason_code 可保留 `LOW_CONFIDENCE` |
| `code/risk_gate/gate.py` | 19, 147, 174 | candidate 字段与 details 键 → `model_signal_strength` |
| `code/risk_gate/calibrate.py` | 240-259, 301-302, 320 | `calibrate_confidence_uncertainty` 改名并标注口径 |
| `code/risk_gate/__init__.py` | 7 | 输入字段清单标注 |
| `code/risk_gate/tests/*` | test_gate.py 37/117-118/137/145、test_overfit_check.py 37、test_evidence_gate.py 39 | 测试夹具字段同步 |
| `code/decision/tests/test_rule_engine.py` | 全文件 | 夹具与断言同步 |

### 3.3 Decision Card（agent/explanation）

| 文件 | 位置 | 改动 |
|---|---|---|
| `agent/explanation/decision_card.py` | 70, 102, 133 | `DecisionCard.confidence` → `model_signal_strength`；Markdown 渲染 `Confidence {x:.2f}` → `Signal {x:.2f}` |
| `agent/explanation/cards.py` | 111-115, 300, 353 | 从旧模型预测读的 `*_conf` 列 → `*_signal`；卡片输出字段同步 |

### 3.4 Production UI（web）

| 文件 | 现状 | 结论 |
|---|---|---|
| `code/app.py` | 只返回 `prob_sell`，未暴露 `confidence` | 无需改名；若后续加信号强度展示，字段名用 `model_signal_strength` |
| `code/templates/index.html` | 展示 q10/q90 区间与建议，未展示 confidence | 无需改名；文案中「置信区间」指分位数区间，非 confidence，需在 tooltip 明确 |

### 3.5 文档（docs）

| 文件 | 位置 | 改动 |
|---|---|---|
| `docs/DecisionPipeline.md` | 11, 37, 79, 87-89, 104 | 模型输出清单与三态阈值 `confidence ≥ 0.20` → `model_signal_strength ≥ 0.20`（标注信号强度）；§7 局限改为「model_signal_strength 未校准」 |
| `docs/risk_gate_v02_rules.md` | 53, 86, 105, 107, 115, 189, 213 | R1 行与 Rule Engine R-D 阈值语义 → signal strength |
| `docs/v0.2_architecture_diff.md` | 14, 51, 94 | 模型输出清单与遗留风险 3 标注 signal strength |
| `docs/v0.2_lead_summary.md` | 13, 59 | 交付清单 `confidence` → `model_signal_strength`；下一步「替换失效 confidence」表述更新 |
| `docs/v0.2_backtest.md` | 56, 72 | 局限行标注 signal strength |
| `docs/Architecture.md` | 51, 78, 98 | 模型输出清单标注 |
| `docs/Model.md` | 10 | `confidence ∈ [0,1]` → `model_signal_strength ∈ [0,1]`（组合量） |
| `docs/Backtest.md` | 9, 11, 86 | 消费 schema 与 Rule 的 confidence 定义标注 |
| `docs/business_contract.md` | 25, 43, 49, 60 | 「预测置信度」改为「模型信号强度」；Decision Policy 输入清单同步 |
| `docs/stage3/confidence_calibration_analysis.md` | 全文 | 历史记录，保留原样，仅在文首加「此 confidence 为 V0.1 组合量，现更名为 model_signal_strength」的指针 |

> 备注：`docs/stage3/` 下的历史分析文档记录的是 V0.1 老 confidence 口径，保留不改，
> 只在入口加命名指针即可。

---

## 4. 诚实声明

- 本次分析只读数据，未训练/未调参；图表与数字全部可复现（`code/tmp/agent_c_calibration.py`）。
- `prob_positive` 未校准的结论基于 val 与 test 两个独立窗口；跨窗口 Isotonic 迁移失败
  说明校准必须在「口径随时间稳定的 base rate」前提下做，而本数据 val→test 的 base rate
  从 0.465 翻到 0.530，属于结构性的 regime 漂移。
- 在完成概率校准并验证跨窗口 transfer 之前，任何把 `model_signal_strength` 或
  `prob_positive` 当作「置信度/概率」对外表述的行为都应被审计拦截。
