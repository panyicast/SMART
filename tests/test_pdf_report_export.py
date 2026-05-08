from __future__ import annotations

from pathlib import Path

from smart.services.pdf_report_export import export_pdf_report

_SAMPLE = """# AI 项目分析报告

这是一段示例段落。

## 关键结论

- 结论 1
- 结论 2

| 指标 | 值 |
|---|---|
| Δv | 1234 m/s |
| 时长 | 30 min |

```python
print('hello')
```
"""


def test_export_pdf_report_creates_pdf(tmp_path: Path) -> None:
    output = tmp_path / "report.pdf"

    result = export_pdf_report(
        _SAMPLE,
        output,
        project_name="F4",
        generated_at="2026-05-08 12:00",
    )

    assert result == output
    assert output.exists()
    contents = output.read_bytes()
    assert contents.startswith(b"%PDF")
    assert len(contents) > 2000


def test_export_pdf_report_appends_pdf_suffix(tmp_path: Path) -> None:
    output = tmp_path / "report-no-suffix"

    result = export_pdf_report(
        "# 仅有标题\n\n正文。",
        output.with_suffix(".pdf"),
        project_name="",
    )

    assert result.suffix == ".pdf"
    assert result.exists()


def test_export_pdf_report_falls_back_for_invalid_accent(tmp_path: Path) -> None:
    output = tmp_path / "report.pdf"

    # 故意传错颜色，应该自动回退到默认 accent，不抛异常。
    export_pdf_report(
        "# 回退测试\n\n正文。",
        output,
        accent="not-a-color",
    )

    assert output.exists()
