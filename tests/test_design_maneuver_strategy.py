from __future__ import annotations

import pytest

from PySide6 import QtCore, QtWidgets

import smart.services.design_maneuver_strategy as design_strategy
from smart.services.design_maneuver_strategy import (
    default_design_maneuver_strategy_payload,
    find_feasible_q_sequences,
    normalize_design_maneuver_strategy_payload,
    optimize_continuous_thrust_model_parameters,
    plan_design_maneuver_strategy,
)
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.nav_icons import has_icon
import smart.ui.widgets.design_maneuver_strategy_page as design_page_module
from smart.ui.widgets.design_maneuver_strategy_page import (
    DesignManeuverStrategyPage,
    _DesignManeuverSettingsDialog,
)


def test_supersynchronous_design_planner_outputs_fixed_tail() -> None:
    result = plan_design_maneuver_strategy(default_design_maneuver_strategy_payload())
    baseline_payload = default_design_maneuver_strategy_payload()
    baseline_payload["alpha"]["optimize_alpha"] = False
    baseline = plan_design_maneuver_strategy(baseline_payload)

    assert result.summary["orbit_type"] == "supersynchronous_transfer"
    assert result.config["terminal_tolerance"]["lon_deg"] == pytest.approx(0.01)
    assert result.summary["actual_count"] == result.summary["recommended_count"]
    assert result.summary["actual_count"] == 5
    assert result.summary["estimated_total_delta_v_mps"] == pytest.approx(1539.0)
    assert result.summary["design_single_burn_delta_v_mps"] == pytest.approx(312.123864, rel=1e-6)
    assert result.summary["apsis_pattern"].endswith("A,P")
    assert len(result.burns) == result.summary["actual_count"]
    assert result.burns[-2].burn_type == "terminal_apogee"
    assert result.burns[-1].burn_type == "terminal_perigee"
    assert result.burns[-2].target_post_a_km == pytest.approx(47271.168509, rel=1e-6)
    assert result.burns[-1].target_post_a_km == pytest.approx(42164.2)
    assert result.burns[-1].post_a_km == pytest.approx(42164.2)
    assert result.burns[0].elapsed_min == pytest.approx(1254.558372, rel=1e-6)
    assert result.burns[0].longitude_deg_e == pytest.approx(73.475631, rel=1e-6)
    assert result.summary["phase_diagnostics"]["optimizer_method"] == "V5.1 hard-constrained"
    assert result.summary["phase_diagnostics"]["hard_constraint_feasible"] is True
    assert result.summary["q_sequence"] == "3,3,3,0"
    assert result.summary["phase_optimized"] is True
    assert result.summary["phase_delta_v_optimized"] is True
    assert result.summary["phase_diagnostics"]["q_total_candidates"] >= 1
    assert result.summary["phase_diagnostics"]["q_tested_fast"] >= 1
    assert result.summary["phase_diagnostics"]["feasible_solutions"] >= 1
    feasible_q_sequences = result.summary["phase_diagnostics"]["feasible_q_sequences"]
    assert [3, 3, 3, 0] in [item["q_sequence"] for item in feasible_q_sequences]
    assert all("propellant_kg" not in item for item in feasible_q_sequences)
    if abs(baseline.summary["terminal_errors"]["i_deg"]) <= baseline.config["terminal_tolerance"]["i_deg"]:
        assert result.summary["optimized_propellant_kg"] <= baseline.summary["optimized_propellant_kg"]
    assert abs(result.summary["terminal_errors"]["i_deg"]) <= result.config["terminal_tolerance"]["i_deg"]
    assert abs(result.summary["terminal_errors"]["lon_deg"]) <= result.config["terminal_tolerance"]["lon_deg"]
    assert all(burn.alpha_deg >= 0.0 for burn in result.burns if burn.apsis == "A")
    assert result.checks[2]["requirement"] == "不限制"
    assert result.checks[-1]["item"] == "终端经度误差"
    assert all(0.0 <= burn.longitude_deg_e < 360.0 for burn in result.burns)
    assert result.checks


