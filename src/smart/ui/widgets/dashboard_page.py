from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

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


def _status_rank(status: str) -> int:
    return {"Ready": 3, "Loading": 2, "Planned": 1, "Disconnected": 0}.get(status, 0)


class _DashboardSurface(QtWidgets.QFrame):
    """Subtle mission-console background without relying on image assets."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QtGui.QColor("#071016"))
        gradient.setColorAt(0.48, QtGui.QColor("#0a1720"))
        gradient.setColorAt(1.0, QtGui.QColor("#0d2027"))
        painter.fillRect(rect, gradient)

        grid_pen = QtGui.QPen(QtGui.QColor(58, 105, 118, 30), 1)
        painter.setPen(grid_pen)
        for x in range(0, rect.width(), 56):
            painter.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), 56):
            painter.drawLine(0, y, rect.width(), y)

        orbit_pen = QtGui.QPen(QtGui.QColor(85, 216, 234, 42), 1.2)
        painter.setPen(orbit_pen)
        painter.drawEllipse(QtCore.QRectF(rect.width() * 0.48, -120, rect.width() * 0.7, rect.height() * 0.78))
        painter.drawEllipse(QtCore.QRectF(-180, rect.height() * 0.54, rect.width() * 0.62, rect.height() * 0.45))
        painter.end()
        super().paintEvent(event)


class _ShortcutButton(QtWidgets.QPushButton):
    def __init__(self, text: str = "", *, secondary: bool = False, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setMinimumHeight(38)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        if secondary:
            self.setProperty("variant", "secondary")


class _SparklineWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[float] = []
        self.setMinimumHeight(52)

    def set_points(self, values: list[float]) -> None:
        self._points = values[-80:]
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 8, -4, -8)
        painter.setPen(QtGui.QPen(QtGui.QColor(47, 101, 117, 90), 1))
        painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        if len(self._points) < 2:
            painter.setPen(QtGui.QPen(QtGui.QColor("#2f6575"), 1.4))
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.top() + rect.height() * 0.42)
            painter.end()
            return

        minimum = min(self._points)
        maximum = max(self._points)
        span = maximum - minimum if not math.isclose(maximum, minimum) else 1.0
        path = QtGui.QPainterPath()
        for index, value in enumerate(self._points):
            x = rect.left() + rect.width() * index / (len(self._points) - 1)
            y = rect.bottom() - ((value - minimum) / span) * rect.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setPen(QtGui.QPen(QtGui.QColor("#66d9ea"), 2.0))
        painter.drawPath(path)
        painter.setPen(QtGui.QPen(QtGui.QColor("#f2b84b"), 3.2))
        painter.drawPoint(path.currentPosition())
        painter.end()


class _MetricTile(QtWidgets.QFrame):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "metricTile")
        self.setMinimumHeight(116)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        self._title = QtWidgets.QLabel()
        self._title.setProperty("role", "cardCaption")
        layout.addWidget(self._title)

        self._value = QtWidgets.QLabel()
        self._value.setProperty("role", "metricValue")
        layout.addWidget(self._value)

        self._caption = QtWidgets.QLabel()
        self._caption.setProperty("role", "metricCaption")
        self._caption.setWordWrap(True)
        layout.addWidget(self._caption)
        layout.addStretch(1)

    def set_metric(self, title: str, value: str, caption: str) -> None:
        self._title.setText(title)
        self._value.setText(value)
        self._caption.setText(caption)


class _TimelineWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, str]] = []
        self.setMinimumHeight(132)

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self._items = items
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        if not self._items:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(18, 20, -18, -24)
        y = rect.top() + 28
        step = rect.width() / max(1, len(self._items) - 1)
        painter.setPen(QtGui.QPen(QtGui.QColor("#234b59"), 2))
        painter.drawLine(rect.left(), y, rect.right(), y)

        for index, (label, status) in enumerate(self._items):
            x = rect.left() + index * step
            color = {
                "Ready": "#55d18f",
                "Loading": "#66d9ea",
                "Planned": "#f2b84b",
                "Disconnected": "#ff7a66",
            }.get(status, "#78939e")
            painter.setBrush(QtGui.QColor(color))
            painter.setPen(QtGui.QPen(QtGui.QColor("#0b1a22"), 2))
            painter.drawEllipse(QtCore.QPointF(x, y), 7, 7)
            painter.setPen(QtGui.QPen(QtGui.QColor("#b8c9d2"), 1))
            label_rect = QtCore.QRectF(x - 54, y + 16, 108, 44)
            painter.drawText(label_rect, QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.TextFlag.TextWordWrap, label)
        painter.end()


class _SummaryCard(QtWidgets.QFrame):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setMinimumHeight(230)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(9)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.title = QtWidgets.QLabel()
        self.title.setProperty("role", "cardTitle")
        self.title.setWordWrap(True)
        header.addWidget(self.title, 1)
        self.status = QtWidgets.QLabel()
        self.status.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.status)
        layout.addLayout(header)

        self.body = QtWidgets.QLabel()
        self.body.setProperty("role", "pageBody")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.body, 1)

        self.sparkline = _SparklineWidget()
        layout.addWidget(self.sparkline)

    def set_status_text(self, text: str, role: str) -> None:
        self.status.setText(text)
        self.status.setProperty("role", role)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


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
        self._refresh_recent_strip()

    def showEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._refresh_project_summary()

    def _build_empty_page(self) -> None:
        page = _DashboardSurface()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(56, 46, 56, 46)
        layout.addStretch(1)

        center = QtWidgets.QFrame()
        center.setProperty("role", "dashboardHero")
        center.setMaximumWidth(920)
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(34, 30, 34, 30)
        center_layout.setSpacing(18)

        self._empty_eyebrow_label = QtWidgets.QLabel()
        self._empty_eyebrow_label.setProperty("role", "eyebrow")
        center_layout.addWidget(self._empty_eyebrow_label)

        self._empty_title_label = QtWidgets.QLabel()
        self._empty_title_label.setProperty("role", "pageTitle")
        self._empty_title_label.setWordWrap(True)
        center_layout.addWidget(self._empty_title_label)

        self._empty_subtitle_label = QtWidgets.QLabel()
        self._empty_subtitle_label.setProperty("role", "pageBody")
        self._empty_subtitle_label.setWordWrap(True)
        center_layout.addWidget(self._empty_subtitle_label)

        shortcuts = QtWidgets.QFrame()
        shortcuts.setProperty("role", "glassPanel")
        shortcuts_layout = QtWidgets.QGridLayout(shortcuts)
        shortcuts_layout.setContentsMargins(20, 18, 20, 18)
        shortcuts_layout.setHorizontalSpacing(16)
        shortcuts_layout.setVerticalSpacing(12)

        self._new_project_button = _ShortcutButton()
        self._new_project_button.clicked.connect(self.new_project_requested)
        shortcuts_layout.addWidget(self._new_project_button, 0, 0)
        shortcuts_layout.addWidget(self._shortcut_label("Ctrl + Shift + N"), 0, 1)

        self._open_project_button = _ShortcutButton(secondary=True)
        self._open_project_button.clicked.connect(self.open_project_requested)
        shortcuts_layout.addWidget(self._open_project_button, 1, 0)
        shortcuts_layout.addWidget(self._shortcut_label("Ctrl + O"), 1, 1)
        shortcuts_layout.setColumnStretch(0, 1)
        center_layout.addWidget(shortcuts)

        self._recent_title_label = QtWidgets.QLabel()
        self._recent_title_label.setProperty("role", "sectionTitle")
        center_layout.addWidget(self._recent_title_label)

        self._recent_list = QtWidgets.QListWidget()
        self._recent_list.setProperty("role", "recentList")
        self._recent_list.setMinimumHeight(190)
        self._recent_list.itemActivated.connect(self._open_recent_item)
        self._recent_list.itemClicked.connect(self._open_recent_item)
        center_layout.addWidget(self._recent_list)

        layout.addWidget(center, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        self._stack.addWidget(page)

    def _build_project_page(self) -> None:
        page = _DashboardSurface()
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(18)

        self._hero = QtWidgets.QFrame()
        self._hero.setProperty("role", "dashboardHero")
        hero_layout = QtWidgets.QHBoxLayout(self._hero)
        hero_layout.setContentsMargins(26, 22, 26, 22)
        hero_layout.setSpacing(24)

        hero_text = QtWidgets.QVBoxLayout()
        hero_text.setSpacing(8)
        self._project_eyebrow_label = QtWidgets.QLabel()
        self._project_eyebrow_label.setProperty("role", "eyebrow")
        hero_text.addWidget(self._project_eyebrow_label)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        self._title_label.setWordWrap(True)
        hero_text.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        hero_text.addWidget(self._subtitle_label)

        self._project_path_label = QtWidgets.QLabel()
        self._project_path_label.setProperty("role", "cardCaption")
        self._project_path_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._project_path_label.setWordWrap(True)
        hero_text.addWidget(self._project_path_label)
        hero_layout.addLayout(hero_text, 1)

        action_panel = QtWidgets.QVBoxLayout()
        action_panel.setSpacing(10)
        self._project_updated_label = QtWidgets.QLabel()
        self._project_updated_label.setProperty("role", "cardCaption")
        self._project_updated_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        action_panel.addWidget(self._project_updated_label)

        button_row = QtWidgets.QHBoxLayout()
        self._project_new_button = _ShortcutButton()
        self._project_new_button.clicked.connect(self.new_project_requested)
        button_row.addWidget(self._project_new_button)
        self._project_open_button = _ShortcutButton(secondary=True)
        self._project_open_button.clicked.connect(self.open_project_requested)
        button_row.addWidget(self._project_open_button)
        self._refresh_button = _ShortcutButton(secondary=True)
        self._refresh_button.clicked.connect(self._refresh_project_summary)
        button_row.addWidget(self._refresh_button)
        action_panel.addLayout(button_row)
        action_panel.addStretch(1)
        hero_layout.addLayout(action_panel)
        root.addWidget(self._hero)

        metrics = QtWidgets.QHBoxLayout()
        metrics.setSpacing(14)
        self._metrics: dict[str, _MetricTile] = {}
        for key in ("orbit_samples", "maneuver_count", "launch_windows", "tracking_assets"):
            tile = _MetricTile()
            self._metrics[key] = tile
            metrics.addWidget(tile)
        root.addLayout(metrics)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        body.addWidget(scroll, 3)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        grid = QtWidgets.QGridLayout(canvas)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        self._cards: dict[str, _SummaryCard] = {}
        card_keys = ["satellite", "maneuver", "launch_window", "tracking_arc", "spice", "ai_analysis"]
        for index, key in enumerate(card_keys):
            card = _SummaryCard()
            self._cards[key] = card
            grid.addWidget(card, index // 2, index % 2)
        grid.setRowStretch((len(card_keys) + 1) // 2, 1)

        side = QtWidgets.QVBoxLayout()
        side.setSpacing(16)
        body.addLayout(side, 1)

        timeline_panel = QtWidgets.QFrame()
        timeline_panel.setProperty("role", "glassPanel")
        timeline_layout = QtWidgets.QVBoxLayout(timeline_panel)
        timeline_layout.setContentsMargins(18, 18, 18, 18)
        timeline_layout.setSpacing(8)
        self._timeline_title_label = QtWidgets.QLabel()
        self._timeline_title_label.setProperty("role", "sectionTitle")
        timeline_layout.addWidget(self._timeline_title_label)
        self._timeline = _TimelineWidget()
        timeline_layout.addWidget(self._timeline)
        side.addWidget(timeline_panel)

        quick_panel = QtWidgets.QFrame()
        quick_panel.setProperty("role", "glassPanel")
        quick_layout = QtWidgets.QVBoxLayout(quick_panel)
        quick_layout.setContentsMargins(18, 18, 18, 18)
        quick_layout.setSpacing(10)
        self._quick_title_label = QtWidgets.QLabel()
        self._quick_title_label.setProperty("role", "sectionTitle")
        quick_layout.addWidget(self._quick_title_label)
        self._quick_hint_label = QtWidgets.QLabel()
        self._quick_hint_label.setProperty("role", "pageBody")
        self._quick_hint_label.setWordWrap(True)
        quick_layout.addWidget(self._quick_hint_label)
        side.addWidget(quick_panel)

        recent_panel = QtWidgets.QFrame()
        recent_panel.setProperty("role", "glassPanel")
        recent_layout = QtWidgets.QVBoxLayout(recent_panel)
        recent_layout.setContentsMargins(18, 18, 18, 18)
        recent_layout.setSpacing(10)
        self._recent_strip_title_label = QtWidgets.QLabel()
        self._recent_strip_title_label.setProperty("role", "sectionTitle")
        recent_layout.addWidget(self._recent_strip_title_label)
        self._recent_strip = QtWidgets.QListWidget()
        self._recent_strip.setProperty("role", "recentList")
        self._recent_strip.setMaximumHeight(180)
        self._recent_strip.itemActivated.connect(self._open_recent_item)
        self._recent_strip.itemClicked.connect(self._open_recent_item)
        recent_layout.addWidget(self._recent_strip)
        side.addWidget(recent_panel)
        side.addStretch(1)

        root.addLayout(body, 1)
        self._stack.addWidget(page)

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
        self._populate_recent_list(self._recent_list)

    def _refresh_recent_strip(self) -> None:
        if not hasattr(self, "_recent_strip"):
            return
        self._populate_recent_list(self._recent_strip, limit=4)

    def _populate_recent_list(self, widget: QtWidgets.QListWidget, *, limit: int | None = None) -> None:
        widget.clear()
        paths = self._recent_project_paths[:limit] if limit is not None else self._recent_project_paths
        if not paths:
            item = QtWidgets.QListWidgetItem(self._i18n.t("dashboard.empty.recent_empty"))
            item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            widget.addItem(item)
            return
        for path_text in paths:
            path = Path(path_text)
            item = QtWidgets.QListWidgetItem(f"{path.name}\n{path}")
            item.setToolTip(str(path))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
            widget.addItem(item)

    def _refresh_project_summary(self) -> None:
        if self._project is None or not hasattr(self, "_cards"):
            return

        root = self._project.root_dir
        config_dir = root / "config"
        data_dir = root / "data"
        maneuver = _read_json(config_dir / "maneuver_strategy.json") or {}
        launch_config = _read_json(config_dir / "launch_window.json") or {}
        tracking_config = _read_json(config_dir / "tracking_arc.json") or {}
        ai_report = data_dir / "ai_project_analysis.md"

        orbit_rows = _count_csv_rows(data_dir / "full_orbit_history.csv")
        final_orbit_row = self._last_csv_row(data_dir / "full_orbit_history.csv")
        launch_rows = _count_csv_rows(data_dir / "launch_window_results.csv")
        sample_rows = _count_csv_rows(data_dir / "launch_window_samples.csv")
        tracking_assets = self._count_enabled_assets(tracking_config)

        satellite_status = "Planned"
        maneuver_status = "Ready" if orbit_rows else ("Planned" if maneuver else "Disconnected")
        launch_status = "Ready" if launch_rows else ("Loading" if sample_rows else "Planned")
        tracking_status = "Ready" if tracking_config and launch_rows else ("Planned" if tracking_config else "Disconnected")
        spice_status = self._spice_runtime_status
        ai_status = "Ready" if ai_report.exists() else "Planned"

        self._title_label.setText(self._project.name)
        self._project_path_label.setText(str(root))
        self._project_updated_label.setText(self._i18n.t("dashboard.project_updated", updated=self._project.updated_utc))

        self._metrics["orbit_samples"].set_metric(
            self._i18n.t("dashboard.kpi.orbit_samples"),
            str(orbit_rows),
            self._i18n.t("dashboard.kpi.orbit_samples.caption"),
        )
        self._metrics["maneuver_count"].set_metric(
            self._i18n.t("dashboard.kpi.maneuver_count"),
            str(int(maneuver.get("maneuver_count", 0) or 0)),
            self._i18n.t("dashboard.kpi.maneuver_count.caption"),
        )
        self._metrics["launch_windows"].set_metric(
            self._i18n.t("dashboard.kpi.launch_windows"),
            str(launch_rows),
            self._i18n.t("dashboard.kpi.launch_windows.caption", samples=sample_rows),
        )
        self._metrics["tracking_assets"].set_metric(
            self._i18n.t("dashboard.kpi.tracking_assets"),
            str(tracking_assets),
            self._i18n.t("dashboard.kpi.tracking_assets.caption"),
        )

        self._set_card(
            "satellite",
            "dashboard.card.satellite",
            satellite_status,
            self._i18n.t("dashboard.summary.satellite_body"),
            [0.0, 0.0],
        )

        self._set_card(
            "maneuver",
            "dashboard.card.maneuver",
            maneuver_status,
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
            self._numeric_csv_series(data_dir / "full_orbit_history.csv", "mass_kg", limit=80),
        )

        self._set_card(
            "launch_window",
            "dashboard.card.launch_window",
            launch_status,
            self._i18n.t(
                "dashboard.summary.launch_window_body",
                windows=launch_rows,
                samples=sample_rows,
                step=_fmt_float(launch_config.get("sampling_step_min"), 3, " min"),
            ),
            [float(sample_rows), float(launch_rows + 1)],
        )

        self._set_card(
            "tracking_arc",
            "dashboard.card.tracking_arc",
            tracking_status,
            self._i18n.t(
                "dashboard.summary.tracking_arc_body",
                assets=tracking_assets,
                windows=launch_rows,
                config="OK" if tracking_config else "--",
            ),
            [float(tracking_assets), float(launch_rows), float(max(tracking_assets, launch_rows))],
        )

        self._set_card(
            "spice",
            "dashboard.card.spice",
            spice_status,
            self._i18n.t(
                "dashboard.summary.spice_body",
                status=self._i18n.t("status.ready")
                if spice_status == "Ready"
                else self._i18n.t("status.disconnected"),
                kernels=str(data_dir / "kernels"),
            ),
            [1.0 if spice_status == "Ready" else 0.0, 1.0],
        )

        self._set_card(
            "ai_analysis",
            "dashboard.card.ai_analysis",
            ai_status,
            self._i18n.t(
                "dashboard.summary.ai_body",
                report=str(ai_report) if ai_report.exists() else "--",
            ),
            [0.2, 0.35, 0.42, 0.55, 0.62, 0.8 if ai_report.exists() else 0.5],
        )

        self._timeline.set_items(
            [
                (self._i18n.t("dashboard.timeline.satellite"), satellite_status),
                (self._i18n.t("dashboard.timeline.maneuver"), maneuver_status),
                (self._i18n.t("dashboard.timeline.launch"), launch_status),
                (self._i18n.t("dashboard.timeline.tracking"), tracking_status),
                (self._i18n.t("dashboard.timeline.spice"), spice_status),
                (self._i18n.t("dashboard.timeline.ai"), ai_status),
            ]
        )

        statuses = [satellite_status, maneuver_status, launch_status, tracking_status, spice_status, ai_status]
        readiness = sum(_status_rank(status) for status in statuses) / (len(statuses) * 3)
        self._quick_hint_label.setText(self._i18n.t("dashboard.quick_hint", readiness=f"{readiness * 100:.0f}%"))

    @staticmethod
    def _last_csv_row(path: Path) -> dict[str, str] | None:
        last: dict[str, str] | None = None
        for row in _read_csv_rows(path):
            last = row
        return last

    @staticmethod
    def _numeric_csv_series(path: Path, column: str, *, limit: int) -> list[float]:
        values: list[float] = []
        for row in _read_csv_rows(path, limit=limit):
            try:
                values.append(float(row.get(column, "")))
            except ValueError:
                continue
        return values

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

    def _set_card(self, key: str, title_key: str, status: str, body: str, points: list[float]) -> None:
        card = self._cards[key]
        card.title.setText(self._i18n.t(title_key))
        if status == "Ready":
            card.set_status_text(self._i18n.t("status.ready"), "statusReady")
        elif status == "Loading":
            card.set_status_text(self._i18n.t("status.loading"), "statusLoading")
        elif status == "Planned":
            card.set_status_text(self._i18n.t("status.planned"), "statusPlanned")
        else:
            card.set_status_text(self._i18n.t("status.disconnected"), "statusDisconnected")
        card.body.setText(body)
        card.sparkline.set_points(points)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._empty_eyebrow_label.setText(t("dashboard.empty.eyebrow"))
        self._empty_title_label.setText(t("dashboard.empty.title"))
        self._empty_subtitle_label.setText(t("dashboard.empty.subtitle"))
        self._new_project_button.setText(t("dashboard.empty.new_project"))
        self._open_project_button.setText(t("dashboard.empty.open_project"))
        self._recent_title_label.setText(t("dashboard.empty.recent_title"))

        self._project_eyebrow_label.setText(t("dashboard.project.eyebrow"))
        self._subtitle_label.setText(t("dashboard.project_subtitle"))
        self._project_new_button.setText(t("dashboard.empty.new_project"))
        self._project_open_button.setText(t("dashboard.empty.open_project"))
        self._refresh_button.setText(t("dashboard.refresh"))
        self._timeline_title_label.setText(t("dashboard.timeline.title"))
        self._quick_title_label.setText(t("dashboard.quick_title"))
        self._recent_strip_title_label.setText(t("dashboard.empty.recent_title"))
        self._refresh_recent_projects()
        self._refresh_recent_strip()
        self._refresh_project_summary()
