# 天气 Forecast 数据源调研报告（D+1 as-of 特征）

> 调研员：Agent C（天气 Forecast 数据源调研）｜ 日期：2026-08-09 ｜ 状态：结论可落地，两处严格 as-of 缺口已明示
>
> 调研方式：WebFetch 官方/权威页面 + **对 Open-Meteo 三个 API 实测 curl 验证**（实测结果见附录）。未编造数字；无法验证的均标 `[待确认]`。
>
> 核心结论：**Historical Forecast API 与 ERA5 不能用于严格 as-of 回测；Previous Runs 的 run 可溯性有歧义；Single Runs 是唯一"run 完全可溯"的 API 但 GFS 只覆盖 2026-04-02 之后。2024/2025 严格 as-of 的唯一真源是 NOAA GFS 原始档案（NCEI），取 D 日 00Z 或 06Z run。**

---

## 0. 结论先行（TL;DR）

1. **决策约束**：`decision_cutoff` = D−1 日 **10:00 PT** = D 日 **17:00 UTC（PDT）/ 18:00 UTC（PST）**。D+1 天气特征必须由**在此时点之前已发布完毕**的 forecast run 生成。
2. **最新可用的 run**：D 日 **00Z 与 06Z run** 在 cutoff 前**确定发布完毕**（init 后 ~4–6 h）；D 日 **12Z run** 处于临界（init 12:00 UTC = 05:00 PT，发布 ~15:30–18:00 UTC = 08:30–11:00 PT），**无法保证 10:00 PT 前完整**；D 日 18Z run 在 cutoff 之后，不可用。
3. **Open-Meteo 三 API 定位**：
   - **Historical Forecast API**：能回溯 2021+，但**每小时的取值来自"该小时之前最近一次 run"的短时效拼缝**（实测为近实时 nowcast，run 时点晚于 cutoff）→ **不可用于 as-of**。
   - **Previous Runs API**：能回溯 2024/2025（GFS 2m 温度 2021-03 起；太阳辐射/风速 2024-02 起），run 以 `_previous_dayN`（= valid time − N×24 h）给出，**实测确认 `_previous_day1` = 恰好在 valid time 前 24 h 初始化的 run**；但响应不返回 run init 时间、非整点小时存在 6 小时间隔插值歧义 → **run 可溯性部分成立，严格 as-of 为次选**。
   - **Single Runs API**：`run=` 指定 UTC init 时间，**run 完全可溯**；但 **GFS 档案仅从 2026-04-02**（实测：2026-04-02T00 可用、2026-04-01T00 报错），**ECMWF IFS 从 2024-03-14**（实测验证），且 IFS 为 **Cycle 49R1 的 6 小时 hindcast（回顾性重算），非当时实发的运行预报**。
4. **推荐**（详见 §7）：
   - **严格 as-of（2024/2025）→ NOAA GFS 原始档案（NCEI）**，取 D 日 00Z 或 06Z run 对 T=D+1 的预报（lead 24–48 h / 18–42 h）。唯一"当时真正发布过"的源，工程代价最高（GRIB2 下载+解码）。
   - **API 路线（可接受"同版本 hindcast 代理"）→ Open-Meteo Single Runs API，`models=ecmwf_ifs`，`run=D 00:00 UTC`**，覆盖 2024-03-14+，run 完全可溯、发布早于 cutoff；明确标注"非实发预报"限制。
   - 若只需快速探索：Previous Runs API + GFS `_previous_day1`（2024-02+），run 歧义需写入文档。

---

## 1. 背景与约束

- 目标：为 D+1（target_date T）构造 **D−1 决策时点当时可见**的天气预报特征，即"历史上当时真正发布过的 D+1 预报"，不是事后实际天气。
- 铁律：`published_at ≤ decision_cutoff`（T−1 日 10:00 PT = T−1 日 17:00/18:00 UTC）。
- 现有 `zone_weather_hourly.csv` 变量：`t2m_c`（2 m 温度 °C）、`ssrd_wm2`（太阳短波辐射 W/m²，ERA5 风格）、`wind100`（100 m 风速 m/s）。D+1 预报特征需对齐这三个物理量。
- 时区换算：PDT = UTC−7（4–10 月）→ 10:00 PT = 17:00 UTC；PST = UTC−8（11–3 月）→ 10:00 PT = 18:00 UTC。