def test_continuous_thrust_parameter_optimizer_uses_pulse_targets() -> None:
    pulse_result = plan_design_maneuver_strategy(default_design_maneuver_strategy_payload())
    continuous_result = optimize_continuous_thrust_model_parameters(pulse_result)

    assert continuous_result.time_step_s == pytest.approx(10.0)
    assert continuous_result.yaw_step_deg == pytest.approx(0.05)
    assert continuous_result.hard_constraint_passed is True
    assert len(continuous_result.parameters) == len(pulse_result.burns)
    first = continuous_result.parameters[0]
    assert first.maneuver_index == pulse_result.burns[0].index
    assert first.yaw_angle_deg == pytest.approx(pulse_result.burns[0].alpha_deg)
    assert first.target_post_a_km == pytest.approx(
        pulse_result.burns[0].target_post_a_km or pulse_result.burns[0].post_a_km
    )
    assert first.cutoff_min > first.burn_start_min
    assert first.search_evaluations > 0
    assert first.objective_formula == "m + m1 + m2 + m3"
    assert continuous_result.parameters[-1].objective_formula == "m + m3"
    assert continuous_result.parameters[-1].cutoff_longitude_deg_e == pytest.approx(
        pulse_result.config["target"]["lon_degE"], abs=pulse_result.config["terminal_tolerance"]["lon_deg"]
    )
    assert continuous_result.objective_delta_g_kg >= continuous_result.total_propellant_kg


def test_feasible_q_scan_ignores_current_user_q_constraint() -> None:
    payload = default_design_maneuver_strategy_payload()
    payload["apsis"]["pattern_mode"] = "user"
    payload["hard_constraint_planner"]["q_AA_user"] = [3, 3, 3]
    payload["hard_constraint_planner"]["q_AP_user"] = 0
    payload["maneuver_count"]["user"] = 5
    payload["planner"]["maneuver_count_user"] = 5

    feasible = find_feasible_q_sequences(payload)
    q_sequences = [item["q_sequence"] for item in feasible]

    assert [3, 3, 2, 0] in q_sequences
    assert [3, 3, 3, 0] in q_sequences


def test_design_planner_phase_q_search_hits_f4_terminal_longitude() -> None:
    payload = default_design_maneuver_strategy_payload()
    payload["initial"].update(
        {
            "t0_epoch": "2026-05-14T13:09:19Z",
            "m0_kg": 5200.0,
            "e": 0.77684692,
            "mean_anomaly_deg": 1.85437,
        }
    )
    payload["maneuver_count"].update({"max": 5, "user": 5, "total_dv_est_user_mps": 0.0})
    payload["planner"].update({"maneuver_count_user": 5, "force_user_count": True})
    payload["supersynchronous_transfer"].update({"dv_tail_apogee_fixed_mps": 0.0, "dv_tail_perigee_fixed_mps": 0.0})

    result = plan_design_maneuver_strategy(payload)
    assert result.summary["phase_optimized"] is True
    assert result.summary["phase_delta_v_optimized"] is True
    assert result.summary["phase_alpha_optimized"] is True
    assert result.summary["phase_diagnostics"]["q_total_candidates"] == 81
    assert result.summary["phase_diagnostics"]["q_tested_fast"] <= result.summary["phase_diagnostics"]["q_total_candidates"]
    assert result.summary["phase_diagnostics"]["q_tested_slsqp"] == 0
    assert result.summary["phase_diagnostics"]["optimizer_method"] == "V5.1 hard-constrained"
    assert result.summary["phase_diagnostics"]["hard_constraint_feasible"] is True
    assert result.burns[-1].apsis == "P"
    assert abs(result.burns[-1].alpha_deg) > 170.0
    assert all(burn.alpha_deg >= 0.0 for burn in result.burns if burn.apsis == "A")
    assert result.summary["optimized_propellant_kg"] > 0.0
    assert max(burn.total_burn_time_min for burn in result.burns) <= result.config["burn_limit"]["max_total_burn_time_min"]
    assert abs(result.summary["terminal_errors"]["lon_deg"]) <= result.config["terminal_tolerance"]["lon_deg"]
    assert result.checks[-1]["passed"] is True

    payload["distribution"]["first_post_a_control_km"] = 1000.0
    manual_result = plan_design_maneuver_strategy(payload)

    assert manual_result.burns[0].semi_major_axis_control_km == pytest.approx(1000.0, abs=1.0e-6)
    assert manual_result.burns[0].post_a_km == pytest.approx(payload["initial"]["a_km"] + 1000.0, rel=1.0e-9)
    assert abs(manual_result.summary["terminal_errors"]["lon_deg"]) <= manual_result.config["terminal_tolerance"]["lon_deg"]


