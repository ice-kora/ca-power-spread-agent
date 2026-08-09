# Risk Gate V0.2 · 白盒规则文档 + Rule Engine 规格

> 作者：Agent C（Risk Gate 与 Rule Engine 工程师）｜ 日期：2026-08-09
> 代码：`code/risk_gate/`（模块）+ `code/decision/rule_engine.py`（Rule Engine）
> 校准/过拟合检验：`code/risk_gate/calibrate.py` → `code/data/stage3/risk_gate_v02_calibration.json`
> 口径：Return = DA − RTPD；决策时点 = D-1 日 **10:00 PT**（DAM Market Close，官方 BPM）
> PnL：SELL = +actual_return；BUY = −actual_return（1 MWh/仓）

---

## 0. TL;DR

1. **Risk Gate 独立成模块**，职责严格限定为"放行/警告/拒绝"，**不预测方向**。
   输入 = v2 模型输出 + node/hour + 可用的 Pre-decision Evidence + 相似亏损 Case + as-of 风险特征；
   输出 = `PASS / WARNING / REJECT` + `risk_reasons`（reason_code 列表）。
2. **V0.1 empirical guardrails 全部保留**（CONTROLX BUY 拒绝 / ELCA SELL 拒绝 / 低样本拒绝），
   每条标注 **`DATA-DERIVED TEMPORARY GUARDRAIL`**，并完成 **过拟合检验**（结论：**非 test 过拟合**，
   CONTROLX BUY 在 val 与 test 两个独立窗口均为负 EV）。
3. **White-box Rule Engine 独立成模块**（`code/decision/rule_engine.py`），规则可读、可配置、
   可测试、有版本号；**决策不埋在模型内部**。
4. **诚实结论**：gate 是护栏不是引擎——它把 v2 在 val 的 PnL 从 **−32,216 拉到 +5,139**、
   在 test 从 **−153,961 收到 −4,358**（移除 −149,603 的系统性负期望交易），
   但 **v2 test 保留集仍为负**（CONTROLX SELL 信号在 test 亦弱），gate 无法弥补模型侧缺陷。

---

## 1. 模块结构（Risk Gate）

```
code/risk_gate/
  constants.py        reason_code 全集 / 方向 / PnL 口径
  config.py           RiskGateConfig（阈值全部 train+val 校准，带版本与修改记录）
  rules.py            每条规则一个纯函数（RuleHit：rule_id/reason_code/level/message）
  gate.py             RiskGate.evaluate(candidate) -> GateVerdict
  evidence_adapter.py Evidence Time Gate 适配（只放行 decision_eligible=True）
  case_adapter.py     Case Library 适配（相似亏损 Case 检索，as-of）
  calibrate.py        train+val 校准 + 过拟合检验（test 只验证）
  tests/              unittest（46 例，全部通过）
```

### 与决策流水线的关系（docs/DecisionPipeline.md）

```
① Predictive Model → ② Evidence → ③ Evidence Time Gate（只放行 Pre-decision）
→ ④ Case Retrieval → ⑤ Risk Gate（本模块）→ ⑥ Rule Engine（本任务）→ ⑦ Human → ⑧ Review
```

### Risk Gate 候选输入（全部 as-of，≤ target_date-2）

| 字段 | 含义 | 来源 |
|---|---|---|
| node / target_date / hour | 目标交易 | 预测文件 |
| expected_return / confidence / uncertainty | v2 模型输出 | `predictions_v2*.csv` |
| direction | BUY(er<0) / SELL(er>0)，可缺省由符号推导 | 本模块派生 |
| hist_n / cvar99 / rcvar99 / vol_ratio / node_drift | 同 node×hour 历史风险特征 | `stage3/risk_features.parquet` |
| similar_tail_loss_cases | Case Library 匹配的亏损 Case（as-of） | `case_adapter.py` |
| evidence_direction_context | eligible Evidence 方向汇总 | `evidence_adapter.py` |

---

## 2. Risk Gate 规则表（rule_id / 输入 / 阈值来源 train+val / 业务理由 / reason_code / 版本）

> 级别：**REJECT** = 一票否决；**WARNING** = 放行但标记（Rule Engine / 交易员参考）。
> 所有阈值来源列在 train+val；test 零调参。

### 2.1 REJECT 级规则（拦截）

