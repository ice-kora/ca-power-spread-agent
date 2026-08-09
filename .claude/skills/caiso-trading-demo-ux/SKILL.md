---
name: caiso-trading-demo-ux
description: Use whenever designing, reviewing, or implementing the CAISO Trading Decision Agent demo UI. Enforces the business narrative, Chinese-first presentation, white-box decision explanation, Agent tool-trace UX, evidence time-gate story, and Lock→Reveal→Review demo flow while freezing trading logic.
---

# CAISO Trading Decision Agent — Demo UX

This skill is the product-specific design contract for the CAISO Trading Decision Agent MVP.

It governs presentation and interaction only. It must not change trading logic.

## 1. Product definition

Primary product name:

**CAISO 交易决策助手**  
Secondary English name: **Trading Decision Agent**

One-sentence purpose:

> 在日前报价截止前，根据当时可获得的信息，为次日节点交易提供买入日前、卖出日前或不交易建议，并说明原因。

Product boundary:

> 这是可解释、可审计的交易决策辅助系统，不是自动交易系统，也未证明稳定盈利。

Do not let the boundary disclaimer dominate the primary viewport.

## 2. Audience

Primary demo audience:

- electricity-market business users,
- trading/risk stakeholders,
- interviewers or reviewers who may not know the codebase.

They should not need to understand Python field names or internal architecture to follow the demo.

## 3. The first 10 seconds

After a decision is generated, the user must immediately understand:

1. **交易对象是什么？**
2. **系统建议做什么？**
3. **预计价差是多少？**
4. **风控是否允许？**
5. **下一步能做什么？**

Therefore the first viewport must center a Recommendation Hero.

Example business hierarchy:

- CONTROLX · 次日 H3
- **卖出日前 / SELL DA**
- **预计 DA−RT：+$59.79/MWh**
- 模型信号：偏弱
- 风控：通过
- 截止前可用证据：1 条
- 一句话原因
- `[锁定本次决策]`

Never place a large MVP status dashboard above this recommendation.

## 4. Required page architecture

The business page should follow this narrative:

### A. 选择交易
- 决策日期
- 节点
- 目标小时
- Golden Case
- 生成交易建议

### B. 现在怎么办？
Recommendation Hero.

### C. 为什么？
Exactly three business explanations:
1. 模型怎么看？
2. 风控让不让做？
3. 白盒规则如何得到最终建议？

### D. 用了什么信息？
- 本次参与决策的信息
- 被系统拒绝的信息
- 类似历史案例

### E. 问交易 Agent
- natural-language question
- streaming answer
- real tool execution trace

### F. 锁定 → 揭晓
- lock decision
- reveal actual result

### G. 事后复盘
- what happened
- why
- whether future information was excluded
- lesson

### H. 技术详情
Default collapsed:
- context
- raw features
- raw model output
- provenance
- audit
- technical tool trace

Do not return to `S1...S9` as the main navigation.

## 5. Chinese-first business language

Primary UI language is Chinese.

Allowed English terms where business-standard:
- DA
- RT
- BUY DA
- SELL DA
- NO TRADE
- Risk Gate
- Agent
- PnL

Use bilingual labels only where they aid understanding:

- 风险检查（Risk Gate）
- 卖出日前（SELL DA）

Do not expose these by default:

- `expected_return`
- `prob_positive`
- `prob_negative`
- `model_signal_strength`
- `availability_proven`
- `STRUCTURAL_LAG`
- `INITIALIZATION_AFTER_CUTOFF`
- `DecisionSnapshot`
- raw JSON

Business translations:

- `expected_return` → 预计价差
- `model_signal_strength` → 模型信号强度
- `uncertainty` → 不确定性
- `decision_eligible=false` → 未参与决策
- `INITIALIZATION_AFTER_CUTOFF` → 该信息在报价截止后才开始生成
- `AVAILABILITY_NOT_PROVEN` → 无法证明当时已经可获得

Technical codes remain available only inside technical detail.

## 6. Recommendation Hero

This is the most important visual element on the page.

For a SELL example:

**卖出日前 SELL DA**

Supporting information:
- 预计 DA−RT：+$59.79/MWh
- 风控：通过
- 模型信号：偏弱
- 可用外部证据：1 条

Business explanation:

> 系统预计日前价格高于实时价格，同时没有触发禁止交易的风险条件，因此白盒规则建议卖出日前。

BUY explanation should reverse the economic direction correctly.

