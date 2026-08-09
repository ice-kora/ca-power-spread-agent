# 公司数据审计报告（Agent A · 只读数据审计）

> 审计人：Agent A（公司数据审计员）｜ 日期：2026-08-09 ｜ 方式：只读，未联网，未建模
> 审计对象：仓库根目录全部原始数据文件 + `code/data/` 全部生成物 + 参考文档
> 判定原则：**不知道就标 `[UNKNOWN_SOURCE_TYPE]` / `HISTORICAL_BACKTEST_SAFE=UNKNOWN`，不猜测。**
> 本文为审计记录，不改任何文件；所有数据事实均已用脚本对原文件实证（方法见 §10）。

---

## 0. 结论先行（TL;DR）

1. **价格文件（3 节点 × 2 变体）**：DA / RTPD 为**历史真实市场出清结果**（Historical Actual，节点级，小时粒度）。`DARTPD Return = DA − RTPD` 逐小时实证**完全成立**（max 偏差 1e-13）。RTPD 对应 **CAISO 15 分钟实时市场（FMM，历史称 Real-Time Pre-Dispatch）**，**不是 RT5M（RTED 5 分钟）**。
2. **`-c` 文件 = 阻塞（Congestion）分量**：`DARTPD Cong Spread = DA_阻塞 − RTPD_阻塞`（逐小时实证 max 偏差 1e-13），与全价文件 `Return` 的关系是子分量（全 Return = energy+loss 分量 + 阻塞分量）。**当前流水线完全未用 -c 文件**，但可作历史 congestion lag/rolling/regime 特征的数据基础。
3. **Actual Load**：系统级历史实测，**目标日实际负荷未进任何 X 特征**（P0 通过，canonical 中仅有 `load_actual_lag1` / `load_actual_day_mean_lag1`，均为 target_date−2）。
4. **2DA Load Forecast**：文件**没有 forecast_issue_time / published_at / available_at 列** → 按审计规则 `HISTORICAL_BACKTEST_SAFE=UNKNOWN`（文件级）；外部官方时序（docs/market_timeline.md，BPM：Two-Day-Ahead 需求预报 TD−2 18:00 发布，早于决策 cutoff T−1 10:00）支持项目标注 `ASSUMED_AVAILABLE`。**不能因文件名带 "2DA" 就默认回测安全。**
5. **Weather**：**Reanalysis（ERA5 风格）2025-04-02 → 2026-08-09 + 合成模板段 2026-08-10 → 2026-08-19**（ssrd 呈 200 线性步进到 1200 封顶、t2m 日廓线逐日 corr>0.99 的模板重复）。无 run / issue / available_at → **不能当严格 D+1 forecast**，只允许历史天气 lag 特征（`t2m_lag1/ssrd_lag1/wind100_lag1`）。目标日天气作特征 = 穿越（原 `*_next` 特征已禁用）。
6. **P0 全查通过（当前 canonical 数据层）**：目标日实际 DA/RTPD/Return/负荷/天气均不进 X 特征；旧 `features.parquet` 确含目标日实际值（`spread_next/da_price_next/rtpd_price_next/t2m_next/...`）——已废弃，仅作对比。

---

## 1. 审计范围与方法

- **逐文件**：对每个文件读表头、逐行解析、统计时间范围/粒度/覆盖/缺失/重复/值域，全部用 Python 对原文件实证。
- **逐字段 source_type**：按五种类型 + `[UNKNOWN_SOURCE_TYPE]` 判定：`Historical Actual`（事后实测）/ `Historical Forecast`（当时发布的预报）/ `Reanalysis`（再分析，事后反演）/ `Static Data`（静态）/ `Derived Feature`（派生）/ `Label`（目标标签）。
- **参考文档**：`CLAUDE.md`、`README.md`、`数据审计与业务口径.md`、`docs/market_timeline.md`、`docs/business_contract.md`、`docs/leakage_report.md`、`docs/feature_availability_matrix.md`、`docs/evidence_source_v021.md`、`code/read_data.py`、`code/canonical.py`、`code/features.py`、`code/model_v2.py`、`code/app.py`、`业务讲解语音转文字.md`。

---

## 2. 逐文件登记表

### 2.1 原始数据（仓库根 / 价格数据/）

