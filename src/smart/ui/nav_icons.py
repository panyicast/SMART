"""左侧导航栏使用的内嵌 SVG 图标。

为了避免引入额外的素材文件，这里将每个导航项对应的 Lucide 风格图标作为
SVG 字符串内嵌存储，并在运行时通过 ``QtSvg.QSvgRenderer`` 渲染成 ``QIcon``。
所有图标统一使用 ``viewBox="0 0 24 24"`` 与 ``stroke="currentColor"``，
``nav_icon`` 函数会在渲染前替换为指定颜色，使图标可随主题着色。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui
from PySide6.QtSvg import QSvgRenderer

# 每个 SVG 的 stroke 占位符，会被替换为实际颜色字符串。
_COLOR_TOKEN = "{stroke}"

_NAV_SVG: dict[str, str] = {
    "nav.dashboard": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/>'
        '<rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/>'
        '<rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/>'
        '<rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/>'
        '</svg>'
    ),
    "nav.orbit_design": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<ellipse cx="12" cy="12" rx="9" ry="4" transform="rotate(-28 12 12)"/>'
        '<circle cx="12" cy="12" r="2.6" fill="' + _COLOR_TOKEN + '"/>'
        '<circle cx="19.4" cy="8.7" r="0.9" fill="' + _COLOR_TOKEN + '"/>'
        '</svg>'
    ),
    "nav.maneuver_strategy": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 2.5 C9.2 5.7 7.5 9 7.5 12.8 V18 H16.5 V12.8 C16.5 9 14.8 5.7 12 2.5 Z"/>'
        '<circle cx="12" cy="11" r="1.6"/>'
        '<path d="M7.5 14.5 L4.5 17 L4.5 21 L8.5 18.8"/>'
        '<path d="M16.5 14.5 L19.5 17 L19.5 21 L15.5 18.8"/>'
        '<path d="M9.8 21 L12 18.5 L14.2 21"/>'
        '</svg>'
    ),
    "nav.launch_window": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="5" width="18" height="16" rx="2.2"/>'
        '<path d="M3 10 L21 10"/>'
        '<path d="M8 3 L8 7"/>'
        '<path d="M16 3 L16 7"/>'
        '<circle cx="12" cy="15.5" r="2.6"/>'
        '<path d="M12 15.5 L12 13.7"/>'
        '<path d="M12 15.5 L13.6 16.5"/>'
        '</svg>'
    ),
    "nav.tracking_arc": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3.5 14 A9 9 0 0 1 20.5 14"/>'
        '<path d="M6.7 14 A6 6 0 0 1 17.3 14"/>'
        '<path d="M9.6 14 A3 3 0 0 1 14.4 14"/>'
        '<circle cx="12" cy="14" r="1.4" fill="' + _COLOR_TOKEN + '"/>'
        '<path d="M12 14 L12 20"/>'
        '</svg>'
    ),
    "nav.flight_program": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3.5 5.5 L5.4 7.4 L8 4.8"/>'
        '<path d="M3.5 12 L5.4 13.9 L8 11.3"/>'
        '<path d="M3.5 18.5 L5.4 20.4 L8 17.8"/>'
        '<path d="M11 6 L20.5 6"/>'
        '<path d="M11 12.5 L20.5 12.5"/>'
        '<path d="M11 19 L20.5 19"/>'
        '</svg>'
    ),
    "nav.data_visualization": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 4 L4 20 L20 20"/>'
        '<rect x="7" y="12" width="2.6" height="6"/>'
        '<rect x="11.5" y="8" width="2.6" height="10"/>'
        '<rect x="16" y="14" width="2.6" height="4"/>'
        '<path d="M7 9.5 L11.5 6 L16 10 L20 5.5" stroke-dasharray="0"/>'
        '</svg>'
    ),
    "nav.spice_kernels": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<ellipse cx="12" cy="5" rx="8" ry="2.6"/>'
        '<path d="M4 5 L4 12 C4 13.6 7.6 14.7 12 14.7 C16.4 14.7 20 13.6 20 12 L20 5"/>'
        '<path d="M4 12 L4 19 C4 20.6 7.6 21.7 12 21.7 C16.4 21.7 20 20.6 20 19 L20 12"/>'
        '</svg>'
    ),
    "nav.ai_project_analysis": (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M11 3 L13 9 L19 11 L13 13 L11 19 L9 13 L3 11 L9 9 Z"/>'
        '<path d="M19 16 L19.8 18.2 L22 19 L19.8 19.8 L19 22 L18.2 19.8 L16 19 L18.2 18.2 Z"/>'
        '</svg>'
    ),
}

_CHEVRON_LEFT_SVG = (
    '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
    'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M15 5 L8 12 L15 19"/>'
    '</svg>'
)

_CHEVRON_RIGHT_SVG = (
    '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
    'fill="none" stroke="' + _COLOR_TOKEN + '" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M9 5 L16 12 L9 19"/>'
    '</svg>'
)

_DEFAULT_COLOR = "#cfe2e8"
_SELECTED_COLOR = "#ffffff"
_DEFAULT_PIXMAP_SIZE = 28


def _render_svg_to_pixmap(svg: str, color: str, size: int) -> QtGui.QPixmap:
    payload = svg.replace(_COLOR_TOKEN, color).encode("utf-8")
    renderer = QSvgRenderer(QtCore.QByteArray(payload))
    device_ratio = 2.0
    pixmap = QtGui.QPixmap(int(size * device_ratio), int(size * device_ratio))
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(device_ratio)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    target = QtCore.QRectF(0, 0, size, size)
    renderer.render(painter, target)
    painter.end()
    return pixmap


def _build_icon(svg: str, size: int = _DEFAULT_PIXMAP_SIZE) -> QtGui.QIcon:
    icon = QtGui.QIcon()
    normal = _render_svg_to_pixmap(svg, _DEFAULT_COLOR, size)
    selected = _render_svg_to_pixmap(svg, _SELECTED_COLOR, size)
    icon.addPixmap(normal, QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
    icon.addPixmap(normal, QtGui.QIcon.Mode.Active, QtGui.QIcon.State.Off)
    icon.addPixmap(selected, QtGui.QIcon.Mode.Selected, QtGui.QIcon.State.Off)
    icon.addPixmap(selected, QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.On)
    return icon


def nav_icon(key: str, size: int = _DEFAULT_PIXMAP_SIZE) -> QtGui.QIcon:
    """根据导航键 ``nav.xxx`` 返回对应的 ``QIcon``。

    未注册的 key 会返回一个空 ``QIcon``，调用方可据此判定。
    """

    svg = _NAV_SVG.get(key)
    if svg is None:
        return QtGui.QIcon()
    return _build_icon(svg, size=size)


def chevron_icon(direction: str, size: int = 20) -> QtGui.QIcon:
    """侧边栏折叠/展开按钮使用的 chevron 图标。

    ``direction`` 取值 ``"left"`` 或 ``"right"``。
    """

    svg = _CHEVRON_LEFT_SVG if direction == "left" else _CHEVRON_RIGHT_SVG
    return _build_icon(svg, size=size)


def has_icon(key: str) -> bool:
    return key in _NAV_SVG
