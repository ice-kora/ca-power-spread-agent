# CAISO 价差交易 · MVP Demo 使用说明（非技术人员可读）

> 文件：`mvp_demo.py`（入口脚本）｜ 配套：`mvp_readme.md`（本文档）
> 系统归属：V0.2 白盒交易决策流水线（Agent D · MVP Demo 工程师）

> **注意（V0.3.1）**：现在推荐使用浏览器版 **Web + LLM Agent MVP**（`python mvp_web.py` →
> http://127.0.0.1:5000），功能与本文档所述一致并新增 Ask Trading Agent（自然语言追问 +
> Agent Trace）与每日简报。启动方式见 `README.md`。本文档仍适用于命令行版 `mvp_demo.py`。

---

## 0. 一句话介绍

这是一个 **加州电力市场（CAISO）日前与实时价差交易的决策演示系统**。

它要回答一个交易员每天都在问的问题：

> "明天这一小时，日前价格（DA）和实时价格（RTPD）哪个更高？我该不该提前锁定一个方向，赚取它们的价差？"

系统会像一位"守规矩的交易助理"一样，把"该不该交易、为什么"一步步讲清楚，并且**严格只用决策时刻之前已经发生的信息**，绝不"偷看未来"。

> ⚠ **重要诚实声明（请先读）**
> - **MODEL SIGNAL IS EXPERIMENTAL / CURRENT ALPHA = WEAK** —— 模型信号是实验性的，当前能力很弱。
> - **MVP ≠ 已验证盈利系统** —— 这是演示系统，不是能保证赚钱的交易软件。
> - 本 Demo **不编造数据**：所有特征、证据、历史案例都是真实数据，且经过"时点合规"（as-of）检查。

---

## 1. 系统是什么（交易逻辑）

加州电力市场里，"电力"分两种方式买卖：

| 名称 | 含义 | 什么时候定价 |
|---|---|---|
| **DA（Day-Ahead）** | 提前一天约好的价格 | 每天 10:00 前报价，约 13:00 出结果 |
| **RTPD（Real-Time）** | 当天实际交割时的价格 | 实时逐小时结算 |

两者经常不一样，差出来的就是**价差 Return = DA − RTPD**。

系统的交易方式是"虚拟交易"（Convergence Bidding）：
- 如果判断 **Return 会大于 0**（DA 高于 RTPD）→ 建议 **卖出 DA（SELL_DA）**，赚 `DA − RTPD`。
- 如果判断 **Return 会小于 0**（DA 低于 RTPD）→ 建议 **买入 DA（BUY_DA）**，赚 `RTPD − DA`。
- 判断不出 → **不交易（NO_TRADE）**。

每笔按 **1 兆瓦时（1 MWh）** 算盈亏，单位是 **美元/兆瓦时（$/MWh）**。

---

## 2. 怎么启动

环境：已经装好 Python 3、pandas、numpy、pyarrow，并安装了 CatBoost / LightGBM（模型输出已预生成，运行 Demo 不需要再训练）。

在仓库根目录（本文件所在目录）打开命令行，运行：

```bash
python mvp_demo.py
```

这一行就会用**默认参数**（节点 CONTROLX_1_N001、决策日 2026-07-08、目标小时 H2）跑完整个决策闭环。

想自己选一笔交易：

```bash
python mvp_demo.py --decision-date 2026-07-16 --node CONTROLX_1_N001 --hour 3
python mvp_demo.py --node SNLNDRO_1_N001 --decision-date 2026-07-08 --hour 15
python mvp_demo.py --list-rows              # 看看有哪些可选的日期/节点/小时
python mvp_demo.py --auto-reveal            # 跳过"按 Enter"，自动揭晓事后结果
python mvp_demo.py --json-out 我的卡片.json # 把本次决策的完整审计记录存成文件
```

