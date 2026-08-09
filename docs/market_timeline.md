# CAISO Day-Ahead / Real-Time 市场时间线核验报告

> 核验员：Agent A（市场时间线核验员）｜ 日期：2026-08-09 ｜ 状态：**结论可落地，cutoff 需从 13:00 改为 10:00**
>
> 核验方式：WebFetch 官方页面 + 官方 BPM（PDF 原文全文检索）。未凭记忆。
>
> 主要官方来源：
> - **BPM for Market Instruments V93**（2026-05-01 生效）：`https://bpmcm.caiso.com/Pages/BPMDetails.aspx?BPM=Market%20Instruments`（Redline PDF: `https://bpmcm.caiso.com/BPM%20Document%20Library/Market%20Instruments/BPM_for_Market%20Instruments_V93_Redline.pdf`）
> - **BPM for Market Operations V105**（2026-07-29 生效）：`https://bpmcm.caiso.com/Pages/BPMDetails.aspx?BPM=Market%20Operations`
> - **CAISO 官网 Market Operations → Products & Services**：`https://www.caiso.com/market-operations/products-services`

---

## 0. 结论先行（TL;DR）

1. **决策 cutoff 官方值为 10:00 PT，不是 13:00。** CAISO Day-Ahead Market（**同时涵盖 physical Bids 与 Virtual Bids**）于 **Trading Day 前一日 1000 小时（10:00）收盘**。当前仓库把 `decision_cutoff = decision_date 13:00` 冻结为"bid cutoff"是**错误口径**——13:00 是**日前市场出清结果发布**时间，位于 bid cutoff **之后**。
2. **核心问题答案：提交 Virtual Bid 时，目标交易日(D+1) 的 DA clearing price 尚不可知。** 官方时序：bid 截止（D 日 10:00 PT）→ IFM 出清（10:00–13:00）→ DA 结果发布（~13:00 PT）。交易员在 DA 价格确定**之前**锁仓，这正是虚拟套利（Convergence Bidding）的机制。
3. **时区：Pacific Time (PT)**，实行夏令时；本项目数据区间（4–8 月）对应 **PDT（UTC−7）**，冬季为 PST（UTC−8）。
4. **需将 `decision_cutoff` 从 `decision_date 13:00` 改为 `decision_date 10:00`（官方值）**，并把 13:00 重新标注为"目标日 DA 结果发布时点（= label 可见时点）"。改 cutoff 后**不会丢失任何现有特征**（全部 X 特征早于 10:00 可得，见 §5）。修改清单见 §6。

---

## 1. 官方时间线（逐条，含来源 / 时区 / 状态）

> 时间均为 **Pacific Time (PT)**，实行夏令时（4–10 月为 PDT，UTC−7）。下表 "TD" = Trading Day（交付日），"TD−k" = 交付日前 k 天；本项目的 `target_date` = TD，`decision_date` = TD−1。

