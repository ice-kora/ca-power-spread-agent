# As-of 数据结构设计（Agent D · 可追溯 / 防穿越数据层）

> ## SUPERSEDED / HISTORICAL DESIGN RECORD
>
> 本文档是 **V0.2** As-of 数据结构**设计稿**，**NOT CURRENT IMPLEMENTATION**。
> 其核心语义（`available_at <= decision_cutoff`、GFS 00Z/06Z 可回测、12Z/18Z 无可靠 vintage 不可回测）
> 已在当前实现落地，但以**实现代码为准**：
> - 当前 Time Gate 唯一判据：`agent/evidence/time_gate.py` + `agent/evidence/schema.py`
>   （available-at-only，缺失 → MISSING_AVAILABLE_AT，不 fallback published_at）
> - 当前采集实现：`code/data_acquisition/weather_gfs.py`（GFSWeatherCollector）
> - 当前市场规则版本：`code/market_rules.py`（`market_rule_version_for(date)`，date-aware）
>
> V0.2 设计稿保留用于项目演进溯源。

> 作者：Agent D（As-of 数据结构设计师）｜ 日期：2026-08-09
> 版本：asof_v1 ｜ 配套代码：`code/data_acquisition/schemas.py` + `test_schemas.py`
> 范围：**只定义数据结构、采集模式、feature_snapshot 标准；不改模型 / 回测 / 现有 evidence 层**。

---

## 0. TL;DR

任何 Forecast / Evidence / Feature 不能只存 `target_time + value`，必须保存完整 **vintage**
（来源、发布时间、可用时点），否则无法证明交易决策时该信息可见。

- **核心铁律**：`available_at <= decision_cutoff` ⇒ `time_eligible = TRUE`；
  否则只能进 **Post-trade Review**。
- **保守规则**：`available_at / decision_cutoff / target_time / value` 任一缺失或不可解析
  ⇒ 该记录**不可用**（`time_eligible = FALSE`）。
- **eligibility 三拆**（Agent B，MOCK / NOT_BACKTEST_SAFE 硬隔离）：
  - `time_eligible`       = 纯时间门槛（`available_at <= decision_cutoff`）
  - `backtest_eligible`   = `time_eligible` 且 非 MOCK 且 非 NOT_BACKTEST_SAFE
  - `production_eligible` = `time_eligible` 且 非 MOCK
  - `decision_eligible`   = 模式对应单一门槛（PRODUCTION→production_eligible；
                            其余 BACKTEST/空 → backtest_eligible，最保守）
  - **硬规则 R7**：`is_mock=True` ⇒ `backtest_eligible=FALSE` 且 `production_eligible=FALSE`，
    即便 `available_at <= cutoff` 也不能改变（MOCK 只能用于测试/UI 演示/单元测试）。
  - **硬规则 R8**：`not_backtest_safe=True` ⇒ `backtest_eligible=FALSE`（不禁 production）。
- **两套采集模式**：
  - **Historical Backtest Mode**：复原"某历史交易日 D 10:00 PT 前当时能知道什么"，
    用**历史 vintage**（如 GFS 档案 run 初始时刻）作 `available_at`；**禁止**用今天的检索时刻
    冒充过去可知，**禁止**用事后实际值回填成预报。
  - **Production Mode**：Decision Day 在 cutoff 前自动拉取最新 Forecast/Market，
    **保存 raw response** → 记录 `retrieved_at` → 生成当日 feature_snapshot。
- **feature_snapshot**：每个特征值带 `available_at + decision_eligible + asof_record_id`，
  可追溯"这个数当时从哪来"。

---

## 1. 时间模型与时区约定（先定基线，否则一切比较无意义）

### 1.1 决策时点（业务契约冻结，`docs/market_timeline.md` 官方核验）