| 文件 | source_type | time_range | time_granularity | node_coverage | main_columns | business_meaning |
|---|---|---|---|---|---|---|
| `价格数据/SNLNDRO_1_N001.xlsx` | Historical Actual（DA/RTPD）+ Derived（Return） | 2024-01-01 → 2026-08-05（948 天） | 小时 H1–H24 + 日（Avg/Total）；3 月 DST 日 23h | SNLNDRO_1_N001 → ZP26 | Date, B列market(DA/RTPD/DARTPD Return), Hours, Avg, Total, H1..H24 | 节点日前(DA)/实时(RTPD) LMP（$/MWh）+ 价差 |
| `价格数据/SNLNDRO_1_N001-c.xlsx` | Historical Actual（阻塞分量）+ Derived（Cong Spread） | 2024-01-01 → 2026-08-05（948 天） | 小时 H1–H24 + 日 | SNLNDRO_1_N001 → ZP26 | 同上，B列为 DARTPD **Cong Spread** | DA/RTPD 的**阻塞分量** + 阻塞价差（见 §4.2） |
| `价格数据/CONTROLX_1_N001.xlsx` | 同上 | 2024-01-01 → 2026-08-05（948 天） | 小时 + 日 | CONTROLX_1_N001 → ZP26 | 同上 | 同上 |
| `价格数据/CONTROLX_1_N001-c.xlsx` | 同上（阻塞分量） | 2024-01-01 → 2026-08-05（948 天） | 小时 + 日 | CONTROLX_1_N001 → ZP26 | 同上，DARTPD Cong Spread | 阻塞分量 |
| `价格数据/ELCAJNGT_7_N001.xlsx` | 同上 | 2026-03-03 → 2026-08-05（156 天；首日仅 H24） | 小时 + 日 | ELCAJNGT_7_N001 → SP15 | 同上 | 同上 |
| `价格数据/ELCAJNGT_7_N001-c.xlsx` | 同上（阻塞分量） | 2026-03-03 → 2026-08-05（156 天） | 小时 + 日 | ELCAJNGT_7_N001 → SP15 | 同上，DARTPD Cong Spread | 阻塞分量 |
| `load_CA_ISO_TAC_2DA.csv` | **Historical Forecast**（日前 2 日超前负荷预报） | 2025-04-01 → 2026-08-07（**494 个唯一日**，去重后无缺口） | 日 + 小时 H1–H24 | 系统级（TAC，非分节点） | Date, Avg, H1..H24 | 官方 2DA 系统负荷预测（MW）。**无 issue/published/available_at 列** |
| `load_CA_ISO_TAC_ACTUAL.csv` | **Historical Actual**（系统实际负荷） | 2025-04-01 → 2026-08-04（**缺 2026-03-08**） | 日 + 小时 H1–H24 | 系统级 | Date, Avg, H1..H24 | 系统实际负荷（MW），事后可得 |
| `zone_weather_hourly.csv` | **Reanalysis（2025-04-02→08-09）+ 合成段（08-10→08-19，见 §4.5）** | 2025-04-02 → 2026-08-19（503 天 × 24h） | 小时 | NP15 / SP15 / ZP26（3 分区） | zone, valid_pt, t2m_c, ssrd_wm2, wind100, Date | 分区逐小时天气（温度/太阳辐射/100m 风）。**无 run/issue/available_at 列** |
| `节点位置.xlsx` | **Static Data** | — | — | SNLNDRO/CONTROLX→ZP26；ELCA→SP15 | 节点ID, 节点名称, 类型, 区域(Zone), 纬度, 经度, 制图X/Y, 所属省份 | 节点→zone 映射 + 定位信息，静态 |

> ⚠️ 目录内还存在 Excel 临时锁文件 `~$CONTROLX_1_N001.xlsx`（BadZipFile，165 B）——**不是数据文件**，加载时须排除（项目 `.gitignore` 已处理，但审计提醒任何 `glob("*.xlsx")` 都应过滤 `~$` 前缀）。

### 2.2 生成物（`code/data/`）

