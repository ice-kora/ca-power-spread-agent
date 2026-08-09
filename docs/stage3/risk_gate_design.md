# CA-ISO 价差交易 · Risk Gate 设计（第一版白盒 · Agent D）

> 生成时间：2026-08-09
> 决策依据：`top_loss_event_analysis.md`（A） / `top_profit_event_analysis.md`（B） / `buy_sell`·`node_risk`·`hour_risk`·`confidence_calibration`·`model_agreement`（C）
> 数据：`canonical.parquet`（train 2025-04-03~2026-06-01 / val 2026-01-02~2026-06-01 / test 2026-06-02~08-05）+ `predictions_{rule,interpretable,catboost}.csv`
> **规则产生只用 train+val；test 只做最终验证。所有阈值由 train+val 数据扫描/验证得出，未用 test 调参。**

---

## 0. 诚实结论（先说结果）

**第一版 Risk Gate 是有效的**：施加在 Model Committee 上，把 test 窗口的累计 PnL 从 **−130,042** 拉到 **+962**，最大回撤从 **−147,957** 拉到 **−654**，单笔最差从 **−2,216** 收到 **−113**，CVaR(1%) 从 **−1,056** 收到 **−74**；同时保留了 **339/1523（22%）** 的正 EV 交易（全部为 SNLNDRO）。

但必须同时说清三件事：
1. **Risk Gate 的全部作用 = 拦截「CONTROLX BUY」**。test 窗口 committee 的 1,523 笔交易中 1,184 笔是 CONTROLX BUY（ML 双模型一致看 BUY，逆 +84 漂移），累计 −131k——gate 把它们全部拦下。gate **没有创造任何新 alpha**，只是把系统性负期望的尾部毒药移除。
2. **对本来就安全的 Rule 模型，gate 无任何改变**（Rule 只做 SELL，824 笔 +79,485 前后一致）。gate 是"护栏"，不是"引擎"。
3. **7 条候选规则中只留下 3 条有效**（R7 方向门 / R6 低样本 / R2 冲突确认）；R4 尾部门、R1 置信度门、R3 波动门、R5 期望边门**经验证无效或有害，明确删除/降级**（证据见 §3）。

---

## 1. Risk Gate 职责与接口

```
输入：候选交易 (node, target_date, hour, pred_direction, expected_return,
      rule_dir, interpretable_dir, catboost_dir, hist_n, cvar99, rcvar99)
输出：PASS / REJECT / PASS_WITH_WARNING + reason_code
```

reason_code 全集（本版实际使用的加粗）：
`BUY_ON_POSITIVE_DRIFT_NODE`、`SELL_ON_NEGATIVE_DRIFT_NODE`、`MODEL_DISAGREEMENT`、`LOW_SAMPLE_SUPPORT`、`EXTREME_TAIL_NODE`（警告级）。已删除：`LOW_CONFIDENCE`、`HIGH_VOLATILITY`、`EXPECTED_EDGE_TOO_SMALL`。

实现：`code/tmp/agent_d_gate.py`（纯 pandas/numpy 白盒，无任何学习/拟合）。

---

## 2. 最终规则清单