---

## 2. NOAA / NCEP GFS

### 2.1 关键时间定义

| 字段 | 定义 | 本项目取值 |
|---|---|---|
| `model_run_time` / `initialization_time` | GFS 起报时刻，4 次/日：**00 / 06 / 12 / 18 UTC**（NCEI、NOMADS、AWS Open Data 均确认） | 取 D 日 00Z 或 06Z |
| `forecast_target_time` | = initialization_time + lead(F hour)，即预报的 valid time | T=D+1 各小时 = run 的 lead 18–42 h（06Z run）或 24–48 h（00Z run） |
| `actual_available_time` | GFS 输出文件实际发布到 NOMADS/档案的时刻 | init 后约 **3.5–5 h** 首文件、4–6 h 完整（见 2.2） |

### 2.2 12Z initialization 是否 = 12Z available？——**否**

- 官方页面（NCO 产品页、NOMADS 索引、AWS 注册页、NCEI 产品页）均**未给出明确发布延迟数字** `[待确认]`。Open-Meteo 文档的权威表述为"global models 通常 **4–6 h** 发布"（如 GFS、IFS）；NCEP 实际运行惯例为首文件 ~3 h 15 m、完整 run ~4–4.5 h。
- 推论（对 10:00 PT cutoff）：
  - D 日 **00Z**（init D 00:00 UTC = D−1 17:00 PT）→ 发布 ~04–06 UTC（D−1 21:00–23:00 PT）→ **早于 cutoff，确定可用** ✓
  - D 日 **06Z**（init D 06:00 UTC = D−1 23:00 PT）→ 发布 ~10–12 UTC（D 03:00–05:00 PT）→ **早于 cutoff，确定可用** ✓
  - D 日 **12Z**（init D 12:00 UTC = D 05:00 PT）→ 发布 ~15:30–18:00 UTC（D 08:30–11:00 PT）→ **与 10:00 PT 临界，无法保证完整** ✗ `[待确认]`
  - D 日 **18Z**（init D 18:00 UTC = D 11:00 PT）→ **在 cutoff 之后** ✗

### 2.3 能否回溯 2024/2025？——**能（NCEI 档案）**

- **NCEI Global Forecast System 档案**（`www.ncei.noaa.gov/products/weather-climate-models/global-forecast`）：
  - 0.5° 预报（GRIB，3 小时步长，+000–+192 h）：**2006-10-10 → 至今**，覆盖 2024/2025 ✓
  - 1.0° 预报（GRIB，3 小时步长 +000–+240 h，12 小时步长 +252–+384 h）：**2005-02-15 → 至今** ✓
  - 0.5°/1.0° 分析：2007/2004 → 至今 ✓
  - 访问方式：HTTPS 在线浏览（0.5° 预报约**近 2 年在线**，更早走 THREDDS/HAS）；THREDDS Data Server（TDS）覆盖全历史。
  - 注意：**AWS Open Data 的 GFS 桶仅为"滚动 30 天窗口"**，回溯 2024/2025 不可用；0.25° 的长期历史可溯性未核实 `[待确认]`。
- 文件格式 GRIB2/GRIB，需 cfgrib/pygrib 解码；4 次/日 run 的整文件下载工程量大。

### 2.4 小结

| 项 | GFS（原始档案路线） |
|---|---|
| 能否回溯 2024/2025 | ✅ NCEI 0.5°/1.0° 预报档案自 2005/2006 起 |
| vintage/run 可溯 | ✅ 完全（run 文件名含 cycle 00/06/12/18 UTC） |
| 发布时间 | 官方无数字 `[待确认]`；Open-Meteo 口径 4–6 h；实际 ~3.5–5 h |
| 10:00 PT 前可用 | ✅ 00Z/06Z 确定；12Z 临界 ✗ |
| 严格 backtest 适用 | ✅ **是（唯一真正 as-issued 源）** |
| 获取方式 | NCEI HTTPS/TDS + GRIB2 解码；工程量大 |

---

## 3. Open-Meteo Historical Forecast API

**端点**：`https://historical-forecast-api.open-meteo.com/v1/forecast`

