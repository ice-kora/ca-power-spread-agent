# 公司数据 vs CAISO 官方（OASIS）逐小时对账报告

> 对账工程师：Agent F（公司 vs 官方数据对账）｜ 日期：2026-08-09
> 对账窗口：**target 交易日 2026-07-01 ~ 2026-07-07**（7 天 × 3 节点 × 24 小时）
> 官方源：CAISO OASIS API（`oasis.caiso.com/oasisapi/SingleZip`，匿名可用）
> 公司源：`code/data/master.csv` + 原始 `价格数据/*.xlsx`
> 方法：用已有采集器 `CAISOLoadForecastCollector`（SLD_FCST）+ 按 `docs/caiso_oasis_sources.md` 的 query 清单直拉 PRC_LMP / PRC_RTPD_LMP。只读对账，未修改模型/回测。

---

## 0. TL;DR（结论先行）

1. **价格类数据与官方完全一致，可直接用于严格回测**：
   - `master.da_price` == OASIS `PRC_LMP`（DA LMP 总价）**逐小时完全相等**（504/504，max_diff ≤ $0.0001）。
   - `master.rtpd_price`（小时值）== OASIS `PRC_RTPD_LMP`（FMM 15-min）**按小时取 4 区间算术均值后完全相等**（504/504，max_diff ≤ $0.0001）。
   - 原始 `-c` 价格文件的 DA/RTPD 列 == 官方 **MCC 阻塞分量**（`LMP_CONG_PRC`）**完全相等**（每节点 168/168，max_diff ≤ $0.0001）—— **解开了 CLAUDE.md 中"`-c` 文件含义未记录"的悬案**。
2. **负荷预报有口径差异，不可直接对严格数值**：`master.load_2da` 与官方 `SLD_FCST`（CA ISO-TAC）**同序列、不同 vintage**，系统性偏低 ~1%（mean_abs_diff=259 MW / corr=0.998），峰值小时差最大（-2.0%），夜间最小（-0.3%）。**不是**时区/DST/小时对齐错误（最佳对齐 = 原始 (0,0)）。
3. **无时区/DST/hour-ending 错位**：价格类 100% 匹配本身就是铁证——公司 `hour=1..24`（H1 = 00:00–01:00 PT，hour-beginning）与 OASIS `OPR_DT/OPR_HR` 直接对齐，无需任何平移。
4. **官方内部口径确认**：DA LMP 总价 = MCE + MCC + MCL + MGHG（504/504，max_diff=2e-5），即公司 `da_price` 含 GHG 分量。
5. 对 OASIS 参考文档（`caiso_oasis_sources.md`）的**一处勘误**：`PRC_RTPD_LMP` 的 `node=` 过滤**不能**与 `grp_type=ALL_APNODES` 联用（返回 "No data"）；实测可用组合是 `node=<NODE>` 且**不带** grp_type（§3.3）。

---

## 1. 对账范围与方法

| 项 | 值 |
|---|---|
| 交易日窗口 | 2026-07-01 ~ 2026-07-07（全部在 PDT，无 DST 跳变日） |
| 节点 | `SNLNDRO_1_N001`(ZP26)、`CONTROLX_1_N001`(ZP26)、`ELCAJNGT_7_N001`(SP15) |
| 逐小时口径 | `hour=1..24`，H1 = 00:00–01:00 PT（hour-beginning），公司 `master.csv` 与 OASIS `OPR_HR` 一致 |
| 对账字段 | `da_price`、`rtpd_price`、`load_2da`、原始 `-c` 文件 DA/RTPD 列 |
| 匹配容差 | 绝对 $0.5 或 相对 0.1%（价格类远低于该阈值，接近浮点精度） |

### 官方数据获取（query 配方，均实测有效）

| 数据类型 | OASIS query | 关键参数 | 价格列 |
|---|---|---|---|
| DA LMP | `PRC_LMP` | `market_run_id=DAM&version=1&node=<NODE>&resultformat=6`（不带 grp_type） | `MW`（`LMP_TYPE=LMP`） |
| RTPD（FMM 15-min） | `PRC_RTPD_LMP` | `market_run_id=RTPD&version=1&node=<NODE>&resultformat=6`（不带 grp_type） | `PRC`（`LMP_TYPE=LMP`，`OPR_INTERVAL=1..4`） |
| 系统 DA 负荷预报 | `SLD_FCST` | `market_run_id=DAM&version=1&resultformat=6`，过滤 `TAC_AREA_NAME=CA ISO-TAC` | `MW` |
| 阻塞分量 MCC | 上述两个 query 的 `LMP_TYPE=MCC` | — | `MW` / `PRC` |