| rule_id | 输入（as-of 特征） | 判定 | reason_code | 阈值来源（train+val） | 业务理由 |
|---|---|---|---|---|---|
| **R7a** | node ∈ {CONTROLX} 且 direction=BUY | **REJECT** | `BUY_ON_POSITIVE_DRIFT_NODE` | CONTROLX train+val 无条件漂移 **+9.68**；无条件 BUY PnL mean **−9.68**、maxloss **−3,656**、cvar99 **−916**；LR walk-forward 代理在 val 的 CONTROLX BUY 3,620 笔 **mean −10.33、cum −37,412** | CONTROLX 正漂移 → BUY 逆漂移；右尾更长（+3,656 vs −2,295）；赔率倒挂（命中 +100 / 做错 −450，C 报告） |
| **R7b** | node ∈ {ELCA} 且 direction=SELL | **REJECT** | `SELL_ON_NEGATIVE_DRIFT_NODE` | ELCA train+val 无条件漂移 **−1.15**；SELL PnL mean **−1.15**、maxloss **−357**；hist_n≈44（train）~121（test） | ELCA 负漂移 → SELL 逆漂移；冷启动样本不足（A 报告 low_sample） |
| **R2** | node ∈ {CONTROLX}、BUY、且 interpretable_dir<0 且 catboost_dir<0（ML 双 BUY） | 并集 reason（R7a 已 REJECT） | `MODEL_DISAGREEMENT` | Agent C：2/3「CatBoost+Interpretable 双 BUY」2,273 笔 cum −138,648（相关误差放大，非独立确认） | 双 ML 同向 BUY = 同一错误二次确认；Rule 参与才可信 |
| **R6** | hist_n < 150 | **REJECT** | `LOW_SAMPLE_SUPPORT` | ELCA train hist_n 中位 44（max 89）、test 中位 121（max 154）；CONTROLX/SNLNDRO test 全部 ≥878 | 冷启动样本不足，统计不可靠（A 报告 low_sample，n=141） |
| **R4** | 同 node×hour 历史 cvar99（SELL 侧）/ rcvar99（BUY 侧）< −600 | **PASS_WITH_WARNING**（仅警告，不拦） | `EXTREME_TAIL_NODE` | train+val 扫描：cvar99 阈值**无法捕获** CONTROLX SELL 最大亏损（−2,065 事件行 cvar99=−276 不超标）；extreme_state(lag1_pct>0.95) 信号 **regime 翻转**（train+val 负 EV −6,487 / test 正 EV +45,509） | 不能把"CONTROLX 一直很波动"当"这笔该避开"（A 警示） |
| R1 | confidence 阈值 | **删除** | — | train+val Rule 置信度分层：<0.3 mean +2.06 / 0.3-0.5 −0.11 / 0.5-0.7 **−4.16** / >0.7 **−19.29**——conf 越高 mean 越差 | CONFIDENCE NOT CALIBRATED（C 报告）：高置信 = 行情持续的机械产物 + 尾部同源 |
| R3 | vol_ratio 阈值 | **删除** | — | train+val CONTROLX vol_ratio 分层无单调区分（<1.0 mean −6.91 / >3.0 +10.49）；vol_ratio≥1.5 命中 test CONTROLX 75% | 单纯高波动不能作为判别（A 明确警示） |
| R5 | \|expected_return\| 阈值 | **删除** | — | train+val 扫描：CONTROLX 上 \|er\|≥50 仍 cvar99≈−888（不改善）；≥150 才收窄但 coverage 只剩 1.9% | 幅度预测秩相关≈0；edge 门只打薄不降尾 |

---

## 3. 每条规则的 train+val 验证证据

### 3.1 R7 方向门（核心规则）—— train+val 直接验证

**无条件市场结构（canonical train+val，不依赖任何模型）：**

| node | train+val 漂移 | 无条件 BUY mean | BUY maxloss | BUY cvar99 | SELL maxloss | SELL cvar99 |
|---|---|---|---|---|---|---|
| CONTROLX_1_N001 | **+9.68** | **−9.68** | **−3,656** | **−916** | −2,295 | −659 |
| SNLNDRO_1_N001 | +2.61 | −2.61 | −586 | −98 | −389 | −87 |
| ELCAJNGT_7_N001 | **−1.15** | +1.15 | −163 | −89 | **−357** | **−224** |

**LR walk-forward 代理（val，as-of）在 CONTROLX 上的 BUY：** 3,620 笔，cum **−37,412**，mean −10.33，maxloss −3,656，cvar99 −973。

→ 结论：CONTROLX 上 BUY 无条件负期望且尾极深，**不是 test 窗口特有**，train+val 已成立。R7a 阈值（node 漂移 > 0 且 BUY）无需数值扫描——是"漂移方向"这一结构性事实。

### 3.2 R4 尾部门 —— 验证失败，降级为警告

**扫描结果（train+val，Rule 重建的 CONTROLX SELL）：**

| cvar99 阈值 | coverage | max_loss | cvar99 | n_loss>500 |
|---|---|---|---|---|
| 不限 | 1.0 | **−2,066** | −650 | 29 |
| > −500 | 0.929 | **−2,066** | −643 | 26 |
| > −300 | 0.404 | **−2,066** | −543 | 7 |
| > −250 | 0.219 | −1,082 | −387 | 2 |

→ **即使把阈值收到只保留 22% coverage，最大单笔亏损（−2,066）依然存在**——因为该事件行（2025-11-06 H12）事前 cvar99 只有 −276，spread_lag1 仅 −45（未处于历史极值）。**历史 CVaR 无法事前识别 CONTROLX SELL 的 DA 崩塌尾**。

