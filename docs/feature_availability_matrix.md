# 特征可用性矩阵（canonical dataset）

> 生成时间：2026-08-09T08:16:24.488427　|　特征版本：canonical_v1　|　生成者：canonical.py
> 行语义：一行 = (node, target_date, hour)；决策时点 = decision_date = target_date-1 的 13:00 前。
> **铁律**：任何特征若 `available_at > decision_cutoff` 禁止进入训练/推理；UNKNOWN 未确认前默认禁用。

## X 特征（决策时点可见）

| 特征 | 类别 | available_at（相对 target_date） | 状态 |
|---|---|---|---|
| hour | 时间/节点（静态·日历） | 已知（静态/日历） | CONFIRMED |
| node | 时间/节点（静态·日历） | 已知（静态/日历） | CONFIRMED |
| zone | 时间/节点（静态·日历） | 已知（静态/日历） | CONFIRMED |
| dow | 时间/节点（静态·日历） | 已知（静态/日历） | CONFIRMED |
| month | 时间/节点（静态·日历） | 已知（静态/日历） | CONFIRMED |
| is_holiday | 时间/节点（静态·日历） | 已知（静态/日历） | CONFIRMED |
| solar_flag | 时间/节点（静态·日历） | 已知（静态/日历） | CONFIRMED |
| da_lag1 | 价格滞后 | target_date - 2（交付日整日完整） | CONFIRMED |
| rtpd_lag1 | 价格滞后 | target_date - 2（交付日整日完整） | CONFIRMED |
| spread_lag1 | 价格滞后 | target_date - 2（交付日整日完整） | CONFIRMED |
| da_lag2 | 价格滞后 | target_date - 3（交付日整日完整） | CONFIRMED |
| rtpd_lag2 | 价格滞后 | target_date - 3（交付日整日完整） | CONFIRMED |
| spread_lag2 | 价格滞后 | target_date - 3（交付日整日完整） | CONFIRMED |
| da_lag7 | 价格滞后 | target_date - 8（交付日整日完整） | CONFIRMED |
| rtpd_lag7 | 价格滞后 | target_date - 8（交付日整日完整） | CONFIRMED |
| spread_lag7 | 价格滞后 | target_date - 8（交付日整日完整） | CONFIRMED |
| spread_mean7 | 价差滚动统计 | target_date-2 .. target_date-8（同 hour 共 7 天完整） | CONFIRMED |
| spread_std7 | 价差滚动统计 | target_date-2 .. target_date-8（同 hour 共 7 天完整） | CONFIRMED |
| spread_mean14 | 价差滚动统计 | target_date-2 .. target_date-15（同 hour 共 14 天完整） | CONFIRMED |
| spread_std14 | 价差滚动统计 | target_date-2 .. target_date-15（同 hour 共 14 天完整） | CONFIRMED |
| spread_mean30 | 价差滚动统计 | target_date-2 .. target_date-31（同 hour 共 30 天完整） | CONFIRMED |
| spread_std30 | 价差滚动统计 | target_date-2 .. target_date-31（同 hour 共 30 天完整） | CONFIRMED |
| spread_day_std_lag1 | 日级统计（target_date-2 当天） | target_date - 2（当天 24h 完整） | CONFIRMED |
| spread_day_range_lag1 | 日级统计（target_date-2 当天） | target_date - 2（当天 24h 完整） | CONFIRMED |
| spread_day_max_lag1 | 日级统计（target_date-2 当天） | target_date - 2（当天 24h 完整） | CONFIRMED |
| da_day_mean_lag1 | 日级统计（target_date-2 当天） | target_date - 2（当天 24h 完整） | CONFIRMED |
| rtpd_day_mean_lag1 | 日级统计（target_date-2 当天） | target_date - 2（当天 24h 完整） | CONFIRMED |
| spread_day_mean_lag1 | 日级统计（target_date-2 当天） | target_date - 2（当天 24h 完整） | CONFIRMED |
| load_actual_lag1 | 实际负荷（历史） | target_date - 2（当天实际负荷完整） | CONFIRMED |
| load_actual_day_mean_lag1 | 日级统计（target_date-2 当天） | target_date - 2（当天 24h 完整） | CONFIRMED |
| load_2da_forecast | 负荷预报（2DA） | target_date - 2（2DA 负荷预测，预计提前 2 日发布） | ASSUMED_AVAILABLE |
| load_peak_flag | 负荷预报（2DA） | target_date - 2（2DA 负荷预测，预计提前 2 日发布） | ASSUMED_AVAILABLE |
| t2m_lag1 | 天气滞后（历史） | target_date - 2（当天天气完整） | CONFIRMED |
| ssrd_lag1 | 天气滞后（历史） | target_date - 2（当天天气完整） | CONFIRMED |
| wind100_lag1 | 天气滞后（历史） | target_date - 2（当天天气完整） | CONFIRMED |
| peer_spread_lag1 | 关联节点（peer） | target_date - 2（peer 交付日完整） | CONFIRMED |
| peer_da_lag1 | 关联节点（peer） | target_date - 2（peer 交付日完整） | CONFIRMED |
| peer_rtpd_lag1 | 关联节点（peer） | target_date - 2（peer 交付日完整） | CONFIRMED |

## 默认禁用特征（UNKNOWN / 穿越风险，保守模式）

| 特征 | 原因 |
|---|---|
| t2m_next | 目标日(target_date)天气温度。zone_weather_hourly.csv 疑似实测/再分析（变量名 ssrd_wm2/wind100 为 ERA5 风格），且数据延伸到未来（2026-08-19），决策时点不可得。上一轮审计判定很可能用了目标日实际天气（穿越）。默认禁用。 |
| ssrd_next | 目标日太阳辐射。同上，UNKNOWN 未确认是决策时可得预报前，默认禁用。 |
| wind100_next | 目标日 100m 风速。同上，默认禁用。 |

## Label 区（决策时点不可见，仅训练/回测）

| 列 | 定义 | available_at |
|---|---|---|
| actual_da | target_date 当日 DA 清价 | target_date-1 13:00（出清后）|
| actual_rtpd | target_date 当日 RTPD | target_date 深夜（实时市场）|
| actual_return | actual_da - actual_rtpd | 两者齐备后 |
| direction | sign(actual_return) | 两者齐备后 |

## 滞后约定

lag1 -> target_date-2, lag2 -> target_date-3, lag7 -> target_date-8; rolling(w) -> target_date-2 .. target_date-(w+1); 日级统计 -> target_date-2 当天。（DA(target_date-1) 虽已出清，但 RTPD(target_date-1) 决策日深夜才完整，故滞后从 target_date-2 起，宁保守不泄漏）