时间窗口统一用 **25h UTC 窗** `[T 07:00Z, T+1 08:00Z]` 覆盖 PT 运营日 H1..H24，服务端按 `OPR_DT == T` 过滤。

### 统计量定义
- `match_rate`：`|company − official| ≤ 0.5 或 ≤ 0.1%·|official|` 的占比
- `mean_abs_diff` / `max_diff`：绝对差均值 / 最大值
- `missing_rate`：无配对小时占比
- `timezone_diff` / `hour_alignment_diff`：以 `date_shift ∈ {−1,0,+1} × hour_shift ∈ {−3..+3}` 网格试错求最佳匹配对齐（见各节 `hour_alignment_diag`）

---

## 2. 对账结果总表

| 数据类型 | 公司字段 | 官方源 | 配对 | match_rate | mean_abs_diff | max_diff | corr | 结论 |
|---|---|---|---|---|---|---|---|---|
| DA LMP | `master.da_price` | `PRC_LMP` DAM `LMP_TYPE=LMP` | 504/504 | **1.000** | **0.0 $** | **0.0001 $** | **1.000** | **完全一致** |
| RTPD 小时 | `master.rtpd_price` | `PRC_RTPD_LMP`（4×15min 均值） | 504/504 | **1.000** | **0.0 $** | **0.0001 $** | **1.000** | **完全一致** |
| DA 阻塞分量 | `-c` 文件 DA 列 | `PRC_LMP` `MCC` | 504/504 | **1.000** | **0.0 $** | **0.0001 $** | **1.000** | **完全一致** |
| RTPD 阻塞分量 | `-c` 文件 RTPD 列 | `PRC_RTPD_LMP` `MCC`（4×15min 均值） | 504/504 | **1.000** | **0.0 $** | **0.0001 $** | **1.000** | **完全一致** |
| 系统负荷预报 | `master.load_2da` | `SLD_FCST` CA ISO-TAC | 168/168 | 0.0595 | 259.4 MW | 1115.9 MW | 0.9979 | **同序列不同 vintage（见 §4）** |

> 注：负荷为系统级（3 节点同值），故为 7 天 × 24h = 168 对；价格为节点级，7×3×24 = 504 对。

---

## 3. 价格类逐项结果（DA / RTPD / -c 阻塞分量）

### 3.1 DA LMP —— 完全一致

| 节点 | n_both | match_rate | mean_abs_diff | max_diff | corr |
|---|---|---|---|---|---|
| SNLNDRO_1_N001 | 168 | 1.0 | 0.0 | 0.0001 | 1.0 |
| CONTROLX_1_N001 | 168 | 1.0 | 0.0 | 0.0001 | 1.0 |
| ELCAJNGT_7_N001 | 168 | 1.0 | 0.0 | 0.0001 | 1.0 |

- 公司 `da_price` 与官方 DA LMP 总价**数值相等到浮点精度**（max_diff = $0.0001，来自公司文件仅保留 4 位小数的舍入）。
- 官方内部一致性：**LMP = MCE + MCC + MCL + MGHG**（504/504，max_diff=2e-5）→ 公司 `da_price` 是**含 GHG 的全口径 LMP 总价**。
- 时区/小时诊断：最佳对齐 = (date_shift=0, hour_shift=0)，100% 无需平移 → 公司 date/hour 与 OASIS `OPR_DT/OPR_HR` 完全同口径。
- 示例：SNLNDRO 2026-07-01 H1 公司 $25.374 = 官方 $25.374。

### 3.2 RTPD（FMM 15-min）—— 完全一致

| 节点 | n_both | match_rate | mean_abs_diff | max_diff | corr |
|---|---|---|---|---|---|
| SNLNDRO_1_N001 | 168 | 1.0 | 0.0 | 0.0001 | 1.0 |
| CONTROLX_1_N001 | 168 | 1.0 | 0.0 | 0.0001 | 1.0 |
| ELCAJNGT_7_N001 | 168 | 1.0 | 0.0 | 0.0001 | 1.0 |

- 公司小时 `rtpd_price` == 官方 `PRC_RTPD_LMP` 该小时 **4 个 15-min 区间（OPR_INTERVAL=1..4）的算术均值**，相等到浮点精度。
- **结论：公司 RTPD 的小时值就是官方 FMM 15-min 的简单平均，无加权/取首/取末等其它聚合。** 这回答了项目"RT 口径（RTPD/FMM vs RT5M）"的关键疑问——公司用的是 **RTPD/FMM**，不是 RT 5-min（`PRC_INTVL_LMP`）。

