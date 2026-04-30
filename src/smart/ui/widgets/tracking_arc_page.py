from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.earth_orientation import parse_utc
from smart.services.launch_window import (
    config_from_payload,
    default_ground_station_presets,
    default_launch_window_config,
    default_relay_satellite_presets,
    merge_launch_window_samples,
    tracking_assets_from_config,
)
from smart.services.project_workspace import ProjectWorkspace
from smart.services.tracking_arc import (
    TRACKING_ARC_POINT_LEADING,
    TrackingArcOrbitResult,
    TrackingArcSegment,
    compute_tracking_arcs_for_window,
)
from smart.ui.i18n import I18nManager
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDoubleSpinBox


BEIJING_TZ = timezone(timedelta(hours=8))


class TrackingArcGanttWidget(QtWidgets.QWidget):
    _COLORS = {
        "burn": QtGui.QColor("#B91C1C"),
        "ground": QtGui.QColor("#2E7D5B"),
        "relay": QtGui.QColor("#2563A6"),
        "shadow": QtGui.QColor("#6B7280"),
    }

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: TrackingArcOrbitResult | None = None
        self._segment_rects: list[tuple[QtCore.QRectF, TrackingArcSegment]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(260)
        self.setMinimumWidth(980)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.MinimumExpanding)

    def clear(self) -> None:
        self._result = None
        self._segment_rects = []
        self.setMinimumHeight(260)
        self.updateGeometry()
        self.update()

    def set_result(self, result: TrackingArcOrbitResult) -> None:
        self._result = result
        self._segment_rects = []
        self.setMinimumHeight(max(260, 92 + len(result.row_labels) * 38))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QtCore.QSize:
        row_count = 0 if self._result is None else len(self._result.row_labels)
        return QtCore.QSize(980, max(260, 92 + row_count * 38))

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        point = event.position()
        for rect, segment in self._segment_rects:
            if rect.contains(point):
                self.setToolTip(self._segment_tooltip(segment))
                return
        self.setToolTip("")

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, QtGui.QColor("#FFFDF8"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#D8D0C2"), 1))
        painter.drawRoundedRect(rect, 6, 6)

        if self._result is None:
            painter.setPen(QtGui.QColor("#6B6257"))
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "暂无跟踪弧段计算结果")
            return

        start_utc = parse_utc(self._result.timeline_start_utc)
        end_utc = parse_utc(self._result.timeline_end_utc)
        left = min(300.0, max(180.0, rect.width() * 0.24))
        right = 18.0
        top = 44.0
        bottom = 34.0
        plot_width = max(1.0, rect.width() - left - right)
        row_height = 28.0
        row_gap = 10.0
        span_seconds = max(60.0, (end_utc - start_utc).total_seconds())
        axis_y = top - 16.0
        self._segment_rects = []

        painter.setPen(QtGui.QPen(QtGui.QColor("#D8D0C2"), 1))
        painter.drawLine(QtCore.QPointF(left, axis_y), QtCore.QPointF(left + plot_width, axis_y))
        for index in range(6):
            ratio = index / 5
            x = left + plot_width * ratio
            tick_utc = start_utc + timedelta(seconds=span_seconds * ratio)
            painter.drawLine(QtCore.QPointF(x, axis_y - 4), QtCore.QPointF(x, axis_y + 4))
            label = tick_utc.astimezone(BEIJING_TZ).strftime("%m-%d %H:%M")
            painter.setPen(QtGui.QColor("#5F564D"))
            painter.drawText(QtCore.QRectF(x - 42, 8, 84, 18), QtCore.Qt.AlignmentFlag.AlignCenter, label)
            painter.setPen(QtGui.QPen(QtGui.QColor("#D8D0C2"), 1))

        for row_index, row_label in enumerate(self._result.row_labels):
            row_top = top + row_index * (row_height + row_gap)
            row_rect = QtCore.QRectF(left, row_top, plot_width, row_height)
            if row_index % 2:
                painter.fillRect(
                    QtCore.QRectF(1, row_top - 4, rect.width() - 2, row_height + 8),
                    QtGui.QColor("#F6F0E6"),
                )
            painter.setPen(QtGui.QColor("#2A2520"))
            label_text = painter.fontMetrics().elidedText(
                row_label,
                QtCore.Qt.TextElideMode.ElideRight,
                int(left - 18),
            )
            painter.drawText(
                QtCore.QRectF(10, row_top, left - 20, row_height),
                QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight,
                label_text,
            )
            painter.setPen(QtGui.QPen(QtGui.QColor("#E5DDD0"), 1))
            painter.drawLine(
                QtCore.QPointF(left, row_rect.center().y()),
                QtCore.QPointF(left + plot_width, row_rect.center().y()),
            )

        for segment in self._result.segments:
            if segment.row_label not in self._result.row_labels:
                continue
            segment_start = parse_utc(segment.start_utc)
            segment_end = parse_utc(segment.end_utc)
            if segment_end <= start_utc or segment_start >= end_utc:
                continue
            clipped_start = max(segment_start, start_utc)
            clipped_end = min(segment_end, end_utc)
            row_index = self._result.row_labels.index(segment.row_label)
            row_top = top + row_index * (row_height + row_gap)
            x1 = left + ((clipped_start - start_utc).total_seconds() / span_seconds) * plot_width
            x2 = left + ((clipped_end - start_utc).total_seconds() / span_seconds) * plot_width
            bar_rect = QtCore.QRectF(x1, row_top + 4, max(3.0, x2 - x1), row_height - 8)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(self._COLORS.get(segment.kind, QtGui.QColor("#8B6F47")))
            painter.drawRoundedRect(bar_rect, 4, 4)
            self._segment_rects.append((bar_rect, segment))
            if bar_rect.width() >= 44:
                painter.setPen(QtGui.QColor("#FFFFFF"))
                minutes = max(0.0, (segment_end - segment_start).total_seconds() / 60.0)
                painter.drawText(
                    bar_rect.adjusted(5, 0, -5, 0),
                    QtCore.Qt.AlignmentFlag.AlignCenter,
                    f"{minutes:.0f} min",
                )

        painter.setPen(QtGui.QColor("#6B6257"))
        painter.drawText(
            QtCore.QRectF(left, rect.height() - bottom + 4, plot_width, 20),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            "北京时间",
        )

    @staticmethod
    def _segment_tooltip(segment: TrackingArcSegment) -> str:
        start_utc = parse_utc(segment.start_utc)
        end_utc = parse_utc(segment.end_utc)
        start_text = start_utc.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        end_text = end_utc.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        minutes = max(0.0, (end_utc - start_utc).total_seconds() / 60.0)
        return f"{segment.row_label}\n{start_text} - {end_text}\n{minutes:.1f} min"