| 项 | 值 | 说明 |
|---|---|---|
| target_date T | 交付日（D+1） | 预测的负荷/价格生效日 |
| decision_date D | T − 1 | 提交虚拟报价的当日 |
| decision_cutoff | **D 日 10:00 PT**（DAM Market Close / bid cutoff，官方 BPM "closes at 1000 hours"） | 决策层硬边界 |
| label 可见时点 | T 的 DA 于 **D 日 13:00 PT** 发布；RTPD 于 T 日实时 | 事后结算，绝不进 X |

### 1.2 时区约定（本层强制）

| 约定 | 值 |
|---|---|
| 市场计时 | Pacific Time（PT），4–10 月 PDT（UTC−7），冬季 PST（UTC−8） |
| **本层存储口径** | 所有时间字段一律为 **UTC naive ISO 8601**（`YYYY-MM-DDTHH:MM:SS`，无后缀） |
| 转换规则 | 原生 PT naive 的时间（CAISO 排程、`valid_pt`）**在入库前**经 `pt_naive_to_utc_naive()` 转 UTC；带偏移的字符串（`+08:00` / `Z`）由 `parse_timestamp()` 归一化 |
| 比较规则 | `decision_eligible` 只允许在"同一 UTC naive 口径"下比较；混入 PT naive 或 aware 戳 = 判定不可用（宁保守不穿越） |

> 说明：`agent/evidence/gfs_forecast.py` 已按 UTC naive 产证据；本层与之对齐。现有
> evidence schema 部分字段是 PT naive 字符串，接入时须显式转换，不能混比。

### 1.3 target_time 与 hour 的映射（与 `read_data.py` / `canonical.py` 对齐）

`hour` ∈ 1..24，`H1 = 00:00–01:00`（`valid_pt` 0:00 → H1）。
因此 `(target_date, hour=h)` 的目标时刻 = `target_date (h−1):00:00 PT`，再转 UTC naive。
`lead_hours = (target_time − available_at)`（单位小时，实测历史可为负，属正常）。

---

## 2. As-of 数据 Schema（标准记录结构）

一行 As-of 记录 = **"某个时点，某源发布的某个字段，指向某目标时刻的一个值"**（vintage 化的
原子事实）。`code/data_acquisition/schemas.py::AsOfRecord`。

### 2.1 字段定义

