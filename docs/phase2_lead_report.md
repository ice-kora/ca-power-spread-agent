# 阶段二 · Lead 整合验收报告

> 2026-08-09 ｜ 业务契约冻结 → 团队并行（A 审计 / B 数据 / C 建模 / D 回测）→ Lead 整合
> 全程无泄漏要求、保守模式（UNKNOWN→禁用）、不粉饰结果。

## 1. 执行概况

| 角色 | 交付 | 状态 |
|---|---|---|
| A 业务/Feature Availability | `docs/feature_availability_matrix.md`（37 特征全矩阵，天气定性为 ERA5 再分析+合成段） | ✅ |
| B 数据修复 + Canonical | `code/canonical.py` + `canonical.parquet`(49,210 行) + `feature_schema.json` + 修复 app.py | ✅ |
| C 三层模型 | `predictions_{rule,interpretable,catboost}.csv` + `model_notes.json` + `model_c.py` | ✅ |
| D 严格回测 | `code/backtest.py` + `backtest_outputs/`（报告/summary/equity 曲线/6 条 snapshot） | ✅ |
| Lead | 本文档 + `docs/leakage_report.md` | ✅ |

## 2. 验收清单（对应契约 §10）

### ① 修复后的业务时间线
决策时点 = **D-1 日 DAM Market Close / bid cutoff（10:00 PT，官方 BPM；13:00 是 DA 结果发布 = label 可见时点）**。此时：可用 ≤D-2 历史价格/负荷/天气滞后、D+1 负荷预测(2DA, ASSUMED)、日历/节点；**不可见**：D+1 的 DA/RTPD/Return/实际负荷/实际天气（仅作 label）。详见 `docs/business_contract.md` 与 `docs/market_timeline.md`。

### ② feature_availability_matrix.md ✅（`docs/`，37 特征，A/B/C 三组）
- A 确定可用：31 个（价格滞后、滚动、日内形态、负荷滞后、节点联动、2DA、日历）
- B 仅 label：3 个（spread/da/rtpd_next）
- C UNKNOWN→禁用：3 个（t2m/ssrd/wind100_next，天气穿越）

### ③ leakage_report.md ✅（`docs/`，详见该文件）

### ④ canonical dataset schema
一行 = **node × target_date × target_hour**（target_date=交付日，decision_date=target_date−1）。46 列：
- **X 区（38）**：时间/节点 7、价格滞后 9（T-2 起）、滚动 6、日级统计 7、负荷 3、天气滞后 3、节点联动 3
- **label 区（4，隔离）**：actual_da, actual_rtpd, actual_return, direction
- X 与 label 严格不相交；`feature_schema.json` 为单一来源。

### ⑤ 修复前后数据量变化
| 指标 | 旧 features | 新 canonical |
|---|---|---|
| 总行数 | 51,300 | **49,210** |
| 幽灵 hour=0 | 2,052 | **0** |
| label 含 NaN | 2,139 | **0** |
| 日级特征可用率 | 4% | 99.7% |
| split | train 15,311 / val 7,244 / test 4,678 |（按 decision_date 时间切分） |

### ⑥ 删除特征及原因
`t2m_next` / `ssrd_next` / `wind100_next`（目标日天气，穿越，禁用）+ 幽灵 hour=0 行（垃圾）。详见 leakage_report。

### ⑦⑧⑨ 三层模型 test 结果（Agent C）
| 模型 | coverage | 方向acc(交易) | SELL prec | BUY prec | SELL均PnL | BUY均PnL | AUC |
|---|---|---|---|---|---|---|---|
| Rule（白盒） | 21.3% | 61.5% | 61.8% | 16.7% | +80.0 | −6.5 | 0.572 |
| Interpretable（逻辑回归） | 67.0% | 64.8% | 63.4% | 66.2% | −6.4 | −98.7 | 0.638 |
| CatBoost（对照） | 59.4% | 63.5% | 65.8% | 60.9% | −5.2 | −78.7 | 0.639 |

### ⑩ 严格 as-of 回测结果（Agent D，test 2026-06-02~08-05，ZP26，1 MWh/仓）
| 策略 | 覆盖率 | 方向acc | SELL精 | 累计PnL | Sharpe(日) |
|---|---|---|---|---|---|
| 静态全 SELL（漂移基准） | 100% | 48.3% | 48.3% | **+132,746** | 4.87 |
| All-Trade[Rule] | 26% | 60.4% | 60.6% | +79,485 | 4.21 |
| **Decision[Rule]** | 17% | 58.7% | 58.9% | **+79,202** | 4.19 |
| All-Trade[Interpretable] | 60% | **66.2%** | 66.4% | **−124,072** | −4.61 |
| All-Trade[CatBoost] | 54% | 62.9% | 70.0% | **−129,484** | −4.85 |

