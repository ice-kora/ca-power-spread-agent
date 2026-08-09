# Evidence 数据源接入报告（V0.2.1）· 真实历史 as-of 数据源

> 作者：Agent D（Case 自动生成 + Evidence 数据源工程师）｜ 日期：2026-08-09
> 任务：只接一个真实 as-of Evidence 数据源，不伪造。第一优先 = 真实历史 D+1 Weather
> Forecast Archive（含 forecast_issue_time 与 target_time）；找不到就诚实写"不可得"。

---

## 0. 结论先行（TL;DR）

**第一优先已获得：NCEP GFS 历史预报档案（Open-Meteo Single Runs API）。**

- **数据源**：Open-Meteo `single-runs-api.open-meteo.com` 存档的 **NCEP GFS 0.25°（`ncep_gfs025`）** 逐小时历史模型 run。
- **发布时间（published_at）口径**：`forecast_issue_time` = GFS run 初始时刻（UTC），本项目取 **decision_date 12Z（12:00 UTC）**，恒早于决策 cutoff（decision_date 10:00 PT = 17:00 UTC（夏令时）/ 18:00 UTC（冬令时））。
- **是否 historical as-of safe**：**是**（在 GFS 档案覆盖窗口内）。每个 run 用 `run=YYYY-MM-DDTHH:00` 按初始时刻检索，返回的就是该时刻已发布的预报，不是 actual/ERA5 反演；`forecast_issue_time <= decision_cutoff` 恒成立。
- **覆盖**：GFS single-runs 档案从 **2026-04-02** 起。**test 窗口（2026-06-02 ~ 08-05）全量覆盖**；val 窗口（2026-01-02 ~ 06-01）仅 04-02 后覆盖（见 §4 诚实边界）。
- **接入方式**：`agent/evidence/gfs_forecast.py`（真实 fetcher）→ `FETCHER_REGISTRY["WEATHER_FORECAST"]` → `fetch_evidence()` 返回真实 Evidence → `agent/evidence/time_gate.py` 做 `decision_eligible` 程序裁决（§5）。
- **方向判定**：`directional_effect=UNCERTAIN`（预报不直接决定 Return 方向；宁保守不越权）。

> 未采用第二优先（CAISO Load Forecast 等），因为第一优先已成立且直接命中本项目"负电价
> 由可再生供给过剩驱动"的最大亏损机制（温度/辐照/风均与 CAISO 供需直接相关）。

---

## 1. 数据来源详情

### 1.1 选型过程（按任务要求逐项核实）

| 候选 | 结论 | 原因 |
|---|---|---|
| **NCEP GFS 历史预报档案**（经 Open-Meteo Single Runs API） | ✅ **采用** | 真实 as-issued 历史 run；有明确 `run=`（= forecast_issue_time）；含温度/风/辐照；免费无 key；test 窗口全覆盖 |
| NOAA NOMADS 直接拉历史 GFS GRIB | ⚠️ 备选，未采用 | 无正式历史档案承诺、需解析 GRIB、逐文件拼装成本高；且 Open-Meteo 已做统一归档 |
| Open-Meteo Historical Forecast API（seamless） | ⚠️ 未采用 | 把多个 run 前若干小时缝成连续序列，**不保留单个 run 的 issue 时间**，as-of 语义不如 Single Runs 干净 |
| Open-Meteo Historical Weather API（ERA5 / IFS 反演） | ❌ 明确排除 | 是 **reanalysis / 用最新模型版本重跑**，不是交易员当时能看到的真实预报；**绝不用它冒充 forecast** |
| CAISO OASIS Load Forecast | 未到第二步 | 第一优先已成立（且该源历史发布时间戳不易逐日核实） |

### 1.2 最终数据源

- **端点**：`https://single-runs-api.open-meteo.com/v1/forecast`
- **模型**：`models=gfs_global`（= NCEP GFS 0.25°，`ncep_gfs025`）
- **参数**：`run=YYYY-MM-DDTHH:00`（UTC 初始时刻，必填）、`hourly=temperature_2m,wind_speed_100m,shortwave_radiation`、`wind_speed_unit=ms`、`timezone=UTC`
- **变量口径**：`temperature_2m`（°C，对齐 `t2m_c`）、`wind_speed_100m`（m/s，对齐 `wind100`）、`shortwave_radiation`（W/m²，对齐 `ssrd_wm2`）
- **坐标**：按节点实际经纬度（来源 `节点位置.xlsx`）：SNLNDRO (37.711, −122.149)、CONTROLX (37.343, −118.472)、ELCA (32.795, −116.972)

### 1.3 数据真实性 / provenance（诚实声明）