| # | 阶段 | 时间（PT） | 官方原文 / 说明 | 来源 | 状态 |
|---|---|---|---|---|---|
| 1 | **DAM bid 窗口开启** | TD−7 | "Day-Ahead Market Bids may be submitted up to seven days prior to the Trading Day" | BPM Instruments §8.1.9.1（p.119）；§3.1（p.40） | `[已确认]` |
| 2 | **DAM Market Close / bid cutoff** | **TD−1 10:00** | "The Day-Ahead Market (DAM) for both virtual and physical Bids closes at **1000 hours** on the day before the Trading Day"；"must be submitted prior to Market Close … at **1000 hours** of the day prior to the Trading Day"；Exhibit 8-1 stage 9 "10:00 am — HASP T-75 — The DAM and RTM are closed for bid submission" | BPM Instruments §2.1（p.29）、§8.1.9.1（p.119）；BPM Market Ops §2.3.1（p.102） | `[已确认]` |
| 3 | **DAM 出清（MPM → IFM → RUC）** | TD−1 10:00 → ~13:00 | 10:00 收盘后依次执行 MPM → IFM → RUC；"When the DAM clears, at approximately **1300 hours**"；Exhibit 2-1：TD-1 1000 "Close Day-Ahead Market / Determine RMR & MPM / Run IFM & RUC" | BPM Instruments §9.1.10.1（p.146）；BPM Market Ops Exhibit 2-1（p.101–102）、§6.2 | `[已确认]` |
| 4 | **DA 结果发布（目标日 DA LMP 可见）** | **TD−1 13:00** | "DAM results are published … (by **1300 hours** of the day before the Trading Day)"；官网 "Results are published at **1:00 p.m.**"；Exhibit 2-1：TD-1 1300 "Publish DA Schedules" | BPM Instruments §8.1.9.1.2（p.120）；caiso.com products-services | `[已确认]` |
| 5 | **RTM bid 开闸** | TD−1 13:00 | "The RTM for a given Trading Hour opens after the DAM results are published … (by 1300 hours)"；"SCs may submit Real-Time Market Bids beginning when the Day-Ahead Schedules are published at 1300 hours"；Exhibit 2-1：TD-1 1300 "Open Real-Time Market for 0100 to 2400 on Trading Day" | BPM Instruments §8.1.9.1.2（p.120）、§3.1（p.40）；BPM Market Ops Exhibit 2-1 | `[已确认]` |
| 6 | **RTM bid 截止** | 每个 Trading Hour 开始前 **75 分钟** | "Bidding for the Real-Time Market (RTM) closes 75 minutes before the beginning of each Trading Hour"；"closes 75 minutes before the start of that Trading Hour" | BPM Market Ops §2.3.2（p.104）；BPM Instruments §8.1.9.1.2（p.120） | `[已确认]` |
| 7 | **HASP（小时前调度）** | TD，TH−75′ | 逐小时 MPM/RRD 后运行 HASP；"All HASP Schedules for the Trading Hour are published approximately **45 minutes** before the start of each Trading Hour" | BPM Market Ops Exhibit 2-1、§2.3.2.2（p.104） | `[已确认]` |
| 8 | **15-min RTUC / FMM（= 本项目 RTPD 价格来源）** | TD，每 15 分钟，首次 run 同 HASP | "Run Every 15-minute RTUC with First Run Same as HASP"；15 分钟市场逐 15 分钟出清 | BPM Market Ops Exhibit 2-1、§2.3.2；BPM Instruments 全文 "Fifteen Minute Market" | `[已确认]` |
| 9 | **RTED / 5-min（RTD）** | TD，每 5 分钟 | "Run RTED … every 5 minutes" | BPM Market Ops Exhibit 2-1 | `[已确认]` |
| 10 | **2 日超前负荷/需求预报发布** | **TD−2 18:00** | "Prepare/Publish Demand Forecasts & AS Requirements" TD-2 1800；Two Day-Ahead RUC 报告 "available … as soon as the Two Day-Ahead process run is completed (**14:00 and 18:00 PST**)" | BPM Market Ops Exhibit 2-1；BPM Instruments §10.1.1（p.157） | `[已确认]` |
| 11 | **Virtual Bid 结算机制** | — | "The CAISO ensures that Virtual Bids (Supply and Demand) are **not passed from the IFM to RUC or RTM**"；"**Virtual Bids and Awards are not considered in the RTM**" | BPM Market Ops §2.3.1（p.102）、§2.3.2（p.104） | `[已确认]` |

补充（官方最佳实践，非硬性）：BPM Instruments §8.2.1 Note "Bids, including Self-Schedules, should be submitted **within 30 minutes of Market Close**"（即建议 9:30 前提交，留足校验时间；硬性截止仍为 10:00）。

---

## 2. 决策时点语义（对齐本项目命名）

```
 target_date T（交付日 D+1）   ←───────── 决策日 decision_date = T−1 = D ─────────→  T 当日
                                     │                                        │
   【日前市场】DAM bid 截止 = D 日 10:00 PT   ←── 决策时点（提交 Virtual Bid）    │
        ↓ MPM → IFM → RUC（10:00–13:00 窗口）
   【日前市场】DA 结果发布 = D 日 13:00 PT   →  T 的 DA LMP 此时可见（= label）  │
                                                                             【实时市场】T 日 0:00 起
                                                                              RTPD（15-min）/ RTD（5-min）
                                                                              T 日深夜完整（= label）
```

- **决策时点（正确）**：`decision_date 10:00 PT`（DAM Market Close，官方值）
- **label 时点**：T 的 DA LMP 于 `decision_date 13:00 PT` 发布；T 的 RTPD 于 T 日实时产生
- **滞后约定不变**：`lag1 → T−2` 仍然是"决策时已知的最后一个整日完整交付日"（DA(T−1) 虽已发布，但 RTPD(T−1) 要到决策日深夜才完整）。cutoff 改为 10:00 不影响此约定。

---

## 3. 核心问题专项回答