### ⑪ BUY / SELL / NO_TRADE 分别表现
- **SELL（Rule 决策为主）**：命中 58.9%，正 PnL，但由 CONTROLX 极端右尾事件驱动（彩票式）。
- **BUY**：Interpretable/CatBoost 大量 BUY 被 ±数千 $/MWh 极端值打穿 → 巨亏。
- **NO_TRADE**：Rule 决策覆盖率 17%（高选择性）；加"波动高→观望"风控后利润从 +79k 塌到 +2k~+0.6k——说明利润几乎全在高波动尾部小时，NO_TRADE 若用于这些小时则基本无利可图。

### ⑫ node / hour / month performance
详见 `backtest_outputs/backtest_report.md`。要点：SNLNDRO 最平稳（各模型 ~0.66 准确、小幅正 PnL）；CONTROLX 有极端右尾，Interpretable/CatBoost 在此大亏；ELCA cold-start AUC ~0.50 无价值。

### ⑬ decision snapshot（6 条，见 backtest_report.md，示例）
```
Node CONTROLX · HE18 · Expected +7.2 · Prob 0.76 · Decision SELL_DA
Evidence: 近30日同hour spread 偏正；load 2DA 高于历史均值；Rule SELL
Risk: 近7日 spread 波动较高
```

### ⑭ 仍 UNKNOWN 的业务字段
- `load_2da_forecast` / `load_peak_flag` 发布时刻（ASSUMED，未官方实证）
- 天气时区/小时对齐（America/Los_Angeles naive）
- 2DA 文件发布语义、RTPD 聚合口径（15min vs 5min）、结算口径
- 决策 cutoff 具体时刻（**已由 market_timeline.md 核验并修正为 D-1 10:00 PT**；本行系 V0.1 遗留待确认项，原 13:00 实为 DA 结果发布时点）

### ⑮ 下一阶段 Agent 化建议
见下 §4。

## 3. 最终 Lead 判断（不粉饰）

> **在不使用任何未来信息的严格 as-of 条件下，这套策略有"真实但很微弱"的预测价值，不足以支撑实盘交易。**

具体证据：
1. **方向信号真实**：三层模型交易时段方向准确率 58-66%、AUC 0.57-0.64，显著 >50%——**存在可学的真实信息**。
2. **但准确率 ≠ 盈利**：准确率最高的 Interpretable(66%)/CatBoost(63%) 反而巨亏（−124k/−129k）——CONTROLX 的极端右尾事件（±数千 $/MWh）一次打穿 BUY 头寸。
3. **盈利主要来自市场漂移而非预测**：样本期（2026-06 起）DA>RTPD 强正漂移，"静态全 SELL"（+133k）跑赢所有信号策略；唯一正的 Rule 也未跑赢该漂移基准。
4. **幅度预测基本无信息**：expected_return 与 actual_return 秩相关 ≈0.05。
5. **盈利是"彩票式"抓右尾**：top-50 事件贡献 54% PnL；合理风控（规避高波动尾部）下利润塌至接近 0。

**结论**：原黑盒方案中"63.9% 准确率 + 盈利"的相当部分效果来自**数据泄漏**（目标日天气穿越 + label 污染 + 幽灵行）。修复后无泄漏基线显示：模型方向判断有微弱真实信号，但**无法在合理风控下产生风险调整后的稳健收益**。若考核方要的是"能实际交易的预测"，当前数据/特征/模型组合**不构成有效实盘策略**——这是本数据集的真实边界，不是工程缺陷。

## 4. 下一阶段 Agent 化建议

1. **先补数据**（比调模型优先级高）：
   - 真实 **天气预报**（决策时点可得，替代被禁用的实测天气）
   - 本地燃气价（SoCal/PG&E Citygate）、光伏/风电出力、负荷预测修正
   - 确认 2DA 发布时点、RTPD/结算口径
2. **模型**：Rule 的 SELL-only 是当前唯一正收益方向——可做"SELL 方向 + 波动风控"的谨慎策略，但要接受它不跑赢漂移的事实；不建议 Interpretable/CatBoost 的 BUY（右尾风险）。
3. **Agent 化**（后续阶段）：把 Rule 基线作为**白盒决策核心**，ML 作对照；加信息检索（真实预报/气价）、证据解释、审计轨迹。但**先解决数据问题，否则 Agent 也是在弱信号上做解释**。

---

**交付物清单**：`docs/business_contract.md`、`docs/feature_availability_matrix.md`、`docs/leakage_report.md`、`code/canonical.py`+`canonical.parquet`+`feature_schema.json`、`code/model_c.py`+`predictions_*.csv`+`model_notes.json`、`code/backtest.py`+`backtest_outputs/`。
