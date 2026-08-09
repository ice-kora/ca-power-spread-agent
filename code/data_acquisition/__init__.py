# -*- coding: utf-8 -*-
"""code/data_acquisition —— 输入侧 As-of 数据采集层（Agent D 交付）。

提供 AsOfRecord / feature_snapshot 结构、两套采集模式的 available_at 解析、
时间门槛判定与校验，保证可追溯、防穿越。设计见 docs/asof_schema_design.md。

采集框架（Agent E 扩展，docs/data_acquisition_poc.md）：
  base.py          Collector 基类：run(query_date) → raw+normalized 落盘 + 时间戳 + 校验
  weather_gfs.py   GFS 历史预报采集器（真实源：Open-Meteo Single Runs；00Z/06Z 可严格回测，
                   12Z/18Z 无法可靠证明发布早于 cutoff → 不可回测，见 docs/asof_schema_design.md §2.3）
                   —— P0-1 单一事实来源；agent/evidence/gfs_forecast.py 为其纯 Adapter
  caiso_oasis.py   CAISO 官方 DA 负荷预报采集器（真实源：OASIS SLD_FCST，
                   not_backtest_safe=True）
  validation.py    数据质量校验（缺失率 / DST / decision_eligible / 值域 / mock 声明）
  run_acquisition.py CLI（python code/data_acquisition/run_acquisition.py --date ...）
  tests/           单测（python -m unittest code.data_acquisition.tests.test_data_acquisition -v）
降级：网络失败 → 缓存 raw → 确定性 MOCK（is_mock=True 明确标注，不冒充真实预报）。
"""