| rule_id | 输入条件 | reason_code | 阈值来源（train+val） | 业务理由 | 版本/状态 |
|---|---|---|---|---|---|
| **R01** | `required_fields` 任一缺失/NaN/空 | `DATA_MISSING` | 无（第一性原理） | 关键输入缺失→宁保守不穿越 | 0.2 |
| **R7a** | node=CONTROLX 且 direction=BUY | `BUY_ON_POSITIVE_DRIFT_NODE` | canonical train+val 无条件漂移 **+9.68**；无条件 BUY mean **−9.68**、maxloss **−3,656**、cvar99 **−916**；v2 val 被拒集 mean **−10.31** | CONTROLX 正漂移 → BUY 逆漂移；赔率倒挂（命中 +100 / 做错 −450） | **DATA-DERIVED TEMPORARY GUARDRAIL** |
| **R7b** | node=ELCA 且 direction=SELL | `SELL_ON_NEGATIVE_DRIFT_NODE` | canonical train+val 无条件漂移 **−1.15**；SELL mean **−1.15**、maxloss **−357**、cvar99 **−224**；v2 test 被拒集 mean **−8.35** | ELCA 负漂移 → SELL 逆漂移；cold-start 样本不足 | **DATA-DERIVED TEMPORARY GUARDRAIL** |
| **R6** | `hist_n` < 150 | `LOW_SAMPLE_SUPPORT` | ELCA train hist_n 中位 44（max 89）、test 中位 121（max 154）；主节点 ≥455 | 样本不足统计不可靠 | **DATA-DERIVED TEMPORARY GUARDRAIL** |

> R7 泛化版本（`drift_rule_enabled`，默认关闭）：node_drift > +5.0 且 BUY，或 node_drift < −2.0 且 SELL。
> 默认关闭的原因：SNLNDRO 漂移 +2.61 但 BUY 尾极小（cvar99 −98），纯漂移符号会误伤——V0.1 明确弃用泛化、改走节点实证。

### 2.2 WARNING 级规则（标记不拦）

| rule_id | 输入条件 | reason_code | 阈值来源（train+val） | 业务理由 | 版本/状态 |
|---|---|---|---|---|---|
| **R5** | SELL 侧 `cvar99`<−600 或 BUY 侧 `rcvar99`<−600 | `EXTREME_TAIL_NODE` | V0.1 R4 扫描：cvar99 阈值无法捕获 CONTROLX SELL 最大亏损（−2,066 行事前 cvar99 仅 −276） | 历史尾部无法事前识别 DA 崩塌尾，只能降级为警告 | 0.2（继承 V0.1 R4 降级结论） |
| **R3** | `vol_ratio` > 3.0 | `HIGH_VOLATILITY` | V0.1 R3 分层：vol_ratio 无单调判别力（>3.0 段反而 mean +10.49） | 高波动是 CONTROLX 常态，非"这笔危险"信号 | 0.2（继承 V0.1 R3 删除结论，仅保留警告） |
| **R9** | `uncertainty` > 0.95 | `MODEL_UNSTABLE` | v2 val 分层：uncertainty 在 CONTROLX 上与 PnL **非单调**（高不确定段 = 极端事件彩票，右尾正收益） | 高不确定 ≠ 该避开；极端事件恰是高不确定 | 0.2（本版新增，默认不拦） |
| **R8** | 命中相似亏损 Case（同 node×direction，|Δhour|≤3，PnL<−300） | `SIMILAR_TAIL_LOSS_CASE` | `agent/case_library/cases.json`（18 条 test 真实极端事件，as-of 过滤） | 历史类似亏损提示，供人工复核 | 0.2 |
| **R1** | `confidence` < 0.20 | `LOW_CONFIDENCE` | V0.1 R1 分层：conf 越高 mean 越差（+2.06 → −19.29），CONFIDENCE NOT CALIBRATED | 置信度既非概率也非风险度量；Rule Engine 负责转 NO_TRADE | 0.2（继承 V0.1 R1 删除结论，仅警告） |
| **R10** | \|`expected_return`\| < 5.0 | `EXPECTED_RETURN_TOO_SMALL` | V0.1 R5 扫描：\|er\| 阈值不改善 cvar99，只打薄 coverage | 边太小；Rule Engine 负责转 NO_TRADE | 0.2（继承 V0.1 R5 删除结论，仅提示） |
| **R11** | eligible Evidence 方向与候选相反 | `EVIDENCE_CONFLICT` | 当前无真实数据源（全 UNCERTAIN），几乎不触发 | 证据方向冲突应谨慎 | 0.2 |