### 3.3 `-c` 价格文件 —— 官方 MCC 阻塞分量（新发现）

CLAUDE.md 此前注明"`-c` 文件含义未记录"。本次对账确认：

| 节点 | `-c` DA ↔ 官方 MCC | `-c` RTPD ↔ 官方 RTPD MCC(15min均值) |
|---|---|---|
| SNLNDRO_1_N001 | 168/168，max_diff=0.0000 | 168/168，max_diff=0.0000 |
| CONTROLX_1_N001 | 168/168，max_diff=0.0001 | 168/168，max_diff=0.0001 |
| ELCAJNGT_7_N001 | 168/168，max_diff=0.0001 | 168/168，max_diff=0.0001 |

- **`-c` 文件 DA 列 = 官方 DA `MCC`（`LMP_CONG_PRC` 阻塞分量）；`-c` 文件 RTPD 列 = 官方 RTPD `MCC`（15-min 均值）。**
- 佐证：`-c` 文件第三行市场名是 `DARTPD Cong Spread`（主文件是 `DARTPD Return`）——"Cong" 与"阻塞分量"口径吻合。
- CONTROLX 全窗口 168/168 小时阻塞非零（如 07-01 H1 MCC = −$168.82），SNLNDRO 64/168 小时非零（07-01 H8 = +$2.63）——节点间阻塞形态差异也符合（CONTROLX 为东内华达重约束节点）。

---

## 4. 负荷预报口径差异定位（重点）

### 4.1 统计

| 量 | 值 |
|---|---|
| 配对 | 168/168（`missing_rate=0`，窗口内公司 `load_2da` 与官方 SLD_FCST 均齐全） |
| match_rate | 0.0595（容差 0.5 MW / 0.1%） |
| mean_abs_diff | 259.4 MW（≈ 官方均值 25027 MW 的 **1.04%**） |
| max_diff | 1115.9 MW（≈ 5.5%，出现于 2026-07-01 H12） |
| corr | 0.9979 |
| 偏差方向 | **公司系统性偏低**：168 小时中 134 小时 company < official，均值 rel diff = **−0.90%** |

### 4.2 差异结构（逐小时）

| 小时段 | 平均 rel diff | 说明 |
|---|---|---|
| H1–H8（凌晨/早） | −0.48% ~ −0.73% | 夜间偏差最小（H24 仅 −0.30%） |
| H9–H17（白天） | −0.67% ~ **−2.01%** | **峰值 H11–H16 偏差最大**（H14 = −2.01%，H12/H13 ≈ −1.5%） |
| H18–H24（晚） | −0.30% ~ −0.84% | 回落 |

> 差异随负荷规模放大，**正午（光伏高峰）最严重**、夜间最轻——这是"太阳能/天气预报演化误差"的典型形态，而非固定口径偏移。

### 4.3 排除的假设（诊断证据）

1. **不是时区/DST/hour-ending 错位**：`hour_shift_diag` 在 date_shift×hour_shift 全网格上的最佳对齐就是原始 (0,0)，且 match_rate 不随任何平移提升。若公司 hour 是 hour-ending 或差 7/8 小时，网格会找到显著更优对齐——没有。
2. **不是固定的结构性偏移**（如漏掉某个子 LAP）：逐日均差波动极大（2026-07-01 平均 −566 MW，07-04 仅 −40 MW），固定偏移应为常数。
3. **不是"净负荷 vs 总负荷"**：净负荷口径夜间应几乎无差（夜间无光伏），但夜间仍有 −0.3% ~ −0.7% 的系统性负差。
4. **不是其它 TAC 区域**：对 2026-07-01 官方 SLD_FCST 全部 36 个 TAC_AREA_NAME 逐一对比，`CA ISO-TAC` 最接近（mean_abs_diff=566 MW），其余区域差 20–40 倍（PGE-TAC 12709 MW、SCE-TAC 12975 MW 等）。公司数据就是 CA ISO-TAC 这一条序列。

### 4.4 最可能的口径来源：同一序列、不同 vintage（预报时点）

