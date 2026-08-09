# -*- coding: utf-8 -*-
"""
agent —— V0.2 白盒交易决策 Agent 的三个新建模块：

  agent/evidence/     模块 1：Agent Evidence（外部证据，当前全 UNCERTAIN）
  agent/case_library/ 模块 2：Case Library（历史案例检索，≠ Rule Engine）
  agent/explanation/  模块 3：Decision Card（结构化决策卡片 + 生成器）

本目录还存放 stage3 数据产物（risk_features.parquet、top_loss/profit_events.csv 等），
与本包代码共存，不冲突。
"""
