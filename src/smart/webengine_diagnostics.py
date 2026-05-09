from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from smart.app_runtime import configure_graphics_backend, load_app_icon

try:
    from PySide6 import QtWebEngineCore, QtWebEngineWidgets
except Exception:  # pragma: no cover - depends on local Qt runtime
    QtWebEngineCore = None
    QtWebEngineWidgets = None

_ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "diagnostics"
_WEBGL_PROBE_PATH = _ASSET_ROOT / "webgl_probe.html"


class _DiagnosticWebPage(QtWebEngineCore.QWebEnginePage):  # type: ignore[misc]
    console_emitted = QtCore.Signal(str)

    def __init__(self, label: str, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._label = label

    def javaScriptConsoleMessage(
        self,
        level: QtWebEngineCore.QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        self.console_emitted.emit(
            f"[{self._label}] JS {level.name} {source_id}:{line_number} {message}"
        )
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class ProbePane(QtWidgets.QWidget):
    log_emitted = QtCore.Signal(str)

    def __init__(self, title: str, url: QtCore.QUrl, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._status_label = QtWidgets.QLabel("Pending load")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._view = QtWebEngineWidgets.QWebEngineView()
        self._page = _DiagnosticWebPage(title, self._view)
        self._view.setPage(self._page)
        settings = self._page.settings()
        settings.setAttribute(
            QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            True,
        )
        settings.setAttribute(
            QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            True,
        )
        settings.setAttribute(
            QtWebEngineCore.QWebEngineSettings.WebAttribute.WebGLEnabled,
            True,
        )
        layout.addWidget(self._view, 1)

        self._page.console_emitted.connect(self.log_emitted)
        self._view.loadStarted.connect(self._on_load_started)
        self._view.loadProgress.connect(self._on_load_progress)
        self._view.loadFinished.connect(self._on_load_finished)
        self._view.urlChanged.connect(self._on_url_changed)
        self._page.titleChanged.connect(self._on_title_changed)
        self._url = url
        self.reload()

    @property
    def title(self) -> str:
        return self._title

    def reload(self) -> None:
        self.log_emitted.emit(f"[{self._title}] Loading {self._url.toString()}")
        self._view.load(self._url)

    def _on_load_started(self) -> None:
        self._status_label.setText("Loading...")
        self.log_emitted.emit(f"[{self._title}] loadStarted")

    def _on_load_progress(self, progress: int) -> None:
        self._status_label.setText(f"Loading... {progress}%")

    def _on_load_finished(self, ok: bool) -> None:
        state = "loadFinished ok" if ok else "loadFinished failed"
        self._status_label.setText(state)
        self.log_emitted.emit(f"[{self._title}] {state}")

    def _on_url_changed(self, url: QtCore.QUrl) -> None:
        self.log_emitted.emit(f"[{self._title}] URL -> {url.toString()}")

    def _on_title_changed(self, title: str) -> None:
        if title:
            self.log_emitted.emit(f"[{self._title}] Title -> {title}")


class DiagnosticsWindow(QtWidgets.QMainWindow):
    def __init__(self, backend: str) -> None:
        super().__init__()
        self.resize(1500, 960)
        self.setWindowTitle("SMART WebEngine Diagnostics")
        icon = load_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        shell = QtWidgets.QWidget()
        self.setCentralWidget(shell)
        root = QtWidgets.QVBoxLayout(shell)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        intro = QtWidgets.QLabel(
            "Purpose: isolate whether rendering issues come from QWebEngine itself "
            "or from WebGL context creation. "
            f"Current backend: {backend}"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        notes = QtWidgets.QLabel(
            "Expected interpretation: `chrome://gpu` should show GPU feature status; "
            "`WebGL Probe` should display a teal-blue canvas and renderer details."
        )
        notes.setWordWrap(True)
        root.addWidget(notes)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        reload_all_button = QtWidgets.QPushButton("Reload All")
        reload_all_button.clicked.connect(self._reload_all)
        controls.addWidget(reload_all_button)

        clear_log_button = QtWidgets.QPushButton("Clear Log")
        clear_log_button.clicked.connect(self._clear_log)
        controls.addWidget(clear_log_button)

        controls.addStretch(1)
        root.addLayout(controls)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        self._tabs = QtWidgets.QTabWidget()
        splitter.addWidget(self._tabs)

        self._log_edit = QtWidgets.QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        splitter.addWidget(self._log_edit)
        splitter.setSizes([700, 220])

        self._panes: list[ProbePane] = []
        for title, url in (
            ("Chrome GPU", QtCore.QUrl("chrome://gpu")),
            ("WebGL Probe", QtCore.QUrl.fromLocalFile(str(_WEBGL_PROBE_PATH.resolve()))),
        ):
            pane = ProbePane(title, url)
            pane.log_emitted.connect(self._append_log)
            self._panes.append(pane)
            self._tabs.addTab(pane, title)

        self._append_log(f"Backend configured as: {backend}")
        self._append_log(f"QTWEBENGINE_CHROMIUM_FLAGS={os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')}")

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log_edit.appendPlainText(f"[{timestamp}] {message}")

    def _reload_all(self) -> None:
        for pane in self._panes:
            pane.reload()

    def _clear_log(self) -> None:
        self._log_edit.clear()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SMART WebEngine diagnostics")
    parser.add_argument(
        "--backend",
        default=None,
        choices=("swiftshader", "software", "swiftshader-webgl", "d3d11", "desktop"),
        help="Override SMART_WEBENGINE_BACKEND for this diagnostics process.",
    )
    args = parser.parse_args(argv)

    backend = configure_graphics_backend(args.backend)
    app = QtWidgets.QApplication(sys.argv if argv is None else ["smart-webengine-diagnostics", *argv])
    app.setApplicationName("SMART WebEngine Diagnostics")
    app.setOrganizationName("SMART")
    window = DiagnosticsWindow(backend)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
