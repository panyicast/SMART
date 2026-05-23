from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Literal
from xml.sax.saxutils import escape
import zipfile


BlockKind = Literal["heading", "paragraph", "list", "table", "code"]


@dataclass(slots=True)
class _MarkdownBlock:
    kind: BlockKind
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)


def export_markdown_report(markdown: str, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def export_docx_report(markdown: str, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = _parse_markdown(markdown)
    document_xml = _document_xml(blocks)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _content_types_xml())
        package.writestr("_rels/.rels", _package_rels_xml())
        package.writestr("word/document.xml", document_xml)
        package.writestr("word/_rels/document.xml.rels", _document_rels_xml())
        package.writestr("word/styles.xml", _styles_xml())
    return output_path


def _parse_markdown(markdown: str) -> list[_MarkdownBlock]:
    lines = _normalized_markdown_lines(markdown)
    blocks: list[_MarkdownBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(_MarkdownBlock(kind="code", text="\n".join(code_lines)))
            continue

        if _is_table_start(lines, index):
            rows = [_clean_cells(_parse_table_row(lines[index]))]
            index += 2
            while index < len(lines) and _is_table_row(lines[index]):
                if not _is_table_separator(lines[index]):
                    rows.append(_clean_cells(_parse_table_row(lines[index])))
                index += 1
            blocks.append(_MarkdownBlock(kind="table", rows=rows))
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            blocks.append(
                _MarkdownBlock(
                    kind="heading",
                    level=min(len(heading_match.group(1)), 3),
                    text=_clean_inline(heading_match.group(2)),
                )
            )
            index += 1
            continue

        if _is_horizontal_rule(stripped):
            index += 1
            continue

        bullet_match = _bullet_match(line)
        if bullet_match:
            items: list[str] = []
            while index < len(lines):
                item_match = _bullet_match(lines[index])
                if item_match is None:
                    break
                items.append(f"- {_clean_inline(item_match.group(1))}")
                index += 1
            blocks.append(_MarkdownBlock(kind="list", text="\n".join(items)))
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index]
            next_stripped = next_line.strip()
            if (
                not next_stripped
                or next_stripped.startswith("```")
                or re.match(r"^(#{1,6})\s+(.+)$", next_stripped)
                or _is_table_start(lines, index)
                or _bullet_match(next_line)
                or _is_horizontal_rule(next_stripped)
            ):
                break
            paragraph_lines.append(next_stripped)
            index += 1
        blocks.append(_MarkdownBlock(kind="paragraph", text=_clean_inline(" ".join(paragraph_lines))))

    if not blocks:
        blocks.append(_MarkdownBlock(kind="paragraph", text=""))
    return blocks


def _normalized_markdown_lines(markdown: str) -> list[str]:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        lines.extend(_split_compact_table_line(line))
    return lines


def _split_compact_table_line(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return [line]

    first_pipe_index = stripped.find("|")
    prefix = stripped[:first_pipe_index].strip()
    table_part = stripped[first_pipe_index:].strip()
    candidate_rows = re.sub(r"\|\s+(?=\|)", "|\n", table_part).split("\n")
    if len(candidate_rows) <= 1 or not _contains_table_start(candidate_rows):
        return [line]
    if prefix:
        return [prefix, *candidate_rows]
    return candidate_rows


def _contains_table_start(lines: list[str]) -> bool:
    return any(_is_table_start(lines, index) for index in range(max(0, len(lines) - 1)))


def _is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and _is_table_row(lines[index])
        and _is_table_separator(lines[index + 1])
    )


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    cells = _parse_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in cells)


def _parse_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _clean_cells(cells: list[str]) -> list[str]:
    return [_clean_inline(cell) for cell in cells]


def _bullet_match(line: str) -> re.Match[str] | None:
    return re.match(r"^\s*(?:[-*+]|\d+\.)\s+(.+)$", line)


def _is_horizontal_rule(stripped: str) -> bool:
    return bool(re.fullmatch(r"[-*_]{3,}", stripped))


