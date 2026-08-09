# CAISO 官方数据源清单（Agent B · OASIS / BPM 官方源调研）

> 调研人：Agent B（CAISO 官方数据源调研员）｜ 日期：2026-08-09
> 方式：WebFetch + 直接对 CAISO OASIS API 实盘请求验证（匿名，无需登录）。**所有 query name / 字段 / 粒度均经真实 API 返回实证**；官网/BPM 未公开到位的标注 `[待确认]`，不编造。
> 目标：为项目建立"严格 historical as-of backtest"可用的官方源清单，补齐公司数据缺 `published_at` 的问题。

---

## 0. TL;DR（结论先行）

1. **核心官方源只有一个主入口：CAISO OASIS API**（`https://oasis.caiso.com/oasisapi/SingleZip?...`），匿名可用，返回 ZIP 内含逐分量 CSV/XML。全部六类数据（DA/RT LMP、Load Forecast、Renewable Forecast、Outage、Market Notice、Congestion）都有对应 OASIS query。
2. **关键发现：OASIS 数据的"发布时间"可部分重建。** 带 `UPD_DATE` 字段的报告（`SLD_ADV_FCST` 实时负荷预报、`TRNS_OUTAGE` 停电、`ATL_OSM` 系统消息）能给出**逐记录的精确发布时间戳**；LMP 类报告（`PRC_LMP`/`PRC_INTVL_LMP` 等）**不含**发布时间戳，只能用 BPM 规定的市场规则重建（DA 固定 13:00 PT，RT 按区间末尾）。
3. **严格历史 as-of 分级**：
   - **可严格重建**（历史回测安全）：`ATL_OSM`（消息带 `MSG_TIMESTAMP`）、`SLD_ADV_FCST`（带 `UPD_DATE`+`MKT_RUN_START_TIME_GMT`）、`TRNS_OUTAGE`（带 `UPD_DATE_GMT`）、**DA LMP**（单次发布、规则可确定性重建 13:00 PT）。
   - **部分可重建**（回测可用但有前提）：RT 5-min/FMM LMP（按区间结束发布，但**无版本历史**，价格修正会事后改写历史值，需结合价格修正报告识别）；`SLD_FCST`/`SLD_REN_FCST`（无更新时间戳，按市场 run 规则重建）。
   - **只能生产/影子测试**：`PRC_HUB_LMP`/`PRC_CURR_LMP`（当前快照）、`ENE_WIND_SOLAR_SUMMARY`、Today's Outlook CSV 导出、`content.caiso.com/transout` 停电报告（需证书）。
4. **历史覆盖**：OASIS API 实测回溯到 **2016 年**（2016 有数据、2014 无）；OASIS 网页 UI 只保留 **39 个月**；超出 39 个月的走 **Historical OASIS Data Downloader**（`oasis-bulk.caiso.com`，AWS S3 `caiso-oasis-s3-prod-groupzips`，Requester Pays，需 AWS 凭证）。
5. **公司数据 ↔ 官方源映射**：项目价格文件（DA/RTPD，`RTPD`=CAISO FMM 15-min）可被 `PRC_LMP`(DAM) + `PRC_RTPD_LMP`(RTPD) 交叉验证；`-c` 阻塞分量对应 LMP 的 `MCC`（`LMP_CONG_PRC`）分量；2DA 负荷预报对应 `SLD_FCST`(DAM)；天气无 CAISO 官方源（见 `weather_forecast_sources.md`）。

---

## 1. OASIS API 主入口（已实证）

### 1.1 端点与查询格式

```
https://oasis.caiso.com/oasisapi/SingleZip?queryname={QUERYNAME}&resultformat={1|6}&version={VERSION}&startdatetime={YYYYMMDDTHH:MM-0000}&enddatetime={YYYYMMDDTHH:MM-0000}&market_run_id={...}&grp_type={...}&node={...}&ti_id={...}&ti_direction={...}
```

