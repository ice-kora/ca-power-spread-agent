# 阶段三 · Lead 综合总结（stage3_lead_summary）

> 2026-08-09 ｜ 目标：把"为什么方向准却巨亏"从模糊问题变成可量化、可解释、可拦截的风险类型；设计并验证第一版 White-box Risk Gate。
> 团队：Agent A（亏损事件）/ B（盈利事件）/ C（分层）/ D（Risk Gate）→ Lead 综合。全程只读分析、无泄漏口径、不粉饰。

---

## 1. 四角色关键发现汇总

| 角色 | 关键发现 |
|---|---|
| **A 亏损事件** | Top50 亏损 49 笔在 CONTROLX +1 ELCA；两机制：ML 的 CONTROLX BUY（35 笔 −34,630，RTPD 暴跌转 Return 正）+ Rule 的 CONTROLX SELL（14 笔，DA 暴跌）。**事前可识别 44A/6C/0B**（三模型冲突 29、spread 极值分位 29、ML 的 CONTROLX BUY 系统性亏损） |
| **B 盈利事件** | Top50 盈利 100% 是 CONTROLX 极端负价事件，**彩票式抓尾部**、不可复现；盈利与亏损是同一肥尾两侧（Top20 盈亏重叠 7–12/20） |
| **C 分层** | BUY 累计 −254,947 vs SELL +61,706（**严重不对称**）；CONTROLX 重尾（kurt 3.25）+ BUY 逆 +84 漂移 + 赔率倒挂；`CONFIDENCE NOT CALIBRATED`（>0.80 桶反最低）；Model Agreement 仅 3/3 或 Rule 参与有效 |
| **D Risk Gate** | Gate 有效：PnL −130,042→+962、maxDD −147,957→−654、worst −2,216→−113、CVaR −1,056→−74；**作用=拦截 CONTROLX BUY**，无 alpha；7 条规则只留 3 条；反事实拦 36/50、避损 35,544 |

---

## 2. 六个问题的回答

### Q1：为什么 Accuracy 60%+ 仍然亏钱？

**因为准确率衡量的是"方向判断比例"，而 PnL 由极少数大事件主导——两者统计对象不同。**

数据证据：merged BUY 命中率 62–66% 不低，但 **median PnL 为正（+25.9）而 mean 为负（−87.6）**，正是"多数小赚 + 少数巨亏"的赔率倒挂结构。CONTROLX BUY 命中时平均只赚 ~+100，做错平均亏 **−450**；一次 RTPD 深度负电价（−880~−2365）的错单即可吞掉几十笔正常收益。方向准确率 60%+ 与巨额亏损可以同时成立，因为准确率不惩罚"错在多大金额上"。

### Q2：亏损主要来自什么？（给数据）

按贡献排序：
1. **ML 的 CONTROLX BUY（主导）**：merged BUY 累计 **−254,947**；Top50 中 35 笔、−34,630。CONTROLX 平均价差 +84（BUY 逆漂移），重尾右偏（kurt 3.25）。
2. **少数极端事件**：Top50 亏损 ≈ −50,348，集中 06-15~07-17 少数交易日；盈利同理（Top50=100% CONTROLX 极端事件）。
3. **BUY 单侧**：SELL 整体 +61,706，BUY 整体 −254,947——方向结构性不对称。
4. **confidence 虚高且校准失败**：>0.80 桶 accuracy 反最低（60.3%），ML confidence 与 PnL 反相关。
5. **模型结构**：Interpretable+CatBoost 双看 CONTROLX BUY（相关误差二次确认），不是"独立验证"。

**主导 = CONTROLX BUY 单侧 + 少数极端事件**；普通预测错误和 hour 维度是次级。

### Q3：内部数据能提前识别多少极端亏损？

- Agent A：Top50 亏损 **44/50 为 Type A（88%）事前可识别**（三模型冲突、spread 历史极值分位、系统性亏损策略），6 笔残余随机尾部，0 笔需外部信息。
- Agent D 反事实：Risk Gate 实际拦截 **36/50（72%）**、避免亏损 35,544。
- **诚实限定**：可识别的是"危险节点/方向/状态"（CONTROLX BUY 在逆漂移+重尾下），**不等于能预测具体哪一天、多大幅度**；剩余 14 笔（Rule 的 CONTROLX SELL）train+val 证明无法事前可靠识别。

### Q4：Risk Gate 是否有效？

**结论：YES（作为风险控制），但它不创造 alpha。**

