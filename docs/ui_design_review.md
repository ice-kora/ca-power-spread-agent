# V0.4 UI Design Review — CAISO 交易决策助手

> 2026-08-09 ｜ Lead 汇总 5 个并行设计 Agent（A 信息架构 / B 视觉 / C 业务语言 / D Agent UX / E 演示故事）
> 阶段：Product Design Sprint · Phase 1（DESIGN ONLY，CODE LATER）。**未改任何生产代码，交易核心冻结。**
> 原型：`design_prototypes/`（静态演示值）· 截图：`design_shots/`（1440×900）

---

## A. 当前 UI 的 10 个最大问题（Pass 1 Audit）

1. **首屏被 4 层 chrome 占据**（header → sys-strip → controls → 才到 Hero），1440×900 下 Hero 起始 y≈230-260px，建议/价差/风控不在第一眼焦点。
2. **Hero 直接渲染原始 code pill**（`SELL_DA`），主视觉位出现技术字段名。
3. **"为什么"三卡、数据清单、案例卡全是等尺寸 border+shadow tile** → SaaS bento 味、层级平。
4. **Evidence Time Gate 只是小红框**（`.tg-box`），功能对但不够"memorable"，签名组件被淹没。
5. **右栏 360px sticky Agent 与主栏竞争焦点**，主栏仅 ~1060px。
6. **Hero 大数字未统一 tabular-nums**，数字不对齐（交易终端纪律缺失）。
7. **系统状态条平铺 5 项**（Agent/数据/证据/自动交易/模型），密度高、层级无主次。
8. **"市场规则版本"等元数据混入 Hero 头部**，稀释建议叙事。
9. **Daily Brief 与主决策抢视觉空间**。
10. **页面过长**：Final Recommendation / Risk 需多屏滚动才能看到，用户难以形成完整交易故事。

## B. 三套 Visual Concept

| Concept | 定位 | 色彩 | 布局 | 优势 | 风险 |
|---|---|---|---|---|---|
| **A 交易终端 Dark Terminal** | 给专家的数据终端，Bloomberg 工作面 | 深冷 `#0b0e14` + hairline + mono | 12 列 + 320px Agent 右栏 | 天然"专业交易终端"、技术层融入 | 投影/打印不友好，需刻意留白 |
| **B 白皮报告 Editorial Memo** | 给业务读者的决策备忘，金融日刊 | 暖纸白 `#fafaf7` + 衬线标题 + 页边注 | 居中 860px 单栏，无右栏 | 编辑清晰、审计信任感 | 非"交易台"，大数字冲击力靠字号撑 |
| **C 叙事控制塔 Narrative Control Tower** | 给演示者的舞台，把故事线当结构 | 浅中性 `#f6f7f9` + 单强调蓝 | 全宽 Hero 舞台 + 折叠选择抽屉 + Agent 内联 | 唯一把 §22 故事线当页面结构的方案 | 需克制防营销页味 |

（三套共用语义色：绿 PASS / 红 REJECT / 黄 WARNING / 蓝 info；方向色 BUY 蓝 / SELL 红 / NO_TRADE 灰，与状态色分离。）

## C. 推荐 Concept（Lead 决策）

**推荐：Concept C「叙事控制塔」为主**，吸收 B 的编辑排版纪律（留白、编号段落、页边注替代等尺寸卡）+ A 的数字纪律（全数字 tabular mono、技术详情层=终端密度）。

## D. 推荐理由

1. **它把演示故事线当页面结构**（Hero → 为什么 → 数据依据 → Time Gate 聚光灯 → 案例 → Lock → Reveal → 复盘），让"给面试官/业务 10 秒讲故事"成为默认体验，而非事后翻译。
2. **Time Gate 与 Lock→Reveal 天然成幕**：这两个是产品最独特的签名能力，C 用"中央时间轴聚光灯"和"舞台转场"把它们变成视觉高潮。
3. **Agent 内联而非外挂**：Agent 在主栏下方作为"交易系统操作台"，与决策同流，强化"Agent 在操作系统"。
4. **浅色克制** 更贴合"业务演示 + 风险决策产品"（非 AI landing page），投影友好。
5. 符合技能契约 §17「restrained professional trading workstation + editorial clarity」与 §22 演示成功测试逐条。