| 文件 | source_type | 说明 |
|---|---|---|
| `master.csv` | Derived Feature（合并长表） | read_data.py 产物：`node,date,hour,da_price,rtpd_price,spread,zone,load_2da,load_actual,t2m,ssrd,wind100`，49,644 行（2024-01-01..2026-08-05）。**2026-07-21~07-26 存在整行重复 864 行**（根因见 §8.1）。2024-01..2025-03 段 load/weather/2DA 全 NaN |
| `canonical.parquet` | **Derived Feature + Label（生产数据层）** | 49,210 行，行语义=(node,target_date,hour)；X 38 列 + label 4 列 + 标识列；无泄漏（P0 见 §9）。`feature_version=canonical_v1` |
| `features.parquet` | **DEPRECATED（含目标日实际值 = 泄漏）** | 51,300 行；含 `spread_next/da_price_next/rtpd_price_next/load_2da_next/t2m_next/ssrd_next/wind100_next`（目标日实际 DA/RTPD/Return/天气进特征，实证泄漏）。仅作对比，不再使用 |
| `feature_schema.json` | Static Data（schema） | canonical 元数据：X/label/禁用特征/可用性矩阵/滞后约定。`decision_cutoff` 仍写 "decision_date 13:00"（官方应为 10:00，见 §8.8） |
| `predictions_v2.csv` / `predictions_v2_val.csv` | Derived（模型输出） | 生产模型 test（4678 行）/ val（7244 行）预测：expected_return, prob_positive, confidence, uncertainty, actual_return, actual_direction |
| `model_v2_notes.json` / `model_notes.json` | Derived（模型元数据） | 模型定义/特征清单 |
| `backtest_v2_summary.json` / `backtest_v2_ab.json` | Derived（回测结果） | Signal backtest 汇总 / Agent Evidence A/B 对账 |
| `stage3/`、`v021_*` | Derived（分析产物） | 极端事件/分层/校准分析中间产物 |

---

## 3. 字段级 source_type 判定（逐字段）

### 3.1 价格文件（`价格数据/*.xlsx`，非 `-c`）

| 字段 | source_type | 依据 |
|---|---|---|
| Date | Static Data | 交付日（日历），文件首行填充 |
| B列 market（无表头） | Static Data | 市场类型标识：DA / RTPD / DARTPD Return |
| Hours | Static Data | 该市场当天小时数（24 或 23；ELCA 首日 =1） |
| Avg / Total | Derived Feature | 由 H1..H24 对"有值小时"聚合 |
| H1..H24（DA 行） | **Historical Actual** | 日前市场出清 LMP，T−1 13:00 PT 发布 |
| H1..H24（RTPD 行） | **Historical Actual** | 实时 15 分钟市场价（FMM），T 日实时产生，聚合成小时 |
| H1..H24（DARTPD Return 行） | **Derived Feature / Label** | `= DA − RTPD`（逐小时实证 max 偏差 1e-13） |

### 3.2 价格文件（`-c`，阻塞分量）

| 字段 | source_type | 依据 |
|---|---|---|
| H1..H24（DA 行） | Historical Actual（**阻塞分量**） | 数值远小于全 LMP（如 SNLNDRO 2024-01-01 DA=46.65 vs DA_c=1.46）；业务讲解确认为"阻塞价格" |
| H1..H24（RTPD 行） | Historical Actual（**阻塞分量**） | 同上 |
| H1..H24（DARTPD Cong Spread 行） | **Derived Feature** | `= DA_阻塞 − RTPD_阻塞`（逐小时实证 max 偏差 1e-13） |

### 3.3 负荷文件

| 文件 | 字段 | source_type |
|---|---|---|
| `load_CA_ISO_TAC_2DA.csv` | Date | Static Data（预报目标日） |
| 〃 | Avg, H1..H24 | **Historical Forecast**（官方 2DA 负荷预报，MW） |
| `load_CA_ISO_TAC_ACTUAL.csv` | Date | Static Data（实测日） |
| 〃 | Avg, H1..H24 | **Historical Actual**（系统实际负荷，MW） |

### 3.4 天气文件

| 字段 | 时间区间 | source_type |
|---|---|---|
| zone / valid_pt / Date | 全部 | Static Data（分区 / 时间戳 / 日期） |
| t2m_c / ssrd_wm2 / wind100 | 2025-04-02 → 2026-08-09 | **Reanalysis**（ERA5 风格，见 §4.5） |
| t2m_c / ssrd_wm2 / wind100 | 2026-08-10 → 2026-08-19 | **`[UNKNOWN_SOURCE_TYPE]`**（合成模板，非观测/预报/再分析，见 §4.5） |
| t2m_c / ssrd_wm2 / wind100 | 2026-08-06 → 08-09 | 边界段：ssrd 物理、t2m 模板化，来源无法确认 → 保守不当作预报（见 §5） |