| 字段 | 类型 | 必填 | 含义 / 格式 | 来源 / 采集说明 |
|---|---|---|---|---|
| `asof_id` | str | 是 | 唯一主键：`ASOF-{source}-{raw_source_id}-{target_time}`（缺省自动生成） | 采集时生成，防重复落库 |
| `source` | str | 是 | 数据源标识，如 `CAISO_OASIS_DA_LMP`、`NCEP_GFS_025_via_OpenMeteo`、`load_2da_csv` | 采集器声明；登记表见 §5 |
| `field_name` | str | 是 | 变量名（与 canonical 特征名对齐）：`da_lmp / rtpd_lmp / darptd_return / t2m / ssrd / wind100 / load_2da / load_actual` | 采集映射 |
| `forecast_run` | str | 否 | 预报 run 标识：GFS `2026-07-08T06:00Z`、2DA 批号等；**实测/实际值留空** | 源响应 header / API 参数 |
| `model_run_time` | str | 否 | 模型起报时刻（run 起始 = `initialization_time`，UTC naive）。GFS 必填；实际值留空 | GFS run 参数（`run=...T06:00`） |
| `issue_time` | str | 否 | 数据产品生成/模型初始化时刻（UTC naive）= `model_run_time`；预报必须给；实际可为空 | 源元数据（GFS run 初始时刻等） |
| `published_at` | str | **是** | 源方**公开发布**时刻（交易员最早可得，UTC naive）。GFS = init + 发布延迟模型（PRODUCTION +4h / BACKTEST +6h 上界）；CAISO 负荷 = D 10:00 PT（== cutoff，保守上界） | 源 header / 排程表（§3 矩阵） |
| `available_at` | str | **是** | **本项目采用的可用于决策的 as-of 时点**（UTC naive）。回测=历史 `published_at`（vintage，GFS 00Z/06Z）；无可靠 vintage（GFS 12Z/18Z）→ `None`；生产=`max(published_at, retrieved_at)` | 由 `resolve_available_at()` 按模式计算 |
| `retrieved_at` | str | **是** | 我方采集/落库时刻（UTC naive）。**仅审计**；回测中绝不用它主张可用 | 采集器墙钟 |
| `target_time` | str | 是 | 该值指向的交付时刻（UTC naive）：`target_date (h−1):00 PT→UTC` | §1.3 映射 |
| `lead_hours` | float | 计算 | `(target_time − available_at)` 小时；不可算为 `None` | 派生 |
| `node` | str | 是 | 节点 ID（`SNLNDRO_1_N001` 等）；系统级数据用系统标识（如 `CAISO_TAC`） | `节点位置.xlsx` |
| `region` | str | 是 | `ZP26 / SP15 / NP15 / SYSTEM` | 节点→区域映射 |
| `latitude` | float | 否 | 节点纬度（可选，节点级） | `节点位置.xlsx` |
| `longitude` | float | 否 | 节点经度（可选，节点级） | `节点位置.xlsx` |
| `value` | float | 是 | 数值：价格 `$/MWh`、负荷 `MW`、温度 `°C`、辐射 `W/m²`、风速 `m/s`；缺失为 `NaN` | 源响应 |
| `decision_cutoff` | str | 是 | 该记录对应的决策截止（UTC naive）：`D 10:00 PT → UTC` | `make_decision_cutoff()` |
| `source_type` | str | 否 | 源语义类别：`PRICE / LOAD / WEATHER / EVENT / STATIC / DERIVED / UNKNOWN`（显式优先，否则启发式推断） | `infer_source_type()` |
| `is_mock` | bool | 是 | MOCK/降级标记：`True` = 确定性合成/演示数据，**永不进回测/生产**（R7） | 采集器 `provenance=="MOCK"` |
| `not_backtest_safe` | bool | 是 | 严格 as-of 回测不可用标记（如 OASIS 不暴露逐日发布戳）；**禁回测，不禁止生产**（R8） | 采集器声明 |
| `time_eligible` | bool | **计算** | R1/R2 纯时间门槛：`available_at <= decision_cutoff` | `property` 程序计算 |
| `backtest_eligible` | bool | **计算** | = `time_eligible` 且 非 `is_mock` 且 非 `not_backtest_safe` 且 非生产采集 | `property` 程序计算 |
| `production_eligible` | bool | **计算** | = `time_eligible` 且 非 `is_mock` 且 非回测采集 且 published/retrieved 齐备 | `property` 程序计算 |
| `decision_eligible` | bool | **计算** | 模式对应单一门槛（PRODUCTION→production_eligible；其余→backtest_eligible，最保守）；**禁止人工/LLM 改写** | `property` 程序计算 |
| `raw_source_id` | str | 是 | 原始响应/行 ID（API run id、CSV 行号、xlsx 单元格路径），供审计还原 | 采集器保存 raw response 时登记 |
| `version` | str | 是 | schema/数据版本，默认 `asof_v1` | 常量 |
| `mode` | str | 否 | `BACKTEST / PRODUCTION`（采集模式，审计用） | 采集器声明 |

### 2.2 强制规则（R1–R6，代码与文档单一来源）

- **R1（可用性铁律）**：`available_at <= decision_cutoff` ⇒ `decision_eligible = TRUE`；否则 `FALSE`。
- **R2（保守规则）**：`available_at` 或 `decision_cutoff` 缺失 / 不可解析 ⇒ `decision_eligible = FALSE`
  （宁保守不穿越）。
- **R3（完整可用）**：`is_usable = decision_eligible` 且 `target_time` 可解析且 `value` 非 `NaN`
  且 `source / field_name / node` 非空。任一不满足 ⇒ 该记录不可进入 feature_snapshot 的可用侧。