参数说明：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--decision-date` | 决策日期 D（当天 10:00 前做决定） | `2026-07-08` |
| `--node` | 交易哪个节点 | `CONTROLX_1_N001` |
| `--hour` | 交易目标日哪一小时（H1~H24） | `2` |
| `--auto-reveal` | 不等待按键，直接显示事后结果 | 关 |
| `--json-out` | 保存审计 JSON 文件的路径 | 不保存 |
| `--list-rows` | 打印可选交易清单 | — |

> 合法决策日：`2026-06-01 ~ 2026-08-04`（对应预测窗口的目标日 `2026-06-02 ~ 2026-08-05`）。
> 三个可用节点：`CONTROLX_1_N001`、`SNLNDRO_1_N001`（ZP26 区域）、`ELCAJNGT_7_N001`（SP15 区域，冷启动节点）。

---

## 3. Demo 怎么操作（一份完整报告长什么样）

运行后会打印一份"决策报告"，从上到下分 7 大块，**顺序就是真实业务顺序**：

### ① 决策上下文（Decision Context）
告诉你这笔决策的"时间盒子"：
- 决策日、**决策截止时间 = 当天 10:00 前**（美国加州时间 PT，这是电力市场官方规定的报价截止）。
- 目标日 = 决策日 + 1；目标小时 = 你要交易的那一小时。
- 节点、市场规则版本。
- 金句横幅：**"AVAILABLE INFORMATION ONLY AS OF 10:00 PT"** —— 10 点以后的新信息一律不准进决策。

### ② 决策时点可见的数据（Available Data）
只展示对决策最关键的十几项（不是全部），每一项都有 **值 / 来源 / 什么时候能拿到 / 是否合规**：
- 历史价差（昨天 DA−RTPD）、7 天滚动价差、昨日 DA、昨日 RTPD、负荷预测、昨日实际负荷、天气滞后、节点联动（拥堵）等。

### ③ 预测模型（Predictive Model）
模型的输出，**只报预测、不下单指令**（下单是后面规则引擎的事）：
- `expected_return`：预期价差大小。
- `prob_positive / prob_negative`：价差为正/为负的概率。
- `direction_probability`：模型押注方向的概率。
- `model_signal_strength`：模型信号强度（注意：**它不是概率，不是置信度**，只是方向概率和幅度确定的组合量，系统诚实标注）。
- `uncertainty`：不确定度（越大越没把握）。
- 最可能解释这次判断的 Top 5 特征（用"相对历史有多异常"的 z-score 统计量展示，**不是黑盒 SHAP**，诚实标注）。

### ④ 外部证据（Agent Evidence）
系统会去取"决策时刻前真实公开"的信息（当前接入的是 NCEP GFS 天气预报历史档案）：
- 每一条证据都会显示：来源 / 事件类型 / 摘要 / 发布时间 / 是否合规。
- 如果 10:00 前没有任何可用证据 → 显示 `NO ELIGIBLE EXTERNAL EVIDENCE`（不会编造）。
- **重点演示"不穿越"**：系统会抓一条"18:00 才发布的天气预报"（真实数据，但发布晚于 10:00 截止），把它放进 **POST-DECISION / NOT USED（晚于截止 → 只进复盘，绝不影响决策）** 栏。这解释了为什么系统不会偷看未来。

### ⑤ 历史相似案例（Similar Historical Cases）
从案例库检索历史上**结果已经结算**、且**决策时刻之前就能看到**的相似交易（同节点、接近的小时）：
- 只显示 Top 3，每条都有：日期 / 节点 / 小时 / 当时信号 / 当时决策 / 事后结果 / 盈亏 / 教训。
- 硬门槛：`case_available_at <= 决策时刻`，保证不会把"还没发生的案例"拿来做决策（防穿越）。

### ⑥ 风控闸门（Risk Gate）
对这笔候选交易做"能不能入场"的白盒裁决，输出 **PASS（放行）/ WARNING（放行但提示）/ REJECT（拒绝）** + 原因编号（reason_code），并用业务语言解释：
- 例如 `BUY_ON_POSITIVE_DRIFT_NODE`："这个节点历史上 DA 一直高于 RTPD，做多长期亏钱，拒绝。"
- 例如 `LOW_SAMPLE_SUPPORT`："这个节点历史样本太少（冷启动），统计不可靠，拒绝。"

### ⑦ 最终建议（Final Recommendation）
规则引擎把模型 + 风控 + 证据组合成最终动作：**BUY_DA / SELL_DA / NO_TRADE**。
并且分块讲"为什么"：**Model / Historical / Evidence / RiskGate / RuleEngine** 各一行。

### ⑧ 事后复盘（Post-trade Review）—— 先锁决策，才揭晓
系统先**锁定** ⑦ 的决策，然后才显示真实结果：
- 真实 DA、真实 RTPD、真实 Return。
- 这笔交易的 PnL（1 MWh 口径；NO_TRADE 恒为 0）。
- 模型预测误差 = 真实 Return − 预期 Return。
- 方向判对了吗？这笔赚了吗？
- 复盘分类（诚实、多标签），例如：
  - `RISK_GATE_SUCCESS`：闸门拒绝的交易事后证明会亏（闸门帮你躲过亏损）。
  - `NORMAL_PROFIT / MODEL_DIRECTION_CORRECT`：做对方向赚了。
  - `MODEL_ERROR`：信号很强却判错方向（模型/特征侧问题）。
  - `UNFORESEEABLE_EVENT`：存在决策时点后才有的事件信息，属不可预见。

### ⑨ 审计面板（Audit Panel）
最底部一栏"体检报告"：
- 数据泄露检查：PASS（不穿越）。
- 是否用了 MOCK 数据：NONE（决策路径全真实）。
- Backtest-safe 特征：38/38。
- 证据时间门槛：几条合规 / 几条被隔离。
- 决策截止 = 10:00 PT；市场规则版本；模型 / 规则引擎 / 风控闸门 / 时间门槛 / 案例库版本。

---

## 4. 每块内容是什么意思（速查表）

| 屏幕上的英文 | 中文意思 | 重要吗 |
|---|---|---|
| Decision Cutoff | 决策截止（每天 10:00 加州时间） | 系统的"时间红线" |
| as-of / decision_eligible | "截至该时点可获得 / 是否合规可用于决策" | 防穿越的核心概念 |
| POST-DECISION / NOT USED | 晚于截止的信息，只进复盘 | 说明系统不偷看未来 |
| expected_return | 预期价差（DA−RTPD） | 决定方向 |
| model_signal_strength | 模型信号强度（非概率） | 仅供参考，不能当置信度 |
| uncertainty | 不确定度 | 越大越没把握 |
| Risk Gate REJECT | 风控拒绝入场 | 一票否决 |
| BUY_DA / SELL_DA / NO_TRADE | 买入日前 / 卖出日前 / 不交易 | 最终动作 |
| PnL | 盈亏（$ / MWh，1 MWh 仓位） | 事后结算 |
| reason_code | 规则命中的编号 | 审计用 |

---

## 5. MVP 简化（诚实说明：这个版本简化了哪些）

1. **模型信号弱**：`CURRENT ALPHA = WEAK`。模型的幅度预测与真实幅度相关度很低（秩相关≈0.06），
   方向概率也只有很弱的区分度。**不要拿它当赚钱信号**。
2. **没有真实的价格方向判断**：所有外部证据方向一律标 `UNCERTAIN`（宁可未知，不可乱判）。
   当前唯一接的真实数据源是 GFS 天气预报（Open-Meteo 历史档案），它只报天气，不直接说价差方向。
3. **天气是"历史滞后"而不是"当时预报"**：模型用的是昨天/前天的实际天气（`t2m_lag1` 等），
   不是决策时刻能拿到的天气预报档案（真实 as-of 天气预报归档尚未接入）。
4. **风险闸门是"护栏"不是"发动机"**：它能把系统性亏损的交易拦掉，但**不能让弱信号变成强信号**。
5. **没有真实撮合**：PnL 是"信号层面"的估算（1 MWh 仓位），不含报价、成交、结算、手续费。
6. **Case Library 全部来自 test 窗口**：对窗口早期的决策几乎不触发；越往后案例越多。
7. **ELCA 节点**是冷启动：样本不足，风控闸门基本全部拒绝（裁决是"不交易"而非"交易得更聪明"）。

## 6. 未来 Production（上线前还需要什么）

| 缺口 | 说明 |
|---|---|
| 真实 as-of 天气预报归档 | 接入决策时刻可获得的 D+1 天气预报，替换"历史滞后" |
| 真实外部事件数据源 | 可再生能源出力、负荷预测修正、机组停运/阻塞、CAISO 公告 |
| 概率 / 尾部校准 | 用分位数/EVT 校准替换当前未校准的 `model_signal_strength` |
| 更强的方向信号 | 当前模型 alpha 弱，需要特征/模型侧改进 |
| 真实撮合与成本 | 报价、成交、结算、费用，仓位优化 |
| 人工确认环节 | 交易员拍板（Human Confirmation）后才会真实执行 |

---

## 7. 如何验证这个 Demo 是"不穿越"的

每次运行，你可以盯着三个防线：
1. **Section 1 的横幅**：AVAILABLE INFORMATION ONLY AS OF 10:00 PT。
2. **Section 4 的 POST-DECISION / NOT USED**：18:00 发布的真实天气预报被隔离，不进决策。
3. **Section 5 的检索门槛**：case_available_at ≤ 决策时刻。
4. **Post-trade 揭晓**：真实 DA / RTPD / Return 只在决策锁定后才显示，从不出现在前面任何一节。

如果这三条防线都成立，这次决策就是"站在过去某一天的上午 10 点，只用当时能看到的信息"做出来的。

---

## 8. 快速自检清单

- [ ] `python mvp_demo.py` 能跑通并打印 7 大节 + 复盘 + 审计面板。
- [ ] 默认示例（CONTROLX · 2026-07-08 决策 · H2）输出 `NO_TRADE`（被风控拒绝），事后复盘为 `RISK_GATE_SUCCESS`。
- [ ] 把 `--decision-date` 换成 `2026-07-16 --hour 3`，会输出 `SELL_DA`，事后复盘为盈利 + `NORMAL_PROFIT`。
- [ ] `--json-out` 生成的 JSON 文件包含全部 10 个顶层字段（meta/decision_context/.../audit）。
- [ ] 审计面板显示 `Data Leakage Check: PASS`、`Mock Data Used: NONE`。