## E. 最终 Information Architecture（Agent A）

```
CAISO 交易决策助手
├─ 全局：极薄 header（产品名 + 3 项系统状态单行 + 边界/来源/原理链接）
├─ A 选择交易（单行紧凑控件条 / 折叠抽屉）
├─ B Recommendation Hero（首屏唯一视觉中心）
├─ C 为什么（三业务卡：模型/风控/规则）
├─ D 风险细节（折叠）
├─ E 本次决策依据（数据清单 + 血缘折叠）
├─ F 外部信息：允许 / 拒绝（Evidence Time Gate 签名组件）
├─ G 类似历史案例（卡片）
├─ H 锁定 → 揭晓
├─ I 实际结果（DA/RT/DA−RT/PnL）
├─ J 复盘（发生了什么/为什么/偷看未来/教训）
└─ K 技术详情（全折叠：模型原始值/血缘表/审计/工具trace/版本/Decision ID）
```

**首屏 5 个最重要信息**（视觉权重降序）：
1. **Final Recommendation**（卖出日前 SELL DA）— 最大字号/色块
2. **Expected Spread**（预计 DA−RT +$59.79/MWh）— 第二大数字
3. **Risk Status**（风险检查：通过/谨慎/拒绝）— 语义色
4. **Signal Strength**（模型信号：偏弱）
5. **Decision Cutoff**（报价截止 10:00 PT）— 无穿越叙事的锚点

Version / Schema / Data Mode / Decision ID → 全部移入技术详情与"系统边界"。

**主流程状态机**：`INITIAL`（空态引导）→ `DECISION_READY`（Hero 全量 + Lock CTA）→ `LOCKED`（已锁定章 + Reveal 激活）→ `REVEALED`（结果块 + 复盘四问）。

## F. 首页 Wireframe（INITIAL）

```
┌──────────────────────────────────────────────────────────────┐
│  CAISO 交易决策助手 [Trading Decision Agent]   ●Agent已连接·DEMO·自动关闭 │
│  ───────────────────────────────────────────────────────────────
│  [日期][节点][小时][演示案例]          [生成交易建议]                     │
│  ┌────────────────────────────────────────────────────────────┐
│  │  选择一笔交易，查看系统建议                                  │
│  │  设定日期/节点/小时，或选演示案例 → 生成建议                 │
│  └────────────────────────────────────────────────────────────┘
│  问交易 Agent（先运行一笔决策）[胶囊问题 置灰] [输入框 置灰]         │
└──────────────────────────────────────────────────────────────┘
```

## G. Decision Wireframe（DECISION_READY，首屏）

```
│  CONTROLX · 次日 H3 · 报价截止 10:00 PT       卖出日前 SELL DA    │
│  [预计 DA−RT +$59.79/MWh][风险 通过][信号 偏弱][证据 1条]        │
│  系统预计日前价格高于实时价格…因此建议卖出日前。                   │
│  [锁定本次决策] [为什么这样建议？]  ← 锁定 CTA 嵌入 Hero          │
│  ── ① 为什么（模型宽卡 / 风控高亮窄卡 / 规则卡）─────────────────
│  ── ② 本次决策依据（数据清单 + 血缘折叠）────────────────────────
│  ── ③ 类似历史案例（卡片）─────────────────────────────────────
│  审计：PASS · 6 通过 1 提醒 [查看详情]（默认折叠）                 │
```

## H. Agent Wireframe（Agent D）

```
│  问交易 Agent  [CONTROLX·H3·建议 SELL DA]（操作对象 chip 常驻）  │
│  [为什么建议SELL][为什么不是BUY][最大风险][哪些被拒][用了哪些数据][类似案例][为什么亏(揭晓后)]
│  [输入框 问这笔交易…] [发送]                                     │
│  Agent 正在分析当前决策…                                        │
│  ✓ 读取当前交易建议     0.12s · SELL DA                         │
│  ✓ 检查模型判断         0.05s · 偏弱                            │
│  ✓ 检查风险条件         0.02s · 通过                            │
│  ✓ 查询外部证据         0.10s · 1条可用                         │
│  ○ 生成解释…[光标]                                              │
│  ▍这笔交易建议卖出日前，主要有三个原因：① 预计 DA−RT +$59.79…    │
│  [查看技术 Trace]（折叠：tool/args/result/duration/guard）      │
```

