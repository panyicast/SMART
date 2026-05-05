from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from smart.domain.models import OrbitTrajectory

try:
    import pyqtgraph.opengl as gl
except Exception:  # pragma: no cover - depends on local OpenGL runtime
    gl = None

_EARTH_TEXTURE_PATH = Path(__file__).resolve().parents[2] / "assets" / "textures" / "earth_day_2048.png"
_PLOT_BACKGROUND = "#071016"
_PLOT_AXIS = "#9fb5bf"
_PLOT_GRID = "#244958"
_ORBIT_CYAN = "#66d9ea"
_ORBIT_AMBER = "#f2b84b"


def _normalize_vector(vector: np.ndarray) -> np.ndarray | None:
    values = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(values))
    if not np.isfinite(norm) or norm <= 1.0e-12:
        return None
    return values / norm


def _perpendicular_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    fallback = np.array([0.0, 0.0, 1.0], dtype=float)
    if abs(float(np.dot(direction, fallback))) > 0.92:
        fallback = np.array([0.0, 1.0, 0.0], dtype=float)
    right = np.cross(direction, fallback)
    right_unit = _normalize_vector(right)
    if right_unit is None:
        right_unit = np.array([1.0, 0.0, 0.0], dtype=float)
    up = np.cross(right_unit, direction)
    up_unit = _normalize_vector(up)
    if up_unit is None:
        up_unit = np.array([0.0, 1.0, 0.0], dtype=float)
    return right_unit, up_unit


def _arrow_head_mesh(tip: np.ndarray, direction: np.ndarray, length: float, radius: float) -> object:
    assert gl is not None
    right, up = _perpendicular_basis(direction)
    base_center = tip - direction * length
    segments = 18
    vertices = [tip]
    for index in range(segments):
        angle = 2.0 * np.pi * index / segments
        vertices.append(base_center + radius * (np.cos(angle) * right + np.sin(angle) * up))
    faces: list[list[int]] = []
    for index in range(segments):
        faces.append([0, index + 1, 1 + ((index + 1) % segments)])
    return gl.MeshData(vertexes=np.asarray(vertices, dtype=float), faces=np.asarray(faces, dtype=np.int32))


def _load_texture_rgba(texture_path: Path) -> np.ndarray | None:
    image = QtGui.QImage(str(texture_path))
    if image.isNull():
        return None
    image = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    raw = np.frombuffer(image.bits(), dtype=np.uint8, count=image.sizeInBytes()).copy()
    height = image.height()
    width = image.width()
    bytes_per_line = image.bytesPerLine()
    return raw.reshape((height, bytes_per_line))[:, : width * 4].reshape((height, width, 4))


def _build_earth_mesh(rows: int = 72, cols: int = 144) -> tuple[object, bool]:
    assert gl is not None
    mesh = gl.MeshData.sphere(rows=rows, cols=cols, radius=1.0)
    texture = _load_texture_rgba(_EARTH_TEXTURE_PATH)
    if texture is None:
        return mesh, False

    vertexes = mesh.vertexes().astype(np.float64)
    norms = np.linalg.norm(vertexes, axis=1, keepdims=True)
    unit_vectors = vertexes / np.where(norms == 0.0, 1.0, norms)

    longitudes = np.arctan2(unit_vectors[:, 1], unit_vectors[:, 0])
    latitudes = np.arcsin(np.clip(unit_vectors[:, 2], -1.0, 1.0))
    u = (longitudes + np.pi) / (2.0 * np.pi)
    v = (np.pi / 2.0 - latitudes) / np.pi

    tex_height, tex_width, _ = texture.shape
    x_idx = np.clip((u * (tex_width - 1)).astype(np.int32), 0, tex_width - 1)
    y_idx = np.clip((v * (tex_height - 1)).astype(np.int32), 0, tex_height - 1)

    rgb = texture[y_idx, x_idx, :3].astype(np.float32) / 255.0
    alpha = np.ones((rgb.shape[0], 1), dtype=np.float32)
    vertex_colors = np.concatenate([rgb, alpha], axis=1)
    mesh.setVertexColors(vertex_colors)
    return mesh, True