### 3.5 节点元数据

| 字段 | source_type |
|---|---|
| 节点ID / 节点名称 / 类型 / 区域(Zone) / 纬度 / 经度 / 制图X/Y / 所属省份 | 全部 **Static Data** |

### 3.6 生成物字段

| 文件 | 字段 | source_type |
|---|---|---|
| `master.csv` | node/date/hour | Static / 标识 |
| 〃 | da_price / rtpd_price | Historical Actual（直通价格文件） |
| 〃 | spread | Derived Feature（= da − rtpd） |
| 〃 | zone | Static Data |
| 〃 | load_2da | Historical Forecast（直通 2DA 文件） |
| 〃 | load_actual | Historical Actual（直通实测文件） |
| 〃 | t2m / ssrd / wind100 | Reanalysis（直通天气文件；注意 08-10 起为合成段） |
| `canonical.parquet` | X 特征（38 列） | Derived Feature（由历史滞后/滚动/静态/预报构造；`load_2da_forecast` 来源=Historical Forecast，`ASSUMED_AVAILABLE`） |
| 〃 | actual_da / actual_rtpd | **Label**（目标日实际值，仅训练/回测） |
| 〃 | actual_return / direction | **Label**（派生目标） |

---

## 4. 重点审计

### 4.1 DA / RTPD / Return

- **DA**：节点日前市场出清 LMP（$/MWh），**历史真实市场结果**。官方时点：DAM 结果 T−1 13:00 PT 发布（docs/market_timeline.md 已核验 BPM 原文）。**目标日 DA 禁止进 Production Feature**（P0）。
- **RTPD**：**历史真实实时市场价格**。对应 CAISO **15 分钟实时市场（FMM；历史称 Real-Time Pre-Dispatch / RTPD）**，逐 15 分钟出清、数据聚合成小时。**不是 RT5M**（RTED 5 分钟市场是另一口径，docs/market_timeline.md §7）。⚠️ 虚拟报价结算口径：CAISO 对 Virtual Bid 结算通常按 5 分钟 RTM LMP 的加权平均；本项目契约冻结 `Return = DA − RTPD`（用 15 分钟口径）——若考核方按 5 分钟结算，label 与真实 PnL 会有系统性差异，属**业务待确认项**（§5 待确认 #5 的延续）。
- **Return = DA − RTPD**：逐小时实证成立（SNLNDRO/CONTROLX/ELCA 的 DARTPD Return 行与 `DA − RTPD` 逐小时差 max ≈ 1e-13）。文件 Avg 列存在个别日四舍五入差异（如 SNLNDRO 个别日 Avg 差 0.34），但**小时级完全一致**，label 以小时级重算为准（canonical 中 `actual_return == actual_da − actual_rtpd` max 偏差 0.0）。
- **时间粒度**：小时 H1..H24；3 月春令时切换日为 **23 小时（H3 缺失）**，文件以 `Hours=23` 表达（2024-03-09/10、2025-03-08/09、2026-03-08 等）；ELCA 首日 2026-03-03 仅 H24（`Hours=1`）。
- **节点覆盖**：SNLNDRO/CONTROLX 各 948 天（2024-01-01..2026-08-05，每天 DA/RTPD/Return 各 1 行）；ELCA 156 天（2026-03-03 起，冷启动节点）。
- **负价**：DA 约 28%、RTPD 约 17% 为负（负电价常见于午间光伏过剩时段；master 实证 28.1% / 17.2%）。
- **用途判定**：≤ T−2 的历史 DA/RTPD/Return → 可作 lag/rolling/日级统计特征（canonical 已实现，滞后从 target_date−2 起，宁保守不泄漏）；**目标日 DA/RTPD/Return 仅作 label / 回测结算**。

### 4.2 Congestion（阻塞）

