from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtWidgets

from smart.services.design_maneuver_strategy import (
    DesignManeuverResult,
    config_from_orbital_elements,
    default_design_maneuver_strategy_payload,
    normalize_design_maneuver_strategy_payload,
    plan_design_maneuver_strategy,
)
from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDateTimeEdit, NoWheelDoubleSpinBox, NoWheelSpinBox

BEIJING_QT_TIMEZONE_ID = b"Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class _NumberSpec:
    section: str
    key: str
    label: str
    minimum: float
    maximum: float
    step: float
    decimals: int


@dataclass(frozen=True, slots=True)
class _CheckSpec:
    section: str
    key: str
    label: str


class DesignManeuverStrategyPage(QtWidgets.QWidget):
    config_changed = QtCore.Signal(object)

    _NUMBER_SPECS = (
        _NumberSpec("initial", "m0_kg", "初始质量 (kg)", 1.0, 1.0e6, 10.0, 3),
        _NumberSpec("initial", "a_km", "初始半长轴 (km)", 1.0, 1.0e7, 10.0, 6),
        _NumberSpec("initial", "e", "初始偏心率", 0.0, 0.999999, 0.001, 9),
        _NumberSpec("initial", "i_deg", "初始倾角 (deg)", 0.0, 180.0, 0.1, 6),
        _NumberSpec("initial", "lon_node_deg", "升交点地理经度 (deg)", -360.0, 360.0, 0.1, 6),
        _NumberSpec("initial", "argp_deg", "近地点幅角 (deg)", -360.0, 360.0, 0.1, 6),
        _NumberSpec("initial", "mean_anomaly_deg", "平近点角 (deg)", -360.0, 360.0, 0.1, 6),
        _NumberSpec("target", "a_km", "目标半长轴 (km)", 1.0, 1.0e7, 10.0, 6),
        _NumberSpec("target", "e", "目标偏心率", 0.0, 0.999999, 0.001, 9),
        _NumberSpec("target", "i_deg", "目标倾角 (deg)", 0.0, 180.0, 0.1, 6),
        _NumberSpec("target", "lon_degE", "目标经度 (degE)", -360.0, 360.0, 0.1, 6),
        _NumberSpec("target", "dv_lon_margin_mps", "经度相位裕度 (m/s)", 0.0, 1.0e5, 1.0, 3),
        _NumberSpec("earth", "mu_km3_s2", "地球引力常数 (km^3/s^2)", 1.0, 1.0e9, 1.0, 6),
        _NumberSpec("earth", "Re_km", "地球半径 (km)", 1.0, 1.0e6, 1.0, 6),
        _NumberSpec("engine", "F_main_N", "主发动机推力 (N)", 0.0, 1.0e7, 1.0, 3),
        _NumberSpec("engine", "Isp_main_s", "主发动机比冲 (s)", 1.0, 1.0e5, 1.0, 3),
        _NumberSpec("engine", "attitude_control_efficiency", "姿控效率修正", 0.0, 10.0, 0.001, 6),
        _NumberSpec("engine", "F_set_N", "沉底推力 (N)", 0.0, 1.0e7, 1.0, 3),
        _NumberSpec("engine", "Isp_set_s", "沉底比冲 (s)", 1.0, 1.0e5, 1.0, 3),
        _NumberSpec("engine", "tau_set_s", "沉底时长 (s)", 0.0, 1.0e6, 1.0, 3),
        _NumberSpec("burn_limit", "max_total_burn_time_min", "单次总时长上限 (min)", 0.0, 1.0e5, 1.0, 3),
        _NumberSpec("burn_limit", "preferred_total_burn_time_min", "推荐设计时长 (min)", 0.0, 1.0e5, 1.0, 3),
        _NumberSpec("burn_limit", "burn_utilization", "推荐安全系数", 0.0, 1.0, 0.01, 4),
        _NumberSpec("burn_limit", "design_dv_per_burn_mps", "单次设计 Δv (m/s)", 1.0, 1.0e5, 1.0, 3),
        _NumberSpec("orbit_type", "supersync_transfer_margin_km", "超同步判断裕度 (km)", 0.0, 1.0e6, 10.0, 3),
        _NumberSpec("orbit_type", "standard_transfer_apogee_margin_km", "标准转移判断裕度 (km)", 0.0, 1.0e6, 10.0, 3),
        _NumberSpec("maneuver_count", "min", "最小变轨次数", 1.0, 99.0, 1.0, 0),
        _NumberSpec("maneuver_count", "max", "最大变轨次数", 1.0, 99.0, 1.0, 0),
        _NumberSpec("maneuver_count", "user", "用户指定次数 (0=自动)", 0.0, 99.0, 1.0, 0),
        _NumberSpec("maneuver_count", "engineering_min_count", "工程最小次数", 1.0, 99.0, 1.0, 0),
        _NumberSpec("maneuver_count", "total_dv_est_user_mps", "用户总 Δv 估计 (m/s)", 0.0, 1.0e6, 10.0, 3),
        _NumberSpec("distribution", "max_uniform_dv_spread_mps", "均匀性最大离散度 (m/s)", 0.0, 1.0e6, 1.0, 3),
        _NumberSpec("distribution", "dv_min_per_burn_mps", "单次最小 Δv (m/s)", 0.0, 1.0e5, 1.0, 3),
        _NumberSpec("distribution", "front_dv_total_user_mps", "前段总 Δv (m/s)", 0.0, 1.0e6, 10.0, 3),
        _NumberSpec("distribution", "standard_terminal_reserve_mps", "标准末次保留 (m/s)", 0.0, 1.0e5, 1.0, 3),
        _NumberSpec("supersynchronous_transfer", "tail_fixed_count", "固定尾段次数", 0.0, 99.0, 1.0, 0),
        _NumberSpec("supersynchronous_transfer", "a_tail_apogee_plus_fixed_km", "尾段远地点后 a (km)", 1.0, 1.0e7, 10.0, 6),
        _NumberSpec("supersynchronous_transfer", "a_tail_perigee_plus_fixed_km", "尾段近地点后 a (km)", 1.0, 1.0e7, 10.0, 6),
        _NumberSpec("supersynchronous_transfer", "dv_tail_apogee_fixed_mps", "尾段远地点固定 Δv (m/s)", 0.0, 1.0e6, 1.0, 3),
        _NumberSpec("supersynchronous_transfer", "dv_tail_perigee_fixed_mps", "尾段近地点固定 Δv (m/s)", 0.0, 1.0e6, 1.0, 3),
        _NumberSpec("apsis", "q_AA_default", "默认回归圈数 q", 1.0, 99.0, 1.0, 0),
        _NumberSpec("apsis", "search_revolutions_max", "经度搜索最大圈数", 1.0, 999.0, 1.0, 0),
        _NumberSpec("alpha", "alpha_default_deg", "默认方向角 (deg)", -180.0, 180.0, 1.0, 3),
        _NumberSpec("terminal_tolerance", "a_km", "终端 a 容差 (km)", 0.0, 1.0e6, 0.1, 6),
        _NumberSpec("terminal_tolerance", "e", "终端 e 容差", 0.0, 1.0, 0.0001, 9),
        _NumberSpec("terminal_tolerance", "i_deg", "终端 i 容差 (deg)", 0.0, 180.0, 0.01, 6),
        _NumberSpec("terminal_tolerance", "lon_deg", "终端经度容差 (deg)", 0.0, 360.0, 0.01, 6),
    )

    _PAIR_SPECS = (
        ("longitude", "raw_window_degE", "原始经度窗口 (degE)"),
        ("longitude", "planning_window_degE", "规划经度窗口 (degE)"),
        ("longitude", "finite_margin_window_degE", "有限推力预留窗口 (degE)"),
        ("alpha", "front_bounds_deg", "前段方向角范围 (deg)"),
        ("alpha", "tail_apogee_bounds_deg", "尾段远地点方向角范围 (deg)"),
        ("alpha", "tail_perigee_bounds_deg", "尾段近地点方向角范围 (deg)"),
    )

    _CHECK_SPECS = (
        _CheckSpec("engine", "use_settling", "启用沉底"),
        _CheckSpec("burn_limit", "include_settling_in_burn_time", "点火时长包含沉底"),
        _CheckSpec("earth", "use_J2", "启用 J2"),
        _CheckSpec("planner", "auto_recommend_count", "自动推荐次数"),
        _CheckSpec("planner", "force_user_count", "强制采用用户次数"),
        _CheckSpec("supersynchronous_transfer", "tail_fixed_enabled", "超同步固定尾段"),
        _CheckSpec("distribution", "allow_small_dv_correction", "允许小 Δv 修正"),
        _CheckSpec("alpha", "optimize_alpha", "方向角优化"),
    )

    def __init__(
        self,
        i18n: I18nManager,
        workspace: ProjectWorkspace,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._workspace = workspace
        self._config = default_design_maneuver_strategy_payload()
        self._suppress_emit = False
        self._number_fields: dict[tuple[str, str], QtWidgets.QAbstractSpinBox] = {}
        self._pair_fields: dict[tuple[str, str], tuple[NoWheelDoubleSpinBox, NoWheelDoubleSpinBox]] = {}
        self._check_fields: dict[tuple[str, str], QtWidgets.QCheckBox] = {}
        self._combo_fields: dict[tuple[str, str], NoWheelComboBox] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        eyebrow = QtWidgets.QLabel("SMART · DESIGN MANEUVER STRATEGY")
        eyebrow.setProperty("role", "pageEyebrow")
        root.addWidget(eyebrow)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        accent_rule = QtWidgets.QFrame()
        accent_rule.setProperty("role", "accentRule")
        accent_rule.setFixedHeight(2)
        accent_rule.setMaximumWidth(220)
        root.addWidget(accent_rule)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_config_panel())
        splitter.addWidget(self._build_result_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([680, 860])

        self._status_label = QtWidgets.QLabel()
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh_from_workspace()

    def _build_config_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        layout = QtWidgets.QVBoxLayout(canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(self._build_card("初始轨道", self._initial_grid()))
        layout.addWidget(self._build_card("目标轨道", self._grid_for_specs("target")))
        layout.addWidget(self._build_card("发动机与点火约束", self._grid_for_specs("engine", "burn_limit")))
        layout.addWidget(self._build_card("轨道类型与变轨次数", self._count_grid()))
        layout.addWidget(self._build_card("经度窗口与分配", self._distribution_grid()))
        layout.addWidget(self._build_card("超同步尾段与方向角", self._tail_alpha_grid()))

        button_card = QtWidgets.QFrame()
        button_card.setProperty("role", "card")
        button_layout = QtWidgets.QVBoxLayout(button_card)
        button_layout.setContentsMargins(18, 18, 18, 18)
        button_layout.setSpacing(10)
        self._config_path_label = QtWidgets.QLabel()
        self._config_path_label.setProperty("role", "cardCaption")
        self._config_path_label.setWordWrap(True)
        button_layout.addWidget(self._config_path_label)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self._reload_button = QtWidgets.QPushButton()
        self._reload_button.setProperty("variant", "secondary")
        self._reload_button.clicked.connect(self.refresh_from_workspace)
        row.addWidget(self._reload_button)
        self._import_baseline_button = QtWidgets.QPushButton()
        self._import_baseline_button.setProperty("variant", "secondary")
        self._import_baseline_button.clicked.connect(self._load_project_baseline)
        row.addWidget(self._import_baseline_button)
        self._save_button = QtWidgets.QPushButton()
        self._save_button.setProperty("variant", "secondary")
        self._save_button.clicked.connect(self.save_config)
        row.addWidget(self._save_button)
        button_layout.addLayout(row)
        self._plan_button = QtWidgets.QPushButton()
        self._plan_button.setProperty("variant", "primaryAction")
        self._plan_button.clicked.connect(self.run_planner)
        button_layout.addWidget(self._plan_button)
        layout.addWidget(button_card)
        layout.addStretch(1)
        return scroll

    def _build_result_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        summary_card = QtWidgets.QFrame()
        summary_card.setProperty("role", "card")
        summary_layout = QtWidgets.QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 18, 18, 18)
        summary_layout.setSpacing(10)
        self._summary_header_label = QtWidgets.QLabel()
        self._summary_header_label.setProperty("role", "cardTitle")
        summary_layout.addWidget(self._summary_header_label)
        self._summary_table = QtWidgets.QTableWidget(0, 2)
        self._setup_readonly_table(self._summary_table)
        self._summary_table.horizontalHeader().setStretchLastSection(True)
        self._summary_table.setMinimumHeight(220)
        summary_layout.addWidget(self._summary_table)
        layout.addWidget(summary_card, 1)

        burn_card = QtWidgets.QFrame()
        burn_card.setProperty("role", "card")
        burn_layout = QtWidgets.QVBoxLayout(burn_card)
        burn_layout.setContentsMargins(18, 18, 18, 18)
        burn_layout.setSpacing(10)
        self._burn_header_label = QtWidgets.QLabel()
        self._burn_header_label.setProperty("role", "cardTitle")
        burn_layout.addWidget(self._burn_header_label)
        self._burn_table = QtWidgets.QTableWidget(0, 13)
        self._setup_readonly_table(self._burn_table)
        self._burn_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._burn_table.setMinimumHeight(260)
        burn_layout.addWidget(self._burn_table)
        layout.addWidget(burn_card, 2)

        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setSpacing(14)
        check_card = QtWidgets.QFrame()
        check_card.setProperty("role", "card")
        check_layout = QtWidgets.QVBoxLayout(check_card)
        check_layout.setContentsMargins(18, 18, 18, 18)
        check_layout.setSpacing(10)
        self._check_header_label = QtWidgets.QLabel()
        self._check_header_label.setProperty("role", "cardTitle")
        check_layout.addWidget(self._check_header_label)
        self._check_table = QtWidgets.QTableWidget(0, 4)
        self._setup_readonly_table(self._check_table)
        self._check_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._check_table.setMinimumHeight(180)
        check_layout.addWidget(self._check_table)
        bottom_row.addWidget(check_card, 3)

        future_card = QtWidgets.QFrame()
        future_card.setProperty("role", "card")
        future_layout = QtWidgets.QVBoxLayout(future_card)
        future_layout.setContentsMargins(18, 18, 18, 18)
        future_layout.setSpacing(10)
        self._future_header_label = QtWidgets.QLabel()
        self._future_header_label.setProperty("role", "cardTitle")
        future_layout.addWidget(self._future_header_label)
        self._future_slot_label = QtWidgets.QLabel()
        self._future_slot_label.setProperty("role", "pageBody")
        self._future_slot_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._future_slot_label.setMinimumHeight(180)
        future_layout.addWidget(self._future_slot_label, 1)
        self._warning_label = QtWidgets.QLabel()
        self._warning_label.setProperty("role", "statusDisconnected")
        self._warning_label.setWordWrap(True)
        future_layout.addWidget(self._warning_label)
        bottom_row.addWidget(future_card, 2)
        layout.addLayout(bottom_row, 1)
        return panel

    def _initial_grid(self) -> QtWidgets.QGridLayout:
        grid = self._new_form_grid()
        label = QtWidgets.QLabel("初始历元 (北京时间)")
        label.setProperty("role", "cardCaption")
        self._t0_epoch_field = NoWheelDateTimeEdit()
        self._t0_epoch_field.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._t0_epoch_field.setTimeZone(QtCore.QTimeZone(BEIJING_QT_TIMEZONE_ID))
        self._t0_epoch_field.setCalendarPopup(True)
        self._t0_epoch_field.dateTimeChanged.connect(lambda _value: self._emit_config_changed())
        grid.addWidget(label, 0, 0)
        grid.addWidget(self._t0_epoch_field, 0, 1)
        self._add_specs_to_grid(grid, [spec for spec in self._NUMBER_SPECS if spec.section == "initial"], start_row=1)
        return grid

    def _count_grid(self) -> QtWidgets.QGridLayout:
        grid = self._new_form_grid()
        self._add_combo(grid, 0, "orbit_type", "mode", "轨道类型模式", (
            ("auto", "auto"),
            ("supersynchronous_transfer", "supersynchronous_transfer"),
            ("standard_transfer", "standard_transfer"),
            ("general_transfer", "general_transfer"),
        ))
        self._add_specs_to_grid(
            grid,
            [spec for spec in self._NUMBER_SPECS if spec.section in {"orbit_type", "maneuver_count"}],
            start_row=1,
        )
        return grid

    def _distribution_grid(self) -> QtWidgets.QGridLayout:
        grid = self._new_form_grid()
        self._add_combo(grid, 0, "distribution", "mode", "分配模式", (
            ("auto", "auto"),
            ("uniform_all", "uniform_all"),
            ("uniform_front_fixed_tail", "uniform_front_fixed_tail"),
            ("weighted_uniform", "weighted_uniform"),
        ))
        self._add_specs_to_grid(
            grid,
            [spec for spec in self._NUMBER_SPECS if spec.section == "distribution"],
            start_row=1,
        )
        next_row = grid.rowCount()
        for offset, (section, key, label) in enumerate(self._PAIR_SPECS[:3]):
            self._add_pair(grid, next_row + offset, section, key, label)
        return grid

    def _tail_alpha_grid(self) -> QtWidgets.QGridLayout:
        grid = self._new_form_grid()
        self._add_combo(grid, 0, "supersynchronous_transfer", "tail_control_mode", "尾段控制模式", (
            ("fixed_post_a", "fixed_post_a"),
            ("fixed_delta_v", "fixed_delta_v"),
        ))
        self._add_combo(grid, 1, "apsis", "pattern_mode", "拱点序列模式", (
            ("auto", "auto"),
            ("user", "user"),
        ))
        self._add_specs_to_grid(
            grid,
            [
                spec
                for spec in self._NUMBER_SPECS
                if spec.section in {"supersynchronous_transfer", "apsis", "alpha", "terminal_tolerance"}
            ],
            start_row=2,
        )
        next_row = grid.rowCount()
        for offset, (section, key, label) in enumerate(self._PAIR_SPECS[3:]):
            self._add_pair(grid, next_row + offset, section, key, label)
        for offset, spec in enumerate(self._CHECK_SPECS):
            checkbox = QtWidgets.QCheckBox(spec.label)
            checkbox.stateChanged.connect(lambda _value: self._emit_config_changed())
            self._check_fields[(spec.section, spec.key)] = checkbox
            grid.addWidget(checkbox, next_row + len(self._PAIR_SPECS[3:]) + offset, 0, 1, 2)
        return grid

    def _grid_for_specs(self, *sections: str) -> QtWidgets.QGridLayout:
        grid = self._new_form_grid()
        self._add_specs_to_grid(grid, [spec for spec in self._NUMBER_SPECS if spec.section in sections])
        return grid

    def _build_card(self, title: str, content: QtWidgets.QLayout) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        label = QtWidgets.QLabel(title)
        label.setProperty("role", "cardTitle")
        layout.addWidget(label)
        layout.addLayout(content)
        return card

    def _add_specs_to_grid(
        self,
        grid: QtWidgets.QGridLayout,
        specs: list[_NumberSpec],
        *,
        start_row: int = 0,
    ) -> None:
        for row_offset, spec in enumerate(specs):
            row = start_row + row_offset
            label = QtWidgets.QLabel(spec.label)
            label.setProperty("role", "cardCaption")
            if spec.decimals == 0:
                field: QtWidgets.QAbstractSpinBox = NoWheelSpinBox()
                assert isinstance(field, QtWidgets.QSpinBox)
                field.setRange(int(spec.minimum), int(spec.maximum))
                field.setSingleStep(int(spec.step))
            else:
                field = NoWheelDoubleSpinBox()
                assert isinstance(field, QtWidgets.QDoubleSpinBox)
                field.setRange(spec.minimum, spec.maximum)
                field.setDecimals(spec.decimals)
                field.setSingleStep(spec.step)
            field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
            field.valueChanged.connect(lambda _value: self._emit_config_changed())
            field.setMinimumWidth(160)
            self._number_fields[(spec.section, spec.key)] = field
            grid.addWidget(label, row, 0)
            grid.addWidget(field, row, 1)

    def _add_pair(self, grid: QtWidgets.QGridLayout, row: int, section: str, key: str, label: str) -> None:
        caption = QtWidgets.QLabel(label)
        caption.setProperty("role", "cardCaption")
        low = self._double_spin(-360.0, 360.0, 0.1, 6)
        high = self._double_spin(-360.0, 360.0, 0.1, 6)
        low.valueChanged.connect(lambda _value: self._emit_config_changed())
        high.valueChanged.connect(lambda _value: self._emit_config_changed())
        row_widget = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(low)
        row_layout.addWidget(high)
        self._pair_fields[(section, key)] = (low, high)
        grid.addWidget(caption, row, 0)
        grid.addWidget(row_widget, row, 1)

    def _add_combo(
        self,
        grid: QtWidgets.QGridLayout,
        row: int,
        section: str,
        key: str,
        label: str,
        items: tuple[tuple[str, str], ...],
    ) -> None:
        caption = QtWidgets.QLabel(label)
        caption.setProperty("role", "cardCaption")
        combo = NoWheelComboBox()
        for text, data in items:
            combo.addItem(text, data)
        combo.currentIndexChanged.connect(lambda _value: self._emit_config_changed())
        combo.setMinimumWidth(160)
        self._combo_fields[(section, key)] = combo
        grid.addWidget(caption, row, 0)
        grid.addWidget(combo, row, 1)

    def refresh_from_workspace(self) -> None:
        if self._workspace.current_project is None:
            self._config = default_design_maneuver_strategy_payload()
            self._apply_config_to_fields(self._config)
            self._set_controls_enabled(False)
            self._clear_results()
            self._refresh_config_path_label()
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.no_project"))
            return

        try:
            config = self._workspace.load_design_maneuver_strategy()
        except Exception as exc:
            self._set_controls_enabled(False)
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.load_failed", error=str(exc)))
            return

        self._config = config if config is not None else default_design_maneuver_strategy_payload()
        self._apply_config_to_fields(self._config)
        self._set_controls_enabled(True)
        self._refresh_config_path_label()
        self._clear_results()
        self._set_status("statusReady", self._i18n.t("design_maneuver.status.loaded"))

    def save_config(self) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.no_project"))
            return None
        self._config = self.config()
        try:
            path = self._workspace.save_design_maneuver_strategy(self._config)
            loaded = self._workspace.load_design_maneuver_strategy()
        except Exception as exc:
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.save_failed", error=str(exc)))
            return None
        if loaded is not None:
            self._config = loaded
            self._apply_config_to_fields(loaded)
        self._set_status("statusReady", self._i18n.t("design_maneuver.status.saved", path=str(path)))
        return path

    def run_planner(self) -> None:
        if self.save_config() is None:
            return
        try:
            result = plan_design_maneuver_strategy(self._config)
        except Exception as exc:
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.plan_failed", error=str(exc)))
            return
        self._set_result(result)
        self._set_status("statusReady", self._i18n.t("design_maneuver.status.plan_done"))

    def config(self) -> dict[str, Any]:
        config = normalize_design_maneuver_strategy_payload(self._config)
        config["initial"]["t0_epoch"] = self._datetime_edit_to_utc(self._t0_epoch_field)
        for (section, key), field in self._number_fields.items():
            value = field.value()
            config[section][key] = int(value) if isinstance(field, QtWidgets.QSpinBox) else float(value)
        for (section, key), (low, high) in self._pair_fields.items():
            config[section][key] = [float(low.value()), float(high.value())]
        for (section, key), checkbox in self._check_fields.items():
            config[section][key] = checkbox.isChecked()
        for (section, key), combo in self._combo_fields.items():
            config[section][key] = str(combo.currentData())
        config["planner"]["maneuver_count_user"] = int(config["maneuver_count"]["user"])
        return normalize_design_maneuver_strategy_payload(config)

    def _load_project_baseline(self) -> None:
        if self._workspace.current_project is None:
            return
        try:
            orbit_init = self._workspace.load_orbit_initialization()
            satellite = self._workspace.load_satellite_status()
        except Exception as exc:
            self._set_status(
                "statusDisconnected",
                self._i18n.t("design_maneuver.status.baseline_failed", error=str(exc)),
            )
            return
        if orbit_init is None:
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.baseline_missing"))
            return
        mass = satellite.launch_mass_kg if satellite is not None else None
        self._config = config_from_orbital_elements(
            self.config(),
            orbit_init.elements,
            epoch_utc=orbit_init.epoch_utc,
            mass_kg=mass,
        )
        self._apply_config_to_fields(self._config)
        self._emit_config_changed()
        self._set_status("statusReady", self._i18n.t("design_maneuver.status.baseline_loaded"))

    def _apply_config_to_fields(self, config: dict[str, Any]) -> None:
        normalized = normalize_design_maneuver_strategy_payload(config)
        self._suppress_emit = True
        try:
            self._t0_epoch_field.setDateTime(self._utc_to_qdatetime(str(normalized["initial"]["t0_epoch"])))
            for (section, key), field in self._number_fields.items():
                value = normalized[section][key]
                if value is None:
                    value = 0
                if isinstance(field, QtWidgets.QSpinBox):
                    field.setValue(int(value))
                else:
                    field.setValue(float(value))
            for (section, key), (low, high) in self._pair_fields.items():
                values = normalized[section][key]
                low.setValue(float(values[0]))
                high.setValue(float(values[1]))
            for (section, key), checkbox in self._check_fields.items():
                checkbox.setChecked(bool(normalized[section][key]))
            for (section, key), combo in self._combo_fields.items():
                self._set_combo_value(combo, str(normalized[section][key]))
        finally:
            self._suppress_emit = False

    def _set_result(self, result: DesignManeuverResult) -> None:
        self._config = result.config
        summary_rows = [
            ("初始远地点高度", f"{result.summary['initial_apogee_altitude_km']:.3f} km"),
            ("同步轨道高度", f"{result.summary['sync_altitude_km']:.3f} km"),
            ("轨道类型", str(result.summary["orbit_type"])),
            ("粗估总 Δv", f"{result.summary['estimated_total_delta_v_mps']:.3f} m/s"),
            ("单次设计 Δv", f"{result.summary['design_single_burn_delta_v_mps']:.3f} m/s"),
            ("自动推荐次数", str(result.summary["recommended_count"])),
            ("用户指定次数", str(result.summary["user_count"])),
            ("实际采用次数", str(result.summary["actual_count"])),
            ("点火结构", str(result.summary["apsis_pattern"])),
            ("均匀性离散度", f"{result.summary['uniform_spread_mps']:.3f} m/s"),
        ]
        self._set_two_column_rows(self._summary_table, summary_rows)

        self._burn_table.setRowCount(0)
        for burn in result.burns:
            row = self._burn_table.rowCount()
            self._burn_table.insertRow(row)
            values = (
                str(burn.index),
                burn.burn_type,
                burn.apsis,
                f"{burn.elapsed_min:.3f}",
                burn.beijing_time,
                f"{burn.longitude_deg_e:.6f}",
                f"{burn.delta_v_mps:.3f}",
                f"{burn.alpha_deg:.3f}",
                "--" if burn.target_post_a_km is None else f"{burn.target_post_a_km:.6f}",
                f"{burn.total_burn_time_min:.3f}",
                f"{burn.propellant_kg:.6f}",
                f"{burn.post_a_km:.6f}",
                f"{burn.post_i_deg:.6f}",
            )
            self._set_row_values(self._burn_table, row, values)

        self._check_table.setRowCount(0)
        for check in result.checks:
            row = self._check_table.rowCount()
            self._check_table.insertRow(row)
            self._set_row_values(
                self._check_table,
                row,
                (
                    str(check["item"]),
                    str(check["requirement"]),
                    str(check["result"]),
                    "是" if bool(check["passed"]) else "否",
                ),
            )
        self._warning_label.setText("\n".join(result.warnings) if result.warnings else "无警告")

    def _clear_results(self) -> None:
        self._summary_table.setRowCount(0)
        self._burn_table.setRowCount(0)
        self._check_table.setRowCount(0)
        self._warning_label.setText("--")

    def _set_two_column_rows(self, table: QtWidgets.QTableWidget, rows: list[tuple[str, str]]) -> None:
        table.setRowCount(0)
        for row_index, values in enumerate(rows):
            table.insertRow(row_index)
            self._set_row_values(table, row_index, values)

    @staticmethod
    def _set_row_values(table: QtWidgets.QTableWidget, row: int, values: tuple[str, ...]) -> None:
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, column, item)

    def _emit_config_changed(self) -> None:
        if self._suppress_emit:
            return
        self._config = self.config()
        self.config_changed.emit(self._config)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._reload_button,
            self._import_baseline_button,
            self._save_button,
            self._plan_button,
        ):
            widget.setEnabled(enabled)
        for field in list(self._number_fields.values()) + [self._t0_epoch_field]:
            field.setEnabled(enabled)
        for low, high in self._pair_fields.values():
            low.setEnabled(enabled)
            high.setEnabled(enabled)
        for checkbox in self._check_fields.values():
            checkbox.setEnabled(enabled)
        for combo in self._combo_fields.values():
            combo.setEnabled(enabled)

    def _refresh_config_path_label(self) -> None:
        if self._workspace.current_project is None:
            self._config_path_label.setText(self._i18n.t("design_maneuver.config_path.none"))
        else:
            self._config_path_label.setText(
                self._i18n.t(
                    "design_maneuver.config_path",
                    path=str(self._workspace.design_maneuver_strategy_path()),
                )
            )

    def _set_status(self, role: str, text: str) -> None:
        self._status_label.setProperty("role", role)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setText(text)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("design_maneuver.title"))
        self._reload_button.setText(f"+  {t('design_maneuver.reload_button')}")
        self._import_baseline_button.setText(t("design_maneuver.load_baseline_button"))
        self._save_button.setText(t("design_maneuver.save_button"))
        self._plan_button.setText(t("design_maneuver.plan_button"))
        self._summary_header_label.setText(t("design_maneuver.summary_header"))
        self._burn_header_label.setText(t("design_maneuver.burn_header"))
        self._check_header_label.setText(t("design_maneuver.check_header"))
        self._future_header_label.setText(t("design_maneuver.future_header"))
        self._future_slot_label.setText(t("design_maneuver.future_placeholder"))
        self._summary_table.setHorizontalHeaderLabels(["项目", "数值"])
        self._burn_table.setHorizontalHeaderLabels(
            [
                "次数",
                "类型",
                "拱点",
                "航时/min",
                "北京时间",
                "经度/degE",
                "Δv/m/s",
                "alpha/deg",
                "目标a+/km",
                "总时长/min",
                "推进剂/kg",
                "后a/km",
                "后i/deg",
            ]
        )
        self._check_table.setHorizontalHeaderLabels(["检查项", "要求", "结果", "通过"])
        self._refresh_config_path_label()

    @staticmethod
    def _setup_readonly_table(table: QtWidgets.QTableWidget) -> None:
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

    @staticmethod
    def _new_form_grid() -> QtWidgets.QGridLayout:
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        return grid

    @staticmethod
    def _double_spin(minimum: float, maximum: float, step: float, decimals: int) -> NoWheelDoubleSpinBox:
        field = NoWheelDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setSingleStep(step)
        field.setDecimals(decimals)
        field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        return field

    @staticmethod
    def _set_combo_value(combo: NoWheelComboBox, value: str) -> None:
        for index in range(combo.count()):
            if str(combo.itemData(index)) == value:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)

    @staticmethod
    def _beijing_qtimezone() -> QtCore.QTimeZone:
        return QtCore.QTimeZone(BEIJING_QT_TIMEZONE_ID)

    @classmethod
    def _utc_to_qdatetime(cls, value: str) -> QtCore.QDateTime:
        epoch = parse_utc(value)
        milliseconds = int(epoch.timestamp() * 1000)
        return QtCore.QDateTime.fromMSecsSinceEpoch(milliseconds, cls._beijing_qtimezone())

    @staticmethod
    def _datetime_edit_to_utc(field: NoWheelDateTimeEdit) -> str:
        milliseconds = field.dateTime().toMSecsSinceEpoch()
        epoch = QtCore.QDateTime.fromMSecsSinceEpoch(milliseconds, QtCore.QTimeZone.utc()).toPython()
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=timezone.utc)
        return format_utc(epoch.astimezone(timezone.utc))
