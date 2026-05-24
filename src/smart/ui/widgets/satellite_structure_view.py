from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtWidgets

from smart.domain.models import SatelliteStructureConfig

try:
    import pyqtgraph.opengl as gl
except Exception:  # pragma: no cover - depends on local OpenGL runtime
    gl = None

try:
    import trimesh
except Exception:  # pragma: no cover - optional dependency for DAE loading
    trimesh = None

_SUPPORTED_MODEL_EXTENSIONS = (".glb", ".gltf", ".dae")
_UNSUPPORTED_STK_EXTENSIONS = (".mdl",)


def _unit_box_mesh() -> object:
    assert gl is not None
    vertexes = np.array(
        [
            [-0.5, -0.5, -0.5],
            [0.5, -0.5, -0.5],
            [0.5, 0.5, -0.5],
            [-0.5, 0.5, -0.5],
            [-0.5, -0.5, 0.5],
            [0.5, -0.5, 0.5],
            [0.5, 0.5, 0.5],
            [-0.5, 0.5, 0.5],
        ],
        dtype=float,
    )
    faces = np.array(
        [
            [0, 1, 2],
            [0, 2, 3],
            [4, 6, 5],
            [4, 7, 6],
            [0, 4, 5],
            [0, 5, 1],
            [1, 5, 6],
            [1, 6, 2],
            [2, 6, 7],
            [2, 7, 3],
            [3, 7, 4],
            [3, 4, 0],
        ],
        dtype=np.uint32,
    )
    return gl.MeshData(vertexes=vertexes, faces=faces)


def _sequence_positions(count: int, spacing: float, center: float = 0.0) -> list[float]:
    if count <= 1:
        return [center]
    start = center - (count - 1) * spacing * 0.5
    return [start + index * spacing for index in range(count)]