def _clean_inline(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    return _normalize_export_symbols(cleaned)


def _normalize_export_symbols(text: str) -> str:
    return (
        str(text)
        .replace("✅", "√")
        .replace("☑", "√")
        .replace("✔", "√")
        .replace("✓", "√")
        .replace("❌", "×")
        .replace("✖", "×")
        .replace("✗", "×")
        .replace("✘", "×")
    )


def _document_xml(blocks: list[_MarkdownBlock]) -> str:
    body = "\n".join(_block_xml(block) for block in blocks)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        "<w:body>\n"
        f"{body}\n"
        "<w:sectPr>"
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="708" w:footer="708" w:gutter="0"/>'
        "</w:sectPr>\n"
        "</w:body>\n"
        "</w:document>"
    )


def _block_xml(block: _MarkdownBlock) -> str:
    if block.kind == "heading":
        return _paragraph_xml(block.text, style=f"Heading{block.level}", bold=True)
    if block.kind == "list":
        return "\n".join(_paragraph_xml(line) for line in block.text.split("\n"))
    if block.kind == "table":
        return _table_xml(block.rows)
    if block.kind == "code":
        return _paragraph_xml(block.text, style="CodeBlock", code=True)
    return _paragraph_xml(block.text)


def _paragraph_xml(text: str, *, style: str | None = None, bold: bool = False, code: bool = False) -> str:
    paragraph_properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{paragraph_properties}{_run_xml(text, bold=bold, code=code)}</w:p>"


def _run_xml(text: str, *, bold: bool = False, code: bool = False) -> str:
    properties: list[str] = []
    if bold:
        properties.append("<w:b/>")
    if code:
        properties.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="Microsoft YaHei"/>')
    rpr = f"<w:rPr>{''.join(properties)}</w:rPr>" if properties else ""
    parts = text.split("\n")
    text_parts: list[str] = []
    for index, part in enumerate(parts):
        if index:
            text_parts.append("<w:br/>")
        text_parts.append(f'<w:t xml:space="preserve">{escape(part)}</w:t>')
    return f"<w:r>{rpr}{''.join(text_parts)}</w:r>"


def _table_xml(rows: list[list[str]]) -> str:
    if not rows:
        return _paragraph_xml("")
    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    row_xml = []
    for row_index, row in enumerate(normalized_rows):
        cells = "".join(_table_cell_xml(cell, bold=row_index == 0) for cell in row)
        row_xml.append(f"<w:tr>{cells}</w:tr>")
    borders = (
        "<w:tblBorders>"
        '<w:top w:val="single" w:sz="6" w:space="0" w:color="B7B7B7"/>'
        '<w:left w:val="single" w:sz="6" w:space="0" w:color="B7B7B7"/>'
        '<w:bottom w:val="single" w:sz="6" w:space="0" w:color="B7B7B7"/>'
        '<w:right w:val="single" w:sz="6" w:space="0" w:color="B7B7B7"/>'
        '<w:insideH w:val="single" w:sz="6" w:space="0" w:color="D9D9D9"/>'
        '<w:insideV w:val="single" w:sz="6" w:space="0" w:color="D9D9D9"/>'
        "</w:tblBorders>"
    )
    return (
        "<w:tbl>"
        f"<w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/>{borders}</w:tblPr>"
        f"{''.join(row_xml)}"
        "</w:tbl>"
    )


def _table_cell_xml(text: str, *, bold: bool) -> str:
    return (
        "<w:tc>"
        '<w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
        f"{_paragraph_xml(text, bold=bold)}"
        "</w:tc>"
    )


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )


def _package_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )


def _document_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        "</Relationships>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/>'
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>'
        '<w:sz w:val="22"/></w:rPr>'
        "</w:style>"
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr>'
        "</w:style>"
        '<w:style w:type="paragraph" w:styleId="Heading2">'
        '<w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="200" w:after="100"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="26"/></w:rPr>'
        "</w:style>"
        '<w:style w:type="paragraph" w:styleId="Heading3">'
        '<w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="160" w:after="80"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="23"/></w:rPr>'
        "</w:style>"
        '<w:style w:type="paragraph" w:styleId="CodeBlock">'
        '<w:name w:val="Code Block"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="80" w:after="80"/></w:pPr>'
        '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="Microsoft YaHei"/>'
        '<w:sz w:val="20"/></w:rPr>'
        "</w:style>"
        "</w:styles>"
    )