| 问题 | 实测/文档结论 |
|---|---|
| 能否回溯历史？ | ✅ 是。文档："Past weather forecasts from 2022 onwards are available"，GFS 自 **2021-03-23**。实测 `2025-07-01` 返回正常 |
| 能否知道 forecast vintage/run？ | ❌ **否**。无 `run` 参数；文档明确"将各 run 的前几小时拼接成连续小时序列"（each run's first few hours stitched） |
| 每小时的取值来自哪个 run？ | **valid time 之前最近一次初始化的 run（近实时）**。实测：`2026-05-02T00:00` 的取值 = 2026-05-01 **06Z** run 的 lead 18 h（30.8），而非 00Z run 的 lead 24 h（30.7）→ 即"该小时前 ~6 h 才起报"的 nowcast |
| 能否证明 10:00 PT 前可用？ | ❌ **不能**。run 时点贴近 valid time，**必然晚于 D−1 决策时点**（valid time = T，run 在 T 前数小时才初始化，而决策时点在 T−1 10:00 PT） |
| 能否用于严格 as-of backtest？ | ❌ **不能**。等价于"近乎实时的模型再拼接"，不是当时发布的 D+1 预报 |
| 获取方式 | 免费 API，`start_date`/`end_date` + `hourly`，无鉴权 |

**结论**：只适合获取"模型对过去某时刻的临近预报"用于对比/展示，**不可用作 D+1 as-of 特征**。

---

## 4. Open-Meteo Previous Runs API

**端点**：`https://previous-runs-api.open-meteo.com/v1/forecast`

| 问题 | 实测/文档结论 |
|---|---|
| 能否回溯历史？ | ✅ 是。文档："Most models are archived from **January 2024**；GFS 2 m temperature 回溯至 **March 2021**"。实测：GFS `temperature_2m_previous_day1` 在 **2021-04-01** 有值；`shortwave_radiation_previous_day1` / `wind_speed_100m_previous_day1` 在 **2024-02-15 ~ 2025-07** 均有值。**注意：2024-01-15 实测返回 None（档案起点边界存在缺口）** `[待确认]` |
| 能否知道具体 forecast vintage/run？ | ⚠️ **部分**。变量后缀 `_previous_dayN` = "在 valid time 之前 N×24 h 做出的预报"（N=0 当前 run，…，N=7）。实测交叉核对：`_previous_day1` 在 valid `2026-05-02T00:00` 的取值（30.7）**与 Single Runs `run=2026-05-01T00:00` lead+24 h（30.7）完全一致** → 确认 `_previous_day1` = 恰在 valid time 前 24 h 初始化的 run。但**响应不返回 run init 时间**；对非 00/06/12/18 整点 valid hour，6 小时间隔 run 之间存在**插值歧义**，具体 run 不可精确定位 `[待确认]` |
| 能否证明在 10:00 PT 前可用？ | ⚠️ 依 N 而定。`_previous_day1` 的 run 初始化于 valid time 前 ~24 h（= D 日对应小时）。对 valid hour 0–11，run 为 D 00Z/06Z → 发布早于 cutoff ✓；对 valid hour ≥12，run 可能为 D 12Z/18Z → **发布可能越过 cutoff** ✗。`_previous_day2` 及更早的 init 在 D−1 之前 → 确定早于 cutoff ✓（但时效更旧） |
| 能否用于严格 as-of backtest？ | ⚠️ **次选**。固定 lead-time 对齐设计（官方推荐用于 skill/MAE/bias 分析）；对"重建交易员当时所见最新 D+1 预报"存在 run 选择歧义；若只求"某个 D+1 24 h 序列确实在决策时可得"，`_previous_day2`（init D−1）可作保守安全选择 |
| 获取方式 | 免费 API；示例：`?hourly=temperature_2m_previous_day1,temperature_2m_previous_day2&past_days=7&forecast_days=1` |

**注意**：ECMWF IFS 的 previous runs 在 2025-07 实测返回 **None**（GFS 正常）——IFS previous-runs 档案在早于某时段的覆盖存在缺口 `[待确认]`。

---

## 5. Open-Meteo Single Runs API

**端点**：`https://single-runs-api.open-meteo.com/v1/forecast`