class SatelliteStructureView(QtWidgets.QWidget):
    status_changed = QtCore.Signal(object)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._last_status: dict[str, str] = {"state": "parametric_model"}

        if gl is None:
            message = QtWidgets.QLabel(
                "3D structure preview is unavailable because the OpenGL stack could not be initialized."
            )
            message.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            message.setWordWrap(True)
            message.setProperty("role", "pageBody")
            layout.addWidget(message)
            self._view = None
            self._body = None
            self._dynamic_items: list[object] = []
            self._set_status("opengl_unavailable")
            return

        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor("#edf2f4")
        self._view.setCameraPosition(distance=18.0, elevation=16.0, azimuth=36.0)
        layout.addWidget(self._view)

        axis = gl.GLAxisItem()
        axis.setSize(6.0, 6.0, 6.0)
        self._view.addItem(axis)

        grid = gl.GLGridItem()
        grid.setSize(16.0, 16.0)
        grid.setSpacing(1.0, 1.0, 1.0)
        grid.translate(0.0, 0.0, -2.4)
        self._view.addItem(grid)

        self._cube_mesh = _unit_box_mesh()
        self._antenna_mesh = gl.MeshData.sphere(rows=18, cols=24, radius=0.5)
        self._body = gl.GLMeshItem(
            meshdata=self._cube_mesh,
            smooth=False,
            shader="shaded",
            drawEdges=True,
            edgeColor=(0.48, 0.28, 0.10, 0.72),
            color=(0.92, 0.56, 0.14, 1.0),
        )
        self._view.addItem(self._body)
        self._dynamic_items: list[object] = []
        self._model_cache: dict[
            str,
            tuple[
                list[
                    tuple[
                        np.ndarray,
                        np.ndarray,
                        tuple[float, float, float, float],
                        tuple[float, float, float, float],
                    ]
                ],
                np.ndarray,
            ],
        ] = {}
        self.set_structure(SatelliteStructureConfig())

    def set_structure(self, structure: SatelliteStructureConfig) -> None:
        if self._view is None or self._body is None:
            return

        for item in self._dynamic_items:
            self._view.removeItem(item)
        self._dynamic_items.clear()

        model_loaded = self._render_external_model(structure)
        self._body.setVisible(not model_loaded)
        if model_loaded:
            self._set_status("model_loaded", path=structure.model_path.strip())
            return

        body_x = max(structure.body_size_x_m, 0.4)
        body_y = max(structure.body_size_y_m, 0.4)
        body_z = max(structure.body_size_z_m, 0.6)
        antenna_major = max(structure.antenna_major_axis_m, 0.2)
        antenna_minor = max(structure.antenna_minor_axis_m, 0.1)
        antenna_depth = max(structure.antenna_depth_m, 0.05)
        panel_span = max(structure.solar_panel_span_m, 0.2)
        panel_width = max(structure.solar_panel_width_m, 0.2)
        panel_gap = max(structure.solar_panel_gap_m, 0.0)
        panel_count = max(structure.solar_panels_per_wing, 1)
        panel_thickness = max(min(panel_width * 0.04, 0.08), 0.03)

        self._body.resetTransform()
        self._body.scale(body_x, body_y, body_z)

        self._add_antennas(
            side_sign=1.0,
            count=max(structure.east_antenna_count, 0),
            body_x=body_x,
            body_y=body_y,
            body_z=body_z,
            antenna_major=antenna_major,
            antenna_minor=antenna_minor,
            antenna_depth=antenna_depth,
        )
        self._add_antennas(
            side_sign=-1.0,
            count=max(structure.west_antenna_count, 0),
            body_x=body_x,
            body_y=body_y,
            body_z=body_z,
            antenna_major=antenna_major,
            antenna_minor=antenna_minor,
            antenna_depth=antenna_depth,
        )
        self._add_wings(
            side_sign=1.0,
            wing_count=max(structure.north_wing_count, 0),
            body_x=body_x,
            body_y=body_y,
            body_z=body_z,
            panel_count=panel_count,
            panel_span=panel_span,
            panel_width=panel_width,
            panel_gap=panel_gap,
            panel_thickness=panel_thickness,
        )
        self._add_wings(
            side_sign=-1.0,
            wing_count=max(structure.south_wing_count, 0),
            body_x=body_x,
            body_y=body_y,
            body_z=body_z,
            panel_count=panel_count,
            panel_span=panel_span,
            panel_width=panel_width,
            panel_gap=panel_gap,
            panel_thickness=panel_thickness,
        )

        span_x = body_x + 2.0 * (panel_count * panel_span + max(panel_count - 1, 0) * panel_gap)
        span_y = max(body_y + 2.0 * antenna_major, body_y + 2.0 * panel_thickness)
        span_z = max(body_z, panel_width * max(structure.north_wing_count, structure.south_wing_count, 1))
        distance = max(span_x, span_y, span_z) * 4.4
        self._view.setCameraPosition(distance=max(distance * 0.6, 8.0), elevation=22.0, azimuth=52.0)

    def _render_external_model(
        self,
        structure: SatelliteStructureConfig,
    ) -> bool:
        if self._view is None or trimesh is None:
            if structure.model_path.strip():
                self._set_status("model_support_unavailable")
            return False

        raw_path = structure.model_path.strip().strip('"')
        if not raw_path:
            self._set_status("parametric_model")
            return False

        model_path = self._resolve_model_path(raw_path)
        if model_path is None:
            return False

        cached = self._load_model_meshes(model_path)
        if cached is None:
            self._set_status("model_load_failed", path=str(model_path))
            return False

        mesh_packets, bounds = cached
        overall_extents = np.maximum(bounds[1] - bounds[0], 1e-6)
        center = bounds.mean(axis=0)
        for vertexes, faces, color, edge_color in mesh_packets:
            transformed = vertexes - center
            mesh = gl.MeshData(vertexes=transformed, faces=faces)
            item = gl.GLMeshItem(
                meshdata=mesh,
                smooth=True,
                shader="shaded",
                drawEdges=True,
                edgeColor=edge_color,
                color=color,
            )
            self._view.addItem(item)
            self._dynamic_items.append(item)
        distance = float(np.max(overall_extents)) * 2.4
        self._view.setCameraPosition(distance=max(distance, 10.0), elevation=18.0, azimuth=38.0)
        return True

    def _resolve_model_path(self, raw_path: str) -> Path | None:
        candidate = Path(raw_path).expanduser()
        suffix = candidate.suffix.lower()

        if suffix in _UNSUPPORTED_STK_EXTENSIONS:
            self._set_status("mdl_unsupported", path=str(candidate))
            return None

        if suffix in _SUPPORTED_MODEL_EXTENSIONS:
            if candidate.exists():
                return candidate
            self._set_status("model_not_found", path=str(candidate))
            return None

        if suffix:
            self._set_status("model_invalid_extension", path=raw_path)
            return None

        candidates = [candidate.with_suffix(ext) for ext in _SUPPORTED_MODEL_EXTENSIONS]
        for resolved in candidates:
            if resolved.exists():
                return resolved

        self._set_status("model_not_found", path=raw_path)
        return None

    def _load_model_meshes(
        self,
        model_path: Path,
    ) -> tuple[
        list[
            tuple[
                np.ndarray,
                np.ndarray,
                tuple[float, float, float, float],
                tuple[float, float, float, float],
            ]
        ],
        np.ndarray,
    ] | None:
        cache_key = str(model_path.resolve())
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]
        if trimesh is None:
            return None

        try:
            scene = trimesh.load(cache_key, force="scene")
        except Exception:
            return None

        mesh_packets: list[
            tuple[
                np.ndarray,
                np.ndarray,
                tuple[float, float, float, float],
                tuple[float, float, float, float],
            ]
        ] = []
        bounds = np.asarray(scene.bounds, dtype=float)
        for mesh_name, mesh in scene.geometry.items():
            vertexes = np.asarray(mesh.vertices, dtype=float)
            faces = np.asarray(mesh.faces, dtype=np.uint32)
            if len(vertexes) == 0 or len(faces) == 0:
                continue
            mesh_packets.append((vertexes, faces, *self._resolve_mesh_style(mesh_name, mesh)))

        if not mesh_packets:
            return None

        cached = (mesh_packets, bounds)
        self._model_cache[cache_key] = cached
        return cached

    def _add_antennas(
        self,
        *,
        side_sign: float,
        count: int,
        body_x: float,
        body_y: float,
        body_z: float,
        antenna_major: float,
        antenna_minor: float,
        antenna_depth: float,
    ) -> None:
        assert self._view is not None
        if count <= 0:
            return

        z_positions = _sequence_positions(count, max(antenna_minor * 1.35, 0.35), center=-body_z * 0.12)
        z_limit = max(body_z * 0.36, 0.0)
        for index, z_center in enumerate(z_positions):
            antenna = gl.GLMeshItem(
                meshdata=self._antenna_mesh,
                smooth=True,
                shader="shaded",
                drawEdges=False,
                color=(0.82, 0.84, 0.84, 0.98),
            )
            antenna.scale(antenna_minor, antenna_depth, antenna_major)
            x_offset = (index - max(count - 1, 0) * 0.5) * min(antenna_minor * 0.55, body_x * 0.22)
            antenna.translate(
                x_offset,
                side_sign * (body_y * 0.5 + antenna_depth * 0.55),
                float(np.clip(z_center, -z_limit, z_limit)),
            )
            self._view.addItem(antenna)
            self._dynamic_items.append(antenna)

    def _add_wings(
        self,
        *,
        side_sign: float,
        wing_count: int,
        body_x: float,
        body_y: float,
        body_z: float,
        panel_count: int,
        panel_span: float,
        panel_width: float,
        panel_gap: float,
        panel_thickness: float,
    ) -> None:
        assert self._view is not None
        if wing_count <= 0:
            return

        z_positions = _sequence_positions(wing_count, panel_width * 1.18)
        z_limit = max(body_z * 0.38, 0.0)
        for z_center in z_positions:
            clipped_z = float(np.clip(z_center, -z_limit, z_limit))
            for index in range(panel_count):
                panel = gl.GLMeshItem(
                    meshdata=self._cube_mesh,
                    smooth=False,
                    shader="shaded",
                    drawEdges=True,
                    edgeColor=(0.16, 0.22, 0.38, 0.55),
                    color=(0.12, 0.24, 0.48, 0.96),
                )
                panel.scale(panel_span, panel_thickness, panel_width)
                x_center = side_sign * (body_x * 0.5 + panel_span * (index + 0.5) + panel_gap * index)
                panel.translate(x_center, 0.0, clipped_z)
                self._view.addItem(panel)
                self._dynamic_items.append(panel)

    def _set_status(self, state: str, *, path: str = "") -> None:
        payload = {"state": state, "path": path}
        self._last_status = payload
        self.status_changed.emit(payload)

    @property
    def last_status(self) -> dict[str, str]:
        return dict(self._last_status)

    @staticmethod
    def _resolve_mesh_style(
        mesh_name: str,
        mesh: object,
    ) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
        visual = getattr(mesh, "visual", None)
        material = getattr(visual, "material", None)
        visual_kind = str(getattr(visual, "kind", "")).lower()
        material_name = ""
        data = getattr(material, "_data", None)
        if isinstance(data, dict):
            material_name = str(data.get("name", ""))
        tokens = f"{mesh_name} {material_name}".lower()

        if visual_kind == "texture":
            color = _heuristic_model_color(tokens)
            return color, _edge_color_for(color)

        main_color = getattr(material, "main_color", None)
        if main_color is not None and len(main_color) >= 4:
            rgba = np.asarray(main_color[:4], dtype=float) / 255.0
            color = _lift_color((float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3])))
            return color, _edge_color_for(color)

        face_colors = getattr(visual, "face_colors", None)
        if face_colors is not None and len(face_colors) > 0:
            rgba = np.asarray(face_colors[0][:4], dtype=float) / 255.0
            color = _lift_color((float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3])))
            return color, _edge_color_for(color)

        color = _heuristic_model_color(tokens)
        return color, _edge_color_for(color)


