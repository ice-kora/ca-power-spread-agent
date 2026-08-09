# MVP Demo Cases（黄金案例）

> V0.3 MVP ｜ 选择规则 + 真实案例表。所有案例来自 test 窗口（2026-06~08）真实历史数据，用 `mvp_demo.py` 原样运行，**未手工修改任何预测/PnL/规则**。

## 一、选择规则

1. 从 test 窗口候选 `(node, hour)` 中用 `python mvp_demo.py --auto-reveal --json-out ...` 逐一运行。
2. 按"决策类型代表性"选 5 个案例，覆盖：SELL 盈利、NO_TRADE 避险（RiskGate 成功）、NO_TRADE 弱信号、模型判断错误、Evidence 被 Time Gate 拒绝。
3. **诚实声明**：**无明显 BUY_DA 放行真实例**——test 窗口 BUY 方向被 Risk Gate 系统性拒绝（CONTROLX BUY 逆漂移 + 重尾，全被拒）。因此以"CONTROLX BUY 被拒（→NO_TRADE）"作为 BUY 方向的代表案例，并如实说明这是系统设计而非遗漏。
4. 每个案例可直接复现：`python mvp_demo.py --decision-date <D> --node <N> --hour <H>`（决策日 D，目标日 D+1）。

## 二、案例表

| 案例 | 决策日 D | 节点 | H | 模型方向 | RiskGate | 最终建议 | 实际 Return | PnL | 复盘要点 |
|---|---|---|---|---|---|---|---|---|---|
| **B · SELL 盈利（彩票右尾）** | 2026-07-16 | CONTROLX | 3 | SELL | PASS | **SELL_DA** | +2251.3 | **+2251.3** | 正漂移节点 SELL 抓到极端右尾；模型对、方向赚 |
| **C1 · NO_TRADE 避险（Gate 成功）** | 2026-07-08 | CONTROLX | 2 | BUY | REJECT | **NO_TRADE** | +2216.3 | **0** | BUY 逆漂移被拒；实际 Return 转正，BUY 若执行亏 −2216 → Gate 避免大亏（RISK_GATE_SUCCESS） |
| **C2 · NO_TRADE 弱信号** | 2026-07-10 | SNLNDRO | 10 | BUY | WARNING(LOW_CONFIDENCE) | **NO_TRADE** | +8.4 | **0** | 信号弱/置信不足 → 观望是正常业务结果 |
| **D · 模型 SELL 但错** | 2026-07-20 | SNLNDRO | 20 | SELL | WARNING(MODEL_UNSTABLE) | **SELL_DA** | −59.1 | **−59.1** | 模型 SELL 但实际负；gate 仅 WARNING 放行 → 记录 MODEL_ERROR/尾损，供复盘 |
| **E · Evidence 被 Time Gate 拒** | 2026-07-08 | CONTROLX | 2 | — | — | — | — | — | 真实 18Z GFS（11:00 PT > cutoff）被隔离 POST-DECISION / NOT USED，**演示系统不穿越** |

## 三、演示脚本

```bash
# Case B（SELL 盈利）
python mvp_demo.py --decision-date 2026-07-16 --node CONTROLX_1_N001 --hour 3 --auto-reveal
# Case C1（NO_TRADE 避险）
python mvp_demo.py --decision-date 2026-07-08 --node CONTROLX_1_N001 --hour 2 --auto-reveal
# Case C2（NO_TRADE 弱信号）
python mvp_demo.py --decision-date 2026-07-10 --node SNLNDRO_1_N001 --hour 10 --auto-reveal
# Case D（模型 SELL 但错）
python mvp_demo.py --decision-date 2026-07-20 --node SNLNDRO_1_N001 --hour 20 --auto-reveal
```

## 四、诚实边界

- 无 BUY_DA 放行真实例（BUY 被系统性拒）→ 用 CONTROLX BUY 被拒代表。
- Case D 反映模型 alpha 弱（SELL 在 SNLNDRO 也有错），符合 `ALPHA=WEAK / MVP ≠ 盈利系统` 声明。
- 全部为真实历史数据，未调 test/PnL/阈值。