- **R4（回测不穿越）**：Backtest 模式中 `available_at` 必须来自**历史 vintage 元数据**
  （源的排程表 / 档案 run 时间），**禁止**用"今天的检索时刻"（`retrieved_at`）主张历史可用；
  **禁止**用事后实际值回填成"当时预报"。
- **R5（生产可审计）**：Production 模式中采集器必须**先保存 raw response**（登记 `raw_source_id`），
  再记录 `retrieved_at`（墙钟），`available_at = max(published_at, retrieved_at)`；
  拉取窗口在 cutoff 结束，cutoff 后到的新数据只进 Post-trade Review，不进当日 snapshot。
- **R6（快照不可变）**：feature_snapshot 为**追加写、不可变**；任何 `decision_eligible=FALSE`
  的记录只能进 `post_decision` / 复盘，绝不回填生产特征。
- **R7（MOCK 硬隔离，Agent B）**：`is_mock=True` ⇒ `backtest_eligible=FALSE` 且
  `production_eligible=FALSE`（MOCK 只能用于测试/演示；即便 `available_at <= cutoff` 也不能改变）。
- **R8（NOT_BACKTEST_SAFE 硬隔离，Agent B）**：`not_backtest_safe=True` ⇒
  `backtest_eligible=FALSE`（strict as-of 回测不可用；不禁止 production）。
- **R9（Time Gate 只判 available_at）**：`time_eligible` 只由 `available_at <= decision_cutoff`
  判定；**绝不判 `initialization_time`**。且 `available_at` 必须**严格晚于** `model_run_time`
  （发布/下载延迟为正；拒绝把 init 当 available 的退化）。
- **R10（无可靠 vintage 即不可回测）**：某历史 run 无法可靠证明真实 `available_at`
  （如 GFS 12Z/18Z，发布与 cutoff 临界或在其后）⇒ `available_at = None` ⇒
  `time_eligible = backtest_eligible = decision_eligible = FALSE`（不自行推测发布时刻）。

### 2.3 available_at 语义与 GFS 发布延迟模型（P0-1 修复，Agent A）

**Time Gate 只判 `available_at <= decision_cutoff`**，**绝不判 `initialization_time <= cutoff`**。
GFS run 的初始时刻 ≠ 数据真正可用时刻（存在发布/下载延迟）。

#### 五个时间概念（字段名保留英文）

| 概念 | 说明 | 字段 |
|---|---|---|
| `model_run_time` | 模型起报时刻（run 起始） | `model_run_time` / `issue_time` |
| `initialization_time` | = `model_run_time`（同义） | 同上 |
| `available_at` | 该 run 数据**真正可用**的时刻（发布/下载延迟后），Time Gate 唯一判据 | `available_at` |
| `retrieved_at` | 本次抓取时刻（墙钟，仅审计） | `retrieved_at` |
| `decision_cutoff` | D-1 10:00 PT → UTC（DAM Market Close / bid cutoff） | `decision_cutoff` |

#### GFS available_at 赋值策略（`weather_gfs.py`）

Open-Meteo Single Runs API 只暴露 run 的初始时刻，**不返回逐日发布时刻戳**，
因此 GFS 历史 run 的精确 `available_at` 不可直接观测。本项目采用**文档化的发布延迟模型**
（来源 `weather_forecast_sources.md` §2.2 / `evidence_source_v021.md` §2）：

- `GFS_PUBLISH_LAG_TYPICAL_H = 4.0`：典型发布延迟（NCEP 惯例首文件 ~3h15m、完整 ~4–4.5h；
  Open-Meteo "global models 通常 4–6 h 发布"）。**PRODUCTION** 的
  `published_at = init + 4h`（源方最早可得时刻的估计）。
- `GFS_PUBLISH_LAG_CEILING_H = 6.0`：保守上界。**BACKTEST** 的
  `available_at = init + 6h`（"该时刻必然已可用"的可证明上界）。

#### 可回测（有可靠 vintage）分类