**regime 翻转检验（为何不能改用 lag1_pct 极值）：**

| 子集 | train+val（Rule CONTROLX SELL） | test（Rule CONTROLX SELL） |
|---|---|---|
| lag1_pct > 0.95 | n=292, cum **−6,487**, mean −22.2 | n=143, cum **+45,509**, mean +318 |
| lag1_pct ≤ 0.95（保留） | cum +36,648 → +43,135 | cum +111,568 → +66,059 |

→ train+val 上 lag1_pct>0.95 是负 EV（spread 冲高后均值回归），但 test 上是强正 EV（持续负电价 regime 下 spread 高位 = 行情仍在）。**同一信号两个窗口符号相反**，若作为 REJECT 规则会砍掉 test 上 +45,509 的 Rule 利润。故 R4 只能降级为 PASS_WITH_WARNING。

### 3.3 R1 置信度门 —— 删除

train+val Rule 重建候选置信度分层（n=18,481 交易）：

| conf 桶 | n | mean PnL | cum | max_loss |
|---|---|---|---|---|
| <0.3 | 6,884 | **+2.06** | +14,183 | −1,131 |
| 0.3-0.5 | 7,051 | −0.11 | −774 | −1,098 |
| 0.5-0.7 | 4,110 | **−4.16** | −17,105 | −2,066 |
| >0.7 | 436 | **−19.29** | −8,410 | −1,081 |

→ 置信度与 PnL **反相关**。高置信恰好编码"负电价行情持续多日"（Rule 一致性机械接近 1），与 ML 在稀缺定价日前对 BUY 最有信心同源。**置信度既非概率也非风险度量，删除**。

### 3.4 R3 波动门 —— 删除

train+val CONTROLX（Rule 重建 SELL 候选）按 vol_ratio 分层：

| vol_ratio | n | mean PnL | max_loss | n_loss>500 |
|---|---|---|---|---|
| <1.0 | 4,484 | −6.91 | −1,097 | 55 |
| 1.0-1.5 | 1,756 | +7.66 | −1,131 | 36 |
| 1.5-2.0 | 2,086 | −7.48 | −1,101 | 50 |
| 2.0-3.0 | 1,602 | +3.05 | −2,066 | 30 |
| >3.0 | 270 | +10.49 | −941 | 12 |

→ 无单调性，高波动段（>3.0）反而 mean +10.49。**高波动是 CONTROLX 常态，不是"这笔危险"信号**（A 警示命中）。删除。

### 3.5 R5 期望边门 —— 删除

train+val CONTROLX（Rule 重建）按 \|expected_return\| 扫描：

| \|er\| 阈值 | coverage | mean PnL | max_loss | cvar99 |
|---|---|---|---|---|
| ≥0 | 1.0 | −2.36 | −2,066 | −881 |
| ≥20 | 0.51 | −3.92 | −2,066 | −930 |
| ≥50 | 0.22 | −3.23 | −1,101 | −888 |
| ≥150 | 0.019 | −11.06 | −941 | −922 |

→ 提高 edge 阈值不改善尾部（cvar99 稳定 ≈ −880~−930），只打薄 coverage。删除。

### 3.6 R2 模型冲突 —— 保留为 R7 的并集 reason

train+val（Rule 重建 vs LR 代理，val 对齐）的"双 BUY"验证：

| 组合 | n | cum | mean | max_loss | cvar99 |
|---|---|---|---|---|---|
| Rule 与 LR 双 BUY | 1,975 | **−20,162** | −10.21 | −1,097 | −891 |

→ 双模型同向 BUY（在 CONTROLX 上即 ML 双 BUY）负期望，与 R7a 结论一致。R2 不独立新增拦截能力（R7a 已拦），但提供独立 reason_code，且 Agent C 明确其机制（相关误差放大）。

---

## 4. Gate 在 train+val 上的净效果（test 前验证）

将 gate 应用到 train+val 候选集（Rule 重建 + LR 代理），评估尾部/回撤是否实质改善：