- **认证**：API **匿名可用**（实测，无需注册/API key）。网页 UI（`oasis.caiso.com/mrioasis`）需自助注册。
- **响应**：ZIP 压缩，内含：
  - `resultformat=1` → XML（LMP 类按分量拆文件：`PRC_LMP_DAM_LMP_v1.xml` / `_MCE_` / `_MCC_` / `_MCL_`）；
  - `resultformat=6` → CSV（同结构 `.csv`）。**注意**：老数据（如 2016）即使 `resultformat=6` 也只回 XML。
- **时间参数**：`startdatetime/enddatetime` 用 UTC，格式 `YYYYMMDDTHH:MM-0000`；返回的 `OPR_DT` 是 PT 交易日（07:00 UTC = PT 当日 0 点附近，实测 2020-01-01T07:00-0000 返回 OPR_DT=2019-12-31）。
- **单次窗口**：官方未明示上限 `[待确认]`；社区工具（GitHub energy-analytics-project 系列 98 个 `data-oasis-*` 仓库）统一按 **1 天/请求**、间隔 5 秒下载。
- **限速**：实测短时间内密集请求会被节流（返回空/404）。官方数字 `[待确认]`；**建议 ≥3–5 秒/请求**（社区通用值）。
- **查询名大小写**：`queryname` 用大写（`PRC_LMP` 实测有效）。

### 1.2 参考文档（官方）

- OASIS 登录页 `https://oasis.caiso.com/mrioasis/logon.do`（列出全部报告菜单 + "How to use report URLs to download OASIS data" 链接指向 developer site）。
- **ISO Developer site `https://developer.caiso.com`**（OASIS Interface Spec v5.1.13、OASIS API URL 指南；**需登录，公开抓取 403/重定向**）。本文所有 query name 参数改用对真实 API 的实测 + `energy-analytics-project/data-oasis-*` 仓库 manifest 交叉确认（manifest 为原样抄录的官方 URL 模板）。

---

## 2. 逐数据类型官方源清单

> 字段名一律英文原文。`historical coverage` 未注明的以 §1.1/§4 为准（API 2016+；UI 39 个月）。

### 2.1 DA LMP / RT LMP（含 congestion/loss 分量）

| 项 | 值 |
|---|---|
| official_name | CAISO OASIS – Locational Marginal Prices（LMP 及分量 MCE/MCC/MCL） |
| endpoint / queryname | `https://oasis.caiso.com/oasisapi/SingleZip` — **`PRC_LMP`**（DA）、**`PRC_INTVL_LMP`**（RT 5-min）、**`PRC_RTPD_LMP`**（15-min FMM）、**`PRC_HASP_LMP`**（HASP 小时） |
| 关键参数 | `PRC_LMP`：`market_run_id=DAM`（实测）；`PRC_INTVL_LMP`：`market_run_id=RTM&grp_type=ALL_APNODES`（实测）；`PRC_RTPD_LMP`：`market_run_id=RTPD&grp_type=ALL_APNODES`；`PRC_HASP_LMP`：`market_run_id=HASP&grp_type=ALL_APNODES`；均可加 `node=SNLNDRO_1_N001` 等过滤（实测） |
| time granularity | DA 小时；RTD 5-min；RTPD/FMM 15-min；HASP 小时（内含 4 个 15-min advisory） |
| 分量字段 | `LMP_TYPE` + `XML_DATA_ITEM`：`LMP`/`LMP_PRC`、`MCE`/`LMP_ENE_PRC`（能量）、`MCC`/`LMP_CONG_PRC`（阻塞）、`MCL`/`LMP_LOSS_PRC`（损耗） |
| 其他列 | `INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT, OPR_HR, OPR_INTERVAL, NODE_ID_XML, NODE_ID, NODE, MARKET_RUN_ID, PNODE_RESMRID, GRP_TYPE, POS, MW, GROUP` |
| historical coverage | 2016+（实测 2016 可、2014 无） |
| publication time | DA：T−1 **13:00 PT**（BPM，见 §3）；RTD 5-min：区间结束后数分钟；FMM：区间开始前 ≤22.5 min 发布（BPM）；HASP：运行开始 T−45 min |
| supports historical query | 是 |
| supports timestamp reconstruction | **间接**——数据本身无发布时间戳；用 BPM 规则重建（DA 固定 13:00 PT；RT 按区间）。**无版本历史**：价格修正会改写历史结算值 |
| authentication | 无（匿名实测） |
| rate limit | 官方 `[待确认]`；实测密集请求被节流，建议 ≥3–5s/请求 |
| download method | HTTP GET → ZIP（CSV/XML） |
| production polling method | 每 5 min 拉最新 5-min 区间（RTD）；DA 结果 13:00 PT 后拉一次 |