| cycle | init (UTC) | 保守上界 available_at | vs cutoff（17:00 夏 / 18:00 冬 UTC） | backtest_eligible |
|---|---|---|---|---|
| 00Z | D 00:00 | D 06:00 | 严格早于 | ✅ TRUE |
| 06Z | D 06:00 | D 12:00 | 严格早于 | ✅ TRUE |
| 12Z | D 12:00 | D 18:00 | 夏=之后、冬=边界 | ❌ FALSE（无法可靠证明） |
| 18Z | D 18:00 | D+1 00:00 | 之后 | ❌ FALSE |

- **可回测（有 vintage）**：00Z / 06Z（`GFS_BACKTEST_SAFE_CYCLES`）。保守上界
  `init+6h` 仍严格早于 cutoff，可证明。
- **不可回测（FALSE）**：12Z / 18Z。12Z 与 cutoff 临界（冬令时上界 18:00 == cutoff 18:00，
  边界不算"可靠证明"）→ **不自行推测**更短发布延迟；18Z init 在 cutoff 之后。
  此类 run 在 BACKTEST 模式下 `available_at = None`（不解析）→
  `time_eligible = backtest_eligible = decision_eligible = FALSE`，**绝不进历史决策**。
- **PRODUCTION / Shadow（③）**：真实当前抓取，`available_at = max(published_at, retrieved_at)`，
  `retrieved_at` = 墙钟（"源没发布 + 我们没拉到"都不可用）。
- **不为让 Demo 有天气制造历史穿越**：拿不到可靠 `available_at` 就标不可用。

### 2.4 代码接口（`code/data_acquisition/schemas.py`）

| 函数 / 成员 | 作用 |
|---|---|
| `AsOfRecord`（dataclass） | 标准记录；`time_eligible / backtest_eligible / production_eligible / decision_eligible / is_usable / lead_hours` 为计算属性 |
| `parse_timestamp(v)` | 任意 ISO → UTC naive `datetime`；失败返回 `None` |
| `pt_naive_to_utc_naive(v)` | PT naive → UTC naive（zoneinfo，回退 DST 启发式） |
| `make_decision_cutoff(decision_date)` | `D 10:00 PT` → UTC naive ISO |
| `target_time_pt_to_utc(target_date, hour)` | `(target_date, hour)` → UTC naive ISO |
| `resolve_available_at(published_at, retrieved_at, mode)` | 按模式算 `available_at`（R4/R5） |
| `gate_asof_records(records, cutoff)` | 切分 `(eligible, post_decision)` |
| `validate_asof_record(rec)` | 返回所有违规项（空列表=通过） |
| `FeatureSnapshot` / `snapshot_from_asof_record(...)` | 快照结构（§4） |

---

## 3. 数据源 vintage 矩阵（本项目各源的 `available_at` 口径）

> 目标日 T；决策日 D = T−1；cutoff = D 10:00 PT。`available_at` 均指"交易员最早可得的保守时点"。

