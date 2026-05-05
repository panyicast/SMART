from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from smart.domain.models import EARTH_RADIUS_KM, OrbitalElements, OrbitTrajectory, SatelliteStatusSettings

try:
    from PySide6 import QtWebChannel, QtWebEngineCore, QtWebEngineWidgets
except Exception:  # pragma: no cover - depends on local Qt runtime
    QtWebChannel = None
    QtWebEngineCore = None
    QtWebEngineWidgets = None

_ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets"
_CESIUM_HTML_PATH = _ASSET_ROOT / "cesium" / "mission_view.html"
_EARTH_TEXTURE_PATH = _ASSET_ROOT / "textures" / "earth_day_2048.png"
_CESIUM_MODEL_EXTENSIONS = {".glb", ".gltf"}
_GEOSTATIONARY_ALTITUDE_M = 35_786_000.0
_LOGGER = logging.getLogger(__name__)
_CAMERA_MODE_EARTH = "earth"
_CAMERA_MODE_SPACECRAFT = "spacecraft"


def _as_file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _parse_geo_slot_longitude(slot: str) -> float | None:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*([EW])\b", slot.upper())
    if match is None:
        return None
    magnitude = float(match.group(1))
    return magnitude if match.group(2) == "E" else -magnitude


def _resolve_model_payload(model_path: str) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    raw = model_path.strip().strip('"')
    if not raw:
        return None, None

    candidate = Path(raw).expanduser()
    name = candidate.name or raw
    if not candidate.exists():
        return None, {"detail_key": "model_missing", "detail_name": name}

    if candidate.suffix.lower() not in _CESIUM_MODEL_EXTENSIONS:
        return None, {"detail_key": "model_unsupported", "detail_name": name}

    return {
        "name": name,
        "url": _as_file_uri(candidate),
    }, {"detail_key": "model_loaded", "detail_name": name}