| 问题 | 实测/文档结论 |
|---|---|
| 能否回溯历史？ | GFS：**仅 2026-04-02 起**（实测 2026-04-02T00 OK、2026-04-01T00 报错）。ECMWF IFS：**2024-03-14 起**（实测 2024-03-14T00 OK、2024-03-13T00 报错）→ **GFS 不覆盖 2024/2025，IFS 覆盖** |
| 能否获取指定历史 run？ | ✅ 是。`run=YYYY-MM-DDTHH:MM`（UTC，**不含秒**，6 小时 cycle 00/06/12/18 必须匹配）。实测任意有效 cycle 均返回该 run 的完整预报 |
| 能否获取某 run 的逐日序列？ | ✅ 是。`forecast_hours`/`forecast_days` 控制输出窗口，lead 相对 run init 计算。实测 IFS 返回 240 h（10 天）逐小时；GFS hourly |
| 能否证明在 10:00 PT 前可用？ | ✅ 用 **D 日 00Z 或 06Z run**：发布 ~4–6 h 后（Open-Meteo 文档），00Z → ~04–06 UTC、06Z → ~10–12 UTC，均早于 17:00/18:00 UTC cutoff。**12Z run 发布 ~16–18 UTC 临界**，不建议用于严格回测 |
| 能否用于严格 as-of backtest？ | ⚠️ **条件性**：run 可溯性完全成立；但 **GFS 档期 2026-04-02 起不覆盖 2024/2025**；**IFS 是 Cycle 49R1 的 6 小时 hindcast（回顾性重算），非当时实发的运行预报**（见下） |
| 获取方式 | 免费 API；示例：`?latitude=..&longitude=..&run=2025-07-01T00:00&hourly=temperature_2m,shortwave_radiation,wind_speed_100m&models=ecmwf_ifs&forecast_hours=48` |

### 5.1 IFS "hindcast" 性质（重要 caveat）

- Open-Meteo 官方文档原文："ECMWF IFS HRES 9 km：from 14 March 2024（**IFS Cycle 49R1 hindcasts**）；from 12 May 2026 06 UTC runs use Cycle 50R1"。
- **实测**：Single Runs 的 IFS 接受 **06Z / 18Z** run（2025-07-01T06:00、18:00 均返回数据）；而**业务运行的 ECMWF IFS HRES 每天只在 00Z 与 12Z 起报**（ECMWF 官方 medium-range 文档确认 00/12 UTC）。→ 6 小时间隔的 IFS 序列**不是**业务实发的 HRES，而是以固定 49R1 版本**回顾性重算**的 hindcast。
- 含义：IFS hindcast 的初值/物理版本与"当时真正发布的运行预报"不一致，数值接近但不相等。**对"严格 as-of（当时实发预报）"这是缺陷**；对"同版本模型确定性重算"是可接受的代理。是否接受由 Lead 裁决。

---

## 6. 对比总表

| 维度 | NOAA GFS 原始档案 | Open-Meteo Historical Forecast | Open-Meteo Previous Runs | Open-Meteo Single Runs (IFS) | ERA5 |
|---|---|---|---|---|---|
| 端点 | NCEI HTTPS/TDS（GRIB2/GRIB） | `historical-forecast-api.open-meteo.com` | `previous-runs-api.open-meteo.com` | `single-runs-api.open-meteo.com` | Copernicus CDS |
| 能否回溯 2024/2025 | ✅ 0.5° 自 2006、1.0° 自 2005 | ✅ GFS 自 2021-03-23 | ✅ GFS t2m 自 2021-03、ssrd/wind 自 2024-02 | ✅ IFS 自 2024-03-14；❌ GFS 仅 2026-04-02+ | ✅ 1940–今 |
| vintage/run 可溯 | ✅ 完全（run 文件名含 cycle） | ❌ 无 run 概念（拼接） | ⚠️ `_previous_dayN` = valid−N×24 h，整点精确、非整点有插值歧义；响应无 init 时间 | ✅ 完全（`run=` UTC init 时间） | ❌ 无 run 概念（再分析） |
| 发布时间 | 官方无数字 `[待确认]`；实际 ~3.5–5 h | 每值取自"valid time 前最近 run"（nowcast） | run 在 valid−N×24 h，N≥2 必早于 cutoff；N=1 视小时而定 | run 后 4–6 h（Open-Meteo 文档） | 最终产品**滞后实时约 5 天**（ERA5T 近实时亦 ~5 天）`[待确认]` |
| 10:00 PT 前可用 | ✅ 00Z/06Z 确定；12Z 临界 | ❌ 不可（nowcast） | ⚠️ N=1 部分小时临界；N≥2 确定 | ✅ 用 D 日 00Z/06Z；12Z 临界 | ❌ 不可（滞后数天） |
| 严格 backtest 适用 | ✅ **是（唯一 as-issued）** | ❌ 否 | ⚠️ 次选（run 歧义 + IFS 早段覆盖缺口） | ⚠️ IFS：run 可溯但 hindcast 非实发；GFS 档期不覆盖 2024/2025 | ❌ 否（再分析，非预报） |
| 获取方式 | GRIB2 下载+cfgrib/pygrib 解码，工程量大 | 免费 REST，最省事 | 免费 REST，`_previous_dayN` 变量后缀 | 免费 REST，`run=` 参数 | CDS API（需注册/申请） |
| 本项目三变量 | ✅ t2m/ssrd/wind 全有（0.5° 3 小时间隔） | ✅ t2m/ssrd/wind100 | ✅ 实测 2024-02+ t2m/ssrd/wind100 | ✅ 实测 t2m/ssrd/wind100 | ✅ 但为事后实际天气 |

