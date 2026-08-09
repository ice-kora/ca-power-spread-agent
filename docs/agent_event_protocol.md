# Agent Event Protocol — V0.4.1 Observable Agent Event Contract

> Lead 定义（P0）。所有 streaming 事件必须来自**真实执行**，前端据此渲染，禁止假动画/假步骤。

## 事件协议（SSE event + JSON data，每条以 `\n\n` 结束）

| event | data | 触发者 | 说明 |
|---|---|---|---|
| `session_start` | `{conversation_id, message_id, decision_id}` | backend | 本次问答开始 |
| `route_start` | `{label:"正在理解你的问题…"}` | backend | 路由阶段开始 |
| `route_result` | `{intent, tools_planned:[...]}` | backend | 已确定意图与计划工具 |
| `tool_start` | `{tool, label}` | **统一 Tool Executor** | 每个工具调用前（预设+非预设都必须发） |
| `tool_result` | `{tool, summary}` | 统一 Tool Executor | 工具成功（summary 业务化，如"当前建议：不交易"/"1 条可用·1 条被拒"） |
| `tool_error` | `{tool, message}` | 统一 Tool Executor | 工具失败 |
| `answer_start` | `{label:"正在整理回答…"}` | backend | LLM 生成开始 |
| `answer_delta` | `{text}` | LLM streaming | 逐块追加（真实 stream） |
| `answer_done` | `{}` | backend | 回答完成 |
| `guard_start` | `{}` | backend | 一致性检查开始 |
| `guard_result` | `{status:"PASS"\|"BLOCKED", detail?}` | backend | 数字/方向守卫结果 |
| `heartbeat` | `{tick}` | backend | 工具/LLM 超 2~3s 无事件时保活 |
| `error` | `{code, message}` | backend | 异常 |
| `session_done` | `{status:"ok"\|"degraded"\|"blocked"\|"error"}` | backend | 本次问答结束 |

## 统一 Tool Executor（唯一入口，禁止第二套调用路径）

```
execute_agent_tool(tool_name, args, emit):
    emit(tool_start, {tool, label: 业务名})
    try:
        result = ACTUAL_TOOL(tool_name, args)      # 6 个现有工具
        emit(tool_result, {tool, summary: business_summary(result)})
    except Exception:
        emit(tool_error, {tool, message})
```

工具业务名：get_decision→读取当前交易建议 / get_feature_explanation→检查模型判断 / get_evidence→查询外部证据（使用·拒绝）/ get_similar_cases→查询历史案例 / get_data_provenance→查询数据血缘 / get_post_trade_review→查询事后复盘。

## 关键约束

- **绝不展示 private chain-of-thought**：只展示 Action / Tool / Observable Result / Status。
- **不伪造工具调用**：只有真实 tool_start 对应的工具执行了才显示。
- **LLM 真流式**：OpenAI-compatible 用 `stream=true` 读 delta → `answer_delta`，禁止 `response.json()` 后一次性返回。
- **非预设问题也逐工具发 tool_start**（Router 动态路径与预设路径事件一致）。
- **Memory 只作对话上下文**：回答当前交易问题时必须 Tool / DecisionSnapshot 为准，Memory 不得覆盖 Decision/Risk/Evidence/PnL/Final。
- **Reveal 前不得注入 unrevealed outcome**（Tool access control 优先）。
- 响应头：`Content-Type: text/event-stream` / `Cache-Control: no-cache` / `X-Accel-Buffering: no` / `Connection: keep-alive`。

## 前端行为

- `fetch` + `response.body.getReader()` + `TextDecoder` 逐块解析，每收 `answer_delta` 立即追加；光标保留至 `answer_done`。
- `heartbeat` 只保持 loading，不新增内容。
- 会话消息**追加**（不覆盖）；markdown 渲染（markdown-it + DOMPurify sanitize）；数值业务化（≤2 位小数）。