**4 种问题状态轨迹**（Agent D）：A 为什么 SELL → get_decision+feature；B 最大风险 → decision 风控段+similar_cases；C 哪些被拒 → get_evidence；D 为什么亏（Reveal 后）→ get_post_trade_review。未 Reveal 问 D → `✕ 未揭晓（拒绝访问）`。Guard 拦截 → `✕ 一致性检查未通过` + 红卡提示，不展示被拦截内容。无 private CoT。

## I. Case E Wireframe（Time Gate 中央聚光灯）

```
│  ┌ 外部信息 ──────────────────────────────────────────────────┐
│  │  ✓ 本次允许  GFS 06Z（截止前可获得）                        │
│  │  ✕ 被拒绝   GFS 18Z  [NOT USED 未参与决策]                 │
│  │      报价截止 10:00 PT · 该预报开始生成 11:00 PT · 实际可用时间：无法证明
│  │  ┌───────────时间轴───────────┐
│  │  │ ✓GFS06Z    │报价截止 10:00 PT│    ✕GFS18Z 来晚了│
│  │  └──────────────────────────┘
│  │  ▍它在报价截止以后才开始生成，因此当时不可能被交易员获得。      │
│  │  [查看技术详情]（reason_code / UTC / available_at / estimate）│
│  └──────────────────────────────────────────────────────────────┘
│  审计：数据来源 PASS · 可用时刻未证明（如实提醒）· 时间门 PASS
```

## J. Reveal Wireframe（REVEALED）

```
│  [🔒 决策已锁定]  [揭晓真实结果]                                 │
│  ┌ 实际结果（loss 红卡，不隐藏）──────────────┐
│  │  本次方向判断错误  [DA][RT][DA−RT][PnL]   │
│  │  实际价差与建议方向相反，本次卖出日前亏损。 │
│  └──────────────────────────────────────────┘
│  复盘：发生了什么 / 为什么 / 有没有偷看未来（没有，晚于截止都被拒绝）✓ / 教训
│  [查看结构化标签]（taxonomy，折叠）
│  问交易 Agent（已揭晓 · 亏损）→ ✓查询事后复盘 → 回答
```

## K. Business Language Dictionary（Agent C 摘要）

| 技术字段 | 业务中文 | | 技术字段 | 业务中文 |
|---|---|---|---|---|
| `expected_return` | 预计 DA−RT 价差 | | `risk_gate.decision` | 风险检查：通过/提醒/拒绝 |
| `direction_probability` | 方向倾向 | | `decision_eligible=false` | 未参与决策 |
| `model_signal_strength` | 模型信号强度（偏弱/中等/偏强） | | `INITIALIZATION_AFTER_CUTOFF` | 该信息在报价截止后才开始生成 |
| `uncertainty` | 不确定性（低/中/高） | | `AVAILABILITY_NOT_PROVEN` | 无法证明当时已经可以获得 |
| `SELL_DA/BUY_DA/NO_TRADE` | 卖出日前/买入日前/不交易 | | `STRUCTURAL_LAG` | 历史滞后特征（最晚可证上界） |

**主页面英文白名单**：DA / RT / BUY DA / SELL DA / NO TRADE / Risk Gate / Agent / PnL / GFS（信息源名）。其余技术字段只进 Technical Details。

**数值规范**：金额 2 位小数 + $/MWh（`+$59.79/MWh`）、概率百分比 1 位小数、信号强度分档（≥0.5 偏强 / ≥0.2 中等 / 其余偏弱）、时间 `10:00 PT`、不可证明 → `无法证明`。

## L. Design Tokens（Concept C 主轴）