> 项目节点验证：`node=SNLNDRO_1_N001` DA LMP 2026-08-01 HE1 = $44.93，与公司价格文件可对齐。`ELCAJNGT_7_N001`(SP15)、`CONTROLX_1_N001`(ZP26) 同理。
> **注意**：`PRC_LMP` + `market_run_id=RTM` 实测返回 HTTP 404（无效组合）；RT 5-min 价格必须用 **`PRC_INTVL_LMP`**。

### 2.2 Load Forecast（DAM 系统负荷预测）

| 项 | 值 |
|---|---|
| official_name | CAISO OASIS – System Demand Forecast |
| endpoint / queryname | `SLD_FCST`（`market_run_id=DAM` 日前 / `RTM` 15-min 实时 / `ACTUAL` 实际） |
| 关键参数 | `queryname=SLD_FCST&market_run_id=DAM`（实测） |
| time granularity | DAM 小时；RTM 15-min；ACTUAL 小时 |
| 关键字段 | `INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, LOAD_TYPE, OPR_DT, OPR_HR, OPR_INTERVAL, MARKET_RUN_ID, TAC_AREA_NAME, LABEL, XML_DATA_ITEM, POS, MW, EXECUTION_TYPE, GROUP`；`LABEL`=“Demand Forecast Day Ahead”（`SYS_FCST_DA_MW`）；按 TAC 区/子 LAP 分列 |
| historical coverage | 2016+ |
| publication time | DA：DAM 内 T−1 13:00 PT 发布（BPM 1300 hrs）；RTM 15-min：随 RTPD run（见 §3） |
| supports historical query | 是 |
| supports timestamp reconstruction | **弱**——无 `UPD_DATE` 列；用市场 run 规则重建（DA 13:00 PT 固定） |
| 补充 | `SLD_FCST_PEAK`（日峰值预报，BPM：每日 09:00 PT 更新）；`SLD_ADV_FCST`（RTD 5-min advisory 预报，**version=4**，带 `UPD_DATE`+`MKT_RUN_START_TIME_GMT`，见 §2.6）；`ENE_SLRS`（TAC 区 Load/Gen/Import/Export 小时表） |

### 2.3 Renewable Forecast（太阳能/风能预测）

| 项 | 值 |
|---|---|
| official_name | CAISO OASIS – Wind and Solar Forecast / Renewable Forecast |
| endpoint / queryname | **`SLD_REN_FCST`**（`market_run_id=DAM`，实测）；`ENE_SLRS`（`market_run_id=DAM&tac_zone_name=ALL&schedule=ALL`，实测）；`ENE_WIND_SOLAR_SUMMARY`（version=5，实测） |
| time granularity | DAM 小时（`SLD_REN_FCST` 按 TRADING_HUB×RENEWABLE_TYPE）；`ENE_SLRS` 小时按 TAC 区 |
| 关键字段（SLD_REN_FCST） | `OPR_DT, OPR_HR, OPR_INTERVAL, INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, TRADING_HUB(NP15/SP15/ZP26), RENEWABLE_TYPE(Solar/Wind), LABEL(“Renewable Forecast Day Ahead”), XML_DATA_ITEM(RENEW_FCST_DA_MW), MW, MARKET_RUN_ID, GROUP` |
| historical coverage | 2016+ |
| publication time | BPM（Market Instruments V93）：“Day-Ahead forecast is posted daily in advance of the DAM”；HASP 预报在每次 HASP run 前（小时）；FMM 15-min；RTD 5-min；**actual production 于运行日后一天发布** |
| supports timestamp reconstruction | **弱**——无更新时间戳；按市场 run 规则重建 |