- **`-c` 文件 = 阻塞分量文件**（当前仓库文档记载为"组合价格文件，含义不明，不采用"，但审计实证其结构）：
  - 同一日期范围内，`-c` 文件 DA/RTPD 数值远小于全价（如 CONTROLX 2024-01-01 DA=31.21 vs DA_c=−1.00），为**全 LMP 的阻塞（congestion）分量**；
  - `DARTPD Cong Spread = DA_阻塞 − RTPD_阻塞` 逐小时实证**完全成立**（max 偏差 1e-13）；
  - 业务讲解（`业务讲解语音转文字.md`）明确："这个杠C…里边会有一些组合数据"、"阻塞价格…能源价格受到线路影响…正/负阻塞"，并建议"杠C你先不用管"。
- **与 Return 的关系**：全 Return = `(DA − RTPD)` 含 energy+loss+congestion 全分量；CongSpread 仅为其中阻塞部分（SNLNDRO corr(Return, CongSpread)≈0.56、CONTROLX≈0.99，差值为 energy+loss 分量）。
- **当前流水线完全未用 -c 文件**（read_data.py 显式 `base.endswith("-c.xlsx")` 跳过）→ **无 congestion 特征**。
- **可用性结论**：
  - ✅ 可做**历史 congestion lag / rolling / regime**（以 target_date−2 起的交付日为锚，与现有 spread 特征同口径）——数据范围与全价文件一致（SNLNDRO/CONTROLX 2024-01-01..2026-08-05，ELCA 2026-03-03 起）；
  - ❌ **目标日 congestion 只能作 label / review**（与 Return 同属事后值）。
  - `[UNKNOWN_SOURCE_TYPE]`：`-c` 文件的官方组件定义（是否精确等于 CAISO MLC 阻塞分量）未经官方 BPM/源文件独立核验；数学关系已实证，业务语义有转录佐证，但**官方组件口径待确认**。

### 4.3 Actual Load

- **Historical Actual**，系统级（TAC）逐小时实测负荷（MW），事后可得（"必须等到这一天过去它才会有"——业务讲解原话）。
- **P0 检查（通过）**：canonical X 特征中实际负荷只有 `load_actual_lag1`（target_date−2）与 `load_actual_day_mean_lag1`（target_date−2 当天 24h 统计），**没有任何 target_date 当日实际负荷**。✓
- 数据可靠性提示：业务讲解称实测负荷"不一定准"、"实测有问题"（销售原话："这个实测如果不太准…他们那个实测有问题"）——若以实测作回归/校准目标，存在口径风险，审计只登记、不下结论。
- 数据质量：**缺 2026-03-08**（DST 春令时切换日整日缺失；价格/天气该日以 23h 表达，actual load 却整日缺失，处理口径不一致）。

### 4.4 2DA Load Forecast

- **文件内容**：系统级日前负荷预测（MW），日 + 小时（H1..H24）。500 行，去重后 **494 个唯一日，2025-04-01 → 2026-08-07，无缺口**。
- **是否有 forecast_issue_time / published_at / available_at？** **没有。** 文件只有 `Date`（预报目标日）和负荷值，无任何发布/生成时间戳 → 按审计规则：
  **`HISTORICAL_BACKTEST_SAFE=UNKNOWN`（文件级）**，不能因文件名 "2DA" 就默认安全。
- **外部官方证据（docs/market_timeline.md，2026-08-09 BPM 核验）**：CAISO **Two-Day-Ahead 需求预报于 TD−2 18:00 发布**（"Prepare/Publish Demand Forecasts & AS Requirements" TD-2 1800；Two Day-Ahead RUC 过程 14:00/18:00 PST 完成），**早于决策 cutoff（T−1 10:00 PT）** → 支撑项目当前标注 `ASSUMED_AVAILABLE`（低度不确定）。
- **项目现状**：canonical `load_2da_forecast = target_date 当天的 2DA 值`（无 shift），`load_peak_flag` 由它派生；feature_availability_matrix 状态 `ASSUMED_AVAILABLE`。审计认可该标注的保守性，但**严格回测若要作为-of 证据，须逐日核实 2DA 行发布时间**（文件本身不可证）。
- **数据质量**：文件为**未排序聚合**（如 2025-12 段后跳到 2025-04 段）、**双日期格式混排**（`YYYY/M/D` 与 `YYYY-MM-DD`）、**12 行重复**（2026-07-21..07-26 以两种格式各出现一次，值近似相等）；`CLAUDE.md` 记载范围（"2025-10-01 → 2026-07-09"）与实际不符（实为 2025-04-01 → 2026-08-07）。