### Q：目标交易日(D+1) 的 DA clearing price，在交易员提交 Virtual Bid 时是否已经已知？

### A：**否。`[已确认]`**

推理链（每步官方出处）：

1. **Virtual Bid 属于 DAM**，必须于 **TD−1 10:00**（= 决策日 D 日 10:00）前提交。
   —— "The Day-Ahead Market (DAM) for both virtual and physical Bids closes at 1000 hours on the day before the Trading Day"（BPM Instruments §2.1）；"SCs may submit Bids to the DAM beginning seven days prior to the Trading Day and up until 1000 hours the day prior to the Trading Day"（BPM Instruments §3.1，p.40）。
2. **提交后** CAISO 才执行 MPM → IFM → RUC，D+1 各小时 DA LMP 在 IFM 中出清（10:00–13:00 窗口）。
   —— Exhibit 2-1：TD-1 1000 "Close Day-Ahead Market / Determine RMR & MPM / Run IFM & RUC"（BPM Market Ops）；"The IFM … clears Virtual Bids submitted by SCs"（BPM Market Ops §6.6）。
3. **DA 结果（含 D+1 各 hour LMP）于 ~13:00 PT 发布**，晚于 bid 截止 3 小时。
   —— "DAM results are published … by 1300 hours of the day before the Trading Day"（BPM Instruments §8.1.9.1.2）；"Results are published at 1:00 p.m."（caiso.com）。
4. **Virtual Bid 只在 IFM 出清**，不进 RUC / RTM；RTM 不含 Virtual。
   —— "Virtual Bids (Supply and Demand) are not passed from the IFM to RUC or RTM"；"Virtual Bids and Awards are not considered in the RTM"（BPM Market Ops §2.3.1 / §2.3.2）。

⇒ **虚拟报价的 PnL = (DA LMP − RT LMP) 的预测**。出价时 DA 侧价格本身尚未确定——交易员只能基于 10:00 前可得信息（历史价格、负荷/天气预报、日历等）预测该价差。这**支持**本项目"D+1 的 DA/RTPD/Return 只能作事后 label"的核心假设，但**修正**了截止时间。

### 当前口径的误差
仓库/文档把 "bid cutoff" 写作 **13:00**，实为 **DA 结果发布**时间。官方事实：
- bid 截止（决策时点）＝ **10:00 PT**
- DA 结果发布（目标日 DA 价可见，label 时点）＝ **13:00 PT**

用 13:00 作为"决策截止"在语义上会把 cutoff 置于**出清之后**，隐含"目标日 DA 价在决策时可得"的错误暗示。虽然现有代码因 lag 约定（T−2 起）未实际用到 10:00–13:00 间的信息，但**口径必须修正**，否则泄漏审计边界失真。

---

## 4. 时区核验

| 项 | 结论 | 来源 | 状态 |
|---|---|---|---|
| 市场计时基准 | **Pacific Time (PT)**，即加州本地时间 | BPM 全文多处使用 "Pacific Time"：ICE 天然气/电力价 "8 AM – 9 AM **Pacific Time**"（BPM Instruments p.298/330/514）、GHG 价 "~22:00 **Pacific**"（p.403）、manual RLC "8:00 AM **Pacific Time**"（p.485） | `[已确认]` |
| 夏令时 | 实行 DST，报表用 "PPT"（Pacific **Prevailing** Time，即生效中的 PST 或 PDT） | BPM 报表业务触发 "Trade Date + 3 days by 6:00 AM **PPT**"（BPM Instruments §10.2） | `[已确认]` |
| 本项目数据区间 | 2026-04 ~ 2026-08 ⇒ **PDT（UTC−7）**；冬季（11–3 月）为 PST（UTC−8） | 由夏令时规则推出 | `[已确认]` |

> 建议：代码/文档统一标注 **"10:00 PT"** 并注明"4–10 月即 PDT"。是否在代码里引入时区常量（如 `US/Pacific`）由 Lead 决定；本报告不涉及数据文件的 `valid_pt` naive 戳问题（属 Agent 数据审计范围）。

---

## 5. cutoff 从 13:00 → 10:00 对现有特征可用性的影响