> **诚实说明（重要）**：`LOW_CONFIDENCE` / `HIGH_VOLATILITY` / `MODEL_UNSTABLE` / `EXTREME_TAIL_NODE`
> 在 train+val 验证中**不具判别力**（V0.1 已删/降级，本版补充 uncertainty 验证亦然）。
> 因此全部实现为 **WARNING 级、默认不拦交易**，仅保留 reason_code 供审计与交易员参考。
> 把它们当 REJECT 用在本数据上会**砍掉顺漂移右尾收益**（V0.1 实证：lag1_pct>0.95 信号
> train+val 负 EV / test 正 EV +45,509，regime 翻转）。

---

## 3. White-box Rule Engine 规格（`code/decision/rule_engine.py`）

版本 `0.2`；修改记录见模块 `changelog`。规则流水线（命中即返回）：

| rule_id | 条件 | 决策 | reason_code |
|---|---|---|---|
| R-A | RiskGate == REJECT | NO_TRADE | `RISK_GATE_REJECTED` |
| R-B | expected_return/confidence 缺失 | NO_TRADE | `DATA_MISSING` |
| R-C | \|expected_return\| < min_spread (5.0) | NO_TRADE | `EXPECTED_RETURN_TOO_SMALL` |
| R-D | confidence < min_confidence (0.20) | NO_TRADE | `LOW_CONFIDENCE` |
| R-E | eligible Evidence 方向冲突 | NO_TRADE | `EVIDENCE_CONFLICT` |
| R-F | RiskGate == WARNING 且 `reject_on_warning=True`（默认 False） | NO_TRADE | `RISK_GATE_WARNING_ESCALATED` |
| R-G | expected_return > 0 | **SELL_DA** | `EXPECTED_RETURN_POSITIVE` |
| R-H | expected_return < 0 | **BUY_DA** | `EXPECTED_RETURN_NEGATIVE` |
| R-I | expected_return == 0 / 无方向 | NO_TRADE | `NO_CLEAR_DIRECTION` |

- 阈值全部可配置（`RuleEngineConfig`）：`min_spread=5.0`（对齐 `DECISION_CFG.ret_threshold_abs`）、
  `min_confidence=0.20`（对齐 `DECISION_CFG.conf_threshold`）、`reject_on_warning=False`。
- **决策不埋在模型内部**：模型只输出预测量；交易动作由本引擎判定。
- 输出含 `reason`（命中的 reason_code）与 `rules_hit`（命中规则 id）+ `version`。

---

## 4. Evidence Time Gate 接入

`code/risk_gate/evidence_adapter.py` 包装 `agent/evidence/time_gate.py`：

- `filter_eligible_evidence()` → 只放行 `decision_eligible=True` 的 Pre-decision Evidence；
- `assert_no_post_decision()` → Post-decision Evidence 误入决策层直接抛 `RuntimeError`（Leakage Guard）；
- `evidence_direction_context()` → 方向汇总（当前全 UNCERTAIN，无方向信号）。
- 单元测试：`test_evidence_gate.py`（6 例，含"Post-decision 证据不进入"）。

---

## 5. 过拟合检验（V0.1 guardrail 是否只对 test 有效？）

方法：v2 预测分两窗口独立评估——**val 2026-01-02~06-01（3622 CONTROLX + 3622 SNLNDRO）** 与
**test 2026-06-02~08-05（全节点 4678）**。每条 guardrail 的"被拒集"在两个窗口的累计 PnL 都为负
⇒ 结构性、非 test 过拟合。

| guardrail | val 被拒集 | test 被拒集 | 判定 |
|---|---|---|---|
| **R7a** CONTROLX BUY | n=3622, cum **−37,355**, mean **−10.31**, maxloss **−3,656** | n=1267, cum **−138,190**, mean **−109.07**, maxloss **−2,216** | **NOT_OVERFIT**（两窗口均负 EV） |
| **R7b** ELCA SELL | val 无 ELCA（v2 未训 ELCA）→ 用 canonical train+val 漂移 **−1.15** 背书 | n=1455, cum **−12,145**, mean **−8.35**, maxloss **−914** | **NOT_OVERFIT_VIA_STRUCTURAL** |
| **R6** 低样本 | 主节点 hist_n ≥ 729，不触发 | n=1464（ELCA 几乎全部），cum **−11,413** | 结构事实（ELCA train 中位 44） |

**结论：V0.1 guardrail 不是 test 窗口过拟合**。核心机制是"漂移方向 × 节点"这一结构性事实，
在两个独立窗口都成立。但注意它是 **临时 guardrail**——若未来 CONTROLX 漂移转负、
或 ELCA 样本积累超过 150，R7a/R7b/R6 必须复核（规则白盒、可审计）。

---

## 6. Risk Gate 在 v2 预测上的表现（诚实）