def _heuristic_model_color(tokens: str) -> tuple[float, float, float, float]:
    if any(token in tokens for token in ("solar", "panel", "cell", "array")):
        return (0.20, 0.36, 0.62, 0.98)
    if any(token in tokens for token in ("antenna", "dish", "reflector")):
        return (0.77, 0.67, 0.40, 0.98)
    if any(token in tokens for token in ("thruster", "engine", "nozzle")):
        return (0.46, 0.48, 0.54, 0.98)
    if any(token in tokens for token in ("sensor", "camera", "optic")):
        return (0.26, 0.50, 0.54, 0.98)
    if any(token in tokens for token in ("support", "frame", "arm", "truss", "rod")):
        return (0.58, 0.60, 0.64, 0.98)
    return (0.76, 0.78, 0.81, 0.98)


def _lift_color(color: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    rgb = np.array(color[:3], dtype=float)
    alpha = float(color[3])
    luminance = float(np.dot(rgb, np.array([0.2126, 0.7152, 0.0722], dtype=float)))
    if luminance < 0.48:
        rgb = np.clip(rgb * 0.55 + np.array([0.45, 0.47, 0.50], dtype=float), 0.0, 1.0)
    return float(rgb[0]), float(rgb[1]), float(rgb[2]), alpha


def _edge_color_for(color: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    rgb = np.array(color[:3], dtype=float)
    edge = np.clip(rgb * 0.36, 0.08, 0.35)
    return float(edge[0]), float(edge[1]), float(edge[2]), 0.55
