# Data Source Registry（数据源统一登记）

> V0.2.2 数据治理 ｜ 汇总：公司数据审计(A) + CAISO 官方源(B) + 天气源(C) + As-of Schema(D) + 采集 PoC(E)
> 目标：每个字段都能回答"它是什么、从哪来、什么时候发布、交易员当时能不能看到"。

## 一、核心字段登记

| field | 含义 | 当前文件 | source_type | published_at / available_at | historical_backtest_safe | leakage_risk | priority |
|---|---|---|---|---|---|---|---|
| `da_price` | 日前 LMP（$/MWh） | 价格数据/*.xlsx | Historical Actual | T-1 13:00 PT 发布（BPM）；节点小时 | **YES**（label/lag） | 低（仅历史；目标日禁入 X） | P0 |
| `rtpd_price` | 实时 LMP | 价格数据/*.xlsx | Historical Actual | T 日实时（15min FMM 聚合小时） | **YES**（label） | 高（目标日禁入 X） | P0 |
| `spread` / Return | DA − RTPD | 派生（xlsx DARTPD Return） | Derived Label | = 两者齐备后 | **YES**（label/outcome） | 高 | P0 |
| congestion | 阻塞分量 | `*-c.xlsx`（DARTPD Cong Spread） | Historical Actual（阻塞分量） | 同 DA/RTPD | **PARTIAL**（可作 lag/regime；官方组件定义 `[UNKNOWN_SOURCE_TYPE]`） | 低 | P1 |
| `load_actual` | 实际负荷（MW） | load_ACTUAL.csv | Historical Actual | T 日后（滞后） | **YES**（仅历史 lag） | 高（目标日禁入 X） | P0 |
| `load_2da_forecast` | 日前负荷预测 | load_2DA.csv | Historical Forecast | **无 issue_time → UNKNOWN**；外部 BPM 证据 TD−2 18:00 PT | **UNKNOWN**（严格回测需逐日补证） | 中 | P0 |
| weather (t2m/ssrd/wind) | 天气 | zone_weather_hourly.csv | **Reanalysis(ERA5)+合成段** | **无 run/issue → 不可作 D+1 forecast** | **NO（目标日）**；仅历史 lag 可用 | 高（已禁 `*_next`） | P0 |
| node/zone | 节点→区域 | 节点位置.xlsx | Static | 静态 | YES | 低 | — |
| GFS forecast（新） | D+1 天气预报 | `data_acquisition/weather_gfs.py`（Open-Meteo NCEP GFS） | Historical Forecast（as-issued run） | run 12Z（12:00 UTC），早于 cutoff | **PARTIAL**（档案仅 2026-04 起；2024/25 需 NCEI） | 低（time gate 校验） | P0 |

## 二、官方候选源（B/C 调研，OASIS 实测）

| 数据 | CAISO OASIS query | 覆盖 | published_at 重建 | 用途 |
|---|---|---|---|---|
| DA LMP | `PRC_LMP`(DAM) | 2016+ | T−1 13:00 PT（BPM） | label/lag 交叉验证 |
| RT 5-min LMP | `PRC_INTVL_LMP`(RTM) | 2016+ | 区间结束（无版本历史） | 结算口径核对（RT5M） |
| RTPD/FMM | `PRC_RTPD_LMP` | 2016+ | 区间前 22.5min | 公司 RTPD 复现 |
| 负荷预报 | `SLD_FCST` | 2016+ | 无时间戳 → not_backtest_safe | 2DA 交叉验证 |
| 可再生预报 | `SLD_REN_FCST` | 2016+ | 按 run | 补充（P1） |
| 停电 | `TRNS_OUTAGE` | 2016+ | `UPD_DATE_GMT` 可重建 | Agent evidence（P1） |
| 系统消息 | `ATL_OSM` | 2016+ | `MSG_TIMESTAMP` 可重建 | Agent evidence（P2） |
| 阻塞约束 | `PRC_CNSTR` | 2016+ | 部分 | congestion regime（P1） |
| 天气 Forecast | NOAA NCEI GFS 原始档案 / Open-Meteo IFS | 2024/25（NCEI 2006+；IFS 2024-03+） | run 12Z（00Z/06Z 确定早于 cutoff） | **D+1 forecast**（P0） |

> 详见：`docs/company_data_audit.md`、`docs/caiso_oasis_sources.md`、`docs/weather_forecast_sources.md`。
