from __future__ import annotations

from collections.abc import Mapping, Sequence
import ctypes
from datetime import datetime, timedelta, timezone
import re
import os
from pathlib import Path
import sys
import time
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.stk_ephemeris import derive_stk_time_bounds, write_stk_ephemeris
from smart.ui.i18n import I18nManager

_HIDDEN_3D_OVERLAY_BASENAMES = {
    "agi_logo.png",
    "agi_logo_small.ppm",
    "agi_logo_big.ppm",
    "agi_web_logo_small.ppm",
}
_MANEUVER_PHASES = {"settle", "orbit_control"}
_MAIN_TRACK_COLOR = "#00E5FF"
_MANEUVER_TRACK_COLOR = "#FF3030"
_POST_MANEUVERS_COLOR = "#FFFFFF"
_MANEUVER_LINE_WIDTH = 3
_POST_MANEUVERS_LINE_WIDTH = 3


class _StkWinFormsRuntime:
    _initialized = False
    _error = ""
    _availability_checked = False
    _engine_available = False
    _globe_available = False
    _Application = None
    _DockStyle = None
    _Panel = None
    _AxAgUiAx2DCntrl = None
    _AxAgUiAxVOCntrl = None
    _AgSTKXApplication = None
    _AgEFeatureCodes = None
    _AgStkObjectRoot = None

    @classmethod
    def ensure_initialized(cls) -> None:
        if cls._initialized:
            return
        cls._initialized = True

        if sys.platform != "win32":
            cls._error = "STK/X embedded graphics are only available on Windows."
            return

        if not cls._ole_initialize():
            return

        try:
            import clr  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - local dependency
            cls._error = f"pythonnet is unavailable: {exc}"
            return

        pia_dir = Path(r"D:\Program Files\AGI\STK 116\bin\Primary Interop Assemblies")
        if not pia_dir.exists():
            cls._error = f"STK 11.6 interop assemblies not found: {pia_dir}"
            return

        try:
            for dll_name in (
                "stdole.dll",
                "AGI.STKX.Interop.dll",
                "AxAGI.STKX.Interop.dll",
                "AGI.STKX.Controls.Interop.dll",
                "AGI.STKObjects.Interop.dll",
                "AGI.STKUtil.Interop.dll",
            ):
                clr.AddReference(str(pia_dir / dll_name))
            clr.AddReference("System.Windows.Forms")

            from System.Windows.Forms import Application, DockStyle, Panel
            from AGI.STKObjects import AgStkObjectRoot
            from AGI.STKX import AgEFeatureCodes, AgSTKXApplication
            from AGI.STKX.Controls import AxAgUiAx2DCntrl, AxAgUiAxVOCntrl

            cls._Application = Application
            cls._DockStyle = DockStyle
            cls._Panel = Panel
            cls._AxAgUiAx2DCntrl = AxAgUiAx2DCntrl
            cls._AxAgUiAxVOCntrl = AxAgUiAxVOCntrl
            cls._AgSTKXApplication = AgSTKXApplication
            cls._AgEFeatureCodes = AgEFeatureCodes
            cls._AgStkObjectRoot = AgStkObjectRoot

            Application.EnableVisualStyles()
        except Exception as exc:  # pragma: no cover - depends on local STK/.NET runtime
            cls._error = f"Failed to initialize STK/.NET runtime: {exc}"

    @classmethod
    def availability_error(cls) -> str:
        cls.ensure_initialized()
        if cls._error:
            return cls._error
        cls._check_features()
        if not cls._engine_available:
            return "STK Engine runtime is unavailable."
        return ""

    @classmethod
    def create_root(cls):
        cls.ensure_initialized()
        if cls._error or cls._AgStkObjectRoot is None:
            raise RuntimeError(cls._error or "STK Object Model is unavailable.")
        return cls._AgStkObjectRoot()

    @classmethod
    def create_panel_with_control(cls, *, mode: str):
        cls.ensure_initialized()
        if cls._error:
            raise RuntimeError(cls._error)
        if cls._Panel is None or cls._DockStyle is None:
            raise RuntimeError("WinForms Panel support is unavailable.")

        panel = cls._Panel()
        panel.Width = 640
        panel.Height = 360

        if mode == "2d":
            if cls._AxAgUiAx2DCntrl is None:
                raise RuntimeError("STKX 2D control is unavailable.")
            control = cls._AxAgUiAx2DCntrl()
            try:
                control.PanModeEnabled = True
            except Exception:
                pass
        else:
            if cls._AxAgUiAxVOCntrl is None:
                raise RuntimeError("STKX 3D control is unavailable.")
            control = cls._AxAgUiAxVOCntrl()

        cls._disable_control_logo(control)
        control.Dock = cls._DockStyle.Fill
        panel.Controls.Add(control)
        panel.CreateControl()
        control.CreateControl()
        cls._disable_control_logo(control)
        return panel, control

    @staticmethod
    def _disable_control_logo(control: object) -> None:
        try:
            setattr(control, "NoLogo", True)
        except Exception:
            pass

    @classmethod
    def pump_messages(cls, cycles: int = 1, delay_s: float = 0.0) -> None:
        cls.ensure_initialized()
        if cls._Application is None:
            return
        for _ in range(max(cycles, 1)):
            cls._Application.DoEvents()
            if delay_s > 0.0:
                time.sleep(delay_s)

    @classmethod
    def _ole_initialize(cls) -> bool:
        try:
            result = ctypes.windll.ole32.OleInitialize(None)
        except Exception as exc:  # pragma: no cover - Windows-only path
            cls._error = f"OLE initialization failed: {exc}"
            return False
        if result in (0, 1, -2147417850):
            return True
        cls._error = f"OLE initialization failed with HRESULT 0x{result & 0xFFFFFFFF:08X}."
        return False

    @classmethod
    def _check_features(cls) -> None:
        if cls._availability_checked or cls._error:
            return
        cls._availability_checked = True
        if cls._AgSTKXApplication is None or cls._AgEFeatureCodes is None:
            cls._error = "STKX application class is unavailable."
            return
        try:
            app = cls._AgSTKXApplication()
            feature_codes = cls._AgEFeatureCodes
            cls._engine_available = bool(app.IsFeatureAvailable(feature_codes.eFeatureCodeEngineRuntime))
            cls._globe_available = bool(app.IsFeatureAvailable(feature_codes.eFeatureCodeGlobeControl))
        except Exception as exc:  # pragma: no cover - depends on local STK install
            cls._error = f"Failed to query STK licensing/runtime features: {exc}"