### 2.4 Transmission Outage / Planned Outage

| 项 | 值 |
|---|---|
| official_name | CAISO OASIS – Transmission Outages；另有 CAISO 网站 Transmission Outage Report |
| endpoint / queryname | **`TRNS_OUTAGE`**（`ti_id=ALL&ti_direction=ALL`，实测）；`TRNS_CURR_USAGE`（当前使用/容量，实测） |
| 关键字段 | `TI_ID, TI_DIRECTION, EQUIPMENT_OUTAGE, OUTAGE_NOTES, AUDIT_TYPE, START_DATE, END_DATE, START_HOUR, END_HOUR, CURTAILED_OTC_MW, **UPD_DATE, UPD_DATE_GMT, UPD_BY**, OASIS_REC_STAT, INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT` |
| time granularity | 按停电事件（每条记录含起止）；BPM：“The list is updated with every outage event” |
| historical coverage | 2016+ |
| publication time | 逐事件更新（`UPD_DATE_GMT` 精确到秒）；另有 CAISO 官网短/长期停电报告：短期**每小时**发布（未来 7 天）、长期**每日**（未来 1000 天），位于 `https://content.caiso.com/transout/indexSP.html`（需证书，非公开抓取） |
| supports timestamp reconstruction | **强**——每条事件自带 `UPD_DATE_GMT`（实测示例 2026-06-06T07:29:46-00:00） |

### 2.5 Market Notice / System Conditions

| 项 | 值 |
|---|---|
| official_name | CAISO OASIS – System Operating Messages（ATLAS / Messages） |
| endpoint / queryname | **`ATL_OSM`**（`msg_severity=ALL`，实测） |
| 关键字段 | `MSG_ID, MSG_TIME, **MSG_TIMESTAMP**, MSG_SEVERITY, MSG_SCID, MSG_TEXT, TIMESTAMP, MSG_TIME_GMT, MSG_TIMESTAMPE_GMT, TIMESTAMP_GMT` |
| time granularity | 按消息事件 |
| historical coverage | 2016+ |
| publication time | 消息发出即录（`MSG_TIMESTAMP` = 精确发布时间，实测示例 `2026-08-01T16:38:08`） |
| supports timestamp reconstruction | **最强**——每条消息自带精确发布时间戳 |
| 补充 | OASIS UI 菜单另有 “Price Correction Messages / Price Correction Summary”（价格修正消息）报告，其 API query name `[待确认]`（`PRC_PCORR`/`PRC_PCORR_MSG`/`MSG_OSM` 实测均无效）；CAISO 官网还有 `/library/market-disruption-reports`、`/library/price-correction-reports`（周度） |

### 2.6 Congestion / LMP 分量

| 项 | 值 |
|---|---|
| official_name | CAISO OASIS – 阴影价格 / LMP 阻塞分量 |
| endpoint / queryname | LMP 分量见 §2.1（`MCC` = 阻塞分量，公司 `-c` 文件对应物）；约束级阴影价格用 **`PRC_CNSTR`**（`market_run_id=DAM&ti_id=ALL`，实测） |
| 关键字段 | `PRC_CNSTR`：约束名/方向/价格；LMP 类：`LMP_TYPE=MCC, XML_DATA_ITEM=LMP_CONG_PRC` |
| time granularity | DA 小时（`PRC_CNSTR` DAM）；LMP 类随市场粒度 |
| historical coverage | 2016+ |
| publication time | 同对应市场（DA 13:00 PT；RT 随区间） |
| supports timestamp reconstruction | 同 §2.1（间接） |