Open-Meteo 文档明确 Single Runs API 提供"individual weather model runs by their
initialisation time"，即按初始时刻归档的**历史运行预报**，区别于 ERA5/IFS 重算的
Historical Weather API（文档原话：Historical Weather API "based on reanalysis
datasets"、IFS 历史是"re-run with the current model version"，两者都不适合回测
预报 skill）。我们**未独立于 Open-Meteo 再交叉验证**其档案逐日 provenance——
这是诚实边界：Open-Meteo 是一家可商用预报档案服务商，其 as-issued 语义是业界标准做法，
但本项目仅以官方文档为准，未对每个历史 run 做 NOMADS 对拍。

---

## 2. 发布时间（published_at）口径

```
target_date T（交付日）          ← 天气预报的目标日
decision_date D = T−1            ← 决策日（DAM bid cutoff 当日）
decision_cutoff = D 10:00 PT     ← 官方 DAM Market Close（market_timeline.md 已核验）

GFS 12Z run 于 D 12:00 UTC 初始（= D 05:00 PT），
实际发布 ~ D 15:30 UTC（= D 08:30 PT，GFS 产品约 init 后 3.5h 可得）
⇒ forecast_issue_time = D 12:00 UTC < decision_cutoff（D 17:00 UTC（夏令时）/ 18:00 UTC（冬令时））✅
```

- **published_at = forecast_issue_time** = GFS run 初始时刻（UTC naive），本项目固定取 **12Z**。
- **decision_cutoff** 在 Evidence 中写为 **decision_date 10:00 PT 换算后的 UTC naive**（`agent/evidence/gfs_forecast.py::decision_cutoff_utc`，用 `America/Los_Angeles` 换算，冬/夏令时自动正确）。
- **12Z vs 00Z 的取舍**：12Z 是 cutoff 前最新一跑（真实交易员会用最新预报）；即便计入 ~3.5h 发布延迟（08:30 PT）仍早于 10:00 PT cutoff。若审计要求更高保守度，可把 `DEFAULT_CYCLE` 改为 `00Z`（D 00:00 UTC = D-1 17:00 PT 初始化，次日清晨前即可见，绝对无争议）。本项目默认 12Z，两种选择都 `decision_eligible=True`。
- **as-of 硬约束**：`Evidence.decision_eligible = (published_at <= decision_cutoff)`，由 `time_gate.py` **程序计算**，LLM 禁止自行判断。`agent/evidence/gfs_forecast.py::build_gfs_evidence` 产出的证据自洽（published_at=12:00 UTC ≤ cutoff=17:00/18:00 UTC → True）。

---

## 3. 是否 historical as-of safe：是（有明确边界）

| 项 | 判定 |
|---|---|
| 是否为当时真实可见的预报（非 actual/反演） | ✅ 是（Single Runs = as-issued 历史 run，非 ERA5/重跑） |
| 是否有可审计的 issue 时间 | ✅ `run=`（UTC 初始时刻）= published_at |
| `forecast_issue_time <= decision_cutoff` 是否恒成立 | ✅ 12:00 UTC < 17:00（夏）/18:00（冬）UTC |
| 失败是否伪造 | ❌ 不伪造——API 失败/该 run 不存在 → 返回空列表 |
| 方向判定 | `UNCERTAIN`（预报不直接决定 Return 方向） |
| **覆盖窗口** | GFS single-runs 档案 **2026-04-02 起**；**test 2026-06-02~08-05 全量覆盖**，val 仅 04-02 后 |

实测（2026-08-09）：`run=2026-07-08T12:00` 对三个节点均返回 168 小时（含 target
2026-07-09 全部 24 小时），温度/风/辐照数据完整度 100%，`decision_eligible=True`。

---

## 4. 诚实边界 / 局限

1. **档案起点 2026-04-02**：GFS single-runs 只覆盖 04-02 至今。**test 窗口（06-02 起）不受影响**；val 窗口（01-02~06-01）只能覆盖尾部。若需完整 val 回测 as-of 证据，要么接受 04-02 后子集，要么另寻档案（本项目不因此伪造 04-02 前的预报）。
2. **provenance 以 Open-Meteo 官方文档为准**：未逐日与 NOMADS 对拍（诚实边界见 §1.3）。
3. **GFS 0.25° 网格分辨率**：单点坐标取网格最近值（elevation 以模型网格为准，如 CONTROLX 处约 1422m），不精确代表节点海拔；作为"天气证据"足够，作训练特征需另行校准。
4. **12Z 发布延迟 ~3.5h 是 NCEP 常规经验值**，个别日可能更晚；已用 12:00 UTC init 时间 + cutoff 17:00 UTC 留足 ~5h 余量，且可切 00Z 加保险。
5. **只接一个数据源**：本报告只接入 GFS 天气预报。RENEWABLE_GENERATION（CA-ISO 实际可再生出力）、LOAD_FORECAST_REVISION 等仍为 TODO，**不冒充已接入**。

