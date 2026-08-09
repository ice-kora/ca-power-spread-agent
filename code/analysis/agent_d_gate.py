# -*- coding: utf-8 -*-
"""
agent_d_gate.py —— Risk Gate 白盒实现（Agent D）v2
================================================
输入候选交易（node, target_date, hour, pred_direction, expected_return, ...）
+ as-of 风险特征，输出 PASS / REJECT / PASS_WITH_WARNING + reason_code。

规则（阈值在 train+val 校准，test 只验证）：
  R7  BUY_ON_POSITIVE_DRIFT_NODE / SELL_ON_NEGATIVE_DRIFT_NODE   [REJECT]
        - 正漂移节点（CONTROLX train+val mean +9.68）上 BUY = 逆漂移 + 重右尾
          （无条件 BUY maxloss -3656, cvar99 -916；LR 代理 val BUY mean -10.33）
          -> REJECT
        - 负漂移节点（ELCA train+val mean -1.15）上 SELL = 逆漂移 + 低样本
          + 左尾 -357 -> REJECT
        - SNLNDRO 漂移 +2.61 且尾微小（cvar99 -98）-> 不触发
  R6  LOW_SAMPLE_SUPPORT                                        [REJECT]
        - hist_n < 150（ELCA 冷启动，test ~120）-> REJECT（Agent A low_sample）
  R4  EXTREME_TAIL_NODE                                         [PASS_WITH_WARNING]
        - 同 node×hour 历史 cvar99/rcvar99 过深（<-600）-> WARNING 而非 REJECT。
          验证：cvar99 阈值在 train+val 无法捕获 CONTROLX SELL 最大亏损（-2065 行
          cvar99=-276 不超标）；extreme_state(lag1_pct) 信号 regime 翻转（train+val
          负 EV / test 正 EV +45,509），不能作为 REJECT 规则。
  R2  MODEL_DISAGREEMENT                                        [补充 reason]
        - CONTROLX 上 ML 双 BUY（interpretable+catboost 同 BUY）与 rule SELL 冲突
          -> 由 R7 覆盖（CONTROLX BUY 已 REJECT），此处作为 R7 的并集 reason
  R1  CONFIDENCE（删除）——train+val 证实未校准（conf 越高 mean 越差）
  R3  VOLATILITY（删除）——train+val 无单调区分（Agent A 警示）
  R5  EXPECTED_EDGE（删除）——train+val 扫描 |er| 阈值不改善尾部
"""
import numpy as np
import pandas as pd


def apply_gate(df):
    """对候选交易 df 应用 Risk Gate。

    输入列要求：node, target_date, hour, pred_direction, expected_return,
    rule_dir, interpretable_dir, catboost_dir, hist_n, cvar99, rcvar99（可缺省）。
    返回：df + gate_decision + reason_code 列。
    """
    d = df.copy()
    n = len(d)
    decisions = np.full(n, "PASS", dtype=object)
    warnings = np.full(n, "", dtype=object)
    reasons = np.full(n, "", dtype=object)

    for i in range(n):
        row = d.iloc[i]
        node = row["node"]
        dir_ = row["pred_direction"]
        reasons_i = []

        # ---------- R7 方向门 [REJECT] ----------
        if dir_ < 0 and node == "CONTROLX_1_N001":
            decisions[i] = "REJECT"
            reasons_i.append("BUY_ON_POSITIVE_DRIFT_NODE")
        if dir_ > 0 and node == "ELCAJNGT_7_N001":
            decisions[i] = "REJECT"
            reasons_i.append("SELL_ON_NEGATIVE_DRIFT_NODE")

        # ---------- R6 低样本 [REJECT] ----------
        hist_n = row.get("hist_n", np.nan)
        if pd.notna(hist_n) and hist_n < 150:
            decisions[i] = "REJECT"
            reasons_i.append("LOW_SAMPLE_SUPPORT")

        # ---------- R2 模型冲突（CONTROLX ML 双 BUY，与 R7 并集） ----------
        if node == "CONTROLX_1_N001" and dir_ < 0:
            interp = row.get("interpretable_dir", np.nan)
            cat = row.get("catboost_dir", np.nan)
            if pd.notna(interp) and pd.notna(cat) and interp < 0 and cat < 0:
                if decisions[i] != "REJECT":
                    decisions[i] = "REJECT"
                if "MODEL_DISAGREEMENT" not in reasons_i:
                    reasons_i.append("MODEL_DISAGREEMENT")

        # ---------- R4 尾部 [PASS_WITH_WARNING] ----------
        cvar99 = row.get("cvar99", np.nan)
        rcvar99 = row.get("rcvar99", np.nan)
        if decisions[i] != "REJECT":
            if dir_ > 0 and pd.notna(cvar99) and cvar99 < -600:
                decisions[i] = "PASS_WITH_WARNING"
                warnings[i] = "EXTREME_TAIL_NODE"
            if dir_ < 0 and pd.notna(rcvar99) and rcvar99 < -600:
                decisions[i] = "PASS_WITH_WARNING"
                warnings[i] = "EXTREME_TAIL_NODE"

        # ---------- 组装 ----------
        reasons[i] = "|".join(dict.fromkeys(reasons_i))

    d["gate_decision"] = decisions
    d["reason_code"] = reasons
    d["warning_code"] = warnings
    return d


if __name__ == "__main__":
    sample = pd.DataFrame([
        {"node": "CONTROLX_1_N001", "pred_direction": -1, "cvar99": -700, "rcvar99": -900,
         "interpretable_dir": -1, "catboost_dir": -1, "rule_dir": 1, "hist_n": 700},
        {"node": "ELCAJNGT_7_N001", "pred_direction": 1, "cvar99": -224, "rcvar99": -40,
         "interpretable_dir": 1, "catboost_dir": 1, "rule_dir": 1, "hist_n": 78},
        {"node": "SNLNDRO_1_N001", "pred_direction": 1, "cvar99": -60, "rcvar99": -30,
         "interpretable_dir": 1, "catboost_dir": 1, "rule_dir": 1, "hist_n": 600},
        {"node": "SNLNDRO_1_N001", "pred_direction": -1, "cvar99": -60, "rcvar99": -700,
         "interpretable_dir": -1, "catboost_dir": -1, "rule_dir": 1, "hist_n": 600},
    ])
    print(apply_gate(sample)[["node", "pred_direction", "gate_decision", "reason_code", "warning_code"]])