| source | field_name | 性质 | `available_at`（相对 T） | as-of 安全 | 备注 |
|---|---|---|---|---|---|
| `CAISO_OASIS_DA_LMP`（价格 xlsx） | `da_lmp` | 实际（已出清） | T−1 **13:00 PT**（DA 结果发布） | 是 | 作 T 的 label；作 T 特征只能滞后（如 lag1=T−2） |
| `CAISO_OASIS_RTPD`（价格 xlsx） | `rtpd_lmp` | 实际（15-min 聚合小时） | T 当日逐小时实时；整日完整于 T 深夜 | 是 | label；滞后从 T−2 起（`canonical.py` 约定） |
| 价格 xlsx（DARTPD Return） | `darptd_return` | = DA − RTPD | 两者齐备后 | 是 | label |
| `load_2da_csv` | `load_2da_forecast` | 预报 | T−2 **18:00 PT**（BPM Exhibit 2-1） | 是* | *`ASSUMED_AVAILABLE`；若实际发布晚于该点需重审 |
| `load_ACTUAL_csv` | `load_actual` | 实际 | T 日之后 | 是（仅历史） | 作 T 特征=穿越，只作滞后 |
| `zone_weather_hourly.csv` | `t2m / ssrd / wind100` | **ERA5 再分析/实测**（历史段） | 历史段 T−2 末；目标日预报**不可用** | **否（作预报）** | 变量名 ssrd_wm2/wind100 为 ERA5 风格，延伸到未来 → 目标日值禁用（`t2m_next` 等） |
| `NCEP_GFS_025_via_OpenMeteo`（Single Runs） | `t2m / ssrd / wind100`（预报） | **as-issued 预报** | `available_at` = init + 发布延迟模型（BACKTEST 保守上界 +6h）：00Z/06Z → 06:00/12:00 UTC，严格早于 cutoff ✅；12Z → 上界 18:00 UTC 与 cutoff 临界、18Z 在其后 → **不可回测（FALSE）** | 部分（00Z/06Z ✅；12Z/18Z ❌） | 档案起点 2026-04-02，test 窗口全覆盖；默认 cycle = 06Z（`DEFAULT_CYCLE`）；详见 §2.3 |

> **关键穿越点**：`zone_weather_hourly.csv` 的目标日（`*_next`）字段是再分析/延伸段，
> **不是决策时可得预报**。要目标日天气预报，唯一合规来源是 GFS 档案（§5 第 8 行）。

---

## 4. feature_snapshot 标准

**定义**：每个决策日 D 决策后（cutoff 后）冻结的一张表；一行 = 一个
`(node, target_date T, target_hour h, feature_name)` 的特征值及其 vintage 溯源。
任何预测都可沿 `asof_record_id` 追到原始 As-of 记录 → `raw_source_id` → raw response。

### 4.1 字段

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `snapshot_id` | str | 是 | `SNAP-{decision_date}-{node}-{target_date}-H{target_hour}-{feature_name}`（唯一） |
| `decision_date` | str | 是 | 决策日 `YYYY-MM-DD`（= target_date − 1） |
| `decision_cutoff` | str | 是 | UTC naive，D 10:00 PT |
| `created_at` | str | 是 | 快照生成时刻（UTC naive，≥ cutoff） |
| `node` | str | 是 | 节点 ID |
| `target_date` | str | 是 | 交付日 T `YYYY-MM-DD` |
| `target_hour` | int | 是 | 1..24（H1=00:00–01:00） |
| `feature_name` | str | 是 | 特征名（与 `field_name` 对齐） |
| `feature_value` | float | 是 | 该特征当日的取值 |
| `source` | str | 是 | 数据源标识 |
| `source_type` | str | 否 | 源语义类别（同 §2.1） |
| `is_mock` | bool | 是 | MOCK 标记（复制自来源 As-of 记录；`True` → 快照永不可用，R7） |
| `target_time` | str | 是 | 该行指向的交付时刻（UTC naive；由 `(target_date, target_hour)` 复算） |
| `available_at` | str | 是 | 该值的 as-of 可用时点（UTC naive） |
| `retrieved_at` | str | 是 | 采集时刻（审计；复制自来源记录） |
| `raw_source_id` | str | 是 | 原始响应/行 ID（溯源链中间件） |
| `time_eligible` | bool | **计算** | 纯时间门槛（由 `available_at <= decision_cutoff` 复算） |
| `backtest_eligible` | bool | **计算** | 复制自来源记录（含 R7/R8 硬隔离） |
| `production_eligible` | bool | **计算** | 复制自来源记录（含 R7 硬隔离） |
| `decision_eligible` | bool | **计算** | 复制自来源记录的单一门槛（MOCK 恒为 FALSE） |
| `asof_record_id` | str | 是* | 溯源指针：指向来源 As-of 记录主键（*建议必填，追链的关键） |
| `market_rule_version` | str | 是 | DAME/EDAM 市场规则版本标记 |
| `version` | str | 是 | `asof_v1` |