def build_scene_payload(
    elements: OrbitalElements,
    trajectory: OrbitTrajectory,
    settings: SatelliteStatusSettings,
    *,
    satellite_label: str = "Mission Spacecraft",
    scene_overlays: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    orbit_positions_m = (trajectory.positions_km * 1000.0).astype(float)
    if len(orbit_positions_m) > 0:
        orbit_positions_m = np.vstack([orbit_positions_m, orbit_positions_m[0]])

    model_payload, model_note = _resolve_model_payload(settings.structure.model_path)
    relay_satellites: list[dict[str, Any]] = []
    for item in settings.relay_satellites:
        longitude_deg = _parse_geo_slot_longitude(item.orbital_slot_orbit)
        if longitude_deg is None:
            continue
        relay_satellites.append(
            {
                "name": item.name,
                "band": item.band,
                "longitudeDeg": longitude_deg,
                "altitudeM": _GEOSTATIONARY_ALTITUDE_M,
            }
        )

    payload: dict[str, Any] = {
        "centralBodyName": elements.central_body_name,
        "earthTextureUrl": _as_file_uri(_EARTH_TEXTURE_PATH) if _EARTH_TEXTURE_PATH.exists() else "",
        "satelliteLabel": satellite_label,
        "orbitPositionsM": orbit_positions_m.tolist(),
        "currentPositionM": (trajectory.current_position_km * 1000.0).astype(float).tolist(),
        "currentVelocityMps": (trajectory.current_velocity_km_s * 1000.0).astype(float).tolist(),
        "cameraRangeM": max(float(np.max(trajectory.radii_km)) * 2400.0, EARTH_RADIUS_KM * 2000.0),
        "groundAssets": [
            {
                "name": item.name,
                "assetType": item.asset_type,
                "longitudeDeg": float(item.longitude_deg),
                "latitudeDeg": float(item.latitude_deg),
                "altitudeM": float(item.altitude_m),
            }
            for item in settings.ground_assets
        ],
        "relaySatellites": relay_satellites,
        "model": model_payload,
    }
    if scene_overlays:
        payload.update(scene_overlays)
    return payload, model_note


class _SceneBridge(QtCore.QObject):
    scene_requested = QtCore.Signal()
    status_reported = QtCore.Signal(str, str)
    scene_changed = QtCore.Signal(str)

    @QtCore.Slot()
    def requestScene(self) -> None:
        self.scene_requested.emit()

    @QtCore.Slot(str, str)
    def reportStatus(self, state: str, detail: str) -> None:
        self.status_reported.emit(state, detail)


if QtWebEngineCore is not None:

    class _MissionWebPage(QtWebEngineCore.QWebEnginePage):  # type: ignore[misc]
        console_emitted = QtCore.Signal(str)

        def javaScriptConsoleMessage(
            self,
            level: QtWebEngineCore.QWebEnginePage.JavaScriptConsoleMessageLevel,
            message: str,
            line_number: int,
            source_id: str,
        ) -> None:
            self.console_emitted.emit(
                f"JS {level.name} {source_id}:{line_number} {message}"
            )
            super().javaScriptConsoleMessage(level, message, line_number, source_id)

else:
    _MissionWebPage = None


class CesiumMissionView(QtWidgets.QWidget):
    status_changed = QtCore.Signal(object)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._last_status: dict[str, str] = {"state": "loading"}
        self._scene_payload = "{}"
        self._raw_scene_payload: dict[str, Any] = {}
        self._scene_note: dict[str, str] | None = None
        self._page_loaded = False
        self._camera_mode = _CAMERA_MODE_EARTH

        self._camera_toolbar = QtWidgets.QFrame()
        self._camera_toolbar.setProperty("role", "sceneToolbar")
        toolbar_layout = QtWidgets.QHBoxLayout(self._camera_toolbar)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setSpacing(6)
        toolbar_layout.addStretch(1)
        self._earth_focus_button = QtWidgets.QPushButton("地球居中")
        self._spacecraft_focus_button = QtWidgets.QPushButton("卫星居中")
        for button in (self._earth_focus_button, self._spacecraft_focus_button):
            button.setCheckable(True)
            button.setProperty("variant", "secondary")
            toolbar_layout.addWidget(button)
        self._earth_focus_button.clicked.connect(lambda: self._set_camera_mode(_CAMERA_MODE_EARTH))
        self._spacecraft_focus_button.clicked.connect(lambda: self._set_camera_mode(_CAMERA_MODE_SPACECRAFT))
        layout.addWidget(self._camera_toolbar)
        self._sync_camera_buttons()

        if QtWebEngineWidgets is None or QtWebEngineCore is None or QtWebChannel is None:
            message = QtWidgets.QLabel(
                "Cesium mission view is unavailable because Qt WebEngine could not be initialized."
            )
            message.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            message.setWordWrap(True)
            message.setProperty("role", "pageBody")
            layout.addWidget(message)
            self._view = None
            self._bridge = None
            self._emit_status("unavailable")
            return

        self._view = QtWebEngineWidgets.QWebEngineView()
        self._view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        if _MissionWebPage is not None:
            self._page = _MissionWebPage(self._view)
            self._page.console_emitted.connect(self._on_console_message)
            self._view.setPage(self._page)
        else:
            self._page = self._view.page()
        self._page.setBackgroundColor(QtGui.QColor("#050A12"))
        layout.addWidget(self._view)

        self._bridge = _SceneBridge()
        self._bridge.scene_requested.connect(self._publish_scene)
        self._bridge.status_reported.connect(self._on_bridge_status)

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

        channel = QtWebChannel.QWebChannel(self._page)
        channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(channel)

        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(QtCore.QUrl.fromLocalFile(str(_CESIUM_HTML_PATH.resolve())))
        self._emit_status("loading")

    def set_scene(
        self,
        elements: OrbitalElements,
        trajectory: OrbitTrajectory,
        settings: SatelliteStatusSettings,
        *,
        satellite_label: str = "Mission Spacecraft",
        scene_overlays: dict[str, Any] | None = None,
    ) -> None:
        payload, scene_note = build_scene_payload(
            elements,
            trajectory,
            settings,
            satellite_label=satellite_label,
            scene_overlays=scene_overlays,
        )
        self.set_scene_payload(payload, scene_note=scene_note)

    def set_scene_payload(
        self,
        payload: dict[str, Any],
        *,
        scene_note: dict[str, str] | None = None,
    ) -> None:
        self._raw_scene_payload = dict(payload)
        self._scene_payload = json.dumps(self._payload_with_camera_mode(self._raw_scene_payload), ensure_ascii=False)
        self._scene_note = scene_note
        if self._page_loaded:
            self._publish_scene()

    @property
    def last_status(self) -> dict[str, str]:
        return dict(self._last_status)

    def reload_page(self) -> None:
        if self._view is None:
            return
        self._page_loaded = False
        self._emit_status("loading")
        self._view.load(QtCore.QUrl.fromLocalFile(str(_CESIUM_HTML_PATH.resolve())))

    def _publish_scene(self) -> None:
        if self._bridge is None or not self._page_loaded:
            return
        self._emit_status("loading")
        self._bridge.scene_changed.emit(self._scene_payload)

    def _set_camera_mode(self, mode: str) -> None:
        self._camera_mode = mode if mode == _CAMERA_MODE_SPACECRAFT else _CAMERA_MODE_EARTH
        self._sync_camera_buttons()
        self._scene_payload = json.dumps(self._payload_with_camera_mode(self._raw_scene_payload), ensure_ascii=False)
        if self._page_loaded:
            self._publish_scene()

    def _sync_camera_buttons(self) -> None:
        self._earth_focus_button.blockSignals(True)
        self._spacecraft_focus_button.blockSignals(True)
        self._earth_focus_button.setChecked(self._camera_mode == _CAMERA_MODE_EARTH)
        self._spacecraft_focus_button.setChecked(self._camera_mode == _CAMERA_MODE_SPACECRAFT)
        self._earth_focus_button.blockSignals(False)
        self._spacecraft_focus_button.blockSignals(False)

    def _payload_with_camera_mode(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(payload)
        result["cameraMode"] = self._camera_mode
        return result

    def _on_load_finished(self, ok: bool) -> None:
        self._page_loaded = bool(ok)
        if ok:
            QtCore.QTimer.singleShot(0, self._publish_scene)
            return
        self._emit_status("load_failed")

    def _on_bridge_status(self, state: str, detail: str) -> None:
        if state == "ready":
            if self._scene_note is None:
                self._emit_status("ready")
                return
            self._emit_status(
                "ready",
                detail_key=self._scene_note.get("detail_key", ""),
                detail_name=self._scene_note.get("detail_name", ""),
            )
            return
        if state == "scene_error":
            self._emit_status("scene_error", error=detail)
            return
        if state == "library_error":
            self._emit_status("library_error")
            return
        self._emit_status(state or "scene_error", error=detail)

    def _emit_status(
        self,
        state: str,
        *,
        detail_key: str = "",
        detail_name: str = "",
        error: str = "",
    ) -> None:
        payload = {
            "state": state,
            "detail_key": detail_key,
            "detail_name": detail_name,
            "error": error,
        }
        self._last_status = payload
        self.status_changed.emit(payload)

    def _on_console_message(self, message: str) -> None:
        _LOGGER.warning("Cesium mission console: %s", message)