NO TRADE explanation should explicitly state why trading is withheld.

## 7. Explain the decision with three layers

### 7.1 模型怎么看？
Show business values first:
- 预计价差
- 方向倾向
- 信号强度
- 不确定性 if useful

Do not claim z-score inputs are SHAP or causal contribution.
Call them:
- 当前特征状态
- 模型参考特征

Technical model fields are expandable.

### 7.2 风控让不让做？
Use a single strong status:
- 通过
- 提醒
- 拒绝

Then give concise human-readable reasons.

### 7.3 最终规则是什么？
Show the white-box rule as a short visual equation/flow, e.g.:

`预计 DA−RT > 0` + `风险通过` → **SELL DA**

Add the trading meaning:

> SELL DA = 日前卖出、实时买回；如果最终 DA > RT，则交易盈利。

## 8. Evidence Time Gate is a signature demo element

This is one of the product’s most distinctive features and should be visually memorable.

Divide evidence into:

### 本次允许使用的信息
Example:
- ✓ GFS 06Z
- cutoff 前可获得
- 已进入辅助证据

### 被系统拒绝的信息
Example Case E:
- ✕ GFS 18Z
- 报价截止：10:00 PT
- 初始化：11:00 PT
- 可用时间：无法证明
- **未参与决策**

Business explanation:

> 这条预测在报价截止之后才开始生成，因此无论最终什么时候发布，都不可能被当时的交易员获得。

Do not lead with reason codes.
Do not imply a precise available time when it is unknown.

## 9. Similar cases

Default presentation is a case card, not a database table.

Show:
- 日期
- 节点 / 小时
- 当时建议
- 实际结果
- 一句经验

Example:

> 2026-07-08 · CONTROLX · H2  
> 当时建议：SELL  
> 实际结果：+$2,216/MWh  
> 经验：历史上曾出现极端价差，机会存在，但需要注意尾部风险。

Audit metadata is expandable.

## 10. Trading Agent experience

The Agent is not a decorative chatbot.
It is the natural-language interaction layer over the white-box decision system.

Primary question examples:
- 为什么建议卖出？
- 为什么不是买入？
- 最大风险是什么？
- 用了哪些数据？
- 哪些信息被系统拒绝？
- 有没有类似历史案例？
- Reveal 后：这笔为什么亏？

### Streaming
The answer should stream progressively when the provider/transport allows it.

### Observable work trace
Show real actions only, such as:
- ✓ 读取当前交易建议
- ✓ 检查模型判断
- ✓ 检查风险条件
- ✓ 查询外部证据
- ✓ 查询类似历史案例
- ✓ 生成解释

Do not expose or fabricate private chain-of-thought.
Do not show a tool step unless that tool actually ran.

### Business answer style
Never begin with:
> 根据 `get_decision` 返回结果……

Prefer:
> 这笔交易建议卖出日前，主要有三个原因……

Round numeric outputs to business precision.

### Technical trace
Default collapsed.
Show only:
- tool name,
- concise args,
- result summary,
- duration,
- guard result.

Raw JSON is a second-level expansion.

## 11. Agent tool routing must match the question

Expected mapping:

- 决策原因 → `get_decision`, optionally `get_feature_explanation`
- 特征 → `get_feature_explanation`
- 外部证据 / Time Gate → `get_evidence`
- 类似案例 → `get_similar_cases`
- 数据来源 → `get_data_provenance`
- Reveal 后复盘 → `get_post_trade_review`

Do not make every question route only to `get_decision`.

## 12. Lock → Reveal is the story climax

### Before lock
CTA:
**锁定本次决策**

Text:
> 锁定后，之后发生的信息不会反向修改本次建议。

### Locked
Show:
**决策已锁定**

Enable:
**揭晓真实结果**

### Revealed
Show prominently:
- Actual DA
- Actual RT / RTPD
- Actual DA−RT
- PnL
- direction correct?

Do not hide losses.

If wrong:
> **本次方向判断错误。**

## 13. Post-trade review

Default questions:

### 发生了什么？
Human-readable result.

### 为什么？
Use known structured review facts; do not invent causality.

### 系统有没有偷看未来？
Explicitly state whether post-cutoff evidence was excluded.

### 有什么教训？
Explain modeling/risk limitation in business language.

Structured taxonomy remains expandable.

## 14. Audit presentation

Audit is proof, not the homepage.

Default summary:

**审计状态：6 项通过，1 项提醒**

