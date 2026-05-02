from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtWidgets

from smart.domain.models import SatelliteStatusSettings
from smart.services.project_workspace import ProjectInfo
from smart.services.spice_service import runtime_summary
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else None


def _read_csv_rows(path: Path, *, limit: int | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(dict(row))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _row in reader)


def _fmt_float(value: object, decimals: int = 3, suffix: str = "") -> str:
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


class _ShortcutButton(QtWidgets.QPushButton):
    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setMinimumHeight(34)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)


class DashboardPage(QtWidgets.QWidget):
    new_project_requested = QtCore.Signal()
    open_project_requested = QtCore.Signal()
    recent_project_requested = QtCore.Signal(str)

    def __init__(
        self,
        mission_state: MissionState,
        i18n: I18nManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mission_state = mission_state
        self._i18n = i18n
        self._satellite_settings = SatelliteStatusSettings()
        self._spice_runtime_status, _ = runtime_summary()
        self._project: ProjectInfo | None = None
        self._recent_project_paths: list[str] = []

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._stack = QtWidgets.QStackedWidget()
        root.addWidget(self._stack)

        self._build_empty_page()
        self._build_project_page()

        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self._show_current_state()

    def set_project(self, project: ProjectInfo | None) -> None:
        self._project = project
        self._show_current_state()

    def set_recent_projects(self, paths: list[str]) -> None:
        self._recent_project_paths = list(paths)
        self._refresh_recent_projects()

    def set_satellite_settings(self, settings: SatelliteStatusSettings) -> None:
        self._satellite_settings = settings
        self._refresh_project_summary()

    def showEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._refresh_project_summary()

    def _build_empty_page(self) -> None:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.addStretch(1)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(20)
        center.setMaximumWidth(760)

        self._empty_title_label = QtWidgets.QLabel()
        self._empty_title_label.setProperty("role", "pageTitle")
        center_layout.addWidget(self._empty_title_label)

        self._empty_subtitle_label = QtWidgets.QLabel()
        self._empty_subtitle_label.setProperty("role", "pageBody")
        self._empty_subtitle_label.setWordWrap(True)
        center_layout.addWidget(self._empty_subtitle_label)

        shortcuts = QtWidgets.QFrame()
        shortcuts.setProperty("role", "card")
        shortcuts_layout = QtWidgets.QGridLayout(shortcuts)
        shortcuts_layout.setContentsMargins(22, 18, 22, 18)
        shortcuts_layout.setHorizontalSpacing(18)
        shortcuts_layout.setVerticalSpacing(12)

        self._new_project_button = _ShortcutButton()
        self._new_project_button.clicked.connect(self.new_project_requested)
        shortcuts_layout.addWidget(self._new_project_button, 0, 0)
        shortcuts_layout.addWidget(self._shortcut_label("Ctrl + Shift + N"), 0, 1)

        self._open_project_button = _ShortcutButton()
        self._open_project_button.clicked.connect(self.open_project_requested)
        shortcuts_layout.addWidget(self._open_project_button, 1, 0)
        shortcuts_layout.addWidget(self._shortcut_label("Ctrl + O"), 1, 1)
        shortcuts_layout.setColumnStretch(0, 1)
        center_layout.addWidget(shortcuts)

        self._recent_title_label = QtWidgets.QLabel()
        self._recent_title_label.setProperty("role", "cardTitle")
        center_layout.addWidget(self._recent_title_label)

        self._recent_list = QtWidgets.QListWidget()
        self._recent_list.setMinimumHeight(180)
        self._recent_list.itemActivated.connect(self._open_recent_item)
        self._recent_list.itemClicked.connect(self._open_recent_item)
        center_layout.addWidget(self._recent_list)

        layout.addWidget(center, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        self._stack.addWidget(page)

    def _build_project_page(self) -> None:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        grid = QtWidgets.QGridLayout(canvas)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)

        self._cards: dict[str, tuple[QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]] = {}
        card_keys = [
            "project",
            "satellite",
            "maneuver",
            "launch_window",
            "tracking_arc",
            "spice",
            "ai_analysis",
        ]
        for index, key in enumerate(card_keys):
            card, title, status, body = self._make_summary_card()
            self._cards[key] = (title, status, body)
            grid.addWidget(card, index // 2, index % 2)
        grid.setRowStretch((len(card_keys) + 1) // 2, 1)
        self._stack.addWidget(page)

    def _make_summary_card(self) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QtWidgets.QLabel()
        title.setProperty("role", "cardTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        status = QtWidgets.QLabel()
        status.setProperty("role", "statusDisconnected")
        layout.addWidget(status)

        body = QtWidgets.QLabel()
        body.setProperty("role", "pageBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)
        layout.addStretch(1)
        return card, title, status, body

    def _shortcut_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "cardCaption")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        return label

    def _open_recent_item(self, item: QtWidgets.QListWidgetItem) -> None:
        path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(path, str) and path:
            self.recent_project_requested.emit(path)

    def _show_current_state(self) -> None:
        self._stack.setCurrentIndex(0 if self._project is None else 1)
        self._refresh_project_summary()

    def _refresh_recent_projects(self) -> None:
        if not hasattr(self, "_recent_list"):
            return
        self._recent_list.clear()
        if not self._recent_project_paths:
            item = QtWidgets.QListWidgetItem(self._i18n.t("dashboard.empty.recent_empty"))
            item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self._recent_list.addItem(item)
            return
        for path_text in self._recent_project_paths:
            path = Path(path_text)
            item = QtWidgets.QListWidgetItem(f"{path.name}\n{path}")
            item.setToolTip(str(path))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
            self._recent_list.addItem(item)

    def _refresh_project_summary(self) -> None:
        if self._project is None or not hasattr(self, "_cards"):
            return

        root = self._project.root_dir
        config_dir = root / "config"
        data_dir = root / "data"
        satellite = _read_json(config_dir / "satellite_status.json") or {}
        maneuver = _read_json(config_dir / "maneuver_strategy.json") or {}
        launch_config = _read_json(config_dir / "launch_window.json") or {}
        tracking_config = _read_json(config_dir / "tracking_arc.json") or {}
        ai_report = data_dir / "ai_project_analysis.md"

        self._set_card(
            "project",
            "dashboard.card.project",
            "Ready",
            self._i18n.t(
                "dashboard.summary.project_body",
                name=self._project.name,
                path=str(root),
                updated=self._project.updated_utc,
            ),
        )
        self._set_card(
            "satellite",
            "dashboard.card.satellite",
            "Ready" if satellite else "Disconnected",
            self._i18n.t(
                "dashboard.summary.satellite_body",
                launch_mass=_fmt_float(satellite.get("launch_mass_kg"), 1, " kg"),
                fuel=_fmt_float(satellite.get("fuel_load_kg"), 1, " kg"),
                ground=len(satellite.get("ground_assets", [])) if isinstance(satellite.get("ground_assets"), list) else 0,
                relay=len(satellite.get("relay_satellites", [])) if isinstance(satellite.get("relay_satellites"), list) else 0,
            ),
        )

        orbit_rows = _count_csv_rows(data_dir / "full_orbit_history.csv")
        final_orbit_row = self._last_csv_row(data_dir / "full_orbit_history.csv")
        self._set_card(
            "maneuver",
            "dashboard.card.maneuver",
            "Ready" if orbit_rows else ("Planned" if maneuver else "Disconnected"),
            self._i18n.t(
                "dashboard.summary.maneuver_body",
                count=int(maneuver.get("maneuver_count", 0) or 0),
                samples=orbit_rows,
                final_mass=_fmt_float(final_orbit_row.get("mass_kg"), 3, " kg") if final_orbit_row else "--",
                final_lon=_fmt_float(final_orbit_row.get("subsatellite_longitude_deg"), 5, " deg")
                if final_orbit_row
                else "--",
                final_lat=_fmt_float(final_orbit_row.get("subsatellite_latitude_deg"), 5, " deg")
                if final_orbit_row
                else "--",
            ),
        )

        launch_rows = _count_csv_rows(data_dir / "launch_window_results.csv")
        sample_rows = _count_csv_rows(data_dir / "launch_window_samples.csv")
        self._set_card(
            "launch_window",
            "dashboard.card.launch_window",
            "Ready" if launch_rows else ("Loading" if sample_rows else "Planned"),
            self._i18n.t(
                "dashboard.summary.launch_window_body",
                windows=launch_rows,
                samples=sample_rows,
                step=_fmt_float(launch_config.get("sampling_step_min"), 3, " min"),
            ),
        )

        tracking_assets = self._count_enabled_assets(tracking_config)
        self._set_card(
            "tracking_arc",
            "dashboard.card.tracking_arc",
            "Ready" if tracking_config and launch_rows else ("Planned" if tracking_config else "Disconnected"),
            self._i18n.t(
                "dashboard.summary.tracking_arc_body",
                assets=tracking_assets,
                windows=launch_rows,
                config="OK" if tracking_config else "--",
            ),
        )

        self._set_card(
            "spice",
            "dashboard.card.spice",
            self._spice_runtime_status,
            self._i18n.t(
                "dashboard.summary.spice_body",
                status=self._i18n.t("status.ready")
                if self._spice_runtime_status == "Ready"
                else self._i18n.t("status.disconnected"),
                kernels=str(data_dir / "kernels"),
            ),
        )

        self._set_card(
            "ai_analysis",
            "dashboard.card.ai_analysis",
            "Ready" if ai_report.exists() else "Planned",
            self._i18n.t(
                "dashboard.summary.ai_body",
                report=str(ai_report) if ai_report.exists() else "--",
            ),
        )

    @staticmethod
    def _last_csv_row(path: Path) -> dict[str, str] | None:
        rows = _read_csv_rows(path)
        return rows[-1] if rows else None

    @staticmethod
    def _count_enabled_assets(config: dict[str, Any]) -> int:
        total = 0
        for key in (
            "ground_station_presets",
            "custom_ground_stations",
            "relay_satellite_presets",
            "custom_relay_satellites",
        ):
            entries = config.get(key)
            if not isinstance(entries, list):
                continue
            total += sum(1 for item in entries if isinstance(item, dict) and bool(item.get("enabled", True)))
        return total

    def _set_card(self, key: str, title_key: str, status: str, body: str) -> None:
        title, status_label, body_label = self._cards[key]
        title.setText(self._i18n.t(title_key))
        if status == "Ready":
            status_label.setText(self._i18n.t("status.ready"))
            role = "statusReady"
        elif status == "Loading":
            status_label.setText(self._i18n.t("status.loading"))
            role = "statusLoading"
        elif status == "Planned":
            status_label.setText(self._i18n.t("status.planned"))
            role = "statusPlanned"
        else:
            status_label.setText(self._i18n.t("status.disconnected"))
            role = "statusDisconnected"
        status_label.setProperty("role", role)
        status_label.style().unpolish(status_label)
        status_label.style().polish(status_label)
        body_label.setText(body)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._empty_title_label.setText(t("dashboard.empty.title"))
        self._empty_subtitle_label.setText(t("dashboard.empty.subtitle"))
        self._new_project_button.setText(t("dashboard.empty.new_project"))
        self._open_project_button.setText(t("dashboard.empty.open_project"))
        self._recent_title_label.setText(t("dashboard.empty.recent_title"))

        self._title_label.setText(t("dashboard.title"))
        self._subtitle_label.setText(t("dashboard.project_subtitle"))
        self._refresh_recent_projects()
        self._refresh_project_summary()
