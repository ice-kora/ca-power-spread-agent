# -*- coding: utf-8 -*-
"""
code/llm_copilot.py —— Trading Decision Copilot（Agent D：最小 Tool-Calling LLM Agent）
========================================================================================

供 Web "Ask Agent" 面板（Agent E）调用。**LLM 不决定 BUY/SELL/NO_TRADE；所有数字必须来自
Tool（code/decision_service.py 的 6 个工具）；无 API Key 可降级。**

核心铁律（硬约束）：
  * 最终建议 BUY_DA / SELL_DA / NO_TRADE 仅由 Predictive Model + Risk Gate + Rule Engine
    产生（decision_service.run_decision），LLM 只解释、汇总、对比案例、解释来源、生成复盘摘要。
  * 所有数字由程序执行 Tool 获得（_execute_tool），LLM 不能编造或覆盖；
    最后有一道程序化“数字完整性守卫”在 answer 上兜底（防 LLM 声称与工具结果不符的数值）。
  * actual_* 只在 get_post_trade_review 返回 REVEALED 时出现；未 reveal 的决策，
    Tool 返回 OUTCOME_NOT_REVEALED，LLM 拿不到 actual。
  * 无 LLM_API_KEY → 降级模式：Ask Agent 返回 "LLM NOT CONFIGURED"，
    但预置路由仍会执行工具并返回结构化结果（Web 核心交易流程不受影响）。

两种工具调用路径：
  1) 原生 function/tool calling（优先）：OpenAI 兼容（openai/deepseek）与 Anthropic
     REST API 的 `tools` 参数；SDK 可装可不装（SDK 不可用时走 httpx/requests 直连）。
  2) 受控 JSON Tool Router（fallback / 强制）：LLM 输出 JSON 指定 tool+arguments，
     由程序执行 Tool，禁止 LLM 自行编造 Tool 输出。

环境变量配置（API key 禁止写死在代码）：
  LLM_PROVIDER     openai | deepseek | anthropic | mock（默认 openai）
  LLM_API_KEY      API Key（必填，否则降级）
  LLM_MODEL        模型名（缺省按 provider 给默认值）
  LLM_BASE_URL     可选，覆盖 API base（OpenAI 兼容端点 / 代理）
  LLM_USE_ROUTER    1/true → 强制 JSON Tool Router（不原生 calling）
  LLM_MAX_TOOL_ROUNDS  工具调用最大轮数（默认 4）
  LLM_TIMEOUT       请求超时秒数（默认 30）

集成方式（Agent E，Web Ask Agent 面板）：
  from code.llm_copilot import ask, copilot_status
  out = ask(question, decision_id=<当前查看的决策>, context={"decision_date":..,"node":..,"hour":..})
  # out = {answer, status, provider, model, degraded, tools_called:[{tool,args,result_summary}], trace}
  copilot_status() -> {configured, degraded, provider, model, mode, tools, ...}

Trace（Agent Trace，不含私有 CoT）：
  {user_question, mode, degraded, provider, model, steps:[
      {step, stage:"route",  selected_tool, reason},
      {step, stage:"tool",   tool, arguments, result_summary, status},
      {step, stage:"llm",    provider, model, mode, final_answer_excerpt},
      {step, stage:"guard",  status:"PASS"|"BLOCKED", detail} ]}
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code.decision_service import (  # noqa: E402
    TOOL_SCHEMAS,
    default_service,
)

__version__ = "0.1"

SUPPORTED_PROVIDERS = ("openai", "deepseek", "anthropic", "mock")

# ---------------------------------------------------------------------------
# 系统提示词（硬约束）
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a CAISO Trading Decision Copilot. You do NOT decide trade direction. "
    "The final recommendation is generated exclusively by Predictive Model + Risk Gate + Rule Engine.\n"
    "\n"
    "HARD CONSTRAINTS\n"
    "1. You explain and summarize; you never decide BUY/SELL/NO_TRADE. The final recommendation "
    "BUY_DA / SELL_DA / NO_TRADE is computed by the system (get_decision tool). Never propose, "
    "change, or argue for a different final recommendation.\n"
    "2. All numbers must come verbatim from tool results. Never invent prices, probabilities, PnL, "
    "events, or news. If a number is not present in the tool results, you must not state it.\n"
    "3. Never expose or use actual DA/RTPD/PnL for a decision whose outcome has NOT been revealed "
    "(get_post_trade_review returns status=OUTCOME_NOT_REVEALED). Treat such data as unavailable.\n"
    "4. Never claim causality without evidence from the tools "
    "(get_evidence / get_feature_explanation / get_similar_cases).\n"
    "5. Never use future outcomes that were not available at decision time. Respect decision_cutoff "
    "and the available_at / case_available_at fields.\n"
    "6. Only use tools to obtain data. Never fabricate tool outputs.\n"
    "7. If the tools return insufficient evidence to answer, output exactly: "
    "UNCERTAIN / INSUFFICIENT EVIDENCE\n"
    "\n"
    "ALLOWED ACTIONS: call the provided tools, summarize structured evidence, explain the decision, "
    "compare historical cases, explain data provenance, generate post-trade review summaries "
    "(only when the outcome is revealed).\n"
    "\n"
    "DECISION SEMANTICS (for explanation only):\n"
    "  Return = DA - RTPD per hour. Return > 0 -> SELL_DA (virtual supply); "
    "Return < 0 -> BUY_DA (virtual demand); weak/uncertain -> NO_TRADE.\n"
    "  PnL per 1 MWh: BUY_DA = RTPD - DA = -Return; SELL_DA = DA - RTPD = +Return; NO_TRADE = 0.\n"
    "  decision_cutoff = decision day D 10:00 PT (DAM bid cutoff). All features are as-of that moment.\n"
    "\n"
    "Answer in the language of the user's question (Chinese preferred if asked in Chinese). "
    "Keep the answer concise, structured, and grounded only in tool results."
)

ROUTER_INSTRUCTIONS = (
    "\n\nOUTPUT FORMAT (MANDATORY, JSON TOOL ROUTER MODE):\n"
    "When you need data, respond with ONLY a JSON object (no markdown, no commentary):\n"
    '{"tool": "<tool_name>", "arguments": {...}}\n'
    "You may call at most one tool per response. After the tool result is provided, respond with "
    "the final answer as plain text. Never invent tool output — wait for the actual tool result.\n"
)

LLM_NOT_CONFIGURED_MSG = (
    "LLM NOT CONFIGURED\n"
    "未配置 LLM（LLM_API_KEY / LLM_PROVIDER / LLM_MODEL 环境变量缺失），Ask Agent 已降级："
    "工具路由仍可执行，下方为结构化工具结果摘要（非 LLM 解释）。配置环境变量后即可启用解释。"
)

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _json_safe(value: Any) -> Any:
    """递归规约 numpy / NaN / ±Inf / Timestamp 为可 JSON 化的值。"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if (value != value or math.isinf(value)) else value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        import numpy as np  # noqa: F401
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            f = float(value)
            return None if (f != f or math.isinf(f)) else f
        if isinstance(value, np.ndarray):
            return [_json_safe(v) for v in value.tolist()]
        if isinstance(value, np.bool_):
            return bool(value)
    except Exception:
        pass
    try:
        import pandas as pd  # noqa: F401
        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        pass
    return str(value)