| 特征组 | 来源时点 | 10:00 cutoff 下是否仍可用 | 说明 |
|---|---|---|---|
| 价格滞后 lag1/2/7、rolling、日级统计、load_actual_lag1、天气滞后、peer_* | 全部 ≤ T−2 交付日完整 | ✅ 可用 | 远早于 D−1 10:00，无影响 |
| `load_2da_forecast` | TD−2 18:00 发布（2DA 需求预报） | ✅ 可用 | Exhibit 2-1 显示需求预报 TD-2 1800；Two-Day-Ahead RUC 过程 14:00/18:00 PST 完成（BPM Instruments §10.1.1）→ 早于决策时点 |
| `t2m_next` / `ssrd_next` / `wind100_next` | 目标日天气（性质待确认，默认禁用） | 维持禁用 | 与 cutoff 无关 |
| label（actual_da / actual_rtpd / actual_return / direction） | T−1 13:00（DA 发布）/ T 日实时 | ❌ 不可用（label） | **不变**：13:00 仍是 label 可见时点 |

**结论：cutoff 改为 10:00 不会丢失任何当前 X 特征**；是口径/正确性修正。当前虽无特征落在 10:00–13:00 窗口，但后续若新增"当日 AM 数据"类特征，必须以 10:00 为界。

---

## 6. 需修改的文件清单（若 cutoff 改为 10:00；Agent A 只列清单，不实际修改）

| # | 文件 | 改什么 |
|---|---|---|
| 1 | `code/canonical.py` | `DECISION_CUTOFF_DESC`（L117）→ `"decision_date 10:00 PT (Day-Ahead Market Close, virtual+physical bids; DA results published 13:00 PT)"`；模块头注释（L29）与 Leakage Guard 注释（L130）、schema `row_semantics`（L506）、矩阵头（L554）中 `13:00` → `10:00 PT`；**保留** L33-34 注释（DA(T−1) 于 T−2 13:00 发布——这是结果发布时间，语义正确）与 L600 label 行 `target_date-1 13:00（出清后）`（这是 label 可见时点，正确，不动）。 |
| 2 | `code/data/feature_schema.json` | 由 canonical.py 重建（`python code/canonical.py`）→ `decision_cutoff` 字段自动更新。 |
| 3 | `docs/feature_availability_matrix.md` | 由 canonical.py 自动重生成；人工核对表头"决策时点 = … 10:00 前"。 |
| 4 | `docs/business_contract.md` | §2 "约 D-1 日 13:00，日前出清前" → "**D-1 日 10:00 PT（DAM Market Close / bid cutoff，官方）**；日前出清结果 13:00 PT 发布（目标日 DA 价此时可见，作 label）"。 |
| 5 | `docs/DecisionPipeline.md` | §2 输入表 "决策时点 D-1 13:00 前（DA bid cutoff 前）" → "**D-1 10:00 PT 前（DAM Market Close / bid cutoff）**；DA 结果 13:00 PT 发布"。 |
| 6 | `code/backtest.py` | as-of 时间戳语义：决策快照 / 报告头（L734 "决策时点：decision_date 13:00 前"）→ `decision_date 10:00 PT`；确认 as-of 过滤以 10:00 为界（当前特征全为日级、无实际影响，仅文档/快照时间戳）。 |
| 7 | `code/backtest_outputs/backtest_report.md` | 由 backtest.py 重新生成；核对头部时间线文字。 |
| 8 | `code/deliverables/make_ppt.py` | L405 "决策时点 D 日 13:00 前" → "D 日 10:00 PT 前"；重新生成 PPT。 |
| 9 | `code/analysis/agentA_report.py` | L40 "决策截止为 decision_date 13:00" → "decision_date 10:00 PT"。 |
| 10 | `code/model_v2.py` + `code/data/model_v2_notes.json` | "D-1 13:00 DA bid cutoff 前" → "D-1 10:00 PT DAM Market Close 前"；`contract_ref` 注释（"不改 canonical decision_cutoff"）在 canonical 更新后同步口径。 |
| 11 | `code/features.py`（已弃用，保留作对比） | L15 "决策时点 = D 日 13:00 前" → 10:00 PT（可选；如不再引用可跳过）。 |
| 12 | `数据审计与业务口径.md`（根目录，历史审计文档） | §1 时间线图与 §5 待确认项 #4 的 "13:00" → "10:00 PT（bid cutoff）/ 13:00 PT（结果发布）"；建议标注"本版为历史口径，已由 market_timeline.md 修正"。 |
| 13 | `工程报告.md`（根目录） | "决策时点 D 日 13:00 前" → 10:00 PT。 |
| 14 | `README.md` | 无硬编码时间；仅复核其引用链（business_contract / DecisionPipeline 更新后一致性即可），如需可加一句"决策时点 = D-1 10:00 PT（DAM Market Close）"。 |

