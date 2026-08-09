# CAISO 价差交易决策辅助 · V0.1 Baseline

预测 CAISO 日前(DA)与实时(RTPD)价差 `Return = DA − RTPD` 的方向与幅度，辅助 **SELL_DA / BUY_DA / NO_TRADE** 交易决策。V0.1 是**工程冻结基线**：无泄漏数据层 + 三层可对照模型 + 白盒 Risk Gate。

> 状态：V0.1 Baseline（工程冻结，暂不开发新功能）。当前结论：方向信号真实但微弱，Risk Gate 能拦截灾难性尾部（风控价值成立）但暂不创造 alpha。详见 `docs/`。

## 目录结构

```
CA-电力交易预测/
├── 价格数据/           # 3节点 DA/RTPD/DARTPD Return（小时级）
├── load_*_TAC_*.csv    # 系统负荷预测/实际
├── zone_weather_hourly.csv
├── 节点位置.xlsx
├── code/
│   ├── read_data.py    # 数据对齐
│   ├── canonical.py    # 单一特征实现 + 防泄漏 + Leakage Guard
│   ├── model_c.py      # 三层模型（Rule/Interpretable/CatBoost）
│   ├── backtest.py     # 严格 as-of 回测 + Risk Gate
│   ├── analysis/       # 可复现分析脚本
│   └── data/ models/ backtest_outputs/   # 生成物（gitignore）
├── docs/               # 架构与评审文档（见下）
└── README.md / CLAUDE.md
```

## 快速开始

```bash
# 数据层：原始数据 → canonical dataset（无泄漏）
python code/read_data.py        # master.csv
python code/canonical.py        # canonical.parquet + feature_schema.json

# 建模：三层模型 → 预测 CSV
python code/model_c.py          # predictions_{rule,interpretable,catboost}.csv

# 回测：严格 as-of + Risk Gate
python code/backtest.py         # backtest_outputs/
```

## 文档索引（docs/）

| 文档 | 内容 |
|---|---|
| `business_contract.md` | 业务定义与铁律（决策时点、Return、三态） |
| `feature_availability_matrix.md` | 逐特征可用性矩阵 |
| `leakage_report.md` | 泄漏修复与 Leakage Guard |
| `Architecture.md` | 架构总览 + 能力边界（已实现/未实现/未来） |
| `FeatureEngineering.md` | 特征工程（X/label、防泄漏） |
| `Model.md` | 三层模型与指标 |
| `Backtest.md` | 回测引擎与 Risk Gate 结果 |
| `DecisionPipeline.md` | 端到端决策流水线 |
| `stage3/` | 极端事件/分层分析 + 阶段三结论 |

## 核心结论（V0.1）

- **无泄漏基线**：幽灵行/NaN label/天气穿越全部修复，Leakage Guard 自动化防护。
- **三层模型**：方向准确率 58–66%、AUC 0.57–0.64（真实但微弱）。
- **白盒 Risk Gate**：test 上 PnL −130k→+962、maxDrawdown −148k→−0.65k、worst trade −2,216→−113；反事实拦 72% 的 Top 亏损。
- **诚实边界**：盈利主要来自市场漂移而非预测；Gate 拦灾难但不创造 alpha；进 Agent 阶段前需先补"可再生能源出力/负荷修正"等外部数据。
- **已知局限**：confidence 未校准（需尾部/分位校准）、天气 D+1 因穿越被禁用、ELCA 冷启动无价值。

## 环境

Python 3 + pandas/numpy/openpyxl/lightgbm/catboost/matplotlib/flask。价格 xlsx 需 openpyxl ≥3.1.5（或直接 openpyxl 读取）。
