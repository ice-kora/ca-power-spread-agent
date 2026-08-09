/* =========================================================================
 * static/md_render.js —— Markdown Renderer (Agent B · V0.4.1)
 * =========================================================================
 * 供 Agent 前端使用：把 LLM 返回的 markdown 渲染为已清洗的 HTML。
 *   1) markdown-it 渲染（vendor 本地化，无 CDN 依赖）
 *   2) DOMPurify sanitize（必须清洗，禁止执行 raw HTML / 事件属性 / javascript: URL）
 *   3) 表格包一层 .md-table-wrap（overflow-x:auto，不导致整页横向滚动）
 *   4) 组件自带最小表格样式（幂等注入，不依赖外部 CSS）
 *
 * 依赖本地化：static/vendor/markdown-it.min.js（13.0.2）、static/vendor/dompurify.min.js（3.4.13）
 * 浏览器全局：window.markdownit / window.DOMPurify（由 mvp_index.html 先行加载）
 *
 * 导出 API：
 *   MDRender.render(mdText)      -> sanitizedHtml 字符串（已 DOMPurify 清洗）
 *   MDRender.formatNumbers(text) -> text（业务长小数 -> 2 位小数；概率 -> 百分比；
 *                                      不碰日期 / 时间戳 / ID / 版本号 / hash / 行内代码 / 代码块）
 *
 * 交易核心冻结：本文件只做展示渲染，不改变任何模型 / 规则 / 时间门判定。
 * =========================================================================
 */
