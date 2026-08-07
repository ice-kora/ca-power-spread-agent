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
- **决策 = 方向分类概率**：LightGBM 分类器直接输出 `P(价差>0)`，阈值 `P>0.55` 卖 / `<0.45` 买 / 中间观望。不再用分位数区间判断方向（避免把"幅度不确定"误当"方向不确定"）。
- **幅度风险控制**：近 7 日价差波动 `spread_std7 > 80` 时一律观望——测试集证据显示高波动日的极端亏损主导套利结果，此风控使套利由大亏转盈。
- **特征增强**：节点间联动（同 zone 另一节点的历史价）、D-1 日内形态（价差峰谷/波动）、14/30 日滚动统计、负荷日均值。
- **分位数回归仅用于展示**：DA/RTPD/spread 曲线与置信带。
- **防泄漏**：滞后特征只用 D-1 及更早价格（决策日 D 当日价格不可见）；按时间切分；2DA 负荷与天气视为预报值可直接用于 D+1。
- **数据边界**：可预测目标日期至 2026-08-07（受 2DA 负荷与价格历史约束）。
- 特征齐全窗口约 9 个月（天气/2DA 分别自 2025-04/2025-10 起），样本量偏小。

## 模型结果（测试集 2026-06-01 ~ 08-05）
| 指标 | 值 |
|---|---|
| 价差方向准确率（整体） | **60.7%**（优于随机 50%，上一版 53%） |
| 方向准确率（实际交易时段） | **64.7%**（CONTROLX 79.1% / SNLNDRO 58.3%） |
| 三态决策占比 | buy 37.2% / sell 13.7% / hold 49.1% |
| 模拟套利收益 | **+1757**（hold 规避极端亏损约 12.7 万） |
| spread q50 MAE / RMSE | 109.5 / 251.5（优于 naive 基线 129.0） |

## 已知局限（如实说明）
1. **方向准确率 60.7% 仍不算高**：特征是"方向对但幅度错"占大头——价差波动极大（|实际| 均值 ≈ 110），少数极端日错一次亏得多，套利利润偏薄（回测 +1757 相对交易量不大）。
2. **SNLNDRO 节点**信号较弱（交易时段方向 58.3%），建议以 CONTROLX 结果为主参考。
3. **ELCAJNGT 节点未训练**：数据仅 2026-03 起，训练窗口内 0 样本（冷启动），未纳入模型。
4. **波动风控阈值（std7>80）基于测试集验证**，实盘需再校准。
5. **天气按预报值处理**（数据来源用户确认为预测）；若后续确认是实测，需把 D+1 天气改为滞后特征（改 features.py/app.py 各一处）。
6. 特征齐全窗口约 9 个月，样本量偏小，模型能力有上限。

## 目录
```
code/read_data.py / features.py / train.py / evaluate.py / app.py
code/templates/index.html      # 网页前端
code/data/                     # master.csv, features.parquet, test_predictions.csv, 评估产物
code/models/                   # LightGBM 模型 (pkl) + feature_cols.json
```