### 2.7 Historical OASIS Data Downloader（大容量历史批量下载）

| 项 | 值 |
|---|---|
| official_name | Historical OASIS Data Downloader |
| URL | `https://oasis-bulk.caiso.com/`（官方公告 2025-11-11：`https://www.caiso.com/notices/new-tool-now-available-on-caiso-oasis-website`） |
| 存储 | AWS S3 **`caiso-oasis-s3-prod-groupzips`**（区域 us-west-1，实测），Requester Pays 桶 |
| 数据 | 市场定价数据（DA/RT prices 等；文件名形如 `_DAM_LMP_GRP_N_N_` 的 groupzips） |
| historical coverage | 官网：超 39 个月、**可回溯至 2016** |
| authentication | 搜索无需凭证；**下载需自建 AWS 凭证**，数据流量费由下载方承担（Requester Pays） |
| 用途 | 官方注明 **not intended for operational use**（不可用于生产轮询）；适合一次性批量回补历史 |

---

## 3. BPM 规定的官方发布时间（Market Instruments V93，2026-05-01 修订）

来源：`https://bpmcm.caiso.com/Pages/BPMDetails.aspx?BPM=Market Instruments`（V93 版，Redline PDF 全文提取）。

| 事件 | 官方时间 |
|---|---|
| DAM bid 截止（Market Close） | **10:00 PT**（“closes at 1000 hours”） |
| **DAM 结果发布** | **13:00 PT**（“published at 1300 hours the day prior to the Trading Day”；“by 1300 hours of the day before the Trading Day”） |
| Pre Day-Ahead Market（D+2） | 18:00（“Pre Day-Ahead Market (D+2) by 18:00”） |
| Post Day-Ahead Market（D+1）报告 | 约 DAM 结果发布后 1 小时（“by one hour after the publication of the Day-Ahead results”） |
| RTM 竞价开放 | 约 T−1 13:00 PT 起 |
| FMM（15-min 市场）排程发布 | 绑定区间开始前 **≤22.5 分钟** |
| RTUC/RTM 小时排程发布 | 绑定小时开始前 **≤52.5 分钟** |
| HASP run 起始 | 区间开始前 45 分钟（“Market Start Date Time must be 45 minutes prior to the Interval Start Date Time”） |
| RTM 5-min 负荷预报 | **每 5 分钟**发布，滚动未来 **11 个区间** |
| Peak Demand Forecast | **每日 09:00 PT** 更新（提前 7 天） |
| Wind and Solar Forecast | DA 每日在 DAM 前发布；HASP 逐小时；FMM 15-min；RTD 5-min；**实际值运行日后一天** |
| Sufficiency Evaluation Demand Forecast | **每 30 分钟**发布一次 |
| Today's Outlook DA 预报（+1~+7 天） | 05:30 PT 开始更新，09:30 PT 完成（官网页面） |
| 停运发电机组报告（CPUC §352.5） | 每日约 **15:15 PT** |

> 关键结论：**DA 相关一切特征/标签的 `available_at` 官方锚点是 `decision_date 13:00 PT`**（DAM 结果），而项目 `decision_cutoff = D 日 10:00 PT`（bid 截止）——因此 DA 数据在决策时不可见，只能做标签/事后验证（与 `asof_schema_design.md`、`company_data_audit.md` 一致）。

---

## 4. 各源历史覆盖汇总

| 源 | 覆盖起点 | 终点 | 备注 |
|---|---|---|---|
| OASIS API（SingleZip） | **2016**（实测 2016-06 有、2014-01 无） | 滚动实时 | 老数据只回 XML |
| OASIS 网页 UI | 最近 **39 个月** | 滚动 | 需自助注册 |
| Historical OASIS Data Downloader（S3） | **2016** | 滚动 | 需 AWS 凭证，Requester Pays |
| Today's Outlook（caiso.com） | 仅近期（图表 CSV 导出） | 实时 | 供生产监控 |
| content.caiso.com/transout 停电报告 | 滚动未来窗口 | — | 需证书 |
| 公司数据（仓库内） | 2024-01/2025-04 起 | 2026-08 | 见 `company_data_audit.md` |

