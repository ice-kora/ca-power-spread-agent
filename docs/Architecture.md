# Architecture · V0.2 白盒交易决策 Agent

> ## SUPERSEDED / HISTORICAL DESIGN RECORD
>
> 本文档是 **V0.2** 架构设计记录，**NOT CURRENT IMPLEMENTATION**。
> 当前实现（V0.3.1.x Demo Freeze）以 **Web + LLM Agent** 为主入口：
> - 当前架构 / 入口：`README.md`、`mvp_readme.md`、`mvp_web.py`
> - 当前决策链**单一来源**：`code/decision_service.py`（`DecisionService.run_decision` → DecisionSnapshot；CLI/Web/LLM Tools 消费同一份）
> - 当前 Time Gate 唯一判据：`available_at <= decision_cutoff`（`agent/evidence/time_gate.py`、`agent/evidence/schema.py`；available-at-only，不 fallback）
> - 当前市场规则版本：`code/market_rules.py`（`market_rule_version_for(date)`，date-aware，2026-05-01 边界）
>
> V0.2 记录保留用于项目演进溯源。

> CAISO 价差交易决策辅助项目 · V0.2 架构说明
> 目标：在保留 V0.1 正确的数据/特征/模型/回测基础上，重构系统职责为**白盒、可审计、可解释的决策流水线**。供架构评审。

---

## 1. 项目定位

预测 CAISO 日前(DA)与实时(RTPD)价差 `Return = DA − RTPD` 的**方向 / 幅度 / 置信度**，通过 **白盒决策流水线**（Predictive Model → Agent Evidence → Risk Gate → Rule Engine → 人工）辅助 **SELL_DA / BUY_DA / NO_TRADE** 决策。

**V0.2 核心变化**（相对 V0.1）：
- 不再把"三层模型投票"当线上架构（Model Committee 结构缺陷：双 ML 同向=相关误差二次确认）。
- 模型只输出预测量，**最终交易动作由白盒 Rule Engine 决定**。
- 新增 **Agent Evidence + Evidence Time Gate**（防信息穿越的硬约束）。
- 决策 cutoff 修正为官方 **D-1 日 10:00 PT**（BPM "DAM closes at 1000 hours"）。

## 2. 目录结构

```
CA-电力交易预测/
├── 价格数据/*.xlsx            # 3节点 DA/RTPD/DARTPD Return
├── load_CA_ISO_TAC_2DA.csv    # 日前负荷预测
├── load_CA_ISO_TAC_ACTUAL.csv # 实际负荷
├── zone_weather_hourly.csv    # 分区天气（历史段 ERA5；D+1 已禁用）
├── 节点位置.xlsx              # node→zone
├── README.md / CLAUDE.md
├── docs/                      # 架构与评审文档（见 §7）
├── agent/                     # 【V0.2 新增】白盒 Agent 模块
│   ├── evidence/              #   证据 schema + Evidence Time Gate + 测试
│   ├── case_library/          #   历史案例库（18 case）
│   └── explanation/           #   决策卡片生成（12 decision card）
├── code/
│   ├── read_data.py           # 数据对齐 → master.csv
│   ├── canonical.py           # 单一特征实现 + 防泄漏 + Leakage Guard → canonical.parquet
│   ├── model_v2.py            # 【V0.2】单一 Production Predictive Model → predictions_v2.csv
│   ├── model_c.py             # V0.1 三层模型（保留作对照/参考）
│   ├── risk_gate/             # 【V0.2】独立 Risk Gate（11 规则 + 校准 + 测试）
│   ├── decision/rule_engine.py# 【V0.2】白盒 Rule Engine（三态，9 规则）
│   ├── backtest.py            # V0.1 回测引擎
│   ├── backtest_v2.py         # 【V0.2】Signal backtest（可插拔策略）
│   ├── analysis/              # 阶段分析脚本
│   ├── train.py/evaluate.py/app.py/templates/  # 早期黑盒流水线（已降级/参考）
│   └── data/ models/ backtest_outputs/         # 生成物（gitignore）
```

## 3. 数据流与模块依赖（V0.2 8 步）

```
① Predictive Model
   ↓ expected_return / prob_positive/negative / confidence / uncertainty
② Agent Evidence Collection（agent/evidence/fetcher.py）
   ↓ 外部事件（极端天气/outage/CAISO notice；当前无真实源→UNCERTAIN）
③ Evidence Time Gate（agent/evidence/time_gate.py）
   ↓ 程序判断 decision_eligible = (published_at ≤ decision_cutoff)；post-decision 隔离
④ Case / Knowledge Retrieval（agent/case_library/）
   ↓ 历史相似案例（严格 as-of：仅决策日前已出结果可见）
⑤ Risk Gate（code/risk_gate/）
   ↓ PASS / WARNING / REJECT + risk_reasons
⑥ White-box Rule Engine（code/decision/rule_engine.py）
   ↓ BUY_DA / SELL_DA / NO_TRADE
⑦ Human Confirmation（human_review_status：PENDING/APPROVED/REJECTED/OVERRIDDEN）
   ↓
⑧ Post-trade Review（真实 RTPD；UNFORESEEABLE_EVENT 分类）
```

