from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

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


@dataclass(frozen=True, slots=True)
class _ComboSpec:
    section: str
    key: str
    label: str
    items: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _DialogCardSpec:
    title: str
    number_specs: tuple[_NumberSpec, ...] = ()
    pair_specs: tuple[tuple[str, str, str], ...] = ()
    check_specs: tuple[_CheckSpec, ...] = ()
    combo_specs: tuple[_ComboSpec, ...] = ()
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
        panel = QtWidgets.QWidget()
        self._config_panel = panel
        layout = QtWidgets.QVBoxLayout(panel)
        self._config_panel_layout = layout
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(self._build_config_overview_card())

        button_card = QtWidgets.QFrame()
        button_card.setProperty("role", "card")
        button_layout = QtWidgets.QVBoxLayout(button_card)
        button_layout.setContentsMargins(18, 18, 18, 18)
        button_layout.setSpacing(10)
        self._config_path_label = QtWidgets.QLabel()
        self._config_path_label.setProperty("role", "cardCaption")
        self._config_path_label.setWordWrap(True)
        button_layout.addWidget(self._config_path_label)

        self._parameter_config_button = QtWidgets.QPushButton()
        self._parameter_config_button.setProperty("variant", "primaryAction")
        self._parameter_config_button.clicked.connect(self._open_parameter_config_dialog)
        button_layout.addWidget(self._parameter_config_button)

        self._advanced_settings_button = QtWidgets.QPushButton()
        self._advanced_settings_button.setProperty("variant", "secondary")
        self._advanced_settings_button.clicked.connect(self._open_advanced_settings_dialog)
        button_layout.addWidget(self._advanced_settings_button)

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
        layout.addWidget(self._build_summary_card())
        return panel

    def _build_config_overview_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        self._config_overview_header_label = QtWidgets.QLabel("当前配置")
        self._config_overview_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._config_overview_header_label)
        self._config_overview_table = QtWidgets.QTableWidget(0, 2)
        self._setup_readonly_table(self._config_overview_table)
        self._config_overview_table.horizontalHeader().setStretchLastSection(True)
        self._config_overview_table.setMinimumHeight(220)
        layout.addWidget(self._config_overview_table)
        return card

    def _build_result_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        self._result_panel = panel
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

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
        self._burn_table.setMinimumHeight(180)
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
        self._check_table.setMinimumHeight(120)
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
        self._future_slot_label.setMinimumHeight(120)
        future_layout.addWidget(self._future_slot_label, 1)
        self._warning_label = QtWidgets.QLabel()
        self._warning_label.setProperty("role", "statusDisconnected")
        self._warning_label.setWordWrap(True)
        future_layout.addWidget(self._warning_label)
        bottom_row.addWidget(future_card, 2)
        layout.addLayout(bottom_row, 1)
        return panel

    def _build_summary_card(self) -> QtWidgets.QFrame:
        self._summary_card = QtWidgets.QFrame()
        self._summary_card.setProperty("role", "card")
        summary_layout = QtWidgets.QVBoxLayout(self._summary_card)
        summary_layout.setContentsMargins(18, 18, 18, 18)
        summary_layout.setSpacing(10)
        self._summary_header_label = QtWidgets.QLabel()
        self._summary_header_label.setProperty("role", "cardTitle")
        summary_layout.addWidget(self._summary_header_label)
        self._summary_table = QtWidgets.QTableWidget(0, 2)
        self._setup_readonly_table(self._summary_table)
        self._summary_table.horizontalHeader().setStretchLastSection(True)
        self._summary_table.setMinimumHeight(150)
        summary_layout.addWidget(self._summary_table)
        return self._summary_card

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
                number_specs=tuple(spec for spec in cls._NUMBER_SPECS if spec.section == "target"),
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
                    spec for spec in cls._NUMBER_SPECS if spec.section in {"orbit_type", "maneuver_count"}
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
        self._config = normalized
        self._refresh_config_overview()

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

    def _refresh_config_overview(self) -> None:
        config = normalize_design_maneuver_strategy_payload(self._config)
        rows = [
            ("初始历元", self._utc_to_qdatetime(str(config["initial"]["t0_epoch"])).toString("yyyy-MM-dd HH:mm:ss")),
            ("初始轨道", f"a {config['initial']['a_km']:.3f} km, e {config['initial']['e']:.6f}, i {config['initial']['i_deg']:.3f} deg"),
            ("目标轨道", f"a {config['target']['a_km']:.3f} km, e {config['target']['e']:.6f}, i {config['target']['i_deg']:.3f} deg"),
            ("目标经度", f"{config['target']['lon_degE']:.3f} degE"),
            ("发动机", f"{config['engine']['F_main_N']:.3f} N / {config['engine']['Isp_main_s']:.3f} s"),
            ("点火上限", f"{config['burn_limit']['max_total_burn_time_min']:.3f} min"),
            ("次数设置", f"min {config['maneuver_count']['min']}, max {config['maneuver_count']['max']}, user {config['maneuver_count']['user']}"),
            ("经度窗口", f"{config['longitude']['planning_window_degE'][0]:.3f} - {config['longitude']['planning_window_degE'][1]:.3f} degE"),
        ]
        self._set_two_column_rows(self._config_overview_table, rows)

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
            self._import_baseline_button,
            self._save_button,
            self._plan_button,
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
        self._import_baseline_button.setText(t("design_maneuver.load_baseline_button"))
        self._save_button.setText(t("design_maneuver.save_button"))
        self._plan_button.setText(t("design_maneuver.plan_button"))
        self._summary_header_label.setText(t("design_maneuver.summary_header"))
        self._burn_header_label.setText(t("design_maneuver.burn_header"))
        self._check_header_label.setText(t("design_maneuver.check_header"))
        self._future_header_label.setText(t("design_maneuver.future_header"))
        self._future_slot_label.setText(t("design_maneuver.future_placeholder"))
        self._config_overview_table.setHorizontalHeaderLabels(["项目", "数值"])
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
        for (section, key), combo in self._combo_fields.items():
            config[section][key] = str(combo.currentData())
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
            checkbox.setChecked(bool(config[section][key]))
        for (section, key), combo in self._combo_fields.items():
            DesignManeuverStrategyPage._set_combo_value(combo, str(config[section][key]))

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
