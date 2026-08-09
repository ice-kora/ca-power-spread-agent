# 数据采集框架 PoC 报告（Agent E）· `code/data_acquisition/`

> 作者：Agent E（自动采集 PoC 工程师）｜ 日期：2026-08-09
> 配套：`code/data_acquisition/`（可运行代码 + 单测）｜ schema 层见 Agent D 的
> `docs/asof_schema_design.md` + `code/data_acquisition/schemas.py`（本框架直接复用）
> 范围：采集器层（base / weather_gfs / caiso_oasis / validation / CLI / tests）。
> **不联网也可运行（缓存/MOCK 降级）；不造假 forecast；不改模型。**

---

## 0. 结论先行（TL;DR）

**打通了两个真实数据源，均可运行、可落盘、通过 As-of Time Gate：**

| 数据源 | 采集器 | 性质 | as-of | 实测（2026-08-09 联网） |
|---|---|---|---|---|
| **NCEP GFS 0.25° 历史预报**（Open-Meteo Single Runs） | `weather_gfs.GFSWeatherCollector` | as-issued 历史 run 预报（温度/风/辐照） | ✅ backtest safe | 72 条/日全 eligible，0 缺失 |
| **CAISO 官方 DA 负荷预报**（OASIS `SLD_FCST`，`SYS_FCST_DA_MW`） | `caiso_oasis.CAISOLoadForecastCollector` | 官方日前负荷预报（MW） | ⚠️ not_backtest_safe（无逐日发布时间戳） | 24 条/日全 eligible，0 缺失 |

- 每条 As-of 记录含完整时间戳（`published_at` / `available_at` / `retrieved_at` /
  `decision_cutoff`，一律 **UTC naive ISO**），`decision_eligible` **由代码程序计算**
  （`available_at <= decision_cutoff`），不联网也有 **LIVE → CACHE → MOCK** 三级降级。
- 单元测试 56 个全过（Agent D schema 32 + 本框架 24；联网时含 2 个 live 测试，
  离线时 live 自动 skip，其余照常通过）。

---

## 1. 打通的数据源

### 1.1 GFS 天气预报（第一优先，已通）

- **端点**：`https://single-runs-api.open-meteo.com/v1/forecast`
  参数 `models=gfs_global`（NCEP GFS 0.25°）、`run=YYYY-MM-DDTHH:00`、`timezone=UTC`、
  `wind_speed_unit=ms`，逐节点经纬度（`节点位置.xlsx`）。
- **as-of 语义（P0-1 修正）**：决策日 D 取 **D 06Z run（06:00 UTC，默认；00Z/06Z 可回测）**
  作为 as-issued 历史预报（= forecast_issue_time），目标交付日 T = D+1。
  `published_at = init + 发布延迟模型`（BACKTEST 保守上界 +6h → 12:00 UTC；PRODUCTION 典型 +4h），
  **绝不把 init 当 available_at**。`decision_cutoff = D 10:00 PT → UTC`（夏 17:00 / 冬 18:00 UTC）。
  12Z/18Z 无法可靠证明发布早于 cutoff → `backtest_eligible=FALSE`（详见 asof_schema_design.md §2.3）。
- **变量映射**（对齐项目 canonical）：`temperature_2m → t2m(°C)`、
  `wind_speed_100m → wind100(m/s)`、`shortwave_radiation → ssrd(W/m²)`。
- **target_time 对齐项目 hour∈1..24（PT）约定**：`target_time_pt_to_utc(T, h)`，
  例如 H1 = `TT00:00 PT → TT07:00Z`（PDT）、H24 = `TT23:00 PT → T+1T06:00Z`。
- 不伪造：Open-Meteo 无该 run / 网络失败 → 返回空 → 走降级；绝不用 ERA5/再分析冒充。

### 1.2 CAISO 官方 DA 负荷预报（第二优先，已通 + 明确标注）

- **端点**：`http://oasis.caiso.com/oasisapi/SingleZip`，`queryname=SLD_FCST`、
  `market_run_id=DAM`、`resultformat=6`（返回 ZIP 内 CSV，列含 `OPR_DT`（PT 运营日）、
  `OPR_HR`（PT 小时 1..24）、`MW`）。