---

## 5. 如何进入 Agent（fetcher 接入 + Evidence Time Gate）

### 5.1 接入链路

```
code/risk_gate 决策流水线
  ④ Case Retrieval / ⑤ Risk Gate / ⑥ Rule Engine
        ↑ 只消费 decision_eligible=True 的 Pre-decision Evidence
③ Evidence Time Gate（agent/evidence/time_gate.py）
        ↑ split_eligible() 程序切分 eligible / post_decision
② Agent Evidence Collection（agent/evidence/fetcher.py::fetch_evidence）
        ↑ FETCHER_REGISTRY["WEATHER_FORECAST"] = "agent.evidence.gfs_forecast:fetch_gfs_weather_evidence"
        ↑ 调用真实 fetcher，返回 Evidence dict
① 真实数据源：Open-Meteo Single Runs API（NCEP GFS 历史 run）
```

### 5.2 代码实现

- **真实 fetcher**：`agent/evidence/gfs_forecast.py`
  - `fetch_forecast_df(node, decision_date, cycle="12Z")`：抓 GFS run 的逐小时预报
  - `build_gfs_evidence(node, decision_date, cycle="12Z")`：构建一条 Evidence（published_at=12Z run 时间，decision_cutoff=D 10:00 PT→UTC，`directional_effect=UNCERTAIN`，confidence=24h 完整度）
  - `fetch_gfs_weather_evidence(node, decision_date, hours=None, cycle="12Z")`：注册回调
- **注册表**：`agent/evidence/fetcher.py::FETCHER_REGISTRY` 新增 `"WEATHER_FORECAST": "agent.evidence.gfs_forecast:fetch_gfs_weather_evidence"`
- **fetch_evidence 路由**：`fetch_evidence()` 对已注册源调用真实回调；未接入源仍输出 UNCERTAIN 占位（可见"哪些源待接入"）
- **Time Gate**：`Evidence.decision_eligible` 由 `time_gate.py` 程序计算；`assert_no_post_decision` 兜底（Post-decision 证据误入决策层直接抛错）
- **事件类型**：`schema.py::KNOWN_EVENT_TYPES` 新增 `"WEATHER_FORECAST"`

### 5.3 最小示例（真实运行）

```
>>> from agent.evidence.fetcher import fetch_evidence
>>> fetch_evidence("CONTROLX_1_N001", "2026-07-08", event_types=["WEATHER_FORECAST"], include_placeholders=False)
[{'evidence_id': 'GFS-12Z-2026-07-08-CONTROLX_1_N001',
  'event_type': 'WEATHER_FORECAST',
  'published_at': '2026-07-08T12:00:00',          # forecast_issue_time
  'decision_cutoff': '2026-07-08T17:00:00',       # D 10:00 PT → UTC（PDT）
  'decision_eligible': True,                        # 程序计算：12:00 <= 17:00
  'directional_effect': 'UNCERTAIN',
  'confidence': 1.0,                                # 24h 完整度（证据质量，非价格概率）
  'summary': 'D+1(2026-07-09) GFS 12Z 预报：t2m 均值 27.9°C，wind100 3.3 m/s，ssrd 384 W/m² …'}]
```

---

## 6. 与 Case 自动生成的关系（本轮一并交付）

- Case 自动生成（`agent/case_library/policy.py` + `auto_generate_cases.py`）不依赖本数据源，
  只依赖 test 预测 + 回测口径（PnL / 方向 / Risk Gate 裁决）。
- 自动生成的 `cases_auto.json`（319 条）与人工整理的 `cases.json`（18 条）都带
  `case_available_at`；检索侧（`code/risk_gate/case_adapter.py`）已接 `policy.is_retrievable`
  硬约束（`case_available_at <= decision_time`，严格防 Case 穿越）。
- GFS 天气预报作为决策时点可检索的 as-of 证据，未来可挂到 Decision Card / Case 的
  `event_evidence`（当前 Case 的 `event_evidence` 仍为 UNCERTAIN 占位，属另一接入步骤）。

---

## 7. 验收自检

| 检查项 | 结果 |
|---|---|
| 只接一个数据源 | ✅（GFS 天气预报；其余源未接入） |
| 第一优先可用 → 未伪造 / 未用 ERA5 冒充 | ✅（Single Runs = as-issued 历史 run） |
| published_at 有明确口径 | ✅ forecast_issue_time = run 初始时刻（UTC） |
| as-of safe（issue <= cutoff）恒成立 | ✅ 12Z=12:00 UTC < 17:00/18:00 UTC |
| Time Gate 程序裁决 | ✅ decision_eligible 由 time_gate.py 计算 |
| 失败不伪造 | ✅ 空列表返回 |
| 文档诚实标注覆盖/provenance 边界 | ✅ 见 §4 |