---

## 7. 推荐

### 推荐 1（严格 as-of，2024/2025）—— NOAA GFS 原始档案（NCEI）

- 取 **D 日 00Z 或 06Z run**（init 均早于 cutoff，发布 ~4–6 h 后确定在 10:00 PT 前完整），对 **T=D+1** 取 lead 24–48 h / 18–42 h 的 2 m 温度、向下短波辐射、100 m 风速。
- 理由：唯一"当时真实发布过"的预报（as-issued），run/vintage 完全可溯，覆盖 2024/2025。
- 注意：
  - 用 **0.5°** 预报档案（3 小时步长 +000–+192 h 够用；0.25° 历史可溯性 `[待确认]`）。
  - 2024 距当前 ~2 年，0.5° 在线 HTTPS 浏览可能只到近 2 年，更早走 **THREDDS/HAS**。
  - 需要 cfgrib/pygrib 解码 GRIB2/GRIB；3 小时间隔需插值到小时（或直接用 3 h 值 + hour 特征对齐）。
  - **不要用 12Z run**（发布与 10:00 PT 临界）。

### 推荐 2（API 路线，接受"同版本 hindcast 代理"）—— Open-Meteo Single Runs API + ECMWF IFS

- 调用：`run = D 日 00:00 UTC`（或 `06:00 UTC`），`models=ecmwf_ifs`，`forecast_hours` 取到 T=D+1 末小时，`timezone=UTC`。
- 理由：run 完全可溯、覆盖 2024-03-14+、免费 API、实测三变量齐全；发布 ~4–6 h 后早于 cutoff。
- 注意（必须写进特征文档）：**该 IFS 档案为 Cycle 49R1 6 小时间隔 hindcast，非当时实发运行预报**，数值与 as-issued 有差异；GFS 单 run 档期 2026-04-02 起，**不能用于 2024/2025**；变量单位需与 `zone_weather_hourly.csv` 对齐（`temperature_2m`↔`t2m_c`°C，`shortwave_radiation`↔`ssrd_wm2` W/m²，`wind_speed_100m`↔`wind100` m/s）。

### 推荐 3（快速探索/校验，非最终 as-of 源）—— Open-Meteo Previous Runs API + GFS

- 调用：`hourly=temperature_2m_previous_day1,shortwave_radiation_previous_day1,wind_speed_100m_previous_day1`，覆盖 2024-02+（2021-03+ 仅温度）。
- 注意：`_previous_day1` 对 T 的晚段 valid hour 存在 run 发布越过 cutoff 的风险 → 若需严格，用 `_previous_day2`（init D−1，必早于 cutoff）并接受时效旧 1 天；run 精确身份（尤其非整点小时）`[待确认]`。

### 明确排除

- **Open-Meteo Historical Forecast API**：取值是"valid time 前最近 run"的 nowcast 拼缝，晚于决策时点，**不满足 as-of**。
- **ERA5**：再分析（模型+观测事后融合），滞后实时约 5 天，**不是预报**，仅可作为"事后实际天气"的滞后特征（与仓库现有 `t2m_lag1` 等一致）。

