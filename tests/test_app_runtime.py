from __future__ import annotations

import os

from smart.app_runtime import configure_graphics_backend


def test_configure_graphics_backend_defaults_to_swiftshader(monkeypatch) -> None:
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)
    monkeypatch.delenv("SMART_WEBENGINE_BACKEND", raising=False)

    selected = configure_graphics_backend()

    assert selected == "swiftshader"
    flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
    assert "--use-angle=swiftshader" in flags
    assert "--enable-webgl" in flags


def test_configure_graphics_backend_can_override_existing_backend(monkeypatch) -> None:
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--use-angle=d3d11 --foo=bar")

    selected = configure_graphics_backend("desktop")

    assert selected == "desktop"
    flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
    assert "--use-gl=desktop" in flags
    assert "--foo=bar" in flags
    assert "--use-angle=d3d11" in flags