### 4.2 生命周期

```
D 日 09:30 采集器启动（< cutoff）
   ├─ 逐源拉取 → 存 raw response → raw_source_id → retrieved_at（墙钟）
   └─ 逐字段 → AsOfRecord（available_at = max(published, retrieved)）
D 日 10:00（cutoff）采集窗口关闭
   ├─ gate_asof_records()：eligible → 当日 snapshot；post → 复盘区
   └─ created_at 冻结 → feature_snapshot 落库（追加写，不可变）
D+1 训练/推理只消费 decision_eligible=TRUE 的 snapshot 行
```

### 4.3 追溯链

```
预测/交易单
   ↑ feature_value ← feature_snapshot（decision_date, node, target_date, target_hour, feature_name）
   ↑ asof_record_id ← AsOfRecord（source, published_at, available_at, retrieved_at, forecast_run, raw_source_id）
   ↑ raw_source_id   ← 原始响应（API run id / CSV 行 / xlsx 路径，落盘可复现）
```

---

## 5. 采集模式设计（两套，代码即文档）

### 5.1 Historical Backtest Mode（回测：复原历史可知状态）

**目标**：对回测窗口内每个决策日 D，重建"D 10:00 PT 之前当时能知道什么"。

**步骤**：
1. 遍历 `decision_date ∈ 回测窗口`：`cutoff = make_decision_cutoff(D)`。
2. 按特征清单（§3 矩阵 + `canonical.py` 的 Leakage Guard 可用性）逐源拉**历史 as-of 工件**：
   - GFS 预报：`Open-Meteo Single Runs` 按 `run = D 00Z/06Z UTC` 取档案（= as-issued，非重算；
     12Z/18Z 无法可靠证明发布早于 cutoff → 不可回测，见 §2.3）；
   - 历史价格 / 实际负荷：从本地 xlsx / CSV 取，`available_at` 用排程表口径（§3）；
   - 2DA 负荷预测：取文件，`available_at` 用 `T−2 18:00 PT`（ASSUMED）。
3. 每条产出一个 `AsOfRecord`：`mode=BACKTEST`，`available_at = 历史 published_at（vintage）`，
   `retrieved_at = 今天的墙钟（仅审计）`。
4. `gate_asof_records()` 切分 eligible / post；只把 eligible 行写入当日 snapshot。
5. **安全声明**：若某源无法重建历史 `published_at` ⇒ `available_at = None` ⇒ 自动
   `decision_eligible = FALSE`，并标记 **NOT_BACKTEST_SAFE**（与 evidence schema 同语义）。

**禁止（穿越清单）**：
- ❌ 用"今天的实际值/再分析"回填成"当时的预报"；
- ❌ 用 `retrieved_at`（今天）主张历史可用；
- ❌ 把 cutoff 之后才发布的信息放进当日特征。

### 5.2 Production Mode（生产：决策日自动化采集）

**目标**：Decision Day 在 cutoff 前拉取最新 Forecast/Market，留痕并生成当日 snapshot。

**步骤**：
1. 调度器在 `D 09:30 PT`（可配，严格 < cutoff）启动。
2. 逐源（GFS run 当日实拉（默认 06Z；12Z 生产可用但须 cutoff 前拉到）、OASIS 最新价、负荷预报等）：
   - **先存 raw response**（登记 `raw_source_id`，落盘可复现）；
   - 记录 `retrieved_at`（墙钟 UTC naive）；
   - 解析 `published_at`（源 header / 排程）；
   - `available_at = max(published_at, retrieved_at)`（R5，任一缺失 ⇒ `None` ⇒ 不可用）。
3. cutoff（10:00 PT）窗口关闭：`gate_asof_records()` → 生成当日 `feature_snapshot`，`created_at` 冻结。
4. cutoff 后才到达的数据 → `post_decision` 区，只进 Post-trade Review。