---

## 8. `[待确认]` 清单

1. **GFS 官方发布延迟数字**：NCO/NOMADS/AWS/NCEI 官方页面均未给出精确的 "init 后几小时发布" 数字；目前采用 Open-Meteo 文档的 "global models 4–6 h" 与实际惯例 ~3.5–5 h。→ 严格证明 **D 日 12Z run 在 10:00 PT 前完整**不成立，故不推荐 12Z。
2. **NCEI 0.25° 预报的长期历史可溯性**：NCEI 在线档案明确列出 0.5°/1.0°；0.25° 是否在 NCEI 或其它长期档案可回溯 2024/2025 未核实（AWS 仅为 30 天滚动窗口）。
3. **Previous Runs API 的精确 run 选择规则**：`_previous_dayN` 对非 00/06/12/18 整点 valid hour 由哪个 run（含插值方式）提供，官方未说明；实测仅验证了整点（00Z 精确匹配）。
4. **Previous Runs GFS 2024-01 覆盖缺口**：文档称自 Jan 2024，实测 2024-01-15 返回 None、2024-02-15 起正常——起点边界行为未知。
5. **Previous Runs ECMWF IFS 早段覆盖**：实测 2025-07 IFS `_previous_day1` 返回 None（GFS 正常），IFS previous-runs 档案在 2024/2025 的覆盖存在缺口。
6. **Open-Meteo IFS hindcast 的初值来源**：文档称 "49R1 hindcasts"，但未说明初值取自业务同化还是再分析（ERA5），故与 as-issued 预报的偏差幅度未知。
7. **ERA5 发布延迟精确值**：ECMWF/Copernicus 页面抓取被拒（401/404）；"最终产品滞后实时约 5 天（ERA5T 近实时亦 ~5 天）"为业界公认但本次未能页面级验证。
8. **ECMWF IFS 业务实发预报是否有简单 API**：MARS 需授权；Open-Meteo 是否另有商业渠道提供 as-issued IFS 未核实。

---

## 附：实测验证记录（2026-08-09，curl 直连）

| # | 验证项 | 请求要点 | 结果 |
|---|---|---|---|
| 1 | Historical Forecast 回溯 2025 | `historical-forecast-api...start_date=2025-07-01&models=gfs_global` | ✅ 返回 2025-07-01 逐小时 t2m |
| 2 | Historical Forecast 拼接语义 | valid 2026-05-02T00 取值 vs 各 run | 30.8 = 05-01 **06Z** run lead+18 h（非 00Z lead+24 的 30.7）→ nowcast 拼缝 |
| 3 | Previous Runs 回溯 2021/2024/2025 | `previous-runs-api...start_date=2021-04-01/2024-02-15/2025-07-01&models=gfs_global` | ✅ t2m（2021+）；ssrd/wind100（2024-02+）；**2024-01-15 = None** |
| 4 | Previous Runs run 身份 | `_previous_day1`@2026-05-02T00 = 30.7 vs Single Run 05-01T00 lead+24 = 30.7 | ✅ 完全一致 → 等于 valid−24 h 的 00Z run |
| 5 | Previous Runs IFS 早段 | `models=ecmwf_ifs` @2025-07-02 | ⚠️ None |
| 6 | Single Runs GFS 档期起点 | run=2026-04-01T00 / 2026-04-02T00 | ❌/✅ → 起点 **2026-04-02** |
| 7 | Single Runs IFS 档期起点 | run=2024-03-13T00 / 2024-03-14T00 | ❌/✅ → 起点 **2024-03-14** |
| 8 | Single Runs IFS 6 小时间隔 | run=2025-07-01T06:00 / 18:00 | ✅ 均返回 → 非业务 HRES（00/12Z）→ hindcast |
| 9 | D+1 取数模式 | run=2026-05-05T06:00，target 2026-05-06（lead 18–41 h） | ✅ t2m/ssrd/wind100 三变量齐全 |
| 10 | GFS 四 cycle | NCEI/NOMADS/AWS | ✅ 00/06/12/18 UTC，384 h，0.25° hourly / 0.5–1.0° 3-hourly |

> 主模型无视觉能力，本报告不涉及图片；全部证据为文本/API 响应，可直接复验（命令见各节端点）。
