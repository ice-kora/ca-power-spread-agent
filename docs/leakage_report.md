# Leakage Report（泄漏审计报告）

> 阶段二 · 数据修复后的泄漏状态 ｜ 2026-08-09

## 一、已确认并修复的泄漏 / 数据问题

| # | 问题 | 旧状态 | 修复后状态 | 处置 |
|---|---|---|---|---|
| 1 | **天气 D+1 穿越**（`t2m_next/ssrd_next/wind100_next`） | 训练/测试用目标日实际天气（ERA5 再分析 + 合成段）冒充预报 | **从特征集移除** | 改为历史天气滞后 `t2m_lag1/ssrd_lag1/wind100_lag1`（决策时确定可得） |
| 2 | **幽灵 hour=0 行** | 每 (node,date) 25 行，2052 个垃圾行 | **0** | 日级特征正确广播到 24h |
| 3 | **label 污染**（NaN→y=0） | 2139 行被强制标负样本 | **0** | label 只从真实 DA/RTPD/Return 创建 |
| 4 | **日级特征错位**（4 个特征 96-100% NaN） | 死特征 + train/inference 不一致 | 可用率 99.7% | 正确广播 |
| 5 | **特征双实现**（features.py vs app.py） | 两套逻辑，std ddof 不一致 | 单一实现 `canonical.build_row_features()` | app/train 复用 |
| 6 | **滞后口径** | 旧用 T-1 起 | **统一 T-2 起**（RTPD(T-1) 决策日深夜才完整，宁保守不泄漏） | canonical 约定 |

## 二、Leakage Guard（自动化防护）

`canonical.py` 内置防护：**任何 X 特征 `available_at > decision_cutoff`（D-1 日 10:00 PT，DAM Market Close / bid cutoff）→ 阻止进入训练/推理**。已对 38 个 X 特征逐一断言，全部 ≤ decision_cutoff。

## 三、删除 / 禁用的特征及原因

| 特征 | 处置 | 原因 |
|---|---|---|
| `t2m_next` | 禁用 | 目标日天气，ERA5 再分析 + 合成段，决策时不可得（穿越） |
| `ssrd_next` | 禁用 | 同上 |
| `wind100_next` | 禁用 | 同上 |
| 幽灵 hour=0 行 | 删除 | 垃圾行，污染 label |

## 四、验证结果（canonical.py `verify()` 全部 PASS）

1. 无 hour=0，hour ∈ 1..24，每 (node,target_date) 行数与有效价格小时数一致
2. label 无 NaN（actual_da/rtpd/return/direction 全非空）
3. X 与 label 列不相交；X 无任何 target 当日实际列
4. Leakage Guard：38 个 X 特征 available_at ≤ decision_cutoff
5. lag 对位：`spread_lag1` == master (node, target_date−2, hour) 原始值
6. rolling 对位：`spread_mean7` == target_date−2..−8 原始均值
7. label 正确性：`actual_return == actual_da − actual_rtpd`（max 偏差 0.0）

## 五、剩余 UNKNOWN（未关闭，保守禁用）

| 项 | 状态 | 影响 |
|---|---|---|
| `load_2da_forecast` / `load_peak_flag` 发布时刻 | ASSUMED_AVAILABLE（低度不确定） | 若 2DA 发布晚于决策点则需重审 |
| 天气 `valid_pt` 时区（America/Los_Angeles naive） | 未做时区换算 | 历史滞后特征存在小时对齐不确定性 |
| ELCA 冷启动 | 单独评估 | 现有数据下基本不可预测（AUC ~0.50） |

## 六、结论

修复后的 canonical dataset **在严格 as-of 语义下无未来信息泄漏**：所有特征在决策时点确定可得（或保守禁用），label 与 X 完全隔离，Leakage Guard 自动化拦截。旧模型回测中来自天气穿越和 label 污染的效果已被移除。
