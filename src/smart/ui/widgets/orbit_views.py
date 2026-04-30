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
        self.setBackground("#fffdf8")
        self.showGrid(x=True, y=True, alpha=0.18)
        self.setMenuEnabled(False)
        self.setAspectLocked(True)
        self.plotItem.hideButtons()
        self.plotItem.setLabel("left", "Y", units="km")
        self.plotItem.setLabel("bottom", "X", units="km")

        self._earth_item = QtWidgets.QGraphicsEllipseItem()
        self._earth_item.setPen(QtGui.QPen(QtGui.QColor("#7ca1b5"), 2))
        self._earth_item.setBrush(QtGui.QBrush(QtGui.QColor("#dbe9ef")))
        self.plotItem.addItem(self._earth_item)

        self._orbit_item = self.plot(pen=pg.mkPen("#0f7b8c", width=2.4))
        self._marker_item = self.plot(
            pen=None,
            symbol="o",
            symbolSize=10,
            symbolBrush="#c25c38",
            symbolPen=pg.mkPen("#8f3d21", width=1.2),
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
        self._orbit_color = (0.06, 0.48, 0.55, 1.0)
        self._marker_color = (0.76, 0.36, 0.22, 1.0)
        self._maneuver_color = (1.0, 0.05, 0.02, 1.0)
        self._start_marker_color = (0.58, 1.0, 0.16, 1.0)
        self._orbit_width = 2.2
        self._maneuver_lines: list[object] = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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
        self._view.setBackgroundColor("#e7edf1")
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
            font = QtGui.QFont("Microsoft YaHei UI", 12)
            font.setBold(True)
            self._start_label = gl.GLTextItem(
                pos=np.zeros(3, dtype=float),
                color=QtGui.QColor("#9cff57"),
                text="",
                font=font,
            )
            self._start_label.setVisible(False)
            self._view.addItem(self._start_label)

    def set_visual_style(
        self,
        *,
        background_color: str = "#e7edf1",
        orbit_color: tuple[float, float, float, float] = (0.06, 0.48, 0.55, 1.0),
        marker_color: tuple[float, float, float, float] = (0.76, 0.36, 0.22, 1.0),
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
