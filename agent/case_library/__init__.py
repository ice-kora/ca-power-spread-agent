# -*- coding: utf-8 -*-
"""
agent/case_library —— V0.2 白盒交易决策 Agent · 模块 2：Case Library。

Case Library ≠ Rule Engine：
  - 只做历史检索（"历史有无类似情况"），不输出决策规则。
  - 本包提供 Case 数据结构 + 从 stage3 真实极端事件初始化的 cases.json。
"""

from agent.case_library.case import Case

__all__ = ["Case"]