class _WinFormsHostWidget(QtWidgets.QWidget):
    def __init__(self, *, mode: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel = None
        self._control = None
        self._foreign_window: QtGui.QWindow | None = None
        self._container: QtWidgets.QWidget | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        panel, control = _StkWinFormsRuntime.create_panel_with_control(mode=mode)
        self._panel = panel
        self._control = control

        foreign_window = QtGui.QWindow.fromWinId(panel.Handle.ToInt64())
        container = QtWidgets.QWidget.createWindowContainer(foreign_window, self)
        container.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(container, 1)

        self._foreign_window = foreign_window
        self._container = container
        QtCore.QTimer.singleShot(0, self._sync_native_size)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_native_size()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            if self._control is not None:
                self._control.Dispose()
        except Exception:
            pass
        try:
            if self._panel is not None:
                self._panel.Dispose()
        except Exception:
            pass
        super().closeEvent(event)

    def _sync_native_size(self) -> None:
        if self._panel is None:
            return
        width = max(int(self.width()), 1)
        height = max(int(self.height()), 1)
        try:
            self._panel.Width = width
            self._panel.Height = height
        except Exception:
            pass
        _StkWinFormsRuntime.pump_messages()


class StkManeuverGraphicsWidget(QtWidgets.QWidget):
    def __init__(
        self,
        i18n: I18nManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._root = None
        self._ground_host: _WinFormsHostWidget | None = None
        self._orbit_host: _WinFormsHostWidget | None = None
        self._availability_error = ""
        self._status_mode = "idle"
        self._status_error = ""
        self._scenario_name = f"SMART_ManeuverPreview_{id(self) & 0xFFFF:X}"
        self._satellite_name = "PreviewSat"
        self._annotation_ids: list[int] = []

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self._splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self._splitter.setChildrenCollapsible(False)
        root.addWidget(self._splitter, 1)

        (
            self._ground_title_label,
            self._ground_placeholder_label,
            self._ground_content_layout,
        ) = self._build_card()
        (
            self._orbit_title_label,
            self._orbit_placeholder_label,
            self._orbit_content_layout,
        ) = self._build_card()
        self._splitter.setSizes([430, 430])

        self._status_label = QtWidgets.QLabel()
        self._status_label.setProperty("role", "cardCaption")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._initialize_controls()
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()

    def _build_card(self) -> tuple[QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QtWidgets.QLabel()
        title.setProperty("role", "cardTitle")
        layout.addWidget(title)

        content = QtWidgets.QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        layout.addLayout(content, 1)

        placeholder = QtWidgets.QLabel()
        placeholder.setProperty("role", "pageBody")
        placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        content.addWidget(placeholder, 1)

        self._splitter.addWidget(card)
        return title, placeholder, content

    def _initialize_controls(self) -> None:
        self._availability_error = _StkWinFormsRuntime.availability_error()
        if self._availability_error:
            return

        try:
            self._ground_host = _WinFormsHostWidget(mode="2d", parent=self)
            self._orbit_host = _WinFormsHostWidget(mode="3d", parent=self)
            self._ground_placeholder_label.hide()
            self._orbit_placeholder_label.hide()
            self._ground_content_layout.addWidget(self._ground_host, 1)
            self._orbit_content_layout.addWidget(self._orbit_host, 1)
            self._root = _StkWinFormsRuntime.create_root()
        except Exception as exc:  # pragma: no cover - depends on local STK/.NET runtime
            self._availability_error = str(exc)
            self._ground_host = None
            self._orbit_host = None
            self._root = None

    def retranslate(self) -> None:
        t = self._i18n.t
        self._ground_title_label.setText(t("maneuver.ground_track_title"))
        self._orbit_title_label.setText(t("maneuver.orbit_3d_title"))
        if self._availability_error:
            text = t("maneuver.stk.unavailable", error=self._availability_error)
            self._ground_placeholder_label.setText(text)
            self._orbit_placeholder_label.setText(text)
            self._status_label.setText(text)
            return

        self._refresh_status_message()

    def clear_graphics(self) -> None:
        if self._root is None:
            return
        self._unload_current_scenario()
        self._status_mode = "idle"
        self._status_error = ""
        self._refresh_status_message()

    def save_current_scenario(self, target_path: str | Path) -> Path:
        if self._root is None:
            raise RuntimeError("Embedded STK/X runtime is unavailable.")
        scenario = getattr(self._root, "CurrentScenario", None)
        if scenario is None:
            raise RuntimeError("No embedded STK scenario is currently loaded.")

        target = Path(target_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        save_base = target.with_suffix("")
        escaped_path = str(save_base).replace('"', '\\"')
        self._execute(f'SaveAs / * "{escaped_path}"')
        _StkWinFormsRuntime.pump_messages(cycles=2, delay_s=0.02)
        return save_base.with_suffix(".sc")

    def load_trajectory(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        ephemeris_path: str | Path,
        scenario_epoch_utc: str | None = None,
        satellite_name: str | None = None,
        maneuver_summaries: Sequence[Mapping[str, Any]] | None = None,
    ) -> Path | None:
        if self._root is None:
            self._status_mode = "unavailable"
            self._refresh_status_message()
            return None
        if not rows:
            self.clear_graphics()
            return None

        try:
            metadata = write_stk_ephemeris(
                rows,
                ephemeris_path,
                scenario_epoch_utc=scenario_epoch_utc,
            )
            base_satellite_name = self._sanitize_object_name(satellite_name or self._satellite_name)
            main_satellite_path = f"*/Satellite/{base_satellite_name}"
            self._ensure_scenario_loaded()
            self._unload_preview_satellite(base_satellite_name)
            start_time, stop_time = derive_stk_time_bounds(
                rows,
                scenario_epoch_utc=metadata.scenario_epoch_utc,
            )
            self._execute(f'SetAnalysisTimePeriod * "{start_time}" "{stop_time}"')
            self._execute("SetAnimation * StartAndCurrentTime UseAnalysisStartTime")
            self._execute("SetAnimation * EndTime UseAnalysisStopTime")
            self._execute("Graphics * BackgroundImage UseBingMaps Off", ignore_failure=True)
            self._execute("MapAttribs * ScenTime Display Off", ignore_failure=True)
            self._execute(f"New / */Satellite {base_satellite_name}")
            self._execute(
                f'SetState {main_satellite_path} FromFile "{metadata.output_path}" FileFormat StkPL'
            )
            self._apply_main_satellite_graphics(main_satellite_path, label_text=satellite_name)
            self._apply_main_satellite_custom_intervals(
                main_satellite_path,
                rows,
                scenario_epoch_utc=metadata.scenario_epoch_utc,
            )
            self._add_maneuver_annotations(maneuver_summaries or [])
            self._hide_3d_logo_overlays()
            self._execute("VO * View Home", ignore_failure=True)
            self._execute("Animate * Reset")
            _StkWinFormsRuntime.pump_messages(cycles=8, delay_s=0.03)
        except Exception as exc:
            self._status_mode = "load_failed"
            self._status_error = str(exc)
            self._refresh_status_message()
            return None

        self._status_mode = "ready"
        self._status_error = ""
        self._satellite_name = base_satellite_name
        self._refresh_status_message()
        return metadata.output_path

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.clear_graphics()
        super().closeEvent(event)

    def _refresh_status_message(self) -> None:
        t = self._i18n.t
        if self._availability_error:
            message = t("maneuver.stk.unavailable", error=self._availability_error)
        elif self._status_mode == "load_failed":
            message = t("maneuver.stk.load_failed", error=self._status_error)
        elif self._status_mode == "unavailable":
            message = t("maneuver.stk.unavailable", error=self._status_error or "Unknown runtime error.")
        elif self._status_mode == "ready":
            message = t("maneuver.stk.ready")
        else:
            message = t("maneuver.stk.idle")
        self._status_label.setText(message)

    @staticmethod
    def _sanitize_object_name(raw_name: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z_]", "_", str(raw_name).strip())
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        if not cleaned:
            return "PreviewSat"
        if cleaned[0].isdigit():
            cleaned = f"Sat_{cleaned}"
        return cleaned

    def _apply_main_satellite_graphics(self, satellite_path: str, *, label_text: str | None) -> None:
        self._execute(
            f"Graphics {satellite_path} SetAttrType CustomIntervals",
            ignore_failure=True,
        )
        self._execute(
            f"Graphics {satellite_path} CustomIntervals Clear",
            ignore_failure=True,
        )
        self._execute(
            f"Graphics {satellite_path} CustomIntervals Edit Default "
            f"Show On GroundTrack On Orbit On Label On Color {_MAIN_TRACK_COLOR}",
            ignore_failure=True,
        )
        self._execute(
            f"Graphics {satellite_path} SetColor {_MAIN_TRACK_COLOR} GroundTrack",
            ignore_failure=True,
        )
        self._execute(
            f"Graphics {satellite_path} SetColor {_MAIN_TRACK_COLOR} Marker",
            ignore_failure=True,
        )
        self._execute(
            f"Graphics {satellite_path} Label Show On",
            ignore_failure=True,
        )
        if label_text:
            escaped = str(label_text).replace('"', '\\"')
            self._execute(
                f'Graphics {satellite_path} Label LabelText "{escaped}"',
                ignore_failure=True,
            )
        self._execute(
            f"Graphics {satellite_path} Pass2D "
            "GrndLead All GrndTrail SameAsLead Show All ShowPassLabels Off ShowPathLabels Off",
        )
        self._execute(
            f"VO {satellite_path} Pass3D "
            "Inherit Off GroundLead None GroundTrail SameAsLead OrbitLead All OrbitTrail SameAsLead",
        )
        self._execute(
            f"VO {satellite_path} TickMarks TimeBetween 180 Ground Show Off Orbit Show Off",
            ignore_failure=True,
        )

    def _apply_main_satellite_custom_intervals(
        self,
        satellite_path: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        scenario_epoch_utc: str,
    ) -> None:
        maneuver_intervals = self._extract_maneuver_intervals(rows, scenario_epoch_utc=scenario_epoch_utc)
        if maneuver_intervals:
            quoted_pairs = " ".join(f'"{start}" "{stop}"' for start, stop in maneuver_intervals)
            self._execute(
                f"Graphics {satellite_path} CustomIntervals Add {len(maneuver_intervals)} {quoted_pairs}",
                ignore_failure=True,
            )
            self._execute(
                f"Graphics {satellite_path} CustomIntervals Deconflict",
                ignore_failure=True,
            )
            for start_time, stop_time in maneuver_intervals:
                self._execute(
                    f'Graphics {satellite_path} CustomIntervals Edit "{start_time}" "{stop_time}" '
                    f"Show On GroundTrack On Orbit On Label On "
                    f"Color {_MANEUVER_TRACK_COLOR} LineWidth {_MANEUVER_LINE_WIDTH}",
                    ignore_failure=True,
                )
        post_maneuver_interval = self._extract_post_maneuver_interval(
            rows,
            scenario_epoch_utc=scenario_epoch_utc,
        )
        if post_maneuver_interval is not None:
            start_time, stop_time = post_maneuver_interval
            self._execute(
                f'Graphics {satellite_path} CustomIntervals Add 1 "{start_time}" "{stop_time}"',
                ignore_failure=True,
            )
            self._execute(
                f'Graphics {satellite_path} CustomIntervals Edit "{start_time}" "{stop_time}" '
                f"Show On GroundTrack On Orbit On Label On "
                f"Color {_POST_MANEUVERS_COLOR} LineWidth {_POST_MANEUVERS_LINE_WIDTH}",
                ignore_failure=True,
            )

    def _add_maneuver_annotations(self, maneuver_summaries: Sequence[Mapping[str, Any]]) -> None:
        self._annotation_ids.clear()
        self._execute("MapAnnotation * Delete All", ignore_failure=True)
        for summary in maneuver_summaries:
            maneuver_index = int(summary["maneuver_index"])
            lon = float(summary["subsatellite_longitude_deg"])
            lat = float(summary["subsatellite_latitude_deg"])
            text_id = 1000 + maneuver_index * 10
            marker_id = text_id + 1
            self._annotation_ids.extend([text_id, marker_id])
            self._execute(
                " ".join(
                    [
                        f'MapAnnotation * Add {text_id} Text',
                        f'String "{maneuver_index}"',
                        "Color #FF9A2F",
                        "Coord LatLon",
                        f"Position {lon:.6f} {lat:.6f}",
                        "FontStyle 2",
                    ]
                ),
                ignore_failure=True,
            )
            self._execute(
                " ".join(
                    [
                        f"MapAnnotation * Add {marker_id} Marker",
                        "Style 3",
                        "Color #FF3030",
                        "Coord LatLon",
                        f"Position {lon:.6f} {lat:.6f}",
                        "Scale 1.2",
                    ]
                ),
                ignore_failure=True,
            )

    @staticmethod
    def _extract_maneuver_segments(
        rows: Sequence[Mapping[str, Any]],
    ) -> list[list[Mapping[str, Any]]]:
        segments: list[list[Mapping[str, Any]]] = []
        current: list[Mapping[str, Any]] = []
        for index, row in enumerate(rows):
            phase = str(row.get("phase", ""))
            in_maneuver = phase in _MANEUVER_PHASES
            if in_maneuver and not current:
                if index > 0 and int(rows[index - 1].get("is_event_point", 0)):
                    current.append(rows[index - 1])
                current.append(row)
            elif in_maneuver:
                current.append(row)
            elif current:
                if len(current) >= 2:
                    segments.append(current.copy())
                current = []
        if len(current) >= 2:
            segments.append(current.copy())
        return segments

    def _extract_maneuver_intervals(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        scenario_epoch_utc: str,
    ) -> list[tuple[str, str]]:
        scenario_epoch = self._parse_utc(scenario_epoch_utc)
        intervals: list[tuple[str, str]] = []
        for segment_rows in self._extract_maneuver_segments(rows):
            start_elapsed_s = float(segment_rows[0]["elapsed_time_s"])
            stop_elapsed_s = float(segment_rows[-1]["elapsed_time_s"])
            if stop_elapsed_s <= start_elapsed_s:
                continue
            start_time = self._format_stk_epoch(scenario_epoch + timedelta(seconds=start_elapsed_s))
            stop_time = self._format_stk_epoch(scenario_epoch + timedelta(seconds=stop_elapsed_s))
            intervals.append((start_time, stop_time))
        return intervals

    def _extract_post_maneuver_interval(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        scenario_epoch_utc: str,
    ) -> tuple[str, str] | None:
        segments = self._extract_maneuver_segments(rows)
        if not segments or not rows:
            return None
        last_maneuver_end_s = float(segments[-1][-1]["elapsed_time_s"])
        final_elapsed_s = float(rows[-1]["elapsed_time_s"])
        if final_elapsed_s <= last_maneuver_end_s:
            return None
        scenario_epoch = self._parse_utc(scenario_epoch_utc)
        start_time = self._format_stk_epoch(scenario_epoch + timedelta(seconds=last_maneuver_end_s))
        stop_time = self._format_stk_epoch(scenario_epoch + timedelta(seconds=final_elapsed_s))
        return start_time, stop_time

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_stk_epoch(value: datetime) -> str:
        text = value.astimezone(timezone.utc).strftime("%d %b %Y %H:%M:%S.%f")
        return text[1:] if text.startswith("0") else text

    def _unload_current_scenario(self) -> None:
        if self._root is None:
            return
        try:
            self._root.CloseScenario()
            _StkWinFormsRuntime.pump_messages(cycles=2)
        except Exception:
            pass

    def _ensure_scenario_loaded(self) -> None:
        if self._root is None:
            raise RuntimeError("Embedded STK/X runtime is unavailable.")
        if getattr(self._root, "CurrentScenario", None) is None:
            self._root.NewScenario(self._scenario_name)
            _StkWinFormsRuntime.pump_messages(cycles=2)

    def _unload_preview_satellite(self, satellite_name: str) -> None:
        names = {self._satellite_name, satellite_name}
        for name in names:
            cleaned = self._sanitize_object_name(name)
            if not cleaned:
                continue
            self._execute(f"Unload / */Satellite/{cleaned}", ignore_failure=True)
        self._execute("MapAnnotation * Delete All", ignore_failure=True)
        _StkWinFormsRuntime.pump_messages(cycles=2)

    def _hide_3d_logo_overlays(self) -> None:
        overlay_paths = self._query_3d_overlays()
        candidates = {name for name in _HIDDEN_3D_OVERLAY_BASENAMES}
        for overlay_path in overlay_paths:
            basename = os.path.basename(overlay_path).lower()
            if "agi_logo" in basename or basename in candidates:
                self._hide_3d_overlay(overlay_path)
                self._hide_3d_overlay(basename)

    def _hide_3d_overlay(self, overlay_name: str) -> None:
        escaped_name = overlay_name.replace('"', '\\"')
        self._execute(
            f'VO * Overlay Modify "{escaped_name}" Show Off WindowID All',
            ignore_failure=True,
        )
        self._execute(
            f'VO * Overlay Remove "{escaped_name}" WindowID All',
            ignore_failure=True,
        )

    def _query_3d_overlays(self) -> list[str]:
        raw_values = self._execute("VO_R * Overlays", ignore_failure=True)
        overlay_paths: list[str] = []
        for value in raw_values:
            for part in value.split(";"):
                item = part.strip()
                if not item or item.lower() == "none":
                    continue
                overlay_paths.append(item)
        return overlay_paths

    def _execute(self, command: str, *, ignore_failure: bool = False) -> list[str]:
        if self._root is None:
            if ignore_failure:
                return []
            raise RuntimeError("Embedded STK/X runtime is unavailable.")
        try:
            result = self._root.ExecuteCommand(command)
        except Exception as exc:
            if ignore_failure:
                return []
            raise RuntimeError(f"STK command failed: {command}") from exc

        if result is None:
            if ignore_failure:
                return []
            raise RuntimeError(f"STK command failed: {command}")

        try:
            count = int(result.Count)
        except Exception:
            count = 0
        values: list[str] = []
        for index in range(count):
            try:
                item = result[index]
            except Exception:
                try:
                    item = result.get_Item(index)
                except Exception:
                    item = ""
            values.append(str(item).strip())
        return values