- **资源**：默认 `TAC_AREA_NAME == "CA ISO-TAC"`（系统全口径 DA 负荷预报），
  可配置为 `PGE-TAC / SCE-TAC` 等区域资源。
- **窗口语义（实测确认）**：OASIS 按 `INTERVALSTARTTIME_GMT` 过滤；OPR_DT=T 的 24 小时
  区间在 UTC 覆盖 `[TT07:00Z(PDT H1)/TT08:00Z(PST H1), T+1T06:00Z(PDT H24)/T+1T07:00Z(PST H24)]`。
  采集器用 **25h 窗口 `[T 07:00Z, T+1 08:00Z]`**，冬/夏令时都覆盖 H1..H24。
- **as-of 口径（诚实声明）**：OASIS 响应**不暴露逐日发布时间戳**（只有检索时刻），
  无法 pin 精确 vintage。因 DA 负荷预报是 DAM 的**输入**，必然在 D 10:00 PT 收盘前发布，
  故 `published_at = available_at = D 10:00 PT`（== cutoff，边界 eligible，program
  计算通过），但采集器**始终标记 `not_backtest_safe=True`**（validation 输出 WARNING）：
  禁止用于严格 as-of 回测，仅可作生产/参考数据。
- 夏令时/冬令时均已实测（2026-07-08 与 2026-01-08），OPR_HR 与 `target_time_pt_to_utc`
  完全一致。

---

## 2. 目录结构与运行方式

```
code/data_acquisition/
  __init__.py        包说明（Agent D schema 层 + Agent E 采集层）
  schemas.py          As-of 结构（Agent D 交付，本框架复用：AsOfRecord / resolve_available_at / 时间工具）
  base.py             Collector 基类 + CollectionResult + 缓存/MOCK 降级
  weather_gfs.py      GFSWeatherCollector（真实源）
  caiso_oasis.py      CAISOLoadForecastCollector（真实源 + not_backtest_safe）
  validation.py       数据质量校验（缺失率 / DST / decision_eligible / 值域 / mock 声明）
  run_acquisition.py  CLI
  test_schemas.py     Agent D 单测（32 个）
  tests/test_data_acquisition.py   本框架单测（24 个）
  cache/<source>/     raw + normalized 落盘（含真实拉取样例）
docs/data_acquisition_poc.md        本报告
```

### 运行

```bash
# 真实拉取（联网）
python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source all
python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source gfs --node ELCAJNGT_7_N001
python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source caiso --resource "CA ISO-TAC"

# 离线降级（不联网）：有缓存走 CACHE；无缓存走确定性 MOCK（明确标注）
python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source all --offline

# 生产模式（available_at = max(published, retrieved)，cutoff 后拉取自动 ineligible）
python code/data_acquisition/run_acquisition.py --date 2026-07-08 --source gfs --mode PRODUCTION

# 单测（56 个全过；live 测试联网时执行、离线自动 skip）
python -m unittest code.data_acquisition.test_schemas code.data_acquisition.tests.test_data_acquisition -v
```

### 落盘内容（`cache/<source>/<date>[_<cycle>].{raw,normalized}.json`）

- **raw**：真实 API 响应（GFS=Open-Meteo JSON；CAISO=OASIS CSV 解析后的 rows），
  外层 `_meta`（source / query_date / provenance / retrieved_at）——可复现。
- **normalized**：`metadata`（provenance / degraded / is_mock / not_backtest_safe /
  last_error）+ `timestamps`（published_at / available_at / retrieved_at /
  decision_cutoff）+ `validation` + `records`（每条含 `decision_eligible` 计算值）。

---

## 3. 降级路径（不联网也可运行）

统一在 `Collector.run()` 实现 **LIVE → CACHE → MOCK**：

```
run(query_date)
  ├─ network_enabled=False 或 _fetch_raw() 抛错
  │     ├─ 有缓存 cache/<source>/<date>.raw.json  → provenance=CACHE（底层数据是否 mock 保留）
  │     └─ 无缓存                                → provenance=MOCK（is_mock=True）
  └─ 落盘 raw + normalized + 校验（validation 对 MOCK/not_backtest_safe 输出 WARNING）
```