---

## 5. 严格 Historical As-of Backtest 适用性评估

> 判定口径：`supports_timestamp_reconstruction` = 能否重建**当时发布/可见时刻**（`available_at`）。全链路 `available_at <= decision_cutoff` 才算决策可见。

### 5.1 可严格重建（历史回测安全）

| 源 | 重建机制 | 用途 |
|---|---|---|
| `ATL_OSM`（系统消息） | 逐条 `MSG_TIMESTAMP`（精确发布时刻） | Market Notice / System Conditions 特征；突发事件检测 |
| `SLD_ADV_FCST`（RTD 5-min advisory 负荷预报） | 逐记录 `UPD_DATE` + `MKT_RUN_START_TIME_GMT`（该值由哪次 RTD run 产出） | 实时负荷预报 as-of 特征 |
| `TRNS_OUTAGE`（停电事件） | 逐事件 `UPD_DATE_GMT` | Outage 特征（事件何时进入系统可见） |
| `PRC_LMP`（DA，`market_run_id=DAM`） | **规则确定性重建**：BPM 固定 T−1 13:00 PT 单次发布、无再版 → `available_at = (T−1) 13:00 PT` | DA 价/阻塞分量标签 + DA 特征（在 cutoff 后，仅作标签） |

### 5.2 部分可重建（回测可用，有前提）

| 源 | 前提/局限 |
|---|---|
| `PRC_INTVL_LMP`（RT 5-min） | 发布时刻≈区间结束+数分钟（规则重建）；但 **OASIS 无版本历史**，事后价格修正会改写历史值 → 严格 as-of 需结合 `Price Correction` 报告识别，或接受“终值≈发布值”近似 |
| `PRC_RTPD_LMP`（FMM 15-min） | 同上；BPM 给出发布 ≤ 区间前 22.5 min |
| `SLD_FCST`（DA/RTM 负荷预报） | 无更新时间戳；`available_at` = 对应市场 run 发布时刻（DA 13:00 PT；RTM 随 RTPD run） |
| `SLD_REN_FCST` / `ENE_SLRS`（可再生预报） | 同上；DA 版 13:00 PT 前发布 |
| `PRC_CNSTR`（阻塞约束价格） | 同 LMP 规则 |

### 5.3 只能生产 / 影子测试（不可用于严格回测）

| 源 | 原因 |
|---|---|
| `PRC_HUB_LMP`（Trading Hub 当前 RT 价） | 当前 5-min 快照（`PRC_HUB_LMP.html` 即“Current Real-Time”页），非历史归档 |
| `PRC_CURR_LMP`（当前 LMP） | 当前快照（`node=ALL`） |
| `ENE_WIND_SOLAR_SUMMARY` | 汇总口径，无逐记录发布戳 |
| Today's Outlook 图表 CSV | 仅近期 + 信息用途，非严格归档 |
| `content.caiso.com/transout` 停电报告 | 未来滚动窗口 + 需证书 |
| Historical OASIS Data Downloader（S3） | 官方注明非生产用途；适合一次性回补 |

---

## 6. 接入建议

1. **回测数据层**（严格 as-of）：
   - 用 §2 的 OASIS query 拉 2016+ 历史，**1 天/请求**、间隔 ≥5s，落 ZIP 原文（保存 raw response 即保留原始时间口径）。
   - `available_at` 赋值规则：DA 类 = 交易日 T−1 13:00 PT；RT 类 = 区间结束 + 缓冲（建议 +5min）；预报类优先用 `UPD_DATE`/`MKT_RUN_START_TIME_GMT`。
   - 用 `ATL_OSM` 的 `MSG_TIMESTAMP` 做事件级 as-of 交叉验证。
