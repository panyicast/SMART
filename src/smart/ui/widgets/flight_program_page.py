from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import asdict
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import xml.etree.ElementTree as ET

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from smart.domain.models import EARTH_RADIUS_KM, OrbitTrajectory
from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.flight_program import (
    ATTITUDE_KIND,
    DEPLOYMENT_KIND,
    FlightProgramSamplingContext,
    MODE_AFM,
    MODE_EPM,
    MODE_SPM,
    MODE_TRANSITION,
    FlightProgramSample,
    build_flight_program_sampling_context,
    default_flight_program_payload,
    generate_flight_program_draft,
    normalize_flight_event,
    normalize_flight_program_payload,
    sample_flight_program_state,
    validate_flight_program,
)
from smart.services.launch_window import (
    _gmst_rad,
    config_from_payload,
    default_launch_window_config,
    load_orbit_history_rows,
    merge_launch_window_samples,
    tracking_assets_from_config,
)
from smart.services.project_workspace import ProjectWorkspace
from smart.services.stk_ephemeris import derive_scenario_epoch_utc
from smart.services.stk_link import StkLinkService
from smart.services.tracking_arc import (
    TrackingArcAssetSummary,
    TrackingArcOrbitResult,
    TrackingArcSegment,
    compute_tracking_arc_for_launch_time,
    compute_tracking_arcs_for_window,
    tracking_arc_launch_points,
)
from smart.ui.i18n import I18nManager
from smart.ui.widgets.flight_program_overview import (
    FlightProgramOverviewWidget,
    _FlightProgramScrollArea,
)
from smart.ui.widgets.orbit_views import OrbitPlot3D
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDateTimeEdit, NoWheelDoubleSpinBox
from smart.ui.widgets.table_editing import install_combo_table_edit_delegate, install_table_edit_delegate

BEIJING_TZ = timezone(timedelta(hours=8))
BEIJING_QT_TIMEZONE_ID = b"Asia/Shanghai"
_MANEUVER_PHASES = {"settle", "orbit_control"}
_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _beijing_qtimezone() -> QtCore.QTimeZone:
    return QtCore.QTimeZone(BEIJING_QT_TIMEZONE_ID)