Then `[查看审计详情]`.

Keep all real warnings. Never turn WARNING into PASS for presentation.

## 15. Data provenance presentation

Default view answers:
- 用了什么数据？
- 数据来自哪里？
- 当时能不能拿到？

Example:

- 历史 DA/RT 价格 — 公司历史价格文件；已与 CAISO OASIS 对账
- 历史负荷 — 公司历史负荷文件
- 2DA 负荷预测 — 公司预测文件；历史 vintage 仍有局限
- 历史天气 lag — 历史天气数据；不使用目标日实际天气
- GFS evidence — 必须经过时间门

Raw filenames and availability fields are expandable.

## 16. Status and disclaimers

Do not use a giant MVP STATUS block.

Use a compact system status area such as:
- Agent 已连接
- 数据模式：FULL / DEMO
- 自动交易：关闭
- 模型：实验性

Detailed boundaries under “系统边界”:
- Alpha = WEAK
- Profitability verified = NO
- simplified signal backtest
- not automatic trading

## 17. Visual direction

Preferred visual character:

**restrained professional trading workstation + editorial clarity**

Avoid:
- crypto/neon aesthetics,
- generic SaaS bento dashboards,
- glowing AI gradients,
- excessive rounded cards,
- every block having a border,
- endless equal-size tiles.

Use:
- quiet neutral canvas,
- strong recommendation typography,
- disciplined status colors,
- compact data formatting,
- meaningful whitespace,
- one memorable Evidence Time Gate visual.

## 18. First-viewport acceptance criteria

At 1440×900 and 1920×1080, without scrolling after a decision is generated, the viewer should see at minimum:

- selection controls or compact context,
- final recommendation,
- expected spread,
- risk status,
- short “why” explanation,
- lock CTA,
- Ask Trading Agent entry point.

A giant system-status panel fails this criterion.

## 19. Forbidden presentation patterns

Do not ship if the default business view contains:

- `S1/S2/S3...` module headers,
- full-width raw provenance tables,
- horizontal scrolling,
- raw reason codes as primary copy,
- raw JSON inside Agent response,
- model values with 10+ decimal digits,
- daily brief competing with the main decision,
- audit panel permanently expanded,
- final recommendation below several screens of metadata.

## 20. Frozen business core

UI work must not change:

- predictions,
- expected return values,
- model direction,
- Risk Gate logic or thresholds,
- Rule Engine logic,
- final recommendation,
- Evidence Time Gate eligibility,
- case retrieval semantics,
- PnL,
- actual outcome,
- Lock/Reveal access control,
- tool numerical truth.

Presentation is allowed to rename, round, group, collapse, stream, animate, and reorder information, but not alter the underlying DecisionSnapshot meaning.

## 21. Required design workflow

When redesigning this project:

### Pass 1 — Audit
Use screenshots of the current interface and identify:
- first-viewport problem,
- hierarchy problem,
- terminology problem,
- scroll/table problem,
- Agent problem.

### Pass 2 — Design only
Before backend integration, deliver:
1. initial-state wireframe,
2. recommendation-state wireframe,
3. Agent-answer state,
4. Case E state,
5. Reveal/review state,
6. visual token sheet.

No business backend changes in this pass.

### Pass 3 — Static high-fidelity mock
Create a static working page with representative values.
Take screenshots at 1920×1080 and 1440×900.

### Pass 4 — Critique
Reject the design if a new user cannot answer:
- what should I do?
- why?
- what risk?
- what did the Agent do?
within 30 seconds.

### Pass 5 — Integrate
Only after visual approval, bind the accepted UI to the existing DecisionService / DecisionSnapshot.

### Pass 6 — Regression
Prove that the presentation redesign did not alter any Golden Case business output.

## 22. Demo success test

The final demo should naturally tell this story:

> 我要看明天 CONTROLX H3  
> → 系统建议卖出日前  
> → 预计价差 +$59.79/MWh  
> → 模型方向偏 SELL 但信号偏弱  
> → 风控允许  
> → 白盒规则得到 SELL DA  
> → Agent 可以调用系统工具解释原因  
> → 一条截止后的天气信息被系统明确拒绝  
> → 我锁定决策  
> → 揭晓结果  
> → 如果亏损，系统如实复盘  
> → 整个过程可解释、可审计、没有未来信息穿越。

If the interface cannot tell that story without the presenter translating backend fields, redesign it before implementation is accepted.
