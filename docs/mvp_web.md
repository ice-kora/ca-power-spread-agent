# CAISO Trading Decision Agent · Web MVP（mvp_web.py）

> Agent E 交付物：`mvp_web.py` + `templates/mvp_index.html`（+ `static/mvp.css` + `templates/mvp_sources.html` + `templates/mvp_how.html`）。
> 冻结交易核心：决策对象 100% 来自 `code/decision_service.py`，不改模型/规则/阈值/PnL/evidence/case 逻辑。
> 铁律：LOCK 前不展示 actual_*（服务端强制）；不造假证据；无 API Key 时核心照常运行，Ask 面板诚实显示 LLM NOT CONFIGURED。

## 一、启动

```bash
python mvp_web.py                    # http://127.0.0.1:5000（证据默认 real：实时 GFS，失败诚实降级为空）
python mvp_web.py --offline          # 默认证据=离线静态（不取外部 GFS，纯本地演示，最快）
python mvp_web.py --port 8080 --host 0.0.0.0 --debug
```

数据范围：decision_date = 2026-06-01 ~ 2026-08-04（test 窗口 target_date 2026-06-02 ~ 2026-08-05 的前一日）。节点：CONTROLX/SNLNDRO（ZP26）、ELCAJNGT（SP15）。

## 二、URL / 路由清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 主页面 Decision Workspace（Decision Context / Data & Provenance / Model / Evidence / Similar Cases / Risk Gate / Rule Engine / Reveal / Audit + Ask 面板 + Daily Brief） |
| GET | `/data-sources` | Data Sources 页面（各字段来源，防止误以为全是实时 CAISO API） |
| GET | `/how-it-works` | How It Works 页面（10:00 cutoff 流程图 + BUY/SELL/NO_TRADE 语义 + R 规则表 + 诚实边界） |
| GET | `/api/meta` | 元信息：nodes / 黄金案例 / 日期范围 / 版本 / LLM 状态 / 证据模式 / reason_code 翻译表 |
| GET | `/api/decisions` | 已生成决策轻量索引（含 lock 状态） |
| POST | `/api/decision` | 运行决策 `{decision_date,node,hour,evidence}` → 完整结构化决策对象 |
| GET | `/api/decision/<id>` | 单个决策对象（含 lock 状态） |
| POST | `/api/decision/<id>/lock` | LOCK DECISION（锁定前禁止 reveal，服务端强制） |
| POST | `/api/decision/<id>/reveal` | REVEAL ACTUAL OUTCOME（仅锁定后可调；403 若未锁定） |
| POST | `/api/ask` | Ask Trading Agent `{question, decision_id}` → `{answer, tools_called, trace}` |
| POST | `/api/brief` | GENERATE DAILY BRIEF `{decision_date, node?, all_hours?, evidence?}` → 汇总 |

## 三、现场演示建议流程（黄金案例）

顶部 Golden Demo Cases 下拉即可一键复现（全部真实 test 窗口数据，来自 `docs/mvp_demo_cases.md`）：

1. **B · SELL 盈利（彩票右尾）** `2026-07-16 CONTROLX H3` → SELL_DA，实际 Return +2251，复盘 NORMAL_PROFIT/MODEL_DIRECTION_CORRECT。
2. **C1 · NO_TRADE 避险（RiskGate 成功）** `2026-07-08 CONTROLX H2` → NO_TRADE，BUY 逆漂移被拒，复盘 RISK_GATE_SUCCESS。
3. **C2 · NO_TRADE 弱信号** `2026-07-10 SNLNDRO H10` → NO_TRADE（|er| 小 / 信号弱）。
4. **D · 模型 SELL 但错（诚实展示）** `2026-07-20 SNLNDRO H20` → SELL_DA 但实际 −59，复盘 HIGH_UNCERTAINTY。
5. **E · Evidence 被 Time Gate 拒** `2026-07-08 CONTROLX H2`（同 C1 参数）→ S4 展示真实 18Z GFS（published_at 晚于 10:00 cutoff）被隔离 POST-DECISION / NOT USED。

演示要点（证明"这是 Agent"）：
- **Ask Trading Agent**：点快捷问题 → 显示 Agent Trace（Route → Tool Selected → Arguments → Result → LLM Answer），无 Key 时工具路由仍执行、结构化结果照常返回。
- **GENERATE DAILY BRIEF**：先跑几个决策，再对同一日期点 BRIEF / 补齐 24 小时，看 BUY/SELL/NO_TRADE 与 RiskGate 汇总。
- **锁定→揭晓**：LOCK 前点 REVEAL 会收到 403 NOT_LOCKED；LOCK 后显示 Actual DA/RTPD/Return/PnL、Direction Correct?、Risk Gate Correct?、Decision Quality、结构化复盘。

## 四、页面截图说明

运行后浏览器打开 `http://127.0.0.1:5000/` 即可截图；建议窗口宽 1440+。可截：
1. **主页面全貌**：AS-OF 横幅 + 控制栏 + S1~S9 七节卡片 + 右侧 Ask/Brief 面板。
2. **决策页**：运行黄金案例 B 后截 S3（模型指标 + 特征 z 贡献）、S7（FINAL RECOMMENDATION + 白盒推导链）。
3. **证据隔离**：案例 E 后截 S4（ELIGIBLE 空 / REJECTED 18Z GFS + 拒绝原因）。
4. **复盘页**：LOCK 后 REVEAL，截 S8（Actual DA/RTPD/PnL + Review Category chips + What happened/Why/Lessons）。
5. **Ask 面板**：点快捷问题后截 Agent Trace（Step 1 Route → Step 2 Tool Selected → Step 3 LLM）。

## 五、诚实边界

- **Signal/Strategy MVP, Not Full CAISO Settlement Simulator**：MODEL SIGNAL IS EXPERIMENTAL / ALPHA=WEAK / MVP ≠ 盈利系统。
- 数据来源：核心特征来自公司文件（已对账 OASIS）；GFS 仅作 Agent Evidence；OASIS 仅验证未进生产特征。详见 `/data-sources`。
- 无 BUY_DA 放行真实例（test 窗口 BUY 被 Risk Gate 系统性拒绝），系统保守是设计而非遗漏。