**关键**：`available_at = max(published, retrieved)` 保证"源没发布 + 我们没拉到"都不可用；
两者都早于 cutoff 才可能 eligible，天然防"源发布了但今天才拉"的伪历史可用。

---

## 6. 与现有模块的关系（依赖方向）

```
canonical.py（Leakage Guard，X 特征 available_at ≤ cutoff）
    ↑ 训练/推理消费的 X 只能来自 feature_snapshot（eligible 行）或 canonical 已审特征
agent/evidence（Evidence + time_gate）
    ↑ 事件证据层：is_available_before_cutoff() / Evidence.decision_eligible 与本层 R1 同语义
code/data_acquisition/schemas.py  ←（本次交付）输入侧 vintage 层：AsOfRecord → feature_snapshot
    ↑ 消费 raw：价格 xlsx / load csv / zone_weather / Open-Meteo GFS 档案
```

- 本层管**数值型输入特征**的 vintage；evidence 层管**事件证据**的 vintage，二者互补不重叠。
- `time_gate.is_available_before_cutoff()` 与 `AsOfRecord.time_eligible` 判定规则一致；
  统一在接入层把 PT 转 UTC naive 后比较。
- **MOCK 硬隔离双实现同步（Agent B）**：`AsOfRecord`（数据层）与 `Evidence`（证据层）都
  以 `is_mock=True` 硬禁 `backtest_eligible / production_eligible`，`time_gate` 单独把
  MOCK 证据归入 `demo_mock` 桶（Decision Card 显示 `DATA NOT ELIGIBLE / DEMO MOCK`），
  改动须两处同步 + 专项测试（`test_schemas.py::TestEligibilityHardRules`、
  `test_time_gate.py::TestMockHardIsolation`）。

---

## 7. 防穿越规则清单（验收 Checklist）

- [ ] 每条 As-of 记录有 `source / published_at / available_at / retrieved_at / target_time / decision_cutoff`
- [ ] `time_eligible / backtest_eligible / production_eligible / decision_eligible` 全由程序计算，无人工/LLM 覆盖路径
- [ ] `available_at` 在 Backtest 中 = 历史 vintage；Production 中 = `max(published, retrieved)`
- [ ] **Time Gate 只判 `available_at <= decision_cutoff`**，绝不判 `initialization_time`
- [ ] GFS：00Z/06Z 有可靠 vintage（init+6h 保守上界 ≤ cutoff）→ 可回测；12Z/18Z → `backtest_eligible=FALSE`
- [ ] `available_at` 严格晚于 `model_run_time`（发布延迟为正；拒绝 init 当 available）
- [ ] 任一关键时间缺失 ⇒ `time_eligible = decision_eligible = FALSE`
- [ ] `is_mock=True` ⇒ `backtest_eligible / production_eligible / decision_eligible` 全 FALSE（R7，即便 `available_at <= cutoff`）
- [ ] `not_backtest_safe=True` ⇒ `backtest_eligible = FALSE`（R8，不禁止 production）
- [ ] `zone_weather_hourly.csv` 目标日字段不进特征（只作滞后）
- [ ] snapshot 追加写、不可变；post 记录只进复盘
- [ ] `asof_record_id` → `raw_source_id` → raw response 全链可追
- [ ] Decision Card 含 MOCK 证据时明确显示 `DATA NOT ELIGIBLE / DEMO MOCK`

---

## 8. 落地顺序建议

1. 落 `code/data_acquisition/schemas.py` + 单测（本次已交付，`python -m unittest code.data_acquisition.test_schemas`）
2. 接入 GFS 采集器（`agent/evidence/gfs_forecast.py` 已就绪，产 `AsOfRecord`）
3. 接入价格 / 负荷采集器（排程表口径，§3）
4. 生成首个 Backtest 窗口的 feature_snapshot，逐日核对 `decision_eligible` 比例
5. 快照接入 `canonical.py` 的 Leakage Guard 断言（X 全部来自 eligible 快照）
