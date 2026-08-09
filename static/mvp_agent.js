/* static/mvp_agent.js
 * CAISO 交易决策助手 · V0.4 Phase 2 —— Agent B：Agent Streaming + Tool Trace
 * =============================================================================
 * 只负责右侧 "问交易 Agent" 面板的流式问答渲染。
 *
 * 依赖（共享全局，由 mvp_core.js 提供，本文件不定义）：
 *   window.MVP = {
 *     state: { meta, decision, decision_id, locked, revealed, post_trade, evidence },
 *     esc(s), fmt(x, nd), sign(v), post(path, body)
 *   }
 *
 * 导出（由 mvp_index.html 在 DOMContentLoaded 时调用 window.MVPAgent.init()）：
 *   window.MVPAgent = { init(), ask(q) }
 *
 * 铁律：
 *   * 主业务层绝不显示 get_decision 等原始工具名（一律用业务中文名）。
 *   * 无 private CoT：Question → Tool → Result → Answer；只有真实 tool 事件上屏，绝不伪造步骤。
 *   * Guard 拦截 → 红卡提示，不展示被拦截内容；Degraded / Error 有明确视觉反馈。
 *   * 技术 Trace / Raw JSON 一律折叠在 <details> 内，默认业务化回答。
 */
(function () {
  "use strict";

  var doc = document;

  /* ===================== 业务映射（只影响展示，不改任何数字） ===================== */
  var TOOL_ZH = {
    "get_decision": "读取当前交易建议",
    "get_feature_explanation": "检查模型判断",
    "get_evidence": "查询外部证据（使用 / 拒绝）",
    "get_similar_cases": "查询历史案例",
    "get_data_provenance": "查询数据血缘",
    "get_post_trade_review": "查询事后复盘"
  };
  var FINAL_ACTION = { SELL_DA: "SELL DA", BUY_DA: "BUY DA", NO_TRADE: "NO TRADE" };
  var FINAL_ZH = { SELL_DA: "卖出日前", BUY_DA: "买入日前", NO_TRADE: "不交易" };
  var GATE_ZH = { PASS: "通过", WARNING: "谨慎", REJECT: "禁止" };

  /* 预置问题（按真实路由分组；复盘问题仅在揭晓后点亮） */
  var QUICK = [
    { q: "为什么建议卖出？", g: "决策", t: "get_decision" },
    { q: "为什么不是买入？", g: "决策", t: "get_decision" },
    { q: "最大的风险是什么？", g: "风控", t: "" },
    { q: "用了哪些数据？", g: "血缘", t: "get_data_provenance" },
    { q: "哪些信息被拒绝了？", g: "证据", t: "get_evidence" },
    { q: "有没有类似历史案例？", g: "案例", t: "get_similar_cases" },
    { q: "这笔为什么亏了？", g: "复盘", t: "get_post_trade_review", reveal: true }
  ];

  /* ---------- 防御式读取 MVP（可能尚未由 mvp_core.js 注入） ---------- */
  function M() { return window.MVP || null; }
  function esc(s) { var m = M(); return m && m.esc ? m.esc(s) : _esc(s); }
  function fmt(x, nd) { var m = M(); return m && m.fmt ? m.fmt(x, nd) : _fmt(x, nd); }
  function sign(v) { var m = M(); return m && m.sign ? m.sign(v) : (Number(v) > 0 ? "+" : ""); }
  function _esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function _fmt(x, nd) {
    if (x === null || x === undefined) return "—";
    var v = Number(x); if (isNaN(v)) return "—";
    if (nd === undefined) nd = 2;
    return v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: nd });
  }

  function toolZh(tool, serverLabel) {
    if (TOOL_ZH[tool]) return TOOL_ZH[tool];
    if (serverLabel) return serverLabel;
    return "查询工具"; // 业务层兜底，绝不显示原始工具名
  }

  function safeParse(raw) {
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (e) { return null; }
  }

  /* ===================== 当前决策对象（操作对象 chip） ===================== */
  function decisionScope() {
    var st = M() && M().state;
    if (!st || !st.decision) return { text: "尚未运行决策（请先点击「生成交易建议」）", has: false };
    var d = st.decision, ctx = d.context || {};
    var node = ctx.node || d.node || "";
    var zone = ctx.zone || "";
    var hour = ctx.hour;
    var dd = String(ctx.decision_date || "").slice(0, 10);
    var parts = [];
    if (node) parts.push(node + (zone ? "（" + zone + "）" : ""));
    if (hour) parts.push("H" + hour);
    if (dd) parts.push(dd);
    var text = parts.join(" · ");
    if (FINAL_ZH[d.final_recommendation]) text += " · 建议 " + FINAL_ZH[d.final_recommendation];
    return { text: text, has: true, final: d.final_recommendation };
  }

  /* ===================== 工具结果徽标（业务化，不铺原始字段） ===================== */
  function resultBadge(tool, summaryRaw) {
    var obj = safeParse(summaryRaw);
    if (!obj) return "";
    if (obj.status === "error") return '<span class="pill err">查询失败</span>';
    if (tool === "get_decision") {
      var fin = obj.final_recommendation;
      var out = [];
      if (fin) {
        var cls = fin === "SELL_DA" ? "SELL_DA" : (fin === "BUY_DA" ? "BUY_DA" : "NO_TRADE");
        out.push('<span class="pill ' + cls + '">' + esc(FINAL_ACTION[fin] || fin) + '</span>');
      }
      var rg = obj.risk_gate || {};
      if (rg.decision) {
        var gcls = rg.decision === "REJECT" ? "REJECT" : (rg.decision === "WARNING" ? "WARNING" : "PASS");
        out.push('<span class="pill ' + gcls + '">' + esc(GATE_ZH[rg.decision] || rg.decision) + '</span>');
      }
      var er = obj.model_output && obj.model_output.expected_return;
      if (er !== null && er !== undefined && !isNaN(Number(er))) {
        out.push('<span class="tt-num">' + esc(sign(er)) + fmt(Number(er), 2) + ' $/MWh</span>');
      }
      return out.join(" ");
    }
    if (tool === "get_feature_explanation") {
      var rows = obj.top_features || [];
      return rows.length
        ? '<span class="pill ok">' + rows.length + ' 项关键特征</span>'
        : '<span class="pill">无特征统计</span>';
    }
    if (tool === "get_evidence") {
      var elig = (obj.eligible || []).length, rej = (obj.rejected || []).length;
      var parts = [];
      if (elig) parts.push('<span class="pill ok">' + elig + ' 条可用</span>');
      if (rej) parts.push('<span class="pill err">' + rej + ' 条被拒</span>');
      return parts.join(" ") || '<span class="pill">无外部证据</span>';
    }
    if (tool === "get_similar_cases") {
      var n = (obj.cases || []).length;
      return n ? '<span class="pill ok">' + n + ' 个历史案例</span>' : '<span class="pill">无相似案例</span>';
    }
    if (tool === "get_data_provenance") {
      var provs = obj.provenance || [];
      var n2 = obj.n || provs.length;
      return n2 ? '<span class="pill ok">' + n2 + ' 个数据来源</span>' : '<span class="pill">无血缘信息</span>';
    }
    if (tool === "get_post_trade_review") {
      if (obj.status === "REVEALED") {
        var pnl = Number(obj.pnl);
        var cls2 = pnl < 0 ? "err" : "ok";
        return '<span class="pill ' + cls2 + '">复盘 ' + esc(sign(pnl)) + fmt(pnl, 2) + ' $/MWh</span>';
      }
      if (obj.status === "OUTCOME_NOT_REVEALED") return '<span class="pill warn">结果未揭晓</span>';
      return '<span class="pill err">查询失败</span>';
    }
    return "";
  }

  /* 参数摘要（SSE 不携带 args，从当前决策上下文推断真实参数；技术 Trace 用） */
  function argsTextFor(tool) {
    var st = M() && M().state;
    var ctx = st && st.decision && st.decision.context;
    if (tool === "get_decision" && ctx) {
      return String(ctx.decision_date || "").slice(0, 10) + " · " + (ctx.node || "") +
             (ctx.hour ? " · H" + ctx.hour : "");
    }
    if (st && st.decision_id) return "decision_id = " + String(st.decision_id).slice(0, 8) + "…";
    return "基于当前决策";
  }

  function fmtDuration(ms) {
    if (ms === null || ms === undefined || isNaN(ms)) return "—";
    return (ms / 1000).toFixed(2) + "s";
  }

  function prettyJson(raw) {
    if (!raw) return "";
    try { return JSON.stringify(JSON.parse(raw), null, 2); }
    catch (e) { return raw; }
  }

  function isDegraded(text) { return String(text || "").indexOf("LLM NOT CONFIGURED") >= 0; }

  /* 业务化回答：mock 尾部 JSON 折叠进 details，主回答区只留中文业务句 */
  function businessAnswer(text) {
    var lead = text, json = null;
    var m = /（mock 演示回答[^\n]*）依据工具结果：/.exec(String(text || ""));
    if (m) {
      var i = String(text).indexOf("{", m.index);
      if (i >= 0) {
        var j = String(text).lastIndexOf("}");
        if (j > i) { lead = String(text).slice(0, i); json = String(text).slice(i, j + 1); }
      }
    }
    return { lead: (lead || "").trim(), json: json };
  }

  /* ===================== 额外样式（不依赖其他 Agent 改 mvp.css） ===================== */
  function ensureStyle() {
    if (doc.getElementById("mvp-agent-style")) return;
    var st = doc.createElement("style");
    st.id = "mvp-agent-style";
    st.textContent = [
      ".agent-obj{font-size:11.5px;color:var(--text-dim);background:var(--panel-2);border:1px solid var(--border);border-radius:20px;padding:3px 10px;margin-bottom:8px;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}",
      ".agent-obj.muted{opacity:.7}",
      ".agent-quick .qa-group{font-size:10.5px;color:var(--text-dim);font-weight:600;line-height:22px;margin-right:2px}",
      ".agent-quick button:disabled{opacity:.4;cursor:not-allowed;border-style:dashed}",
      ".agent-trace .trace-step{display:flex;gap:8px;align-items:flex-start}",
      ".agent-trace .trace-step .s.err{color:var(--err);font-weight:600}",
      ".agent-trace .trace-step .trace-meta{margin-left:auto;display:inline-flex;gap:8px;align-items:center;white-space:nowrap}",
      ".agent-trace .trace-step .trace-time{color:var(--text-dim);font-size:11px}",
      ".agent-trace .trace-step .trace-badge .pill{font-size:10.5px;padding:1px 7px}",
      ".tt-num{color:var(--text-dim);font-size:11px;white-space:nowrap}",
      ".agent-answer.err{border-left-color:var(--err)}",
      ".tt-pre{white-space:pre-wrap;word-break:break-all;font-family:var(--mono);font-size:11.5px;margin:0}",
      ".tt-line{font-size:12px;padding:3px 0;border-bottom:1px dashed var(--border)}",
      ".tt-line:last-child{border-bottom:none}",
      ".tt-idx{display:inline-block;min-width:18px;color:var(--text-dim);font-size:11px}",
      ".agent-hint{margin-top:6px}",
      ".tech-trace{margin-top:10px}"
    ].join("\n");
    doc.head.appendChild(st);
  }

  /* ===================== 预置问题（按真实路由分组） ===================== */
  function renderQuickQuestions() {
    var box = doc.getElementById("quick-q");
    if (!box) return;
    var st = M() && M().state;
    var revealed = !!(st && st.revealed);
    var frag = doc.createDocumentFragment();
    var lastG = "";
    QUICK.forEach(function (item) {
      if (item.g !== lastG) {
        var lab = doc.createElement("span");
        lab.className = "qa-group";
        lab.textContent = item.g;
        frag.appendChild(lab);
        lastG = item.g;
      }
      var btn = doc.createElement("button");
      btn.type = "button";
      btn.textContent = item.q;
      btn.title = item.t ? ("路由：/" + item.t) : "Agent 自由路由";
      if (item.reveal && !revealed) btn.disabled = true;
      btn.addEventListener("click", function () { MVPAgent.ask(item.q); });
      frag.appendChild(btn);
    });
    box.innerHTML = "";
    box.appendChild(frag);
  }

  var _lastRevealed = null;
  function syncQuickQuestions() {
    var st = M() && M().state;
    var rv = !!(st && st.revealed);
    if (rv !== _lastRevealed) { _lastRevealed = rv; renderQuickQuestions(); }
  }

  /* ===================== 输入 / 发送控件接线 ===================== */
  function wireControls() {
    var input = doc.getElementById("inp-question");
    if (input) {
      var onAttr = input.getAttribute("onkeydown") || "";
      if (onAttr.indexOf("ask(") < 0) {
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); MVPAgent.ask(); }
        });
      }
    }
    var send = doc.querySelector(".agent-input .btn");
    if (send) {
      var btnAttr = send.getAttribute("onclick") || "";
      if (btnAttr.indexOf("ask(") < 0) {
        send.addEventListener("click", function () { MVPAgent.ask(); });
      }
    }
  }

  function resetOutput() {
    var out = doc.getElementById("ask-out");
    if (!out || out.childElementCount) return;
    var scope = decisionScope();
    out.innerHTML = '<div class="agent-obj' + (scope.has ? "" : " muted") + '">' + esc(scope.text) + '</div>' +
      '<div class="empty">输入问题，或点击上方预置问题；Agent 只解释系统结果，不改变建议。</div>';
  }

  /* ===================== 流式 Ask ===================== */
  var busy = false;

  async function ask(q) {
    var input = doc.getElementById("inp-question");
    var question = (q !== undefined && q !== null)
      ? String(q).trim()
      : (input ? String(input.value || "").trim() : "");
    if (!question) return;
    if (busy) return;
    busy = true;

    var sendBtn = doc.querySelector(".agent-input .btn");
    if (sendBtn) { sendBtn.disabled = true; }

    try {
      var out = doc.getElementById("ask-out");
      if (!out) return;

      var scope = decisionScope();
      var chipHtml = '<div class="agent-obj' + (scope.has ? "" : " muted") + '">' + esc(scope.text) + '</div>';
      if (!scope.has) {
        chipHtml += '<div class="notice warn agent-hint"><span class="tag">提示</span> 尚未运行决策：Agent 需要一笔交易建议作为上下文。</div>';
      }
      out.innerHTML = chipHtml +
        '<div class="agent-answer streaming">Agent 正在分析当前决策…<span class="cursor"></span></div>' +
        '<div class="agent-trace"></div>';

      var answerBox = out.querySelector(".agent-answer");
      var traceBox = out.querySelector(".agent-trace");
      var answer = "", blocked = false, degraded = false, finalized = false;
      var trace = [], genStep = null, guardInfo = null;

      function renderTrace() {
        traceBox.innerHTML = trace.map(function (st) {
          var icon, cls;
          if (st.status === "run") { icon = "○"; cls = "run"; }
          else if (st.status === "err") { icon = "✕"; cls = "err"; }
          else { icon = "✓"; cls = "ok"; }
          var meta = "";
          if (st.type === "tool" && st.status !== "run") {
            var bits = [];
            if (st.time) bits.push('<span class="trace-time">' + esc(st.time) + '</span>');
            if (st.badge) bits.push('<span class="trace-badge">' + st.badge + '</span>');
            if (bits.length) meta = '<span class="trace-meta">' + bits.join("") + '</span>';
          }
          return '<div class="trace-step"><span class="s ' + cls + '">' + icon + ' ' + esc(st.zh) + '</span>' + meta + '</div>';
        }).join("");
      }

      function onAgentStatus(d) {
        var label = d && d.label;
        if (label && answerBox && !answer && !blocked && !finalized) {
          answerBox.innerHTML = esc(label) + '<span class="cursor"></span>';
          answerBox.classList.add("streaming");
        }
      }

      function onToolStart(d) {
        var tool = d.tool || "";
        trace.push({ type: "tool", tool: tool, zh: toolZh(tool, d.label), status: "run",
                     t0: Date.now(), time: "", badge: "", args: argsTextFor(tool), raw: "", guard: "" });
        renderTrace();
      }

      function onToolResult(d) {
        var tool = d.tool || "";
        var last = null;
        for (var i = trace.length - 1; i >= 0; i--) {
          if (trace[i].type === "tool" && trace[i].tool === tool && trace[i].status === "run") { last = trace[i]; break; }
        }
        var st;
        if (last) {
          st = last;
          st.status = (d.status && d.status !== "ok") ? "err" : "done";
          st.time = fmtDuration(Date.now() - st.t0);
          st.badge = resultBadge(tool, d.result_summary);
          st.raw = d.result_summary || "";
          st.guard = d.status || "ok";
        } else {
          st = { type: "tool", tool: tool, zh: toolZh(tool, d.label), status: "done", time: "",
                 badge: resultBadge(tool, d.result_summary), args: argsTextFor(tool),
                 raw: d.result_summary || "", guard: d.status || "ok" };
          trace.push(st);
        }
        renderTrace();
      }

      function onAnswerDelta(d) {
        if (blocked || finalized || !answerBox) return;
        var txt = (d && d.text) || "";
        if (!txt) return;
        if (!genStep && trace.some(function (t) { return t.type === "tool"; })) {
          genStep = { type: "llm", zh: "生成解释…", status: "run" };
          trace.push(genStep);
          renderTrace();
        }
        answer += txt;
        answerBox.classList.add("streaming");
        answerBox.innerHTML = esc(answer) + '<span class="cursor"></span>';
      }

      function onGuard(d) {
        var pass = (d && d.status) === "PASS";
        guardInfo = { status: (d && d.status) || "", detail: (d && d.detail) || "" };
        if (pass) {
          trace.push({ type: "guard", zh: "一致性检查通过", status: "ok", guard: "PASS" });
        } else {
          blocked = true;
          trace.push({ type: "guard", zh: "一致性检查未通过（已拦截）", status: "err", guard: "BLOCKED", detail: guardInfo.detail });
          if (answerBox) {
            answerBox.className = "agent-answer err";
            answerBox.classList.remove("streaming");
            answerBox.innerHTML = '<div class="notice err"><span class="tag">已拦截 BLOCKED</span>' +
              '<div><b>一致性检查未通过</b>：Agent 回答与工具数据不一致，已拦截，不展示被拦截内容。请重新查询。</div></div>';
          }
        }
        renderTrace();
      }

      function onError(d) {
        var msg = (d && d.message) || "流式请求失败";
        if (answerBox) {
          answerBox.className = "agent-answer err";
          answerBox.innerHTML = '<div class="notice err"><span class="tag">出错</span><div>' + esc(msg) + '</div></div>';
        }
        appendTechTrace();   // 出错也保留已产生的真实工具轨迹
        finalized = true;    // 阻止 finalize 覆盖错误提示
      }

      function finalize() {
        if (finalized) return;
        finalized = true;
        degraded = degraded || isDegraded(answer);
        if (genStep) {
          genStep.status = "done";
          genStep.zh = degraded ? "降级：结构化结果摘要（LLM 未配置）" : "生成解释完成";
          renderTrace();
        }
        if (answerBox) {
          if (!blocked) {
            var biz = businessAnswer(answer);
            var html = esc(biz.lead);
            if (biz.json) {
              html += '<details class="detail"><summary>查看结构化结果 JSON</summary>' +
                      '<div class="detail-body"><pre class="tt-pre">' + esc(biz.json) + '</pre></div></details>';
            }
            answerBox.innerHTML = html || '<div class="empty">（无回答）</div>';
            answerBox.classList.remove("streaming");
            if (degraded) {
              answerBox.innerHTML = '<div class="notice warn"><span class="tag">降级模式 DEGRADED</span>' +
                '<div>LLM 未配置：以下为结构化工具结果摘要（非 LLM 解释）。配置 LLM_API_KEY 后即可启用解释。</div></div>' +
                answerBox.innerHTML;
            }
          }
        }
        appendTechTrace();
      }

      function appendTechTrace() {
        var html = techTraceHtml();
        if (html) out.insertAdjacentHTML("beforeend", html);
      }

      function techTraceHtml() {
        var rows = [];
        var n = 0;
        rows.push('<div class="tt-line muted">question: ' + esc(question) + '</div>');
        trace.forEach(function (st) {
          if (st.type === "tool") {
            n++;
            var rawHtml = st.raw
              ? '<details class="detail"><summary>Raw JSON（' + esc(st.tool) + '）</summary>' +
                '<div class="detail-body"><pre class="tt-pre">' + esc(prettyJson(st.raw)) + '</pre></div></details>'
              : "";
            rows.push('<div class="tt-line"><span class="tt-idx">' + n + '</span> <code>' + esc(st.tool) + '</code> ' +
              esc(st.zh) + ' · 参数：' + esc(st.args || "—") + ' · 耗时 ' + esc(st.time || "—") +
              ' · 状态 ' + esc(st.guard || st.status || "") + rawHtml + '</div>');
          } else if (st.type === "guard") {
            n++;
            rows.push('<div class="tt-line"><span class="tt-idx">' + n + '</span> guard <code>' + esc(st.guard || "") + '</code> ' +
              esc(st.zh) + (st.detail ? ' <span class="muted">（' + esc(st.detail) + '）</span>' : "") + '</div>');
          } else if (st.type === "llm") {
            n++;
            rows.push('<div class="tt-line"><span class="tt-idx">' + n + '</span> stage: llm ' + esc(st.zh) + '</div>');
          }
        });
        return '<details class="detail tech-trace"><summary>查看技术 Trace</summary>' +
               '<div class="detail-body">' + rows.join("") + '</div></details>';
      }

      function handleBlock(block) {
        var ev = "message", data = "";
        var lines = block.split("\n");
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
          else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
        }
        if (!data) return;
        var d = null;
        try { d = JSON.parse(data); } catch (e) { return; }
        switch (ev) {
          case "agent_status": onAgentStatus(d); break;
          case "tool_start": onToolStart(d); break;
          case "tool_result": onToolResult(d); break;
          case "answer_delta": onAnswerDelta(d); break;
          case "answer_done": finalize(); break;
          case "guard": onGuard(d); break;
          case "degraded": degraded = true; break;
          case "error": onError(d); break;
          default: break;
        }
      }

      var resp = await fetch("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question, decision_id: (M() && M().state && M().state.decision_id) || null })
      });
      if (!resp.ok) {
        var errData = null;
        try { errData = await resp.json(); } catch (e) { errData = null; }
        var em = (errData && (errData.message || (errData.error && errData.error.message))) || ("HTTP " + resp.status);
        onError({ message: em });
        finalize();
        return;
      }
      if (!resp.body || !resp.body.getReader) {
        var txt = await resp.text();
        answer = txt;
        finalize();
        return;
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buf = "", streamDone = false;
      while (!streamDone) {
        var r = await reader.read();
        streamDone = r.done;
        buf += decoder.decode(r.value || new Uint8Array(), { stream: !streamDone });
        var idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          var block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          handleBlock(block);
        }
      }
      if (!finalized) finalize();
    } catch (e) {
      var out2 = doc.getElementById("ask-out");
      if (out2) {
        var ab = out2.querySelector(".agent-answer");
        if (ab) ab.innerHTML = '<div class="notice err"><span class="tag">出错</span><div>' +
          esc((e && e.message) || "流式请求失败") + '</div></div>';
      }
    } finally {
      busy = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  /* ===================== init ===================== */
  function init() {
    ensureStyle();
    wireControls();
    renderQuickQuestions();
    _lastRevealed = !!(M() && M().state && M().state.revealed);
    resetOutput();
    /* 兼容旧版内联 onclick/onkeydown 引用 ask()；inline script 先执行，此处覆写可靠 */
    if (window.ask !== ask) window.ask = ask;
    window.MVPAgent = { init: init, ask: ask };

    /* 揭晓后启用「复盘」预置问题：监听 DOM 变化（reveal 会重渲染 #reveal-area） */
    if (typeof MutationObserver !== "undefined") {
      var rafPending = false;
      var mo = new MutationObserver(function () {
        if (rafPending) return;
        rafPending = true;
        requestAnimationFrame(function () { rafPending = false; syncQuickQuestions(); });
      });
      var body = doc.body;
      if (body) mo.observe(body, { childList: true, subtree: true });
    }
  }

  window.MVPAgent = { init: init, ask: ask };
})();