**模块依赖关键点**：
- `canonical.py` 是唯一特征实现（app/train 复用）；`feature_schema.json` 记录 X/label 与可用性。
- `model_v2.py` 只消费 canonical X（决策时点可见），输出统一预测 schema。
- `risk_gate` 依赖 `agent/evidence/time_gate.py`（只放行 eligible evidence）与 `case_library`。
- `rule_engine` 消费模型输出 + gate 结果 → 三态。
- `backtest_v2.py` 对任意预测 CSV 通用，策略可插拔。

## 4. 核心设计决策

1. **业务口径（已冻结）**：`docs/business_contract.md` + `docs/market_timeline.md`：决策 cutoff = **D-1 日 10:00 PT**（官方 BPM）；D+1 的 DA/RTPD/Return 只能作 label。
2. **As-of Decision-Time Evidence（V0.2 硬约束）**：任何证据 `published_at ≤ decision_cutoff` 才能进决策；`decision_eligible` 程序计算、LLM 禁止判断；post-decision 只进复盘。
3. **单一 Production Predictive Model**（CatBoost 方向 + LightGBM 幅度）：只输出 `expected_return / prob / confidence / uncertainty`，**不出 BUY/SELL**。
4. **模型定位重构**：Rule = benchmark / 回测基线；Interpretable = 开发与验证工具（特征方向 sanity check）；CatBoost = 主要预测模型。
5. **独立白盒 Risk Gate**：不预测方向，只判定"是否放行"；11 条可解释规则（V0.1 empirical guardrail 标注 `DATA-DERIVED TEMPORARY GUARDRAIL` 并经 val+test 双窗口过拟合检验）。
6. **Shared Model + Node Feature**（默认不做一节点一模型）；ELCA 作 cold-start 单独评估。

## 5. 能力边界（客观如实）

### ✅ 已实现（V0.2）
- 无泄漏 canonical 数据层（49,210 行，Leakage Guard）
- 单一 Predictive Model（`predictions_v2.csv`，hour 特征经验证保留）
- Agent Evidence + **Evidence Time Gate**（6 测试）
- Case Library（18 case）+ Explanation Decision Card（12 张）
- 独立 Risk Gate（11 规则 + 47 测试 + 过拟合检验）
- White-box Rule Engine（9 规则，可配置/可测试/带版本）
- Signal Backtest（可插拔 4 策略）+ 架构 Diff V0.1→V0.2
- 全套文档（业务契约/时间线/泄漏审计/特征可用性/阶段报告/本架构）

### ❌ 未实现（V0.2 边界内）
- **Agent 联网检索**：真实外部数据源未接入（evidence 当前全 `UNCERTAIN`）
- **真实 as-of weather forecast archive**：`[当前缺失]`；只用历史天气滞后 + load_2da_forecast
- **概率/尾部校准**：confidence/uncertainty 是显式组合量、非校准概率；需 quantile/EVT
- **真实 Convergence Bidding PnL**：当前仅为 Signal Backtest（缺 bid/quantity/award/settlement/fees）
- **自动真实下单 / 仓位优化**：明确不做（人工确认）

### 🔜 留待未来
1. 补外部数据（可再生能源出力 → 负荷修正 → 停机/阻塞 → 本地燃气价），识别"深度负电价时段"
2. 尾部/分位校准（quantile/EVT）替换失效 confidence
3. 数据到位后接入 Agent 信息检索到 Evidence Layer（届时 evidence 才有非 UNCERTAIN 输出）

## 6. 当前真实结论

- **方向信号真实但微弱**（AUC 0.57–0.65）；盈利主要来自市场漂移而非预测。
- **Risk Gate 的价值 = 风险护栏**：test 上移除 −149k 系统负期望交易、maxDD −138k→−3k、worst −2,216→−208；**但 coverage 降至 5.8%、不创造 alpha（PnL 未改善）**。
- **V0.2 的真实价值在可解释/可审计/防穿越**，不在 PnL 提升——详见 `docs/v0.2_lead_summary.md` §4。

## 7. 文档索引（docs/）

| 文档 | 内容 |
|---|---|
| `business_contract.md` | 业务定义与铁律（冻结口径） |
| `market_timeline.md` | 【V0.2】官方时间线（cutoff 10:00） |
| `feature_availability_matrix.md` | 逐特征可用性矩阵 |
| `leakage_report.md` | 数据泄漏修复与 Leakage Guard |
| `Architecture.md`（本文件） | 架构与能力边界（V0.2） |
| `FeatureEngineering.md` / `Model.md` | 特征工程 / 三层模型（V0.1 基线说明） |
| `Backtest.md` / `DecisionPipeline.md` | 回测引擎 / 8 步决策流水线（V0.2） |
| `v0.2_backtest.md` | V0.2 Signal backtest 对比 |
| `v0.2_architecture_diff.md` | V0.1→V0.2 KEEP/MODIFY/REMOVE/ADD |
| `v0.2_lead_summary.md` | V0.2 最终整合与价值回答 |
| `risk_gate_v02_rules.md` | Risk Gate 11 条规则文档 |
| `stage3/` | 极端事件/分层/Risk Gate 分析 |
