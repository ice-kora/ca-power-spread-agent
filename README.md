# CAISO 价差交易决策辅助 · V0.2 白盒决策流水线

预测 CAISO 日前(DA)与实时(RTPD)价差 `Return = DA − RTPD` 的方向与幅度，辅助 **SELL_DA / BUY_DA / NO_TRADE** 交易决策。

V0.2 重构为**白盒、可审计、可解释的决策流水线**：单一 **Production Predictive Model** → Agent Evidence → **Evidence Time Gate**（防穿越）→ Case Library → **Risk Gate** → **White-box Rule Engine** → 人工确认 → 复盘。

> 状态：V0.2（白盒决策流水线）。**模型定位**：线上只跑一个 Predictive Model（`model_v2.py`，只输出预测量，不直接出 BUY/SELL）；Rule Baseline = benchmark / 回测基线；Interpretable = 开发与验证工具（特征方向 sanity check）；两者**不参与线上投票**。决策 cutoff 已统一为官方 **D-1 日 10:00 PT**（DAM Market Close；13:00 是 DA 结果发布 = label 可见时点）。

## 目录结构

```
CA-电力交易预测/
├── 价格数据/           # 3节点 DA/RTPD/DARTPD Return（小时级）
├── load_*_TAC_*.csv    # 系统负荷预测/实际
├── zone_weather_hourly.csv
├── 节点位置.xlsx
├── agent/              # 【V0.2】白盒 Agent 模块
│   ├── evidence/       #   证据 schema + Evidence Time Gate + 测试
│   ├── case_library/   #   历史案例库（18 case，只检索不决策）
│   └── explanation/    #   决策卡片生成（Decision Card）
├── code/
│   ├── canonical.py    # 单一特征实现 + 防泄漏 + Leakage Guard
│   ├── model_v2.py     # 【V0.2】单一 Production Predictive Model
│   ├── model_c.py      # V0.1 三层模型（Rule/Interpretable/CatBoost，保留作 benchmark/对照）
│   ├── risk_gate/      # 【V0.2】独立 Risk Gate（11 规则 + 校准 + 测试）
│   ├── decision/       # 【V0.2】白盒 Rule Engine（三态，可配置可测试）
│   ├── backtest.py     # V0.1 回测引擎
│   ├── backtest_v2.py  # 【V0.2】Signal backtest（可插拔策略）
│   ├── backtest_v2_ab.py # 【V0.2.1】Agent Evidence A/B + PnL 逐笔对账（Agent E）
│   ├── analysis/       # 可复现分析脚本（offline validation）
│   └── data/ models/ backtest_outputs/   # 生成物（gitignore）
├── docs/               # 架构与评审文档（见下）
└── README.md / CLAUDE.md
```

## 快速开始

```bash
# 数据层：原始数据 → canonical dataset（无泄漏）
python code/read_data.py        # master.csv
python code/canonical.py        # canonical.parquet + feature_schema.json

# 建模：单一 Production Predictive Model（只输出预测量）
python code/model_v2.py         # predictions_v2.csv + model_v2_notes.json

# 回测：Signal backtest（V0.2，可插拔策略）
python code/backtest_v2.py      # backtest_v2_summary.json

# Agent 层：Evidence Time Gate 测试 / 案例库 / 决策卡
python agent/evidence/test_time_gate.py
python agent/case_library/init_cases.py
python agent/explanation/cards.py
```

## 文档索引（docs/）

| 文档 | 内容 |
|---|---|
| `Architecture.md` | 【V0.2】架构总览 + 能力边界 |
| `DecisionPipeline.md` | 【V0.2】8 步决策流水线 + Evidence Time Gate + 规则清单 |
| `market_timeline.md` | 【V0.2】官方时间线（cutoff 10:00，13:00 = DA 结果发布） |
| `business_contract.md` | 业务定义与铁律（决策时点、Return、三态） |
| `feature_availability_matrix.md` | 逐特征可用性矩阵 |
| `leakage_report.md` | 泄漏修复与 Leakage Guard |
| `FeatureEngineering.md` | 特征工程（X/label、防泄漏） |
| `Model.md` | V0.1 三层模型（benchmark 基线说明） |
| `Backtest.md` | V0.1 回测引擎与 Risk Gate 结果 |
| `risk_gate_v02_rules.md` | Risk Gate 11 条规则文档 + Rule Engine 规格 |
| `v0.2_backtest.md` | V0.2 Signal backtest 对比 |
| `v0.2.1_evidence_ab.md` | 【V0.2.1】Agent Evidence A/B（无证据 vs 极端状态证据，逐笔对账） |
| `v0.2.1_pnl_reconciliation.md` | 【V0.2.1】PnL 逐笔对账（Production Signal Set / Calibration Candidate Set 两口径） |
| `v0.2_architecture_diff.md` | V0.1→V0.2 KEEP/MODIFY/REMOVE/ADD |
| `v0.2_lead_summary.md` | V0.2 最终整合与价值回答 |
| `stage3/` | 极端事件/分层分析（V0.1 阶段三结论，历史） |

> **PnL 对账口径（V0.2.1 起，团队统一标注）**：
> - **Production Signal Set** = Predictive Model only 在 test 的完整候选（\|er\|≥5 & conf≥0.2）。
> - **Calibration Candidate Set** = Risk Gate 校准/验证时的全候选（val + test）。
> 两者**不混写**，对账恒等式 `Original PnL == Accepted PnL + Rejected PnL` 逐笔成立
> （见 `v0.2.1_pnl_reconciliation.md`；生成物 `code/data/backtest_v2_ab.json`）。

## 核心结论（V0.2）

- **单一生产模型**：`model_v2.py` 只输出 `expected_return / prob_positive / prob_negative / confidence / uncertainty`，**不直接输出 BUY/SELL**；交易动作由白盒 Rule Engine 判定。
- **三层模型不参与线上投票**：Rule / Interpretable / CatBoost 仅作 V0.1 benchmark 与 offline validation；"三模型一致性"残留已从 Production Explanation 与正式 Risk Gate 移除。
- **Evidence Time Gate（防穿越硬约束）**：任何证据必须 `published_at <= decision_cutoff`（D-1 10:00 PT）才进决策层；post-decision 证据只进复盘。
- **Risk Gate = 护栏**：把 test 上系统性负期望交易移除（test maxDD −138k → −3k、worst −2,216 → −208），但**不创造 alpha**（PnL 未改善）。
- **Agent Evidence A/B（V0.2.1）**：真实 as-of 极端状态证据（离线持久性代理，`directional_effect=UNCERTAIN`）经 Time Gate + Risk Gate 新规则 R12 消费后，ZP26 test 上 PnL −820 → +1,113（净 +1,933，避免尾部亏损 5,162 / 误伤盈利 3,229）；但改进集中在少数极端日、阈值未能在 val 校准、证据为持久性代理而非真实 GFS 预报 → 判定 **YES_WITH_LIMITS**（非 YES）。ELCA A/B 无差异（Gate 已全关）。
- **诚实边界**：盈利主要来自市场漂移而非预测；test 窗口仅 65 天；confidence/uncertainty 未校准（需尾部/分位校准）；真实外部数据源未接入（Evidence 全 UNCERTAIN）。

## 环境

Python 3 + pandas/numpy/openpyxl/lightgbm/catboost/matplotlib/flask。价格 xlsx 需 openpyxl ≥3.1.5（或直接 openpyxl 读取）。
