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
- **主目标 = 价差分位数**（非分别预测 DA/RTPD 再相减），消除误差叠加，输出置信区间支撑"观望"。
- **防泄漏**：滞后特征只用 D-1 及更早价格（决策日 D 当日价格不可见）；按时间切分；2DA 负荷与天气视为预报值可直接用于 D+1。
- **数据边界**：可预测目标日期至 2026-08-07（受 2DA 负荷与价格历史约束）；更远期 2DA 特征缺失（NaN，LightGBM 原生处理）。
- 特征齐全窗口约 9 个月（天气/2DA 分别自 2025-04/2025-10 起），样本量偏小。

## 模型结果（测试集 2026-06-01 ~ 08-05）
| 指标 | 值 |
|---|---|
| spread q50 MAE / RMSE | 108.8 / 252.8（优于 naive 基线 129.0） |
| 价差方向准确率（整体） | 53%（CONTROLX 67%、SNLNDRO 39%） |
| 三态决策占比 | buy 0.1% / sell 0% / hold 99.9% |
| 模拟套利（三态策略） | 4 笔交易全对，+471；hold 避开全交易 -8.3 万亏损 |

## 已知局限（如实说明）
1. **价差信号弱**：特征（负荷/天气/历史价）与实际价差相关性低，方向准确率仅略高于随机；模型预测价差趋近 0（均值回归），而实际价差波动极大（|实际| 均值 ≈ 110）。
2. **决策偏保守**：99.9% 观望正是模型"把握不足就不动"的诚实表现——全交易基准亏损 8.3 万，证明强行多交易只会更亏。
3. **SNLNDRO 节点**方向准确率 39%（低于 50%），信号不可靠，建议以 CONTROLX 结果为主参考。
4. **ELCAJNGT 节点未训练**：数据仅 2026-03 起，训练窗口内 0 样本（冷启动），未纳入模型。
5. **天气按预报值处理**（数据来源用户确认为预测）；若后续确认是实测，需把 D+1 天气改为滞后特征（改 features.py/app.py 各一处）。

## 目录
```
code/read_data.py / features.py / train.py / evaluate.py / app.py
code/templates/index.html      # 网页前端
code/data/                     # master.csv, features.parquet, test_predictions.csv, 评估产物
code/models/                   # LightGBM 模型 (pkl) + feature_cols.json
```