### 4.5 Weather

- **性质判定：Reanalysis（ERA5 风格）+ 合成段。**
  - **Reanalysis 段（2025-04-02 → 2026-08-09）**依据：变量名 `t2m_c / ssrd_wm2 / wind100` 为 ERA5 风格；高精度浮点（如 `1.4996661125171613`）；**覆盖 503 天 × 24h，仅缺 2 个整日（2026-06-29 与 2026-07-14，全分区一致）**——缺口极少，符合再分析/网格数据而非实测观测的特征；ssrd 平滑物理日循环（如 2026-08-05 SP15：0,0,…,47,222,432,630,794,908,965,957,885,758,585,384,180,26,0…），max ≈ 1053 W/m²。
  - **合成段（2026-08-10 → 2026-08-19）**依据：ssrd 呈**线性 200 步进**（0,0,0,0,0,0,**200,400,600,800,1000,1200**,1200…1200,1088,976,863,751,639,527），25% 数值 ≥1200（历史段 0%）；t2m 日廓线逐日 **corr>0.99 的模板重复**；wind100 呈规则 V 形。**非观测、非预报、非再分析** → `[UNKNOWN_SOURCE_TYPE]`。
  - **边界段（2026-08-06 → 08-09）**：ssrd 物理、t2m 模板化；因在价格数据截止（08-05）之后，来源无法确认，保守不作为预报使用。
- **无 forecast_issue_time / run / available_at** → **不能当严格 D+1 forecast**；`HISTORICAL_BACKTEST_SAFE=UNKNOWN`。
- **时区**：`valid_pt` 为 **naive 小时戳**（无时区记录；CA-ISO 为 America/Los_Angeles），存在小时对齐不确定性（历史 lag 不受泄漏影响，但对齐口径未确认）。
- **项目现状（正确）**：天气只作历史滞后特征 `t2m_lag1/ssrd_lag1/wind100_lag1`（target_date−2）；目标日天气 `*_next` **默认禁用**（canonical DISABLED_FEATURES）。上一轮审计确认旧 features.parquet 把目标日实际天气当预报用了（穿越），已修复。
- **真实 as-of 天气源（生产决策层，非本静态文件）**：`agent/evidence/gfs_forecast.py` 接入 **Open-Meteo NCEP GFS 历史 run 档案**（Single Runs API），带 `forecast_issue_time`（decision_date 12Z = 12:00 UTC < cutoff 17:00/18:00 UTC），`decision_eligible` 由 time_gate 程序计算、失败不伪造——这是仓库内**唯一真实 as-of 安全的天气预报来源**（docs/evidence_source_v021.md）。
- **边界风险提示**：若对 target_date > 2026-08-07 的未来日做推理，`t2m_lag1` 等的 target_date−2 会落入天气合成段（08-10 起），此时天气 lag 特征来自合成模板（不可信）。

---

## 5. `[UNKNOWN_SOURCE_TYPE]` 清单

| # | 字段 / 对象 | 原因 | 影响 |
|---|---|---|---|
| 1 | `zone_weather_hourly.csv` 2026-08-10 → 08-19 的 `t2m_c / ssrd_wm2 / wind100` | 合成模板（线性 200 步进 ssrd、t2m 日廓线 corr>0.99），非观测/预报/再分析 | 该段任何值不可作为真实天气使用 |
| 2 | `zone_weather_hourly.csv` 2026-08-06 → 08-09 的天气三字段 | 位于价格数据截止后，ssrd 物理但 t2m 模板化，来源无法确认 | 保守不作为预报使用 |
| 3 | `load_CA_ISO_TAC_2DA.csv` 每行的**发布/生成时点** | 文件无 issue/published/available_at 列；外部官方时序支持但未逐日核验 | 不能从文件本身证明决策时可得 |
| 4 | `价格数据/*-c.xlsx` 阻塞分量的**官方组件定义** | 数学关系已实证、业务转录佐证，但官方 BPM/源文件组件口径未独立核验 | 作 congestion 特征前需确认口径 |
| 5 | 天气 `valid_pt` 的**时区口径** | naive 小时戳，无时区记录 | 小时对齐存在 ±1h 级不确定 |

