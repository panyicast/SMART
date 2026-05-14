from __future__ import annotations

import pytest

from PySide6 import QtCore, QtWidgets

from smart.services.design_maneuver_strategy import (
    default_design_maneuver_strategy_payload,
    normalize_design_maneuver_strategy_payload,
    plan_design_maneuver_strategy,
)
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.nav_icons import has_icon
from smart.ui.widgets.design_maneuver_strategy_page import DesignManeuverStrategyPage


def test_supersynchronous_design_planner_outputs_fixed_tail() -> None:
    result = plan_design_maneuver_strategy(default_design_maneuver_strategy_payload())

    assert result.summary["orbit_type"] == "supersynchronous_transfer"
    assert result.summary["actual_count"] == result.summary["recommended_count"]
    assert result.summary["apsis_pattern"].endswith("A,P")
    assert len(result.burns) == result.summary["actual_count"]
    assert result.burns[-2].burn_type == "tail_fixed"
    assert result.burns[-1].burn_type == "tail_fixed"
    assert result.burns[-2].target_post_a_km == pytest.approx(47271.168509)
    assert result.burns[-1].target_post_a_km == pytest.approx(42164.2)
    assert result.burns[-1].post_a_km == pytest.approx(42164.2)
    assert all(0.0 <= burn.longitude_deg_e < 360.0 for burn in result.burns)
    assert result.checks


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


def test_design_maneuver_strategy_page_uses_independent_config(tmp_path) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workspace = ProjectWorkspace()
    workspace.create_project("design-maneuver-page", tmp_path)

    page = DesignManeuverStrategyPage(I18nManager("zh"), workspace)
    assert page._title_label.text() == "设计变轨策略"
    assert page._plan_button.property("variant") == "primaryAction"
    assert page._t0_epoch_field.displayFormat() == "yyyy-MM-dd HH:mm:ss"
    assert page._t0_epoch_field.dateTime().timeZone().id().data().decode() == "Asia/Shanghai"
    assert has_icon("nav.design_maneuver_strategy")

    page._number_fields[("maneuver_count", "user")].setValue(2)
    saved = page.save_config()
    assert saved == workspace.design_maneuver_strategy_path()
    assert workspace.design_maneuver_strategy_path().name == "design_maneuver_strategy.json"
    assert workspace.maneuver_strategy_path().name == "maneuver_strategy.json"
    assert workspace.load_design_maneuver_strategy()["maneuver_count"]["user"] == 2

    page.run_planner()
    assert page._summary_table.rowCount() > 0
    assert page._burn_table.rowCount() == 2
    assert page._check_table.rowCount() > 0

    beijing_tz = QtCore.QTimeZone(b"Asia/Shanghai")
    page._t0_epoch_field.setDateTime(
        QtCore.QDateTime(QtCore.QDate(2024, 1, 1), QtCore.QTime(8, 0, 0), beijing_tz)
    )
    assert page.config()["initial"]["t0_epoch"] == "2024-01-01T00:00:00Z"


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
