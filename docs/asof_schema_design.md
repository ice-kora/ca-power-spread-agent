# As-of 数据结构设计（Agent D · 可追溯 / 防穿越数据层）

> 作者：Agent D（As-of 数据结构设计师）｜ 日期：2026-08-09
> 版本：asof_v1 ｜ 配套代码：`code/data_acquisition/schemas.py` + `test_schemas.py`
> 范围：**只定义数据结构、采集模式、feature_snapshot 标准；不改模型 / 回测 / 现有 evidence 层**。

---

## 0. TL;DR

任何 Forecast / Evidence / Feature 不能只存 `target_time + value`，必须保存完整 **vintage**
（来源、发布时间、可用时点），否则无法证明交易决策时该信息可见。

- **核心铁律**：`available_at <= decision_cutoff` ⇒ `decision_eligible = TRUE`；
  否则只能进 **Post-trade Review**。
- **保守规则**：`available_at / decision_cutoff / target_time / value` 任一缺失或不可解析
  ⇒ 该记录**不可用**（`decision_eligible = FALSE`）。
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
| `forecast_run` | str | 否 | 预报 run 标识：GFS `2026-07-08T12:00Z`、2DA 批号等；**实测/实际值留空** | 源响应 header / API 参数 |
| `issue_time` | str | 否 | 数据产品生成/模型初始化时刻（UTC naive）；预报必须给；实际可为空 | 源元数据（GFS run 初始时刻等） |
| `published_at` | str | **是** | 源方**公开发布**时刻（交易员最早可得，UTC naive） | 源 header / 排程表（§5 矩阵） |
| `available_at` | str | **是** | **本项目采用的可用于决策的 as-of 时点**（UTC naive）。回测=历史 `published_at`（vintage）；生产=`max(published_at, retrieved_at)` | 由 `resolve_available_at()` 按模式计算 |
| `retrieved_at` | str | **是** | 我方采集/落库时刻（UTC naive）。**仅审计**；回测中绝不用它主张可用 | 采集器墙钟 |
| `target_time` | str | 是 | 该值指向的交付时刻（UTC naive）：`target_date (h−1):00 PT→UTC` | §1.3 映射 |
| `lead_hours` | float | 计算 | `(target_time − available_at)` 小时；不可算为 `None` | 派生 |
| `node` | str | 是 | 节点 ID（`SNLNDRO_1_N001` 等）；系统级数据用系统标识（如 `CAISO_TAC`） | `节点位置.xlsx` |
| `region` | str | 是 | `ZP26 / SP15 / NP15 / SYSTEM` | 节点→区域映射 |
| `latitude` | float | 否 | 节点纬度（可选，节点级） | `节点位置.xlsx` |
| `longitude` | float | 否 | 节点经度（可选，节点级） | `节点位置.xlsx` |
| `value` | float | 是 | 数值：价格 `$/MWh`、负荷 `MW`、温度 `°C`、辐射 `W/m²`、风速 `m/s`；缺失为 `NaN` | 源响应 |
| `decision_cutoff` | str | 是 | 该记录对应的决策截止（UTC naive）：`D 10:00 PT → UTC` | `make_decision_cutoff()` |
| `decision_eligible` | bool | **计算** | 强制规则 R1/R2 的结果；**禁止人工/LLM 改写** | `property` 程序计算 |
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

### 2.3 代码接口（`code/data_acquisition/schemas.py`）

| 函数 / 成员 | 作用 |
|---|---|
| `AsOfRecord`（dataclass） | 标准记录；`decision_eligible` / `is_usable` / `lead_hours` 为计算属性 |
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
| `NCEP_GFS_025_via_OpenMeteo`（Single Runs） | `t2m / ssrd / wind100`（预报） | **as-issued 预报** | issue = D **12Z UTC**，发布 ≈ D 08:30 PT（init+3.5h） | 是 | 12:00 UTC < cutoff（17:00 UTC 夏 / 18:00 UTC 冬）；档案起点 2026-04-02，test 窗口全覆盖 |

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
| `available_at` | str | 是 | 该值的 as-of 可用时点（UTC naive） |
| `decision_eligible` | bool | 是 | 程序计算（复制自来源 As-of 记录） |
| `asof_record_id` | str | 是* | 溯源指针：指向来源 As-of 记录主键（*建议必填，追链的关键） |
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
   - GFS 预报：`Open-Meteo Single Runs` 按 `run = D 12Z UTC` 取档案（= as-issued，非重算）；
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
2. 逐源（GFS 12Z run 当日实拉、OASIS 最新价、负荷预报等）：
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
- `time_gate.is_available_before_cutoff()` 与 `AsOfRecord.decision_eligible` 判定规则一致；
  统一在接入层把 PT 转 UTC naive 后比较。

---

## 7. 防穿越规则清单（验收 Checklist）

- [ ] 每条 As-of 记录有 `source / published_at / available_at / retrieved_at / target_time / decision_cutoff`
- [ ] `decision_eligible` 全由程序计算，无人工/LLM 覆盖路径
- [ ] `available_at` 在 Backtest 中 = 历史 vintage；Production 中 = `max(published, retrieved)`
- [ ] 任一关键时间缺失 ⇒ `decision_eligible = FALSE`
- [ ] `zone_weather_hourly.csv` 目标日字段不进特征（只作滞后）
- [ ] snapshot 追加写、不可变；post 记录只进复盘
- [ ] `asof_record_id` → `raw_source_id` → raw response 全链可追

---

## 8. 落地顺序建议

1. 落 `code/data_acquisition/schemas.py` + 单测（本次已交付，`python -m unittest code.data_acquisition.test_schemas`）
2. 接入 GFS 采集器（`agent/evidence/gfs_forecast.py` 已就绪，产 `AsOfRecord`）
3. 接入价格 / 负荷采集器（排程表口径，§3）
4. 生成首个 Backtest 窗口的 feature_snapshot，逐日核对 `decision_eligible` 比例
5. 快照接入 `canonical.py` 的 Leakage Guard 断言（X 全部来自 eligible 快照）
