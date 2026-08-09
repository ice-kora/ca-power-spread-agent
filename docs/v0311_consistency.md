# Golden Case Consistency（FULL vs DEMO）

> 由 `check_demo_consistency.py` 生成（Agent A · Demo Artifacts）。比较 5 个 Golden Cases 在
> **FULL**（完整数据 `code/data/*`）与 **DEMO**（`demo_artifacts/` 真实历史最小切片）两种模式下的
> 决策结果一致性。交易核心冻结：只做数据切片，不改任何模型 / 规则 / 阈值 / PnL。

## 总体结论

**Golden Case Consistency: 5/5 PASS**

- 比较字段（9 项）：`expected_return` / `direction_probability` / `model_signal_strength`
  / `risk_gate_result` / `rule_engine_result` / `final_recommendation` / `actual_da` /
  `actual_rtpd` / `pnl`（数值容差 RTOL=1e-06 / ATOL=1e-06）。
- 证据口径：两模式均用 `StaticEvidenceAdapter([])`（确定性、离线）。GFS 证据 severity=INFO /
  directional_effect=UNCERTAIN，不参与 gate/rule；`evidence` 不打包进 demo 切片，网络可用性
  **不影响**所比较字段。
- 展示类字段（非比较项）：`top_features` 的特征统计 z-score 属解释性展示（非 SHAP），
  DEMO 用 90 天前置窗口计算，可能与 FULL（全历史）略有差异；**不影响**任何决策字段。
- manifest 核验：`data_mode=="DEMO"`、`contains_mock==false`、`contains_future_outcome==true`
  → **PASS**。

## 案例表（FULL 值；DEMO 与之一致）

| 案例 | decision_date node H | expected_return | direction_prob | signal_strength | risk_gate | rule_engine | final | actual_da | actual_rtpd | pnl | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **B** | 2026-07-16 CONTROLX_1_N001 H3 | 59.7937 | 0.3595 | 0.2197 | PASS | SELL_DA · EXPECTED_RETURN_POSITIVE | SELL_DA | -48.4863 | -2,299.7905 | 2,251.3042 | PASS |
| **C1** | 2026-07-08 CONTROLX_1_N001 H2 | -76.1581 | 0.6533 | 0.3600 | REJECT · BUY_ON_POSITIVE_DRIFT_NODE · EXTREME_TAIL_NODE · SIMILAR_TAIL_LOSS_CASE | NO_TRADE · RISK_GATE_REJECTED | NO_TRADE | -149.0314 | -2,365.3089 | 0.0000 | PASS |
| **C2** | 2026-07-10 SNLNDRO_1_N001 H10 | -3.3065 | 0.4917 | 0.1356 | WARNING · LOW_CONFIDENCE · EXPECTED_RETURN_TOO_SMALL | NO_TRADE · EXPECTED_RETURN_TOO_SMALL | NO_TRADE | 27.6677 | 19.2588 | 0.0000 | PASS |
| **D** | 2026-07-20 SNLNDRO_1_N001 H20 | 6.0431 | 0.6265 | 0.2580 | WARNING · MODEL_UNSTABLE | SELL_DA · EXPECTED_RETURN_POSITIVE | SELL_DA | 80.1450 | 139.2050 | -59.0600 | PASS |
| **E** | 2026-07-08 CONTROLX_1_N001 H2 | -76.1581 | 0.6533 | 0.3600 | REJECT · BUY_ON_POSITIVE_DRIFT_NODE · EXTREME_TAIL_NODE · SIMILAR_TAIL_LOSS_CASE | NO_TRADE · RISK_GATE_REJECTED | NO_TRADE | -149.0314 | -2,365.3089 | 0.0000 | PASS |

## 逐案例明细

#### Case B · B · SELL 盈利（彩票右尾）
- 参数：`2026-07-16 CONTROLX_1_N001 H3`
- DEMO 无 MOCK（is_mock 特征/证据均为 False）：PASS
- 相似案例（top cases case_id）FULL == DEMO：PASS
- 9 项比较字段全部一致。

#### Case C1 · C1 · NO_TRADE 避险（RiskGate 成功）
- 参数：`2026-07-08 CONTROLX_1_N001 H2`
- DEMO 无 MOCK（is_mock 特征/证据均为 False）：PASS
- 相似案例（top cases case_id）FULL == DEMO：PASS
- 9 项比较字段全部一致。

#### Case C2 · C2 · NO_TRADE 弱信号
- 参数：`2026-07-10 SNLNDRO_1_N001 H10`
- DEMO 无 MOCK（is_mock 特征/证据均为 False）：PASS
- 相似案例（top cases case_id）FULL == DEMO：PASS
- 9 项比较字段全部一致。

#### Case D · D · 模型 SELL 但错（诚实展示）
- 参数：`2026-07-20 SNLNDRO_1_N001 H20`
- DEMO 无 MOCK（is_mock 特征/证据均为 False）：PASS
- 相似案例（top cases case_id）FULL == DEMO：PASS
- 9 项比较字段全部一致。

#### Case E · E · Evidence 被 Time Gate 拒（同 C1 参数）
- 参数：`2026-07-08 CONTROLX_1_N001 H2`
- DEMO 无 MOCK（is_mock 特征/证据均为 False）：PASS
- 相似案例（top cases case_id）FULL == DEMO：PASS
- 9 项比较字段全部一致。


## DEMO ≠ MOCK（明确声明）

- **DEMO**：`demo_artifacts/` 是**真实历史记录**的子集（Golden Cases 决策所需行），保留全部
  PnL / prediction / decision 数值；决策链照常运行，可给出真实 BUY_DA / SELL_DA / NO_TRADE 推荐。
- **MOCK**：编造/占位数据，`is_mock=True`，永不参与真实推荐（Evidence Time Gate R7 硬隔离）。
- 本一致性核验同时断言：每个 DEMO 决策对象的 `top_features` 与 `evidence.eligible` 中
  **不存在任何 is_mock 项**。

## 复现

```bash
# 1. 在完整数据机上抽取 demo 切片 + 跑一致性（生成本文档）
python build_demo_artifacts.py --check

# 2. 单独跑一致性
python check_demo_consistency.py

# 3. clean clone（无 code/data）直接以 DEMO 模式启动
python prepare_mvp.py            # 显示 DATA MODE = DEMO
python mvp_web.py
```
