/* mvp_agent.js — CAISO 交易决策助手 · V0.4.1 Trading Copilot 会话窗口
   Lead 统一写（集成 Agent B 的 MDRender）。会话化 + Observable Event Protocol 流式。
   不展示 private chain-of-thought；轨迹来自真实 backend 事件；交易核心冻结。
*/
(function () {
  "use strict";
  var doc = document;
  var LS_KEY = "caiso_agent_sessions_v2";
  var session = null;          // 当前会话 {conversation_id, decision_id, title, messages}
  var history = [];            // 会话历史（localStorage）
  var busy = false;
  var controller = null;       // AbortController（停止）
  var lastDecisionId = null;
  var autoFollow = true;

  function M() { return window.MVP || null; }
  function esc(s) { var m = M(); return m && m.esc ? m.esc(s) : _esc(s); }
  function _esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toolZh(tool) {
    return { get_decision: "读取当前交易建议", get_feature_explanation: "检查模型判断",
             get_evidence: "查询外部证据（使用·拒绝）", get_similar_cases: "查询历史案例",
             get_data_provenance: "查询数据血缘", get_post_trade_review: "查询事后复盘" }[tool] || tool;
  }
  function mdRender(text) {
    // 优先 Agent B 的 MDRender（sanitized）；未加载时 fallback：转义 + 简单表格/加粗
    if (window.MDRender && window.MDRender.render) {
      try { return window.MDRender.render(text); } catch (e) { /* fallback */ }
    }
    var t = esc(text);
    t = t.replace(/^### (.+)$/gm, "<h4>$1</h4>").replace(/^## (.+)$/gm, "<h3>$1</h3>").replace(/^# (.+)$/gm, "<h2>$1</h2>");
    t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\*([^*]+)\*/g, "<i>$1</i>");
    t = t.replace(/^\|(.+)\|$/gm, function (line) {
      var cells = line.replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); });
      var isSep = cells.every(function (c) { return /^:?-{2,}:?$/.test(c); });
      if (isSep) return "<!--sep-->";
      return "<tr>" + cells.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>";
    });
    t = t.replace(/<!--sep-->/g, "");
    if (t.indexOf("<tr>") >= 0) t = '<table class="md-table">' + t + "</table>";
    return t.replace(/\n/g, "<br>");
  }
  function fmtNum(s) {
    if (window.MDRender && window.MDRender.formatNumbers) {
      try { return window.MDRender.formatNumbers(s); } catch (e) { /* fallback */ }
    }
    // 保守：只格式化业务长小数（>6 位小数，非 date/id/hash）
    return String(s).replace(/(-?\d+\.\d{6,})(?![\w-])/g, function (n) {
      var v = parseFloat(n);
      if (isNaN(v)) return n;
      return v.toFixed(2);
    });
  }

  /* ================= 会话存储（localStorage） ================= */
  function persist() {
    try {
      if (session) {
        session.updated_at = new Date().toISOString();
        var i = history.findIndex(function (h) { return h.conversation_id === session.conversation_id; });
        if (i >= 0) history[i] = session; else history.unshift(session);
        localStorage.setItem(LS_KEY, JSON.stringify(history));
      }
    } catch (e) { /* storage 不可用时降级 */ }
  }
  function loadHistory() {
    try { history = JSON.parse(localStorage.getItem(LS_KEY) || "[]") || []; }
    catch (e) { history = []; }
  }
  function newSession() {
    session = { conversation_id: "cv_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
                decision_id: (M() && M().state.decision_id) || null,
                title: "新会话", messages: [], created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    persist(); renderMessages(); renderPanelHead();
  }
  function switchSession(cid) {
    var h = history.find(function (x) { return x.conversation_id === cid; });
    if (!h) return;
    session = h; renderMessages(); renderPanelHead(); autoFollow = true;
  }

  /* ================= 渲染 ================= */
  function els() {
    return { head: doc.getElementById("agent-head"),
             chip: doc.getElementById("agent-chip"),
             quick: doc.getElementById("quick-q"),
             input: doc.getElementById("inp-question"),
             send: doc.getElementById("btn-ask"),
             messages: doc.getElementById("agent-messages"),
             stop: doc.getElementById("btn-stop") };
  }
  function decisionScope() {
    var m = M(); if (!m || !m.state || !m.state.decision) return null;
    var ctx = m.state.decision.context || {};
    return { text: (ctx.node || "") + " · H" + (ctx.hour || "?") + " · " + (ctx.decision_date || ""),
             has: true, decision_id: m.state.decision_id };
  }
  function renderPanelHead() {
    var e = els(); if (!e.chip) return;
    var scope = decisionScope();
    e.chip.textContent = scope ? "CONTROLX" + "" : "";
    e.chip.textContent = scope ? (scope.text || "") : "先运行一笔决策";
    e.chip.className = "agent-chip" + (scope ? "" : " muted");
  }
  function renderMessages() {
    var e = els(); if (!e.messages || !session) return;
    var html = "";
    session.messages.forEach(function (msg, i) {
      if (msg.role === "user") {
        html += '<div class="agent-msg user"><div class="bubble">' + esc(msg.content) + "</div></div>";
      } else {
        var traceHtml = "";
        if (msg.trace && msg.trace.length) {
          traceHtml = '<div class="agent-trace">' + msg.trace.map(function (st) {
            var icon = st.status === "run" ? "○" : (st.status === "err" ? "✕" : "✓");
            var cls = st.status === "run" ? "run" : (st.status === "err" ? "err" : "ok");
            return '<div class="trace-step"><span class="s ' + cls + '">' + icon + " " + esc(st.zh) + "</span>" +
              (st.badge ? '<span class="trace-badge">' + esc(st.badge) + "</span>" : "") + "</div>";
          }).join("") + "</div>";
        }
        var guard = msg.guard === "BLOCKED" ? '<div class="agent-guard err">⚠ 回答未通过一致性检查，已保留工具原始数据。</div>' : "";
        var tech = msg.trace && msg.trace.length
          ? '<details class="detail"><summary>查看技术 Trace</summary><div class="detail-body">' +
            msg.trace.map(function (st) {
              return '<div>✓ ' + esc(st.zh) + (st.badge ? " · " + esc(st.badge) : "") + (st.status ? " · " + esc(st.status) : "") + "</div>";
            }).join("") +
            (msg.guard ? '<div>guard: ' + esc(msg.guard) + "</div>" : "") +
            "</div></details>" : "";
        html += '<div class="agent-msg assistant"><div class="bubble md">' + mdRender(fmtNum(msg.content)) + "</div>" + traceHtml + tech + guard + "</div>";
      }
    });
    e.messages.innerHTML = html;
    if (autoFollow) { e.messages.scrollTop = e.messages.scrollHeight; }
  }
  function appendUser(text) {
    session.messages.push({ id: "u" + Date.now(), role: "user", content: text, created_at: new Date().toISOString() });
    persist(); renderMessages();
  }
  function appendAssistant() {
    var msg = { id: "a" + Date.now(), role: "assistant", content: "", trace: [], guard: "", created_at: new Date().toISOString() };
    session.messages.push(msg);
    persist(); return msg;
  }

  /* ================= 流式 Ask（Observable Event Protocol） ================= */
  function renderLive(assistantMsg, answerBox, traceBox) {
    var traceHtml = assistantMsg.trace.map(function (st) {
      var icon = st.status === "run" ? "○" : (st.status === "err" ? "✕" : "✓");
      var cls = st.status === "run" ? "run" : (st.status === "err" ? "err" : "ok");
      return '<div class="trace-step"><span class="s ' + cls + '">' + icon + " " + esc(st.zh) + "</span>" +
        (st.badge ? '<span class="trace-badge">' + esc(st.badge) + "</span>" : "") + "</div>";
    }).join("");
    traceBox.innerHTML = traceHtml || "";
    answerBox.textContent = assistantMsg.content;
    if (!assistantMsg.done) { answerBox.classList.add("streaming"); }
    else { answerBox.classList.remove("streaming"); answerBox.innerHTML = mdRender(fmtNum(assistantMsg.content)); }
    var msgs = els().messages; if (msgs && autoFollow) msgs.scrollTop = msgs.scrollHeight;
  }
  function handleEvent(ev, d, am, answerBox, traceBox) {
    if (!d) return;
    if (ev === "tool_start") {
      am.trace.push({ zh: toolZh(d.tool) || d.label || "正在调用…", status: "run", badge: "" });
    } else if (ev === "tool_result") {
      var last = am.trace[am.trace.length - 1];
      if (last && last.status === "run") { last.status = "ok"; last.badge = d.summary || d.label || ""; }
      else am.trace.push({ zh: toolZh(d.tool) || "完成", status: "ok", badge: d.summary || "" });
    } else if (ev === "tool_error") {
      var e0 = am.trace[am.trace.length - 1];
      if (e0 && e0.status === "run") { e0.status = "err"; e0.badge = d.message || "失败"; }
      else am.trace.push({ zh: (d.tool ? toolZh(d.tool) : "工具") + " 失败", status: "err", badge: d.message || "" });
    } else if (ev === "answer_start") {
      am.content = "";
    } else if (ev === "answer_delta") {
      am.content += d.text || "";
    } else if (ev === "answer_done") {
      am.done = true;
    } else if (ev === "guard_result" || ev === "guard") {
      am.guard = d.status;
      if (d.status === "BLOCKED") { am.content = "Agent 回答未通过一致性检查，请重新查询。"; }
    } else if (ev === "agent_status" || ev === "route_start") {
      am.trace.push({ zh: d.label || "正在分析…", status: "run", badge: "" });
    } else if (ev === "heartbeat") {
      // 保活：刷新 loading，不新增内容
    }
    renderLive(am, answerBox, traceBox);
  }
  async function ask(q) {
    var e = els();
    var question = (q !== undefined && q !== null) ? String(q).trim() : (e.input ? String(e.input.value || "").trim() : "");
    if (!question || busy) return;
    var scope = decisionScope();
    if (!scope || !scope.decision_id) { showHint("请先运行一笔决策，再开始追问。"); return; }
    // decision 切换 → 新会话
    if (lastDecisionId && lastDecisionId !== scope.decision_id) {
      lastDecisionId = scope.decision_id; newSession();
    } else if (!lastDecisionId) { lastDecisionId = scope.decision_id; }
    if (!session || session.decision_id !== scope.decision_id) {
      newSession(); session.decision_id = scope.decision_id;
    }
    appendUser(question);
    var am = appendAssistant();
    var msgEl = els().messages;
    // 动态生成本条 assistant 的渲染节点
    var liveWrap = document.createElement("div");
    liveWrap.className = "agent-msg assistant";
    liveWrap.innerHTML = '<div class="bubble md streaming">…<span class="cursor"></span></div><div class="agent-trace"></div>';
    msgEl.appendChild(liveWrap);
    var answerBox = liveWrap.querySelector(".bubble");
    var traceBox = liveWrap.querySelector(".agent-trace");
    busy = true; if (e.send) e.send.disabled = true; if (e.stop) e.stop.style.display = "";
    controller = new AbortController();
    try {
      var resp = await fetch("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question, decision_id: scope.decision_id, conversation: session }),
        signal: controller.signal,
      });
      var reader = resp.body.getReader(); var decoder = new TextDecoder();
      var buf = "", done = false;
      while (!done) {
        var r = await reader.read(); done = r.done;
        buf += decoder.decode(r.value || new Uint8Array(), { stream: !done });
        var idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          var block = buf.slice(0, idx); buf = buf.slice(idx + 2);
          var ev = "message", data = "";
          block.split("\n").forEach(function (line) {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          });
          if (data) { try { handleEvent(ev, JSON.parse(data), am, answerBox, traceBox); } catch (e2) {} }
        }
      }
    } catch (err) {
      if (err.name === "AbortError") { am.content += "（已停止生成）"; }
      else { am.content = "Agent 暂时无法生成自然语言回答。" + (am.content ? "\n（以上为已生成部分）" : ""); }
    } finally {
      am.done = true; renderLive(am, answerBox, traceBox);
      busy = false; if (e.send) e.send.disabled = false; if (e.stop) e.stop.style.display = "none";
      persist();
    }
  }
  function stopGen() { if (controller) controller.abort(); }

  function showHint(txt) {
    var e = els(); if (!e.messages) return;
    var d = document.createElement("div"); d.className = "agent-hint"; d.textContent = txt;
    e.messages.appendChild(d);
  }

  /* ================= 历史会话 UI ================= */
  function renderHistoryDrawer() {
    var wrap = doc.getElementById("history-drawer");
    if (!wrap) return;
    var items = history.slice(0, 10).map(function (h) {
      var t = h.title || "会话";
      var when = h.updated_at ? h.updated_at.slice(11, 16) : "";
      return '<div class="hist-item" data-cid="' + esc(h.conversation_id) + '">' + esc(t) +
        '<span class="time">' + when + "</span></div>";
    }).join("");
    wrap.innerHTML = items || '<div class="empty">暂无历史会话</div>';
    wrap.querySelectorAll(".hist-item").forEach(function (el) {
      el.addEventListener("click", function () { switchSession(el.getAttribute("data-cid")); });
    });
  }
  function toggleHistory() {
    var wrap = doc.getElementById("history-drawer");
    if (wrap) { wrap.style.display = wrap.style.display === "none" ? "block" : "none"; renderHistoryDrawer(); }
  }

  /* ================= init ================= */
  function wireControls() {
    var e = els();
    if (e.input) { e.input.addEventListener("keydown", function (ev) { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); ask(); } }); }
    if (e.send) e.send.addEventListener("click", function () { ask(); });
    if (e.stop) e.stop.addEventListener("click", stopGen);
    var n = doc.getElementById("btn-new-session"); if (n) n.addEventListener("click", newSession);
    var h = doc.getElementById("btn-history"); if (h) h.addEventListener("click", toggleHistory);
    var msgs = els().messages;
    if (msgs) {
      msgs.addEventListener("scroll", function () {
        var nearBottom = msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 60;
        autoFollow = nearBottom;
        var j = doc.getElementById("jump-down");
        if (j) j.style.display = nearBottom ? "none" : "block";
      });
      var j = doc.getElementById("jump-down");
      if (j) j.addEventListener("click", function () { autoFollow = true; msgs.scrollTop = msgs.scrollHeight; });
    }
  }
  function init() {
    loadHistory();
    var scope = decisionScope();
    var last = scope && history.find(function (h) { return h.decision_id === scope.decision_id; });
    if (last) { session = last; lastDecisionId = scope.decision_id; }
    else { newSession(); if (scope) session.decision_id = scope.decision_id; }
    // 预设问题（按真实路由分组，业务化）
    var quick = doc.getElementById("quick-q");
    if (quick) {
      quick.innerHTML = "";
      ["为什么建议卖出？", "为什么不是买入？", "最大的风险是什么？", "用了哪些数据？",
       "哪些信息被拒绝了？", "有没有类似历史案例？", "这笔为什么亏了？"].forEach(function (q) {
        var b = document.createElement("button");
        b.textContent = q;
        b.addEventListener("click", function () { ask(q); });
        quick.appendChild(b);
      });
    }
    renderPanelHead(); renderMessages(); wireControls();
  }
  window.MVPAgent = { init: init, ask: ask, stop: stopGen, newSession: newSession, renderMessages: renderMessages };
})();
