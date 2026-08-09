# -*- coding: utf-8 -*-
"""
code/agent_memory.py —— Session Memory Layer（Agent C · 会话记忆层，V0.4.1）
=============================================================================

纯后端独立模块，为 mvp_web / llm_copilot 提供**会话记忆**：对话上下文持久化、
阈值压缩（rolling_summary）与决策隔离。不依赖 decision_service / llm_copilot
（零重依赖，仅标准库），接入方只需：

    from code.agent_memory import get_session_manager, SessionManager

    mgr = get_session_manager()                    # 模块级单例
    conv = mgr.get_or_create(decision_id, title)   # 新 decision_id → 默认新会话
    mgr.append_message(conv["conversation_id"], {"role": "user", "content": q})
    ctx = mgr.build_context(mgr.get(conv["conversation_id"]))   # 注入 llm_copilot

铁律（与 docs/agent_event_protocol.md 一致）：
  * Memory 只作对话上下文，不覆盖 Tool / DecisionSnapshot 事实。
  * 回答当前交易问题必须 Tool（get_decision 等 6 个工具）为准。
  * 未 reveal 的决策：decision_context_summary 不得注入 actual DA/RTPD/PnL/outcome
    （Tool access control 优先）。build_context 即使 metadata 传入完整 snapshot，
    只要 outcome_revealed 不是 True 就强制剔除 outcome 字段（双保险）。
  * 摘要不得修改任何交易事实：rolling_summary 带"非权威事实"前缀，仅供对话衔接。

持久化：data/agent_sessions/<conversation_id>.json（UTF-8，无数据库，目录不存在则创建）。

数据结构（conversation 为纯 dict，可直接 JSON 序列化）：
  conversation = {
    conversation_id, decision_id, title, created_at, updated_at,
    rolling_summary, messages: [{id, role: user|assistant, content,
                                 trace?, status?, created_at}], metadata,
  }

阈值压缩（P3）：
  AGENT_MEMORY_MAX_MESSAGES = 12   # 消息条数超限触发
  AGENT_MEMORY_MAX_CHARS    = 8000 # 消息总字符超限触发
  AGENT_MEMORY_KEEP_RECENT  = 6    # 压缩后保留最近 6~8 条完整
  触发 compress(conversation)：先尝试 LLM 摘要回调（summary_callback），
  失败降级为确定性结构化摘要（最近用户主题 + 助手首句）；压缩后
  rolling_summary = 新摘要，删除被压缩旧消息，保留最近 6~8 条。

安全边界：
  * decision_context_summary 只在 outcome_revealed=True 时含 outcome；
  * 未 reveal 的会话记忆绝不注入当前交易的 unrevealed outcome。
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# 阈值常量（单一事实来源，禁止在业务代码散落 magic number）
# ---------------------------------------------------------------------------
AGENT_MEMORY_MAX_MESSAGES = 12      # 消息条数超限 → 触发压缩
AGENT_MEMORY_MAX_CHARS = 8000       # 消息内容总字符超限 → 触发压缩
AGENT_MEMORY_KEEP_RECENT = 6        # 压缩后 / build_context 保留最近完整消息数
AGENT_MEMORY_KEEP_RECENT_MIN = 6    # 保留最近消息下限（6~8 条区间）
AGENT_MEMORY_KEEP_RECENT_MAX = 8    # 保留最近消息上限（6~8 条区间）
AGENT_MEMORY_SUMMARY_MAX_CHARS = 2000  # rolling_summary 软上限（防止无限增长）

#: 摘要前缀：明确标注"非权威事实"，防止摘要被当成交易事实覆盖 Tool/DecisionSnapshot
SUMMARY_PREFIX = (
    "[会话记忆摘要 · 仅作对话上下文，非权威交易事实，不得覆盖 Tool/DecisionSnapshot]"
)

#: build_context.system_constraints（注入 llm_copilot 的对话上下文硬约束）
SYSTEM_CONSTRAINTS = (
    "[Session Memory Layer · 对话上下文约束]\n"
    "1. Memory 只作对话上下文；回答当前交易问题必须 Tool / DecisionSnapshot 事实为准，"
    "Memory 不得覆盖 Decision / Risk / Evidence / PnL / Final。\n"
    "2. 未 reveal 的决策不得注入 / 引用 actual DA/RTPD/PnL/outcome（Tool access control 优先）。\n"
    "3. LLM 不决定 BUY_DA / SELL_DA / NO_TRADE；最终建议由 Predictive Model + Risk Gate + "
    "Rule Engine 计算（get_decision 工具）。\n"
    "4. 所有数字必须来自工具结果，缺失即不得编造；不得修改工具返回的任何事实。\n"
    "5. rolling_summary / recent_messages 仅辅助对话衔接，不是权威交易事实。"
)

DEFAULT_STORE_SUBDIR = ("data", "agent_sessions")   # 相对仓库根目录


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """UTC ISO 毫秒时间戳（Z 后缀；固定宽度，可按字典序排序）。"""
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _sanitize_id(value) -> str:
    """会话 id 安全化：仅允许字母/数字/下划线/连字符，防路径穿越。"""
    s = re.sub(r"[^A-Za-z0-9_\-]", "_", str(value or "")).strip("_")
    if not s:
        raise ValueError("conversation_id 非法/为空")
    return s


def _new_message_id() -> str:
    return "MSG-" + uuid.uuid4().hex[:12]


def _truncate(text, n: int) -> str:
    text = str(text or "").strip()
    if len(text) <= n:
        return text
    return text[: max(0, n - 1)] + "…"


def _strip_newlines(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


_SENT_SPLIT = re.compile(r"[。！？!?\n]")


def _first_sentence(text, max_len: int = 100) -> str:
    text = str(text or "").strip()
    for piece in _SENT_SPLIT.split(text):
        piece = piece.strip()
        if piece:
            return _truncate(piece, max_len)
    return ""


def _normalize_message(msg=None, *, role: Optional[str] = None,
                       content: Optional[str] = None, **extra) -> Dict[str, Any]:
    """规约一条消息为 {id, role, content, created_at, trace?, status?}。

    兼容两种调用：append_message(cid, {"role":.., "content":..}) 与
    append_message(cid, role="user", content=..)。trace / status 为可选透传。
    """
    if isinstance(msg, dict):
        m = dict(msg)
        role = m.get("role", role)
        content = m.get("content", content)
        for k, v in m.items():
            if k not in ("role", "content") and v is not None:
                extra[k] = v
    elif isinstance(msg, str):
        content = msg
    elif msg is not None:
        raise TypeError(f"msg 必须为 dict/str，got {type(msg).__name__}")

    role = str(role or "").strip().lower()
    if role not in ("user", "assistant"):
        raise ValueError(f"消息 role 必须为 user/assistant，got {role!r}")
    out: Dict[str, Any] = {
        "id": str(extra.get("id") or _new_message_id()),
        "role": role,
        "content": str(content if content is not None else ""),
        "created_at": str(extra.get("created_at") or _now_iso()),
    }
    for k in ("trace", "status"):
        if extra.get(k) is not None:
            out[k] = extra[k]
    return out


def conversation_to_dict(conversation: Dict[str, Any]) -> Dict[str, Any]:
    """规约 conversation 为可 JSON 序列化的规范 dict（to_dict 便利入口）。"""
    return conversation_from_dict(conversation)


def conversation_from_dict(raw: Any) -> Dict[str, Any]:
    """从任意 dict / JSON 对象恢复规范 conversation（from_dict 便利入口）。

    缺省字段自动补默认值；非法消息行被跳过（不中断整个会话恢复）。
    """
    raw = dict(raw or {})
    now = _now_iso()
    conv: Dict[str, Any] = {
        "conversation_id": str(raw.get("conversation_id") or "").strip(),
        "decision_id": str(raw.get("decision_id") or "").strip(),
        "title": str(raw.get("title") or ""),
        "created_at": str(raw.get("created_at") or now),
        "updated_at": str(raw.get("updated_at") or raw.get("created_at") or now),
        "rolling_summary": str(raw.get("rolling_summary") or ""),
        "messages": [],
        "metadata": dict(raw.get("metadata") or {}),
    }
    for m in (raw.get("messages") or []):
        try:
            conv["messages"].append(_normalize_message(m))
        except (TypeError, ValueError):
            continue
    return conv


def decision_context_from_snapshot(snapshot: Any) -> Dict[str, Any]:
    """从 DecisionSnapshot（to_dict / as_jsonable 输出）提取最小决策上下文。

    供 mvp_web / llm_copilot 在 bind_decision 时生成 metadata.decision_context。
    **只在 snapshot.outcome_revealed=True 时透传 actual_* 字段**，否则置 None
    （Tool access control 优先；build_context 内部还会再校验一道）。
    """
    snap = dict(snapshot or {})
    ctx = dict(snap.get("context") or {})
    out: Dict[str, Any] = {
        "decision_date": str(ctx.get("decision_date") or "")[:10],
        "target_date": str(ctx.get("target_date") or "")[:10],
        "hour": ctx.get("hour"),
        "node": ctx.get("node", ""),
        "zone": ctx.get("zone", ""),
        "final_recommendation": snap.get("final_recommendation", ""),
        "outcome_revealed": bool(snap.get("outcome_revealed", False)),
    }
    if out["outcome_revealed"]:
        outcome = dict(snap.get("outcome") or {})
        for k in ("actual_da", "actual_rtpd", "actual_return", "pnl",
                  "model_prediction_error", "direction_correct"):
            if outcome.get(k) is not None:
                out[k] = outcome[k]
    else:
        out["outcome"] = None
    return out


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------
class SessionManager:
    """会话记忆层：JSON 持久化 + 阈值压缩 + 决策隔离 + 上下文构造。

    summary_callback（可选）：轻量 LLM 摘要回调，签名
        summary_callback(conversation, old_messages) -> str
    返回被压缩旧消息的结构化摘要正文（不含 SUMMARY_PREFIX）。异常 / 空返回 →
    降级为确定性摘要。
    """

    def __init__(self, store_dir: Optional[os.PathLike] = None,
                 summary_callback: Optional[Callable] = None):
        self.store_dir = Path(store_dir) if store_dir is not None else (
            REPO_ROOT.joinpath(*DEFAULT_STORE_SUBDIR))
        self.summary_callback = summary_callback
        self._lock = threading.RLock()

    # --------------------------------------------------------- 路径 / IO
    def _path(self, conversation_id: str) -> Path:
        return self.store_dir / f"{_sanitize_id(conversation_id)}.json"

    def _save(self, conversation: Dict[str, Any]) -> None:
        cid = _sanitize_id(conversation.get("conversation_id"))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        p = self.store_dir / f"{cid}.json"
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(conversation, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(str(tmp), str(p))  # 原子替换，避免半写文件

    def _read(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        p = self._path(conversation_id)
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        conv = conversation_from_dict(raw)
        conv["conversation_id"] = _sanitize_id(conversation_id)  # 文件名权威
        return conv

    # --------------------------------------------------------- CRUD
    def create(self, decision_id: str = "", title: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None,
               conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """新建会话并持久化。decision_id 绑定当前交易对象。"""
        cid = _sanitize_id(conversation_id) if conversation_id else (
            "CONV-" + uuid.uuid4().hex[:12])
        now = _now_iso()
        conv = {
            "conversation_id": cid,
            "decision_id": str(decision_id or ""),
            "title": title or "",
            "created_at": now,
            "updated_at": now,
            "rolling_summary": "",
            "messages": [],
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            self._save(conv)
        return copy.deepcopy(conv)

    def get(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """读取会话（读盘，保证最新；返回深拷贝，外部修改不会静默落盘）。"""
        with self._lock:
            conv = self._read(conversation_id)
        return copy.deepcopy(conv) if conv is not None else None

    def list(self) -> List[Dict[str, Any]]:
        """全部会话，按 updated_at 倒序。"""
        if not self.store_dir.exists():
            return []
        out: List[Dict[str, Any]] = []
        with self._lock:
            for p in self.store_dir.glob("*.json"):
                if p.suffix.lower() == ".json.tmp":
                    continue
                conv = self._read(p.stem)
                if conv is not None:
                    out.append(copy.deepcopy(conv))
        out.sort(key=lambda c: str(c.get("updated_at") or ""), reverse=True)
        return out

    def delete(self, conversation_id: str) -> bool:
        """删除会话文件；存在则删除返回 True，否则返回 False。"""
        with self._lock:
            p = self._path(conversation_id)
            if p.exists():
                p.unlink()
                return True
        return False

    # --------------------------------------------------------- 决策隔离
    def find_by_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """按 decision_id 查找已绑定的会话（无则 None）。"""
        did = str(decision_id or "")
        if not did:
            return None
        for conv in self.list():
            if conv.get("decision_id") == did:
                return conv
        return None

    def get_or_create(self, decision_id: str, title: Optional[str] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """决策绑定的会话入口（默认新会话语义）。

        用户切换 date/node/hour → 新 decision_id → 默认新会话；同一 decision_id
        再次访问则复用原会话，防上一笔交易污染下一笔。
        """
        existing = self.find_by_decision(decision_id)
        if existing is not None:
            return existing
        return self.create(decision_id, title=title, metadata=metadata)

    def bind_decision(self, conversation_id: str, decision_id: str,
                      decision_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """重新绑定会话到决策对象；可选写决策上下文（metadata.decision_context）。"""
        with self._lock:
            conv = self._read(conversation_id)
            if conv is None:
                raise KeyError(f"conversation 不存在: {conversation_id!r}")
            conv["decision_id"] = str(decision_id or "")
            if decision_context is not None:
                conv["metadata"]["decision_context"] = dict(decision_context)
            conv["updated_at"] = _now_iso()
            self._save(conv)
        return copy.deepcopy(self._read(conversation_id))

    def set_decision_context(self, conversation_id: str,
                             decision_context: Dict[str, Any]) -> Dict[str, Any]:
        """更新会话绑定的决策上下文（不含时点，只更新 metadata）。"""
        with self._lock:
            conv = self._read(conversation_id)
            if conv is None:
                raise KeyError(f"conversation 不存在: {conversation_id!r}")
            conv["metadata"]["decision_context"] = dict(decision_context or {})
            conv["updated_at"] = _now_iso()
            self._save(conv)
        return copy.deepcopy(self._read(conversation_id))

    def update_metadata(self, conversation_id: str,
                        patch: Dict[str, Any]) -> Dict[str, Any]:
        """合并更新会话 metadata（决策上下文 / 来源 / 版本等扩展字段）。"""
        with self._lock:
            conv = self._read(conversation_id)
            if conv is None:
                raise KeyError(f"conversation 不存在: {conversation_id!r}")
            conv["metadata"].update(dict(patch or {}))
            conv["updated_at"] = _now_iso()
            self._save(conv)
        return copy.deepcopy(self._read(conversation_id))

    # --------------------------------------------------------- 消息
    def append_message(self, conversation_id: str, msg: Any = None, *,
                       role: Optional[str] = None, content: Optional[str] = None,
                       **extra) -> Dict[str, Any]:
        """追加一条消息；触发阈值检查（超限自动压缩）。返回更新后会话。

        兼容：append_message(cid, {"role": "user", "content": "..."}) 或
              append_message(cid, role="user", content="...")。
        """
        with self._lock:
            conv = self._read(conversation_id)
            if conv is None:
                raise KeyError(
                    f"conversation 不存在: {conversation_id!r}（先用 create / get_or_create）")
            message = _normalize_message(msg, role=role, content=content, **extra)
            conv["messages"].append(message)
            conv["updated_at"] = _now_iso()
            self._save(conv)
            if self._needs_compress(conv):
                self.compress(conv, summary_callback=self.summary_callback)
        return copy.deepcopy(self._read(conversation_id))

    @staticmethod
    def _total_chars(messages) -> int:
        return sum(len(str(m.get("content", ""))) for m in messages)

    def _needs_compress(self, conversation: Dict[str, Any]) -> bool:
        msgs = conversation.get("messages", [])
        if len(msgs) > AGENT_MEMORY_MAX_MESSAGES:
            return True
        if self._total_chars(msgs) > AGENT_MEMORY_MAX_CHARS:
            return True
        return False

    # --------------------------------------------------------- 压缩（P3）
    def compress(self, conversation: Dict[str, Any],
                 summary_callback: Optional[Callable] = None) -> bool:
        """阈值压缩：保留最近 6~8 条完整，把旧消息压缩进 rolling_summary。

        - 先尝试 LLM 摘要回调（summary_callback），失败降级；
        - 降级 fallback：最近 user 主题 + assistant 首句拼成结构化摘要；
        - 摘要不得修改任何交易事实（带 SUMMARY_PREFIX 标注，非权威）；
        - 消息条数 ≤ KEEP_RECENT 时无旧消息可压缩 → 返回 False（不丢小历史）。
        """
        messages = conversation.get("messages", [])
        if len(messages) <= AGENT_MEMORY_KEEP_RECENT:
            return False
        keep = messages[-AGENT_MEMORY_KEEP_RECENT:]
        old = messages[:-AGENT_MEMORY_KEEP_RECENT]
        body = self._summarize(conversation, old, summary_callback)
        existing = str(conversation.get("rolling_summary") or "").strip()
        rolling = body if existing else SUMMARY_PREFIX + "\n" + body
        if existing:
            rolling = existing + "\n" + body
        conversation["rolling_summary"] = _truncate(rolling, AGENT_MEMORY_SUMMARY_MAX_CHARS)
        conversation["messages"] = keep
        conversation["updated_at"] = _now_iso()
        cid = conversation.get("conversation_id")
        if cid:
            with self._lock:
                self._save(conversation)
        return True

    def _summarize(self, conversation: Dict[str, Any], old_messages,
                   summary_callback: Optional[Callable] = None) -> str:
        cb = summary_callback or self.summary_callback
        if cb is not None:
            try:
                body = str(cb(conversation, list(old_messages)) or "").strip()
                if body:
                    if body.startswith(SUMMARY_PREFIX):
                        body = body[len(SUMMARY_PREFIX):].strip()
                    return body
            except Exception:
                pass  # 失败降级
        return self._fallback_summary(conversation, old_messages)

    def _fallback_summary(self, conversation: Dict[str, Any],
                          old_messages) -> str:
        """确定性降级摘要：最近用户主题 + 助手首句（不修改任何交易事实）。"""
        lines: List[str] = []
        dctx = dict((conversation.get("metadata") or {}).get("decision_context") or {})
        node = dctx.get("node")
        hour = dctx.get("hour")
        if node and hour is not None:
            lines.append(f"用户正在分析 {node} H{int(hour)}。")
        elif conversation.get("decision_id"):
            lines.append(f"用户正在分析 {conversation['decision_id']}。")
        topics = [m for m in old_messages if m.get("role") == "user"][-4:]
        points = [m for m in old_messages if m.get("role") == "assistant"][-4:]
        lines.append("已确认：")
        idx = 0
        for m in topics:
            idx += 1
            lines.append(f"{idx}. 用户主题：{_truncate(_strip_newlines(m.get('content', '')), 120)}")
        for m in points:
            first = _first_sentence(m.get("content", ""))
            if not first:
                continue
            idx += 1
            lines.append(f"{idx}. 助手要点：{first}")
        return "\n".join(lines) or "（无可用摘要内容）"

    # --------------------------------------------------------- 上下文构造
    def build_context(self, conversation: Dict[str, Any],
                      recent: Optional[int] = None) -> Dict[str, Any]:
        """构造注入 llm_copilot 的对话上下文。

        返回 {system_constraints, decision_context_summary, rolling_summary,
              recent_messages}。recent_messages 保留最近 6~8 条完整；
        decision_context_summary 仅在 outcome_revealed=True 时含 outcome。
        """
        if recent is None:
            recent = AGENT_MEMORY_KEEP_RECENT
        recent = int(recent)
        recent = max(AGENT_MEMORY_KEEP_RECENT_MIN,
                     min(recent, AGENT_MEMORY_KEEP_RECENT_MAX))
        messages = conversation.get("messages", [])
        return {
            "system_constraints": SYSTEM_CONSTRAINTS,
            "decision_context_summary": self._decision_context_summary(conversation),
            "rolling_summary": str(conversation.get("rolling_summary") or ""),
            "recent_messages": [copy.deepcopy(m) for m in messages[-recent:]],
        }

    def _decision_context_summary(self, conversation: Dict[str, Any]) -> str:
        """决策对象摘要。**未 reveal 绝不注入 outcome**（Tool access control 优先）。"""
        meta = dict(conversation.get("metadata") or {})
        dctx = dict(meta.get("decision_context") or {})
        cid = str(conversation.get("decision_id") or "")
        lines: List[str] = []
        node = dctx.get("node")
        hour = dctx.get("hour")
        zone = dctx.get("zone")
        target_date = str(dctx.get("target_date") or "")[:10]
        if node and hour is not None:
            loc = str(node)
            if zone:
                loc += f"（{zone}）"
            if target_date:
                loc += f" · 目标日 {target_date}"
            lines.append(f"决策对象: {loc} · H{int(hour)}")
        else:
            lines.append(f"决策对象: {cid or '（未绑定）'}")
        fr = dctx.get("final_recommendation")
        if fr:
            lines.append(f"最终建议: {fr}")
        revealed = bool(dctx.get("outcome_revealed", False))
        if revealed:
            # 只有已 reveal 才允许出现 actual_* / outcome
            outcome = dict(dctx.get("outcome") or {})
            src = outcome if outcome else dctx
            for k, label in (("actual_da", "实际 DA"), ("actual_rtpd", "实际 RTPD"),
                             ("actual_return", "实际 Return"), ("pnl", "PnL")):
                if src.get(k) is not None:
                    lines.append(f"{label}: {src[k]}")
        else:
            lines.append("Post-trade: OUTCOME_NOT_REVEALED（actual_* 未揭晓，禁止注入）")
        return "\n".join(lines)

    # --------------------------------------------------------- 序列化
    def to_dict(self) -> Dict[str, Any]:
        """整库导出：{conversation_id: conversation, ...}。"""
        out: Dict[str, Any] = {}
        for conv in self.list():
            out[conv["conversation_id"]] = conv
        return out

    def from_dict(self, data: Any) -> int:
        """从 dict（to_dict 产物）恢复会话，逐条写盘。返回恢复条数。"""
        count = 0
        for raw in (data or {}).values():
            conv = conversation_from_dict(raw)
            cid = _sanitize_id(conv["conversation_id"] or
                               conv.get("decision_id") or
                               ("CONV-" + uuid.uuid4().hex[:8]))
            conv["conversation_id"] = cid
            with self._lock:
                self._save(conv)
            count += 1
        return count


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------
_default_manager: Optional[SessionManager] = None
_manager_lock = threading.Lock()


def get_session_manager(**kw) -> SessionManager:
    """模块级单例（无参复用默认实例；首次调用可用 kw 指定 store_dir / 回调）。"""
    global _default_manager
    with _manager_lock:
        if _default_manager is None:
            _default_manager = SessionManager(**kw)
        return _default_manager


# ---------------------------------------------------------------------------
# 自测演示（临时目录；验证 create/append/compress/决策隔离）
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp(prefix="agent_memory_selftest_")
    store = os.path.join(tmp, "sessions")
    mgr = SessionManager(store_dir=store)
    checks = 0

    def ok(name: str) -> None:
        nonlocal checks
        checks += 1
        print(f"  [PASS] {name}")

    # 1) create / append / get
    conv = mgr.create("DEC-SELF-A", title="自测 A")
    mgr.append_message(conv["conversation_id"], {"role": "user", "content": "为什么不卖"})
    mgr.append_message(conv["conversation_id"],
                       {"role": "assistant", "content": "工具结果：当前建议不交易", "status": "ok"})
    got = mgr.get(conv["conversation_id"])
    assert len(got["messages"]) == 2 and got["messages"][1].get("status") == "ok"
    ok("create/append/get（消息含 id/role/content/status/created_at）")

    # 2) 决策隔离：新 decision_id → 新会话；同 decision_id 复用
    convB = mgr.get_or_create("DEC-SELF-B", title="自测 B")
    mgr.append_message(convB["conversation_id"], {"role": "user", "content": "B 的问题"})
    assert convB["conversation_id"] != conv["conversation_id"]
    gotA = mgr.get(conv["conversation_id"])
    assert len(gotA["messages"]) == 2 and "B 的问题" not in gotA["messages"][0]["content"]
    convA2 = mgr.get_or_create("DEC-SELF-A")
    assert convA2["conversation_id"] == conv["conversation_id"]
    ok("决策隔离：新 decision_id 新会话 / 同 decision_id 复用 / 无跨交易污染")

    # 3) 阈值压缩（消息条数）
    convC = mgr.create("DEC-SELF-C", title="压缩")
    for i in range(AGENT_MEMORY_MAX_MESSAGES + 3):
        mgr.append_message(convC["conversation_id"],
                           {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"})
    gotC = mgr.get(convC["conversation_id"])
    # 自动压缩在 >12 条时触发（压缩后 6 条），再追加 2 条 → 8 条，落在"6~8"窗口内
    assert AGENT_MEMORY_KEEP_RECENT_MIN <= len(gotC["messages"]) <= AGENT_MEMORY_KEEP_RECENT_MAX, \
        len(gotC["messages"])
    assert gotC["rolling_summary"] and "msg" in gotC["rolling_summary"]
    assert gotC["messages"][-1]["content"] == f"msg {AGENT_MEMORY_MAX_MESSAGES + 2}"
    assert "msg 0" not in [m["content"] for m in gotC["messages"]]
    ok("阈值压缩：>12 条触发，保留最近 6~8 条，rolling_summary 生成，旧消息删除")

    # 4) 阈值压缩（字符数，回调成功 / 失败降级）
    def llm_cb(c, old):
        return "LLM 摘要：用户关注天气来源与证据可用性。"
    mgr2 = SessionManager(store_dir=os.path.join(tmp, "s2"), summary_callback=llm_cb)
    convD = mgr2.create("DEC-SELF-D", title="LLM 压缩")
    for i in range(AGENT_MEMORY_MAX_MESSAGES + 1):
        mgr2.append_message(convD["conversation_id"], {"role": "user", "content": f"主题{i}"})
    assert "LLM 摘要" in mgr2.get(convD["conversation_id"])["rolling_summary"]
    ok("压缩回调：LLM 摘要回调生效")

    def bad_cb(c, old):
        raise RuntimeError("LLM down")
    mgr3 = SessionManager(store_dir=os.path.join(tmp, "s3"), summary_callback=bad_cb)
    convE = mgr3.create("DEC-SELF-E", title="降级")
    for i in range(AGENT_MEMORY_MAX_MESSAGES + 1):
        mgr3.append_message(convE["conversation_id"], {"role": "user", "content": f"主题{i}"})
    rs = mgr3.get(convE["conversation_id"])["rolling_summary"]
    assert "用户正在分析" in rs and "已确认" in rs
    ok("压缩降级：LLM 回调失败 → 确定性结构化摘要")

    # 5) 未 reveal 不注入 outcome（build_context 安全边界）
    convF = mgr.create("DEC-SELF-F", title="安全边界")
    dctx = {"node": "CONTROLX_1_N001", "target_date": "2026-07-09", "hour": 2,
            "zone": "ZP26", "final_recommendation": "BUY_DA",
            "outcome_revealed": False,
            "outcome": {"actual_da": 999.0, "actual_rtpd": 998.0, "pnl": 123.0}}
    mgr.set_decision_context(convF["conversation_id"], dctx)
    ctx = mgr.build_context(mgr.get(convF["conversation_id"]))
    assert set(ctx.keys()) == {"system_constraints", "decision_context_summary",
                               "rolling_summary", "recent_messages"}
    assert "OUTCOME_NOT_REVEALED" in ctx["decision_context_summary"]
    assert "999" not in ctx["decision_context_summary"] and "123" not in ctx["decision_context_summary"]
    dctx2 = dict(dctx); dctx2["outcome_revealed"] = True
    mgr.set_decision_context(convF["conversation_id"], dctx2)
    ctx2 = mgr.build_context(mgr.get(convF["conversation_id"]))
    assert "999" in ctx2["decision_context_summary"] and "123" in ctx2["decision_context_summary"]
    ok("安全边界：未 reveal 剔除 outcome，reveal 后注入")

    # 6) list 倒序 / delete / to_dict / from_dict
    lst = mgr.list()
    assert lst[0]["updated_at"] >= lst[-1]["updated_at"]
    assert len(lst) == 4  # A / B / C / F（D/E 在 mgr2/mgr3）
    assert mgr.delete(conv["conversation_id"]) is True and mgr.get(conv["conversation_id"]) is None
    data = mgr.to_dict()
    mgr4 = SessionManager(store_dir=os.path.join(tmp, "s4"))
    assert mgr4.from_dict(data) == 3
    assert mgr4.get(convB["conversation_id"]) is not None
    ok("list 倒序 / delete / to_dict / from_dict 恢复")

    print(f"  --- 自测通过：{checks} 项检查 ---")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