class OrbitPlot2D(pg.PlotWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self.setBackground(_PLOT_BACKGROUND)
        self.showGrid(x=True, y=True, alpha=0.18)
        self.setMenuEnabled(False)
        self.setAspectLocked(True)
        self.plotItem.hideButtons()
        self.plotItem.getViewBox().setBackgroundColor(_PLOT_BACKGROUND)
        self.plotItem.getViewBox().setBorder(pg.mkPen("#1e3b49", width=1))
        label_style = {"color": _PLOT_AXIS, "font-size": "10pt"}
        self.plotItem.setLabel("left", "Y", units="km", **label_style)
        self.plotItem.setLabel("bottom", "X", units="km", **label_style)
        for axis_name in ("left", "bottom"):
            axis = self.getAxis(axis_name)
            axis.setPen(pg.mkPen(_PLOT_GRID, width=1))
            axis.setTextPen(pg.mkPen(_PLOT_AXIS))
            axis.setStyle(tickFont=QtGui.QFont("Noto Sans SC", 9), tickTextOffset=8)

        self._earth_item = QtWidgets.QGraphicsEllipseItem()
        self._earth_item.setPen(QtGui.QPen(QtGui.QColor("#2d7788"), 2))
        self._earth_item.setBrush(QtGui.QBrush(QtGui.QColor(22, 78, 92, 170)))
        self.plotItem.addItem(self._earth_item)

        self._orbit_item = self.plot(pen=pg.mkPen(_ORBIT_CYAN, width=2.6))
        self._marker_item = self.plot(
            pen=None,
            symbol="o",
            symbolSize=10,
            symbolBrush=_ORBIT_AMBER,
            symbolPen=pg.mkPen("#f8d07a", width=1.2),
        )

    def set_trajectory(self, trajectory: OrbitTrajectory, body_radius_km: float) -> None:
        positions = trajectory.positions_km
        self._orbit_item.setData(positions[:, 0], positions[:, 1])
        self._marker_item.setData(
            [trajectory.current_position_km[0]],
            [trajectory.current_position_km[1]],
        )
        self._earth_item.setRect(
            -body_radius_km,
            -body_radius_km,
            2.0 * body_radius_km,
            2.0 * body_radius_km,
        )

        span = float(np.max(np.linalg.norm(positions[:, :2], axis=1))) * 1.15
        self.setXRange(-span, span, padding=0.0)
        self.setYRange(-span, span, padding=0.0)


class OrbitPlot3D(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._orbit_color = (0.40, 0.85, 0.92, 1.0)
        self._marker_color = (0.95, 0.72, 0.29, 1.0)
        self._maneuver_color = (1.0, 0.05, 0.02, 1.0)
        self._start_marker_color = (0.58, 1.0, 0.16, 1.0)
        self._orbit_width = 2.2
        self._maneuver_lines: list[object] = []
        self._direction_lines: list[object] = []
        self._direction_heads: list[object] = []
        self._direction_labels: list[object] = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._info_overlay = QtWidgets.QLabel(self)
        self._info_overlay.setWordWrap(True)
        self._info_overlay.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        self._info_overlay.setStyleSheet(
            "QLabel {"
            "background: rgba(3, 8, 16, 188);"
            "border: 1px solid rgba(102, 217, 234, 140);"
            "border-radius: 8px;"
            "color: #dff6ff;"
            "font-family: 'Noto Sans SC', 'Segoe UI';"
            "font-size: 12px;"
            "padding: 8px 10px;"
            "}"
        )
        self._info_overlay.hide()

        if gl is None:
            message = QtWidgets.QLabel(
                "3D view is unavailable because the OpenGL stack could not be initialized."
            )
            message.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            message.setWordWrap(True)
            message.setProperty("role", "pageBody")
            layout.addWidget(message)
            self._view = None
            self._line = None
            self._marker = None
            self._start_marker = None
            self._start_label = None
            self._body = None
            return

        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor(_PLOT_BACKGROUND)
        self._view.setCameraPosition(distance=22000.0, elevation=24.0, azimuth=40.0)
        layout.addWidget(self._view)

        axis = gl.GLAxisItem()
        axis.setSize(12000.0, 12000.0, 12000.0)
        self._view.addItem(axis)

        grid = gl.GLGridItem()
        grid.setSpacing(2500.0, 2500.0, 2500.0)
        grid.setSize(30000.0, 30000.0)
        self._view.addItem(grid)

        mesh, has_texture = _build_earth_mesh()
        body_options: dict[str, object] = {
            "meshdata": mesh,
            "smooth": True,
            "shader": "shaded",
            "drawEdges": False,
        }
        if not has_texture:
            body_options["color"] = (0.48, 0.66, 0.73, 0.75)
        self._body = gl.GLMeshItem(**body_options)
        self._view.addItem(self._body)

        self._line = gl.GLLinePlotItem(
            pos=np.zeros((2, 3), dtype=float),
            color=self._orbit_color,
            width=self._orbit_width,
            antialias=True,
            mode="line_strip",
        )
        self._view.addItem(self._line)

        self._marker = gl.GLScatterPlotItem(
            pos=np.zeros((1, 3), dtype=float),
            color=self._marker_color,
            size=12.0,
            pxMode=True,
        )
        self._view.addItem(self._marker)

        self._start_marker = gl.GLScatterPlotItem(
            pos=np.zeros((1, 3), dtype=float),
            color=self._start_marker_color,
            size=14.0,
            pxMode=True,
        )
        self._start_marker.setVisible(False)
        self._view.addItem(self._start_marker)

        self._start_label = None
        if hasattr(gl, "GLTextItem"):
            font = QtGui.QFont("Noto Sans SC", 12)
            font.setBold(True)
            self._start_label = gl.GLTextItem(
                pos=np.zeros(3, dtype=float),
                color=QtGui.QColor("#9cff57"),
                text="",
                font=font,
            )
            self._start_label.setVisible(False)
            self._view.addItem(self._start_label)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._position_info_overlay()

    def set_info_overlay(self, text: str) -> None:
        clean_text = text.strip()
        self._info_overlay.setText(clean_text)
        self._info_overlay.setVisible(bool(clean_text))
        self._position_info_overlay()

    def _position_info_overlay(self) -> None:
        if not self._info_overlay.text().strip():
            return
        margin = 12
        width = min(max(self.width() - margin * 2, 180), 360)
        self._info_overlay.setFixedWidth(width)
        self._info_overlay.adjustSize()
        self._info_overlay.move(margin, margin)
        self._info_overlay.raise_()

    def set_visual_style(
        self,
        *,
        background_color: str = _PLOT_BACKGROUND,
        orbit_color: tuple[float, float, float, float] = (0.40, 0.85, 0.92, 1.0),
        marker_color: tuple[float, float, float, float] = (0.95, 0.72, 0.29, 1.0),
        orbit_width: float = 2.2,
    ) -> None:
        if self._view is None:
            return

        self._orbit_color = orbit_color
        self._marker_color = marker_color
        self._orbit_width = orbit_width
        assert self._line is not None
        assert self._marker is not None
        self._view.setBackgroundColor(background_color)
        self._line.setData(color=self._orbit_color, width=self._orbit_width)
        self._marker.setData(color=self._marker_color)

    def set_trajectory(self, trajectory: OrbitTrajectory, body_radius_km: float) -> None:
        self.set_trajectory_overlays(
            trajectory,
            body_radius_km,
            maneuver_segments_km=None,
            start_label=None,
        )

    def clear_trajectory(self) -> None:
        if self._view is None:
            return

        assert self._line is not None
        assert self._marker is not None
        assert self._start_marker is not None
        assert self._body is not None

        self._line.setVisible(False)
        self._marker.setVisible(False)
        self._start_marker.setVisible(False)
        self._body.setVisible(False)
        if self._start_label is not None:
            self._start_label.setVisible(False)
        self._set_maneuver_segments(None)
        self.set_direction_vectors(None, [])

    def set_trajectory_overlays(
        self,
        trajectory: OrbitTrajectory,
        body_radius_km: float,
        *,
        maneuver_segments_km: Sequence[np.ndarray] | None = None,
        start_label: str | None = None,
    ) -> None:
        if self._view is None:
            return

        assert self._line is not None
        assert self._marker is not None
        assert self._start_marker is not None
        assert self._body is not None

        self._line.setVisible(True)
        self._marker.setVisible(True)
        self._body.setVisible(True)
        self._body.resetTransform()
        self._body.scale(body_radius_km, body_radius_km, body_radius_km)
        self._line.setData(
            pos=trajectory.positions_km.astype(float),
            color=self._orbit_color,
            width=self._orbit_width,
        )
        self._marker.setData(
            pos=trajectory.current_position_km.reshape(1, 3).astype(float),
            color=self._marker_color,
        )
        self._set_start_marker(trajectory.positions_km[0], start_label)
        self._set_maneuver_segments(maneuver_segments_km)

        max_radius = float(np.max(trajectory.radii_km))
        self._view.setCameraPosition(distance=max_radius * 2.4, elevation=22.0, azimuth=45.0)

    def set_direction_vectors(
        self,
        origin_km: np.ndarray | Sequence[float] | None,
        vectors: Sequence[dict[str, object]],
    ) -> None:
        if self._view is None:
            return

        origin = None if origin_km is None else np.asarray(origin_km, dtype=float).reshape(3)
        valid_vectors: list[tuple[str, np.ndarray, tuple[float, float, float, float], float]] = []
        if origin is not None:
            base_length = max(float(np.linalg.norm(origin)) * 0.32, 3200.0)
            for item in vectors:
                direction = _normalize_vector(np.asarray(item.get("direction", []), dtype=float))
                if direction is None:
                    continue
                label = str(item.get("label", ""))
                color = item.get("color", (1.0, 1.0, 1.0, 1.0))
                rgba = tuple(float(value) for value in color)  # type: ignore[arg-type]
                length_km = float(item.get("length_km", base_length))
                valid_vectors.append((label, direction, rgba, length_km))

        while len(self._direction_lines) > len(valid_vectors):
            item = self._direction_lines.pop()
            self._view.removeItem(item)
        while len(self._direction_heads) > len(valid_vectors):
            item = self._direction_heads.pop()
            self._view.removeItem(item)
        while len(self._direction_labels) > len(valid_vectors):
            item = self._direction_labels.pop()
            self._view.removeItem(item)

        while len(self._direction_lines) < len(valid_vectors):
            item = gl.GLLinePlotItem(
                pos=np.zeros((2, 3), dtype=float),
                color=(1.0, 1.0, 1.0, 1.0),
                width=max(self._orbit_width + 0.9, 3.2),
                antialias=True,
                mode="line_strip",
            )
            self._view.addItem(item)
            self._direction_lines.append(item)

        while len(self._direction_heads) < len(valid_vectors):
            item = gl.GLMeshItem(meshdata=_arrow_head_mesh(np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), 1.0, 0.35))
            self._view.addItem(item)
            self._direction_heads.append(item)

        if hasattr(gl, "GLTextItem"):
            while len(self._direction_labels) < len(valid_vectors):
                item = gl.GLTextItem(pos=np.zeros(3, dtype=float), text="")
                self._view.addItem(item)
                self._direction_labels.append(item)

        for index, (label, direction, color, length_km) in enumerate(valid_vectors):
            assert origin is not None
            tip = origin + direction * length_km
            head_length = min(max(length_km * 0.18, 500.0), 1800.0)
            shaft_end = tip - direction * head_length * 0.55
            self._direction_lines[index].setData(
                pos=np.vstack([origin, shaft_end]),
                color=color,
                width=max(self._orbit_width + 0.9, 3.2),
            )
            self._direction_heads[index].setMeshData(
                meshdata=_arrow_head_mesh(tip, direction, head_length, head_length * 0.36)
            )
            self._direction_heads[index].setColor(color)
            if index < len(self._direction_labels):
                label_item = self._direction_labels[index]
                label_item.setData(
                    pos=tip + direction * max(head_length * 0.25, 200.0),
                    text=label,
                    color=QtGui.QColor.fromRgbF(color[0], color[1], color[2], color[3]),
                )

    def _set_start_marker(self, position_km: np.ndarray, label: str | None) -> None:
        assert self._start_marker is not None
        if label is None:
            self._start_marker.setVisible(False)
            if self._start_label is not None:
                self._start_label.setVisible(False)
            return

        position = np.asarray(position_km, dtype=float).reshape(1, 3)
        self._start_marker.setData(pos=position, color=self._start_marker_color)
        self._start_marker.setVisible(True)

        if self._start_label is not None:
            offset = float(np.linalg.norm(position[0])) * 0.025
            label_position = position[0].copy()
            label_position[2] += max(offset, 250.0)
            self._start_label.setData(pos=label_position, text=label)
            self._start_label.setVisible(bool(label))

    def _set_maneuver_segments(self, segments: Sequence[np.ndarray] | None) -> None:
        if self._view is None:
            return

        valid_segments: list[np.ndarray] = []
        for segment in segments or ():
            segment_positions = np.asarray(segment, dtype=float)
            if segment_positions.ndim != 2 or segment_positions.shape[1] != 3:
                continue
            if segment_positions.shape[0] < 2:
                continue
            valid_segments.append(segment_positions)

        while len(self._maneuver_lines) > len(valid_segments):
            item = self._maneuver_lines.pop()
            self._view.removeItem(item)

        while len(self._maneuver_lines) < len(valid_segments):
            item = gl.GLLinePlotItem(
                pos=np.zeros((2, 3), dtype=float),
                color=self._maneuver_color,
                width=max(self._orbit_width + 1.6, 4.2),
                antialias=True,
                mode="line_strip",
            )
            self._view.addItem(item)
            self._maneuver_lines.append(item)

        for item, segment in zip(self._maneuver_lines, valid_segments, strict=True):
            item.setData(
                pos=segment,
                color=self._maneuver_color,
                width=max(self._orbit_width + 1.6, 4.2),
            )
