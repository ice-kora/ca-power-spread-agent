# Data Acquisition Plan（数据获取计划）

> V0.2.2 数据治理 ｜ 按优先级获取缺失数据，统一走 As-of Schema（`code/data_acquisition/schemas.py`）+ Time Gate，防穿越。

## 一、优先级（Lead 定序 + 理由）

### P0（先做，直击"严格 as-of 回测可信度"）
1. **D+1 天气 Forecast 历史档案（vintage）**
   - 去哪拿：NOAA NCEI GFS 原始档案（0.5°，2006+，GRIB2）；或 Open-Meteo `Single Runs` + `ecmwf_ifs`（2024-03+，run 可溯，接受 hindcast 代理）
   - 怎么拿：按 D 日 00Z/06Z run 取对 T=D+1 的预报（lead 18–42h）；`published_at=run init`，早于 10:00 PT cutoff
   - 存储：As-of Schema（`forecast_run/issue_time/published_at/available_at/target_time/lead_hours/value`）；raw GRIB2 + normalized
   - 回溯：NCEI 全历史；回溯时只用"当时 run 已发布"的 vintage，禁止事后重算
   - 防穿越：`resolve_available_at` 回测模式只认 vintage；Time Gate `decision_eligible`
2. **2DA Load Forecast 发布时间补证**
   - CAISO OASIS `SLD_FCST`；逐日记录取数时刻，证实是否 ≤ cutoff；或换带 vintage 的官方源
3. **RTPD 结算口径确认**
   - 业务方确认 FMM(15min) vs RT5M；回测按确认口径对齐（`PRC_RTPD_LMP` vs `PRC_INTVL_LMP`）

### P1（增强模型/Agent）
4. Renewable Forecast（`SLD_REN_FCST`，识别负电价时段）
5. Transmission Outage（`TRNS_OUTAGE`，UPD_DATE 可重建 → 证据层）
6. Congestion regime（`-c` 文件 + `PRC_LMP` MCC 分量交叉验证）

### P2（补充证据）
7. Market Notice（`ATL_OSM`）
8. 本地燃气价（EIA 加州 Citygate）
9. Wildfire / News

## 二、获取/存储/防穿越规范（所有源统一）

- **采集**：`code/data_acquisition/`（Collector 基类：fetch→raw→normalize→validation；LIVE→CACHE→MOCK 三级降级）
- **存储**：raw snapshot（JSON envelope `_meta`+`data`）+ normalized（含全部时间戳）+ `feature_snapshot`
- **回溯**：Historical Backtest Mode 只用"当时已发布 vintage"；Production Mode 每天 cutoff 前自动拉最新 + 存 raw + 记 `retrieved_at`
- **防穿越**：`available_at <= decision_cutoff` 才 `decision_eligible=TRUE`；任一时间缺失 → 不可用；进 production 前过 Time Gate

## 三、Production Shadow Test 每日抓取清单（cutoff 前）

| 时间（PT） | 抓取 | 源 |
|---|---|---|
| 每天（T 为决策日，cutoff=T 10:00 PT 前） | GFS 00Z/06Z run 对 T+1 的预报（温度/风/辐照） | NCEI/Open-Meteo |
| 同前 | CAISO Load Forecast（SLD_FCST） | OASIS |
| 同前 | （P1 后）Renewable Forecast / Outage | OASIS |
| T−1 13:00 后（非 cutoff 前） | DA LMP（作当天参考/对账） | OASIS |

> 详见：`docs/data_acquisition_poc.md`（采集器）、`docs/asof_schema_design.md`（As-of 结构）、`docs/caiso_oasis_sources.md`（query 清单）。