---

## 6. `HISTORICAL_BACKTEST_SAFE=UNKNOWN` 清单

| # | 对象 | 判定 | 说明 |
|---|---|---|---|
| 1 | `load_CA_ISO_TAC_2DA.csv` | **HISTORICAL_BACKTEST_SAFE=UNKNOWN** | 无 forecast_issue_time/published_at/available_at → 文件级不能证明 as-of；仅凭官方 BPM（TD−2 18:00 发布）得 `ASSUMED_AVAILABLE`。严格回测若要求硬 as-of，需逐日补证 |
| 2 | `zone_weather_hourly.csv` | **HISTORICAL_BACKTEST_SAFE=UNKNOWN** | 无 run/issue 时间戳 + 含合成段；只能作历史 lag，**不能当 D+1 forecast** |
| 3 | 天气 `valid_pt` 时区 | UNKNOWN（小时对齐） | naive 戳，未换算 America/Los_Angeles |

> 注：`load_CA_ISO_TAC_ACTUAL.csv`（历史实测）与价格文件（历史市场结果）**不涉及** as-of 可用性问题——它们本就只能在事后用于 label / 历史特征；作为历史滞后（target_date−2）时不存在穿越。

---

## 7. 不能用于严格回测的字段（黑名单）

| 字段 | 原因 | 处置 |
|---|---|---|
| 目标日 `DA`（target_date 当日 DA 清价） | T−1 13:00 才发布，决策时不可见 | 仅 label / 回测结算 |
| 目标日 `RTPD` | T 日实时产生 | 仅 label |
| 目标日 `Return = DA − RTPD` | 两者齐备后才有 | 仅 label |
| 目标日 actual load | 事后实测 | 仅 label；X 只允许 `load_actual_lag*` / 历史统计 |
| 目标日实际/再分析天气（`t2m_next/ssrd_next/wind100_next`） | 决策时不可得（原穿越点） | 已禁用；只允许历史 lag |
| 天气合成段（08-10..08-19）任何字段 | 合成模板，非真实 | 任何用途都不可用 |
| 2DA 作为特征 | 文件级 as-of 未证 | 仅 `ASSUMED_AVAILABLE`，严格回测须补证 |
| `features.parquet` 全部 | 已确认含目标日实际值（泄漏） | 废弃，仅作对比 |

---

## 8. 数据质量问题清单（审计实证）

1. **master.csv 重复行**：2026-07-21 ~ 07-26 共 864 行全列重复（3 节点 × 6 天 × 24h × 2）。根因：2DA 文件这 6 天同时存在 `YYYY/M/D` 与 `YYYY-MM-DD` 两行，read_data.py 熔合后自然翻倍。canonical.py / app.py 已 `drop_duplicates` 兜底。
2. **2DA 文件未排序 + 双格式 + 12 行重复**：文件是按块拼装的乱序聚合（如 2025-12 段后跳到 2025-04 段；尾部 07-15..07-19 后接 07-2、07-20..07-26、07-3..07-9），且 07-21..07-26 双格式重复。
3. **CLAUDE.md / 数据审计与业务口径.md 中 2DA 时间范围记载与实际不符**：文档记 "2025-10-01 → 2026-07-09"，实际 **2025-04-01 → 2026-08-07**（494 个唯一日）。
4. **日期格式不一致**：2DA 为 `YYYY/M/D`（无补零）+ 尾部 ISO；ACTUAL 为 `YYYY-MM-DD`；天气 `valid_pt` 为 `YYYY-MM-DD HH:MM:SS`。
5. **actual load 缺 2026-03-08**（DST 日整日缺失，与价格/天气的 23h 表达口径不一致）。
6. **价格文件 DST 日 23h**（H3 缺失）：2024-03-09/10、2025-03-08/09、2026-03-08 等；ELCA 首日 2026-03-03 仅 H24。
7. **天气缺失 2 个整日**：2026-06-29 与 2026-07-14 在全部分区缺失（503 天 / 505 日历日）→ 该两日 weather lag 特征为 NaN。另：DST 春令时切换日（2026-03-08）天气有 24 个 naive 小时戳，而价格文件当日为 23 个市场小时（H3 缺失），两者对齐存在口径差异。
8. **天气 valid_pt naive 时区**：未记录时区，与 America/Los_Angeles 对齐存疑。
9. **canonical schema `decision_cutoff` 仍写 "13:00"**：官方 BPM 核验为 **10:00 PT（DAM Market Close）**，13:00 是 DA 结果发布（label 可见）时点。docs/market_timeline.md 已列出修复清单（canonical.py → 重新生成 schema/矩阵 → 文档同步）；现有 38 个 X 特征均早于 10:00 可得，**改口径不丢任何特征**。
10. **ELCA 冷启动**：仅 156 天（2026-03-03 起），首日 1 小时；训练/验证窗口短（单独切分）。
11. **master.csv 早期覆盖空洞**：价格自 2024-01-01 起，但负荷/天气/2DA 自 2025-04 起 → 2024-01..2025-03 段 load/weather/2DA 全 NaN（canonical 中 split=NaN 的 warm-up 行 21,977 个，不进建模集）。

