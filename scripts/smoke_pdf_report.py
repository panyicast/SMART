"""临时冒烟脚本：验证 pdf_report_export 能成功生成 PDF。可手动运行。"""
from __future__ import annotations

from pathlib import Path

from smart.services.pdf_report_export import export_pdf_report

SAMPLE = """# AI 项目分析报告

这是 SMART 自动生成的示例报告，用于验证 PDF 渲染管线。

## 关键结论

- 第一条结论包含**加粗**和*斜体*
- 第二条结论
- 第三条结论

## 数据汇总

| 指标 | 值 | 备注 |
|---|---|---|
| Δv | 1234 m/s | 累计 |
| 时长 | 30 min | 包含双脉冲 |
| 推进剂 | 8.4 kg | 估算 |

## 代码示例

```python
def hello():
    print("hello smart")
```

> 这是引用样式（暂不显式渲染）。
"""


def main() -> None:
    output_dir = Path("data") if Path("data").exists() else Path.cwd()
    out = output_dir / "smoke_ai_report.pdf"
    export_pdf_report(
        SAMPLE,
        out,
        project_name="F4",
        generated_at="2026-05-08 18:55",
    )
    print(f"OK: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
