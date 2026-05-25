from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
import math
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.design_maneuver_strategy import (
    ContinuousThrustOptimizationResult,
    DesignManeuverResult,
    default_design_maneuver_strategy_payload,
    export_continuous_thrust_maneuver_strategy_xlsx,
    export_continuous_thrust_orbit_history_csv,
    find_feasible_q_sequences as service_find_feasible_q_sequences,
    initial_design_maneuver_subsatellite_longitude_deg_e,
    normalize_design_maneuver_strategy_payload,
    optimize_continuous_thrust_model_parameters,
    plan_design_maneuver_strategy,
)
from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDateTimeEdit, NoWheelDoubleSpinBox, NoWheelSpinBox

BEIJING_QT_TIMEZONE_ID = b"Asia/Shanghai"


class _ArrowNoWheelComboBox(NoWheelComboBox):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        color = QtGui.QColor("#66d9ea" if self.isEnabled() else "#5d7684")
        painter.setBrush(color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        center_x = self.width() - 16
        center_y = self.height() // 2 + 1
        triangle = QtGui.QPolygon(
            [
                QtCore.QPoint(center_x - 5, center_y - 3),
                QtCore.QPoint(center_x + 5, center_y - 3),
                QtCore.QPoint(center_x, center_y + 4),
            ]
        )
        painter.drawPolygon(triangle)
        painter.end()


def _perigee_altitude_km(a_km: float, e: float, re_km: float) -> float:
    return float(a_km) * (1.0 - float(e)) - float(re_km)


def _apogee_altitude_km(a_km: float, e: float, re_km: float) -> float:
    return float(a_km) * (1.0 + float(e)) - float(re_km)


def _format_config_text_value(section: str, key: str, value: Any) -> str:
    if value in (None, ""):
        return ""
    if section == "hard_constraint_planner" and key in {"q_AA_user", "q_AP_candidates"}:
        if isinstance(value, (list, tuple)):
            return ",".join(str(int(item)) for item in value)
    if section == "hard_constraint_planner" and key == "fixed_hp_targets_km":
        if isinstance(value, dict):
            return ",".join(
                f"{int(raw_key)}:{float(raw_value):g}"
                for raw_key, raw_value in sorted(value.items(), key=lambda item: int(item[0]))
            )
    return str(value)


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


@dataclass(frozen=True, slots=True)
class _ComboSpec:
    section: str
    key: str
    label: str
    items: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _TextSpec:
    section: str
    key: str
    label: str
    placeholder: str = ""


@dataclass(frozen=True, slots=True)
class _DialogCardSpec:
    title: str
    number_specs: tuple[_NumberSpec, ...] = ()
    pair_specs: tuple[tuple[str, str, str], ...] = ()
    check_specs: tuple[_CheckSpec, ...] = ()
    combo_specs: tuple[_ComboSpec, ...] = ()
    text_specs: tuple[_TextSpec, ...] = ()
    include_epoch: bool = False


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
        _NumberSpec("target", "dv_lon_margin_mps", "Δv估算裕度 (m/s)", 0.0, 1.0e5, 1.0, 3),
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
        _NumberSpec("maneuver_count", "user", "用户指定变轨次数 (0=自动)", 0.0, 99.0, 1.0, 0),
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
        _NumberSpec("hard_constraint_planner", "prefilter_top_k", "V5.1 预筛候选数", 1.0, 999.0, 1.0, 0),
        _NumberSpec("hard_constraint_planner", "max_local_starts_per_sequence", "V5.1 多起点数", 1.0, 99.0, 1.0, 0),
        _NumberSpec("hard_constraint_planner", "local_maxiter", "V5.1 单序列迭代上限", 1.0, 999.0, 1.0, 0),
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
        _CheckSpec("hard_constraint_planner", "enabled", "启用 V5.1 硬约束优化"),
        _CheckSpec("hard_constraint_planner", "hard_raw_window", "原始窗口硬约束"),
        _CheckSpec("hard_constraint_planner", "hard_planning_window", "规划窗口硬约束"),
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
        self._last_result: DesignManeuverResult | None = None
        self._continuous_thrust_result: ContinuousThrustOptimizationResult | None = None
        self._updating_burn_table = False
        self._planning_busy = False
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

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

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(18)
        left_stack = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_stack)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._build_config_panel())
        top_row.addWidget(left_stack, 1)
        top_row.addWidget(self._build_config_overview_card(), 1)
        root.addLayout(top_row, 0)

        root.addWidget(self._build_result_panel(), 0)
        root.addStretch(1)

        self._status_label = QtWidgets.QLabel()
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh_from_workspace()

    def _build_config_panel(self) -> QtWidgets.QWidget:
        button_card = QtWidgets.QFrame()
        self._config_panel = button_card
        button_card.setProperty("role", "card")
        button_card.setMinimumHeight(194)
        button_card.setMaximumHeight(218)
        button_layout = QtWidgets.QVBoxLayout(button_card)
        self._config_panel_layout = button_layout
        button_layout.setContentsMargins(18, 14, 18, 14)
        button_layout.setSpacing(10)
        self._config_path_label = QtWidgets.QLabel()
        self._config_path_label.setProperty("role", "cardCaption")
        self._config_path_label.setWordWrap(True)
        self._config_path_label.setMaximumHeight(38)
        button_layout.addWidget(self._config_path_label)

        button_grid = QtWidgets.QGridLayout()
        button_grid.setHorizontalSpacing(10)
        button_grid.setVerticalSpacing(10)

        self._parameter_config_button = QtWidgets.QPushButton()
        self._parameter_config_button.setProperty("variant", "primaryAction")
        self._prepare_action_button(self._parameter_config_button, min_width=170, height=44)
        self._parameter_config_button.clicked.connect(self._open_parameter_config_dialog)
        button_grid.addWidget(self._parameter_config_button, 0, 0)

        self._advanced_settings_button = QtWidgets.QPushButton()
        self._advanced_settings_button.setProperty("variant", "secondary")
        self._prepare_action_button(self._advanced_settings_button, min_width=170, height=44)
        self._advanced_settings_button.clicked.connect(self._open_advanced_settings_dialog)
        button_grid.addWidget(self._advanced_settings_button, 0, 1)
        self._plan_button = QtWidgets.QPushButton()
        self._plan_button.setProperty("variant", "primaryAction")
        self._prepare_action_button(self._plan_button, min_width=190, height=44)
        self._plan_button.clicked.connect(self.run_planner)
        button_grid.addWidget(self._plan_button, 0, 2)
        self._reload_button = QtWidgets.QPushButton()
        self._reload_button.setProperty("variant", "secondary")
        self._prepare_action_button(self._reload_button, min_width=170, height=42)
        self._reload_button.clicked.connect(self.refresh_from_workspace)
        button_grid.addWidget(self._reload_button, 1, 0)
        self._save_button = QtWidgets.QPushButton()
        self._save_button.setProperty("variant", "secondary")
        self._prepare_action_button(self._save_button, min_width=170, height=42)
        self._save_button.clicked.connect(self.save_config)
        button_grid.addWidget(self._save_button, 1, 1)
        self._find_feasible_q_button = QtWidgets.QPushButton()
        self._find_feasible_q_button.setProperty("variant", "secondary")
        self._prepare_action_button(self._find_feasible_q_button, min_width=190, height=42)
        self._find_feasible_q_button.clicked.connect(self.find_feasible_q_sequences)
        button_grid.addWidget(self._find_feasible_q_button, 1, 2)
        for column in range(3):
            button_grid.setColumnStretch(column, 1)
            button_grid.setColumnMinimumWidth(column, 170)
        button_layout.addLayout(button_grid)

        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("等待计算")
        self._progress_bar.hide()
        button_layout.addWidget(self._progress_bar)
        return button_card

    @staticmethod
    def _prepare_action_button(button: QtWidgets.QPushButton, *, min_width: int, height: int) -> None:
        button.setMinimumHeight(height)
        button.setMinimumWidth(min_width)
        button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

    def _build_config_overview_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card.setMinimumHeight(194)
        card.setMaximumHeight(218)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        self._config_overview_header_label = QtWidgets.QLabel("当前配置")
        self._config_overview_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._config_overview_header_label)
        self._config_overview_table = QtWidgets.QTableWidget(0, 2)
        self._setup_readonly_table(self._config_overview_table)
        self._config_overview_table.horizontalHeader().setStretchLastSection(True)
        self._config_overview_table.setMinimumHeight(128)
        self._config_overview_table.setMaximumHeight(150)
        layout.addWidget(self._config_overview_table)
        return card

    def _build_continuous_thrust_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card.setMinimumHeight(280)
        card.setMaximumHeight(310)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header_row = QtWidgets.QHBoxLayout()
        self._continuous_thrust_header_label = QtWidgets.QLabel("连续推力模型参数")
        self._continuous_thrust_header_label.setProperty("role", "cardTitle")
        header_row.addWidget(self._continuous_thrust_header_label)
        header_row.addStretch(1)
        layout.addLayout(header_row)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addStretch(1)
        self._continuous_thrust_button = QtWidgets.QPushButton("优化连续推力模型参数")
        self._continuous_thrust_button.setProperty("variant", "secondary")
        self._prepare_action_button(self._continuous_thrust_button, min_width=230, height=40)
        self._continuous_thrust_button.clicked.connect(self.run_continuous_thrust_optimization)
        action_row.addWidget(self._continuous_thrust_button)
        self._export_continuous_strategy_button = QtWidgets.QPushButton("导出变轨策略")
        self._export_continuous_strategy_button.setProperty("variant", "secondary")
        self._prepare_action_button(self._export_continuous_strategy_button, min_width=150, height=40)
        self._export_continuous_strategy_button.clicked.connect(self.export_continuous_thrust_strategy)
        action_row.addWidget(self._export_continuous_strategy_button)
        layout.addLayout(action_row)

        self._continuous_thrust_hint_label = QtWidgets.QLabel("变量：点火开始时间 t、偏航角 δ；步长 10s / 0.05deg")
        self._continuous_thrust_hint_label.setProperty("role", "cardCaption")
        self._continuous_thrust_hint_label.setWordWrap(True)
        layout.addWidget(self._continuous_thrust_hint_label)

        self._continuous_thrust_table = QtWidgets.QTableWidget(0, 16)
        self._setup_readonly_table(self._continuous_thrust_table)
        self._continuous_thrust_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._continuous_thrust_table.horizontalHeader().setStretchLastSection(True)
        self._continuous_thrust_table.verticalHeader().setDefaultSectionSize(22)
        self._continuous_thrust_table.setMaximumHeight(146)
        layout.addWidget(self._continuous_thrust_table)
        return card

    def _build_result_panel(self) -> QtWidgets.QWidget:
        burn_card = QtWidgets.QFrame()
        self._result_panel = burn_card
        burn_card.setProperty("role", "card")
        burn_layout = QtWidgets.QVBoxLayout(burn_card)
        burn_layout.setContentsMargins(14, 10, 14, 10)
        burn_layout.setSpacing(6)
        self._burn_header_label = QtWidgets.QLabel()
        self._burn_header_label.setProperty("role", "cardTitle")
        burn_layout.addWidget(self._burn_header_label)
        self._burn_table = QtWidgets.QTableWidget(0, 16)
        self._setup_readonly_table(self._burn_table)
        self._burn_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._burn_table.verticalHeader().setDefaultSectionSize(24)
        self._burn_table.setMinimumHeight(150)
        self._burn_table.setMaximumHeight(210)
        burn_layout.addWidget(self._burn_table)
        burn_layout.addWidget(self._build_perigee_target_controls())
        burn_layout.addWidget(self._build_continuous_thrust_card())
        return burn_card

    def _build_perigee_target_controls(self) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(10)

        self._mv1_hp_target_label = QtWidgets.QLabel("第一次目标近地点高度/km")
        self._mv1_hp_target_label.setProperty("role", "cardCaption")
        row.addWidget(self._mv1_hp_target_label)
        self._mv1_hp_target_edit = QtWidgets.QLineEdit()
        self._mv1_hp_target_edit.setPlaceholderText("不约束")
        self._mv1_hp_target_edit.setMinimumHeight(42)
        self._mv1_hp_target_edit.setMinimumWidth(96)
        self._mv1_hp_target_edit.setValidator(QtGui.QDoubleValidator(0.0, 1.0e7, 6, self))
        self._mv1_hp_target_edit.returnPressed.connect(self._apply_perigee_target_constraints)
        row.addWidget(self._mv1_hp_target_edit, 1)

        self._mv2_hp_target_label = QtWidgets.QLabel("第二次目标近地点高度/km")
        self._mv2_hp_target_label.setProperty("role", "cardCaption")
        row.addWidget(self._mv2_hp_target_label)
        self._mv2_hp_target_edit = QtWidgets.QLineEdit()
        self._mv2_hp_target_edit.setPlaceholderText("不约束")
        self._mv2_hp_target_edit.setMinimumHeight(42)
        self._mv2_hp_target_edit.setMinimumWidth(96)
        self._mv2_hp_target_edit.setValidator(QtGui.QDoubleValidator(0.0, 1.0e7, 6, self))
        self._mv2_hp_target_edit.returnPressed.connect(self._apply_perigee_target_constraints)
        row.addWidget(self._mv2_hp_target_edit, 1)

        self._q_sequence_user_label = QtWidgets.QLabel("q 序列")
        self._q_sequence_user_label.setProperty("role", "cardCaption")
        row.addWidget(self._q_sequence_user_label)

        self._q_sequence_combo = _ArrowNoWheelComboBox()
        self._q_sequence_combo.setObjectName("designManeuverQSequenceCombo")
        self._q_sequence_combo.setMinimumHeight(42)
        self._q_sequence_combo.setMinimumWidth(108)
        self._q_sequence_combo.setStyleSheet(
            """
            QComboBox#designManeuverQSequenceCombo {
                padding-right: 28px;
            }
            QComboBox#designManeuverQSequenceCombo::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border-left: 1px solid #25586a;
            }
            QComboBox#designManeuverQSequenceCombo::down-arrow {
                image: none;
            }
            """
        )
        self._q_sequence_combo.addItem("", None)
        row.addWidget(self._q_sequence_combo, 1)

        self._apply_hp_targets_button = QtWidgets.QPushButton("应用并重算")
        self._apply_hp_targets_button.setProperty("variant", "primaryAction")
        self._prepare_action_button(self._apply_hp_targets_button, min_width=150, height=44)
        self._apply_hp_targets_button.clicked.connect(self._apply_perigee_target_constraints)
        row.addWidget(self._apply_hp_targets_button)
        return holder

    @classmethod
    def _engine_burn_specs(cls) -> tuple[_NumberSpec, ...]:
        return tuple(spec for spec in cls._NUMBER_SPECS if spec.section in {"engine", "burn_limit"})

    @classmethod
    def _basic_dialog_cards(cls) -> tuple[_DialogCardSpec, ...]:
        engine_burn = cls._engine_burn_specs()
        return (
            _DialogCardSpec(
                "初始轨道",
                number_specs=tuple(spec for spec in cls._NUMBER_SPECS if spec.section == "initial"),
                include_epoch=True,
            ),
            _DialogCardSpec(
                "目标轨道",
                number_specs=tuple(
                    spec
                    for spec in cls._NUMBER_SPECS
                    if spec.section == "target" and spec.key != "dv_lon_margin_mps"
                ),
            ),
            _DialogCardSpec(
                "规划设置",
                number_specs=tuple(
                    spec
                    for spec in cls._NUMBER_SPECS
                    if (spec.section, spec.key) == ("maneuver_count", "user")
                ),
            ),
            _DialogCardSpec("发动机与点火约束", number_specs=engine_burn[:7]),
        )

    @classmethod
    def _advanced_dialog_cards(cls) -> tuple[_DialogCardSpec, ...]:
        engine_burn = cls._engine_burn_specs()
        return (
            _DialogCardSpec(
                "发动机与点火约束高级项",
                number_specs=engine_burn[7:],
                check_specs=tuple(
                    spec for spec in cls._CHECK_SPECS if (spec.section, spec.key) in {
                        ("engine", "use_settling"),
                        ("burn_limit", "include_settling_in_burn_time"),
                    }
                ),
            ),
            _DialogCardSpec(
                "地球模型",
                number_specs=tuple(spec for spec in cls._NUMBER_SPECS if spec.section == "earth"),
                check_specs=tuple(spec for spec in cls._CHECK_SPECS if (spec.section, spec.key) == ("earth", "use_J2")),
            ),
            _DialogCardSpec(
                "轨道类型与变轨次数",
                number_specs=tuple(
                    spec
                    for spec in cls._NUMBER_SPECS
                    if spec.section == "orbit_type"
                    or (spec.section == "maneuver_count" and spec.key != "user")
                ),
                check_specs=tuple(
                    spec for spec in cls._CHECK_SPECS if spec.section == "planner"
                ),
                combo_specs=(
                    _ComboSpec(
                        "orbit_type",
                        "mode",
                        "轨道类型模式",
                        (
                            ("auto", "auto"),
                            ("supersynchronous_transfer", "supersynchronous_transfer"),
                            ("standard_transfer", "standard_transfer"),
                            ("general_transfer", "general_transfer"),
                        ),
                    ),
                ),
            ),
            _DialogCardSpec(
                "估算参数",
                number_specs=tuple(
                    spec
                    for spec in cls._NUMBER_SPECS
                    if (spec.section, spec.key) == ("target", "dv_lon_margin_mps")
                ),
            ),
            _DialogCardSpec(
                "经度窗口与分配",
                number_specs=tuple(spec for spec in cls._NUMBER_SPECS if spec.section == "distribution"),
                pair_specs=cls._PAIR_SPECS[:3],
                check_specs=tuple(spec for spec in cls._CHECK_SPECS if spec.section == "distribution"),
                combo_specs=(
                    _ComboSpec(
                        "distribution",
                        "mode",
                        "分配模式",
                        (
                            ("auto", "auto"),
                            ("uniform_all", "uniform_all"),
                            ("uniform_front_fixed_tail", "uniform_front_fixed_tail"),
                            ("weighted_uniform", "weighted_uniform"),
                        ),
                    ),
                ),
            ),
            _DialogCardSpec(
                "超同步尾段与方向角",
                number_specs=tuple(
                    spec
                    for spec in cls._NUMBER_SPECS
                    if spec.section in {"supersynchronous_transfer", "apsis", "alpha", "terminal_tolerance"}
                ),
                pair_specs=cls._PAIR_SPECS[3:],
                check_specs=tuple(
                    spec for spec in cls._CHECK_SPECS if spec.section in {"supersynchronous_transfer", "alpha"}
                ),
                combo_specs=(
                    _ComboSpec(
                        "supersynchronous_transfer",
                        "tail_control_mode",
                        "尾段控制模式",
                        (
                            ("fixed_post_a", "fixed_post_a"),
                            ("fixed_delta_v", "fixed_delta_v"),
                        ),
                    ),
                    _ComboSpec(
                        "apsis",
                        "pattern_mode",
                        "拱点序列模式",
                        (
                            ("auto", "auto"),
                            ("user", "user"),
                        ),
                    ),
                ),
            ),
            _DialogCardSpec(
                "V5.1 硬约束用户指定项",
                number_specs=tuple(spec for spec in cls._NUMBER_SPECS if spec.section == "hard_constraint_planner"),
                check_specs=tuple(spec for spec in cls._CHECK_SPECS if spec.section == "hard_constraint_planner"),
                text_specs=(
                    _TextSpec("hard_constraint_planner", "q_AA_user", "远地点间 q 序列", "空=自动搜索"),
                    _TextSpec("hard_constraint_planner", "q_AP_user", "终端 A-P q", "空=搜索候选"),
                    _TextSpec("hard_constraint_planner", "q_AP_candidates", "终端 A-P q 候选", "0,1,2"),
                    _TextSpec("hard_constraint_planner", "fixed_hp_targets_km", "指定控后近地点高度/km", "空=自由优化"),
                ),
            ),
        )

    def _open_parameter_config_dialog(self) -> None:
        dialog = _DesignManeuverSettingsDialog(
            self._i18n.t("design_maneuver.parameter_config_dialog.title"),
            self.config(),
            self._basic_dialog_cards(),
            self,
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._accept_dialog_config(dialog.config())

    def _open_advanced_settings_dialog(self) -> None:
        dialog = _DesignManeuverSettingsDialog(
            self._i18n.t("design_maneuver.advanced_settings_dialog.title"),
            self.config(),
            self._advanced_dialog_cards(),
            self,
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._accept_dialog_config(dialog.config())

    def _accept_dialog_config(self, config: dict[str, Any]) -> None:
        self._config = normalize_design_maneuver_strategy_payload(config)
        self._refresh_config_overview()
        self._sync_perigee_target_fields(self._config)
        self._clear_results()
        self.config_changed.emit(self._config)
        self._set_status("statusReady", self._i18n.t("design_maneuver.status.config_updated"))

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
        archived_loaded = self._load_archived_result()
        if archived_loaded is True:
            return
        elif archived_loaded is False:
            self._set_status("statusReady", self._i18n.t("design_maneuver.status.loaded"))

    def save_config(self) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.no_project"))
            return None
        try:
            self._config = self.config()
        except Exception as exc:
            self._set_status("statusDisconnected", f"配置格式错误: {exc}")
            return None
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
        if self._planning_busy:
            return
        self._set_planning_busy(True, "正在计算脉冲规划...")
        try:
            if self.save_config() is None:
                return
            result = plan_design_maneuver_strategy(self._config)
            self._set_result(result)
            try:
                path = self._workspace.save_design_maneuver_results(result)
            except Exception as exc:
                self._set_status(
                    "statusDisconnected",
                    self._i18n.t("design_maneuver.status.result_save_failed", error=str(exc)),
                )
                return
            self._set_constraint_status(result.checks)
        except Exception as exc:
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.plan_failed", error=str(exc)))
        finally:
            self._set_planning_busy(False)

    def find_feasible_q_sequences(self) -> None:
        if self._planning_busy:
            return
        self._set_planning_busy(True, "正在查找全部可行 q 序列...")
        try:
            if self.save_config() is None:
                return
            candidates = service_find_feasible_q_sequences(self._config)
            self._set_q_candidate_rows_from_candidates(candidates)
            self._set_status("statusReady", f"可行 q 序列查找完成，共 {len(candidates)} 组。")
        except Exception as exc:
            self._set_status("statusDisconnected", f"可行 q 序列查找失败：{exc}")
        finally:
            self._set_planning_busy(False)

    def run_continuous_thrust_optimization(self) -> None:
        if self._planning_busy:
            return
        if self._last_result is None:
            loaded = self._load_archived_result()
            if loaded is not True or self._last_result is None:
                self._set_status("statusDisconnected", "请先生成脉冲规划，再优化连续推力模型参数。")
                return
        self._set_planning_busy(True, "正在优化连续推力模型参数...")
        try:
            continuous_result = optimize_continuous_thrust_model_parameters(self._last_result)
            self._continuous_thrust_result = continuous_result
            self._set_continuous_thrust_rows(continuous_result)
            history_path = None
            import_strategy_path = None
            result_path = None
            if self._workspace.current_project is not None:
                result_path = self._workspace.save_design_continuous_thrust_results(
                    continuous_result,
                    pulse_result=self._last_result,
                )
                history_path = export_continuous_thrust_orbit_history_csv(
                    continuous_result,
                    self._workspace.data_dir() / "design_continuous_thrust_orbit_history.csv",
                )
                import_strategy_path = self._workspace.save_continuous_thrust_import_maneuver_strategy(
                    continuous_result,
                    self._last_result.config,
                )
            if continuous_result.hard_constraint_passed:
                self._set_status(
                    "statusReady",
                    f"连续推力参数优化完成，硬约束全部通过，ΔG={continuous_result.objective_delta_g_kg:.3f} kg"
                    + (f"，结果存档：{result_path}" if result_path is not None else "")
                    + (f"，轨道数据：{history_path}" if history_path is not None else "")
                    + (f"，导入配置：{import_strategy_path}" if import_strategy_path is not None else ""),
                )
            else:
                self._set_status(
                    "statusDisconnected",
                    "连续推力参数优化后未通过硬约束："
                    + "、".join(continuous_result.failed_constraints)
                    + (f"；导入配置：{import_strategy_path}" if import_strategy_path is not None else ""),
                )
        except Exception as exc:
            self._set_status("statusDisconnected", f"连续推力参数优化失败：{exc}")
        finally:
            self._set_planning_busy(False)

    def export_continuous_thrust_strategy(self) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", self._i18n.t("design_maneuver.status.no_project"))
            return None
        if self._continuous_thrust_result is None:
            self._set_status("statusDisconnected", "请先优化连续推力模型参数，再导出变轨策略。")
            return None
        if self._last_result is None:
            self._set_status("statusDisconnected", "缺少脉冲规划配置，无法导出连续推力变轨策略。")
            return None
        try:
            output_path = export_continuous_thrust_maneuver_strategy_xlsx(
                self._continuous_thrust_result,
                self._last_result.config,
                self._workspace.data_dir() / "design_continuous_thrust_maneuver_strategy.xlsx",
            )
        except Exception as exc:
            self._set_status("statusDisconnected", f"连续推力变轨策略导出失败：{exc}")
            return None
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(output_path)))
        self._set_status("statusReady", f"连续推力变轨策略已导出：{output_path}")
        return output_path

    def config(self) -> dict[str, Any]:
        config = normalize_design_maneuver_strategy_payload(self._config)
        if hasattr(self, "_mv1_hp_target_edit"):
            config["hard_constraint_planner"]["fixed_hp_targets_km"] = self._perigee_target_constraints_from_fields()
            config["distribution"]["first_post_a_control_km"] = None
        config["planner"]["maneuver_count_user"] = int(config["maneuver_count"]["user"])
        return normalize_design_maneuver_strategy_payload(config)

    def _load_archived_result(self) -> bool | None:
        if self._workspace.current_project is None:
            return False
        try:
            result = self._workspace.load_design_maneuver_results(require_current_config=True)
        except Exception as exc:
            self._set_status(
                "statusDisconnected",
                self._i18n.t("design_maneuver.status.result_load_failed", error=str(exc)),
            )
            return None
        if result is None:
            return False
        self._set_result(result)
        self._load_archived_continuous_thrust_result()
        return True

    def _load_archived_continuous_thrust_result(self) -> bool | None:
        if self._workspace.current_project is None:
            return False
        try:
            result = self._workspace.load_design_continuous_thrust_results(pulse_result=self._last_result)
        except Exception as exc:
            self._set_status(
                "statusDisconnected",
                self._i18n.t("design_maneuver.status.continuous_result_load_failed", error=str(exc)),
            )
            return None
        if result is None:
            return False
        self._continuous_thrust_result = result
        self._set_continuous_thrust_rows(result)
        self._set_status("statusReady", self._i18n.t("design_maneuver.status.loaded_with_continuous_result"))
        return True

    def _apply_config_to_fields(self, config: dict[str, Any]) -> None:
        normalized = normalize_design_maneuver_strategy_payload(config)
        self._config = normalized
        self._refresh_config_overview()
        self._sync_perigee_target_fields(normalized)
        self._sync_q_sequence_field(normalized)

    def _set_result(self, result: DesignManeuverResult) -> None:
        self._config = result.config
        self._last_result = result
        self._continuous_thrust_result = None
        self._continuous_thrust_table.setRowCount(0)
        self._updating_burn_table = True
        self._burn_table.setRowCount(0)
        self._burn_table.insertRow(0)
        config = normalize_design_maneuver_strategy_payload(result.config)
        initial = config["initial"]
        earth = config["earth"]
        separation_longitude = initial_design_maneuver_subsatellite_longitude_deg_e(result.config)
        period_min = 2.0 * math.pi * math.sqrt(
            max(1.0, float(initial["a_km"]) ** 3 / float(earth["mu_km3_s2"]))
        ) / 60.0
        re_km = float(earth["Re_km"])
        self._set_row_values(
            self._burn_table,
            0,
            (
                "分离点",
                "0.00",
                "1",
                "近地点",
                f"{separation_longitude:.2f}",
                f"{float(initial['a_km']):.2f}",
                f"{period_min:.2f}",
                f"{float(initial['i_deg']):.2f}",
                f"{float(initial['e']):.6f}",
                "0.00",
                "0.00",
                "0.00",
                "0.00",
                f"{float(initial['m0_kg']):.2f}",
                f"{_perigee_altitude_km(float(initial['a_km']), float(initial['e']), re_km):.2f}",
                f"{_apogee_altitude_km(float(initial['a_km']), float(initial['e']), re_km):.2f}",
            ),
        )
        for burn in result.burns:
            row = self._burn_table.rowCount()
            self._burn_table.insertRow(row)
            values = (
                f"MV{burn.index}",
                f"{burn.elapsed_min:.2f}",
                str(burn.flight_revolution),
                burn.position_label or ("远地点" if burn.apsis == "A" else "近地点"),
                f"{burn.longitude_deg_e:.2f}",
                f"{burn.post_a_km:.2f}",
                f"{burn.orbit_period_min:.2f}",
                f"{burn.post_i_deg:.2f}",
                f"{burn.post_e:.6f}",
                f"{burn.delta_v_mps:.2f}",
                f"{burn.alpha_deg:.2f}",
                f"{burn.total_burn_time_min:.2f}",
                f"{burn.propellant_kg:.2f}",
                f"{burn.post_mass_kg:.2f}",
                f"{_perigee_altitude_km(burn.post_a_km, burn.post_e, re_km):.2f}",
                f"{_apogee_altitude_km(burn.post_a_km, burn.post_e, re_km):.2f}",
            )
            self._set_row_values(self._burn_table, row, values)
            for column in range(self._burn_table.columnCount()):
                item = self._burn_table.item(row, column)
                if item is not None:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        for column in range(self._burn_table.columnCount()):
            item = self._burn_table.item(0, column)
            if item is not None:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self._updating_burn_table = False
        self._sync_perigee_target_fields(config)
        self._sync_q_sequence_field(config)
        self._set_q_candidate_rows(result)
        self._set_constraint_status(result.checks)

    def _set_q_candidate_rows(self, result: DesignManeuverResult) -> None:
        diagnostics = result.summary.get("phase_diagnostics", {})
        candidates = diagnostics.get("feasible_q_sequences", []) if isinstance(diagnostics, dict) else []
        self._set_q_candidate_rows_from_candidates(candidates)

    def _set_constraint_status(self, checks: list[dict[str, Any]]) -> None:
        failed = [str(check.get("item", "")) for check in checks if not bool(check.get("passed"))]
        if failed:
            self._set_status("statusDisconnected", "未通过硬约束：" + "、".join(item for item in failed if item))
        else:
            self._set_status("statusReady", "硬约束全部通过")

    def _set_continuous_thrust_rows(self, result: ContinuousThrustOptimizationResult) -> None:
        self._continuous_thrust_table.setRowCount(0)
        config = normalize_design_maneuver_strategy_payload(self._last_result.config if self._last_result else self._config)
        re_km = float(config["earth"]["Re_km"])
        for parameter in result.parameters:
            row = self._continuous_thrust_table.rowCount()
            self._continuous_thrust_table.insertRow(row)
            self._set_row_values(
                self._continuous_thrust_table,
                row,
                (
                    f"MV{parameter.maneuver_index} / {parameter.flight_revolution} / {parameter.optimization_mode}",
                    parameter.position_label,
                    f"{parameter.burn_start_min:.2f}",
                    f"{parameter.cutoff_min:.2f}",
                    f"{parameter.total_burn_time_min:.2f}",
                    f"{parameter.ignition_longitude_deg_e:.2f}",
                    f"{parameter.cutoff_longitude_deg_e:.2f}",
                    f"{parameter.post_i_deg:.2f}",
                    f"{parameter.post_e:.6f}",
                    f"{parameter.post_a_km:.2f}",
                    f"{parameter.yaw_angle_deg:.2f}",
                    f"{parameter.delta_v_mps:.2f}",
                    f"{parameter.propellant_kg:.2f}",
                    f"{parameter.post_mass_kg:.2f}",
                    f"{_perigee_altitude_km(parameter.post_a_km, parameter.post_e, re_km):.2f}",
                    f"{_apogee_altitude_km(parameter.post_a_km, parameter.post_e, re_km):.2f}",
                ),
            )
            formula_item = self._continuous_thrust_table.item(row, 12)
            if formula_item is not None:
                formula_item.setToolTip(
                    (
                        f"{parameter.objective_formula}; "
                        f"m={parameter.propellant_kg:.3f}, "
                        f"mA={parameter.future_apogee_raise_propellant_kg:.3f}, "
                        f"mP={parameter.future_perigee_lower_propellant_kg:.3f}; "
                        f"ΔG={parameter.objective_delta_g_kg:.3f} kg; "
                        f"模式={parameter.optimization_mode}; "
                        f"初值 t={parameter.initial_burn_start_min:.2f} min, "
                        f"δ={parameter.initial_yaw_angle_deg:.2f} deg; "
                        f"评估 {parameter.search_evaluations} 组"
                    )
                )

    def _set_q_candidate_rows_from_candidates(self, candidates: Any) -> None:
        if not hasattr(self, "_q_sequence_combo"):
            return
        previous = self._current_q_sequence_text()
        blocker = QtCore.QSignalBlocker(self._q_sequence_combo)
        self._q_sequence_combo.clear()
        self._q_sequence_combo.addItem("", None)
        if not isinstance(candidates, list):
            del blocker
            return
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            q_sequence = candidate.get("q_sequence", [])
            if not isinstance(q_sequence, (list, tuple)):
                continue
            q_text = ",".join(str(int(value)) for value in q_sequence)
            if q_text in seen:
                continue
            seen.add(q_text)
            hp_targets = candidate.get("hp_targets_km", [])
            hp_text = ""
            if isinstance(hp_targets, (list, tuple)):
                hp_text = ", ".join(f"{float(value):.0f}" for value in hp_targets)
            self._q_sequence_combo.addItem(q_text, q_text)
            index = self._q_sequence_combo.count() - 1
            self._q_sequence_combo.setItemData(
                index,
                (
                    f"最大时长 {float(candidate.get('max_burn_duration_min', 0.0)):.2f} min；"
                    f"终端经度误差 {float(candidate.get('lon_error_deg', 0.0)):.5f} deg；"
                    f"目标近地点高度 {hp_text} km"
                ),
                QtCore.Qt.ItemDataRole.ToolTipRole,
            )
        if previous:
            index = self._q_sequence_combo.findText(previous)
            if index >= 0:
                self._q_sequence_combo.setCurrentIndex(index)
        del blocker

    def _sync_perigee_target_fields(self, config: dict[str, Any] | None = None) -> None:
        if not hasattr(self, "_mv1_hp_target_edit"):
            return
        normalized = normalize_design_maneuver_strategy_payload(config or self._config)
        fixed_hp = normalized["hard_constraint_planner"].get("fixed_hp_targets_km", {})
        self._mv1_hp_target_edit.setText(self._format_optional_hp_target(fixed_hp.get("1")))
        self._mv2_hp_target_edit.setText(self._format_optional_hp_target(fixed_hp.get("2")))

    def _sync_q_sequence_field(self, config: dict[str, Any] | None = None) -> None:
        if not hasattr(self, "_q_sequence_combo"):
            return
        normalized = normalize_design_maneuver_strategy_payload(config or self._config)
        hard_cfg = normalized["hard_constraint_planner"]
        blocker = QtCore.QSignalBlocker(self._q_sequence_combo)
        if str(normalized["apsis"].get("pattern_mode", "auto")) != "user":
            self._q_sequence_combo.setCurrentIndex(0)
            del blocker
            return
        q_aa = hard_cfg.get("q_AA_user", [])
        q_ap = hard_cfg.get("q_AP_user")
        if not q_aa or q_ap is None:
            self._q_sequence_combo.setCurrentIndex(0)
            del blocker
            return
        q_text = ",".join(str(int(value)) for value in [*q_aa, int(q_ap)])
        index = self._q_sequence_combo.findText(q_text)
        if index < 0:
            self._q_sequence_combo.addItem(q_text, q_text)
            index = self._q_sequence_combo.count() - 1
        self._q_sequence_combo.setCurrentIndex(index)
        del blocker

    @staticmethod
    def _format_optional_hp_target(value: Any) -> str:
        if value in (None, ""):
            return ""
        return f"{float(value):g}"

    def _perigee_target_constraints_from_fields(self) -> dict[str, float]:
        fixed_hp: dict[str, float] = {}
        for key, field in (("1", self._mv1_hp_target_edit), ("2", self._mv2_hp_target_edit)):
            text = field.text().strip()
            if not text:
                continue
            try:
                hp_km = float(text)
            except ValueError:
                raise ValueError("目标近地点高度必须是数字。") from None
            if hp_km <= 0.0:
                raise ValueError("目标近地点高度必须大于 0。")
            fixed_hp[key] = hp_km
        return fixed_hp

    def _apply_perigee_target_constraints(self) -> None:
        if self._planning_busy:
            return
        try:
            fixed_hp = self._perigee_target_constraints_from_fields()
            q_values = self._selected_q_sequence_values()
        except ValueError as exc:
            self._set_status("statusDisconnected", str(exc))
            return
        config = self.config()
        config["hard_constraint_planner"]["fixed_hp_targets_km"] = fixed_hp
        if q_values:
            config["apsis"]["pattern_mode"] = "user"
            config["hard_constraint_planner"]["q_AA_user"] = q_values[:-1]
            config["hard_constraint_planner"]["q_AP_user"] = q_values[-1]
        else:
            config["apsis"]["pattern_mode"] = "auto"
            config["hard_constraint_planner"]["q_AA_user"] = []
            config["hard_constraint_planner"]["q_AP_user"] = None
        config["distribution"]["first_post_a_control_km"] = None
        self._config = normalize_design_maneuver_strategy_payload(config)
        self._refresh_config_overview()
        self._sync_perigee_target_fields(self._config)
        self._sync_q_sequence_field(self._config)
        self.config_changed.emit(self._config)
        self.run_planner()

    def _current_q_sequence_text(self) -> str:
        if not hasattr(self, "_q_sequence_combo"):
            return ""
        data = self._q_sequence_combo.currentData()
        return str(data or self._q_sequence_combo.currentText()).strip()

    def _selected_q_sequence_values(self) -> list[int]:
        text = self._current_q_sequence_text()
        if not text:
            return []
        values: list[int] = []
        for chunk in text.replace(";", ",").split(","):
            item = chunk.strip()
            if not item:
                continue
            try:
                values.append(int(float(item)))
            except ValueError:
                raise ValueError("q 序列必须是逗号分隔整数。") from None
        if len(values) < 2:
            raise ValueError("q 序列至少包含一个 A-A q 和一个终端 A-P q。")
        if any(value < 1 for value in values[:-1]) or values[-1] < 0:
            raise ValueError("A-A q 必须大于等于 1，终端 A-P q 必须大于等于 0。")
        return values

    def _set_planning_busy(self, busy: bool, message: str = "") -> None:
        self._planning_busy = busy
        self._progress_bar.setVisible(busy)
        if busy:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setFormat(message or "正在计算...")
            self._set_status("statusReady", message or "正在计算...")
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(100)
            self._progress_bar.setFormat("计算完成")
            self._progress_bar.hide()
        for widget in (
            self._parameter_config_button,
            self._advanced_settings_button,
            self._reload_button,
            self._save_button,
            self._plan_button,
            self._find_feasible_q_button,
            self._continuous_thrust_button,
            self._export_continuous_strategy_button,
            self._burn_table,
            self._continuous_thrust_table,
            self._mv1_hp_target_edit,
            self._mv2_hp_target_edit,
            self._apply_hp_targets_button,
            self._q_sequence_combo,
        ):
            widget.setEnabled(not busy)
        if busy:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def _clear_results(self) -> None:
        self._updating_burn_table = True
        self._last_result = None
        self._continuous_thrust_result = None
        self._burn_table.setRowCount(0)
        self._continuous_thrust_table.setRowCount(0)
        if hasattr(self, "_q_sequence_combo"):
            blocker = QtCore.QSignalBlocker(self._q_sequence_combo)
            self._q_sequence_combo.clear()
            self._q_sequence_combo.addItem("", None)
            del blocker
        self._updating_burn_table = False

    def _refresh_config_overview(self) -> None:
        config = normalize_design_maneuver_strategy_payload(self._config)
        hard_cfg = config["hard_constraint_planner"]
        q_sequence_text = (
            _format_config_text_value("hard_constraint_planner", "q_AA_user", hard_cfg["q_AA_user"])
            if str(config["apsis"].get("pattern_mode", "auto")) == "user"
            else "自动搜索"
        )
        rows = [
            ("初始历元", self._utc_to_qdatetime(str(config["initial"]["t0_epoch"])).toString("yyyy-MM-dd HH:mm:ss")),
            ("初始轨道", f"a {config['initial']['a_km']:.3f} km, e {config['initial']['e']:.6f}, i {config['initial']['i_deg']:.3f} deg"),
            ("目标轨道", f"a {config['target']['a_km']:.3f} km, e {config['target']['e']:.6f}, i {config['target']['i_deg']:.3f} deg"),
            ("目标经度", f"{config['target']['lon_degE']:.3f} degE"),
            ("发动机", f"{config['engine']['F_main_N']:.3f} N / {config['engine']['Isp_main_s']:.3f} s"),
            ("点火上限", f"{config['burn_limit']['max_total_burn_time_min']:.3f} min"),
            ("次数设置", f"min {config['maneuver_count']['min']}, max {config['maneuver_count']['max']}, user {config['maneuver_count']['user']}"),
            ("经度窗口", f"{config['longitude']['planning_window_degE'][0]:.3f} - {config['longitude']['planning_window_degE'][1]:.3f} degE"),
            ("V5.1 q 序列", q_sequence_text),
            (
                "V5.1 A-P q",
                str(hard_cfg["q_AP_user"])
                if hard_cfg["q_AP_user"] is not None
                else f"候选 {_format_config_text_value('hard_constraint_planner', 'q_AP_candidates', hard_cfg['q_AP_candidates'])}",
            ),
            (
                "V5.1 近地点目标",
                _format_config_text_value(
                    "hard_constraint_planner",
                    "fixed_hp_targets_km",
                    hard_cfg["fixed_hp_targets_km"],
                ),
            ),
        ]
        self._set_two_column_rows(self._config_overview_table, rows[:4])

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
        self._config = self.config()
        self.config_changed.emit(self._config)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._parameter_config_button,
            self._advanced_settings_button,
            self._reload_button,
            self._save_button,
            self._plan_button,
            self._find_feasible_q_button,
            self._continuous_thrust_button,
            self._export_continuous_strategy_button,
            self._mv1_hp_target_edit,
            self._mv2_hp_target_edit,
            self._apply_hp_targets_button,
            self._q_sequence_combo,
            self._continuous_thrust_table,
        ):
            widget.setEnabled(enabled)

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
        self._config_overview_header_label.setText(t("design_maneuver.config_overview_header"))
        self._parameter_config_button.setText(t("design_maneuver.parameter_config_button"))
        self._advanced_settings_button.setText(t("design_maneuver.advanced_settings_button"))
        self._reload_button.setText(f"+  {t('design_maneuver.reload_button')}")
        self._save_button.setText(t("design_maneuver.save_button"))
        self._plan_button.setText(t("design_maneuver.plan_button"))
        self._find_feasible_q_button.setText("查找全部可行q")
        self._continuous_thrust_header_label.setText("连续推力模型参数")
        self._continuous_thrust_button.setText("优化连续推力模型参数")
        self._export_continuous_strategy_button.setText("导出变轨策略")
        self._continuous_thrust_hint_label.setText("变量：点火开始时间 t、偏航角 δ；步长 10s / 0.05deg")
        self._burn_header_label.setText(t("design_maneuver.burn_header"))
        self._mv1_hp_target_label.setText("第一次目标近地点高度/km")
        self._mv2_hp_target_label.setText("第二次目标近地点高度/km")
        self._mv1_hp_target_edit.setPlaceholderText("不约束")
        self._mv2_hp_target_edit.setPlaceholderText("不约束")
        self._apply_hp_targets_button.setText("应用并重算")
        self._q_sequence_user_label.setText("q 序列")
        self._config_overview_table.setHorizontalHeaderLabels(["项目", "数值"])
        self._continuous_thrust_table.setHorizontalHeaderLabels(
            [
                "变轨/飞行圈次",
                "位置",
                "点火开始点航时/min",
                "点火结束点航时/min",
                "点火总时长/min",
                "点火开始点经度/degE",
                "点火结束点经度/degE",
                "点火结束点倾角/deg",
                "点火结束点偏心率",
                "控后半长轴/km",
                "偏航角/deg",
                "总速度增量/(m/s)",
                "总推进剂消耗/kg",
                "控后卫星质量/kg",
                "控后近地点高度/km",
                "控后远地点高度/km",
            ]
        )
        self._burn_table.setHorizontalHeaderLabels(
            [
                "",
                "航时",
                "飞行圈次",
                "位置",
                "星下点经度/degE",
                "控后半长轴/km",
                "轨道周期/min",
                "轨道倾角/deg",
                "控后偏心率",
                "速度增量/(m/s)",
                "计算的变轨推力偏航角/deg",
                "点火时长/min",
                "推进剂消耗/kg",
                "控后卫星质量/kg",
                "控后近地点高度/km",
                "控后远地点高度/km",
            ]
        )
        self._refresh_config_path_label()

    @staticmethod
    def _setup_readonly_table(table: QtWidgets.QTableWidget) -> None:
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(26)
        table.horizontalHeader().setFixedHeight(30)
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