```
canvas #f6f7f9 · surface #ffffff · surface-2 #eef1f6 · border #e2e6ee
text #1a2233 · dim #667085 · faint #98a2b3 · accent #2563eb
ok #16a34a/ok-bg #e9f7ef · warn #b45309/warn-bg #fdf3e3 · err #dc2626/err-bg #fdecec
buy #0ea5e9/buy-bg #e6f4fe · sell #ef4444/sell-bg #fdecec · hold #6b7280/hold-bg #eef0f3
圆角 8/12/16/20 · 间距 4/8/16/24/32/40 · 阴影仅 Hero（0 1px 3px + 0 8px 24px rgba(16,24,40,.06)）
数字全 tabular-nums mono · 中文栈 PingFang/Microsoft YaHei
动效：流式光标 / 截止线划出 0.5s / Reveal 0.4s 背景过渡 / prefers-reduced-motion 全关
```

## M. 删除的旧 UI 元素

- 大块 **MVP STATUS** 面板（sys-strip 平铺 5 项）→ 3 项单行小条
- **Hero 上的 `SELL_DA` code pill** 与"市场规则版本"元数据
- **等尺寸 border+shadow 三卡 / 数据卡** → 编辑排版（编号段落、hairline、高亮窄卡）
- **Daily Brief 从 sticky 右栏移除** → 移到"更多功能"
- **默认展开的 Audit cards** → 单行摘要

## N. 折叠的旧 UI 元素

- 模型技术数据（expected_return / prob_* / signal_strength / uncertainty）→ "查看模型技术数据"
- 数据血缘表（available_at / source_type / raw_file）→ "查看数据血缘"
- 案例审计（case_id / case_available_at）→ "查看案例审计信息"
- 工具技术 Trace（tool / args / result / duration / guard）→ "查看技术 Trace"，原始 JSON 二级展开
- 审计详情（7 项检查）→ "查看审计详情"
- 复盘结构化标签（taxonomy）→ "查看结构化复盘标签"
- 系统边界（Alpha / Profitability / Settlement / Version）→ "系统边界" 抽屉

## O. 保留的旧 UI 元素

- **业务语义色**（绿 PASS / 红 REJECT / 黄 WARNING / 蓝 info；BUY 蓝 / SELL 红 / NO_TRADE 灰）—— 已冻结契约
- **Evidence Time Gate 允许/拒绝 双栏** → 强化为中央时间轴聚光灯
- **Lock → Reveal → 复盘四问**（发生了什么/为什么/有没有偷看/教训）—— 已冻结契约，保留为故事第二高潮
- **Agent 真实工具轨迹 + Guard** → 业务化呈现，不改实现
- **"不展示模型私有思考过程"**声明

## 原型与截图

`design_prototypes/`（静态演示值，PROTOTYPE ONLY）：
- `01_initial.html` · `02_decision.html` · `03_agent.html` · `04_case_e.html` · `05_reveal.html`
`design_shots/*.png`（1440×900，img2text 抽查合格）：02 首屏 Hero 前置 ✓ / 04 时间轴"来晚了"清晰 ✓

---

## 最终 Lead 回答

**Q1：第一屏是否在 10 秒内告诉用户做什么？** **YES**（Hero 前置，建议/价差/风控/一句话原因首屏齐全）
**Q2：Decision Recommendation 是否为页面最大视觉中心？** **YES**（46px 建议大标题 + 唯一 Hero 阴影）
**Q3：是否彻底取消 S1/S2/S3 作为业务主结构？** **YES**（改为交易员问题叙事 A→K）
**Q4：Agent 是否看起来在操作交易系统？** **YES**（操作对象 chip + 真实工具轨迹 + 流式，非外挂 Chatbot）
**Q5：是否默认隐藏 raw JSON / technical fields / Audit？** **YES**（全部折叠进技术详情，原始 JSON 二级展开）
**Q6：Case E 是否一眼看懂"这条信息来晚了所以没用"？** **YES**（中央时间轴"报价截止 10:00 ← 11:00 才生成"+ NOT USED 印章）
**Q7：8 分钟演示是否最多约 3~4 次主要滚动？** **YES**（Agent E：3 次主滚动 / 4 次点击）
**Q8：当前阶段是否修改正式交易 UI / Backend？** **NO**（仅设计稿 + 静态原型，生产代码零改动）

---

# DESIGN REVIEW READY

**Phase 1（Design）完成，等待用户审核设计方向。** 用户批准后才进入 **Phase 2 — Production UI Implementation**（绑定真实 DecisionSnapshot，Pass 5/6 回归 Golden Case 数字一致）。
