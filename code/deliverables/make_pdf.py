# -*- coding: utf-8 -*-
"""
将《工程报告.md》渲染为中文 PDF。

技术栈：fpdf2（纯 Python，无 C 扩展，Python 3.14 兼容）。
中文字体：微软雅黑 msyh.ttc（index 0 常规 / index 1 粗体），宋体 simhei.ttf 作为兜底。
覆盖：标题层级、正文、无序/有序列表、引用、代码块、Markdown 表格（带表头底纹）、页脚页码、A4。
"""
import os
import re
import sys

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MD_PATH = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "工程报告.md"))
OUT_PDF = os.path.join(BASE_DIR, "工程报告.pdf")

# ---------------------------------------------------------------- 字体注册
FONT_DIR = r"C:\Windows\Fonts"
YAHEI_TTC = os.path.join(FONT_DIR, "msyh.ttc")
SIMHEI_TTF = os.path.join(FONT_DIR, "simhei.ttf")


def pick_font(*paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


BODY_FONT = pick_font(YAHEI_TTC)
FALLBACK_FONT = pick_font(SIMHEI_TTF)
if not BODY_FONT:
    print("错误：未找到可用中文字体（msyh.ttc / simhei.ttf）。", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- 样式常量（mm / 0-255 颜色）
MARGIN_L = 16
MARGIN_R = 16
MARGIN_T = 18
MARGIN_B = 22
BODY_SIZE = 10
BODY_LH = 5.3
NAVY = (20, 43, 97)       # 标题藏青
DARK = (31, 31, 36)       # 正文深灰
GRAY = (97, 97, 102)      # 引用/页脚灰
CODE_BG = (238, 240, 243)  # 代码块浅灰背景
HDR_FILL = (214, 224, 240)  # 表头浅蓝底纹


class ReportPDF(FPDF):
    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-13)
        self.set_font("YaHei", "", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 8, f"第 {self.page_no()} 页 · 共 {self.str_alias_nb_pages} 页", align="C")
        self.set_text_color(0, 0, 0)


# ---------------------------------------------------------------- markdown 辅助
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
BOLD_MARK_RE = re.compile(r"\*\*")


def strip_inline(s: str) -> str:
    """去掉行内反引号（fpdf2 的 markdown 不认 backtick，直接剥掉保留文本）。"""
    return INLINE_CODE_RE.sub(r"\1", s)


def clean_for_width(s: str) -> str:
    s = strip_inline(s)
    return BOLD_MARK_RE.sub("", s)


def char_weight(s: str) -> float:
    """CJK 按 2 个单位、ASCII 按 1 个单位估算宽度。"""
    w = 0.0
    for ch in s:
        if ch == " ":
            w += 0.5
        elif ord(ch) > 0x2E7F or "\u4e00" <= ch <= "\u9fff":
            w += 2.0
        else:
            w += 1.0
    return w


def split_row(line: str):
    """按未转义竖线拆分表格行，并还原 \\|。"""
    parts = re.split(r"(?<!\\)\|", line)
    parts = [p.strip() for p in parts]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return [p.replace("\\|", "|") for p in parts]


SEP_RE = re.compile(r"^\s*\|?[\s:|\-]+\|?\s*$")


def is_table_sep(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and SEP_RE.match(s) and "-" in s


def is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def parse_table(block):
    """block: 连续的表格行；返回 (headers, rows)。"""
    headers = split_row(block[0])
    rows = []
    for line in block[1:]:
        if is_table_sep(line):
            continue
        cells = split_row(line)
        rows.append(cells)
    return headers, rows


def table_col_widths(headers, rows, epw):
    ncols = len(headers)
    weights = [0.0] * ncols
    for r in [headers] + rows:
        for i in range(ncols):
            cell = clean_for_width(r[i]) if i < len(r) else ""
            weights[i] = max(weights[i], char_weight(cell))
    total = sum(weights) or 1.0
    return [epw * w / total for w in weights]


# ---------------------------------------------------------------- 渲染器
class MarkdownRenderer:
    def __init__(self, pdf: FPDF):
        self.pdf = pdf
        self.epw = pdf.w - MARGIN_L - MARGIN_R

    # ---- 基础块 ----
    def paragraph(self, text, *, size=BODY_SIZE, color=DARK, indent=0.0, lh=BODY_LH,
                  align="J", markdown=True, fill=False, border=0):
        pdf = self.pdf
        text = strip_inline(text)
        pdf.set_font("YaHei", "", size)
        pdf.set_text_color(*color)
        x = pdf.l_margin + indent
        w = self.epw - indent
        if fill:
            pdf.set_fill_color(*CODE_BG)
        pdf.set_x(x)
        pdf.multi_cell(w, lh, text, align=align, markdown=markdown,
                       fill=fill, border=border, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
        if fill:
            pdf.set_fill_color(255, 255, 255)

    def spacing(self, mm):
        self.pdf.ln(mm)

    def hrule(self, y_gap=2.0, color=(158, 168, 184)):
        pdf = self.pdf
        pdf.ln(y_gap)
        y = pdf.get_y()
        pdf.set_draw_color(*color)
        pdf.set_line_width(0.25)
        pdf.line(pdf.l_margin, y, pdf.w - MARGIN_R, y)
        pdf.set_draw_color(0, 0, 0)
        pdf.ln(y_gap)

    # ---- 标题 ----
    def heading(self, level, text):
        pdf = self.pdf
        text = strip_inline(text)
        if level == 1:
            pdf.ln(2)
            pdf.set_font("YaHei", "B", 20)
            pdf.set_text_color(*NAVY)
            pdf.multi_cell(0, 9.5, text, align="C", markdown=True,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
        elif level == 2:
            pdf.ln(5)
            pdf.set_font("YaHei", "B", 14)
            pdf.set_text_color(*NAVY)
            pdf.multi_cell(0, 7.2, text, align="L", markdown=True,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1.2)
        else:
            pdf.ln(3.5)
            pdf.set_font("YaHei", "B", 12)
            pdf.set_text_color(*DARK)
            pdf.multi_cell(0, 6.2, text, align="L", markdown=True,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(0.8)

    # ---- 引用 ----
    def blockquote(self, lines):
        pdf = self.pdf
        for i, ln in enumerate(lines):
            t = strip_inline(ln)
            pdf.set_font("YaHei", "", BODY_SIZE)
            pdf.set_text_color(*GRAY)
            pdf.set_x(pdf.l_margin + 5)
            pdf.multi_cell(self.epw - 5, BODY_LH, t, align="L", markdown=True,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            if i < len(lines) - 1:
                pdf.ln(0.8)

    # ---- 列表 ----
    def bullet_item(self, text):
        pdf = self.pdf
        text = strip_inline(text)
        pdf.set_font("YaHei", "", BODY_SIZE)
        bullet = "\u2022  "
        bw = pdf.get_string_width(bullet) + 1.0
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(bw, BODY_LH, bullet, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.multi_cell(self.epw - bw, BODY_LH, text, markdown=True,
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(0.6)

    def num_item(self, number, text):
        pdf = self.pdf
        text = strip_inline(text)
        pdf.set_font("YaHei", "", BODY_SIZE)
        prefix = f"{number}.  "
        pw = pdf.get_string_width(prefix) + 1.0
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pw, BODY_LH, prefix, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.multi_cell(self.epw - pw, BODY_LH, text, markdown=True,
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(0.6)

    # ---- 代码块 ----
    def code_block(self, lines):
        pdf = self.pdf
        inset = 4.0
        pdf.set_font("YaHei", "", 8.5)
        pdf.set_fill_color(240, 242, 245)
        pdf.set_draw_color(199, 204, 214)
        for i, ln in enumerate(lines):
            x = pdf.l_margin + inset
            w = self.epw - inset * 2
            pdf.set_x(x)
            pdf.multi_cell(w, 4.4, ln, align="L", fill=True,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_fill_color(255, 255, 255)
        pdf.ln(1.5)

    # ---- 表格 ----
    def table_block(self, headers, rows):
        pdf = self.pdf
        if not rows:
            return
        ncols = len(headers)
        rows = [r for r in rows if len(r) >= ncols or any(r)]
        if not rows:
            return
        widths = table_col_widths(headers, rows, self.epw)
        pdf.set_font("YaHei", "", 9)
        pdf.set_text_color(0, 0, 0)
        pdf.set_fill_color(255, 255, 255)  # 重置：fpdf2 会以进入 table 时的填充色铺满单元格
        headings_style = FontFace(
            family="YaHei", emphasis="BOLD", size_pt=9,
            color=(0, 0, 0), fill_color=HDR_FILL,
        )
        with pdf.table(
            col_widths=widths,
            headings_style=headings_style,
            text_align="LEFT",
            markdown=True,
            line_height=1.0,
            padding=1.6,
        ) as table:
            header = table.row()
            for h in headers:
                header.cell(strip_inline(h))
            for r in rows:
                tr = table.row()
                for i in range(ncols):
                    tr.cell(strip_inline(r[i]) if i < len(r) else "")
        pdf.ln(2.0)

    # ---- 顶层调度 ----
    def render(self, lines):
        pdf = self.pdf
        i = 0
        n = len(lines)
        ordered_counter = None
        while i < n:
            raw = lines[i]
            s = raw.strip()
            if not s:
                ordered_counter = None
                i += 1
                continue

            # 代码块
            if s.startswith("```"):
                j = i + 1
                buf = []
                while j < n and not lines[j].strip().startswith("```"):
                    buf.append(lines[j].rstrip("\n"))
                    j += 1
                self.code_block(buf)
                i = j + 1
                ordered_counter = None
                continue

            # 标题
            if s.startswith("#"):
                m = re.match(r"^(#{1,4})\s+(.*)$", s)
                if m:
                    self.heading(len(m.group(1)), m.group(2))
                i += 1
                ordered_counter = None
                continue

            # 引用
            if s.startswith(">"):
                buf = []
                while i < n and lines[i].strip().startswith(">"):
                    buf.append(re.sub(r"^>\s?", "", lines[i].strip()))
                    i += 1
                self.blockquote(buf)
                ordered_counter = None
                continue

            # 表格
            if is_table_line(s):
                buf = []
                while i < n and is_table_line(lines[i]):
                    buf.append(lines[i].strip())
                    i += 1
                if len(buf) >= 2 and is_table_sep(buf[1]):
                    headers, rows = parse_table(buf)
                    self.table_block(headers, rows)
                else:  # 不是表格则按普通行处理
                    for line in buf:
                        self.paragraph(line)
                ordered_counter = None
                continue

            # 水平线
            if re.fullmatch(r"-{3,}", s):
                self.hrule()
                i += 1
                ordered_counter = None
                continue

            # 无序列表
            m = re.match(r"^[-*]\s+(.*)$", s)
            if m:
                self.bullet_item(m.group(1))
                ordered_counter = None
                i += 1
                continue

            # 有序列表
            m = re.match(r"^(\d+)\.\s+(.*)$", s)
            if m:
                self.num_item(int(m.group(1)), m.group(2))
                ordered_counter = None
                i += 1
                continue

            # 普通段落：合并连续非空行
            buf = []
            while i < n:
                t = lines[i].strip()
                if not t or is_table_line(t) or t.startswith("#") or t.startswith(">"):
                    break
                if re.fullmatch(r"-{3,}", t) or re.match(r"^```", t):
                    break
                if re.match(r"^[-*]\s+", t) or re.match(r"^\d+\.\s+", t):
                    break
                buf.append(t)
                i += 1
            if buf:
                self.paragraph(" ".join(buf))
            ordered_counter = None


# ---------------------------------------------------------------- 主流程
def main():
    with open(MD_PATH, "r", encoding="utf-8") as f:
        md_text = f.read()

    pdf = ReportPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(MARGIN_L, MARGIN_T, MARGIN_R)
    pdf.set_auto_page_break(True, margin=MARGIN_B)

    pdf.add_font("YaHei", "", BODY_FONT, collection_font_number=0)
    pdf.add_font("YaHei", "B", BODY_FONT, collection_font_number=1)
    if FALLBACK_FONT:
        pdf.add_font("SimHei", "", FALLBACK_FONT)
        pdf.set_fallback_fonts(["SimHei"])

    pdf.add_page()
    renderer = MarkdownRenderer(pdf)
    renderer.render(md_text.split("\n"))

    pdf.output(OUT_PDF)
    print(f"OK: {OUT_PDF}")
    print(f"size={os.path.getsize(OUT_PDF)} bytes, pages={pdf.pages_count}")


if __name__ == "__main__":
    main()
