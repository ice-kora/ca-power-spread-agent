# 特征工程（V0.1 Baseline）—— Canonical Dataset

> 单一实现：`code/canonical.py` ｜ 数据集：`code/data/canonical.parquet` ｜ Schema：`code/data/feature_schema.json` ｜ 特征版本：canonical_v1
> 本文为 V0.1 Baseline 架构评审用；字段名保留英文，正文中文。

## 1. 行语义与决策时点

- **一行 = (node, target_date, hour)**：节点 × 交付日 × 小时，共 49,210 行（2024-01-01 .. 2026-08-05）。
- **决策日** decision_date = target_date − 1；**决策截止** decision_cutoff = decision_date 10:00 PT（DAM Market Close / bid cutoff，官方 BPM；13:00 是 DA 结果发布 = label 可见时点）。
- 决策时点可见的**历史/静态信息 → X（38 特征）**；D+1 的实际 DA / RTPD / Return **仅进 label 区**，绝不进入 X。

## 2. X 区：38 个决策时点可见特征

| 类别（数） | 字段（业务含义） |
|---|---|
| 时间/节点（7） | `hour`（小时 1..24）· `node`（节点）· `zone`（区域 NP15/SP15/ZP26）· `dow`（星期）· `month`（月）· `is_holiday`（US 联邦假日）· `solar_flag`（日照窗口 10–16 时） |
| 价格滞后（9） | `da_lag1/rtpd_lag1/spread_lag1`（T−2 交付日 DA / RTPD / 价差）· `da_lag2/rtpd_lag2/spread_lag2`（T−3）· `da_lag7/rtpd_lag7/spread_lag7`（T−8） |
| 价差滚动统计（6） | `spread_mean7/spread_std7`（T−2..T−8 同 hour 7 日）· `spread_mean14/spread_std14`（14 日）· `spread_mean30/spread_std30`（30 日） |
| 日级统计（7） | `spread_day_std_lag1/spread_day_range_lag1/spread_day_max_lag1`（T−2 当天价差 std / 极差 / 峰值）· `da_day_mean_lag1/rtpd_day_mean_lag1/spread_day_mean_lag1`（T−2 当天均值）· `load_actual_day_mean_lag1`（T−2 当天实际负荷均值）——均**广播到 24h** |
| 负荷（3） | `load_actual_lag1`（T−2 实际负荷）· `load_2da_forecast`（target_date 日前负荷预测 2DA）· `load_peak_flag`（当日负荷峰值时点标记） |
| 天气滞后（3） | `t2m_lag1/ssrd_lag1/wind100_lag1`（T−2 同 hour 气温 / 太阳辐射 / 100m 风速，历史滞后，决策时确定可得） |
| 节点联动（3） | `peer_spread_lag1/peer_da_lag1/peer_rtpd_lag1`（同 zone 关联节点 peer 的 T−2 价差 / DA / RTPD；ELCA 无 peer → NaN） |

> 注：`load_actual_day_mean_lag1` 归入日级统计（schema 口径）；`load_2da_forecast` / `load_peak_flag` 状态为 ASSUMED_AVAILABLE。

## 3. Label 区：4 列（隔离，不进 X）

| 字段 | 定义 | available_at |
|---|---|---|
| `actual_da` | target_date 当日 DA 清价（$/MWh） | T−1 13:00（DA 结果发布，非 bid cutoff） |
| `actual_rtpd` | target_date 当日 RTPD | T 日深夜（实时市场） |
| `actual_return` | actual_da − actual_rtpd（契约冻结 = DARTPD Return） | 两者齐备后 |
| `direction` | sign(actual_return)：+1 / −1 / 0 | 两者齐备后 |

## 4. 防泄漏设计

- **滞后统一 T−2 起**：DA(T−1) 虽已于 T−2 13:00 出清（DA 结果发布，非 bid cutoff），但 RTPD(T−1) 决策日深夜才完整 → lag1=T−2、lag2=T−3、lag7=T−8，宁保守不泄漏。
- **rolling 锚定 T−2**：滚动统计在"交付日对齐"宽表上 `rolling(w).mean()/std().shift(2)`，窗口最新一天 = T−2（覆盖 T−2..T−(w+1)），同 hour 对齐。
- **日级特征正确广播到 24h**：以 date 为键 merge 广播，修复旧实现的幽灵 hour=0 行与单 hour 落值错位。
- **天气 `*_next` 禁用**：`t2m_next/ssrd_next/wind100_next` 为目标日实际天气（ERA5 再分析 + 合成段），决策时不可得（穿越），默认禁用；只保留历史滞后 `*_lag1`。
- **Leakage Guard 自动化**：38 个 X 特征逐一断言 `available_at ≤ decision_cutoff`，`verify()` 全 PASS；X ∩ label = ∅。
- **label 只从真实 DA/RTPD 创建**：`actual_return` 为 NaN 的行剔除（38 行），无 NaN→0 污染。
- **单一实现**：`build_row_features()` 为 train / 推理唯一特征构造函数，消除 features.py 双实现漂移。

## 5. 时间切分（严格按 decision_date，无随机 split）

| split | decision_date 区间 | 行数 |
|---|---|---|
| train | 2025-04-02 .. 2025-12-31 | 15,311 |
| val | 2026-01-01 .. 2026-05-31 | 7,244 |
| test | 2026-06-01 .. 2026-08-05 | 4,678 |

- 三节点合计 labeled 27,233 行；其余 21,977 行在切分区间外（2024 暖机段）不进训练/评估。
- ELCA 冷启动节点用独立区间（train 2026-03-03 起、无 val 样本），单独评估。

## 6. 已知局限 / 待确认

- **`load_2da_forecast` / `load_peak_flag` 发布时刻 ASSUMED**（低度不确定）：若 2DA 发布晚于决策点需重审；原始 CSV 仅到 2026-07-09，8 月 test 值来自 master 更新源。
- **天气时区 naive**：`valid_pt` 未做 America/Los_Angeles 换算 → 历史滞后特征存在小时对齐不确定性（不影响泄漏判断）。
- `peer_*` 对 ELCA 恒为 NaN（SP15 无同区节点）：解释模型中位数插补、树模型按缺失处理。
- 天气滞后特征 2026-07-01..07-29 部分日缺失（三节点共 216 个 test 行）。
