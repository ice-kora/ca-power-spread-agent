# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A CA-ISO (California Independent System Operator) **day-ahead electricity price forecasting / trading** dataset. As of now it contains **data only — no source code, no README, no build/package files**. All analysis/ML work is expected to be written from scratch.

The dataset joins three signal types by date/hour:

- **Nodal day-ahead vs. real-time prices** (target variable) — `价格数据/`
- **System-wide load forecasts & actuals** — `load_CA_ISO_TAC_2DA.csv`, `load_CA_ISO_TAC_ACTUAL.csv`
- **Hourly weather by zone** (temperature, solar radiation, wind) — `zone_weather_hourly.csv`
- **Node→zone mapping** — `节点位置.xlsx`

`TAC` = total actual load (system-wide, single series, not per-zone). Zones are NP15 / SP15 / ZP26.

## Data layout and gotchas

### Price files: `价格数据/*.xlsx` (nodal LMP, `$`/MWh)

Three nodes, each in its own file. The `-c` suffix files (`*-c.xlsx`) have the same shape and date range as their originals but **different values** — their exact meaning is undocumented; do not assume they're interchangeable.

Column layout (flat Excel table, **not** a tidy frame):
- **Col A `Date`** — filled only on the **first row of each date group**; blank on the RTPD / DARTPD-Return rows. Merge groups by carrying the last non-null date forward.
- **Col B (no header!)** — market type: `DA`, `RTPD`, or `DARTPD Return` (the "Hours" header in col C is effectively this block's label row for B/C).
- **Col C `Hours`** — usually `24`; can be smaller (partial days exist, e.g. `Hours=1` with only H24 populated).
- **Col D `Avg`**, **Col E `Total`** — across populated hours only.
- **Col F–AC `H1`–`H24`** — hourly LMP.

Each date has **3 rows** (one per market type). `DARTPD Return` = DA price minus RTPD price per hour.

Coverage differs by node:
- `SNLNDRO_1_N001`, `CONTROLX_1_N001` — 2024-01-01 → 2026-08-05 (~2,845 rows)
- `ELCAJNGT_7_N001` — only from 2026-03-03 (~469 rows)

### Load files (CSV)

Both have columns `Date, Avg, H1…H24`:
- `load_CA_ISO_TAC_2DA.csv` — day-ahead load forecast. Date format is `YYYY/M/D` (e.g. `2025/10/1`, no zero-padding, slash separator). 500 rows, 2025-10-01 → 2026-07-09.
- `load_CA_ISO_TAC_ACTUAL.csv` — actual load. Date format is ISO `YYYY-MM-DD`. 490 rows, 2025-04-01 → 2026-08-04.

### Weather: `zone_weather_hourly.csv`

Hourly per zone, columns `zone, valid_pt, t2m_c, ssrd_wm2, wind100, Date`:
- `zone` — NP15 | SP15 | ZP26 (3 zones; only ZP26 & SP15 have node price files — NP15 has no nodal file here)
- `valid_pt` — naive hourly timestamp `YYYY-MM-DD HH:MM:SS` (timezone is not recorded; CA-ISO data is America/Los_Angeles — confirm tz handling before aligning to prices)
- `t2m_c` — 2 m temperature (°C); `ssrd_wm2` — surface shortwave downward radiation (W/m²); `wind100` — 100 m wind speed (m/s)
- 36,216 rows ≈ 3 zones × 24 h × ~503 days, 2025-04-02 → **2026-08-19** (extends past load/price data — appears to include a forecast window)

### Node metadata: `节点位置.xlsx`

Columns: 节点ID, 节点名称, 类型, 区域(Zone), 纬度, 经度, 制图X坐标, 制图Y坐标, 所属省份. Node→zone mapping:
- `SNLNDRO_1_N001` → ZP26, `CONTROLX_1_N001` → ZP26, `ELCAJNGT_7_N001` → SP15

## Environment

- Python 3 + pandas + openpyxl are installed, but the installed **openpyxl is 3.1.2 while pandas requires ≥ 3.1.5 — `pandas.read_excel(...)` raises `ImportError`**. Read the `.xlsx` files with `openpyxl` directly, or `pip install -U openpyxl` first.
- Directory and file names are Chinese (`价格数据/`, `节点位置.xlsx`); quote paths when scripting.
