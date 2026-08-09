# Architecture · V0.1 Baseline

> CAISO 价差交易决策辅助项目 · 工程冻结版架构说明
> 目标：客观记录当前真实实现与能力边界，供后续架构评审。V0.1 不含 Agent 化/联网检索/UI 产品化。

---

## 1. 项目定位

预测 CAISO 日前(DA)与实时(RTPD)价差 `Return = DA − RTPD` 的方向与幅度，辅助 **SELL_DA / BUY_DA / NO_TRADE** 决策。核心是**无泄漏的时序数据层 + 三层可对照模型 + 白盒 Risk Gate**，而非"预测价格数值的黑盒"。

## 2. 目录结构

```
CA-电力交易预测/
├── 价格数据/*.xlsx            # 3节点 DA/RTPD/DARTPD Return（-c 排除）
├── load_CA_ISO_TAC_2DA.csv    # 日前负荷预测
├── load_CA_ISO_TAC_ACTUAL.csv # 实际负荷
├── zone_weather_hourly.csv    # 分区天气（历史段为 ERA5 再分析，D+1 已被禁用）
├── 节点位置.xlsx              # node→zone
├── CLAUDE.md / README.md      # 环境说明 / V0.1 概览
├── 工程报告.md / 数据审计与业务口径.md / 业务讲解语音转文字.md
├── docs/                      # 架构与评审文档（本文件 + 下方清单）
├── code/
│   ├── read_data.py           # 数据对齐 → master.csv
│   ├── canonical.py           # 单一特征实现 + 防泄漏 + Leakage Guard → canonical.parquet
│   ├── model_c.py             # 三层模型（Rule/Interpretable/CatBoost）→ predictions_*.csv
│   ├── backtest.py            # 严格 as-of 回测 + Risk Gate
│   ├── train.py / evaluate.py # 早期黑盒流水线（已降级/弃用，仅保留参考）
│   ├── app.py / templates/    # 早期网页（V0.1 已与 canonical 模型不匹配，未接入）
│   ├── analysis/              # 阶段分析脚本（可复现：agentA/B/C/D）
│   ├── deliverables/          # Word/PDF/PPT 生成器
│   ├── data/                  # 生成物（gitignore）
│   ├── models/                # 模型 pkl（gitignore）
│   └── backtest_outputs/      # 回测报告与曲线
```

## 3. 数据流与模块依赖

```
原始数据(价格/负荷/天气/节点)
   ↓ read_data.py
master.csv（node×date×hour 长表）
   ↓ canonical.py（单一特征构造：X 38 特征 + label 4，防泄漏，Leakage Guard）
canonical.parquet + feature_schema.json
   ↓ model_c.py（Shared Model + Node Feature，严格时间切分 train/val/test）
predictions_{rule,interpretable,catboost}.csv
   ↓ backtest.py（as-of 回测 + 三态决策 + Risk Gate）
backtest_outputs/ + docs/stage3/（分析报告）
```

**依赖关键点**：`canonical.py` 是唯一特征实现（app/train 均复用，消除双实现）；`feature_schema.json` 记录 X/label 与可用性；`backtest.py` 对"任意模型预测 CSV"通用。

## 4. 核心设计决策

1. **业务口径冻结**（`docs/business_contract.md`）：决策时点 = D-1 日 DA bid cutoff 前；D+1 的 DA/RTPD/Return 只能作 label；Return=DA−RTPD。
2. **无泄漏数据层**（`docs/leakage_report.md`）：幽灵行/NaN label/天气穿越/滞后口径全部修复；Leakage Guard 拦截 `available_at > decision_cutoff` 的特征。
3. **三层模型**（`docs/Model.md`）：Rule（白盒）→ Interpretable（逻辑回归/线性）→ CatBoost/LightGBM（对照），Shared Model + Node Feature，不做一节点一模型。
4. **白盒 Risk Gate**（`docs/DecisionPipeline.md`）：不预测方向，只判定候选交易是否放行（PASS/REJECT/WARNING + reason_code）；用 train+val 定规则、test 只验证。

## 5. 能力边界（客观如实）

### ✅ 已实现（V0.1）
- 数据对齐、单一特征实现、无泄漏 canonical dataset（49,210 行）
- 三层模型（Rule/Interpretable/CatBoost）+ 特征重要性/系数
- 严格 as-of 回测（多指标：accuracy/coverage/PnL/maxDD/CVaR/Sharpe 等）
- 第一版白盒 Risk Gate（3 条有效规则 + 反事实）
- 全套文档（业务契约、特征可用性、泄漏审计、阶段报告）

### ❌ 未实现（V0.1 边界内明确不做）
- **Agent 化**：信息检索、白盒规则引擎产品化、决策解释 UI、审计轨迹——未做
- **联网数据**：真实天气预报、本地燃气价、可再生能源出力、负荷修正——未接入（V0.1 只用内部数据；`weather_next` 因穿越被禁用）
- **概率/尾部校准**：confidence 已知未校准（`CONFIDENCE NOT CALIBRATED`），Platt/Isotonic 无效，需尾部/分位校准——未做
- **推理链网页**：早期 app.py 存在但与 canonical 模型不匹配，未接入 V0.1
- **LSTM/Transformer / 未来多天预测 / 自动下单 / 仓位优化**——明确不做

### 🔜 留待未来（已识别，需数据/阶段前提）
1. **补外部数据**（按阶段三亏损事件排序）：可再生能源出力 → 负荷实时修正 → 停机/阻塞 → 本地燃气价。目标：识别"深度负电价时段"（最大亏损机制）。
2. **尾部/分位校准**（quantile/EVT），替换失效的 confidence。
3. **数据到位后的 Agent 化**：信息检索（真实预报/气价）→ 白盒规则引擎 → 决策解释与审计。

## 6. 当前真实结论（来自 docs/stage3/stage3_lead_summary.md）

- 方向信号**真实但微弱**（AUC 0.57–0.64），不足支撑稳健盈利；
- 盈利主要来自市场漂移而非预测；亏损由 CONTROLX BUY（逆漂移+重尾）主导；
- Risk Gate 能把灾难性尾部（maxDD −148k→−0.65k）压到可忽略（**风控价值成立**），但**不创造 alpha（盈利价值缺失）**；
- 结论：V0.1 的价值是"**无泄漏基线 + 可拦截灾难的风控**"，不是"能稳定赚钱的交易引擎"。

## 7. 文档索引（docs/）

| 文档 | 内容 |
|---|---|
| `business_contract.md` | 业务定义与铁律（冻结口径） |
| `feature_availability_matrix.md` | 逐特征可用性矩阵 |
| `leakage_report.md` | 泄漏修复与 Leakage Guard |
| `phase2_lead_report.md` | 阶段二（无泄漏基线 + 三层模型 + 回测） |
| `stage3/` | 极端事件/分层/Risk Gate 分析 + 阶段三结论 |
| `Architecture.md`（本文件） | 架构与能力边界 |
| `FeatureEngineering.md` / `Model.md` / `Backtest.md` / `DecisionPipeline.md` | 分模块设计文档 |