- 公司文件名即 `load_CA_ISO_TAC_**2DA**.csv`，项目 `canonical.py` 对 `load_2da` 的标注是"2DA 负荷预测，预计提前 2 日发布"（`available_at = target_date − 2`）。
- OASIS `SLD_FCST`（DAM）返回的是该运营日的**最终日前预报**（D−1 13:00 PT 发布，BPM Market Instruments V93）；OASIS **不保留逐日预报历史版本**（`caiso_oasis_sources.md` §5.2：SLD_FCST 无更新时间戳、无版本历史），因此**无法从 OASIS 直接拉到 D−2 的旧版本来做严格同 vintage 对账**。
- 综合 4.2/4.3 证据：公司 `load_2da` 是 **CA ISO-TAC 系统 DA 负荷预报的更早一个 vintage（约 2 天前发布）**，其天气/光伏假设较 D−1 最终版偏旧，导致正午（光伏主导）低估最明显、夜间低估较轻，且日间波动（天气逐日演变）解释了逐日均差的不稳定。

> 诚实声明：由于 OASIS 无 SLD_FCST 版本历史，vintage 身份无法被"逐日拉取对照"证实，属**强证据推断**（命名 + 项目标注 + 差异结构 + 36 区域排除），非可复核的逐版本比对。

---

## 5. 结论：公司数据可用于严格回测的边界

| 公司字段 | 是否可直接用于严格回测 | 说明 |
|---|---|---|
| `master.da_price`（DA LMP） | **是** | 与官方 `PRC_LMP` 逐小时完全一致（含 GHG 的全口径 LMP 总价）。官方 DA 在 D−1 13:00 PT 发布，作为**标签**严格可用。 |
| `master.rtpd_price`（小时 RTPD） | **是**（聚合口径已确认） | 与官方 FMM 15-min 算术均值完全一致。官方 RTPD 在区间开始前 ≤22.5 min 发布，无版本历史（事后修正会改写），严格 as-of 需结合价格修正报告或接受终值≈发布值。 |
| `-c` 文件 DA/RTPD（MCC 阻塞分量） | **是** | 与官方 MCC 完全一致；阻塞/损耗分量的业务含义（`da_price = MCE+MCC+MCL+MGHG`）已闭环。 |
| `master.load_2da`（系统负荷预报） | **否**（数值层面）；**可作为相对特征** | 与官方 `SLD_FCST` 终值有 ~1% 系统偏差（vintage 差异）。若严格回测需要官方数值，应改用 `SLD_FCST` 重新拉取；但 corr=0.998，作为**形状/相对量级**特征可接受。另注意官方 SLD_FCST `not_backtest_safe=True`（无逐日发布戳）。 |

**换算建议**：
- DA–RT 价差交易标签（`spread = da_price − rtpd_price`）**两个分量都与官方完全一致**，价差本身可直接严格回测。
- 若需在特征层使用系统负荷，优先从 OASIS `SLD_FCST` 重建"终值"；若沿用公司 `load_2da`，请标注为"D−2 近似（≈1% 系统偏差）"，避免被误认为官方 D−1 终值。

---

## 6. 对账产物与复现

| 产物 | 路径 |
|---|---|
| 对账脚本（只读，可重跑） | `code/analysis/agent_f_reconcile.py` |
| 对账统计 JSON | `code/data/recon_official.json` |
| OASIS 原始响应缓存（断点续跑） | `code/data/recon_oasis_cache.json` |

复现：`python code/analysis/agent_f_reconcile.py`（重跑走缓存，不重复请求；删缓存后重拉，请求间隔 6s + 429 指数退避）。

---

## 7. 局限与后续

1. **窗口内无 DST 跳变**（7 月全程 PDT）。时区/日对齐在常规日 100% 匹配；DST 回拨日（25h）的边界行为（采集器 `_normalize` 已做去重）未在本窗口实测，建议补一个 3 月/11 月边界窗口。
2. **负荷 vintage 推断受限**：OASIS SLD_FCST 无版本历史，无法逐日拉 D−2 旧版做同 vintage 对照。若需实证，可改用 `Historical OASIS Data Downloader`（AWS S3，需凭证）或联系公司数据源标注 `published_at`。
3. **RTPD/DA 无版本历史**：官方"终值"可能被事后价格修正改写；严格 as-of 回测建议按 `caiso_oasis_sources.md` §5.2 结合 Price Correction 报告识别。
4. **RT 5-min（`PRC_INTVL_LMP`）未对账**：本项目公司数据不含 RT 5-min 口径；如需 RT 精细对账（15min vs 5min）需另行取数。
5. **`-c` 文件对账仅覆盖 7 天窗口**：若要证明全历史一致（2024-01 起），可扩展窗口重跑脚本（缓存会缓存增量请求）。
