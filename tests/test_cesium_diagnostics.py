"""Lightweight tests for the Cesium diagnostics CLI.

不实际启动 Chromium（避免 CI 没有浏览器的情况下失败）；只验证：

1. CLI 入口能加载脚本模块；
2. 参数解析返回预期默认值；
3. 静态资源 HTTP 服务器能在 ``src/smart/assets`` 目录下提供探针 HTML。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from smart.cesium_diagnostics_cli import _load_script_module


def test_cli_loads_script_module() -> None:
    module = _load_script_module()
    assert hasattr(module, "main")
    assert hasattr(module, "parse_args")


def test_parse_args_defaults_align_with_repo_layout() -> None:
    module = _load_script_module()

    args = module.parse_args([])

    assert args.timeout == 30
    assert args.include_mission is False
    assert args.headed is False
    output_path = Path(args.output)
    # 默认输出目录应位于仓库 output/playwright 下（绝对路径）。
    assert output_path.name == "playwright"
    assert output_path.parent.name == "output"


def test_serve_assets_exposes_diagnostic_pages() -> None:
    module = _load_script_module()
    asset_root = Path(module._ASSET_ROOT)
    assert asset_root.exists(), "缺少 SMART 静态资源目录"

    with module._serve_assets(asset_root) as base_url:
        for path in (
            "/diagnostics/webgl_probe.html",
            "/diagnostics/cesium_probe.html",
        ):
            with urllib.request.urlopen(f"{base_url}{path}", timeout=2) as response:
                assert response.status == 200
                payload = response.read(64)
            assert payload.lstrip().lower().startswith(b"<!doctype html"), path
