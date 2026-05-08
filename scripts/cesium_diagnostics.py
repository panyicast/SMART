"""命令行工具：自动化验证 SMART 的 Cesium / WebGL 诊断页面。

通过 Playwright + 无头 Chromium 直接打开仓库内的 webgl_probe.html、
cesium_probe.html（必要时还有 mission_view.html），抓取控制台日志、
DOM 状态以及全屏截图，最后输出 JSON 报告。

用途：
- 在 Qt WebEngine 行为可疑时，先确认本地 HTML/CesiumJS 资源本身是健康的；
- CI 或本地 smoke 测试中快速回归 Cesium 集成。

依赖：
- playwright Python 包；
- 浏览器二进制：``python -m playwright install chromium``。

退出码：
- 0：所有页面通过
- 2：至少一个页面失败
- 3：缺少依赖或运行环境异常
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import socket
import socketserver
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ASSET_ROOT = _REPO_ROOT / "src" / "smart" / "assets"
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "output" / "playwright"


@dataclass(slots=True)
class ProbeResult:
    name: str
    url: str
    passed: bool
    status_text: str = ""
    status_class: str = ""
    duration_ms: float = 0.0
    screenshot_path: str = ""
    console_warnings: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(slots=True)
class DiagnosticsReport:
    base_url: str
    asset_root: str
    timestamp: str
    overall_passed: bool
    probes: list[ProbeResult] = field(default_factory=list)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature
        return


@contextlib.contextmanager
def _serve_assets(asset_root: Path):
    """在后台线程启动 SimpleHTTPServer 服务静态资源。"""

    if not asset_root.exists():
        raise FileNotFoundError(f"asset root does not exist: {asset_root}")

    handler = lambda *args, **kwargs: _SilentHandler(  # noqa: E731
        *args, directory=str(asset_root), **kwargs
    )
    port = _find_free_port()
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="cesium-diag-http", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "未找到 playwright，请先安装：\n"
            "  pip install 'playwright>=1.50,<2'\n"
            "  python -m playwright install chromium"
        ) from exc
    from playwright.sync_api import sync_playwright

    return sync_playwright


def _probe(
    page,
    *,
    name: str,
    url: str,
    output_dir: Path,
    timeout_ms: int,
    expected_class: str = "ok",
) -> ProbeResult:
    """打开一个诊断页面并采集结果。"""

    result = ProbeResult(name=name, url=url, passed=False)
    started_at = page.context._impl_obj._loop.time() if hasattr(page.context, "_impl_obj") else None

    console_warnings: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []

    def _on_console(message) -> None:
        text = f"[{message.type}] {message.text}"
        if message.type == "warning":
            console_warnings.append(text)
        elif message.type in ("error", "assert"):
            console_errors.append(text)

    def _on_page_error(error) -> None:
        page_errors.append(str(error))

    page.on("console", _on_console)
    page.on("pageerror", _on_page_error)

    import time as _time

    t0 = _time.perf_counter()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # 等待 #status 元素出现 ok / fail。
        page.wait_for_selector("#status", timeout=timeout_ms)
        # 等待至少一帧渲染完成；多数诊断页面会在初始化后立刻置 class。
        deadline = _time.perf_counter() + timeout_ms / 1000.0
        status_class = ""
        status_text = ""
        while _time.perf_counter() < deadline:
            status_class = page.eval_on_selector("#status", "el => el.className") or ""
            status_text = page.eval_on_selector("#status", "el => el.textContent") or ""
            if "ok" in status_class or "fail" in status_class:
                break
            page.wait_for_timeout(150)
        result.status_class = status_class.strip()
        result.status_text = status_text.strip()
        result.passed = expected_class in status_class
    except Exception as exc:
        result.notes = f"navigation/timeout error: {exc}"
        result.passed = False
    finally:
        elapsed_ms = (_time.perf_counter() - t0) * 1000.0
        result.duration_ms = round(elapsed_ms, 1)
        # 无论成败都尝试存截图。
        screenshot_path = output_dir / f"{name}.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
            result.screenshot_path = str(screenshot_path)
        except Exception as exc:
            result.notes = (result.notes + f" | screenshot failed: {exc}").strip(" |")

    result.console_warnings = console_warnings
    result.console_errors = console_errors
    result.page_errors = page_errors
    return result


def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 3

    probes: list[tuple[str, str]] = [
        ("webgl_probe", "/diagnostics/webgl_probe.html"),
        ("cesium_probe", "/diagnostics/cesium_probe.html"),
    ]
    if args.include_mission:
        probes.append(("mission_view", "/cesium/mission_view.html"))

    results: list[ProbeResult] = []
    with _serve_assets(_ASSET_ROOT) as base_url:
        with sync_playwright() as playwright_runtime:
            browser = playwright_runtime.chromium.launch(
                headless=not args.headed,
                args=["--use-gl=swiftshader", "--ignore-gpu-blocklist"],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True,
            )
            page = context.new_page()
            try:
                for name, path in probes:
                    url = f"{base_url}{path}"
                    print(f"[probe] {name} → {url}")
                    result = _probe(
                        page,
                        name=name,
                        url=url,
                        output_dir=output_dir,
                        timeout_ms=args.timeout * 1000,
                    )
                    results.append(result)
                    flag = "PASS" if result.passed else "FAIL"
                    print(
                        f"  [{flag}] status='{result.status_text}' class='{result.status_class}' "
                        f"errors={len(result.page_errors)+len(result.console_errors)} "
                        f"({result.duration_ms} ms)"
                    )
                    if result.page_errors:
                        for err in result.page_errors:
                            print(f"    pageerror: {err}")
                    if result.console_errors:
                        for err in result.console_errors:
                            print(f"    console : {err}")
            finally:
                context.close()
                browser.close()

    overall = all(item.passed for item in results) if results else False
    report = DiagnosticsReport(
        base_url=base_url,
        asset_root=str(_ASSET_ROOT),
        timestamp=__import__("datetime").datetime.now().isoformat(timespec="seconds"),
        overall_passed=overall,
        probes=results,
    )
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[report] {report_path}")
    return 0 if overall else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate SMART Cesium / WebGL diagnostic pages with Playwright.",
    )
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT_DIR), help="结果与截图输出目录")
    parser.add_argument("--timeout", type=int, default=30, help="单个页面等待时间（秒）")
    parser.add_argument("--include-mission", action="store_true", help="同时验证 mission_view.html")
    parser.add_argument("--headed", action="store_true", help="以可见模式运行 Chromium")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