class FlightProgramPage(QtWidgets.QWidget):
    _ATTITUDE_COLUMNS = (
        "序号",
        "锁定",
        "模式",
        "名称",
        "开始 T0+min",
        "结束 T0+min",
        "时长/min",
    )
    _MAJOR_EVENT_COLUMNS = (
        "序号",
        "锁定",
        "名称",
        "开始 T0+min",
        "结束 T0+min",
        "时长/min",
        "瞬时",
    )
    _REFERENCE_COLUMNS = (
        "序号",
        "类型",
        "名称/目标",
        "开始 T0+min",
        "结束 T0+min",
        "时长/min",
    )
    _ATTITUDE_EDITABLE_COLUMNS = {2, 3, 4, 5}
    _MAJOR_EVENT_EDITABLE_COLUMNS = {2, 3, 4}

    def __init__(
        self,
        i18n: I18nManager,
        workspace: ProjectWorkspace,
        stk_link_service_factory: Callable[[], StkLinkService] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._workspace = workspace
        self._stk_link_service_factory = stk_link_service_factory or (lambda: StkLinkService(self._workspace))
        self._program = default_flight_program_payload()
        self._windows: list[Any] = []
        self._tracking_results: dict[str, TrackingArcOrbitResult] = {}
        self._reference_segments: list[dict[str, Any]] = []
        self._selected_event_id = ""
        self._selected_reference_id = ""
        self._playhead_min = 0.0
        self._pending_stk_playhead_sync = False
        self._suppress_table = False
        self._suppress_reference_table = False
        self._suppress_autosave = False
        self._status_role = "statusDisconnected"
        self._orbit_history_cache_key: tuple[str, int, int] | None = None
        self._orbit_history_rows_cache: list[dict[str, float | str]] | None = None
        self._orbit_positions_cache: list[list[float]] | None = None
        self._orbit_history_epoch_cache_key: tuple[str, int, int] | None = None
        self._orbit_history_epoch_cache: object = None
        self._sample_context_cache_key: tuple[tuple[str, int, int], str, str] | None = None
        self._sample_context_cache: FlightProgramSamplingContext | None = None
        self._stk_playhead_sync_timer = QtCore.QTimer(self)
        self._stk_playhead_sync_timer.setSingleShot(True)
        self._stk_playhead_sync_timer.setInterval(180)
        self._stk_playhead_sync_timer.timeout.connect(self._flush_stk_playhead_sync)

        outer_root = QtWidgets.QVBoxLayout(self)
        outer_root.setContentsMargins(0, 0, 0, 0)
        outer_root.setSpacing(0)

        scroll = _FlightProgramScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer_root.addWidget(scroll, 1)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)

        root = QtWidgets.QVBoxLayout(canvas)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        self._title_label = QtWidgets.QLabel("飞行程序设计")
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)
        subtitle = QtWidgets.QLabel("基于变轨、跟踪弧段与地影结果编排姿态模式、过渡段和主要飞行事件。")
        subtitle.setProperty("role", "pageBody")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        root.addLayout(self._build_toolbar())

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setMinimumHeight(520)
        root.addWidget(main_splitter, 1)
        main_splitter.addWidget(self._build_overview_panel())

        bottom_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        bottom_splitter.setChildrenCollapsible(False)
        bottom_splitter.setMinimumHeight(360)
        bottom_splitter.addWidget(self._build_event_design_panel())
        bottom_splitter.addWidget(self._build_right_panel())
        bottom_splitter.setStretchFactor(0, 3)
        bottom_splitter.setStretchFactor(1, 6)
        bottom_splitter.setSizes([560, 920])
        main_splitter.addWidget(bottom_splitter)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 5)
        main_splitter.setSizes([300, 560])

        self._status_label = QtWidgets.QLabel()
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)
        self.refresh_from_workspace()

    def refresh_from_workspace(self) -> None:
        self._suppress_autosave = True
        if self._workspace.current_project is None:
            self._program = default_flight_program_payload()
            self._tracking_results = {}
            self._reference_segments = []
            self._set_controls_enabled(False)
            self._set_status("statusDisconnected", "没有活动项目。")
            self._refresh_all()
            self._suppress_autosave = False
            return
        try:
            self._set_controls_enabled(True)
            try:
                self._program = self._workspace.load_flight_program_config() or default_flight_program_payload()
            except Exception as exc:
                self._program = default_flight_program_payload()
                self._set_status("statusDisconnected", f"加载飞行程序失败：{exc}")
            self._program = normalize_flight_program_payload(self._program)
            launch_mode_index = self._launch_source_combo.findData(str(self._program.get("launch_selection_mode", "window")))
            if launch_mode_index >= 0:
                self._launch_source_combo.blockSignals(True)
                self._launch_source_combo.setCurrentIndex(launch_mode_index)
                self._launch_source_combo.blockSignals(False)
            point_index = self._orbit_point_combo.findData(str(self._program.get("selected_orbit_point", "leading")))
            if point_index >= 0:
                self._orbit_point_combo.blockSignals(True)
                self._orbit_point_combo.setCurrentIndex(point_index)
                self._orbit_point_combo.blockSignals(False)
            self._sync_manual_launch_field_from_state()
            self._reload_windows(show_status=False)
            self._update_launch_source_controls()
            self._sync_selected_t0_from_launch_state()
            self._load_saved_reference_results()
            self._refresh_source_labels()
            self._refresh_all()
            self._set_status("statusReady", "飞行程序页面已就绪。")
        finally:
            self._suppress_autosave = False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if isinstance(watched, QtWidgets.QTableWidget) and event.type() == QtCore.QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QtGui.QKeyEvent):
                if key_event.key() in {QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter}:
                    if watched.state() != QtWidgets.QAbstractItemView.State.EditingState:
                        return self._jump_to_table_current_row(watched)
        return super().eventFilter(watched, event)

    def _build_toolbar(self) -> QtWidgets.QLayout:
        layout = QtWidgets.QHBoxLayout()
        layout.setSpacing(8)
        self._launch_source_label = QtWidgets.QLabel("时间来源")
        layout.addWidget(self._launch_source_label)
        self._launch_source_combo = NoWheelComboBox()
        self._launch_source_combo.addItem("发射窗口", "window")
        self._launch_source_combo.addItem("手动发射时间", "manual")
        self._launch_source_combo.currentIndexChanged.connect(lambda _index: self._on_launch_source_changed())
        layout.addWidget(self._launch_source_combo)
        self._window_label = QtWidgets.QLabel("发射窗口")
        self._window_combo = NoWheelComboBox()
        self._window_combo.setMinimumWidth(300)
        self._window_combo.currentIndexChanged.connect(lambda _index: self._on_window_changed())
        layout.addWidget(self._window_label)
        layout.addWidget(self._window_combo)
        self._manual_launch_label = QtWidgets.QLabel("发射时间")
        layout.addWidget(self._manual_launch_label)
        self._manual_launch_edit = self._launch_datetime_edit()
        self._manual_launch_edit.setMinimumWidth(220)
        self._manual_launch_edit.dateTimeChanged.connect(lambda _value: self._on_manual_launch_changed())
        layout.addWidget(self._manual_launch_edit)
        self._orbit_point_combo = NoWheelComboBox()
        for label, key in (("窗口前沿", "leading"), ("窗口中点", "midpoint"), ("窗口后沿", "trailing")):
            self._orbit_point_combo.addItem(label, key)
        self._orbit_point_combo.currentIndexChanged.connect(lambda _index: self._on_orbit_point_changed())
        layout.addWidget(self._orbit_point_combo)
        self._calculate_refs_button = QtWidgets.QPushButton("计算参考轨")
        self._calculate_refs_button.setProperty("variant", "secondary")
        self._calculate_refs_button.clicked.connect(self.calculate_reference_arcs)
        layout.addWidget(self._calculate_refs_button)
        self._generate_button = QtWidgets.QPushButton("生成草案")
        self._generate_button.clicked.connect(self.generate_draft)
        layout.addWidget(self._generate_button)
        self._save_button = QtWidgets.QPushButton("导出Excel")
        self._save_button.clicked.connect(self.save_program)
        layout.addWidget(self._save_button)
        layout.addStretch(1)
        self._add_attitude_button = QtWidgets.QPushButton("新增姿态")
        self._add_attitude_button.setProperty("variant", "secondary")
        self._add_attitude_button.clicked.connect(lambda: self._add_event(MODE_SPM, self._playhead_min))
        layout.addWidget(self._add_attitude_button)
        self._add_deploy_button = QtWidgets.QPushButton("新增主要事件")
        self._add_deploy_button.setProperty("variant", "secondary")
        self._add_deploy_button.clicked.connect(lambda: self._add_event("deployment", self._playhead_min))
        layout.addWidget(self._add_deploy_button)
        self._delete_button = QtWidgets.QPushButton("删除")
        self._delete_button.setProperty("variant", "secondary")
        self._delete_button.clicked.connect(self._delete_selected_event)
        layout.addWidget(self._delete_button)
        return layout

    def _build_overview_panel(self) -> QtWidgets.QWidget:
        card = self._card("综合时间线总览")
        layout = card.layout()
        self._time_label = QtWidgets.QLabel("T0+0.0 min")
        self._time_label.setProperty("role", "cardCaption")
        layout.addWidget(self._time_label)
        self._overview = FlightProgramOverviewWidget()
        self._overview.playhead_changed.connect(lambda value: self._set_playhead(value, sync_immediately=False))
        self._overview.event_selected.connect(self._select_event)
        self._overview.reference_selected.connect(self._select_reference)
        layout.addWidget(self._overview, 1)
        return card

    def _build_event_design_panel(self) -> QtWidgets.QWidget:
        card = self._card("分组事件表格")
        layout = card.layout()

        self._source_labels = []

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setSpacing(12)
        filter_row.addWidget(QtWidgets.QLabel("参考图层"))
        self._layer_checks: dict[str, QtWidgets.QCheckBox] = {}
        for key, text in (("burn", "点火"), ("shadow", "地影"), ("ground", "地面站"), ("relay", "中继星")):
            check = QtWidgets.QCheckBox(text)
            check.setChecked(True)
            check.stateChanged.connect(lambda _state: self._refresh_timeline())
            self._layer_checks[key] = check
            filter_row.addWidget(check)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        self._table_tabs = QtWidgets.QTabWidget()
        self._table_tabs.setDocumentMode(True)
        self._reference_table = QtWidgets.QTableWidget(0, len(self._REFERENCE_COLUMNS))
        self._reference_table.setHorizontalHeaderLabels(list(self._REFERENCE_COLUMNS))
        self._reference_table.verticalHeader().setVisible(False)
        self._reference_table.setAlternatingRowColors(True)
        self._reference_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._reference_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._reference_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._reference_table.horizontalHeader().setStretchLastSection(False)
        self._reference_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for column, width in ((0, 48), (1, 92), (3, 108), (4, 108), (5, 84)):
            self._reference_table.horizontalHeader().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.Fixed)
            self._reference_table.setColumnWidth(column, width)
        self._reference_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._reference_table.itemSelectionChanged.connect(self._on_reference_selection_changed)
        self._reference_table.itemDoubleClicked.connect(self._on_reference_item_double_clicked)
        self._reference_table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._reference_table.customContextMenuRequested.connect(self._show_reference_context_menu)
        self._reference_table.installEventFilter(self)

        self._event_table = self._create_program_event_table(
            columns=self._ATTITUDE_COLUMNS,
            editable_columns=self._ATTITUDE_EDITABLE_COLUMNS,
            table_kind="attitude",
        )
        self._major_event_table = self._create_program_event_table(
            columns=self._MAJOR_EVENT_COLUMNS,
            editable_columns=self._MAJOR_EVENT_EDITABLE_COLUMNS,
            table_kind="major",
        )
        install_combo_table_edit_delegate(
            self._event_table,
            {
                2: [
                    (MODE_SPM, MODE_SPM),
                    (MODE_EPM, MODE_EPM),
                    (MODE_AFM, MODE_AFM),
                    (MODE_TRANSITION, MODE_TRANSITION),
                ]
            },
        )
        self._table_tabs.addTab(self._reference_table, "参考时段")
        self._table_tabs.addTab(self._event_table, "卫星姿态设置")
        self._table_tabs.addTab(self._major_event_table, "主要飞行事件")
        layout.addWidget(self._table_tabs, 1)

        self._warnings_list = QtWidgets.QListWidget()
        self._warnings_list.setMaximumHeight(92)
        layout.addWidget(self._warnings_list)
        return card

    def _create_program_event_table(
        self,
        *,
        columns: tuple[str, ...],
        editable_columns: set[int],
        table_kind: str,
    ) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(list(columns))
        table.setProperty("tableKind", table_kind)
        table.setProperty("editableColumns", sorted(editable_columns))
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
            | QtWidgets.QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        if table_kind == "attitude":
            width_map = ((0, 48), (1, 64), (2, 108), (4, 108), (5, 108), (6, 84))
            stretch_column = 3
        else:
            width_map = ((0, 48), (1, 64), (3, 108), (4, 108), (5, 84), (6, 64))
            stretch_column = 2
        for column, width in width_map:
            table.horizontalHeader().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(column, width)
        table.horizontalHeader().setSectionResizeMode(stretch_column, QtWidgets.QHeaderView.ResizeMode.Stretch)
        install_table_edit_delegate(table)
        table.itemSelectionChanged.connect(self._on_table_selection_changed)
        table.itemChanged.connect(self._on_table_item_changed)
        table.itemDoubleClicked.connect(self._on_table_item_double_clicked)
        table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_table_context_menu)
        table.installEventFilter(self)
        return table

    def _build_right_panel(self) -> QtWidgets.QWidget:
        preview = self._card("实时状态")
        preview.setMinimumHeight(360)
        preview_layout = preview.layout()
        self._scene_view = OrbitPlot3D()
        self._scene_view.setMinimumHeight(320)
        preview_layout.addWidget(self._scene_view, 1)
        return preview

    def calculate_reference_arcs(self) -> None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return
        self._reload_windows(show_status=False)
        try:
            strategy = self._workspace.load_maneuver_strategy()
            if strategy is None:
                raise FileNotFoundError(self._workspace.maneuver_strategy_path())
            payload = self._workspace.load_tracking_arc_config() or self._workspace.load_launch_window_config() or default_launch_window_config()
            config = config_from_payload(payload)
            if self._launch_selection_mode() == "manual":
                launch_utc = self._manual_launch_utc()
                result = compute_tracking_arc_for_launch_time(
                    orbit_history_csv=self._orbit_history_path(),
                    maneuver_strategy=strategy,
                    config=config,
                    launch_utc=launch_utc,
                    assets=tracking_assets_from_config(config),
                )
                results = [result]
            else:
                window = self._selected_window()
                if window is None:
                    self._set_status("statusDisconnected", "没有可用发射窗口。请先在发射窗口页面完成计算。")
                    return
                results = compute_tracking_arcs_for_window(
                    orbit_history_csv=self._orbit_history_path(),
                    maneuver_strategy=strategy,
                    config=config,
                    window=window,
                    assets=tracking_assets_from_config(config),
                )
        except Exception as exc:
            self._set_status("statusDisconnected", f"计算参考轨失败：{exc}")
            return
        self._tracking_results = {item.point_key: item for item in results}
        self._program["launch_selection_mode"] = self._launch_selection_mode()
        if self._launch_selection_mode() == "manual":
            self._program["selected_launch_utc"] = results[0].launch_utc
        else:
            self._program["selected_orbit_point"] = str(self._orbit_point_combo.currentData() or "leading")
        selected = self._selected_tracking_result()
        if selected is not None:
            self._program["selected_t0_utc"] = selected.t0_utc
            self._program["selected_launch_utc"] = selected.launch_utc
        self._refresh_reference_segments()
        self._refresh_all()
        self._autosave_program()
        self._save_reference_results()
        self._sync_stk_analysis_time_if_available()
        self._set_status("statusReady", f"参考轨计算完成：{len(results)} 条。")

    def generate_draft(self) -> None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return
        if not self._tracking_results and self._can_calculate_reference_arcs():
            self.calculate_reference_arcs()
            if not self._tracking_results:
                return
        try:
            strategy = self._workspace.load_maneuver_strategy()
            if strategy is None:
                raise FileNotFoundError(self._workspace.maneuver_strategy_path())
            tracking_result = self._selected_tracking_result()
            key = "manual" if self._launch_selection_mode() == "manual" else str(self._orbit_point_combo.currentData() or "leading")
            self._program = generate_flight_program_draft(
                orbit_history_csv=self._orbit_history_path(),
                maneuver_strategy=strategy,
                tracking_result=tracking_result,
                selected_orbit_point=key,
                launch_selection_mode=self._launch_selection_mode(),
            )
        except Exception as exc:
            self._set_status("statusDisconnected", f"生成飞行程序草案失败：{exc}")
            return
        self._selected_event_id = ""
        self._sync_manual_launch_field_from_state()
        self._update_launch_source_controls()
        self._refresh_reference_segments()
        self._refresh_all()
        self._autosave_program()
        self._set_status("statusReady", "已生成可编辑飞行程序草案。")

    def save_program(self) -> None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return
        default_path = self._workspace.data_dir() / "flight_program.xlsx"
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出飞行程序 Excel",
            str(default_path),
            "Excel 文件 (*.xlsx);;所有文件 (*)",
        )
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() != ".xlsx":
            path = path.with_suffix(".xlsx")
        try:
            self._autosave_program()
            self._export_program_xlsx(path)
        except Exception as exc:
            self._set_status("statusDisconnected", f"导出飞行程序 Excel 失败：{exc}")
            return
        self._set_status("statusReady", f"已导出飞行程序 Excel：{path}")

    def _export_program_xlsx(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        sheets = [
            ("飞行程序概览", self._program_summary_xlsx_rows()),
            ("参考时段", self._reference_xlsx_rows()),
            ("姿态设置", self._event_xlsx_rows(attitude=True)),
            ("主要事件", self._event_xlsx_rows(attitude=False)),
        ]
        _write_flight_program_xlsx(path, sheets)
        return path

    def _program_summary_xlsx_rows(self) -> list[list[Any]]:
        return [
            ["字段", "值"],
            ["发射选择模式", self._launch_selection_mode()],
            ["选中发射时刻 UTC", str(self._program.get("selected_launch_utc", ""))],
            ["选中 T0 UTC", str(self._program.get("selected_t0_utc", ""))],
            ["选中轨道点", str(self._program.get("selected_orbit_point", ""))],
            ["事件数量", len(self._program.get("events", []))],
            ["参考时段数量", len(self._visible_reference_segments())],
        ]

    def _reference_xlsx_rows(self) -> list[list[Any]]:
        rows: list[list[Any]] = [list(self._REFERENCE_COLUMNS)]
        references = sorted(
            self._visible_reference_segments(),
            key=lambda item: (float(item.get("start_min", 0.0)), str(item.get("kind", "")), str(item.get("name", ""))),
        )
        for row_index, segment in enumerate(references, start=1):
            start_min = float(segment.get("start_min", 0.0))
            end_min = float(segment.get("end_min", start_min))
            rows.append(
                [
                    row_index,
                    self._reference_kind_label(str(segment.get("kind", ""))),
                    str(segment.get("name", segment.get("label", ""))),
                    round(start_min, 6),
                    round(end_min, 6),
                    round(max(0.0, end_min - start_min), 6),
                ]
            )
        return rows

    def _event_xlsx_rows(self, *, attitude: bool) -> list[list[Any]]:
        columns = self._ATTITUDE_COLUMNS if attitude else self._MAJOR_EVENT_COLUMNS
        rows: list[list[Any]] = [list(columns)]
        events = sorted(
            [
                dict(item)
                for item in self._program.get("events", [])
                if (item.get("kind") == ATTITUDE_KIND) is attitude
            ],
            key=lambda item: (float(item.get("start_min", 0.0)), float(item.get("end_min", 0.0)), str(item.get("name", ""))),
        )
        for row_index, event in enumerate(events, start=1):
            start_min = float(event.get("start_min", 0.0))
            end_min = float(event.get("end_min", start_min))
            instant = bool(event.get("instant"))
            duration = 0.0 if instant else max(0.0, end_min - start_min)
            locked = "是" if bool(event.get("locked")) else "否"
            if attitude:
                rows.append(
                    [
                        row_index,
                        locked,
                        str(event.get("mode", "")),
                        str(event.get("name", "")),
                        round(start_min, 6),
                        round(end_min, 6),
                        round(duration, 6),
                    ]
                )
            else:
                rows.append(
                    [
                        row_index,
                        locked,
                        str(event.get("name", "")),
                        round(start_min, 6),
                        round(end_min, 6),
                        round(duration, 6),
                        "是" if instant else "否",
                    ]
                )
        return rows

    def _autosave_program(self) -> None:
        if self._suppress_autosave or self._workspace.current_project is None:
            return
        try:
            self._workspace.save_flight_program_config(self._program)
        except Exception as exc:
            self._set_status("statusDisconnected", f"自动保存飞行程序失败：{exc}")

    def _sync_stk_analysis_time_if_available(self) -> bool:
        if self._suppress_autosave or self._workspace.current_project is None:
            return False
        try:
            return bool(self._stk_link_service_factory().sync_current_scenario_analysis_time())
        except Exception:
            return False

    def _schedule_stk_playhead_sync(self) -> None:
        self._pending_stk_playhead_sync = True
        self._stk_playhead_sync_timer.start()

    def _flush_stk_playhead_sync(self) -> bool:
        if self._stk_playhead_sync_timer.isActive():
            self._stk_playhead_sync_timer.stop()
        self._pending_stk_playhead_sync = False
        return self._sync_stk_current_time_if_available()

    def _sync_stk_current_time_if_available(self) -> bool:
        if self._suppress_autosave or self._workspace.current_project is None:
            return False
        t0_value = str(self._program.get("selected_t0_utc", "") or "")
        if not t0_value:
            return False
        try:
            current_utc = parse_utc(t0_value) + timedelta(minutes=float(self._playhead_min))
            return bool(self._stk_link_service_factory().sync_current_scenario_time(format_utc(current_utc)))
        except Exception:
            return False

    def _save_reference_results(self) -> Path | None:
        if self._suppress_autosave or self._workspace.current_project is None:
            return None
        payload = {
            "version": 1,
            "launch_selection_mode": self._launch_selection_mode(),
            "selected_orbit_point": str(self._program.get("selected_orbit_point", "")),
            "selected_launch_utc": str(self._program.get("selected_launch_utc", "")),
            "selected_t0_utc": str(self._program.get("selected_t0_utc", "")),
            "results": [asdict(result) for result in self._tracking_results.values()],
        }
        try:
            return self._workspace.save_flight_program_reference_results(payload)
        except Exception as exc:
            self._set_status("statusDisconnected", f"自动保存参考轨失败：{exc}")
            return None

    def _load_saved_reference_results(self) -> bool:
        self._tracking_results = {}
        if self._workspace.current_project is None:
            return False
        try:
            payload = self._workspace.load_flight_program_reference_results()
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            return False
        results = [self._tracking_result_from_payload(item) for item in raw_results if isinstance(item, dict)]
        if not results:
            return False
        self._tracking_results = {result.point_key: result for result in results}
        return True

    def _refresh_all(self) -> None:
        self._refresh_reference_segments()
        self._refresh_timeline()
        self._refresh_warnings()
        self._refresh_sample_preview()

    def _refresh_timeline(self, *, rebuild_tables: bool = True) -> None:
        duration = self._timeline_duration()
        references = self._visible_reference_segments()
        self._overview.set_data(
            events=list(self._program.get("events", [])),
            reference_segments=references,
            duration_min=duration,
            playhead_min=self._playhead_min,
            selected_event_id=self._selected_event_id,
            selected_reference_id=self._selected_reference_id,
        )
        if rebuild_tables:
            self._refresh_reference_table()
            self._refresh_event_table()
        else:
            self._update_reference_table_statuses()
            self._update_event_table_statuses()
        self._time_label.setText(
            f"当前播放头：T0+{self._playhead_min:.2f} min；"
            f"事件 {len(self._program.get('events', []))} 条；"
            f"参考段 {len(references)} 条。"
        )

    def _refresh_reference_table(self) -> None:
        self._suppress_reference_table = True
        self._reference_table.setRowCount(0)
        references = sorted(
            self._visible_reference_segments(),
            key=lambda item: (float(item.get("start_min", 0.0)), str(item.get("kind", "")), str(item.get("name", ""))),
        )
        for row_index, segment in enumerate(references):
            self._reference_table.insertRow(row_index)
            self._set_reference_table_row(row_index, segment)
        self._reference_table.blockSignals(True)
        if self._selected_reference_id:
            for row in range(self._reference_table.rowCount()):
                item = self._reference_table.item(row, 0)
                if item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole) == self._selected_reference_id:
                    self._reference_table.setCurrentCell(row, 0)
                    self._reference_table.selectRow(row)
                    break
        self._reference_table.blockSignals(False)
        self._suppress_reference_table = False

    def _update_reference_table_statuses(self) -> None:
        return

    def _set_reference_table_row(self, row: int, segment: dict[str, Any]) -> None:
        reference_id = str(segment.get("id", ""))
        start_min = float(segment.get("start_min", 0.0))
        end_min = float(segment.get("end_min", start_min))
        values = [
            str(row + 1),
            self._reference_kind_label(str(segment.get("kind", ""))),
            str(segment.get("name", segment.get("label", ""))),
            f"{start_min:.3f}",
            f"{end_min:.3f}",
            f"{max(0.0, end_min - start_min):.3f}",
        ]
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, reference_id)
            item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self._reference_table.setItem(row, column, item)

    def _refresh_event_table(self) -> None:
        self._suppress_table = True
        self._event_table.setRowCount(0)
        self._major_event_table.setRowCount(0)
        events = sorted(
            [dict(item) for item in self._program.get("events", [])],
            key=lambda item: (float(item.get("start_min", 0.0)), float(item.get("end_min", 0.0)), str(item.get("name", ""))),
        )
        attitude_events = [event for event in events if event.get("kind") == ATTITUDE_KIND]
        major_events = [event for event in events if event.get("kind") != ATTITUDE_KIND]
        for row_index, event in enumerate(attitude_events):
            self._event_table.insertRow(row_index)
            self._set_event_table_row(self._event_table, row_index, event)
        for row_index, event in enumerate(major_events):
            self._major_event_table.insertRow(row_index)
            self._set_event_table_row(self._major_event_table, row_index, event)
        self._event_table.blockSignals(True)
        self._major_event_table.blockSignals(True)
        if self._selected_event_id:
            self._select_event_row_in_table(self._event_table, self._selected_event_id)
            self._select_event_row_in_table(self._major_event_table, self._selected_event_id)
        self._event_table.blockSignals(False)
        self._major_event_table.blockSignals(False)
        self._suppress_table = False

    def _update_event_table_statuses(self) -> None:
        return

    def _select_event_row_in_table(self, table: QtWidgets.QTableWidget, event_id: str) -> bool:
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole) == event_id:
                table.setCurrentCell(row, 0)
                table.selectRow(row)
                return True
        return False

    def _set_event_table_row(self, table: QtWidgets.QTableWidget, row: int, event: dict[str, Any]) -> None:
        event_id = str(event.get("id", ""))
        start_min = float(event.get("start_min", 0.0))
        end_min = float(event.get("end_min", start_min))
        instant = bool(event.get("instant"))
        duration = 0.0 if instant else max(0.0, end_min - start_min)
        locked = bool(event.get("locked"))
        table_kind = str(table.property("tableKind") or "")
        if table_kind == "major":
            values = [
                str(row + 1),
                "是" if locked else "否",
                str(event.get("name", "")),
                f"{start_min:.3f}",
                f"{end_min:.3f}",
                f"{duration:.3f}",
                "是" if instant else "否",
            ]
            flag_columns = {"locked": 1, "instant": 6}
        else:
            values = [
                str(row + 1),
                "是" if locked else "否",
                str(event.get("mode", "")),
                str(event.get("name", "")),
                f"{start_min:.3f}",
                f"{end_min:.3f}",
                f"{duration:.3f}",
            ]
            flag_columns = {"locked": 1}
        editable_columns = {int(value) for value in (table.property("editableColumns") or [])}
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, event_id)
            flags = QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled
            if column in editable_columns and not locked:
                flags |= QtCore.Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            table.setItem(row, column, item)
        for field, column in flag_columns.items():
            self._set_event_flag_button(table, row, column, event_id, field, bool(event.get(field)), locked)

    def _set_event_flag_button(
        self,
        table: QtWidgets.QTableWidget,
        row: int,
        column: int,
        event_id: str,
        field: str,
        checked: bool,
        locked: bool,
    ) -> None:
        button = QtWidgets.QPushButton("是" if checked else "否")
        button.setCheckable(True)
        button.setChecked(checked)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        button.setFixedHeight(28)
        button.setProperty("variant", "secondary")
        button.setEnabled(field == "locked" or not locked)
        if not button.isEnabled():
            button.setToolTip("事件已锁定，解锁后才能修改。")
        button.clicked.connect(
            lambda _checked=False, tbl=table, event=event_id, key=field: self._toggle_event_flag_from_button(tbl, event, key)
        )
        self._refresh_event_flag_button_style(button, checked)
        table.setCellWidget(row, column, button)

    @staticmethod
    def _refresh_event_flag_button_style(button: QtWidgets.QPushButton, checked: bool) -> None:
        button.setText("是" if checked else "否")
        if checked:
            button.setStyleSheet(
                """
                QPushButton {
                    background: #1f6c7a;
                    color: #7ff1ff;
                    border: 1px solid #66d9ea;
                    border-radius: 8px;
                    padding: 4px 10px;
                    font-weight: 700;
                }
                QPushButton:hover {
                    background: #248799;
                }
                """
            )
            return
        button.setStyleSheet(
            """
            QPushButton {
                background: #132733;
                color: #cde3ea;
                border: 1px solid #244958;
                border-radius: 8px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #173343;
                border: 1px solid #347084;
            }
            """
        )

    def _toggle_event_flag_from_button(self, table: QtWidgets.QTableWidget, event_id: str, field: str) -> None:
        event = self._event_by_id(event_id)
        if event is None:
            return
        if bool(event.get("locked")) and field != "locked":
            self._set_status("statusDisconnected", "事件已锁定，请先解锁。")
            return
        updated = dict(event)
        updated[field] = not bool(event.get(field))
        if hasattr(table, "selectRow"):
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole) == event_id:
                    table.selectRow(row)
                    break
        self._selected_event_id = event_id
        self._selected_reference_id = ""
        self._upsert_event(updated)

    def _selected_event_locked(self) -> bool:
        event = self._selected_event()
        return bool(event.get("locked")) if event is not None else False

    def _event_status(self, event: dict[str, Any]) -> str:
        try:
            start_min = float(event.get("start_min", 0.0))
            end_min = float(event.get("end_min", start_min))
        except (TypeError, ValueError):
            return "时间错误"
        if bool(event.get("instant")):
            return "里程碑"
        if end_min < start_min:
            return "时间错误"
        active = start_min <= self._playhead_min <= end_min
        return "当前" if active else "正常"

    def _reference_status(self, segment: dict[str, Any]) -> str:
        start_min = float(segment.get("start_min", 0.0))
        end_min = float(segment.get("end_min", start_min))
        return "当前" if start_min <= self._playhead_min <= end_min else "正常"

    @staticmethod
    def _reference_kind_label(kind: str) -> str:
        return {
            "burn": "点火",
            "shadow": "地影",
            "ground": "地面站可见",
            "relay": "中继星可见",
        }.get(kind, kind)

    @staticmethod
    def _reference_action_label(kind: str) -> str:
        return {
            "burn": "生成 AFM",
            "shadow": "标记避让",
            "ground": "生成 EPM",
            "relay": "生成 EPM",
        }.get(kind, "引用")

    def _visible_reference_segments(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self._reference_segments
            if self._layer_checks.get(str(item.get("kind")), None) is None
            or self._layer_checks[str(item.get("kind"))].isChecked()
        ]

    def _refresh_reference_segments(self) -> None:
        result = self._selected_tracking_result()
        self._reference_segments = []
        if result is None:
            return
        t0 = parse_utc(result.t0_utc)
        for segment in result.segments:
            if segment.kind not in {"burn", "shadow", "ground", "relay"}:
                continue
            start = (parse_utc(segment.start_utc) - t0).total_seconds() / 60.0
            end = (parse_utc(segment.end_utc) - t0).total_seconds() / 60.0
            self._reference_segments.append(
                {
                    "id": f"ref-{len(self._reference_segments) + 1:04d}",
                    "kind": segment.kind,
                    "label": segment.row_label,
                    "name": segment.row_label,
                    "start_min": start,
                    "end_min": end,
                    "source": "tracking_arc",
                    "tooltip": segment.tooltip,
                }
            )

    def _refresh_warnings(self) -> None:
        self._warnings_list.clear()
        try:
            strategy = self._workspace.load_maneuver_strategy() if self._workspace.current_project is not None else None
        except Exception:
            strategy = None
        tracking = self._selected_tracking_result()
        warnings = validate_flight_program(
            self._program,
            maneuver_strategy=strategy,
            reference_segments=[] if tracking is None else tracking.segments,
        )
        if not warnings:
            self._warnings_list.addItem("未发现冲突。")
            return
        for warning in warnings:
            self._warnings_list.addItem(f"[{warning.severity}] {warning.message}")

    def _refresh_sample_preview(self) -> None:
        sample: FlightProgramSample | None = None
        if self._workspace.current_project is not None and self._orbit_history_path().exists():
            try:
                strategy = self._workspace.load_maneuver_strategy() or {}
                context = self._sampling_context(strategy)
                sample = sample_flight_program_state(
                    orbit_history_csv=self._orbit_history_path(),
                    maneuver_strategy=strategy,
                    payload=self._program,
                    elapsed_min=self._playhead_min,
                    t0_utc=self._program.get("selected_t0_utc") or None,
                    context=context,
                )
            except Exception:
                sample = None
        if sample is None:
            self._scene_view.clear_trajectory()
            self._scene_view.set_info_overlay("状态：暂无轨道历史或采样失败")
            return
        self._update_orbit_view_for_sample(sample)

    def _update_orbit_view_for_sample(self, sample: FlightProgramSample) -> None:
        trajectory = self._orbit_trajectory_for_sample(sample)
        if trajectory is None:
            self._scene_view.clear_trajectory()
            self._scene_view.set_info_overlays(self._sample_overlay_sections(sample))
            return
        try:
            rows = self._orbit_history_rows() or []
            earth_rotation_rad = self._earth_rotation_rad_for_sample(sample.elapsed_min)
            self._scene_view.set_trajectory_overlays(
                trajectory,
                EARTH_RADIUS_KM,
                maneuver_segments_km=self._maneuver_segments_km(rows, trajectory.positions_km),
                start_label="起点",
                earth_rotation_rad=earth_rotation_rad,
                subsatellite_position_km=self._subsatellite_position_km_for_sample(sample),
            )
            self._scene_view.set_direction_vectors(
                trajectory.current_position_km,
                self._direction_vectors_for_sample(sample, trajectory.current_position_km),
            )
            self._scene_view.set_info_overlays(self._sample_overlay_sections(sample))
        except Exception:
            self._scene_view.clear_trajectory()
            self._scene_view.set_info_overlays(self._sample_overlay_sections(sample))

    def _sample_overlay_sections(self, sample: FlightProgramSample) -> dict[str, str]:
        attitude = sample.mode if not sample.event_name else f"{sample.mode} / {sample.event_name}"
        return {
            "top_left": f"主要测控事件：{self._major_tracking_event_text()}",
            "top_right": f"卫星姿态：{attitude}",
            "bottom_left": f"当前时间（北京）：{self._sample_time_beijing_text(sample)}",
            "bottom_right": (
                "星下点："
                f"经度 {sample.subsatellite_longitude_deg:.3f}° / 纬度 {sample.subsatellite_latitude_deg:.3f}°"
            ),
        }

    def _sample_time_beijing_text(self, sample: FlightProgramSample) -> str:
        t0_value = str(self._program.get("selected_t0_utc", "") or "")
        if not t0_value:
            tracking = self._selected_tracking_result()
            t0_value = "" if tracking is None else tracking.t0_utc
        if not t0_value:
            return "--"
        try:
            epoch = parse_utc(t0_value) + timedelta(minutes=float(sample.elapsed_min))
        except Exception:
            return "--"
        return epoch.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

    def _major_tracking_event_text(self) -> str:
        candidates = [
            segment
            for segment in self._visible_reference_segments()
            if str(segment.get("kind", "")) in {"ground", "relay"}
        ]
        current = [
            segment
            for segment in candidates
            if float(segment.get("start_min", 0.0)) <= self._playhead_min <= float(segment.get("end_min", 0.0))
        ]
        if current:
            current.sort(key=lambda item: (float(item.get("end_min", 0.0)) - float(item.get("start_min", 0.0)), str(item.get("name", ""))))
            return self._format_tracking_event_label("当前", current[0])
        upcoming = [segment for segment in candidates if float(segment.get("start_min", 0.0)) > self._playhead_min]
        if upcoming:
            upcoming.sort(key=lambda item: float(item.get("start_min", 0.0)))
            return self._format_tracking_event_label("下一", upcoming[0])
        return "--"

    def _format_tracking_event_label(self, prefix: str, segment: dict[str, Any]) -> str:
        start_min = float(segment.get("start_min", 0.0))
        end_min = float(segment.get("end_min", start_min))
        kind = self._reference_kind_label(str(segment.get("kind", "")))
        name = str(segment.get("name", "") or segment.get("label", "") or kind)
        return f"{prefix} {kind}：{name}（T0+{start_min:.1f} 至 {end_min:.1f} min）"

    def _on_table_selection_changed(self) -> None:
        if self._suppress_table:
            return
        table = self.sender()
        if not isinstance(table, QtWidgets.QTableWidget):
            return
        items = table.selectedItems()
        if not items:
            return
        event_id = str(items[0].data(QtCore.Qt.ItemDataRole.UserRole) or "")
        if event_id:
            self._select_event(event_id)

    def _on_reference_selection_changed(self) -> None:
        if self._suppress_reference_table:
            return
        items = self._reference_table.selectedItems()
        if not items:
            return
        reference_id = str(items[0].data(QtCore.Qt.ItemDataRole.UserRole) or "")
        if reference_id:
            self._select_reference(reference_id)

    def _on_reference_item_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        reference = self._reference_by_id(str(item.data(QtCore.Qt.ItemDataRole.UserRole) or ""))
        if reference is None:
            return
        self._set_playhead(float(reference.get("start_min", self._playhead_min)), sync_immediately=True)

    def _on_table_item_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        event_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        event = self._event_by_id(event_id)
        if event is None:
            return
        self._set_playhead(float(event.get("start_min", self._playhead_min)), sync_immediately=True)

    def _on_table_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._suppress_table:
            return
        event_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        event = self._event_by_id(event_id)
        if event is None:
            return
        if bool(event.get("locked")):
            self._refresh_timeline()
            self._set_status("statusDisconnected", "事件已锁定，请先解锁。")
            return
        updated = dict(event)
        table = item.tableWidget()
        table_kind = str(table.property("tableKind") or "") if table is not None else ""
        column = item.column()
        text = item.text().strip()
        try:
            if table_kind == "attitude" and column == 2:
                updated["mode"] = text or str(event.get("mode", MODE_SPM))
            elif table_kind == "attitude" and column == 3:
                updated["name"] = text or str(event.get("name", "事件"))
            elif table_kind == "attitude" and column == 4:
                new_start_min = float(text)
                if not self._apply_attitude_start_change(str(event.get("id", "")), new_start_min):
                    return
                updated["start_min"] = new_start_min
            elif table_kind == "attitude" and column == 5:
                new_end_min = float(text)
                if not self._apply_attitude_end_change(str(event.get("id", "")), new_end_min):
                    return
                updated["end_min"] = new_end_min
            elif table_kind == "major" and column == 2:
                updated["name"] = text or str(event.get("name", "事件"))
            elif table_kind == "major" and column == 3:
                updated["start_min"] = float(text)
            elif table_kind == "major" and column == 4:
                updated["end_min"] = float(text)
        except ValueError:
            self._refresh_timeline()
            self._set_status("statusDisconnected", "表格输入无效：时间列需要数字。")
            return
        self._upsert_event(updated)

    def _apply_attitude_start_change(self, event_id: str, new_start_min: float) -> bool:
        attitudes = sorted(
            [
                dict(item)
                for item in self._program.get("events", [])
                if str(item.get("kind", "")) == ATTITUDE_KIND and not bool(item.get("instant"))
            ],
            key=lambda item: (float(item.get("start_min", 0.0)), float(item.get("end_min", 0.0)), str(item.get("name", ""))),
        )
        current_index = next((index for index, item in enumerate(attitudes) if str(item.get("id", "")) == event_id), -1)
        if current_index <= 0:
            return True
        previous = attitudes[current_index - 1]
        previous_start_min = float(previous.get("start_min", 0.0))
        if new_start_min < previous_start_min:
            self._refresh_timeline()
            self._set_status(
                "statusDisconnected",
                "姿态段冲突：开始时间不能早于前一个姿态段的开始时间。",
            )
            return False
        if bool(previous.get("locked")):
            self._refresh_timeline()
            self._set_status(
                "statusDisconnected",
                "姿态段冲突：前一个姿态段已锁定，无法自动调整其结束时间。",
            )
            return False
        previous_id = str(previous.get("id", ""))
        updated_events: list[dict[str, Any]] = []
        for item in self._program.get("events", []):
            if str(item.get("id", "")) != previous_id:
                updated_events.append(dict(item))
                continue
            adjusted = dict(item)
            adjusted["end_min"] = float(new_start_min)
            updated_events.append(normalize_flight_event(adjusted))
        self._program["events"] = updated_events
        return True

    def _apply_attitude_end_change(self, event_id: str, new_end_min: float) -> bool:
        attitudes = sorted(
            [
                dict(item)
                for item in self._program.get("events", [])
                if str(item.get("kind", "")) == ATTITUDE_KIND and not bool(item.get("instant"))
            ],
            key=lambda item: (float(item.get("start_min", 0.0)), float(item.get("end_min", 0.0)), str(item.get("name", ""))),
        )
        current_index = next((index for index, item in enumerate(attitudes) if str(item.get("id", "")) == event_id), -1)
        if current_index < 0 or current_index >= len(attitudes) - 1:
            return True
        following = attitudes[current_index + 1]
        following_end_min = float(following.get("end_min", 0.0))
        if new_end_min > following_end_min:
            self._refresh_timeline()
            self._set_status(
                "statusDisconnected",
                "姿态段冲突：结束时间不能晚于后一个姿态段的结束时间。",
            )
            return False
        if bool(following.get("locked")):
            self._refresh_timeline()
            self._set_status(
                "statusDisconnected",
                "姿态段冲突：后一个姿态段已锁定，无法自动调整其开始时间。",
            )
            return False
        following_id = str(following.get("id", ""))
        updated_events: list[dict[str, Any]] = []
        for item in self._program.get("events", []):
            if str(item.get("id", "")) != following_id:
                updated_events.append(dict(item))
                continue
            adjusted = dict(item)
            adjusted["start_min"] = float(new_end_min)
            updated_events.append(normalize_flight_event(adjusted))
        self._program["events"] = updated_events
        return True

    def _show_table_context_menu(self, position: QtCore.QPoint) -> None:
        table = self.sender()
        if not isinstance(table, QtWidgets.QTableWidget):
            table = self._event_table
        table_kind = str(table.property("tableKind") or "")
        item = table.itemAt(position)
        if item is not None:
            event_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
            if event_id:
                self._select_event(event_id)
        menu = QtWidgets.QMenu(self)
        add_spm = add_epm = add_afm = add_transition = add_deploy = duplicate = delete = jump = None
        if table_kind == "attitude":
            add_spm = menu.addAction("新增 SPM 姿态")
            add_epm = menu.addAction("新增 EPM 姿态")
            add_afm = menu.addAction("新增 AFM 姿态")
            add_transition = menu.addAction("新增过渡段")
            menu.addSeparator()
            jump = menu.addAction("跳转到当前姿态段")
            delete = menu.addAction("删除当前姿态")
            jump.setEnabled(bool(self._selected_event_id))
            delete.setEnabled(bool(self._selected_event_id) and not self._selected_event_locked())
        else:
            duplicate = menu.addAction("复制选中事件")
            delete = menu.addAction("删除选中事件")
            duplicate.setEnabled(bool(self._selected_event_id) and not self._selected_event_locked())
            delete.setEnabled(bool(self._selected_event_id) and not self._selected_event_locked())
        chosen = menu.exec(table.viewport().mapToGlobal(position))
        if chosen == add_spm:
            self._add_event(MODE_SPM, self._playhead_min)
        elif chosen == add_epm:
            self._add_event(MODE_EPM, self._playhead_min)
        elif chosen == add_afm:
            self._add_event(MODE_AFM, self._playhead_min)
        elif chosen == add_transition:
            self._add_event(MODE_TRANSITION, self._playhead_min)
        elif chosen == add_deploy:
            self._add_event("deployment", self._playhead_min)
        elif chosen == jump:
            self._jump_to_selected_event()
        elif chosen == duplicate:
            self._duplicate_selected_event()
        elif chosen == delete:
            self._delete_selected_event()

    def _show_reference_context_menu(self, position: QtCore.QPoint) -> None:
        item = self._reference_table.itemAt(position)
        if item is not None:
            reference_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
            if reference_id:
                self._select_reference(reference_id)
        reference = self._selected_reference()
        menu = QtWidgets.QMenu(self)
        create_event = menu.addAction("从参考段生成程序事件")
        jump = menu.addAction("播放头跳到开始")
        create_event.setEnabled(reference is not None)
        jump.setEnabled(reference is not None)
        chosen = menu.exec(self._reference_table.viewport().mapToGlobal(position))
        if reference is None:
            return
        if chosen == create_event:
            self._create_event_from_reference(reference)
        elif chosen == jump:
            self._set_playhead(float(reference.get("start_min", self._playhead_min)), sync_immediately=True)

    def _create_event_from_reference(self, reference: dict[str, Any]) -> None:
        kind = str(reference.get("kind", ""))
        start_min = float(reference.get("start_min", self._playhead_min))
        end_min = float(reference.get("end_min", start_min))
        mode = MODE_EPM
        name = f"{self._reference_kind_label(kind)}程序"
        if kind == "burn":
            mode = MODE_AFM
            name = "点火姿态 AFM"
        elif kind == "shadow":
            mode = MODE_SPM
            name = "地影避让标记"
        event = normalize_flight_event(
            {
                "id": f"fp-{uuid4().hex[:10]}",
                "name": name,
                "kind": ATTITUDE_KIND,
                "mode": mode,
                "start_min": start_min,
                "end_min": end_min,
                "instant": False,
                "source": "reference",
                "locked": False,
                "notes": f"由参考段生成：{reference.get('name', '')}",
                "properties": {"reference_id": str(reference.get("id", "")), "reference_kind": kind},
            }
        )
        self._program["events"] = [*self._program.get("events", []), event]
        self._selected_event_id = str(event["id"])
        self._table_tabs.setCurrentWidget(self._event_table)
        self._refresh_all()
        self._autosave_program()

    def _upsert_event(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        normalized = normalize_flight_event(event)
        events = list(self._program.get("events", []))
        for index, item in enumerate(events):
            if str(item.get("id")) == str(normalized["id"]):
                events[index] = normalized
                break
        else:
            events.append(normalized)
        self._program["events"] = events
        self._selected_event_id = str(normalized["id"])
        self._refresh_timeline()
        self._refresh_warnings()
        self._refresh_sample_preview()
        self._autosave_program()

    def _add_event(self, mode_or_kind: str, elapsed_min: float) -> None:
        is_deployment = mode_or_kind == "deployment"
        event = normalize_flight_event(
            {
                "id": f"fp-{uuid4().hex[:10]}",
                "name": "主要事件" if is_deployment else f"{mode_or_kind} 姿态",
                "kind": DEPLOYMENT_KIND if is_deployment else ATTITUDE_KIND,
                "mode": "SolarArrayDeploy" if is_deployment else mode_or_kind,
                "start_min": elapsed_min,
                "end_min": elapsed_min + (10.0 if is_deployment else 20.0),
                "instant": False,
                "source": "manual",
                "locked": False,
                "notes": "",
                "properties": {},
            }
        )
        self._program["events"] = [*self._program.get("events", []), event]
        self._selected_event_id = str(event["id"])
        self._refresh_all()
        self._autosave_program()

    def _duplicate_selected_event(self) -> None:
        event = self._selected_event()
        if event is None:
            return
        if bool(event.get("locked")):
            self._set_status("statusDisconnected", "事件已锁定，请先解锁。")
            return
        copy = dict(event)
        copy["id"] = f"fp-{uuid4().hex[:10]}"
        copy["name"] = f"{event['name']} 副本"
        copy["start_min"] = float(event["start_min"]) + 5.0
        copy["end_min"] = float(event["end_min"]) + 5.0
        copy["source"] = "manual"
        self._program["events"] = [*self._program.get("events", []), normalize_flight_event(copy)]
        self._selected_event_id = str(copy["id"])
        self._refresh_all()
        self._autosave_program()

    def _jump_to_selected_event(self) -> None:
        event = self._selected_event()
        if event is None:
            return
        self._set_playhead(float(event.get("start_min", self._playhead_min)), sync_immediately=True)

    def _jump_to_table_current_row(self, table: QtWidgets.QTableWidget) -> bool:
        row = table.currentRow()
        if row < 0:
            return False
        item = table.item(row, 0)
        if item is None:
            return False
        item_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        if table is self._reference_table:
            reference = self._reference_by_id(item_id)
            if reference is None:
                return False
            self._select_reference(item_id)
            self._set_playhead(float(reference.get("start_min", self._playhead_min)), sync_immediately=True)
            return True
        event = self._event_by_id(item_id)
        if event is None:
            return False
        self._select_event(item_id)
        self._set_playhead(float(event.get("start_min", self._playhead_min)), sync_immediately=True)
        return True

    def _delete_selected_event(self) -> None:
        if not self._selected_event_id:
            return
        if self._selected_event_locked():
            self._set_status("statusDisconnected", "事件已锁定，请先解锁。")
            return
        self._program["events"] = [
            item for item in self._program.get("events", []) if str(item.get("id")) != self._selected_event_id
        ]
        self._selected_event_id = ""
        self._refresh_all()
        self._autosave_program()

    def _select_event(self, event_id: str) -> None:
        self._selected_event_id = event_id
        self._selected_reference_id = ""
        event = self._selected_event()
        if hasattr(self, "_table_tabs"):
            self._table_tabs.setCurrentWidget(
                self._major_event_table if event is not None and event.get("kind") != ATTITUDE_KIND else self._event_table
            )
        self._refresh_timeline()

    def _select_reference(self, reference_id: str) -> None:
        self._selected_reference_id = reference_id
        self._selected_event_id = ""
        reference = self._selected_reference()
        if reference is not None:
            self._set_playhead(float(reference.get("start_min", self._playhead_min)), sync_immediately=True)
        if hasattr(self, "_table_tabs"):
            self._table_tabs.setCurrentWidget(self._reference_table)
        self._refresh_timeline()
        self._refresh_sample_preview()

    def _set_playhead(self, elapsed_min: float, *, sync_immediately: bool = True) -> None:
        self._playhead_min = min(max(0.0, float(elapsed_min)), self._timeline_duration())
        self._refresh_timeline(rebuild_tables=False)
        self._refresh_sample_preview()
        if sync_immediately:
            self._flush_stk_playhead_sync()
        else:
            self._schedule_stk_playhead_sync()

    def _on_slider_changed(self, value: int) -> None:
        self._set_playhead((float(value) / 10000.0) * self._timeline_duration(), sync_immediately=False)

    def _on_launch_source_changed(self) -> None:
        self._program["launch_selection_mode"] = self._launch_selection_mode()
        if self._launch_selection_mode() == "manual":
            self._sync_manual_launch_field_from_state()
        else:
            suggested_launch = self._suggested_manual_launch_utc()
            if suggested_launch:
                self._program["selected_launch_utc"] = suggested_launch
        self._tracking_results = {}
        self._sync_selected_t0_from_launch_state()
        self._refresh_reference_segments()
        self._update_launch_source_controls()
        self._refresh_all()
        self._autosave_program()
        self._save_reference_results()
        self._sync_stk_analysis_time_if_available()

    def _on_manual_launch_changed(self) -> None:
        if self._launch_selection_mode() != "manual":
            return
        self._program["selected_launch_utc"] = self._manual_launch_utc()
        self._tracking_results = {}
        self._sync_selected_t0_from_launch_state()
        self._refresh_reference_segments()
        self._refresh_all()
        self._autosave_program()
        self._save_reference_results()
        self._sync_stk_analysis_time_if_available()

    def _on_window_changed(self) -> None:
        if self._launch_selection_mode() == "manual":
            return
        self._tracking_results = {}
        suggested_launch = self._suggested_manual_launch_utc()
        if suggested_launch:
            self._program["selected_launch_utc"] = suggested_launch
            self._sync_manual_launch_field_from_state()
        self._sync_selected_t0_from_launch_state()
        self._refresh_reference_segments()
        self._refresh_all()
        self._autosave_program()
        self._save_reference_results()
        self._sync_stk_analysis_time_if_available()

    def _on_orbit_point_changed(self) -> None:
        if self._launch_selection_mode() == "manual":
            return
        key = str(self._orbit_point_combo.currentData() or "leading")
        self._program["selected_orbit_point"] = key
        selected = self._selected_tracking_result()
        if selected is not None:
            self._program["selected_t0_utc"] = selected.t0_utc
            self._program["selected_launch_utc"] = selected.launch_utc
        else:
            suggested_launch = self._suggested_manual_launch_utc()
            if suggested_launch:
                self._program["selected_launch_utc"] = suggested_launch
                self._sync_manual_launch_field_from_state()
            self._sync_selected_t0_from_launch_state()
        self._refresh_all()
        self._autosave_program()
        self._sync_stk_analysis_time_if_available()

    def _selected_event(self) -> dict[str, Any] | None:
        return next((item for item in self._program.get("events", []) if str(item.get("id")) == self._selected_event_id), None)

    def _event_by_id(self, event_id: str) -> dict[str, Any] | None:
        return next((item for item in self._program.get("events", []) if str(item.get("id")) == event_id), None)

    def _selected_reference(self) -> dict[str, Any] | None:
        return self._reference_by_id(self._selected_reference_id)

    def _reference_by_id(self, reference_id: str) -> dict[str, Any] | None:
        return next((item for item in self._reference_segments if str(item.get("id")) == reference_id), None)

    def _selected_window(self) -> Any | None:
        if not self._windows:
            return None
        try:
            index = int(self._window_combo.currentData())
        except (TypeError, ValueError):
            index = self._window_combo.currentIndex()
        if 0 <= index < len(self._windows):
            return self._windows[index]
        return None

    def _selected_tracking_result(self) -> TrackingArcOrbitResult | None:
        if self._launch_selection_mode() == "manual":
            return self._tracking_results.get("manual")
        key = str(self._orbit_point_combo.currentData() or self._program.get("selected_orbit_point", "leading"))
        return self._tracking_results.get(key)

    def _launch_selection_mode(self) -> str:
        mode = str(self._launch_source_combo.currentData() or self._program.get("launch_selection_mode", "window") or "window")
        return mode if mode in {"window", "manual"} else "window"

    def _can_calculate_reference_arcs(self) -> bool:
        return self._launch_selection_mode() == "manual" or self._selected_window() is not None

    def _update_launch_source_controls(self) -> None:
        manual_mode = self._launch_selection_mode() == "manual"
        self._window_label.setVisible(not manual_mode)
        self._window_combo.setVisible(not manual_mode)
        self._orbit_point_combo.setVisible(not manual_mode)
        self._manual_launch_label.setVisible(manual_mode)
        self._manual_launch_edit.setVisible(manual_mode)

    def _sync_manual_launch_field_from_state(self) -> None:
        launch_utc = str(self._program.get("selected_launch_utc", "") or self._suggested_manual_launch_utc() or "")
        if not launch_utc:
            return
        try:
            qdatetime = self._utc_to_qdatetime(launch_utc)
        except Exception:
            return
        self._manual_launch_edit.blockSignals(True)
        self._manual_launch_edit.setDateTime(qdatetime)
        self._manual_launch_edit.blockSignals(False)

    def _suggested_manual_launch_utc(self) -> str:
        window = self._selected_window()
        if window is None:
            return ""
        selected_key = str(self._orbit_point_combo.currentData() or self._program.get("selected_orbit_point", "leading") or "leading")
        for point_key, _point_label, launch_utc in tracking_arc_launch_points(window):
            if point_key == selected_key:
                return format_utc(launch_utc)
        return format_utc(tracking_arc_launch_points(window)[0][2]) if tracking_arc_launch_points(window) else ""

    def _manual_launch_utc(self) -> str:
        py_dt = self._manual_launch_edit.dateTime().toUTC().toPython()
        return format_utc(py_dt)

    def _sync_selected_t0_from_launch_state(self) -> None:
        selected = self._selected_tracking_result()
        if selected is not None:
            self._program["selected_launch_utc"] = selected.launch_utc
            self._program["selected_t0_utc"] = selected.t0_utc
            return
        launch_utc = str(self._program.get("selected_launch_utc", "") or "")
        if not launch_utc:
            self._program["selected_t0_utc"] = ""
            return
        try:
            launch_dt = parse_utc(launch_utc)
            t0_dt = launch_dt + timedelta(seconds=self._rocket_flight_time_s())
        except Exception:
            self._program["selected_t0_utc"] = ""
            return
        self._program["selected_launch_utc"] = format_utc(launch_dt)
        self._program["selected_t0_utc"] = format_utc(t0_dt)

    def _rocket_flight_time_s(self) -> float:
        payload = (
            self._workspace.load_tracking_arc_config()
            or self._workspace.load_launch_window_config()
            or default_launch_window_config()
        )
        try:
            return float(config_from_payload(payload).rocket_flight_time_s)
        except Exception:
            return float(config_from_payload(default_launch_window_config()).rocket_flight_time_s)

    def _reload_windows(self, *, show_status: bool) -> None:
        if self._workspace.current_project is None:
            self._set_windows([])
            return
        path = self._workspace.data_dir() / "launch_window_samples.csv"
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
        self._windows = list(windows)
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        for index, window in enumerate(self._windows):
            label = (
                f"{index + 1}. {self._format_beijing(window.window_start_utc)} - "
                f"{self._format_beijing(window.window_end_utc)} ({window.duration_min:.1f} min)"
            )
            self._window_combo.addItem(label, index)
        self._window_combo.blockSignals(False)
        if not str(self._program.get("selected_launch_utc", "") or ""):
            suggested_launch = self._suggested_manual_launch_utc()
            if suggested_launch:
                self._program["selected_launch_utc"] = suggested_launch
        self._sync_manual_launch_field_from_state()

    def _read_sample_csv(self, path: Path) -> list[dict[str, Any]]:
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
                    "ok": str(raw_row.get("ok", "")).strip().lower() in {"1", "true", "yes", "y", "通过", "pass", "passed"},
                    "failure": str(raw_row.get("failure", "")).strip(),
                }
                for column in numeric_columns:
                    raw_value = raw_row.get(column)
                    if column == "longest_shadow_min" and not str(raw_value or "").strip():
                        continue
                    sample[column] = float(raw_value or 0.0)
                constraint_results = str(raw_row.get("constraint_results", "")).strip()
                if constraint_results:
                    parsed_results = json.loads(constraint_results)
                    if isinstance(parsed_results, list):
                        sample["constraint_results"] = parsed_results
                if sample["launch_utc"]:
                    samples.append(sample)
        return samples

    def _timeline_duration(self) -> float:
        duration = 1.0
        for event in self._program.get("events", []):
            duration = max(duration, float(event.get("end_min", 0.0)))
        for segment in self._reference_segments:
            duration = max(duration, float(segment.get("end_min", 0.0)))
        try:
            rows = self._orbit_history_rows()
            if rows is not None:
                duration = max(duration, max(float(row.get("elapsed_time_min", 0.0)) for row in rows))
        except Exception:
            pass
        return max(60.0, duration)

    def _orbit_trajectory_for_sample(self, sample: FlightProgramSample) -> OrbitTrajectory | None:
        if self._selected_tracking_result() is None:
            return None
        raw_rows = self._orbit_history_rows()
        if not raw_rows:
            return None
        try:
            positions_km = np.asarray(
                [
                    [
                        float(row["position_x_m"]) / 1000.0,
                        float(row["position_y_m"]) / 1000.0,
                        float(row["position_z_m"]) / 1000.0,
                    ]
                    for row in raw_rows
                ],
                dtype=np.float64,
            )
            velocities_km_s = np.asarray(
                [
                    [
                        float(row["velocity_x_m_s"]) / 1000.0,
                        float(row["velocity_y_m_s"]) / 1000.0,
                        float(row["velocity_z_m_s"]) / 1000.0,
                    ]
                    for row in raw_rows
                ],
                dtype=np.float64,
            )
            elapsed_seconds = np.asarray([float(row.get("elapsed_time_s", 0.0)) for row in raw_rows], dtype=np.float64)
        except (KeyError, TypeError, ValueError):
            return None
        if positions_km.ndim != 2 or positions_km.shape[0] < 2 or positions_km.shape[1] != 3:
            return None
        index = self._orbit_row_index_for_elapsed(raw_rows, sample.elapsed_min)
        radii_km = np.linalg.norm(positions_km, axis=1)
        speeds_km_s = np.linalg.norm(velocities_km_s, axis=1)
        return OrbitTrajectory(
            positions_km=positions_km,
            velocities_km_s=velocities_km_s,
            radii_km=radii_km,
            speeds_km_s=speeds_km_s,
            elapsed_seconds=elapsed_seconds,
            current_position_km=positions_km[index],
            current_velocity_km_s=velocities_km_s[index],
        )

    @staticmethod
    def _orbit_row_index_for_elapsed(rows: list[dict[str, float | str]], elapsed_min: float) -> int:
        elapsed = np.asarray([float(row.get("elapsed_time_min", 0.0)) for row in rows], dtype=np.float64)
        return int(np.argmin(np.abs(elapsed - float(elapsed_min))))

    @staticmethod
    def _maneuver_segments_km(rows: list[dict[str, float | str]], positions_km: np.ndarray) -> list[np.ndarray]:
        segments: list[np.ndarray] = []
        current: list[np.ndarray] = []
        for index, row in enumerate(rows):
            phase = str(row.get("phase", ""))
            in_maneuver = phase in _MANEUVER_PHASES
            if in_maneuver and not current:
                if index > 0 and int(float(rows[index - 1].get("is_event_point", 0))):
                    current.append(positions_km[index - 1])
                current.append(positions_km[index])
            elif in_maneuver:
                current.append(positions_km[index])
            elif current:
                if len(current) >= 2:
                    segments.append(np.asarray(current, dtype=np.float64))
                current = []

        if len(current) >= 2:
            segments.append(np.asarray(current, dtype=np.float64))
        return segments

    def _direction_vectors_for_sample(self, sample: FlightProgramSample, current_position_km: np.ndarray) -> list[dict[str, object]]:
        earth_direction = -np.asarray(current_position_km, dtype=np.float64)
        sun_direction = self._ecef_direction_to_plot_inertial(np.asarray(sample.sun_ecef, dtype=np.float64), sample.elapsed_min)
        return [
            {
                "label": "Earth",
                "direction": earth_direction,
                "color": (0.42, 0.64, 1.0, 1.0),
            },
            {
                "label": "Sun",
                "direction": sun_direction,
                "color": (1.0, 0.55, 0.25, 1.0),
            },
        ]

    def _subsatellite_position_km_for_sample(self, sample: FlightProgramSample) -> np.ndarray:
        lon_rad = np.deg2rad(float(sample.subsatellite_longitude_deg))
        lat_rad = np.deg2rad(float(sample.subsatellite_latitude_deg))
        cos_lat = np.cos(lat_rad)
        surface_ecef = EARTH_RADIUS_KM * np.asarray(
            [
                cos_lat * np.cos(lon_rad),
                cos_lat * np.sin(lon_rad),
                np.sin(lat_rad),
            ],
            dtype=np.float64,
        )
        return self._ecef_direction_to_plot_inertial(surface_ecef, sample.elapsed_min)

    def _ecef_direction_to_plot_inertial(self, direction: np.ndarray, elapsed_min: float) -> np.ndarray:
        tracking = self._selected_tracking_result()
        if tracking is None:
            return direction
        try:
            theta = self._earth_rotation_rad_for_sample(elapsed_min)
        except Exception:
            return direction
        cos_theta = np.cos(theta)
        sin_theta = np.sin(theta)
        return np.asarray(
            [
                cos_theta * direction[0] - sin_theta * direction[1],
                sin_theta * direction[0] + cos_theta * direction[1],
                direction[2],
            ],
            dtype=np.float64,
        )

    def _earth_rotation_rad_for_sample(self, elapsed_min: float) -> float:
        reference_epoch = self._orbit_history_reference_epoch()
        if reference_epoch is None:
            tracking = self._selected_tracking_result()
            if tracking is None:
                return 0.0
            reference_epoch = parse_utc(tracking.t0_utc)
        epoch = reference_epoch + timedelta(minutes=float(elapsed_min))
        return float(_gmst_rad(epoch))

    def _orbit_history_reference_epoch(self) -> object:
        rows = self._orbit_history_rows()
        if not rows or self._orbit_history_cache_key is None:
            return None
        if self._orbit_history_epoch_cache_key != self._orbit_history_cache_key:
            self._orbit_history_epoch_cache_key = self._orbit_history_cache_key
            self._orbit_history_epoch_cache = None
            try:
                self._orbit_history_epoch_cache = parse_utc(derive_scenario_epoch_utc(rows))
            except Exception:
                self._orbit_history_epoch_cache = None
        return self._orbit_history_epoch_cache

    def _orbit_history_rows(self) -> list[dict[str, float | str]] | None:
        cache_key = self._orbit_history_signature()
        if cache_key != self._orbit_history_cache_key:
            self._orbit_history_cache_key = cache_key
            self._orbit_history_rows_cache = None
            self._orbit_positions_cache = None
            self._orbit_history_epoch_cache_key = None
            self._orbit_history_epoch_cache = None
            self._sample_context_cache = None
            self._sample_context_cache_key = None
        if cache_key is None:
            return None
        if self._orbit_history_rows_cache is None:
            self._orbit_history_rows_cache = load_orbit_history_rows(self._orbit_history_path())
        return self._orbit_history_rows_cache

    def _orbit_history_signature(self) -> tuple[str, int, int] | None:
        if self._workspace.current_project is None:
            return None
        path = self._orbit_history_path()
        if not path.exists():
            return None
        stat = path.stat()
        return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))

    def _sampling_context(self, maneuver_strategy: dict[str, Any]) -> FlightProgramSamplingContext | None:
        rows = self._orbit_history_rows()
        if rows is None or self._orbit_history_cache_key is None:
            return None
        strategy_key = json.dumps(maneuver_strategy, ensure_ascii=False, sort_keys=True, default=str)
        t0_key = str(self._program.get("selected_t0_utc", "") or "")
        cache_key = (self._orbit_history_cache_key, strategy_key, t0_key)
        if self._sample_context_cache is None or self._sample_context_cache_key != cache_key:
            self._sample_context_cache = build_flight_program_sampling_context(
                orbit_history_csv=self._orbit_history_path(),
                maneuver_strategy=maneuver_strategy,
                payload=self._program,
                t0_utc=t0_key or None,
                rows=rows,
            )
            self._sample_context_cache_key = cache_key
        return self._sample_context_cache

    def _refresh_source_labels(self) -> None:
        if not self._source_labels:
            return
        if self._workspace.current_project is None:
            values = ["项目：--", "变轨策略：--", "轨道历史：--", "飞行程序：--"]
        else:
            values = [
                f"项目：{self._workspace.current_project.name}",
                f"变轨策略：{self._workspace.maneuver_strategy_path()}",
                f"轨道历史：{self._orbit_history_path()}",
                f"飞行程序：{self._workspace.flight_program_path()}",
            ]
        for label, value in zip(self._source_labels, values, strict=True):
            label.setText(value)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._launch_source_combo,
            self._window_combo,
            self._manual_launch_edit,
            self._orbit_point_combo,
            self._calculate_refs_button,
            self._generate_button,
            self._save_button,
            self._add_attitude_button,
            self._add_deploy_button,
            self._delete_button,
        ):
            widget.setEnabled(enabled)

    def _set_status(self, role: str, text: str) -> None:
        self._status_role = role
        self._status_label.setProperty("role", role)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setText(text)

    def _orbit_history_path(self) -> Path:
        return self._workspace.data_dir() / "full_orbit_history.csv"

    @staticmethod
    def _tracking_result_from_payload(payload: dict[str, Any]) -> TrackingArcOrbitResult:
        segments = [
            TrackingArcSegment(
                row_label=str(item.get("row_label", "")),
                start_utc=str(item.get("start_utc", "")),
                end_utc=str(item.get("end_utc", "")),
                kind=str(item.get("kind", "")),
                tooltip=str(item.get("tooltip", "")),
            )
            for item in payload.get("segments", [])
            if isinstance(item, dict)
        ]
        summaries = [
            TrackingArcAssetSummary(
                name=str(item.get("name", "")),
                asset_type=str(item.get("asset_type", "")),
                interval_count=int(item.get("interval_count", 0)),
                total_duration_min=float(item.get("total_duration_min", 0.0)),
                longest_duration_min=float(item.get("longest_duration_min", 0.0)),
            )
            for item in payload.get("asset_summaries", [])
            if isinstance(item, dict)
        ]
        return TrackingArcOrbitResult(
            point_key=str(payload.get("point_key", "")),
            point_label=str(payload.get("point_label", "")),
            launch_utc=str(payload.get("launch_utc", "")),
            t0_utc=str(payload.get("t0_utc", "")),
            timeline_start_utc=str(payload.get("timeline_start_utc", "")),
            timeline_end_utc=str(payload.get("timeline_end_utc", "")),
            row_labels=[str(item) for item in payload.get("row_labels", [])],
            segments=segments,
            asset_summaries=summaries,
            shadow_total_min=float(payload.get("shadow_total_min", 0.0)),
            maneuver_count=int(payload.get("maneuver_count", 0)),
        )

    @staticmethod
    def _format_beijing(value: str) -> str:
        return parse_utc(value).astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _card(title: str) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("role", "cardTitle")
        layout.addWidget(title_label)
        return card

    @staticmethod
    def _minutes_spin() -> NoWheelDoubleSpinBox:
        spin = NoWheelDoubleSpinBox()
        spin.setRange(-100000.0, 100000.0)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        return spin

    @staticmethod
    def _launch_datetime_edit() -> NoWheelDateTimeEdit:
        field = NoWheelDateTimeEdit()
        field.setCalendarPopup(True)
        field.setDisplayFormat("yyyy-MM-dd HH:mm:ss 'BJT'")
        field.setTimeZone(_beijing_qtimezone())
        return field

    @staticmethod
    def _utc_to_qdatetime(value: str) -> QtCore.QDateTime:
        epoch = parse_utc(value)
        milliseconds = int(round(epoch.timestamp() * 1000.0))
        return QtCore.QDateTime.fromMSecsSinceEpoch(milliseconds, _beijing_qtimezone())