def test_v51_user_sequence_and_perigee_targets_drive_planner() -> None:
    payload = default_design_maneuver_strategy_payload()
    payload["apsis"]["pattern_mode"] = "user"
    payload["maneuver_count"]["user"] = 2
    payload["planner"]["maneuver_count_user"] = 2
    payload["hard_constraint_planner"]["q_AA_user"] = "3,2"
    payload["hard_constraint_planner"]["q_AP_user"] = "0"
    payload["hard_constraint_planner"]["q_AP_candidates"] = "0,1"
    payload["hard_constraint_planner"]["fixed_hp_targets_km"] = "1:6000"

    normalized = normalize_design_maneuver_strategy_payload(payload)
    assert normalized["hard_constraint_planner"]["q_AA_user"] == [3, 2]
    assert normalized["hard_constraint_planner"]["q_AP_user"] == 0
    assert normalized["hard_constraint_planner"]["q_AP_candidates"] == [0, 1]
    assert normalized["hard_constraint_planner"]["fixed_hp_targets_km"] == {"1": 6000.0}

    with pytest.raises(RuntimeError, match="V5.1"):
        plan_design_maneuver_strategy(payload)


def test_v51_single_fixed_perigee_target_keeps_duration_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = default_design_maneuver_strategy_payload()
    payload["hard_constraint_planner"]["fixed_hp_targets_km"] = {"1": 3940.0}
    payload["hard_constraint_planner"]["q_AP_user"] = 0
    payload["distribution"]["first_post_a_control_km"] = None
    monkeypatch.setattr(design_strategy, "minimize", None)

    result = plan_design_maneuver_strategy(payload)

    limit = result.config["burn_limit"]["max_total_burn_time_min"]
    assert result.summary["phase_diagnostics"]["fixed_hp_targets_km"]["1"] == pytest.approx(3940.0)
    assert result.summary["phase_diagnostics"]["hard_constraint_feasible"] is True
    assert max(burn.total_burn_time_min for burn in result.burns) <= limit