> 执行顺序建议：`canonical.py` → 重跑生成 schema/矩阵 → `backtest.py` 重跑 → `make_ppt.py` 重生成 → 文档同步。全部改动由 Lead 统一执行。

---

## 7. `[待确认]` 项（未查证 / 本报告不裁决）

1. **DA 结果发布是否恒为 13:00**：官方表述为 "by 1300 hours" / "approximately 1300" / "1:00 p.m."，个别日可能略迟（视出清耗时）。对决策影响为零（发布在 cutoff 之后），故不阻塞；若需精确 publish 时刻可用于 label 时间戳校准。
2. **`load_CA_ISO_TAC_2DA.csv` 每行（某日期的负荷预测）的实际生成/发布时点**：本报告确认"2DA 需求预报 TD−2 18:00 发布"与"Two-Day-Ahead RUC 过程 14:00/18:00 PST 完成"，但该 CSV 文件具体取数时间（OASIS 下载时刻）未核验。现有 `ASSUMED_AVAILABLE` 标注可维持。
3. **天气预报 vs 实测**：属数据审计范围（`zone_weather_hourly.csv`），与 cutoff 无关，本报告不裁决。
4. **RTPD 术语**：价格文件列名 "RTPD" 对应 CAISO 15 分钟实时市场（现称 FMM；历史称 Real-Time Pre-Dispatch）的 LMP，逐 15 分钟出清。数据文件将其聚合为小时值。此点供 Lead/数据侧复核。

---

## 附：引用原文摘录（含页码）

**BPM for Market Instruments V93**
- §2.1（PDF p.29）："The Day-Ahead Market (DAM) for both virtual and physical Bids closes at 1000 hours on the day before the Trading Day"
- §3.1（p.40）："SCs may submit Bids to the DAM beginning seven days prior to the Trading Day and up until 1000 hours the day prior to the Trading Day. SCs may submit Real-Time Market Bids beginning when the Day-Ahead Schedules are published at 1300 hours the day prior to the Trading Day and up until 75 minutes prior to the start of the relevant Trading Hour."
- Exhibit 8-1（p.117–118）：stage 1 "Submit bids up to seven days prior to the Trading Day"；stage 9 "10:00 am — HASP T-75 — The DAM and RTM are closed for bid submission"
- §8.1.9.1（p.119）："Day-Ahead Market Bids may be submitted up to seven days prior to the Trading Day … at 1000 hours of the day prior to the Trading Day."
- §8.1.9.1.2（p.120）："The RTM for a given Trading Hour opens after the DAM results are published for the Trading Day that includes the relevant Trading Hour (by 1300 hours of the day before the Trading Day) and closes 75 minutes before the start of that Trading Hour."
- §8.2.1（p.122，Note）："Bids, including Self-Schedules, should be submitted within 30 minutes of Market Close."
- §9.1.10.1（p.146）："When the DAM clears, at approximately 1300 hours"
- §10.1.1（p.157）："This advisory data will be available … as soon as the Two Day-Ahead process run is completed (14:00 and 18:00 PST)."

**BPM for Market Operations V105**
- §2.3.1（p.102）："Bidding for the Day-Ahead Market (DAM) closes at 1000 hours on the day before the Trading Day … The CAISO ensures that Virtual Bids (Supply and Demand) are not passed from the IFM to RUC or RTM."
- §2.3.2（p.104）："Bidding for the Real-Time Market (RTM) closes 75 minutes before the beginning of each Trading Hour."；"Virtual Bids and Awards are not considered in the RTM."；"All HASP Schedules for the Trading Hour are published approximately 45 minutes before the start of each Trading Hour."
- Exhibit 2-1（p.100–101）：TD-7 Open DAM；TD-2 1800 Prepare/Publish Demand Forecasts；TD-1 1000 Close DAM / MPM / IFM & RUC；TD-1 1300 Publish DA Schedules / Open RTM；TD TH-75' HASP / 15-min RTUC；TD every 5 min RTED
- §6.6（p.265）："The IFM performs Unit Commitment and Congestion Management, clears Virtual Bids submitted by SCs …"

**www.caiso.com**（Market Operations → Products & Services）
- "The day-ahead market opens for bids and schedules seven days before … closes the day prior to the trade date"
- "Results are published at 1:00 p.m."（页面未标时区；结合 BPM 全文 "Pacific Time" 判定为 PT）