class TrackingArcPage(QtWidgets.QWidget):
    def __init__(
        self,
        i18n: I18nManager,
        workspace: ProjectWorkspace,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._workspace = workspace
        self._windows: list[Any] = []
        self._orbit_results: dict[str, TrackingArcOrbitResult] = {}
        self._status_role = "statusDisconnected"
        self._number_fields: dict[str, NoWheelDoubleSpinBox] = {}
        self._ground_station_table: QtWidgets.QTableWidget | None = None
        self._relay_satellite_table: QtWidgets.QTableWidget | None = None
        self._ground_station_preset_names = {str(item["name"]) for item in default_ground_station_presets()}
        self._relay_satellite_preset_names = {str(item["name"]) for item in default_relay_satellite_presets()}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_result_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([600, 900])

        self._status_label = QtWidgets.QLabel()
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh_from_workspace()

    def refresh_from_workspace(self) -> None:
        if self._workspace.current_project is None:
            self._set_config(default_launch_window_config())
            self._set_controls_enabled(False)
            self._clear_results()
            self._set_windows([])
            self._set_status("statusDisconnected", "没有活动项目。")
            return

        try:
            payload = self._load_config_payload()
        except Exception as exc:
            payload = default_launch_window_config()
            self._set_status("statusDisconnected", f"加载跟踪弧段配置失败：{exc}")
        self._set_config(payload)
        self._set_controls_enabled(True)
        self._refresh_source_labels()
        self._reload_windows(show_status=False)
        if self._windows:
            self._set_status("statusReady", f"已加载发射窗口：{len(self._windows)} 个。")
        else:
            self._set_status("statusReady", "已加载跟踪弧段参数，暂无可选发射窗口。")

    def _build_left_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        layout = QtWidgets.QVBoxLayout(canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_source_card())
        layout.addWidget(self._build_window_card())
        layout.addWidget(self._build_tracking_asset_card())
        layout.addWidget(self._build_action_card())
        layout.addStretch(1)
        return scroll

    def _build_source_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._source_title_label = self._card_title()
        layout.addWidget(self._source_title_label)
        self._source_body_label = QtWidgets.QLabel()
        self._source_body_label.setProperty("role", "cardCaption")
        self._source_body_label.setWordWrap(True)
        layout.addWidget(self._source_body_label)

        self._strategy_path_label = self._path_label()
        self._history_path_label = self._path_label()
        self._tracking_config_path_label = self._path_label()
        self._sample_path_label = self._path_label()
        layout.addWidget(self._strategy_path_label)
        layout.addWidget(self._history_path_label)
        layout.addWidget(self._tracking_config_path_label)
        layout.addWidget(self._sample_path_label)
        return card

    def _build_window_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._window_title_label = self._card_title()
        layout.addWidget(self._window_title_label)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        self._window_combo = NoWheelComboBox()
        self._window_combo.currentIndexChanged.connect(self._on_window_selection_changed)
        form.addRow("发射窗口", self._window_combo)
        self._number_fields["rocket_flight_time_s"] = self._double_spin(2134.4121, 0.0, 20000.0, 0.1, 3)
        form.addRow("火箭飞行时间 (s)", self._number_fields["rocket_flight_time_s"])
        layout.addLayout(form)

        self._window_detail_label = QtWidgets.QLabel()
        self._window_detail_label.setProperty("role", "cardCaption")
        self._window_detail_label.setWordWrap(True)
        layout.addWidget(self._window_detail_label)
        return card

    def _build_tracking_asset_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = self._card_title()
        title.setText("测控资源")
        layout.addWidget(title)

        self._ground_station_table = self._asset_table()
        layout.addWidget(self._section_label("测控地面站"))
        layout.addWidget(self._ground_station_table)
        ground_buttons = QtWidgets.QHBoxLayout()
        self._add_custom_ground_button = QtWidgets.QPushButton("新增地面站")
        self._add_custom_ground_button.clicked.connect(
            lambda: self._append_asset_row(
                self._ground_station_table,
                {"enabled": True, "name": "自定义地面站", "longitude_deg": 0.0, "latitude_deg": 0.0, "altitude_m": 0.0},
            )
        )
        self._delete_custom_ground_button = QtWidgets.QPushButton("删除地面站")
        self._delete_custom_ground_button.clicked.connect(lambda: self._delete_selected_rows(self._ground_station_table))
        ground_buttons.addWidget(self._add_custom_ground_button)
        ground_buttons.addWidget(self._delete_custom_ground_button)
        ground_buttons.addStretch(1)
        layout.addLayout(ground_buttons)

        ground_form = QtWidgets.QGridLayout()
        ground_form.setHorizontalSpacing(12)
        ground_form.setVerticalSpacing(10)
        self._number_fields["ground_station_min_elevation_deg"] = self._double_spin(5.0, -90.0, 90.0, 0.5, 2)
        self._number_fields["ground_station_max_theta_st_deg"] = self._double_spin(70.0, 0.0, 180.0, 0.5, 2)
        ground_form.addWidget(QtWidgets.QLabel("仰角最小值 (deg)"), 0, 0)
        ground_form.addWidget(self._number_fields["ground_station_min_elevation_deg"], 0, 1)
        ground_form.addWidget(QtWidgets.QLabel("天线角最大值 (deg)"), 0, 2)
        ground_form.addWidget(self._number_fields["ground_station_max_theta_st_deg"], 0, 3)
        ground_form.setColumnStretch(1, 1)
        ground_form.setColumnStretch(3, 1)
        layout.addLayout(ground_form)

        self._relay_satellite_table = self._asset_table()
        layout.addWidget(self._section_label("中继星"))
        layout.addWidget(self._relay_satellite_table)
        relay_note = QtWidgets.QLabel("中继星姿态：+Z 指向地球，+X 指向卫星速度方向。")
        relay_note.setProperty("role", "cardCaption")
        relay_note.setWordWrap(True)
        layout.addWidget(relay_note)

        relay_buttons = QtWidgets.QHBoxLayout()
        self._add_custom_relay_button = QtWidgets.QPushButton("新增中继星")
        self._add_custom_relay_button.clicked.connect(
            lambda: self._append_asset_row(
                self._relay_satellite_table,
                {"enabled": True, "name": "自定义中继星", "longitude_deg": 0.0, "latitude_deg": 0.0, "altitude_m": 35786000.0},
            )
        )
        self._delete_custom_relay_button = QtWidgets.QPushButton("删除中继星")
        self._delete_custom_relay_button.clicked.connect(lambda: self._delete_selected_rows(self._relay_satellite_table))
        relay_buttons.addWidget(self._add_custom_relay_button)
        relay_buttons.addWidget(self._delete_custom_relay_button)
        relay_buttons.addStretch(1)
        layout.addLayout(relay_buttons)

        relay_form = QtWidgets.QGridLayout()
        relay_form.setHorizontalSpacing(12)
        relay_form.setVerticalSpacing(10)
        self._number_fields["relay_alpha_abs_max_deg"] = self._double_spin(20.0, 0.0, 180.0, 0.5, 2)
        self._number_fields["relay_beta_abs_max_deg"] = self._double_spin(40.0, 0.0, 180.0, 0.5, 2)
        self._number_fields["relay_max_theta_st_deg"] = self._double_spin(80.0, 0.0, 180.0, 0.5, 2)
        relay_form.addWidget(QtWidgets.QLabel("alpha 最大值 (deg)"), 0, 0)
        relay_form.addWidget(self._number_fields["relay_alpha_abs_max_deg"], 0, 1)
        relay_form.addWidget(QtWidgets.QLabel("beta 最大值 (deg)"), 0, 2)
        relay_form.addWidget(self._number_fields["relay_beta_abs_max_deg"], 0, 3)
        relay_form.addWidget(QtWidgets.QLabel("天线覆盖角最大值 (deg)"), 1, 0)
        relay_form.addWidget(self._number_fields["relay_max_theta_st_deg"], 1, 1)
        relay_form.setColumnStretch(1, 1)
        relay_form.setColumnStretch(3, 1)
        layout.addLayout(relay_form)

        return card

    def _build_action_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._action_title_label = self._card_title()
        layout.addWidget(self._action_title_label)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self._reload_button = QtWidgets.QPushButton("重新加载")
        self._reload_button.clicked.connect(self.refresh_from_workspace)
        self._save_button = QtWidgets.QPushButton("保存参数")
        self._save_button.clicked.connect(self.save_config)
        self._calculate_button = QtWidgets.QPushButton("计算跟踪弧段")
        self._calculate_button.clicked.connect(self.calculate_tracking_arcs)
        self._reload_windows_button = QtWidgets.QPushButton("刷新窗口")
        self._reload_windows_button.clicked.connect(lambda: self._reload_windows(show_status=True))
        for button in (self._reload_button, self._save_button, self._reload_windows_button, self._calculate_button):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _build_result_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        summary_card = self._card()
        summary_layout = QtWidgets.QGridLayout(summary_card)
        summary_layout.setContentsMargins(18, 18, 18, 18)
        summary_layout.setHorizontalSpacing(16)
        summary_layout.setVerticalSpacing(8)
        self._summary_title_label = self._card_title()
        summary_layout.addWidget(self._summary_title_label, 0, 0, 1, 4)
        self._summary_values: dict[str, QtWidgets.QLabel] = {}
        for index, (key, caption) in enumerate(
            (
                ("window", "窗口"),
                ("orbit", "轨道"),
                ("launch", "发射时刻"),
                ("t0", "入轨 T0"),
                ("shadow", "地影总时长"),
                ("maneuvers", "点火次数"),
            ),
            start=1,
        ):
            label = QtWidgets.QLabel(caption)
            label.setProperty("role", "cardCaption")
            value = QtWidgets.QLabel("--")
            value.setProperty("role", "pageBody")
            value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            row = (index + 1) // 2
            col = 0 if index % 2 else 2
            summary_layout.addWidget(label, row, col)
            summary_layout.addWidget(value, row, col + 1)
            self._summary_values[key] = value
        layout.addWidget(summary_card)

        point_row = QtWidgets.QHBoxLayout()
        point_row.setSpacing(10)
        point_row.addWidget(QtWidgets.QLabel("轨道显示"))
        self._orbit_point_combo = NoWheelComboBox()
        self._orbit_point_combo.currentIndexChanged.connect(self._on_orbit_point_changed)
        point_row.addWidget(self._orbit_point_combo)
        point_row.addStretch(1)
        layout.addLayout(point_row)

        self._asset_summary_table = QtWidgets.QTableWidget(0, 5)
        self._asset_summary_table.setProperty("role", "card")
        self._asset_summary_table.setAlternatingRowColors(True)
        self._asset_summary_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._asset_summary_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._asset_summary_table.verticalHeader().setVisible(False)
        self._asset_summary_table.horizontalHeader().setStretchLastSection(True)
        self._asset_summary_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._asset_summary_table.setHorizontalHeaderLabels(["类型", "资源", "跟踪段数", "总时长/min", "最长连续/min"])
        self._asset_summary_table.setMinimumHeight(160)
        layout.addWidget(self._asset_summary_table, 1)

        self._gantt_chart = TrackingArcGanttWidget()
        gantt_scroll = QtWidgets.QScrollArea()
        gantt_scroll.setWidgetResizable(True)
        gantt_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        gantt_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        gantt_scroll.setWidget(self._gantt_chart)
        layout.addWidget(gantt_scroll, 2)
        return panel

    def save_config(self) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return None
        try:
            path = self._workspace.save_tracking_arc_config(self.config_payload())
        except Exception as exc:
            self._set_status("statusDisconnected", f"保存跟踪弧段参数失败：{exc}")
            return None
        self._set_status("statusReady", f"已保存跟踪弧段参数：{path}")
        return path

    def calculate_tracking_arcs(self) -> None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return
        if self.save_config() is None:
            return
        selected_window = self._selected_window()
        if selected_window is None:
            self._set_status("statusDisconnected", "没有可用发射窗口。请先在发射窗口页面完成计算。")
            return

        try:
            strategy = self._workspace.load_maneuver_strategy()
            if strategy is None:
                raise FileNotFoundError(self._workspace.maneuver_strategy_path())
        except Exception as exc:
            self._set_status("statusDisconnected", f"加载变轨策略失败：{exc}")
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        self._set_status("statusLoading", "正在计算跟踪弧段。")
        try:
            config = config_from_payload(self.config_payload())
            results = compute_tracking_arcs_for_window(
                orbit_history_csv=self._workspace.data_dir() / "full_orbit_history.csv",
                maneuver_strategy=strategy,
                config=config,
                window=selected_window,
                assets=tracking_assets_from_config(config),
            )
        except Exception as exc:
            self._set_status("statusDisconnected", f"跟踪弧段计算失败：{exc}")
            return
        finally:
            if QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()

        self._orbit_results = {result.point_key: result for result in results}
        self._set_orbit_point_options(results)
        self._show_orbit_result(self._orbit_results.get(TRACKING_ARC_POINT_LEADING, results[0]))
        self._set_status("statusReady", f"跟踪弧段计算完成：{len(results)} 条轨道。")

    def config_payload(self) -> dict[str, Any]:
        try:
            payload = self._load_config_payload() if self._workspace.current_project is not None else None
        except Exception:
            payload = None
        result = dict(payload or default_launch_window_config())
        for key, field in self._number_fields.items():
            result[key] = float(field.value())
        ground_rows = self._asset_rows_payload(self._ground_station_table, asset_type="ground")
        result["ground_station_presets"] = [
            row for row in ground_rows if str(row.get("name", "")) in self._ground_station_preset_names
        ]
        result["custom_ground_stations"] = [
            row for row in ground_rows if str(row.get("name", "")) not in self._ground_station_preset_names
        ]
        relay_rows = self._asset_rows_payload(self._relay_satellite_table, asset_type="relay")
        result["relay_satellite_presets"] = [
            row for row in relay_rows if str(row.get("name", "")) in self._relay_satellite_preset_names
        ]
        result["custom_relay_satellites"] = [
            row for row in relay_rows if str(row.get("name", "")) not in self._relay_satellite_preset_names
        ]
        return result

    def _load_config_payload(self) -> dict[str, Any]:
        payload = self._workspace.load_tracking_arc_config()
        if payload is not None:
            return payload
        return self._workspace.load_launch_window_config() or default_launch_window_config()

    def _set_config(self, payload: dict[str, Any]) -> None:
        config = config_from_payload(payload)
        for key, field in self._number_fields.items():
            field.blockSignals(True)
            field.setValue(float(getattr(config, key)))
            field.blockSignals(False)
        self._set_asset_rows(
            self._ground_station_table,
            [*config.ground_station_presets, *config.custom_ground_stations],
        )
        self._set_asset_rows(
            self._relay_satellite_table,
            [*config.relay_satellite_presets, *config.custom_relay_satellites],
        )

    def _reload_windows(self, *, show_status: bool) -> None:
        if self._workspace.current_project is None:
            self._set_windows([])
            return
        path = self._sample_csv_path()
        if not path.exists():
            self._set_windows([])
            if show_status:
                self._set_status("statusDisconnected", "未找到发射窗口样本，请先计算发射窗口。")
            return
        try:
            samples = self._read_sample_csv(path)
            launch_payload = self._workspace.load_launch_window_config() or default_launch_window_config()
            windows = merge_launch_window_samples(samples, config_from_payload(launch_payload))
        except Exception as exc:
            self._set_windows([])
            self._set_status("statusDisconnected", f"加载发射窗口失败：{exc}")
            return
        self._set_windows(windows)
        if show_status:
            self._set_status("statusReady", f"已刷新发射窗口：{len(windows)} 个。")

    def _set_windows(self, windows: list[Any]) -> None:
        current_index = self._window_combo.currentData() if hasattr(self, "_window_combo") else None
        self._windows = list(windows)
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        for index, window in enumerate(self._windows):
            label = (
                f"{index + 1}. {self._format_beijing(window.window_start_utc)} - "
                f"{self._format_beijing(window.window_end_utc)} ({window.duration_min:.1f} min)"
            )
            self._window_combo.addItem(label, index)
        if self._windows:
            selected = int(current_index) if isinstance(current_index, int) and 0 <= current_index < len(self._windows) else 0
            self._window_combo.setCurrentIndex(selected)
        self._window_combo.blockSignals(False)
        self._refresh_window_detail()

    def _selected_window(self) -> Any | None:
        if not self._windows:
            return None
        index = self._window_combo.currentData()
        try:
            parsed = int(index)
        except (TypeError, ValueError):
            parsed = self._window_combo.currentIndex()
        if parsed < 0 or parsed >= len(self._windows):
            return None
        return self._windows[parsed]

    def _on_window_selection_changed(self, _index: int) -> None:
        self._clear_results()
        self._refresh_window_detail()

    def _refresh_window_detail(self) -> None:
        window = self._selected_window()
        if window is None:
            self._window_detail_label.setText("当前没有可选发射窗口。")
            for key in ("window",):
                self._summary_values[key].setText("--")
            return
        self._window_detail_label.setText(
            f"前沿：{self._format_beijing(window.window_start_utc)}；"
            f"后沿：{self._format_beijing(window.window_end_utc)}；长度 {window.duration_min:.1f} min。"
        )
        self._summary_values["window"].setText(
            f"{self._format_beijing(window.window_start_utc)} - {self._format_beijing(window.window_end_utc)}"
        )

    def _set_orbit_point_options(self, results: list[TrackingArcOrbitResult]) -> None:
        current_key = self._orbit_point_combo.currentData()
        self._orbit_point_combo.blockSignals(True)
        self._orbit_point_combo.clear()
        for result in results:
            self._orbit_point_combo.addItem(result.point_label, result.point_key)
        preferred_key = current_key if current_key in self._orbit_results else TRACKING_ARC_POINT_LEADING
        index = self._orbit_point_combo.findData(preferred_key)
        self._orbit_point_combo.setCurrentIndex(index if index >= 0 else 0)
        self._orbit_point_combo.blockSignals(False)

    def _on_orbit_point_changed(self, _index: int) -> None:
        key = str(self._orbit_point_combo.currentData())
        result = self._orbit_results.get(key)
        if result is not None:
            self._show_orbit_result(result)

    def _show_orbit_result(self, result: TrackingArcOrbitResult | None) -> None:
        if result is None:
            self._clear_results()
            return
        self._summary_values["orbit"].setText(result.point_label)
        self._summary_values["launch"].setText(self._format_beijing(result.launch_utc))
        self._summary_values["t0"].setText(self._format_beijing(result.t0_utc))
        self._summary_values["shadow"].setText(f"{result.shadow_total_min:.1f} min")
        self._summary_values["maneuvers"].setText(str(result.maneuver_count))
        self._asset_summary_table.setRowCount(0)
        for summary in result.asset_summaries:
            row = self._asset_summary_table.rowCount()
            self._asset_summary_table.insertRow(row)
            self._set_table_values(
                self._asset_summary_table,
                row,
                [
                    "地面站" if summary.asset_type == "ground" else "中继星",
                    summary.name,
                    str(summary.interval_count),
                    f"{summary.total_duration_min:.1f}",
                    f"{summary.longest_duration_min:.1f}",
                ],
            )
        self._gantt_chart.set_result(result)

    def _clear_results(self) -> None:
        self._orbit_results = {}
        self._orbit_point_combo.blockSignals(True)
        self._orbit_point_combo.clear()
        self._orbit_point_combo.blockSignals(False)
        self._asset_summary_table.setRowCount(0)
        self._gantt_chart.clear()
        for key, value in self._summary_values.items():
            if key != "window":
                value.setText("--")
        if hasattr(self, "_window_combo"):
            self._refresh_window_detail()

    def _sample_csv_path(self) -> Path:
        return self._workspace.data_dir() / "launch_window_samples.csv"

    def _read_sample_csv(self, path: Path) -> list[dict[str, Any]]:
        import csv
        import json

        samples: list[dict[str, Any]] = []
        numeric_columns = {
            "first_orbit_shadow_min",
            "no_shadow_period_shadow_min",
            "separation_shadow_min",
            "longest_shadow_min",
            "min_burn_sun_margin_deg",
            "max_tracking_gap_min",
            "inclination_deg",
        }
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw_row in reader:
                sample: dict[str, Any] = {
                    "launch_utc": str(raw_row.get("launch_utc", "")).strip(),
                    "t0_utc": str(raw_row.get("t0_utc", "")).strip(),
                    "ok": self._parse_bool(raw_row.get("ok")),
                    "failure": str(raw_row.get("failure", "")).strip(),
                }
                for column in numeric_columns:
                    raw_value = raw_row.get(column)
                    if column == "longest_shadow_min" and not str(raw_value or "").strip():
                        continue
                    sample[column] = self._to_float(raw_value)
                constraint_results = str(raw_row.get("constraint_results", "")).strip()
                if constraint_results:
                    parsed_results = json.loads(constraint_results)
                    if isinstance(parsed_results, list):
                        sample["constraint_results"] = parsed_results
                if sample["launch_utc"]:
                    samples.append(sample)
        return samples

    @staticmethod
    def _parse_bool(value: object) -> bool:
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "y", "通过", "pass", "passed"}

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            *self._number_fields.values(),
            self._ground_station_table,
            self._relay_satellite_table,
            self._window_combo,
            self._orbit_point_combo,
            self._add_custom_ground_button,
            self._delete_custom_ground_button,
            self._add_custom_relay_button,
            self._delete_custom_relay_button,
            self._reload_button,
            self._save_button,
            self._reload_windows_button,
            self._calculate_button,
        ):
            if widget is not None:
                widget.setEnabled(enabled)

    def _refresh_source_labels(self) -> None:
        if self._workspace.current_project is None:
            self._strategy_path_label.setText("变轨策略：--")
            self._history_path_label.setText("变轨结果：--")
            self._tracking_config_path_label.setText("跟踪弧段配置：--")
            self._sample_path_label.setText("发射窗口样本：--")
            return
        self._strategy_path_label.setText(f"变轨策略：{self._workspace.maneuver_strategy_path()}")
        self._history_path_label.setText(f"变轨结果：{self._workspace.data_dir() / 'full_orbit_history.csv'}")
        self._tracking_config_path_label.setText(f"跟踪弧段配置：{self._workspace.tracking_arc_path()}")
        self._sample_path_label.setText(f"发射窗口样本：{self._sample_csv_path()}")

    def _asset_table(self) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["启用", "名称", "经度/deg", "纬度/deg", "高度/m"])
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setMinimumHeight(120)
        return table

    def _set_asset_rows(self, table: QtWidgets.QTableWidget | None, rows: list[dict[str, Any]]) -> None:
        if table is None:
            return
        table.setRowCount(0)
        for row in rows:
            self._append_asset_row(table, row)

    def _append_asset_row(self, table: QtWidgets.QTableWidget | None, row_payload: dict[str, Any]) -> None:
        if table is None:
            return
        row = table.rowCount()
        table.insertRow(row)
        enabled_item = QtWidgets.QTableWidgetItem()
        enabled_item.setFlags(enabled_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        enabled_item.setCheckState(
            QtCore.Qt.CheckState.Checked if bool(row_payload.get("enabled", True)) else QtCore.Qt.CheckState.Unchecked
        )
        enabled_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 0, enabled_item)
        values = [
            str(row_payload.get("name", "")),
            f"{float(row_payload.get('longitude_deg', 0.0)):.6f}",
            f"{float(row_payload.get('latitude_deg', 0.0)):.6f}",
            f"{float(row_payload.get('altitude_m', 0.0)):.3f}",
        ]
        for offset, value in enumerate(values, start=1):
            item = QtWidgets.QTableWidgetItem(value)
            if offset > 1:
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, offset, item)

    def _asset_rows_payload(self, table: QtWidgets.QTableWidget | None, *, asset_type: str) -> list[dict[str, Any]]:
        if table is None:
            return []
        return [self._asset_row_payload(table, row, asset_type=asset_type) for row in range(table.rowCount())]

    def _asset_row_payload(self, table: QtWidgets.QTableWidget, row: int, *, asset_type: str) -> dict[str, Any]:
        enabled_item = table.item(row, 0)
        return {
            "enabled": enabled_item is None or enabled_item.checkState() == QtCore.Qt.CheckState.Checked,
            "name": self._table_text(table, row, 1),
            "longitude_deg": self._to_float(self._table_text(table, row, 2)),
            "latitude_deg": self._to_float(self._table_text(table, row, 3)),
            "altitude_m": self._to_float(self._table_text(table, row, 4)),
            "asset_type": asset_type,
        }

    def _delete_selected_rows(self, table: QtWidgets.QTableWidget | None) -> None:
        if table is None:
            return
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        if not rows and table.currentRow() >= 0:
            rows = [table.currentRow()]
        for row in rows:
            table.removeRow(row)

    def retranslate(self, _language: str | None = None) -> None:
        self._title_label.setText("跟踪弧段分析")
        self._subtitle_label.setText(
            "从已计算的发射窗口中选择一个窗口，分别计算窗口前沿、中点和后沿对应轨道的测控跟踪、点火和地影时段。"
        )
        self._source_title_label.setText("数据来源")
        self._source_body_label.setText("本页复用发射窗口结果、变轨策略和 full_orbit_history.csv；所有时刻显示为北京时间。")
        self._window_title_label.setText("窗口选择")
        self._action_title_label.setText("计算")
        self._summary_title_label.setText("轨道结果")
        self._refresh_source_labels()

    def _set_status(self, role: str, text: str) -> None:
        self._status_role = role
        self._status_label.setProperty("role", role)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setText(text)

    @staticmethod
    def _card() -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        return card

    @staticmethod
    def _card_title() -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setProperty("role", "cardTitle")
        return label

    @staticmethod
    def _path_label() -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setProperty("role", "cardCaption")
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "cardCaption")
        return label

    @staticmethod
    def _double_spin(value: float, minimum: float, maximum: float, step: float, decimals: int) -> NoWheelDoubleSpinBox:
        field = NoWheelDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        field.setSingleStep(step)
        field.setDecimals(decimals)
        field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        return field

    @staticmethod
    def _format_beijing(value: str) -> str:
        return parse_utc(value).astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _set_table_values(table: QtWidgets.QTableWidget, row: int, values: list[str]) -> None:
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, column, item)

    @staticmethod
    def _table_text(table: QtWidgets.QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return "" if item is None else item.text().strip()

    @staticmethod
    def _to_float(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
