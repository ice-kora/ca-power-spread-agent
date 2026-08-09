/* =========================================================================
 * static/mvp_evidence.js —— Agent C · Evidence / Case E / Cases
 * =========================================================================
 * V0.4 Phase 2 拆分渲染层：本文件只负责三段业务视图
 *   1) renderData(dec)     —— “本次决策依据”：top_features 已使用信息清单 + 数据血缘表
 *   2) renderEvidence(dec) —— “外部信息”：允许 ✓ / 拒绝 ✕（Case E → Revision 4 时间轴）
 *   3) renderCases(dec)    —— “类似历史案例”（最多 3 条；case_id 只进折叠审计）
 *
 * 唯一依赖 mvp_core.js 提供的共享全局 window.MVP：
 *   esc / fmt / sign / numCls / section / FEAT_ZH / utcToPt
 * 由 mvp_core.js 的 renderDecision 按顺序调用（Hero → Why → renderData →
 * renderEvidence → renderCases → Lock/Reveal → Audit）。
 *
 * 交易核心冻结：本文件只做展示，不改变任何模型 / 规则 / 时间门判定。
 * =========================================================================
 */
(function () {
  "use strict";

  const M = () => window.MVP || {};

  /* ---------- 展示用小工具（只做展示，不碰判定） ---------- */

  // 解析 UTC naive ISO 为毫秒（无时区字符串按 UTC 解释，保证相对比较稳定）
  function _utcTs(s) {
    if (!s) return NaN;
    const t = Date.parse(s);
    if (!isNaN(t)) return t;
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(s));
    if (!m) return NaN;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  }

  // a > b（均按 UTC naive 解释）；任一时间不可解析则返回 false
  function _utcGt(a, b) {
    const ta = _utcTs(a), tb = _utcTs(b);
    return !isNaN(ta) && !isNaN(tb) && ta > tb;
  }

  // 展示用 PT 时刻：优先 MVP.utcToPt；解析不出时回退预设文案（Case E 演示固定时间线）
  function _ptTime(iso, fb) {
    if (iso) {
      const out = typeof M().utcToPt === "function" ? M().utcToPt(iso) : "—";
      if (out && out !== "—") return out;
    }
    return fb || "—";
  }

  // 从 summary / source 里提取 GFS 起报周期（00Z/06Z/12Z/18Z）
  function _gfsCycle(text) {
    const m = /(?:GFS[^\d]*|^)(0?[0-2][0-9]Z)/i.exec(String(text || ""));
    return m ? m[1].toUpperCase() : "";
  }

  // 从 rejection_reason 提取机器码（如 INITIALIZATION_AFTER_CUTOFF）——只进技术详情
  function _reasonCode(r) {
    if (!r) return "";
    const m = /^([A-Z][A-Z_]+)/.exec(String(r).trim());
    return m ? m[1] : String(r);
  }

  // 业务名翻译（FEAT_ZH 由 mvp_core.js 提供；未登记则回退原始 feature 名）
  function _featZh(f) {
    const m = M();
    return (m.FEAT_ZH && m.FEAT_ZH[f]) ? m.FEAT_ZH[f] : f;
  }

  // 一条被拒证据的“初始化 · 可用 · 截止”元信息行（PT 展示）
  function _metaLine(e) {
    const m = M();
    return `<div class="d muted">初始化 ${m.esc(_ptTime(e.initialization_time, "—"))} · 可用 ${m.esc(_ptTime(e.available_at, "—"))} · 截止 ${m.esc(_ptTime(e.decision_cutoff, "—"))}</div>`;
  }

  // 技术详情折叠（reason_code / available_at / initialization_time /
  // available_at_source / decision_cutoff）。机器码只出现在这里，不进主业务文案。
  function _techDetails(e) {
    const m = M();
    const availShow = e.available_at ? m.esc(e.available_at) : "UNKNOWN / NOT PROVEN（不伪造）";
    return `<details class="detail"><summary>查看技术详情</summary><div class="detail-body">
      <div class="kv" style="grid-template-columns:1fr">
        <div class="item"><div class="k">reason_code</div><div class="v mono">${m.esc(_reasonCode(e.rejection_reason) || "UNKNOWN")}</div></div>
        <div class="item"><div class="k">拒绝说明 rejection_reason</div><div class="v mono">${m.esc(e.rejection_reason || "—")}</div></div>
        <div class="item"><div class="k">available_at（可用时间）</div><div class="v mono">${availShow}</div></div>
        <div class="item"><div class="k">initialization_time（初始化，UTC）</div><div class="v mono">${m.esc(e.initialization_time || "—")}</div></div>
        <div class="item"><div class="k">available_at_source</div><div class="v mono">${m.esc(e.available_at_source || "—")}</div></div>
        <div class="item"><div class="k">decision_cutoff（UTC）</div><div class="v mono">${m.esc(e.decision_cutoff || "—")}</div></div>
      </div>
    </div></details>`;
  }

  // Case E（Revision 4）：报价截止 vs GFS 运行时间 —— 时间轴 + 大字结论 + 技术详情
  function _caseEBox(e) {
    const m = M();
    const cycle = _gfsCycle(e.summary || e.source || "") || "18Z";
    const initPt = _ptTime(e.initialization_time, "11:00 PT");
    return `<div class="tg-stage">
      <div class="tg-grid">
        <div class="tg-col ok">
          <div class="col-t">✓ GFS 06Z · 截止前可用</div>
          <div class="tg-note">08:00 PT 前即可获得，通过时间门，可参与决策。</div>
        </div>
        <div class="tg-col err">
          <div class="col-t">✕ GFS ${m.esc(cycle)} · 截止后生成</div>
          <div class="tg-note">${m.esc(initPt)} 才开始初始化，晚于报价截止。</div>
        </div>
      </div>
      <div class="timeline">
        <div class="item ok">✓ GFS 06Z 08:00 PT</div>
        <div class="cut"></div>
        <div class="item err">✕ GFS ${m.esc(cycle)} ${m.esc(initPt)}</div>
      </div>
      <div class="tg-conclusion">它来晚了，所以没有使用</div>
      <div class="tg-note">该预测在报价截止后才开始生成，因此当时不可能被交易员获得。</div>
      ${_techDetails(e)}
    </div>`;
  }

  /* =======================================================================
   * 1. 本次决策依据（top_features + 数据血缘）
   * ======================================================================*/
  function renderData(dec) {
    const m = M();
    if (typeof m.section !== "function") return "";
    const feats = dec.top_features || [];

    const rows = feats.length
      ? feats.map((f) => {
          const zPart = (f.z !== null && f.z !== undefined)
            ? ` · 偏离历史 ${f.z > 0 ? "+" : ""}${m.fmt(f.z, 2)}σ`
            : "";
          return `<div class="ck-item"><span class="mark ok">✓</span><div class="body">
            <div class="t">${m.esc(_featZh(f.feature))}（<code>${m.esc(f.feature)}</code>）</div>
            <div class="d">当前值 ${m.fmt(f.value, 2)}${zPart}</div>
          </div></div>`;
        }).join("")
      : `<div class="empty">（本决策无可用特征统计）</div>`;

    const provRows = feats.length
      ? feats.map((f) => `<tr>
          <td><code>${m.esc(f.feature)}</code></td>
          <td class="num">${m.fmt(f.value, 2)}</td>
          <td>${m.esc(f.source || "")}</td>
          <td>${m.esc(f.source_type || "")}</td>
          <td>${m.esc(f.available_at || "—")}</td>
          <td>${m.esc(f.availability_basis || "—")}</td>
          <td>${f.decision_eligible ? "是" : "否"}</td>
        </tr>`).join("")
      : `<tr><td colspan="7" class="empty">（无特征血缘数据）</td></tr>`;

    return m.section("本次决策依据", "决策时点（10:00 PT）前可得的真实信息",
      `<div class="ck-list">${rows}</div>
       <div class="muted" style="font-size:12px;margin-top:6px">zσ 用于解释当前输入是否偏离历史水平，不代表严格的模型因果贡献。</div>
       <details class="detail"><summary>查看数据血缘</summary><div class="detail-body">
         <div class="tbl-wrap"><table class="tbl"><thead><tr>
           <th>特征 Feature</th><th>值 Value</th><th>来源 Source</th><th>类型</th>
           <th>可用时刻 available_at</th><th>可用性依据</th><th>决策可用</th>
         </tr></thead><tbody>${provRows}</tbody></table></div>
         <div class="muted" style="margin-top:6px">展示的 available_at 与时间门判定口径一致；滞后/历史特征以最晚可证上界表达，不显示伪精确时间戳。</div>
       </div></details>`);
  }

  /* =======================================================================
   * 2. 外部信息：允许 ✓ / 拒绝 ✕（Case E → 时间轴）
   * ======================================================================*/
  function renderEvidence(dec) {
    const m = M();
    if (typeof m.section !== "function") return "";
    const ev = dec.evidence || dec.evidences || {};
    const elig = ev.eligible || [];
    const rej = (ev.rejected && ev.rejected.length) ? ev.rejected : (ev.post_decision || []);

    const eligHtml = elig.length
      ? elig.map((e) => `<div class="ck-item"><span class="mark ok">✓</span><div class="body">
          <div class="t">${m.esc(e.source || "")}</div>
          <div class="d">${m.esc(e.summary || "")}</div>
          ${_metaLine(e)}
        </div></div>`).join("")
      : `<div class="empty">截止前无可用真实外部证据（不编造）。</div>`;

    const rejHtml = rej.length
      ? rej.map((e) => {
          const initAfter = _utcGt(e.initialization_time, e.decision_cutoff)
            || /INITIALIZATION_AFTER_CUTOFF/.test(e.rejection_reason || "");
          const reason = initAfter
            ? "该 GFS run 在交易截止时间之后才开始初始化，因此无论最终何时发布，都不可能被当时的交易员获得。"
            : (e.available_at
                ? "该信息在交易截止之后才可获得，因此未参与当时的决策。"
                : "该信息无法证明在截止前可获得（AVAILABILITY_NOT_PROVEN），因此未参与决策。");
          const extra = initAfter ? _caseEBox(e) : _techDetails(e);
          return `<div class="ck-item"><span class="mark no">✕</span><div class="body">
            <div class="t">${m.esc(e.source || "")}<span class="not-used">未参与决策 NOT USED</span></div>
            <div class="d">${m.esc(reason)}</div>
            ${_metaLine(e)}
            ${extra}
          </div></div>`;
        }).join("")
      : `<div class="empty">本决策未获取到被拒的外部信息。</div>`;

    return m.section("外部信息", "系统用了什么 / 拒绝了什么（Evidence Time Gate）",
      `<div style="font-weight:700;font-size:13.5px;margin-bottom:6px">本次允许使用的信息</div>
       <div class="ck-list">${eligHtml}</div>
       <div style="height:14px"></div>
       <div style="font-weight:700;font-size:13.5px;margin:14px 0 6px">被系统拒绝的信息 <span style="color:var(--text-faint);font-weight:400;font-size:12px">（只进复盘，不影响决策）</span></div>
       <div class="ck-list">${rejHtml}</div>`);
  }

  /* =======================================================================
   * 3. 类似历史案例（最多 3 条；case_id 只进折叠审计）
   * ======================================================================*/
  function renderCases(dec) {
    const m = M();
    if (typeof m.section !== "function") return "";
    const cases = (dec.top_cases || []).slice(0, 3);
    if (!cases.length) {
      return m.section("类似历史案例", "", `<div class='empty'>决策时点前无相似已结算案例。</div>`);
    }
    const list = cases.map((c) => {
      const decision = c.decision !== undefined ? c.decision : c.model_prediction;
      const outcome = c.outcome !== undefined ? c.outcome : c.actual_Return;
      const lessons = c.lessons || [];
      const lesson = c.lesson !== undefined ? c.lesson : (lessons[0] || c.why_correct_or_wrong || "");
      const pillCls = decision === "SELL" ? "SELL" : (decision === "BUY" ? "BUY" : "NO_TRADE");
      const outcomeHtml = (outcome === null || outcome === undefined)
        ? "—"
        : `${m.sign(outcome)}${m.fmt(outcome, 1)} $/MWh`;
      return `<div class="case-card"><div class="case-head">
        <div class="case-date">${m.esc(String(c.decision_date || "").slice(0, 10))} · ${m.esc(c.node || "")} · H${m.esc(c.hour)}</div>
        <div><span class="pill ${pillCls}">${m.esc(decision || "—")}</span>
          <span class="case-outcome ${m.numCls(outcome)}">${outcomeHtml}</span></div>
      </div>
      <div class="case-lesson">${m.esc(lesson || "—")}</div>
      <details class="detail"><summary>查看案例审计信息</summary><div class="detail-body">
        <div class="kv" style="grid-template-columns:1fr">
          <div class="item"><div class="k">case_id</div><div class="v mono">${m.esc(c.case_id || "—")}</div></div>
          <div class="item"><div class="k">case_available_at</div><div class="v mono">${m.esc(c.case_available_at || "—")}</div></div>
          <div class="item"><div class="k">expected_return</div><div class="v mono">${m.fmt(c.expected_return, 2)}</div></div>
        </div>
      </div></details>
    </div>`;
    }).join("");
    return m.section("类似历史案例", `找到 ${cases.length} 个类似已结算案例（as-of）`, list);
  }

  /* ============ 对外契约（mvp_core.js 调用） ============ */
  window.MVPEvidence = {
    renderData: renderData,
    renderEvidence: renderEvidence,
    renderCases: renderCases,
  };
})();