- **不造假**：MOCK 是**确定性合成**数据（季节性/日周期曲线，可复现），每条记录的
  `metadata.provenance="MOCK"` + validation WARNING 明确注明
  “仅演示采集流程，禁止用于生产/回测，生产需接真实 API”。
- 缓存优先于 MOCK：先跑一次 live 落盘后，离线重跑同一日期会自动用缓存里的**真实数据**
  （provenance=CACHE，is_mock=False）。

---

## 4. 时间戳与 As-of Time Gate

**时间口径**（与 Agent D schema 层、`agent/evidence/gfs_forecast.py` 一致，全 UTC naive）：

| 字段 | GFS | CAISO |
|---|---|---|
| `forecast_run` / `issue_time` / `model_run_time` | `2026-07-08T06:00Z` / `2026-07-08T06:00:00` / `2026-07-08T06:00:00`（06Z） | `DAM-2026-07-09` / `2026-07-08T17:00:00` |
| `published_at` | D 06Z init + 6h 保守上界（12:00 UTC）；PRODUCTION = init+4h 典型 | D 10:00 PT→UTC（== cutoff，保守上界） |
| `available_at` | 按模式：BACKTEST = init+6h（仅 00Z/06Z）；12Z/18Z = None（不可回测）；PRODUCTION=max(pub,ret) | 同左（BACKTEST=17:00Z） |
| `retrieved_at` | 本次采集墙钟（UTC naive） | 同左 |
| `decision_cutoff` | `make_decision_cutoff(D)` | 同左 |
| `decision_eligible` | **程序计算** `available_at <= decision_cutoff` | 同左 |

**Time Gate 校验链**（全程序，无 LLM）：
1. `AsOfRecord.decision_eligible`（schemas）按 R1/R2 计算；
2. `validation.validate_collection` **复算**每条记录并与存储值比对（防漂移 → ERROR）；
3. `validation.check_dst` 用 zoneinfo 独立换算 D 10:00 PT→UTC，与存储 cutoff 比对
   （DST 错误 → ERROR）；
4. 本框架测试额外做**双实现一致性**：`schemas.make_decision_cutoff` 与
   `agent/evidence/gfs_forecast.decision_cutoff_utc` 在 2026 全年 DST 边界逐日一致
   （见 `test_cutoff_matches_agent_evidence`）。

实测 GFS/CAISO 产出全部 `decision_eligible=True`，validation 无 ERROR。

---

## 5. 测试结果（2026-08-09 实测）

```
$ python -m unittest code.data_acquisition.test_schemas code.data_acquisition.tests.test_data_acquisition -v
Ran 56 tests in 4.2s
OK
```

覆盖点：
- **时间口径**：cutoff DST（2026-03-08 转夏 / 11-01 转冬）、schemas↔agent 双实现一致、
  zoneinfo 独立换算一致、H1/H24 target_time 映射（PDT/PST 各一次）。
- **GFS 离线**：fixture normalize（72 条、全 eligible、时间戳、值对齐）、MOCK 降级、
  CACHE 降级、落盘文件、网络优先于缓存。
- **CAISO 离线**：fixture normalize（24 条、node=CAISO_TAC、region=SYSTEM）、MOCK/CACHE、
  not_backtest_safe 标注。
- **校验**：eligible 漂移→ERROR、缺失率→WARNING、重复→ERROR、NaN→WARNING、
  DST 错误→ERROR、PRODUCTION 拉取晚于 cutoff→全 ineligible。
- **Live**（联网时）：GFS 72 条 / CAISO 24 条，provenance=LIVE，全 eligible、0 缺失、
  值域合理。