class _DesignManeuverSettingsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str,
        config: dict[str, Any],
        cards: tuple[_DialogCardSpec, ...],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = normalize_design_maneuver_strategy_payload(config)
        self._cards = cards
        self._number_fields: dict[tuple[str, str], QtWidgets.QAbstractSpinBox] = {}
        self._pair_fields: dict[tuple[str, str], tuple[NoWheelDoubleSpinBox, NoWheelDoubleSpinBox]] = {}
        self._check_fields: dict[tuple[str, str], QtWidgets.QCheckBox] = {}
        self._combo_fields: dict[tuple[str, str], NoWheelComboBox] = {}
        self._text_fields: dict[tuple[str, str], QtWidgets.QLineEdit] = {}
        self._t0_epoch_field: NoWheelDateTimeEdit | None = None
        self._drag_position: QtCore.QPoint | None = None

        self.setObjectName("designManeuverSettingsDialog")
        self.setWindowTitle(title)
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        self.resize(940, 760)
        self.setMinimumSize(760, 620)
        self._apply_dialog_style()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(14)
        root.addWidget(self._title_bar(title))

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        body = QtWidgets.QVBoxLayout(canvas)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        for card in cards:
            body.addWidget(self._build_card(card))
        body.addStretch(1)
        root.addWidget(scroll, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        save_button.setText("▣  保存配置")
        cancel_button.setText("取消")
        save_button.setProperty("variant", "primaryAction")
        cancel_button.setProperty("variant", "secondary")
        save_button.setMinimumHeight(48)
        cancel_button.setMinimumHeight(48)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons, 0, QtCore.Qt.AlignmentFlag.AlignRight)

        self._apply_config_to_fields()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched in {self._dialog_title_bar, *self._dialog_title_bar.findChildren(QtWidgets.QLabel)}:
            if self._handle_drag_event(event):
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_drag_event(event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_drag_event(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_drag_event(event):
            return
        super().mouseReleaseEvent(event)

    def _handle_drag_event(self, event: QtCore.QEvent) -> bool:
        if not isinstance(event, QtGui.QMouseEvent):
            return False
        if event.type() == QtCore.QEvent.Type.MouseButtonPress:
            if event.button() != QtCore.Qt.MouseButton.LeftButton:
                return False
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return True
        if event.type() == QtCore.QEvent.Type.MouseMove and self._drag_position is not None:
            if not event.buttons() & QtCore.Qt.MouseButton.LeftButton:
                return False
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
            return True
        if event.type() == QtCore.QEvent.Type.MouseButtonRelease and self._drag_position is not None:
            self._drag_position = None
            event.accept()
            return True
        return False

    def config(self) -> dict[str, Any]:
        config = normalize_design_maneuver_strategy_payload(self._config)
        if self._t0_epoch_field is not None:
            config["initial"]["t0_epoch"] = DesignManeuverStrategyPage._datetime_edit_to_utc(self._t0_epoch_field)
        for (section, key), field in self._number_fields.items():
            value = field.value()
            config[section][key] = int(value) if isinstance(field, QtWidgets.QSpinBox) else float(value)
        for (section, key), (low, high) in self._pair_fields.items():
            config[section][key] = [float(low.value()), float(high.value())]
        for (section, key), checkbox in self._check_fields.items():
            config[section][key] = checkbox.isChecked()
        config["earth"]["use_J2"] = True
        for (section, key), combo in self._combo_fields.items():
            config[section][key] = str(combo.currentData())
        for (section, key), field in self._text_fields.items():
            config[section][key] = field.text().strip()
        config["planner"]["maneuver_count_user"] = int(config["maneuver_count"]["user"])
        return normalize_design_maneuver_strategy_payload(config)

    def _title_bar(self, title: str) -> QtWidgets.QWidget:
        self._dialog_title_bar = QtWidgets.QWidget()
        self._dialog_title_bar.setObjectName("dialogTitleBar")
        self._dialog_title_bar.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        row = QtWidgets.QHBoxLayout(self._dialog_title_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        icon = QtWidgets.QLabel("⌬")
        icon.setObjectName("dialogTitleIcon")
        icon.setFixedSize(28, 28)
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        label = QtWidgets.QLabel(title)
        label.setProperty("role", "pageTitle")
        row.addWidget(label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        close_button = QtWidgets.QToolButton()
        close_button.setObjectName("dialogCloseButton")
        close_button.setText("X")
        close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.reject)
        row.addWidget(close_button, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        for drag_widget in (self._dialog_title_bar, icon, label):
            drag_widget.installEventFilter(self)
        return self._dialog_title_bar

    def _build_card(self, spec: _DialogCardSpec) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        header = QtWidgets.QLabel(spec.title)
        header.setProperty("role", "cardTitle")
        layout.addWidget(header)
        grid = DesignManeuverStrategyPage._new_form_grid()
        row = 0
        for combo_spec in spec.combo_specs:
            self._add_combo(grid, row, combo_spec)
            row += 1
        for text_spec in spec.text_specs:
            self._add_text(grid, row, text_spec)
            row += 1
        if spec.include_epoch:
            label = QtWidgets.QLabel("初始历元 (北京时间)")
            label.setProperty("role", "cardCaption")
            self._t0_epoch_field = NoWheelDateTimeEdit()
            self._t0_epoch_field.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            self._t0_epoch_field.setTimeZone(DesignManeuverStrategyPage._beijing_qtimezone())
            self._t0_epoch_field.setCalendarPopup(True)
            self._t0_epoch_field.setMinimumHeight(40)
            grid.addWidget(label, row, 0)
            grid.addWidget(self._t0_epoch_field, row, 1)
            row += 1
        for number_spec in spec.number_specs:
            self._add_number(grid, row, number_spec)
            row += 1
        for section, key, label in spec.pair_specs:
            self._add_pair(grid, row, section, key, label)
            row += 1
        for check_spec in spec.check_specs:
            checkbox = QtWidgets.QCheckBox(check_spec.label)
            checkbox.setMinimumHeight(32)
            if (check_spec.section, check_spec.key) == ("earth", "use_J2"):
                checkbox.setChecked(True)
                checkbox.setEnabled(False)
                checkbox.setToolTip("J2 perturbation is mandatory for maneuver dynamics.")
            self._check_fields[(check_spec.section, check_spec.key)] = checkbox
            grid.addWidget(checkbox, row, 0, 1, 2)
            row += 1
        layout.addLayout(grid)
        return card

    def _add_number(self, grid: QtWidgets.QGridLayout, row: int, spec: _NumberSpec) -> None:
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
        field.setMinimumHeight(40)
        field.setMinimumWidth(190)
        self._number_fields[(spec.section, spec.key)] = field
        grid.addWidget(label, row, 0)
        grid.addWidget(field, row, 1)

    def _add_pair(self, grid: QtWidgets.QGridLayout, row: int, section: str, key: str, label_text: str) -> None:
        label = QtWidgets.QLabel(label_text)
        label.setProperty("role", "cardCaption")
        low = DesignManeuverStrategyPage._double_spin(-360.0, 360.0, 0.1, 6)
        high = DesignManeuverStrategyPage._double_spin(-360.0, 360.0, 0.1, 6)
        low.setMinimumHeight(40)
        high.setMinimumHeight(40)
        holder = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(holder)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(low)
        row_layout.addWidget(high)
        self._pair_fields[(section, key)] = (low, high)
        grid.addWidget(label, row, 0)
        grid.addWidget(holder, row, 1)

    def _add_combo(self, grid: QtWidgets.QGridLayout, row: int, spec: _ComboSpec) -> None:
        label = QtWidgets.QLabel(spec.label)
        label.setProperty("role", "cardCaption")
        combo = NoWheelComboBox()
        for text, data in spec.items:
            combo.addItem(text, data)
        combo.setMinimumHeight(40)
        combo.setMinimumWidth(190)
        combo.setMaxVisibleItems(max(2, len(spec.items)))
        self._combo_fields[(spec.section, spec.key)] = combo
        grid.addWidget(label, row, 0)
        grid.addWidget(combo, row, 1)

    def _add_text(self, grid: QtWidgets.QGridLayout, row: int, spec: _TextSpec) -> None:
        label = QtWidgets.QLabel(spec.label)
        label.setProperty("role", "cardCaption")
        field = QtWidgets.QLineEdit()
        field.setPlaceholderText(spec.placeholder)
        field.setMinimumHeight(40)
        field.setMinimumWidth(190)
        self._text_fields[(spec.section, spec.key)] = field
        grid.addWidget(label, row, 0)
        grid.addWidget(field, row, 1)

    def _apply_config_to_fields(self) -> None:
        config = self._config
        if self._t0_epoch_field is not None:
            self._t0_epoch_field.setDateTime(
                DesignManeuverStrategyPage._utc_to_qdatetime(str(config["initial"]["t0_epoch"]))
            )
        for (section, key), field in self._number_fields.items():
            value = config[section].get(key)
            if value is None:
                value = 0
            if isinstance(field, QtWidgets.QSpinBox):
                field.setValue(int(value))
            else:
                field.setValue(float(value))
        for (section, key), (low, high) in self._pair_fields.items():
            values = config[section][key]
            low.setValue(float(values[0]))
            high.setValue(float(values[1]))
        for (section, key), checkbox in self._check_fields.items():
            if (section, key) == ("earth", "use_J2"):
                checkbox.setChecked(True)
                checkbox.setEnabled(False)
            else:
                checkbox.setChecked(bool(config[section][key]))
        for (section, key), combo in self._combo_fields.items():
            DesignManeuverStrategyPage._set_combo_value(combo, str(config[section][key]))
        for (section, key), field in self._text_fields.items():
            field.setText(_format_config_text_value(section, key, config[section].get(key)))

    def _apply_dialog_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog#designManeuverSettingsDialog {
                background: qradialgradient(cx:0.50, cy:0.10, radius:1.15, fx:0.50, fy:0.10, stop:0 #0c2230, stop:0.50 #07131c, stop:1 #03090f);
                border: 1px solid #1c7d9a;
                border-radius: 22px;
            }
            QDialog#designManeuverSettingsDialog QWidget {
                background: transparent;
            }
            QDialog#designManeuverSettingsDialog QFrame[role="card"] {
                background: rgba(5, 17, 25, 0.62);
                border: 1px solid #1e7892;
                border-radius: 14px;
            }
            QDialog#designManeuverSettingsDialog QLabel[role="pageTitle"] {
                color: #f4fbff;
                font-size: 17pt;
                font-weight: 800;
            }
            QDialog#designManeuverSettingsDialog QLabel#dialogTitleIcon {
                background: rgba(19, 48, 63, 0.9);
                border: 1px solid #27677d;
                border-radius: 14px;
                color: #3bdcff;
                font-size: 13pt;
                font-weight: 700;
            }
            QDialog#designManeuverSettingsDialog QToolButton#dialogCloseButton {
                background: transparent;
                color: #c4d4dc;
                border: none;
                font-size: 18pt;
                font-weight: 300;
                padding: 2px 8px;
            }
            QDialog#designManeuverSettingsDialog QToolButton#dialogCloseButton:hover {
                color: #ffffff;
                background: rgba(59, 169, 198, 0.18);
                border-radius: 8px;
            }
            QDialog#designManeuverSettingsDialog QLabel[role="cardTitle"] {
                color: #f2fbff;
                font-size: 14pt;
                font-weight: 800;
            }
            QDialog#designManeuverSettingsDialog QLabel[role="cardCaption"] {
                color: #8fb0bb;
            }
            QDialog#designManeuverSettingsDialog QDoubleSpinBox,
            QDialog#designManeuverSettingsDialog QSpinBox,
            QDialog#designManeuverSettingsDialog QDateTimeEdit,
            QDialog#designManeuverSettingsDialog QLineEdit,
            QDialog#designManeuverSettingsDialog QComboBox {
                background: rgba(7, 19, 28, 0.98);
                border: 1px solid #2b6075;
                border-radius: 6px;
                padding: 8px 10px;
                color: #e6f6fb;
            }
            QDialog#designManeuverSettingsDialog QDoubleSpinBox:focus,
            QDialog#designManeuverSettingsDialog QSpinBox:focus,
            QDialog#designManeuverSettingsDialog QDateTimeEdit:focus,
            QDialog#designManeuverSettingsDialog QLineEdit:focus,
            QDialog#designManeuverSettingsDialog QComboBox:focus {
                border: 1px solid #62d8ea;
            }
            QDialog#designManeuverSettingsDialog QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left: 1px solid #25586a;
            }
            QDialog#designManeuverSettingsDialog QComboBox QAbstractItemView {
                background: #07141d;
                color: #f3fbff;
                border: 1px solid #1e7892;
                selection-background-color: #153e4d;
                outline: none;
            }
            QDialog#designManeuverSettingsDialog QCheckBox {
                color: #d7edf5;
                font-weight: 600;
            }
            QDialog#designManeuverSettingsDialog QPushButton[variant="secondary"] {
                min-width: 116px;
                border-radius: 7px;
                padding: 11px 18px;
            }
            QDialog#designManeuverSettingsDialog QPushButton[variant="primaryAction"] {
                min-width: 152px;
                border-radius: 7px;
                padding-left: 24px;
                padding-right: 24px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff9b35, stop:1 #ff5a22);
                border: 1px solid #ffbd6a;
                color: #ffffff;
                font-size: 12pt;
                font-weight: 800;
            }
            QDialog#designManeuverSettingsDialog QPushButton[variant="primaryAction"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffae53, stop:1 #ff6d35);
                border: 1px solid #ffd196;
            }
            QDialog#designManeuverSettingsDialog QPushButton[variant="primaryAction"]:pressed {
                background: #df4b1f;
            }
            """
        )
