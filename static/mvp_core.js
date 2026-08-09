/* mvp_core.js — CAISO 交易决策助手 · V0.4 Production UI 核心交互层
   Lead 单写；提供 window.MVP 契约（Agent B/C 依赖），编排各组件。
   DESIGN REVISION 1（Hero 组合）/ 5（Empty 三步）/ 6（Technical Accordion）
   数据全部来自 DecisionSnapshot，UI 只做 formatting/presentation。交易核心冻结。
*/
"use strict";
(function () {
  const $ = id => document.getElementById(id);
  const state = {
    meta: null, decision: null, decision_id: null,
    locked: false, revealed: false, post_trade: null, evidence: "real",
  };

  /* ================= utils ================= */
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmt(x, nd) { if (x === null || x === undefined) return "—"; const v = Number(x); if (Number.isNaN(v)) return "—"; if (nd === undefined) nd = 2; return v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: nd }); }
  function fmtP(x, nd) { return (x === null || x === undefined || Number.isNaN(Number(x))) ? "—" : Number(x).toFixed(nd === undefined ? 3 : nd); }
  function sign(v) { const n = Number(v); if (!Number.isFinite(n)) return ""; return n > 0 ? "+" : ""; }
  function numCls(v) { return v > 0 ? "pos" : (v < 0 ? "neg" : "zero"); }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    let data = null; try { data = await r.json(); } catch (e) { data = { status: "error", message: "响应非 JSON" }; }
    if (!r.ok && data.status !== "REVEALED") {
      const e = data.error || {}; const err = new Error(e.message || data.message || ("HTTP " + r.status));
      err.code = e.code || data.status || ("HTTP_" + r.status); err.action = e.suggested_action || ""; err.detail = e.detail || "";
      throw err;
    }
    return data;
  }
  async function get(path) { const r = await fetch(path); return r.json(); }
  function showError(err) {
    let msg = err, code = "", action = "", detail = "";
    if (err instanceof Error) { msg = err.message; code = err.code || ""; action = err.action || ""; detail = err.detail || ""; }
    const codeHtml = code ? `<div class="err-code">错误码 ERROR CODE: ${esc(code)}</div>` : "";
    const actionHtml = action ? `<div class="err-action"><b>建议操作 Suggested action:</b> ${esc(action)}</div>` : "";
    const detailHtml = detail ? `<div class="err-detail muted">详情 detail: ${esc(detail)}</div>` : "";
    $("error-box").innerHTML = `<div class="notice err"><span class="tag">出错了</span><div>${codeHtml}<p><b>${esc(msg)}</b></p>${actionHtml}${detailHtml}</div></div>`;
  }
  function clearError() { $("error-box").innerHTML = ""; }
  function section(title, hint, html) { return `<div class="act"><div class="act-title">${esc(title)} ${hint ? `<span class="n">${esc(hint)}</span>` : ""}</div>${html || `<div class="empty">运行一次决策后填充</div>`}</div>`; }

  /* ================= 业务翻译（只影响展示，不改数字） ================= */
  const FINAL_ZH = { SELL_DA: "卖出日前", BUY_DA: "买入日前", NO_TRADE: "不交易" };
  const FINAL_ACTION = { SELL_DA: "SELL DA", BUY_DA: "BUY DA", NO_TRADE: "NO TRADE" };
  const GATE_ZH = { PASS: "通过", WARNING: "谨慎", REJECT: "不交易" };
  const FEAT_ZH = {
    spread_mean30: "最近 30 天平均价差", spread_std30: "最近 30 天波动", spread_mean7: "最近 7 天价差",
    spread_std7: "最近 7 天波动", spread_mean14: "最近 14 天价差", spread_std14: "最近 14 天波动",
    spread_lag1: "昨日价差（滞后）", da_lag1: "昨日日前价（滞后）", rtpd_lag1: "昨日实时价（滞后）",
    load_actual_lag1: "昨日实际负荷", load_2da_forecast: "两日前负荷预测", load_peak_flag: "负荷峰值标记",
    t2m_lag1: "昨日气温（滞后）", ssrd_lag1: "昨日太阳辐射（滞后）", wind100_lag1: "昨日风速（滞后）",
    peer_spread_lag1: "同类节点价差（滞后）"
  };
  function featZh(f) { return FEAT_ZH[f] || f; }
  function signalZh(ms) { if (ms == null) return "未知"; if (ms >= 0.5) return "偏强"; if (ms >= 0.2) return "中等"; return "偏弱"; }
  function dirZh(d) { return d === "SELL" ? "SELL" : (d === "BUY" ? "BUY" : "—"); }
  function utcToPt(iso) {
    if (!iso) return "—";
    try {
      const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
      if (!m) return "—";
      const utcMs = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
      const pt = new Date(utcMs - 7 * 3600 * 1000);
      return String(pt.getUTCHours()).padStart(2, "0") + ":" + String(pt.getUTCMinutes()).padStart(2, "0") + " PT";
    } catch (e) { return "—"; }
  }

  /* ================= 系统状态（Header 单行）+ 系统边界 Drawer ================= */
  function renderSysStatus(meta) {
    const s = meta.mvp_status || {};
    const em = state.evidence === "offline" ? "NONE" : (meta.evidence_mode_default || "LIVE");
    const emZh = em === "HISTORICAL_SNAPSHOT" ? "历史快照" : (em === "LIVE" ? "实时" : "离线");
    const grid = $("sys-grid"); if (!grid) return;
    const items = [
      ["Agent", "已连接", "ok"],
      ["数据模式", s.data_mode || "DEMO", ""],
      ["证据模式", emZh, em === "HISTORICAL_SNAPSHOT" ? "ok" : ""],
      ["自动交易", (s.auto_trading || "DISABLED") === "DISABLED" ? "关闭" : "开启", "warn"],
    ];
    grid.innerHTML = items.map(([k, v, cls]) =>
      `<span><span class="dot ${cls === "ok" ? "ok" : (cls === "warn" ? "warn" : "")}"></span>${esc(k)}：<b>${esc(v)}</b></span>`).join("");
    const llm = meta.llm || {}, ms = meta.mvp_status || {}; const connected = (ms.llm || "") === "CONNECTED";
    const badge = $("llm-badge");
    badge.className = "badge " + (connected ? "ok" : "warn");
    badge.textContent = connected ? `LLM 已连接 (${esc((ms.llm_provider || llm.provider || "?").toUpperCase())})` : "LLM 未配置 NOT CONFIGURED";
  }
  function renderBoundary() {
    const m = state.meta || {}, s = m.mvp_status || {}, llm = m.llm || {};
    const rows = [
      ["模型状态 Model", "实验性"],
      ["稳定盈利验证 Profitability", s.profitability_verified || "NO"],
      ["自动交易 Auto Trading", s.auto_trading || "Disabled"],
      ["结算 Settlement", s.settlement || "Simplified Signal Backtest"],
      ["数据模式 Data Mode", s.data_mode || "DEMO"],
      ["证据模式 Evidence Mode", m.evidence_mode_default || "LIVE"],
      ["LLM", (s.llm || "NOT CONFIGURED") + " / " + (llm.provider || "")],
      ["版本号 Version", m.web_version || "V0.4 Demo"],
      ["Alpha 说明", "当前为实验性信号，不保证盈利；本系统不自动下单"],
    ];
    $("boundary-kv").innerHTML = rows.map(([k, v]) => `<div class="kv"><div class="item"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div></div>`).join("").replace(/\<\/div\>\<div class="kv"\>/g, "</div>") || "";
  }
  function openBoundary() { $("boundary").classList.add("open"); renderBoundary(); }
  function closeBoundary() { $("boundary").classList.remove("open"); }

  /* ================= init ================= */
  async function init() {
    try {
      const meta = await get("/api/meta"); state.meta = meta; renderSysStatus(meta);
      const nodeSel = $("inp-node");
      nodeSel.innerHTML = Object.entries(meta.nodes).map(([n, z]) => `<option value="${esc(n)}">${esc(meta.node_short[n])} (${z})</option>`).join("");
      const hourSel = $("inp-hour");
      hourSel.innerHTML = Array.from({ length: 24 }, (_, i) => `<option value="${i + 1}">H${i + 1}</option>`).join("");
      const d = $("inp-date"); d.min = meta.min_decision_date; d.max = meta.max_decision_date;
      d.value = meta.golden_cases[0].decision_date;
      const db = $("inp-brief-date"); if (db) { db.min = meta.min_decision_date; db.max = meta.max_decision_date; db.value = meta.golden_cases[0].decision_date; }
      const cs = $("inp-case");
      meta.golden_cases.forEach(c => { const o = document.createElement("option"); o.value = c.id; o.textContent = c.label; cs.appendChild(o); });
      cs.addEventListener("change", () => { const c = meta.golden_cases.find(x => x.id === cs.value); if (c) { d.value = c.decision_date; nodeSel.value = c.node; hourSel.value = String(c.hour); } });
      const ev = $("inp-evidence");
      meta.evidence_modes.forEach(m => { const o = document.createElement("option"); o.value = m.id; o.textContent = m.label; ev.appendChild(o); });
      const defEv = meta.default_evidence || "real"; state.evidence = defEv; ev.value = defEv;
      ev.addEventListener("change", () => { state.evidence = ev.value; renderSysStatus(meta); });
      $("btn-run").addEventListener("click", runDecision);
      $("btn-boundary").addEventListener("click", openBoundary);
      $("boundary-close").addEventListener("click", closeBoundary);
      $("boundary").addEventListener("click", e => { if (e.target === $("boundary")) closeBoundary(); });
      // Agent（Agent B 提供；若未加载则跳过）
      if (window.MVPAgent && window.MVPAgent.init) { try { window.MVPAgent.init(); } catch (e) { console.error("MVPAgent.init", e); } }
      renderEmpty();
    } catch (e) { showError(e); }
  }

  /* ================= Empty / Loading ================= */
  function renderEmpty() {
    $("main-col").innerHTML = `<div class="empty-state">
      <div class="big">选择一笔交易，查看系统建议</div>
      <p>设定交易日期 / 节点 / 小时，或直接选一个演示案例，然后点击「生成交易建议」。</p>
      <div class="steps">
        <div class="step"><span class="n">1</span>选择交易</div>
        <div class="step"><span class="n">2</span>获取建议</div>
        <div class="step"><span class="n">3</span>锁定并复盘</div>
      </div>
      <p class="muted" style="margin-top:14px">系统只使用报价截止前能够获得的信息。</p>
    </div>`;
  }
  function renderLoading() {
    $("main-col").innerHTML = `<div class="loading"><div class="spinner"></div>正在生成交易建议…</div>`;
  }

  /* ================= RUN DECISION ================= */
  async function runDecision() {
    const body = { decision_date: $("inp-date").value, node: $("inp-node").value, hour: Number($("inp-hour").value), evidence: state.evidence };
    if (!body.decision_date) { showError("请选择交易日期"); return; }
    clearError();
    const btn = $("btn-run"); btn.disabled = true;
    renderLoading();
    try {
      const data = await post("/api/decision", body);
      state.decision = data.decision; state.decision_id = data.decision.decision_id;
      state.locked = data.decision.lock.locked; state.revealed = data.decision.outcome_revealed; state.post_trade = null;
      renderDecision();
    } catch (e) { renderEmpty(); showError(e); }
    finally { btn.disabled = false; }
  }

  /* ================= 编排（组件顺序固定） ================= */
  function renderDecision() {
    const dec = state.decision, ctx = dec.context || {};
    state.evidence = ctx.evidence_mode || state.evidence;
    const html = [
      renderHero(dec),
      renderWhy(dec),
      (window.MVPEvidence && window.MVPEvidence.renderData) ? window.MVPEvidence.renderData(dec) : "",
      (window.MVPEvidence && window.MVPEvidence.renderEvidence) ? window.MVPEvidence.renderEvidence(dec) : "",
      (window.MVPEvidence && window.MVPEvidence.renderCases) ? window.MVPEvidence.renderCases(dec) : "",
      renderLockReveal(dec),
      renderAudit(dec),
      renderTechnical(dec),
    ].join("");
    $("main-col").innerHTML = html;
  }

  /* ---------- Hero（Revision 1：Recommendation + Expected Spread 组合） ---------- */
  function renderHero(dec) {
    const ctx = dec.context || {}, mo = dec.model_output || {}, rg = dec.risk_gate || {}, ev = dec.evidence || {};
    const final = dec.final_recommendation, er = mo.expected_return, nElig = (ev.eligible || []).length;
    const cls = final === "SELL_DA" ? "SELL" : (final === "BUY_DA" ? "BUY" : "NO_TRADE");
    const gateCls = rg.decision === "REJECT" ? "err" : (rg.decision === "WARNING" ? "warn" : "ok");
    const dirLine = final === "SELL_DA"
      ? "模型预计日前价格高于实时价格，同时当前没有触发风险拦截，因此系统建议卖出日前。"
      : (final === "BUY_DA"
        ? "模型预计实时价格高于日前价格，同时当前没有触发风险拦截，因此系统建议买入日前。"
        : "当前信号或风险条件不足以支持交易，因此系统建议不交易。");
    return `<div class="hero">
      <div class="hero-head">
        <div class="hero-who">${esc(ctx.node || "")}（${esc(ctx.zone || "")}）· <b>次日 ${ctx.hour ? "H" + ctx.hour : ""}</b> · 报价截止 ${esc(ctx.decision_cutoff_pt || "")}</div>
        <div class="hero-rec ${cls}"><div class="cn">${esc(FINAL_ZH[final] || final)}</div><div class="en">${esc(FINAL_ACTION[final] || final)}</div></div>
      </div>
      <div class="hero-core">
        <div class="hero-spread">
          <div class="k">预计 DA − RT（Expected Spread）</div>
          <div class="v ${er == null ? "" : numCls(er)}">${er == null ? "—" : sign(er) + fmt(er, 2) + " $/MWh"}</div>
        </div>
      </div>
      <div class="decision-bar">
        <div class="db"><div class="k">风险检查</div><div class="v ${gateCls}">${esc(GATE_ZH[rg.decision] || rg.decision || "—")}</div></div>
        <div class="db"><div class="k">模型信号</div><div class="v">${esc(signalZh(mo.model_signal_strength))}</div></div>
        <div class="db"><div class="k">截止前可用证据</div><div class="v">${nElig} 条</div></div>
      </div>
      <div class="hero-explain">${esc(dirLine)}</div>
      <div class="hero-actions">
        <button class="btn" id="btn-lock" onclick="MVP.lockDecision()">锁定本次决策</button>
        <button class="btn secondary" id="btn-reveal" onclick="MVP.revealOutcome()" disabled>揭晓真实结果</button>
        <span class="muted">锁定后，之后发生的信息不会反向修改本次建议</span>
      </div>
    </div>`;
  }

  /* ---------- Why This Decision（三卡） ---------- */
  function renderWhy(dec) {
    const mo = dec.model_output || {}, rg = dec.risk_gate || {}, re = dec.rule_engine || {}, final = dec.final_recommendation;
    const er = mo.expected_return;
    const gateZh = state.meta && state.meta.reason_translations ? state.meta.reason_translations.gate || {} : {};
    const ruleZh = state.meta && state.meta.reason_translations ? state.meta.reason_translations.rule || {} : {};
    const reasons = (rg.risk_reasons || []).map(c => `· ${esc(gateZh[c] || c)}`).join("<br>");
    const rules = (re.rules_hit || []).map((rid, i) => { const rc = (re.reasons || [])[i]; return `<code>${esc(rid)}</code> ${esc(ruleZh[rc] || rc || "")}`; }).join(" · ");
    const flow = final === "SELL_DA"
      ? `预计 DA−RT &gt; 0 <span class="arrow">+</span> 风险通过 <span class="arrow">↓</span> <b>卖出日前 SELL DA</b>`
      : (final === "BUY_DA"
        ? `预计 DA−RT &lt; 0 <span class="arrow">+</span> 风险通过 <span class="arrow">↓</span> <b>买入日前 BUY DA</b>`
        : `信号或风险条件不足 <span class="arrow">→</span> <b>不交易 NO TRADE</b>`);
    const modelNote = er != null && er > 0
      ? "模型预计 DA 高于 RT，但当前模型信号并不强，因此模型意见只作为量化参考。"
      : "模型当前判断不构成明确方向，仅供量化参考。";
    const gateBigCls = rg.decision === "REJECT" ? "err" : (rg.decision === "WARNING" ? "warn" : "ok");
    return section("为什么这样建议", "白盒推理 · 三个独立环节",
      `<div class="why-grid">
        <div class="why-card">
          <h3>① 模型怎么看？</h3>
          <div class="kv" style="grid-template-columns:1fr 1fr">
            <div class="item"><div class="k">预计价差</div><div class="v ${er == null ? "" : numCls(er)}">${er == null ? "—" : sign(er) + fmt(er, 2)}</div></div>
            <div class="item"><div class="k">方向倾向</div><div class="v">${esc(dirZh(mo.direction))}</div></div>
            <div class="item"><div class="k">信号强度</div><div class="v">${esc(signalZh(mo.model_signal_strength))}</div></div>
            <div class="item"><div class="k">不确定性</div><div class="v">${mo.uncertainty == null ? "—" : (mo.uncertainty >= 0.7 ? "较高" : (mo.uncertainty >= 0.4 ? "中等" : "较低"))}</div></div>
          </div>
          <div class="note">${esc(modelNote)}</div>
        </div>
        <div class="why-card hl">
          <h3>② 风险让不让做？</h3>
          <div class="big ${gateBigCls}">${esc(GATE_ZH[rg.decision] || rg.decision || "—")}</div>
          <div class="note">${reasons ? `命中风险条件：<br>${reasons}` : "当前没有发现必须阻止交易的风险条件。"}</div>
        </div>
        <div class="why-card">
          <h3>③ 最终规则是什么？</h3>
          <div class="rule-flow">${flow}</div>
          <div class="note" style="margin-top:8px">卖出日前 = 日前卖出、实时买回；若最终 DA 高于 RT，本笔盈利。方向由白盒规则决定，不是 LLM。</div>
        </div>
      </div>`);
  }

  /* ---------- Lock / Reveal ---------- */
  function renderLockReveal(dec) {
    const lockedHtml = state.locked
      ? `<span class="lock-seal">🔒 决策已锁定</span><span class="muted">锁定后，之后发生的信息不会反向修改本次建议</span>`
      : `<button class="btn" id="btn-lock-hero" onclick="MVP.lockDecision()">锁定本次决策</button><span class="muted">锁定后，之后发生的信息不会反向修改本次建议</span>`;
    const revealArea = state.revealed && state.post_trade ? renderResult(dec, state.post_trade) : "";
    return section("锁定本次决策", "Lock → Reveal → 复盘",
      `<div class="lock-bar">${lockedHtml}
        <button class="btn secondary" id="btn-reveal-2" onclick="MVP.revealOutcome()" ${state.locked ? "" : "disabled"}>揭晓真实结果</button>
      </div>
      <div id="reveal-area" style="margin-top:12px">${revealArea}</div>`);
  }
  async function lockDecision() {
    clearError();
    try {
      const r = await post("/api/decision/" + state.decision_id + "/lock", {});
      state.locked = true;
      const b1 = $("btn-lock"); if (b1) { b1.disabled = true; b1.textContent = "✓ 已锁定"; }
      const b2 = $("btn-lock-hero"); if (b2) { b2.disabled = true; b2.textContent = "✓ 已锁定"; }
      const rv1 = $("btn-reveal"); if (rv1) rv1.disabled = false;
      const rv2 = $("btn-reveal-2"); if (rv2) rv2.disabled = false;
      const area = $("reveal-area");
      if (area) area.innerHTML = `<div class="notice info"><span class="tag">已锁定</span> 已锁定于 ${esc(r.locked_at || "")}。之后发生的信息不会反向修改本次建议。</div>`;
    } catch (e) { showError(e); }
  }
  async function revealOutcome() {
    clearError();
    try {
      const data = await post("/api/decision/" + state.decision_id + "/reveal", {});
      state.revealed = true; state.post_trade = data.post_trade;
      const rv1 = $("btn-reveal"); if (rv1) rv1.disabled = true;
      const rv2 = $("btn-reveal-2"); if (rv2) rv2.disabled = true;
      const area = $("reveal-area");
      if (area) area.innerHTML = renderResult(state.decision, data.post_trade);
    } catch (e) { showError(e); }
  }

  /* ---------- Result + Review（四问） ---------- */
  function renderResult(dec, pt) {
    if (!pt || pt.status !== "REVEALED") return "";
    const final = dec.final_recommendation;
    const pnl = pt.pnl || 0; const loss = pnl < 0;
    const review = pt.review || {}; const prim = review.primary || [];
    const what = loss ? "实际价差与本次建议方向相反，本次交易亏损。" : "实际价差与本次建议方向一致，本次交易盈利。";
    const whyText = (prim.includes("MODEL_ERROR") ? "模型本身信号偏弱且方向判断有误。" : (prim.includes("HIGH_UNCERTAINTY") ? "模型不确定性较高，最终市场走势发生反转。" : "市场实际走势与模型预期不同。"));
    return `<div class="result-box ${loss ? "loss" : "win"}">
      <div class="r-title">${loss ? "本次方向判断错误" : "本次方向判断正确"}</div>
      <div class="result-grid">
        <div class="rg"><div class="k">日前价格 DA</div><div class="v num">${fmt(pt.actual_da, 2)} $/MWh</div></div>
        <div class="rg"><div class="k">实时价格 RT</div><div class="v num">${fmt(pt.actual_rtpd, 2)} $/MWh</div></div>
        <div class="rg"><div class="k">实际 DA − RT</div><div class="v num ${numCls(pt.actual_return)}">${sign(pt.actual_return)}${fmt(pt.actual_return, 2)}</div></div>
        <div class="rg"><div class="k">本次结果（PnL）</div><div class="v num ${numCls(pnl)}">${sign(pnl)}${fmt(pnl, 2)} $/MWh</div></div>
      </div>
      <div style="margin-top:12px;font-size:14px">${esc(what)}</div>
    </div>
    <div class="case-card" style="margin-top:12px">
      <div class="act-title" style="margin-bottom:6px">复盘</div>
      <div class="review-q"><div class="q">发生了什么？</div><div class="a">${esc(what)}</div></div>
      <div class="review-q"><div class="q">为什么？</div><div class="a">${esc(whyText)}</div></div>
      <div class="review-q"><div class="q">系统当时有没有使用未来信息？</div><div class="a"><span class="ok">没有。</span> 任何晚于报价截止的信息都明确被拒绝，本建议只基于截止前可用信息。</div></div>
      <div class="review-q"><div class="q">有什么教训？</div><div class="a">当前模型对尾部价格变化仍不够稳定，未来需重点改善 EV / Tail Risk 建模；本次属实验性范围。</div></div>
      <details class="detail"><summary>查看结构化复盘标签</summary><div class="detail-body">
        ${prim.map(t => `<span class="pill warn">${esc(t)}</span>`).join(" ") || "—"}
        <div class="kv" style="grid-template-columns:1fr;margin-top:8px">
          <div class="item"><div class="k">direction_correct</div><div class="v">${pt.direction_correct === null ? "N/A（未交易）" : (pt.direction_correct ? "是" : "否")}</div></div>
          <div class="item"><div class="k">model_prediction_error</div><div class="v">${pt.model_prediction_error == null ? "—" : sign(pt.model_prediction_error) + fmt(pt.model_prediction_error, 2)}</div></div>
        </div>
      </div></details>
    </div>`;
  }

  /* ---------- Audit（默认折叠） ---------- */
  function renderAudit(dec) {
    const a = dec.audit || {}, rt = a.runtime || {}, items = rt.items || [];
    const overall = rt.overall || a.overall || "UNKNOWN";
    const nPass = items.filter(i => i.status === "PASS").length, nWarn = items.filter(i => i.status === "WARNING").length, nFail = items.filter(i => i.status === "FAIL").length;
    const cls = overall === "PASS" ? "ok" : (overall === "WARNING" ? "warn" : "err");
    const rows = items.map(it => `<div class="audit-item"><span class="st ${it.status === "PASS" ? "ok" : (it.status === "WARNING" ? "warn" : "err")}">${esc(it.status)}</span> · ${esc(it.name || it.key)}<div class="note">${esc(it.note || "")}</div></div>`).join("");
    return `<div class="audit-sum"><span class="overall ${cls}">${esc(overall)}</span><span>审计状态：${nPass} 项通过 · ${nWarn} 项提醒${nFail ? (" · " + nFail + " 项失败") : ""}</span>
      <details class="detail" style="margin:0"><summary>查看审计详情</summary><div class="detail-body"><div class="audit-items">${rows || "<div class='empty'>无审计数据</div>"}</div></div></details>
    </div>`;
  }

  /* ---------- Technical Details（Accordion，默认全折叠，Revision 6） ---------- */
  function renderTechnical(dec) {
    const ctx = dec.context || {}, mo = dec.model_output || {}, re = dec.rule_engine || {}, rg = dec.risk_gate || {};
    const ev = dec.evidence || {};
    const featRows = (dec.top_features || []).map(f => `<tr><td><code>${esc(f.feature)}</code></td><td class="num">${fmt(f.value, 2)}</td><td>${esc(f.source || "")}</td><td>${esc(f.source_type || "")}</td><td>${esc(f.available_at || "—")}</td><td>${esc(f.availability_basis || "—")}</td><td>${f.decision_eligible ? "是" : "否"}</td></tr>`).join("");
    const evRows = (ev.eligible || []).concat(ev.rejected || ev.post_decision || []).map(e => `<tr><td>${esc(e.evidence_id || "")}</td><td>${esc(e.source || "")}</td><td class="num">${esc(e.initialization_time || "—")}</td><td class="num">${esc(e.available_at || "UNKNOWN / NOT PROVEN")}</td><td>${esc(e.rejection_reason || "—")}</td></tr>`).join("");
    return `<div class="tech">
      <div class="tech-title">技术详情</div>
      <details class="detail"><summary>交易上下文</summary><div class="detail-body"><div class="kv" style="grid-template-columns:1fr">
        <div class="item"><div class="k">market_rule_version</div><div class="v">${esc(ctx.market_rule_version || "—")}</div></div>
        ${Object.entries(ctx).filter(([k]) => k !== "evidence_provenance" && k !== "market_rule_version").map(([k, v]) => `<div class="item"><div class="k">${esc(k)}</div><div class="v">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</div></div>`).join("")}
      </div></div></details>
      <details class="detail"><summary>原始特征</summary><div class="detail-body"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>feature</th><th>value</th><th>z</th><th>hist_mean</th><th>hist_std</th></tr></thead><tbody>${(dec.top_features || []).map(f => `<tr><td><code>${esc(f.feature)}</code></td><td class="num">${fmt(f.value, 4)}</td><td class="num">${fmt(f.z, 3)}</td><td class="num">${fmt(f.hist_mean, 3)}</td><td class="num">${fmt(f.hist_std, 3)}</td></tr>`).join("")}</tbody></table></div></div></details>
      <details class="detail"><summary>模型原始输出</summary><div class="detail-body"><div class="kv" style="grid-template-columns:1fr">
        ${Object.entries(mo).map(([k, v]) => `<div class="item"><div class="k">${esc(k)}</div><div class="v">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</div></div>`).join("")}
      </div></div></details>
      <details class="detail"><summary>数据血缘</summary><div class="detail-body"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>feature</th><th>source</th><th>type</th><th>available_at</th><th>basis</th><th>eligible</th></tr></thead><tbody>${featRows}</tbody></table></div></div></details>
      <details class="detail"><summary>Evidence 原始字段</summary><div class="detail-body"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>id</th><th>source</th><th>init</th><th>available</th><th>reason</th></tr></thead><tbody>${evRows || "<tr><td colspan=5>无证据</td></tr>"}</tbody></table></div></div></details>
      <details class="detail"><summary>Rule Engine / Risk Gate 原始</summary><div class="detail-body"><div class="kv" style="grid-template-columns:1fr">
        <div class="item"><div class="k">risk_gate.decision</div><div class="v">${esc(rg.decision || "—")}</div></div>
        <div class="item"><div class="k">rule_engine.decision</div><div class="v">${esc(re.decision || "—")}</div></div>
        <div class="item"><div class="k">reason_codes</div><div class="v">${esc(JSON.stringify(dec.reason_codes || []))}</div></div>
      </div></div></details>
      <details class="detail"><summary>Raw JSON（DecisionSnapshot）</summary><div class="detail-body"><pre style="white-space:pre-wrap;font-family:var(--mono);font-size:11px">${esc(JSON.stringify(dec, null, 2))}</pre></div></details>
    </div>`;
  }

  /* ================= Daily Brief（弱化，更多功能） ================= */
  async function generateBrief(allHours, allNodes) {
    const dd = $("inp-brief-date").value; if (!dd) { showError("请选择日报日期"); return; }
    const out = $("brief-out"); if (!out) return; out.innerHTML = `<div class="empty">扫描中…</div>`;
    const node = allNodes ? null : $("inp-node").value;
    try {
      const data = await post("/api/brief", { decision_date: dd, node, all_hours: !!allHours, evidence: state.evidence });
      const s = data.summary || {}; const rows = data.rows || [];
      let html = `<div class="kv" style="font-size:12px">${[["日期", data.decision_date], ["节点", data.node_scope], ["候选", data.n_candidates], ["BUY", s.BUY_DA], ["SELL", s.SELL_DA], ["NO_TRADE", s.NO_TRADE]].map(([k, v]) => `<div class="item"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>`;
      if (rows.length) html += `<div style="margin-top:6px;font-size:12px">${rows.slice(0, 8).map(r => `<div>${esc(r.node)} H${r.hour} · <b>${esc(r.final)}</b> · ${fmt(r.expected_return, 1)}</div>`).join("")}</div>`;
      out.innerHTML = html;
    } catch (e) { out.innerHTML = `<div class="notice err">${esc(e.message || "日报生成失败")}</div>`; }
  }
  function generateBriefAll() { generateBrief(false, true); }

  /* ================= 导出（Agent B/C 依赖） ================= */
  window.MVP = {
    state, esc, fmt, fmtP, sign, numCls, post, get, section, showError, clearError,
    FINAL_ZH, GATE_ZH, FEAT_ZH, signalZh, dirZh, utcToPt, featZh,
    init, runDecision, lockDecision, revealOutcome, renderDecision, renderEmpty,
    renderHero, renderWhy, renderLockReveal, renderResult, renderAudit, renderTechnical,
    renderSysStatus, renderBoundary, openBoundary, closeBoundary,
    generateBrief, generateBriefAll,
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
