# -*- coding: utf-8 -*-
"""
code —— 项目代码包（V0.2 起成为可导入包）。

注意：目录名 `code` 与 Python 标准库 `code`（交互式解释器辅助模块）同名。
本 `__init__.py` 使仓库内的 `code` 成为常规包，可 `from code.risk_gate import ...`。
仓库内现有脚本（backtest.py / model_v2.py 等）为独立运行脚本，不受影响；
如需使用标准库 `code` 模块，请移除此包或改名（本项目不需要标准库 `code`）。

子包：
  code/risk_gate/    Risk Gate 独立模块（PASS/WARNING/REJECT + reason_code）
  code/decision/     White-box Rule Engine（BUY_DA / SELL_DA / NO_TRADE）
"""

__version__ = "0.2"
