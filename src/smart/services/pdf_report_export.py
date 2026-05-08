"""AI 项目分析报告 → PDF 导出。

设计哲学借鉴 minimax-pdf 的 ``report`` 风格（深色背景封面 + 强调色 + 衬线
标题），但完全使用 ``reportlab`` 渲染，无需 Node.js 或 Chromium，
适合在 SMART 桌面端就地导出。

使用方式：

    from smart.services.pdf_report_export import export_pdf_report
    export_pdf_report(markdown_text, "report.pdf",
                      project_name="F4", generated_at="2026-05-08")

报告由 ``smart.services.report_export._parse_markdown`` 复用解析；
PDF 输出包含两个 PageTemplate：``cover`` 与 ``body``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

from smart.services.report_export import _MarkdownBlock, _parse_markdown


_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Noto_Sans_SC"
_NOTO_FONT_PATH = _FONT_DIR / "NotoSansSC-VariableFont_wght.ttf"

_FONT_REGULAR = "NotoSansSC"
_FONT_BOLD = "NotoSansSC-Bold"

_DEFAULT_ACCENT = "#2D5F8A"  # minimax-pdf 推荐的 technology / engineering 配色
_DARK_BG = HexColor("#0E1B2C")
_DARK_BG_TEXT = HexColor("#F4F8FC")
_DARK_BG_MUTED = HexColor("#7E97AB")
_BODY_TEXT = HexColor("#1F2A33")
_BODY_MUTED = HexColor("#5A6B78")
_BODY_RULE = HexColor("#D5DEE6")
_TABLE_ROW_TINT = HexColor("#F2F6FA")
_CALLOUT_BG = HexColor("#F2F6FA")
_CODE_BG = HexColor("#F4F4F4")

_PAGE_MARGIN = 22 * mm
_COVER_MARGIN_X = 20 * mm
_COVER_MARGIN_Y = 24 * mm

_FONTS_REGISTERED = False


@dataclass(slots=True)
class PdfReportMeta:
    title: str = "AI 项目分析报告"
    project_name: str = ""
    generated_at: str = ""
    accent: str = _DEFAULT_ACCENT


def export_pdf_report(
    markdown: str,
    path: str | Path,
    *,
    title: str = "AI 项目分析报告",
    project_name: str = "",
    generated_at: str | None = None,
    accent: str = _DEFAULT_ACCENT,
) -> Path:
    """将 Markdown 渲染为带封面的 PDF 报告。"""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _ensure_fonts_registered()

    meta = PdfReportMeta(
        title=title.strip() or "AI 项目分析报告",
        project_name=project_name.strip(),
        generated_at=(generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")).strip(),
        accent=_validate_accent(accent),
    )

    blocks = _parse_markdown(markdown)
    accent_color = HexColor(meta.accent)
    styles = _build_styles(accent_color)

    page_width, page_height = A4
    cover_frame = Frame(
        _COVER_MARGIN_X,
        _COVER_MARGIN_Y,
        page_width - 2 * _COVER_MARGIN_X,
        page_height - 2 * _COVER_MARGIN_Y,
        id="cover",
        showBoundary=0,
    )
    body_frame = Frame(
        _PAGE_MARGIN,
        _PAGE_MARGIN,
        page_width - 2 * _PAGE_MARGIN,
        page_height - 2 * _PAGE_MARGIN - 8 * mm,  # leave room for footer
        id="body",
        showBoundary=0,
    )
    cover_template = PageTemplate(
        id="cover",
        frames=[cover_frame],
        onPage=lambda canvas, doc: _draw_cover_background(canvas, doc, meta, accent_color),
    )
    body_template = PageTemplate(
        id="body",
        frames=[body_frame],
        onPage=lambda canvas, doc: _draw_body_chrome(canvas, doc, meta, accent_color),
    )

    doc = BaseDocTemplate(
        str(output_path),
        pagesize=A4,
        title=meta.title,
        author=meta.project_name or "SMART",
        creator="SMART",
        leftMargin=_PAGE_MARGIN,
        rightMargin=_PAGE_MARGIN,
        topMargin=_PAGE_MARGIN,
        bottomMargin=_PAGE_MARGIN,
    )
    doc.addPageTemplates([cover_template, body_template])

    story = list(_cover_story(meta, styles, accent_color))
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())
    story.extend(_body_story(blocks, styles, accent_color))

    doc.build(story)
    return output_path


def _validate_accent(value: str) -> str:
    candidate = value.strip()
    if not candidate.startswith("#") or len(candidate) not in (4, 7):
        return _DEFAULT_ACCENT
    try:
        HexColor(candidate)
    except Exception:
        return _DEFAULT_ACCENT
    return candidate


def _ensure_fonts_registered() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    if _NOTO_FONT_PATH.exists():
        try:
            pdfmetrics.registerFont(TTFont(_FONT_REGULAR, str(_NOTO_FONT_PATH)))
            # Variable TTF reused as bold; ParagraphStyle uses bold font name selectively.
            pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(_NOTO_FONT_PATH)))
        except Exception:
            # 保底：保持 reportlab 默认 Helvetica（中文会乱码，但不会抛错）。
            pass
    _FONTS_REGISTERED = True


def _build_styles(accent: colors.Color) -> dict[str, ParagraphStyle]:
    base_font = _FONT_REGULAR if _NOTO_FONT_PATH.exists() else "Helvetica"
    bold_font = _FONT_BOLD if _NOTO_FONT_PATH.exists() else "Helvetica-Bold"

    body = ParagraphStyle(
        name="Body",
        fontName=base_font,
        fontSize=10.5,
        leading=16,
        textColor=_BODY_TEXT,
        spaceBefore=2,
        spaceAfter=4,
        alignment=TA_LEFT,
    )
    return {
        "body": body,
        "h1": ParagraphStyle(
            name="H1",
            parent=body,
            fontName=bold_font,
            fontSize=20,
            leading=26,
            textColor=accent,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            name="H2",
            parent=body,
            fontName=bold_font,
            fontSize=15,
            leading=20,
            textColor=_BODY_TEXT,
            spaceBefore=12,
            spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            name="H3",
            parent=body,
            fontName=bold_font,
            fontSize=12.5,
            leading=18,
            textColor=_BODY_TEXT,
            spaceBefore=8,
            spaceAfter=3,
        ),
        "list_item": ParagraphStyle(
            name="ListItem",
            parent=body,
            leftIndent=14,
            firstLineIndent=-12,
            spaceBefore=0,
            spaceAfter=2,
        ),
        "callout": ParagraphStyle(
            name="Callout",
            parent=body,
            backColor=_CALLOUT_BG,
            borderColor=accent,
            borderWidth=0,
            borderPadding=(8, 10, 8, 14),
            leftIndent=0,
            rightIndent=0,
            textColor=_BODY_TEXT,
            spaceBefore=8,
            spaceAfter=8,
        ),
        "code": ParagraphStyle(
            name="Code",
            parent=body,
            fontName="Courier",
            fontSize=9,
            leading=12,
            textColor=_BODY_TEXT,
            backColor=_CODE_BG,
            borderPadding=(6, 8, 6, 10),
            spaceBefore=4,
            spaceAfter=8,
        ),
        "table_cell": ParagraphStyle(
            name="TableCell",
            parent=body,
            fontSize=9.5,
            leading=13,
        ),
        "table_header": ParagraphStyle(
            name="TableHeader",
            parent=body,
            fontSize=9.5,
            leading=13,
            fontName=bold_font,
            textColor=colors.white,
        ),
        "cover_title": ParagraphStyle(
            name="CoverTitle",
            fontName=bold_font,
            fontSize=42,
            leading=52,
            textColor=_DARK_BG_TEXT,
            spaceAfter=14,
        ),
        "cover_subtitle": ParagraphStyle(
            name="CoverSubtitle",
            fontName=base_font,
            fontSize=14,
            leading=22,
            textColor=_DARK_BG_MUTED,
        ),
        "cover_eyebrow": ParagraphStyle(
            name="CoverEyebrow",
            fontName=bold_font,
            fontSize=10,
            leading=14,
            textColor=accent,
        ),
        "cover_meta": ParagraphStyle(
            name="CoverMeta",
            fontName=base_font,
            fontSize=10.5,
            leading=14,
            textColor=_DARK_BG_MUTED,
        ),
    }


def _cover_story(meta: PdfReportMeta, styles: dict, _accent: colors.Color) -> Iterable:
    yield Spacer(1, 60 * mm)
    yield Paragraph("SMART · AI PROJECT ANALYSIS", styles["cover_eyebrow"])
    yield Spacer(1, 4 * mm)
    yield Paragraph(_escape(meta.title), styles["cover_title"])
    if meta.project_name:
        yield Paragraph(f"Project · {_escape(meta.project_name)}", styles["cover_subtitle"])
    yield Spacer(1, 24 * mm)
    yield Paragraph(f"Generated · {_escape(meta.generated_at)}", styles["cover_meta"])


def _body_story(
    blocks: list[_MarkdownBlock],
    styles: dict,
    accent: colors.Color,
) -> Iterable:
    for block in blocks:
        yield from _block_to_flowables(block, styles, accent)


def _block_to_flowables(
    block: _MarkdownBlock,
    styles: dict,
    accent: colors.Color,
) -> Iterable:
    if block.kind == "heading":
        level = max(1, min(3, block.level))
        yield Paragraph(_escape(block.text), styles[f"h{level}"])
        return
    if block.kind == "paragraph":
        text = _escape(block.text)
        if not text:
            return
        yield Paragraph(text, styles["body"])
        return
    if block.kind == "list":
        for line in block.text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- "):
                stripped = stripped[2:]
            yield Paragraph(f"• {_escape(stripped)}", styles["list_item"])
        return
    if block.kind == "table":
        yield from _table_flowables(block.rows, styles, accent)
        return
    if block.kind == "code":
        yield Preformatted(block.text or "", styles["code"])
        return


def _table_flowables(
    rows: list[list[str]],
    styles: dict,
    accent: colors.Color,
) -> Iterable:
    if not rows:
        return
    column_count = max(len(row) for row in rows)
    normalized = [[*row, *([""] * (column_count - len(row)))] for row in rows]
    cell_data: list[list[Paragraph]] = []
    for row_index, row in enumerate(normalized):
        style_key = "table_header" if row_index == 0 else "table_cell"
        cell_data.append([Paragraph(_escape(cell), styles[style_key]) for cell in row])

    table = Table(cell_data, repeatRows=1, hAlign="LEFT")
    base_style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), accent),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEABOVE", (0, 1), (-1, 1), 0.5, _BODY_RULE),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, _BODY_RULE),
            ("LINEBEFORE", (0, 0), (0, -1), 0.5, _BODY_RULE),
            ("LINEAFTER", (-1, 0), (-1, -1), 0.5, _BODY_RULE),
            ("INNERGRID", (0, 1), (-1, -1), 0.25, _BODY_RULE),
        ]
    )
    for row_index in range(2, len(normalized), 2):
        base_style.add("BACKGROUND", (0, row_index), (-1, row_index), _TABLE_ROW_TINT)
    table.setStyle(base_style)
    yield Spacer(1, 4)
    yield table
    yield Spacer(1, 6)


def _draw_cover_background(canvas, doc, meta: PdfReportMeta, accent: colors.Color) -> None:
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(_DARK_BG)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(accent)
    canvas.rect(0, 0, 8 * mm, height, fill=1, stroke=0)
    canvas.setStrokeColor(accent)
    canvas.setLineWidth(2)
    canvas.line(_COVER_MARGIN_X, height - 60 * mm, _COVER_MARGIN_X + 36 * mm, height - 60 * mm)
    canvas.setFillColor(_DARK_BG_MUTED)
    canvas.setFont(_FONT_REGULAR if _NOTO_FONT_PATH.exists() else "Helvetica", 9)
    canvas.drawString(_COVER_MARGIN_X, 18 * mm, "Generated by SMART · 航天器任务分析、研究与工具集")
    canvas.restoreState()


def _draw_body_chrome(canvas, doc, meta: PdfReportMeta, accent: colors.Color) -> None:
    width, _height = A4
    canvas.saveState()
    canvas.setStrokeColor(accent)
    canvas.setLineWidth(1.4)
    canvas.line(_PAGE_MARGIN, _PAGE_MARGIN - 2 * mm, _PAGE_MARGIN + 24 * mm, _PAGE_MARGIN - 2 * mm)
    canvas.setFillColor(_BODY_MUTED)
    canvas.setFont(_FONT_REGULAR if _NOTO_FONT_PATH.exists() else "Helvetica", 8)
    footer_left = meta.title
    if meta.project_name:
        footer_left = f"{meta.title} · {meta.project_name}"
    canvas.drawString(_PAGE_MARGIN, _PAGE_MARGIN - 8 * mm, footer_left)
    canvas.drawRightString(width - _PAGE_MARGIN, _PAGE_MARGIN - 8 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
