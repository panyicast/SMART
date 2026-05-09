from __future__ import annotations

import os
import shlex
from pathlib import Path

from PySide6 import QtCore, QtGui, QtQuick


def _upsert_chromium_flags(*flags: str) -> None:
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    tokens = shlex.split(existing) if existing else []
    ordered_keys: list[str] = []
    by_key: dict[str, str] = {}

    for token in tokens:
        key = token.split("=", 1)[0]
        if key not in by_key:
            ordered_keys.append(key)
        by_key[key] = token

    for flag in flags:
        key = flag.split("=", 1)[0]
        if key not in by_key:
            ordered_keys.append(key)
        by_key[key] = flag

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(by_key[key] for key in ordered_keys)


def configure_graphics_backend(backend: str | None = None) -> str:
    # Force Qt Quick / QQuickWidget to use the same API as the QWidget shell.
    # The main SMART window mixes pyqtgraph's GL widgets with Qt WebEngine internals
    # that rely on Qt Quick composition; on Windows the default Qt Quick backend may
    # choose D3D11, which is incompatible with an OpenGL-composited top-level window.
    os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
    os.environ.setdefault("QT_OPENGL", "desktop")
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    QtQuick.QQuickWindow.setGraphicsApi(QtQuick.QSGRendererInterface.GraphicsApi.OpenGL)

    # Qt WebEngine uses Chromium. On some Windows driver stacks the hardware WebGL
    # path stays black, so default to SwiftShader and allow explicit overrides.
    selected_backend = (backend or os.environ.get("SMART_WEBENGINE_BACKEND", "swiftshader")).strip().lower()
    os.environ["SMART_WEBENGINE_BACKEND"] = selected_backend

    common_flags = (
        "--ignore-gpu-blocklist",
        "--enable-webgl",
    )
    if selected_backend == "d3d11":
        _upsert_chromium_flags(
            *common_flags,
            "--use-gl=angle",
            "--use-angle=d3d11",
        )
        return selected_backend

    if selected_backend in {"swiftshader", "software"}:
        _upsert_chromium_flags(
            *common_flags,
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
        )
        return selected_backend

    if selected_backend == "swiftshader-webgl":
        _upsert_chromium_flags(
            *common_flags,
            "--use-gl=angle",
            "--use-angle=swiftshader-webgl",
            "--enable-unsafe-swiftshader",
        )
        return selected_backend

    if selected_backend == "desktop":
        _upsert_chromium_flags(
            *common_flags,
            "--use-gl=desktop",
        )
        return selected_backend

    _upsert_chromium_flags(
        *common_flags,
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
    )
    return "swiftshader"


def load_app_icon() -> QtGui.QIcon:
    icon_path = Path(__file__).resolve().parent / "assets" / "icons" / "smart-main-icon.svg"
    return QtGui.QIcon(str(icon_path))