def _env_bool(v: Any, default: Optional[bool] = None) -> Optional[bool]:
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _http_post(url: str, headers: Dict[str, str], payload: Dict[str, Any],
               timeout: float):
    """httpx 优先，requests 兜底。返回 (status_code, text)。"""
    try:
        import httpx
        resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        return resp.status_code, resp.text
    except Exception:
        import requests
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        return resp.status_code, resp.text


def _merge_roles(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并相邻同 role 消息（满足 Anthropic 交替 role 约束）。"""
    out: List[Dict[str, Any]] = []
    for m in messages:
        if out and out[-1]["role"] == m.get("role"):
            prev, cur = out[-1], m
            if isinstance(prev.get("content"), list) and isinstance(cur.get("content"), list):
                prev["content"] = prev["content"] + cur["content"]
            else:
                prev["content"] = str(prev.get("content", "")) + "\n\n" + str(cur.get("content", ""))
        else:
            out.append(dict(m))
    return out


# ---------------------------------------------------------------------------
# 预置问题路由（确定性：自动选 Tool，不依赖 LLM）
# ---------------------------------------------------------------------------
_PRESET_ROUTES: List[tuple] = [
    ("get_post_trade_review", [
        "赚了吗", "盈利吗", "亏了吗", "复盘", "为什么错", "为什么亏", "赚没赚",
        "结果如何", "did we profit", "how did it go", "why wrong", "pnl", "赚了", "赔了",
    ]),
    ("get_feature_explanation", [
        "最重要的特征", "主要特征", "特征贡献", "top feature", "most important",
        "feature", "为什么涨", "为什么跌",
    ]),
    ("get_similar_cases", [
        "类似案例", "相似案例", "历史案例", "类似", "相似", "similar case", "analogous",
    ]),
    ("get_data_provenance", [
        "天气来源", "数据来源", "来源", "原始文件", "raw file", "provenance", "10点",
        "10:00", "可见", "available", "数据可用", "数据是否", "cutoff", "as of",
    ]),
    ("get_evidence", [
        "证据", "外部事件", "evidence", "新闻事件", "极端天气事件",
    ]),
    ("get_decision", [
        "为什么不卖", "为什么不买", "为什么卖", "为什么买", "为什么", "no trade",
        "no_trade", "观望", "不交易", "决策", "解释", "说明", "理由", "原因",
        "recommendation", "why", "explain", "决策依据",
    ]),
]


def _match_preset(question: str) -> Optional[str]:
    q = str(question or "").lower()
    for tool, terms in _PRESET_ROUTES:
        for term in terms:
            if term.lower() in q:
                return tool
    return None


# ---------------------------------------------------------------------------
# LLM Client 抽象
# ---------------------------------------------------------------------------
class LlmClient:
    """LLM 客户端抽象：统一 chat / chat_with_tools，屏蔽 provider 差异。"""

    provider: str = ""
    model: str = ""
    supports_native_tools: bool = False

    def chat(self, messages: List[Dict[str, Any]]) -> str:
        raise NotImplementedError

    def chat_with_tools(self, messages: List[Dict[str, Any]],
                        tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        """返回 {"text": str, "tool_calls": [{"id","tool","arguments"}]}。"""
        raise NotImplementedError


class OpenAICompatClient(LlmClient):
    """OpenAI / DeepSeek / 任何 OpenAI 兼容端点。SDK 可用则用 SDK，否则直连 REST。"""

    provider = "openai"
    supports_native_tools = True

    def __init__(self, *, provider: str, api_key: str, model: str,
                 base_url: str, timeout: float):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": "Bearer " + self.api_key,
                   "Content-Type": "application/json"}
        status, text = _http_post(self.base_url + "/chat/completions",
                                  headers, payload, self.timeout)
        try:
            data = json.loads(text)
        except Exception:
            raise RuntimeError(f"LLM 响应非 JSON (HTTP {status}): {text[:300]}")
        if status >= 400:
            raise RuntimeError(f"LLM API 错误 (HTTP {status}): {text[:500]}")
        return data

    def _convert(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for m in messages:
            role = m.get("role", "")
            if role == "assistant" and m.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": m.get("content", ""),
                    "tool_calls": [
                        {"id": tc.get("id", f"call_{i}"), "type": "function",
                         "function": {"name": tc.get("tool", ""),
                                      "arguments": json.dumps(tc.get("arguments", {}),
                                                              ensure_ascii=False)}}
                        for i, tc in enumerate(m["tool_calls"])
                    ],
                })
            elif role == "tool":
                out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""),
                            "content": str(m.get("content", ""))})
            else:
                out.append({"role": role, "content": str(m.get("content", ""))})
        return out

    def chat(self, messages: List[Dict[str, Any]]) -> str:
        payload = {"model": self.model, "temperature": 0,
                   "messages": self._convert(messages)}
        data = self._post(payload)
        return data["choices"][0]["message"].get("content") or ""

    def chat_with_tools(self, messages: List[Dict[str, Any]],
                        tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = {"model": self.model, "temperature": 0,
                   "messages": self._convert(messages)}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "parameters": t["function"]["parameters"],
                }} for t in tools
            ]
            payload["tool_choice"] = "auto"
        data = self._post(payload)
        msg = data["choices"][0]["message"]
        text = msg.get("content") or ""
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            tool_calls.append({"id": tc.get("id"), "tool": fn.get("name"), "arguments": args})
        return {"text": text, "tool_calls": tool_calls}


class AnthropicClient(LlmClient):
    """Anthropic Messages API。SDK 可用则用 SDK，否则直连 REST。"""

    provider = "anthropic"
    supports_native_tools = True

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float,
                 max_tokens: int = 1024):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                   "Content-Type": "application/json"}
        status, text = _http_post(self.base_url + "/v1/messages",
                                  headers, payload, self.timeout)
        try:
            data = json.loads(text)
        except Exception:
            raise RuntimeError(f"LLM 响应非 JSON (HTTP {status}): {text[:300]}")
        if status >= 400:
            raise RuntimeError(f"LLM API 错误 (HTTP {status}): {text[:500]}")
        return data

    def _convert(self, messages: List[Dict[str, Any]]):
        system_parts: List[str] = []
        out: List[Dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                system_parts.append(str(content))
                continue
            if role == "assistant":
                tcs = m.get("tool_calls")
                if tcs:
                    blocks = []
                    if content:
                        blocks.append({"type": "text", "text": str(content)})
                    for tc in tcs:
                        blocks.append({"type": "tool_use", "id": tc.get("id"),
                                       "name": tc.get("tool", ""),
                                       "input": tc.get("arguments", {})})
                    out.append({"role": "assistant", "content": blocks})
                else:
                    out.append({"role": "assistant", "content": str(content)})
            elif role == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                     "content": str(content)}]})
            else:
                out.append({"role": "user", "content": str(content)})
        return "\n\n".join(system_parts), _merge_roles(out)

    def chat(self, messages: List[Dict[str, Any]]) -> str:
        system, msgs = self._convert(messages)
        payload = {"model": self.model, "max_tokens": self.max_tokens, "messages": msgs}
        if system:
            payload["system"] = system
        data = self._post(payload)
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")

    def chat_with_tools(self, messages: List[Dict[str, Any]],
                        tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        system, msgs = self._convert(messages)
        payload = {"model": self.model, "max_tokens": self.max_tokens, "messages": msgs}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t["function"]["name"],
                 "description": t["function"]["description"],
                 "input_schema": t["function"]["parameters"]} for t in tools
            ]
        data = self._post(payload)
        text_parts, tool_calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({"id": block.get("id"),
                                   "tool": block.get("name"),
                                   "arguments": block.get("input") or {}})
        return {"text": "".join(text_parts), "tool_calls": tool_calls}


def _compact_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """从工具结果抽取演示用摘要（非 LLM 决策，仅展示）。"""
    out: Dict[str, Any] = {}
    if not isinstance(result, dict):
        return out
    for k in ("tool", "status", "decision_id"):
        if result.get(k) is not None:
            out[k] = result[k]
    if result.get("final_recommendation"):
        out["final_recommendation"] = result["final_recommendation"]
    if result.get("reason_codes"):
        out["reason_codes"] = result["reason_codes"]
    mo = result.get("model_output") or {}
    for k in ("expected_return", "direction", "model_signal_strength", "uncertainty"):
        if mo.get(k) is not None:
            out[k] = mo[k]
    if "cases" in result:
        out["n_cases"] = len(result["cases"])
        if result["cases"]:
            c = dict(result["cases"][0])
            out["first_case"] = {k: c.get(k) for k in
                                 ("case_id", "decision_date", "decision", "PnL", "lesson")
                                 if c.get(k) is not None}
    if "provenance" in result:
        p = result["provenance"]
        if isinstance(p, dict):
            out["provenance"] = {k: p.get(k) for k in
                                 ("feature", "source", "raw_file", "source_type",
                                  "available_at_display", "is_mock")
                                 if p.get(k) is not None}
        elif p:
            out["provenance"] = p[0]
    if result.get("status") == "OUTCOME_NOT_REVEALED":
        out["note"] = result.get("message", "")
    return out


class MockLlmClient(LlmClient):
    """离线 Mock LLM（demo / 测试用，不真实调用 API）。

    提供 responder 则每轮调用 responder(client, messages, tools) → dict：
      {"text": str}            或  {"tool_calls": [{"id","tool","arguments"}]}
    否则使用内置默认行为：读到 [TOOL_RESULT] 就返回紧凑摘要，否则直接返回简短文本。
    """

    provider = "mock"
    supports_native_tools = True

    def __init__(self, model: str = "mock-model",
                 responder: Optional[Callable] = None):
        self.model = model
        self.responder = responder
        self.calls: List[Dict[str, Any]] = []   # 每次调用的消息快照（供测试断言）

    def _snap(self, messages, tools):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})

    @staticmethod
    def _last_tool_result(messages) -> Optional[Dict[str, Any]]:
        for m in reversed(messages):
            content = str(m.get("content", ""))
            idx = content.rfind("[TOOL_RESULT]")
            if idx < 0:
                continue
            tail = content[idx + len("[TOOL_RESULT]"):]
            start = tail.find("{")
            if start < 0:
                continue
            try:
                obj, _ = json.JSONDecoder().raw_decode(tail[start:])
                return obj
            except Exception:
                continue
        return None

    def _default_answer(self, messages) -> str:
        result = self._last_tool_result(messages)
        if result:
            return ("（mock 演示回答，非真实 LLM）依据工具结果：" +
                    json.dumps(_compact_summary(result), ensure_ascii=False))
        return "（mock 演示回答，非真实 LLM）已收到问题，未调用工具。"

    def chat(self, messages: List[Dict[str, Any]]) -> str:
        self._snap(messages, None)
        if self.responder is not None:
            r = self.responder(self, messages, None)
            return r.get("text", "") if isinstance(r, dict) else str(r)
        return self._default_answer(messages)

    def chat_with_tools(self, messages: List[Dict[str, Any]],
                        tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._snap(messages, tools)
        if self.responder is not None:
            r = self.responder(self, messages, tools)
            return {"text": r.get("text", ""), "tool_calls": r.get("tool_calls", [])}
        return {"text": self._default_answer(messages), "tool_calls": []}


# ---------------------------------------------------------------------------
# 数字 / 方向完整性守卫（程序化兜底：LLM 不能覆盖 Tool 数字）
# ---------------------------------------------------------------------------
_NUM_FIELD_PATTERNS = [
    ("expected_return", r"expected[_\s-]?return"),
    ("prob_positive", r"prob[_\s-]?positive"),
    ("prob_negative", r"prob[_\s-]?negative"),
    ("direction_probability", r"direction[_\s-]?probability"),
    ("model_signal_strength", r"model[_\s-]?signal[_\s-]?strength"),
    ("uncertainty", r"uncertainty"),
    ("actual_da", r"actual[_\s-]?da\b"),
    ("actual_rtpd", r"actual[_\s-]?rtpd\b"),
    ("actual_return", r"actual[_\s-]?return"),
    ("pnl", r"\bpnl\b"),
    ("model_prediction_error", r"model[_\s-]?prediction[_\s-]?error"),
]
_NUM_PATTERN_BY_LABEL = {re.sub(r"[^a-z0-9]", "", label): pat
                         for label, pat in _NUM_FIELD_PATTERNS}

_REC_PATTERNS = [
    r"final\s+recommendation\s+is\s+(BUY_DA|SELL_DA|NO_TRADE)",
    r"final\s+recommendation\s*[:：]\s*(BUY_DA|SELL_DA|NO_TRADE)",
    r"最终建议\s*[:：为是]?\s*(BUY_DA|SELL_DA|NO_TRADE)",
]


def _norm_key(k) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-6, 0.05 * max(abs(a), abs(b)))


def _collect_authoritative_numbers(tools_called, out: Dict[str, float]):
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                nk = _norm_key(k)
                if nk in _NUM_PATTERN_BY_LABEL and isinstance(v, (int, float)) \
                        and not isinstance(v, bool):
                    out[nk] = float(v)
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)
    for r in tools_called:
        walk(r.get("result", {}))
    return out


def _assert_answer_integrity(answer: str, tools_called) -> tuple:
    """校验 LLM 最终答案与工具结果一致（数字 + 最终方向）。返回 (ok, detail)。"""
    authoritative = _collect_authoritative_numbers(tools_called, {})
    for nk, truth in authoritative.items():
        pattern = _NUM_PATTERN_BY_LABEL[nk]
        rx = re.compile(pattern + r"\s*[:：=]?\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
        for m in rx.findall(str(answer)):
            try:
                claimed = float(m)
            except ValueError:
                continue
            if not _close(claimed, truth):
                return False, f"数字 {nk}: 答案声称 {claimed}，工具结果 {truth}"
    # 方向完整性：答案声称的 final recommendation 不得与工具结果冲突
    truth_dirs = set()
    for r in tools_called:
        res = r.get("result", {})
        if isinstance(res, dict) and res.get("final_recommendation"):
            truth_dirs.add(str(res["final_recommendation"]))
    if truth_dirs:
        for pat in _REC_PATTERNS:
            for m in re.findall(pat, str(answer), re.IGNORECASE):
                claimed = m.upper()
                if claimed not in truth_dirs:
                    return False, f"方向 {claimed}: 工具结果为 {sorted(truth_dirs)}"
    return True, ""


# ---------------------------------------------------------------------------
# LLMCopilot
# ---------------------------------------------------------------------------
_PROVIDER_DEFAULTS = {
    "openai": {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"},
    "deepseek": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com"},
    "anthropic": {"model": "claude-3-5-sonnet-20241022", "base_url": "https://api.anthropic.com"},
    "mock": {"model": "mock-model", "base_url": ""},
}

_SUMMARY_LIMIT = 4000


def _summary_of(result: Any) -> str:
    s = json.dumps(result, ensure_ascii=False)
    if len(s) > _SUMMARY_LIMIT:
        s = s[:_SUMMARY_LIMIT] + "…[truncated]"
    return s


class LLMCopilot:
    """Trading Decision Copilot：LLM 只解释，不决策。所有数字来自 Tool。"""

    def __init__(self, service=None, *, provider: Optional[str] = None,
                 api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: Optional[str] = None, use_router: Optional[bool] = None,
                 max_tool_rounds: Optional[int] = None, timeout: Optional[float] = None,
                 llm_client: Optional[LlmClient] = None, env: Optional[Dict[str, str]] = None):
        self.service = service if service is not None else default_service()
        cfg = dict(env) if env else os.environ

        self.provider = (provider or cfg.get("LLM_PROVIDER", "") or "").strip().lower()
        if not self.provider:
            self.provider = "openai"
        self.api_key = (api_key if api_key is not None
                        else cfg.get("LLM_API_KEY", "").strip() or None)
        defaults = _PROVIDER_DEFAULTS.get(self.provider, _PROVIDER_DEFAULTS["openai"])
        self.model = (model or cfg.get("LLM_MODEL", "") or defaults["model"]).strip()
        self.base_url = (base_url or cfg.get("LLM_BASE_URL", "") or defaults["base_url"]).strip()
        self.use_router = _env_bool(cfg.get("LLM_USE_ROUTER"), use_router) or False
        self.max_tool_rounds = int(max_tool_rounds or cfg.get("LLM_MAX_TOOL_ROUNDS", 4))
        self.timeout = float(timeout or cfg.get("LLM_TIMEOUT", 30))

        if llm_client is not None:
            self.client = llm_client
            self.configured = True
        else:
            self.configured = self._is_configured()
            self.client = self._build_client() if self.configured else None

    # ------------------------------------------------------------- 配置
    def _is_configured(self) -> bool:
        if self.provider == "mock":
            return True
        if self.provider not in SUPPORTED_PROVIDERS:
            return False
        return bool(self.api_key) and bool(self.model)

    def _build_client(self) -> Optional[LlmClient]:
        if self.provider == "mock":
            return MockLlmClient(model=self.model)
        if self.provider in ("openai", "deepseek"):
            return OpenAICompatClient(provider=self.provider, api_key=self.api_key,
                                      model=self.model, base_url=self.base_url,
                                      timeout=self.timeout)
        if self.provider == "anthropic":
            return AnthropicClient(api_key=self.api_key, model=self.model,
                                   base_url=self.base_url, timeout=self.timeout)
        return None

    @property
    def degraded(self) -> bool:
        return self.client is None

    # ------------------------------------------------------------- Tool
    def _llm_tool_definitions(self) -> List[Dict[str, Any]]:
        defs = []
        for name, schema in TOOL_SCHEMAS.items():
            defs.append({"type": "function", "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            }})
        return defs

    def _validate_args(self, tool: str, args: Dict[str, Any]) -> List[str]:
        schema = TOOL_SCHEMAS[tool]["parameters"]
        required = schema.get("required", [])
        return [r for r in required if args.get(r) in (None, "")]

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        args = dict(args or {})
        if name not in TOOL_SCHEMAS:
            return {"status": "error", "tool": name, "message": f"未知工具 {name!r}"}
        method = getattr(self.service, name, None)
        if method is None:
            return {"status": "error", "tool": name,
                    "message": f"DecisionService 缺少工具方法 {name!r}"}
        try:
            result = method(**args)
        except TypeError as exc:
            missing = self._validate_args(name, args)
            return {"status": "error", "tool": name, "args": args,
                    "message": f"参数错误（缺少: {missing}）: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "tool": name, "args": args, "message": str(exc)}
        return _json_safe(result)

    # ------------------------------------------------------------- 上下文解析
    def _decision_params(self, context: Optional[Dict[str, Any]]):
        c = context or {}
        return (c.get("decision_date") or c.get("dd"),
                c.get("node"),
                c.get("hour"))

    def _ctx_for(self, decision_id: str) -> Optional[Dict[str, Any]]:
        try:
            for d in self.service.list_decisions():
                if d.get("decision_id") == decision_id:
                    return d.get("context", {})
        except Exception:
            pass
        return None

    def _latest_decision_id(self) -> Optional[str]:
        try:
            lst = self.service.list_decisions()
            if lst:
                return lst[-1]["decision_id"]
        except Exception:
            pass
        return None

    def _decision_args(self, dd, node, hour, decision_id) -> Optional[Dict[str, Any]]:
        if dd and node and hour is not None:
            return {"decision_date": str(dd)[:10], "node": node, "hour": int(hour)}
        if decision_id:
            ctx = self._ctx_for(decision_id)
            if ctx:
                return {"decision_date": str(ctx.get("decision_date", ""))[:10],
                        "node": ctx.get("node"),
                        "hour": int(ctx.get("hour", -1) or -1)}
        return None

    def _build_plan_for_route(self, tool: str, decision_id: Optional[str],
                              context: Optional[Dict[str, Any]]):
        """预置路由 → 确定性的 Tool 调用计划。返回 list | "INSUFFICIENT"。"""
        dd, node, hour = self._decision_params(context)
        if tool == "get_decision":
            args = self._decision_args(dd, node, hour, decision_id)
            if args is None:
                return "INSUFFICIENT"
            return [{"tool": "get_decision", "args": args}]
        # decision_id 型工具
        if decision_id:
            return [{"tool": tool, "args": {"decision_id": decision_id}}]
        if dd and node and hour is not None:
            return [{"tool": "get_decision",
                     "args": {"decision_date": str(dd)[:10], "node": node, "hour": int(hour)}},
                    {"tool": tool, "args": {"decision_id": "@latest"}}]
        did = self._latest_decision_id()
        if did:
            return [{"tool": tool, "args": {"decision_id": did}}]
        return "INSUFFICIENT"

    def _execute_plan(self, plan: List[Dict[str, Any]], question: str,
                      route: str):
        steps = [{"stage": "route", "selected_tool": route,
                  "reason": f"preset 路由命中: {route}"}]
        tools_called = []
        for i, item in enumerate(plan):
            tool = item["tool"]
            args = dict(item["args"])
            if args.get("decision_id") == "@latest":
                args["decision_id"] = self._latest_decision_id()
                if not args["decision_id"]:
                    return tools_called, steps + [{"stage": "tool", "tool": tool,
                                                   "arguments": args,
                                                   "result_summary": '{"status":"error","message":"无法解析 decision_id"}',
                                                   "status": "error"}]
            result = self._execute_tool(tool, args)
            summary = _summary_of(result)
            tools_called.append({"tool": tool, "args": args, "result": result,
                                 "result_summary": summary})
            steps.append({"stage": "tool", "tool": tool, "arguments": args,
                          "result_summary": summary,
                          "status": result.get("status", "ok")})
        return tools_called, steps

    # ------------------------------------------------------------- 消息组装
    def _build_user_message(self, question: str, decision_id: Optional[str],
                            context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        parts = [question]
        meta: Dict[str, Any] = {}
        if decision_id:
            meta["decision_id"] = decision_id
        c = context or {}
        for k in ("decision_date", "dd", "node", "hour", "target_date", "zone"):
            if c.get(k) is not None:
                meta[k] = c[k]
        if meta:
            parts.append("")
            parts.append("COPILOT CONTEXT (machine-readable): " +
                         json.dumps(meta, ensure_ascii=False))
        return {"role": "user", "content": "\n".join(parts)}

    def _build_messages(self, question: str, decision_id: Optional[str],
                        context: Optional[Dict[str, Any]], tools_called) -> List[Dict[str, Any]]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append(self._build_user_message(question, decision_id, context))
        if tools_called:
            for tc in tools_called:
                messages.append({"role": "user",
                                 "content": "[TOOL_RESULT] " + tc["tool"] + "\n" +
                                            json.dumps(tc["result"], ensure_ascii=False)})
            messages.append({"role": "user",
                             "content": "Use ONLY the tool results above to answer the original "
                                        "question. Do not invent or modify any number. "
                                        "Do not change the final recommendation."})
        return _merge_roles(messages)

    def _call_final_answer(self, messages, trace_steps, mode: str) -> str:
        text = self.client.chat(messages)
        trace_steps.append({"stage": "llm", "provider": self.client.provider,
                            "model": self.client.model, "mode": mode,
                            "final_answer_excerpt": str(text)[:300]})
        return str(text)

    # ------------------------------------------------------------- Agent 循环
    def _agent_loop_native(self, question, decision_id, context):
        tools = self._llm_tool_definitions()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append(self._build_user_message(question, decision_id, context))
        tools_called, steps = [], []
        for rnd in range(self.max_tool_rounds):
            resp = self.client.chat_with_tools(messages, tools)
            tcs = resp.get("tool_calls") or []
            if not tcs:
                text = str(resp.get("text", ""))
                steps.append({"stage": "llm", "provider": self.client.provider,
                              "model": self.client.model, "mode": "native",
                              "final_answer_excerpt": text[:300]})
                return text, tools_called, steps
            assoc = {"role": "assistant", "content": str(resp.get("text", "")),
                     "tool_calls": [
                         {"id": tc.get("id") or f"call_{rnd}_{i}",
                          "tool": tc["tool"], "arguments": tc["arguments"]}
                         for i, tc in enumerate(tcs)]}
            messages.append(assoc)
            for i, tc in enumerate(tcs):
                result = self._execute_tool(tc["tool"], tc["arguments"])
                summary = _summary_of(result)
                tools_called.append({"tool": tc["tool"], "args": tc["arguments"],
                                     "result": result, "result_summary": summary})
                messages.append({"role": "tool",
                                 "tool_call_id": assoc["tool_calls"][i]["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
                steps.append({"stage": "tool", "tool": tc["tool"],
                              "arguments": tc["arguments"], "result_summary": summary,
                              "status": result.get("status", "ok")})
        text = "UNCERTAIN / INSUFFICIENT EVIDENCE（达到最大工具调用轮数）"
        steps.append({"stage": "llm", "provider": self.client.provider,
                      "model": self.client.model, "mode": "native",
                      "final_answer_excerpt": text[:300]})
        return text, tools_called, steps

    def _router_tools_text(self) -> str:
        lines = ["Available tools (name: description; required params):"]
        for name, s in TOOL_SCHEMAS.items():
            req = s["parameters"].get("required", [])
            lines.append(f"- {name}: {s['description']} (required: {req})")
        return "\n".join(lines)

    def _parse_router_json(self, text: str) -> Optional[Dict[str, Any]]:
        t = str(text or "").strip()
        if t.startswith("```"):
            lines = t.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines).strip()
        obj = None
        try:
            obj = json.loads(t)
        except Exception:
            start, end = t.find("{"), t.rfind("}")
            if start >= 0 and end > start:
                try:
                    obj = json.loads(t[start:end + 1])
                except Exception:
                    return None
            else:
                return None
        if isinstance(obj, dict) and obj.get("tool") in TOOL_SCHEMAS:
            args = obj.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            return {"tool": obj["tool"], "arguments": args}
        return None

    def _agent_loop_router(self, question, decision_id, context):
        messages = [{"role": "system",
                     "content": SYSTEM_PROMPT + ROUTER_INSTRUCTIONS +
                                "\n\n" + self._router_tools_text()}]
        messages.append(self._build_user_message(question, decision_id, context))
        tools_called, steps = [], []
        for _ in range(self.max_tool_rounds):
            plan_text = self.client.chat(messages)
            plan = self._parse_router_json(plan_text)
            if plan is None:
                steps.append({"stage": "llm", "provider": self.client.provider,
                              "model": self.client.model, "mode": "router",
                              "final_answer_excerpt": str(plan_text)[:300]})
                return str(plan_text), tools_called, steps
            result = self._execute_tool(plan["tool"], plan["arguments"])
            summary = _summary_of(result)
            tools_called.append({"tool": plan["tool"], "args": plan["arguments"],
                                 "result": result, "result_summary": summary})
            steps.append({"stage": "tool", "tool": plan["tool"],
                          "arguments": plan["arguments"], "result_summary": summary,
                          "status": result.get("status", "ok")})
            messages.append({"role": "user",
                             "content": "[TOOL_RESULT]\n" +
                                        json.dumps(result, ensure_ascii=False) +
                                        "\n\nNow write the final answer as plain text "
                                        "using ONLY the tool result."})
            final_text = str(self.client.chat(messages))
            steps.append({"stage": "llm", "provider": self.client.provider,
                          "model": self.client.model, "mode": "router",
                          "final_answer_excerpt": final_text[:300]})
            return final_text, tools_called, steps
        text = "UNCERTAIN / INSUFFICIENT EVIDENCE（路由达到最大轮数）"
        steps.append({"stage": "llm", "provider": self.client.provider,
                      "model": self.client.model, "mode": "router",
                      "final_answer_excerpt": text[:300]})
        return text, tools_called, steps

    # ------------------------------------------------------------- Ask 主入口
    def ask(self, question: str, decision_id: Optional[str] = None,
            context: Optional[Dict[str, Any]] = None, trace: bool = True) -> Dict[str, Any]:
        question = str(question or "").strip()
        trace_steps: List[Dict[str, Any]] = []
        tools_called = []

        if not question:
            return self._response(
                "UNCERTAIN / INSUFFICIENT EVIDENCE（空问题）", [], question,
                trace, trace_steps, "insufficient_evidence", "preset")

        route = _match_preset(question)
        if route is not None:
            plan = self._build_plan_for_route(route, decision_id, context)
            if plan == "INSUFFICIENT":
                return self._response(
                    "UNCERTAIN / INSUFFICIENT EVIDENCE（无法确定决策对象：请提供 decision_id "
                    "或 decision_date/node/hour，或先运行一次决策）",
                    [], question, trace, trace_steps, "insufficient_evidence", "preset")
            tools_called, steps = self._execute_plan(plan, question, route)
            trace_steps.extend(steps)
            mode = "preset"
            if self.client is None:
                answer = LLM_NOT_CONFIGURED_MSG
                trace_steps.append({"stage": "llm", "status": "SKIPPED_DEGRADED",
                                    "reason": "LLM NOT CONFIGURED"})
                status = "degraded"
            else:
                messages = self._build_messages(question, decision_id, context, tools_called)
                answer = self._call_final_answer(messages, trace_steps, mode)
                status = "ok"
        else:
            if self.client is None:
                mode = "degraded"
                answer = LLM_NOT_CONFIGURED_MSG
                trace_steps.append({"stage": "llm", "status": "SKIPPED_DEGRADED",
                                    "reason": "LLM NOT CONFIGURED"})
                status = "degraded"
            else:
                use_router = self.use_router or (not self.client.supports_native_tools)
                mode = "router" if use_router else "native"
                if use_router:
                    answer, tools_called, steps = self._agent_loop_router(
                        question, decision_id, context)
                else:
                    answer, tools_called, steps = self._agent_loop_native(
                        question, decision_id, context)
                trace_steps.extend(steps)
                status = "ok"

        # 程序化完整性守卫：LLM 不得覆盖 Tool 数字 / 最终方向
        if self.client is not None and tools_called and status == "ok":
            ok, detail = _assert_answer_integrity(answer, tools_called)
            if ok:
                trace_steps.append({"stage": "guard", "status": "PASS"})
            else:
                answer = ("UNCERTAIN / INSUFFICIENT EVIDENCE — 数字/方向完整性校验未通过"
                          f"（{detail}；已拦截，工具数字不可被 LLM 覆盖）。")
                status = "blocked"
                trace_steps.append({"stage": "guard", "status": "BLOCKED", "detail": detail})

        return self._response(answer, tools_called, question, trace, trace_steps, status, mode)

    def _response(self, answer: str, tools_called, question: str, trace_enabled: bool,
                  trace_steps: List[Dict[str, Any]], status: str,
                  mode: str) -> Dict[str, Any]:
        trace_obj = None
        if trace_enabled:
            numbered = []
            for i, s in enumerate(trace_steps):
                s = dict(s)
                s["step"] = i + 1
                numbered.append(s)
            trace_obj = {
                "user_question": question,
                "mode": mode,
                "degraded": self.degraded,
                "provider": self.client.provider if self.client else self.provider,
                "model": self.client.model if self.client else self.model,
                "steps": numbered,
            }
        return {
            "answer": answer,
            "status": status,
            "provider": self.client.provider if self.client else self.provider,
            "model": self.client.model if self.client else self.model,
            "degraded": self.degraded,
            "tools_called": [
                {"tool": t["tool"], "args": t["args"], "result_summary": t["result_summary"]}
                for t in tools_called
            ],
            "trace": trace_obj,
        }


# ---------------------------------------------------------------------------
# 模块级便捷函数（供 Agent E 的 Web Ask Agent 面板调用）
# ---------------------------------------------------------------------------
_default_copilot: Optional[LLMCopilot] = None


def make_copilot(service=None, **kw) -> LLMCopilot:
    return LLMCopilot(service=service, **kw)


def default_copilot(service=None, **kw) -> LLMCopilot:
    global _default_copilot
    if _default_copilot is None:
        _default_copilot = LLMCopilot(service=service, **kw)
    return _default_copilot


def ask(question: str, decision_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None, trace: bool = True,
        service=None, **kw) -> Dict[str, Any]:
    """Ask Agent 面板主入口。kwargs 空时复用缓存的默认 copilot。"""
    if not kw:
        cp = default_copilot(service=service)
    else:
        cp = make_copilot(service=service, **kw)
    return cp.ask(question, decision_id=decision_id, context=context, trace=trace)


def copilot_status(service=None, **kw) -> Dict[str, Any]:
    # 无定制参数时复用缓存的默认 copilot，避免每次重载数据文件
    if not kw and service is None:
        cp = default_copilot()
    else:
        cp = make_copilot(service=service, **kw)
    return {
        "configured": cp.configured,
        "degraded": cp.degraded,
        "provider": cp.provider,
        "model": cp.model,
        "base_url": cp.base_url,
        "native_tool_calling": bool(cp.client is not None and cp.client.supports_native_tools
                                    and not cp.use_router),
        "mode": ("router" if (cp.use_router or (cp.client is not None and not cp.client.supports_native_tools))
                 else ("native" if cp.client is not None else "degraded")),
        "tools": sorted(TOOL_SCHEMAS.keys()),
        "system_prompt_constraints": {
            "llm_decides_trade": False,
            "numbers_from_tools": True,
            "actual_leak_before_reveal": False,
            "evidence_insufficient_rule": "UNCERTAIN / INSUFFICIENT EVIDENCE",
        },
    }


# ---------------------------------------------------------------------------
# 自检演示
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== LLM Copilot 自检（无 API Key 降级）===")
    cp = LLMCopilot(env={})  # env={} 确保不读真实环境变量 → 降级
    print(json.dumps(copilot_status(env={}), ensure_ascii=False, indent=2))
    out = cp.ask("为什么不卖", context={"decision_date": "2026-07-08",
                                        "node": "CONTROLX_1_N001", "hour": 2},
                 trace=True)
    print("\nanswer:", out["answer"][:120])
    print("status:", out["status"], "| degraded:", out["degraded"])
    print("tools_called:", [t["tool"] for t in out["tools_called"]])
    print("\n=== mock 演示（有“key”，离线确定性）===")
    cp2 = LLMCopilot(provider="mock", model="mock-model", env={})
    out2 = cp2.ask("最重要的特征", context={"decision_date": "2026-07-08",
                                             "node": "CONTROLX_1_N001", "hour": 2},
                   trace=True)
    print("status:", out2["status"], "| tools:", [t["tool"] for t in out2["tools_called"]])
    print("answer:", out2["answer"][:160])