(function (factory) {
  "use strict";
  if (typeof module === "object" && typeof module.exports === "object") {
    // Node / CommonJS（测试环境）：直接 require 本地 vendor
    module.exports = factory(
      require("./vendor/markdown-it.min.js"),
      require("./vendor/dompurify.min.js")
    );
  } else if (typeof window !== "undefined") {
    // 浏览器：依赖 mvp_index.html 提前引入的 vendor 全局
    window.MDRender = factory(window.markdownit, window.DOMPurify);
  } else {
    var g = (typeof globalThis !== "undefined") ? globalThis : this;
    g.MDRender = factory(g.markdownit, g.DOMPurify);
  }
})(function (markdownit, DOMPurify) {
  "use strict";

  /* ---------- 组件自带表格样式（幂等注入，只在浏览器执行） ---------- */

  var TABLE_CSS =
    ".md-table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;}" +
    ".md-table-wrap table{border-collapse:collapse;border-spacing:0;width:max-content;min-width:100%;" +
      "font-size:12.5px;line-height:1.5;}" +
    ".md-table-wrap th,.md-table-wrap td{border:1px solid rgba(128,128,128,.35);" +
      "padding:4px 10px;text-align:left;vertical-align:top;}" +
    ".md-table-wrap thead th{background:rgba(128,128,128,.12);font-weight:600;white-space:nowrap;}" +
    ".md-table-wrap tbody tr:nth-child(even){background:rgba(128,128,128,.06);}";

  function ensureCss() {
    if (typeof document === "undefined") return;
    if (document.getElementById("md-render-css")) return;
    var st = document.createElement("style");
    st.id = "md-render-css";
    st.textContent = TABLE_CSS;
    (document.head || document.documentElement).appendChild(st);
  }

  /* ---------- 基础转义（fallback 用） ---------- */

  function escHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ---------- markdown-it 实例（惰性单例） ---------- */

  var _md = null;
  function getMd() {
    if (_md) return _md;
    if (typeof markdownit !== "function") return null; // vendor 缺失 -> 走 fallback
    var md = markdownit({
      html: false,       // 解析器不产出 raw HTML 元素（纵深防御；DOMPurify 仍是最终闸门）
      linkify: true,     // 自动把 URL 变成链接
      typographer: false,
      breaks: true       // 支持换行：单个 \n -> <br>
    });
    // 表格包一层可横向滚动的容器（不导致整页横向滚动）
    md.renderer.rules.table_open = function () {
      return '<div class="md-table-wrap"><table>';
    };
    md.renderer.rules.table_close = function () {
      return "</table></div>";
    };
    _md = md;
    return md;
  }

  /* ---------- sanitize：DOMPurify 为准，缺失/异常时返回 null（调用方走受限渲染器） ---------- */

  function sanitize(html) {
    var p = (typeof DOMPurify !== "undefined" && DOMPurify && typeof DOMPurify.sanitize === "function") ? DOMPurify : null;
    if (p) {
      try {
        return p.sanitize(html, {
          USE_PROFILES: { html: true },
          FORBID_TAGS: [
            "style", "form", "input", "button", "select", "textarea",
            "iframe", "object", "embed", "script", "noscript", "template"
          ],
          FORBID_ATTR: ["style", "srcdoc"],
          ALLOW_DATA_ATTR: true
        });
      } catch (e) {
        // DOMPurify 在无 window 的 Node 环境会抛错；交给受限渲染器兜底
      }
    }
    return null; // DOMPurify 不可用 -> 调用方使用受限渲染器（绝无任何元素可执行）
  }

  /* ---------- 受限渲染器 fallback（vendor 缺失时） ---------- */

  function renderFallback(text) {
    // 只做：段落 + 换行 + 转义；保证任何输入都安全
    return String(text)
      .split(/\n\s*\n/)
      .map(function (para) {
        return "<p>" + escHtml(para).replace(/\n/g, "<br>") + "</p>";
      })
      .join("");
  }

  /* ---------- render(mdText) -> sanitizedHtml ---------- */

  function render(mdText) {
    ensureCss();
    var text = (mdText == null) ? "" : String(mdText);
    var md = getMd();
    if (!md) return renderFallback(text);
    var html;
    try {
      html = md.render(text);
    } catch (e) {
      return renderFallback(text);
    }
    var clean = sanitize(html);
    return (clean === null) ? renderFallback(text) : clean;
  }

  /* ---------- 数值业务化 formatNumbers(text) -> text ---------- */

  // 只命中"长小数"：6 位及以上小数位；前后不得紧贴单词字符或小数点
  // （防止误伤 版本号 x.1.2.3 / ID 前缀 id.123 / 十六进制片段）
  var RE_LONG_DEC = /(?<![\w.])-?\d+\.\d{6,}(?!\d)/g;

  function looksLikeDateOrTime(before, after) {
    // ISO / 日期前缀：2026-08-09、2026/08/09、2026-08-09T10:30:00
    if (/\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(before)) return true;
    // 日期紧贴本数字（小数吃掉日字段）：2026-08-09.123456 -> before 止于 2026-08-
    if (/\d{4}[-/]\d{1,2}[-/]$/.test(before)) return true;
    // 时钟时间前缀：:10、:10:30、10:30:00（紧贴在本数字之前）
    if (/\d{1,2}:\d{2}(:\d{2})?$/.test(before)) return true;
    // 时区后缀：00.123456Z / 00.123456+08:00
    if (/^[zZ]/.test(after)) return true;
    if (/^[+-]\d{2}:\d{2}/.test(after)) return true;
    // 十六进制 hash 片段接续（前/后都是 hex 字符 -> 视为标识符一部分）
    if (/[0-9a-fA-F]$/.test(before) && /^[0-9a-fA-F]/.test(after)) return true;
    return false;
  }

  function fmtPlain(line) {
    return line.replace(RE_LONG_DEC, function (m, off, full) {
      var before = full.slice(Math.max(0, off - 80), off);
      var after = full.slice(off + m.length, off + m.length + 30);
      if (looksLikeDateOrTime(before, after)) return m; // 不碰日期/时间戳/ID/hash
      var v = parseFloat(m);
      if (v >= 0 && v <= 1) {
        // 概率：0.234567... -> 23%；过小则保留 2 位百分比避免误导成 0%
        var p = v * 100;
        var pr = Math.round(p);
        return (pr >= 1) ? (pr + "%") : (p.toFixed(2) + "%");
      }
      return v.toFixed(2); // 普通业务数值 -> 2 位小数
    });
  }

  // 行内代码（反引号跨度）内部不格式化
  function fmtInline(line) {
    if (line.indexOf("`") === -1) return fmtPlain(line);
    var parts = line.split(/(`+)/);
    var inCode = false;
    var out = "";
    for (var i = 0; i < parts.length; i++) {
      if (/^`+$/.test(parts[i])) {
        inCode = !inCode;
        out += parts[i];
      } else {
        out += inCode ? parts[i] : fmtPlain(parts[i]);
      }
    }
    return out;
  }

  function formatNumbers(text) {
    if (text == null) return "";
    var s = String(text);
    var lines = s.split("\n");
    var inFence = false;
    for (var i = 0; i < lines.length; i++) {
      if (/^\s*(`{3,}|~{3,})/.test(lines[i])) { // 围栏代码块开关
        inFence = !inFence;
        continue; // 代码块围栏行本身不动
      }
      if (inFence) continue; // 代码块内部保持原样
      lines[i] = fmtInline(lines[i]);
    }
    return lines.join("\n");
  }

  /* ---------- 导出 ---------- */

  return {
    render: render,
    formatNumbers: formatNumbers
  };
});
