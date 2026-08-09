# As-of Data Gap Report（As-of 数据缺口报告）

> V0.2.2 数据治理 ｜ 所有缺口按"决策时点（D-1 10:00 PT）是否真可见"判定。缺口可影响模型或 Agent，或两者。

## 一、缺口矩阵

| 字段 | 当前是否有 | 是否历史安全 | 缺什么 | 严重度 | 影响模型 | 影响 Agent | 推荐数据源 | 解决方案 |
|---|---|---|---|---|---|---|---|---|
| **D+1 天气 forecast 历史档案** | ⚠️ 有（ERA5+合成） | **NO**（非当时可见预报） | forecast_issue_time / vintage / run | **P0** | ✅（天气特征只能历史 lag） | ✅（evidence 无真实源） | NOAA NCEI GFS 档案（2006+）或 Open-Meteo IFS | 接 GFS 00Z/06Z run 逐日；或接受 IFS hindcast 代理 |
| **2DA Load Forecast vintage** | ✅ 有值 | **UNKNOWN**（无 issue_time） | forecast_issue_time / published_at | **P0** | ✅（as-of 未证实） | — | CAISO OASIS `SLD_FCST` | 逐日补证发布时点；或官方源复现 |
| **RTPD 结算口径** | ✅ 有值 | 口径待确认 | FMM(15min) vs RT5M | **P0** | ✅（label/回测真实性） | — | OASIS `PRC_RTPD_LMP` vs `PRC_INTVL_LMP` | 业务方确认结算用哪个；回测按对应口径 |
| Renewable Forecast | ❌ | — | 太阳能/风能 as-of 预测 | **P1** | ✅（负电价时段特征） | ✅ | OASIS `SLD_REN_FCST` | 接入，识别供给过剩 |
| Transmission Outage | ❌ | — | 计划停电 as-of | **P1** | — | ✅ | OASIS `TRNS_OUTAGE`（UPD_DATE 可重建） | 接入证据层 |
| Congestion regime | ⚠️（-c 文件） | PARTIAL | 官方组件定义 | **P1** | ✅（congestion lag/regime） | — | OASIS `PRC_LMP` MCC 分量 | 用 -c 文件 + 官方交叉验证 |
| Market Notice | ❌ | — | 系统公告 as-of | **P2** | — | ✅ | OASIS `ATL_OSM`（MSG_TIMESTAMP 可重建） | 接入证据层 |
| Fuel/Gas Price | ❌ | — | 本地燃气价 as-of | **P2** | ✅（边际成本） | ✅ | EIA 加州 Citygate | 后期接入 |
| DA/RT 官方复现 | ⚠️（公司有） | PARTIAL | 官方逐笔对账 | **P1** | ✅（数据可信度） | — | OASIS PRC_LMP/PRC_INTVL_LMP | 公司 vs 官方对账 |

## 二、重点结论

1. **D+1 Weather Forecast 缺失**：公司天气是 ERA5 再分析（非当时预报），目标日天气已被禁用；真实历史 forecast 档案需 NCEI GFS（2024/25）或 IFS hindcast 代理（C 已核实无免费 API 同时满足"2024/25+as-issued+run 可溯"）。
2. **Load Forecast vintage 缺失**：2DA 文件无 issue_time，`HISTORICAL_BACKTEST_SAFE=UNKNOWN`；BPM 证据支持 ASSUMED 但未逐日证实。
3. **Renewable / Outage / Notice 全部缺失**：P1/P2 待接（OASIS 可提供且部分可重建发布时间）。
4. **RTPD 口径待确认**（FMM vs RT5M）——影响 label 与回测结算真实性，P0。
5. **DA/RT 官方可复现**（OASIS 2016+）→ 公司数据可对账验证。

> 详见：`docs/company_data_audit.md`、`docs/evidence_source_v021.md`、`docs/weather_forecast_sources.md`。