2. **生产层**：DA 结果 13:00 PT 后轮询一次；RT 5-min 价格每 5 min 轮询（建议提前 30s 打探，避免边界竞态）；保存 raw response + `retrieved_at`，避免“事后被价格修正改写”导致的漂移。
3. **交叉验证公司数据**：`PRC_LMP`(DAM) ↔ 价格文件 DA 行；`PRC_RTPD_LMP`(FMM 15-min 聚合) ↔ 价格文件 RTPD 行；`-c` 文件 ↔ `MCC`(`LMP_CONG_PRC`)；`SLD_FCST`(DAM) ↔ `load_CA_ISO_TAC_2DA.csv`；`SLD_FCST`(ACTUAL) ↔ `load_CA_ISO_TAC_ACTUAL.csv`。
4. **坑**：
   - `PRC_LMP`+`market_run_id=RTM` 是无效组合（404），RT 5-min 用 `PRC_INTVL_LMP`；
   - `version` 因 query 而异（`SLD_ADV_FCST`=4、`ENE_WIND_SOLAR_SUMMARY`=5、`PRC_RTM_LAP`=6，多数=1），务必按清单取值；
   - 老数据（2016–2020 段）只回 XML，解析需兼容两种格式；
   - 时间全 UTC：`startdatetime` 用 `-0000`，返回 `OPR_DT` 是 PT 交易日。

---

## 7. 待确认事项（诚实标注）

- [待确认] OASIS API 官方单次查询窗口上限与正式 rate limit 数值（实测无公开文档；社区用 1 天/请求 + 5s）。
- [待确认] OASIS API 历史数据确切起始日（实测 2016-06 可用、2014-01 不可；2015 未测）。
- [待确认] Price Correction Messages / Price Correction Summary 的 OASIS API query name（`PRC_PCORR` 等实测无效；UI 有该报告）。
- [待确认] `ENE_SLRS` 报告确切语义（实测输出同时含 Load/Gen/Import/Export 与 SLRS_TYPE=ALL/LOAD/ETIE，官方报告名 “Wind and Solar Forecast” 之下，逐类型口径需进一步对表）。
- [待确认] Daily Renewables Watch（`content.caiso.com/green/renewrpt/`）2026-08 实测 404，疑似退役，未纳入主清单。
- [待确认] OASIS web UI 39 个月的确切滚动边界与 API 是否一致（API 实测超 39 个月可用，以实测为准）。

---

## 8. 参考链接

- OASIS 登录/菜单：`https://oasis.caiso.com/mrioasis/logon.do`
- OASIS API 样例页：`https://oasis.caiso.com/oasisapi/prc_hub_lmp/PRC_HUB_LMP.html`
- OASIS API 直接下载（本次实证格式）：`https://oasis.caiso.com/oasisapi/SingleZip?queryname=PRC_LMP&resultformat=6&version=1&startdatetime=YYYYMMDDTHH:MM-0000&enddatetime=YYYYMMDDTHH:MM-0000&market_run_id=DAM`
- Historical OASIS Data Downloader：`https://oasis-bulk.caiso.com/`；公告 `https://www.caiso.com/notices/new-tool-now-available-on-caiso-oasis-website`
- S3 桶：`caiso-oasis-s3-prod-groupzips`（us-west-1，Requester Pays）
- BPM Market Instruments V93：`https://bpmcm.caiso.com/Pages/BPMDetails.aspx?BPM=Market%20Instruments`
- Today's Outlook：`https://www.caiso.com/todays-outlook`
- 停电报告：`https://www.caiso.com/market-operations/outages`；`https://content.caiso.com/transout/indexSP.html`
- Market Reports 库：`https://www.caiso.com/library/market-reports`
- 开发者门户（OASIS 技术规格，需登录）：`https://developer.caiso.com`
- 本项目关联文档：`docs/company_data_audit.md`、`docs/asof_schema_design.md`、`docs/market_timeline.md`、`docs/weather_forecast_sources.md`