---

## 9. P0 检查结果（生产数据层 canonical.parquet）

| 检查项 | 结果 | 实证 |
|---|---|---|
| 目标日实际 DA/RTPD 是否进 X 特征 | **否** ✅ | X(38) ∩ {actual_da, actual_rtpd, actual_return, direction} = ∅ |
| 目标日 actual load 是否进 X | **否** ✅ | X 仅含 `load_actual_lag1` / `load_actual_day_mean_lag1`（均 target_date−2） |
| 目标日实际/再分析天气是否进 X | **否** ✅ | `t2m/ssrd/wind100` 裸列不在 X；`*_next` 已禁用 |
| `load_2da_forecast` 是否为目标日预报而非实际 | **是** ✅ | canonical `load_2da_forecast` == 2DA 文件值（如 2026-06-01 CONTROLX：20770.55），≠ 实际负荷（28048.0） |
| 历史 lag 是否指向 target_date−2 | **是** ✅ | `t2m_lag1`(06-01) == master `t2m`(05-30) 原值；`spread_lag1` == master target_date−2 spread（canonical verify 全 PASS） |
| label 是否 = 真实 DA/RTPD 重算 | **是** ✅ | `actual_return == actual_da − actual_rtpd` max 偏差 0.0；`direction` 与 `sign(Return)` 一致（+1: 24,636 / −1: 24,552 / 0: 22） |
| 旧 features.parquet 是否含目标日实际值 | **是**（已废弃） | `spread_next`(06-01 行) == master 06-02 实际 spread（−26.0169）✅ 泄漏实证；`da_price_next/rtpd_price_next/t2m_next/...` 同理 |

> 结论：**当前 canonical 数据层在严格 as-of 语义下无未来信息泄漏**；旧 features.parquet 的泄漏特征已从生产路径移除（仅作对比保留）。

---

## 10. 审计方法与复现要点

- 全部判定基于对原文件的脚本实证（openpyxl / pandas 读取），关键验证：
  1. 价格：`DARTPD Return == DA − RTPD`（逐小时 max 偏差 1e-13）；`DARTPD Cong Spread == DA_c − RTPD_c`（逐小时 max 偏差 1e-13）；-c 文件数值量级 = 阻塞分量。
  2. 天气：分段统计 ssrd/t2m/wind100（合成段 vs 再分析段差异显著：≥1200 占比 25% vs 0%、日廓线 corr 0.99+ vs 物理平滑曲线）。
  3. 2DA：日期解析（format="mixed" 全 500 行可解析）、唯一日 494、重复行定位（07-21..07-26 双格式）。
  4. P0：canonical X 与 label 列集不相交 + 抽样对拍 master 原值。
- 审计中发现 `~$CONTROLX_1_N001.xlsx`（Excel 锁文件，165 B，非 zip）——任何 `glob("*.xlsx")` 加载路径应过滤 `~$` 前缀。

---

**审计员声明**：本报告为只读登记与判定，未改动任何数据/代码；所有 `[UNKNOWN_SOURCE_TYPE]` / `HISTORICAL_BACKTEST_SAFE=UNKNOWN` 均如实标注，未作无依据推断。对 2DA 发布时点、天气合成段边界、-c 官方组件定义、RTPD 结算口径四处的最终裁决建议交由 Lead 在业务口径层面确认。