def _write_flight_program_xlsx(path: Path, sheets: list[tuple[str, list[list[Any]]]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types_xml(len(sheets)))
        archive.writestr("_rels/.rels", _xlsx_root_rels_xml())
        archive.writestr("xl/workbook.xml", _xlsx_workbook_xml([name for name, _rows in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels_xml(len(sheets)))
        archive.writestr("xl/styles.xml", _xlsx_styles_xml())
        for index, (_name, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _xlsx_sheet_xml(rows))


def _xlsx_sheet_xml(rows: list[list[Any]]) -> str:
    ET.register_namespace("", _XLSX_NS)
    worksheet = ET.Element(f"{{{_XLSX_NS}}}worksheet")
    max_columns = max((len(row) for row in rows), default=1)
    ET.SubElement(worksheet, f"{{{_XLSX_NS}}}dimension", {"ref": f"A1:{_xlsx_col_name(max_columns)}{max(len(rows), 1)}"})
    sheet_views = ET.SubElement(worksheet, f"{{{_XLSX_NS}}}sheetViews")
    ET.SubElement(sheet_views, f"{{{_XLSX_NS}}}sheetView", {"workbookViewId": "0"})
    cols = ET.SubElement(worksheet, f"{{{_XLSX_NS}}}cols")
    ET.SubElement(cols, f"{{{_XLSX_NS}}}col", {"min": "1", "max": str(max_columns), "width": "18", "customWidth": "1"})
    sheet_data = ET.SubElement(worksheet, f"{{{_XLSX_NS}}}sheetData")
    for row_index, values in enumerate(rows, start=1):
        row = ET.SubElement(sheet_data, f"{{{_XLSX_NS}}}row", {"r": str(row_index)})
        for col_index, value in enumerate(values, start=1):
            _append_xlsx_cell(row, row_index, col_index, value, style_id=1 if row_index == 1 else 2)
    return ET.tostring(worksheet, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _append_xlsx_cell(row: ET.Element, row_index: int, col_index: int, value: Any, *, style_id: int) -> None:
    cell = ET.SubElement(row, f"{{{_XLSX_NS}}}c", {"r": f"{_xlsx_col_name(col_index)}{row_index}", "s": str(style_id)})
    if value is None or value == "":
        return
    if isinstance(value, str):
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, f"{{{_XLSX_NS}}}is")
        text = ET.SubElement(inline, f"{{{_XLSX_NS}}}t")
        text.text = value
        return
    value_node = ET.SubElement(cell, f"{{{_XLSX_NS}}}v")
    value_node.text = f"{float(value):.12g}"


def _xlsx_col_name(index: int) -> str:
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_content_types_xml(sheet_count: int) -> str:
    worksheet_overrides = "\n".join(
        f'  <Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{worksheet_overrides}
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""


def _xlsx_root_rels_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _xlsx_workbook_rels_xml(sheet_count: int) -> str:
    sheet_rels = "\n".join(
        f'  <Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_REL_NS}">
{sheet_rels}
  <Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _xlsx_workbook_xml(sheet_names: list[str]) -> str:
    sheets = "\n".join(
        f'    <sheet name="{_escape_xlsx_attr(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{_XLSX_NS}" xmlns:r="{_XLSX_REL_NS}">
  <sheets>
{sheets}
  </sheets>
</workbook>"""


def _escape_xlsx_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _xlsx_styles_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{_XLSX_NS}">
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color auto="1"/></left><right style="thin"><color auto="1"/></right><top style="thin"><color auto="1"/></top><bottom style="thin"><color auto="1"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

def _parse_yes_no(value: str) -> bool:
    text = value.strip().lower()
    if text in {"是", "true", "1", "yes", "y", "锁定", "瞬时"}:
        return True
    if text in {"否", "false", "0", "no", "n", ""}:
        return False
    raise ValueError(f"Invalid yes/no value: {value}")


def _parse_kind(value: str) -> str:
    text = value.strip().lower()
    if text in {"姿态", "attitude", ATTITUDE_KIND}:
        return ATTITUDE_KIND
    if text in {"主要事件", "deployment", DEPLOYMENT_KIND}:
        return DEPLOYMENT_KIND
    raise ValueError(f"Invalid event kind: {value}")