CLI 实测输出（在线）：
```
[NCEP_GFS_025_via_OpenMeteo] 2026-07-08 → 2026-07-09 records=72 eligible=72 provenance=LIVE errors=0
  timestamps: {"published_at": "2026-07-08T12:00:00", "available_at": "2026-07-08T12:00:00",
               "retrieved_at": "2026-08-09T04:58:43", "decision_cutoff": "2026-07-08T17:00:00"}
[CAISO_OASIS_SLD_FCST] 2026-07-08 → 2026-07-09 records=24 eligible=24 provenance=LIVE errors=0
  validation: [WARNING] not_backtest_safe=True：源未暴露逐日发布时间戳，禁止用于严格 as-of 回测
```

---

## 6. 与既有模块的关系（依赖方向）

```
canonical.py / 特征层（decision_eligible 只消费 eligible snapshot）
    ↑
feature_snapshot（Agent D schemas）——由 AsOfRecord 派生
    ↑
本框架采集器：AsOfRecord（source/forecast_run/issue_time/published_at/available_at/
                       retrieved_at/target_time/value/decision_cutoff/decision_eligible）
    ↑ 复用
code/data_acquisition/schemas.py（AsOfRecord / resolve_available_at / make_decision_cutoff）
agent/evidence/schema.py + time_gate.py（同语义；schemas 为独立实现，已用专项测试同步）
    ↑ 真实 API
Open-Meteo Single Runs（GFS） / CAISO OASIS SLD_FCST
```

- 本层管**数值型输入特征**的 vintage；`agent/evidence` 管**事件证据**的 vintage，互补。
- 双实现一致性已由测试钉住（cutoff 换算 / target_time 映射），后续改动须同步测试。

---

## 7. 生产接入还缺什么

| 项 | 现状 | 缺口 / 建议 |
|---|---|---|
| GFS 历史回测 as-of | ✅ 可用（BACKTEST=vintage） | 档案起点 2026-04-02；val 窗口早段仍缺 → 诚实只取 04-02 后 |
| CAISO 负荷预报 as-of | ⚠️ `not_backtest_safe=True` | OASIS 无逐日发布时间戳 → 严格回测前需：a) 用排程表口径核验发布时点；b) 或换带 vintage 的源 |
| raw 可复现性 | ✅ 落盘 raw + `_meta` | OASIS CSV 大（~340KB/日，含全部 36 个 TAC 区域）；可后续裁剪到目标资源以减体积 |
| Production 调度 | 模式开关已就绪 | 需 cron/看门狗：D 09:30 PT 启动、cutoff 后关闭窗口、失败重试（CAISO 已内置 3 次退避） |
| 入 feature_snapshot | 未接 | 下一步：把 eligible AsOfRecord → `FeatureSnapshot`（Agent D schema）→ canonical 特征 |
| DST 过渡日 | 已防御（去重） | 25h/23h 运营日需专项回归（当前 fall-back 日同一 OPR_HR 重复值保留首条） |
| 认证 / 限流 | Open-Meteo 免费无 key；OASIS 无 key | OASIS 有速率限制（实测偶发 HTTPError → 采集器已 3 次重试+2s 退避）；生产建议注册 API key / 错峰 |
| 多节点 | GFS 三节点坐标已内置 | CAISO 为系统级（CAISO_TAC），无节点细分 |

---

## 8. 验收自检

| 检查项 | 结果 |
|---|---|
| 打通至少一个真实数据源 | ✅ 两个（GFS 天气 + CAISO 负荷预报），现场联网实测 |
| 可配置日期、可运行 | ✅ `run_acquisition.py --date YYYY-MM-DD [--source gfs\|caiso\|all]` |
| 保存 raw + normalized + 全部时间戳 | ✅ `cache/<source>/<date>_<cycle>.{raw,normalized}.json` |
| 通过 As-of Time Gate | ✅ `decision_eligible` 程序计算 + validation 复算 + zoneinfo DST 校验 |
| 单元测试 | ✅ 56 个（Agent D 32 + 本框架 24）全过；离线可跑（live 自动 skip） |
| 不联网也有可运行降级 | ✅ LIVE → CACHE → MOCK，MOCK 明确标注禁止生产/回测 |
| 不造假 forecast | ✅ MOCK 确定性合成且显式标注；真实源失败不编造 |
| 不改模型 | ✅ 未触碰任何模型/回测/evidence 层代码 |