- 有效性：施加在 Model Committee 上，test PnL −130,042→**+962**、maxDD −147,957→**−654**、worst −2,216→**−113**、CVaR(1%) −1,056→**−74**，四项风险指标数量级改善。
- 代价：coverage 48.8%→**10.9%**（几乎只做唯一正 EV 的 SNLNDRO SELL）；对原本安全的 Rule（+79,485）零改变。
- 反事实净收益 +16,134（拦 36 笔亏 vs 误伤 18 笔盈利）。
- **本质**：Gate 不是"交易得更聪明"，而是"避开 CONTROLX BUY 这个灾难"——它把策略退回到"只在安全节点做正漂移方向"。**作为风险闸有效，作为盈利引擎仍无正 EV 来源。**

### Q5：优先补什么外部信息？（基于本轮实际亏损事件排序，非照搬）

按本轮最大亏损的实际成因（CONTROLX **RTPD 深度负电价**，2026-06/07 夜间清晨，−880~−2365）排序：
1. **CA-ISO 可再生能源出力（尤其夜间/清晨的实际+预测，供给过剩→深度负电价信号）**——直接命中最大亏损机制。
2. **负荷预测的实时修正 / 夜间低谷负荷**——负电价时段的系统需求侧信号。
3. **机组停运 / 输电阻塞（outage / constraint）**——RTPD 尖峰类亏损（如 ELCA 晚间 +1102）的驱动。
4. **本地燃气价**——本轮亏损主要由供给过剩（负电价）而非气价驱动，**优先级低于上述**（可作为长期补充，非本轮关键）。

### Q6：是否适合进入 Agent 阶段？

**结论：NOT READY。**

原因：即使 Risk Gate 后，策略的唯一正 EV 来源是 SNLNDRO 的 SELL（coverage 10.9%），没有创造 alpha；CONTROLX（无论 BUY/SELL）在现有内部数据下要么灾难（BUY）要么无法事前识别（SELL 的 DA 暴跌）。在补上"可再生能源出力 / 负电价预警 / 负荷修正"这类能识别深度负电价时段的数据之前，Agent 即便去"搜索信息"，能搜到的关键信号也缺失——**先解决数据，再谈 Agent**（否则 Agent 只是在弱信号上做解释，重蹈"黑盒解释"覆辙）。

---

## 3. 综合结论

1. **"方向准却巨亏"已从模糊问题变成可量化风险类型**：CONTROLX BUY（逆漂移+重尾）→ 方向门拦截；极端尾部事件 → Tail/CVaR 监控；confidence 不可信 → 不能用概率校准，需尾部/分位校准；model agreement 只有 3/3 或 Rule 参与有效。
2. **第一版 White-box Risk Gate 验证有效**：能把灾难性尾部（maxDD −148k→−0.65k）压到可忽略，证明**内部数据足以识别并拦截大部分危险交易（72%）**。
3. **但盈利来源问题未解决**：Gate 只是"不亏"，不是"会赚"；在 CONTROLX 深负电价可被预测（需外部供给数据）之前，策略没有可外推的正 EV。
4. **诚实结论**：现有内部数据下的"真实预测价值"= 能识别并规避灾难（风控价值），**不是能稳定赚钱（盈利价值）**。

---

## 4. 下一阶段建议（按优先级）

1. **补数据（决定能否盈利的关键）**：CA-ISO 可再生能源出力 / 负荷修正 / 停机阻塞——验证能否在交易前识别"深度负电价时段"（直击最大亏损机制）。
2. **尾部/分位校准**：对 Model 的 expected_return 做 quantile/EVT 校准，替换失效的 confidence。
3. **若外部数据到位**：再把 Model Committee + Risk Gate 升级为"能识别负电价风险事件"的可解释 Agent 骨架（此时才值得做 Agent/信息检索）。
4. **在此之前**：维持"SNLNDRO SELL-only + Risk Gate"作为不亏的保守基线，明确其局限。

---

## 5. 交付物清单（docs/stage3/ + code/data/stage3/）

`top_loss_event_analysis.md`、`top_profit_event_analysis.md`、`buy_sell_analysis.md`、`node_risk_analysis.md`、`hour_risk_analysis.md`、`confidence_calibration_analysis.md`、`model_agreement_analysis.md`、`risk_gate_design.md`、`risk_gate_backtest.md`、`risk_gate_counterfactual.md`、本文件 + `code/data/stage3/`（risk_features.parquet、top_loss/profit_events.csv、risk_gate_*.json 等）+ `code/tmp/agent_*_*.py`（可复现脚本）。
