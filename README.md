# CA-ISO 电价价差预测

预测 CA-ISO（加州电力市场）次日各节点、各小时的 **DA 日前价、RTPD 实时价** 与 **价差 spread = DA − RTPD**，并据此给出"买 / 卖 / 观望"交易建议。

## 业务背景
电力交易规则：T-1 日在日前市场（DA）买卖 1 度电，T 日在实时市场（RTPD）反向平仓，赚取价差。
- 价差为负（RTPD 更高）→ **日前买、实时卖**
- 价差为正（DA 更高）→ **日前卖、实时买**
- 把握不足 → **观望**

## 快速开始
```bash
pip install flask lightgbm joblib pandas numpy openpyxl matplotlib
python code/app.py          # 启动网页 -> 浏览器打开 http://127.0.0.1:5000
```
网页输入「节点 + 目标日期」→ 输出次日 24h 预测曲线、置信区间与买卖建议；选择已过去日期显示预测 vs 真实对比。

## 数据管线
```
read_data.py  → master.csv           # 对齐价格/负荷/天气为长表
features.py   → features.parquet     # 特征 + 时间切分(train/val/test) + D-1 防泄漏
train.py      → 模型(pkl) + test_predictions.csv   # LightGBM 分位数(q10/50/90) + DA/RTPD 点预测
evaluate.py   → 指标表 + arb_curve.png + evaluation_summary.json
app.py        → Flask 网页（加载模型实时推理）
```

## 关键设计
- **单边策略（核心）**：实测发现模型对方向信号**不对称**——预测"卖"（正价差）时准确率 71%、预测"买"（负价差）时仅 58%，而负价差方向的错单是套利亏损主因。因此只在 `P(价差>0) > 0.5` 且近 7 日价差波动 `spread_std7 ≤ 120` 时交易（**卖**：日前卖、实时买），其余一律观望。
- **训练集扩大**：train 从 2025-10 扩至 **2025-04**（天气起始），样本 4600→13700，让模型见过更多市场状态，提升跨时段稳健性（val 期由大亏转盈）。
- **方向分类（CatBoost）+ 特征增强**：CatBoost 分类器直接输出 `P(价差>0)`（实测优于 LightGBM，收益 +30%）；特征含节点间联动、D-1 日内形态、14/30 日滚动、负荷日均值。
- **分位数回归仅用于展示**：DA/RTPD/spread 曲线与置信带。
- **防泄漏**：滞后特征只用 D-1 及更早价格；按时间切分；2DA 负荷与天气视为预报值。
- **数据边界**：可预测目标日期至 2026-08-07。

## 模型结果（测试集 2026-06-01 ~ 08-05，主模型 CatBoost）
| 指标 | 值 |
|---|---|
| 价差方向准确率（整体） | **63.9%**（val 65.3%） |
| 方向准确率（实际交易时段） | **68.8%**（val 71.3%） |
| 决策占比 | sell ~33% / hold ~67%（单边，无买） |
| 模拟套利收益 | **val +6884 / test +2243**（两时段均盈利） |
| 最大单笔亏损 | **-171**（单边大幅压降风险，原 -2251） |
| spread q50 MAE / RMSE | 109.1 / 251.5 |

## 已知局限（如实说明）
1. **套利利润偏薄**：保守单边策略下 val +6884 / test +2243（日均 ~35–100），相对价差波动量级不大——价差本身难预测，方向 63.9% 带来的收益空间有限。
2. **单边牺牲双边机会**：只做"卖"（正价差）方向，负价差方向的套利机会（可能很大）被放弃。
3. **交易集中在 SNLNDRO**：单边策略下 CONTROLX 几乎不交易（模型对其正价差信号弱），实际交易大多落在 SNLNDRO。
4. **跨时段仍有漂移**：val/test 收益均为正但绝对值不同（6884 vs 2243），市场状态变化仍影响表现。
5. **ELCAJNGT 仅参考**：已单独训练但不可靠（方向 62.3%、套利 -8833）——数据仅 2026-03 起、训练仅 3 个月、无同 zone peer、价差幅度大。**主策略只采用 ZP26 两节点**（val +6884 / test +2243）。
6. **决策阈值基于回测校准**（prob>0.5、std7≤120），实盘需再校准。
7. **天气按预报值处理**（用户确认为预测）；若确认是实测，需把 D+1 天气改为滞后特征（改 features.py/app.py 各一处）。

## 目录
```
code/read_data.py / features.py / train.py / evaluate.py / app.py
code/templates/index.html      # 网页前端
code/data/                     # master.csv, features.parquet, test_predictions.csv, 评估产物
code/models/                   # CatBoost 方向分类器 + LightGBM 分位数回归 (pkl) + feature_cols.json
```