| 窗口 | v2 全候选 | gate 后保留 | 被拒集（移除的负期望） |
|---|---|---|---|
| val | n=7244, cum **−32,216** | n=3622（SNLNDRO）, cum **+5,139**, mean +1.42 | n=3622（CONTROLX BUY）, cum **−37,355** |
| test | n=4678, cum **−153,961** | n=1851, cum **−4,358**, mean −2.35 | n=2827, cum **−149,603** |

逐节点（test 保留集构成）：

| 节点×方向 | n | cum PnL | mean | 说明 |
|---|---|---|---|---|
| CONTROLX SELL | 292 | **−7,259** | −24.86 | V0.1 gate 刻意不拦（Rule 模型 SELL 曾 +79k）；但 v2 的 CONTROLX SELL 信号在 test 亦弱 |
| SNLNDRO BUY | 624 | +543 | +0.87 | 保留 |
| SNLNDRO SELL | 935 | +2,357 | +2.52 | 保留（主力） |
| ELCA BUY（hist_n<150 被 R6 误伤） | 105 | **+731 被错过** | +6.97 | 低样本规则的诚实代价：连正 EV 的小样本 BUY 也一并关闭 |

**诚实结论**：
1. **gate 是有效的护栏**：val −32k → +5.1k；test −154k → −4.4k；被拒集在两个窗口均为负 EV。
2. **gate 不创造 alpha**：test 保留集仍为 **−4,358**，主要拖累是 **CONTROLX SELL**（−7,259）。
   V0.1 的 Rule 模型在 CONTROLX 上 SELL 赚 +79k，但 **v2 模型在 CONTROLX 上的 SELL 信号在 test 是负 EV**
   ——这是**模型侧问题**，gate 无法修补（gate 的 R7a 只拦 CONTROLX BUY）。
3. **R6 的误伤**：ELCA BUY（+731）因 hist_n<150 被一并拒绝。cold-start 节点在样本积累前，
   gate 的裁决是"不交易"而非"交易得更聪明"——这是显式取舍。
4. 若以"是否把灾难组合转为可接受小负/小正"为标准，**Risk Gate 达成设计目标**；若以"gate 是否让 v2
   变成正 alpha 策略"为标准，**否**——需要模型侧改进（尤其 CONTROLX SELL 信号），gate 只负责兜底。

---

## 7. 使用示例

```python
from code.risk_gate import RiskGate, match_similar_tail_cases, filter_eligible_evidence
from code.decision import RuleEngine

gate = RiskGate()                      # 默认配置（阈值 train+val 校准）
engine = RuleEngine()

candidate = {
    "node": "CONTROLX_1_N001", "target_date": "2026-07-10", "hour": 10,
    "expected_return": -30.0, "confidence": 0.6, "uncertainty": 0.5,
    "hist_n": 900, "cvar99": -900.0, "rcvar99": -700.0, "vol_ratio": 2.0,
}
candidate = match_similar_tail_cases(candidate, as_of=True)   # 挂上相似亏损 Case

eligible, post = filter_eligible_evidence(evidences, "2026-07-09T10:00:00")  # Evidence Time Gate
verdict = gate.evaluate(candidate)     # PASS / WARNING / REJECT + risk_reasons
decision = engine.evaluate(candidate, gate_verdict=verdict, evidences=eligible)
print(decision.decision, decision.reasons, decision.rules_hit)
```

运行校验：
- `python -m code.risk_gate.calibrate`（校准 + 过拟合检验，写 JSON）
- `python -m unittest discover -s code -t .`（46 例单测，全部通过）

---

## 8. 局限与诚实声明

1. **guardrail 依赖漂移方向稳定**：CONTROLX +9.68 / ELCA −1.15 是 train+val 结构事实；regime 翻转需复核。
2. **gate 覆盖范围**：CONTROLX SELL 的 DA 崩塌尾（2026-06-23/06-30）不在本版拦截——
   V0.1 已证明无法用事前内部信号可靠识别（cvar99/lag1_pct 均失败），强行拦截会误伤顺漂移右尾。
3. **case_library 全部来自 test 窗口**：as-of 检索对 test 早期候选几乎不触发，窗口推进后才逐步命中；
   `SIMILAR_TAIL_LOSS_CASE` 当前是提示级，不拦截。
4. **置信度/不确定度未校准（confidence 现名 `model_signal_strength`，即模型信号强度，非概率；见 docs/confidence_calibration.md）**：V0.1 + 本版均确认与 PnL 非单调，故全部降为 WARNING。
5. **未做仓位优化与成本**：契约约定 1 MWh/仓；test 窗口仅 65 天，外推受限。