def test_v51_single_fixed_low_perigee_refines_terminal_longitude(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = default_design_maneuver_strategy_payload()
    payload["hard_constraint_planner"]["fixed_hp_targets_km"] = {"1": 3400.0}
    payload["hard_constraint_planner"]["q_AP_user"] = 0
    payload["distribution"]["first_post_a_control_km"] = None
    monkeypatch.setattr(design_strategy, "minimize", None)

    result = plan_design_maneuver_strategy(payload)

    re_km = float(result.config["earth"]["Re_km"])
    hp_targets = [burn.post_a_km * (1.0 - burn.post_e) - re_km for burn in result.burns[:3]]
    assert hp_targets[0] == pytest.approx(3400.0)
    assert hp_targets[1] == pytest.approx(8544.211, abs=0.01)
    assert hp_targets[2] == pytest.approx(18146.006, abs=0.01)
    assert result.summary["phase_diagnostics"]["hard_constraint_feasible"] is True
    assert abs(result.summary["terminal_errors"]["lon_deg"]) <= result.config["terminal_tolerance"]["lon_deg"]


def test_standard_design_planner_honors_user_count() -> None:
    payload = default_design_maneuver_strategy_payload()
    payload["orbit_type"]["mode"] = "standard_transfer"
    payload["maneuver_count"]["user"] = 3
    payload["planner"]["maneuver_count_user"] = 3
    payload["initial"]["a_km"] = 24300.0
    payload["initial"]["e"] = 0.735143

    result = plan_design_maneuver_strategy(payload)

    assert result.summary["orbit_type"] == "standard_transfer"
    assert result.summary["actual_count"] == 3
    assert result.summary["apsis_pattern"] == "A,A,A"
    assert [burn.apsis for burn in result.burns] == ["A", "A", "A"]
    assert result.burns[-1].burn_type == "normal"


def test_design_maneuver_strategy_page_uses_independent_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workspace = ProjectWorkspace()
    workspace.create_project("design-maneuver-page", tmp_path)

    page = DesignManeuverStrategyPage(I18nManager("zh"), workspace)
    assert page._title_label.text() == "设计变轨策略"
    assert page._parameter_config_button.text() == "参数配置"
    assert page._advanced_settings_button.text() == "高级设置"
    assert not hasattr(page, "_import_baseline_button")
    assert page._plan_button.property("variant") == "primaryAction"
    assert page._find_feasible_q_button.text() == "查找全部可行q"
    assert page._find_feasible_q_button.property("variant") == "secondary"
    assert page._progress_bar.isHidden()
    assert not hasattr(page, "_summary_card")
    assert not hasattr(page, "_summary_table")
    assert not hasattr(page, "_check_table")
    assert page._config_panel.maximumHeight() <= 178
    assert page._config_overview_table.maximumHeight() <= 118
    assert page._config_overview_table.rowCount() == 4
    assert page._burn_table.maximumHeight() <= 210
    assert page._continuous_thrust_button.text() == "优化连续推力模型参数"
    assert page._continuous_thrust_table.columnCount() == 6
    perigee_layout = page._mv1_hp_target_label.parentWidget().layout()
    assert perigee_layout.indexOf(page._q_sequence_combo) >= 0
    assert perigee_layout.indexOf(page._apply_hp_targets_button) >= 0
    assert not hasattr(page, "_v51_user_constraints_header_label")
    assert not hasattr(page, "_v51_hp_targets_edit")
    assert has_icon("nav.design_maneuver_strategy")

    advanced_dialog = _DesignManeuverSettingsDialog(
        "高级设置",
        page.config(),
        page._advanced_dialog_cards(),
        page,
    )
    assert ("target", "dv_lon_margin_mps") in advanced_dialog._number_fields
    assert ("maneuver_count", "user") not in advanced_dialog._number_fields
    assert advanced_dialog._text_fields[("hard_constraint_planner", "q_AA_user")].text() == ""
    advanced_dialog._text_fields[("hard_constraint_planner", "q_AA_user")].setText("3,2")
    advanced_dialog._text_fields[("hard_constraint_planner", "q_AP_user")].setText("0")
    advanced_dialog._text_fields[("hard_constraint_planner", "fixed_hp_targets_km")].setText("1:6000")
    dialog_config = advanced_dialog.config()
    assert dialog_config["hard_constraint_planner"]["q_AA_user"] == [3, 2]
    assert dialog_config["hard_constraint_planner"]["q_AP_user"] == 0
    assert dialog_config["hard_constraint_planner"]["fixed_hp_targets_km"] == {"1": 6000.0}

    page_config = page.config()
    page_config["apsis"]["pattern_mode"] = "auto"
    page_config["hard_constraint_planner"]["q_AA_user"] = []
    page_config["hard_constraint_planner"]["q_AP_user"] = None
    page_config["hard_constraint_planner"]["fixed_hp_targets_km"] = {"1": 3400.0}
    page_config["maneuver_count"]["user"] = 0
    page_config["planner"]["maneuver_count_user"] = 0
    page._accept_dialog_config(page_config)
    saved = page.save_config()
    assert saved == workspace.design_maneuver_strategy_path()
    assert workspace.design_maneuver_strategy_path().name == "design_maneuver_strategy.json"
    assert workspace.maneuver_strategy_path().name == "maneuver_strategy.json"
    assert workspace.load_design_maneuver_strategy()["hard_constraint_planner"]["fixed_hp_targets_km"]["1"] == 3400.0

    def fake_find_feasible_q_sequences(config):
        assert config["hard_constraint_planner"]["fixed_hp_targets_km"]["1"] == 3400.0
        return [
            {
                "q_sequence": [2, 3, 1, 0],
                "max_burn_duration_min": 37.5,
                "lon_error_deg": 0.0042,
                "hp_targets_km": [3400.0, 8200.0, 17680.0],
            }
        ]

    monkeypatch.setattr(design_page_module, "service_find_feasible_q_sequences", fake_find_feasible_q_sequences)
    page._find_feasible_q_button.click()
    assert page._q_sequence_combo.count() == 2
    assert page._q_sequence_combo.itemText(0) == ""
    assert page._q_sequence_combo.itemText(1) == "2,3,1,0"
    assert "共 1 组" in page._status_label.text()

    page.run_planner()
    assert page._burn_table.rowCount() == 6
    page._continuous_thrust_button.click()
    assert page._continuous_thrust_table.rowCount() == 5
    assert page._continuous_thrust_table.item(0, 0).text() == "MV1"
    assert page._continuous_thrust_table.item(0, 5).text()
    assert "连续推力参数优化完成" in page._status_label.text()
    assert page._burn_table.columnCount() == 14
    assert page._burn_table.horizontalHeaderItem(4).text() == "星下点经度/degE"
    assert page._burn_table.horizontalHeaderItem(9).text() == "计算的变轨推力偏航角/deg"
    assert page._burn_table.horizontalHeaderItem(13).text() == "控后近地点高度/km"
    assert page._burn_table.item(0, 0).text() == "分离点"
    assert page._burn_table.item(0, 1).text() == "0.00"
    assert page._burn_table.item(0, 13).text() == "200.00"
    assert page._burn_table.item(0, 4).text()
    assert page._burn_table.item(0, 4).text().count(".") == 1
    assert len(page._burn_table.item(0, 4).text().split(".")[1]) == 2
    assert page._burn_table.item(1, 0).text() == "MV1"
    assert len(page._burn_table.item(1, 1).text().split(".")[1]) == 2
    assert len(page._burn_table.item(1, 9).text().split(".")[1]) == 2
    assert not page._burn_table.item(1, 13).flags() & QtCore.Qt.ItemFlag.ItemIsEditable
    assert not page._burn_table.item(2, 13).flags() & QtCore.Qt.ItemFlag.ItemIsEditable
    assert page._burn_table.editTriggers() == QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    assert page._mv1_hp_target_edit.text() == "3400"
    assert page._mv2_hp_target_edit.text() == ""
    assert not hasattr(page, "_q_candidate_table")
    assert page._q_sequence_combo.count() > 1
    assert page._q_sequence_combo.itemText(0) == ""
    candidate_q = page._q_sequence_combo.itemText(1)
    assert candidate_q
    replans: list[bool] = []
    page.run_planner = lambda: replans.append(True)  # type: ignore[method-assign]
    page._q_sequence_combo.setCurrentIndex(1)
    assert replans == []
    page._apply_hp_targets_button.click()
    assert replans == [True]
    q_values = [int(value) for value in candidate_q.split(",")]
    q_config = page.config()
    assert q_config["apsis"]["pattern_mode"] == "user"
    assert q_config["hard_constraint_planner"]["q_AA_user"] == q_values[:-1]
    assert q_config["hard_constraint_planner"]["q_AP_user"] == q_values[-1]
    replans.clear()
    page._q_sequence_combo.setCurrentIndex(0)
    assert replans == []
    page._apply_hp_targets_button.click()
    assert replans == [True]
    cleared_q_config = page.config()
    assert cleared_q_config["apsis"]["pattern_mode"] == "auto"
    assert cleared_q_config["hard_constraint_planner"]["q_AA_user"] == []
    assert cleared_q_config["hard_constraint_planner"]["q_AP_user"] is None
    replans.clear()
    page._mv1_hp_target_edit.setText("6100.00")
    page._apply_hp_targets_button.click()
    assert replans == [True]
    assert page.config()["hard_constraint_planner"]["fixed_hp_targets_km"]["1"] == pytest.approx(6100.0)
    assert page.config()["distribution"]["first_post_a_control_km"] is None
    assert workspace.design_maneuver_results_path().exists()

    reloaded_page = DesignManeuverStrategyPage(I18nManager("zh"), workspace)
    assert reloaded_page._burn_table.rowCount() == 6
    assert reloaded_page._status_label.text() == "硬约束全部通过"

    beijing_tz = QtCore.QTimeZone(b"Asia/Shanghai")
    parameter_dialog = _DesignManeuverSettingsDialog(
        "参数配置",
        page.config(),
        page._basic_dialog_cards(),
        page,
    )
    assert ("maneuver_count", "user") in parameter_dialog._number_fields
    assert ("target", "dv_lon_margin_mps") not in parameter_dialog._number_fields
    parameter_dialog._number_fields[("maneuver_count", "user")].setValue(4)
    assert parameter_dialog.config()["maneuver_count"]["user"] == 4
    assert parameter_dialog.config()["planner"]["maneuver_count_user"] == 4
    assert parameter_dialog._t0_epoch_field is not None
    assert parameter_dialog._t0_epoch_field.displayFormat() == "yyyy-MM-dd HH:mm:ss"
    assert parameter_dialog._t0_epoch_field.dateTime().timeZone().id().data().decode() == "Asia/Shanghai"
    parameter_dialog._t0_epoch_field.setDateTime(
        QtCore.QDateTime(QtCore.QDate(2024, 1, 1), QtCore.QTime(8, 0, 0), beijing_tz)
    )
    assert parameter_dialog.config()["initial"]["t0_epoch"] == "2024-01-01T00:00:00Z"


def test_design_maneuver_config_normalizes_booleans_and_windows() -> None:
    payload = normalize_design_maneuver_strategy_payload(
        {
            "engine": {"use_settling": False},
            "longitude": {"planning_window_degE": [50, 170]},
            "maneuver_count": {"min": 2, "max": 1},
        }
    )

    assert payload["engine"]["use_settling"] is False
    assert payload["longitude"]["planning_window_degE"] == [50.0, 170.0]
    assert payload["maneuver_count"]["max"] == 2


def test_design_maneuver_config_accepts_reference_package_shape() -> None:
    payload = normalize_design_maneuver_strategy_payload(
        {
            "version": "V4.2_simplified_transfer_type",
            "t0_bj": "2026-04-24 21:54:27",
            "initial_mass_kg": 6515.0,
            "initial_orbit": {
                "a_km": 29478.137,
                "e": 0.77684692,
                "i_deg": 16.5,
                "argp_deg": 200.0,
                "M_deg": 1.85437,
                "ascending_node_longitude_deg": 8.53237,
            },
            "maneuver_count": {
                "user": 0,
                "total_dv_est_user_mps": 1539.0,
                "engineering_min_count_supersync": 5,
            },
        }
    )

    assert payload["initial"]["t0_epoch"] == "2026-04-24T13:54:27Z"
    assert payload["initial"]["m0_kg"] == pytest.approx(6515.0)
    assert payload["initial"]["mean_anomaly_deg"] == pytest.approx(1.85437)
    assert payload["maneuver_count"]["engineering_min_count_supersync"] == 5