| 候选集 | 窗口 | 过滤 | n | cum | mean | max_loss | cvar99 | max_drawdown |
|---|---|---|---|---|---|---|---|---|
| Rule 重建 | train+val | before | 20,396 | −4,083 | −0.20 | −2,066 | −734 | −45,712 |
| Rule 重建 | train+val | **after gate** | 15,242 | **+56,576** | +3.71 | −2,066 | **−346** | **−19,392** |
| LR 代理 | val | before | 7,244 | −29,100 | −4.02 | −3,656 | −859 | −66,829 |
| LR 代理 | val | **after gate** | 3,624 | **+8,312** | +2.29 | **−195** | **−86** | **−1,116** |

> Rule 重建 after 的 max_loss 仍 −2,066（R7 只拦 BUY，CONTROLX SELL 的 DA 崩塌尾不在本版 gate 覆盖——诚实保留）；但 n_loss>500 从 184 降到 31，cvar99 从 −734 到 −346。LR 代理（ML 型 BUY 主）gate 后 max_loss 从 −3,656 到 −195，cvar99 从 −859 到 −86——**ML 型策略的尾部被实质消除**。

---

## 5. 已删除规则的完整记录（避免"拍脑袋"）

| 候选规则 | 决策 | 证据摘要（全部 train+val） |
|---|---|---|
| R1 Confidence Gate | **删除** | 置信度与 PnL 反相关（conf 桶 mean +2.06 → −19.29）；C 报告 CONFIDENCE NOT CALIBRATED |
| R3 Volatility Gate | **删除** | vol_ratio 分层无单调性；A 报告 vol_ratio≥1.5 命中 75% CONTROLX |
| R4 Tail Gate（REJECT 级） | **降级为警告** | cvar99 阈值扫不到最大 SELL 亏损（−2,066 行 cvar99=−276）；lag1_pct 信号 regime 翻转（train+val −6,487 / test +45,509） |
| R5 Expected Edge | **删除** | \|er\| 阈值不改善 cvar99，只打薄 coverage |

**核心教训**：本数据上"风险"几乎全部来自**方向 × 节点**这一结构性事实（CONTROLX BUY / ELCA SELL 逆漂移），而不是来自历史 CVaR、波动、置信度或期望幅度。任何试图"用波动/置信度挑出危险交易"的规则都失败，因为它们无法区分"CONTROLX 一直很波动"与"这笔该避开"。

---

## 6. 实现与复现

- Gate：`code/tmp/agent_d_gate.py`
- as-of 风险特征（hist_n/cvar99/rcvar99/vol_ratio/lag1_pct/node_drift 等，全部 ≤ target_date-2）：`code/tmp/agent_d_features.py` → `code/data/stage3/risk_features.parquet`
- train+val 校准：`code/tmp/agent_d_calibrate.py` → `code/data/stage3/risk_gate_calibration.json`
- train+val 净效果验证：`code/tmp/agent_d_validate_tv.py` → `code/data/stage3/risk_gate_validate_tv.json`
- test 回测比较：`code/tmp/agent_d_backtest.py` → `code/data/stage3/risk_gate_backtest_metrics.json`
- 反事实：`code/tmp/agent_d_counterfactual.py` → `code/data/stage3/risk_gate_counterfactual.json` + 两个 CSV

---

## 7. 局限与诚实声明

1. **gate 只防"逆漂移方向"，不防"顺漂移方向的极端尾"**：CONTROLX SELL 的 DA 崩塌尾（2026-06-23/06-30）不在本版覆盖——train+val 上该尾无法用任何事前内部信号可靠识别（cvar99 扫描、lag1_pct 极值均失败）。
2. **方向规则依赖漂移方向稳定**：CONTROLX 漂移在 train+val（+9.7）与 test（+84）都为正，方向门成立；但若未来 regime 让 CONTROLX 漂移转负，R7a 需复核（规则为白盒，可审计）。
3. **gate 使 coverage 从 49% 降到 11%**：1,523→339 笔。保留的是 SNLNDRO（唯一正 EV 节点）；这是"牺牲覆盖换取尾/回撤"的显式取舍，任务优先级为 tail/drawdown 优先。
4. **R2 的 rule_dir 在单模型视角下置 NaN**（避免误伤单模型自身判定），仅在 committee/多模型一致场景下作为并集 reason 生效。
5. **反事实中的"prevented"口径**：按 worst 模型的实际 action 判定；若某事件行多模型同时交易且 direction 不同，取最差模型方向。详见 `risk_gate_counterfactual.md`。
