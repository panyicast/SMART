from __future__ import annotations

import numpy as np

from PySide6 import QtWidgets
from PySide6 import QtCore, QtTest
from PySide6.QtCore import Qt

from smart.domain.models import EARTH_RADIUS_KM
from smart.services.earth_orientation import parse_utc
from smart.services.flight_program import DEPLOYMENT_KIND, FlightProgramSample, normalize_flight_program_payload
from smart.services.project_workspace import ProjectWorkspace
from smart.services.tracking_arc import TrackingArcOrbitResult
from smart.ui.i18n import I18nManager
from smart.ui.widgets.flight_program_page import FlightProgramOverviewWidget, FlightProgramPage
from smart.ui.widgets.table_editing import ComboBoxTableEditDelegate


class _FakeStkSyncService:
    def __init__(self) -> None:
        self.calls = 0
        self.current_times: list[str] = []

    def sync_current_scenario_analysis_time(self) -> bool:
        self.calls += 1
        return True

    def sync_current_scenario_time(self, current_utc: str) -> bool:
        self.current_times.append(current_utc)
        return True


def test_set_playhead_updates_status_without_rebuilding_tables(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "event-1",
                    "name": "测试姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 10.0,
                    "end_min": 20.0,
                }
            ]
        }
    )
    page._reference_segments = [
        {
            "id": "ref-1",
            "kind": "ground",
            "name": "测试可见段",
            "start_min": 5.0,
            "end_min": 15.0,
            "source": "tracking_arc",
        }
    ]
    page._refresh_timeline()

    calls = {"event": 0, "reference": 0, "event_status": 0, "reference_status": 0}
    monkeypatch.setattr(page, "_refresh_event_table", lambda: calls.__setitem__("event", calls["event"] + 1))
    monkeypatch.setattr(page, "_refresh_reference_table", lambda: calls.__setitem__("reference", calls["reference"] + 1))
    monkeypatch.setattr(page, "_update_event_table_statuses", lambda: calls.__setitem__("event_status", calls["event_status"] + 1))
    monkeypatch.setattr(
        page,
        "_update_reference_table_statuses",
        lambda: calls.__setitem__("reference_status", calls["reference_status"] + 1),
    )
    monkeypatch.setattr(page, "_refresh_sample_preview", lambda: None)

    page._set_playhead(12.0)

    assert calls == {"event": 0, "reference": 0, "event_status": 1, "reference_status": 1}

    page._set_playhead(30.0)

    assert calls == {"event": 0, "reference": 0, "event_status": 2, "reference_status": 2}


def test_program_tables_split_attitudes_and_main_events() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "太阳指向巡航",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 0.0,
                    "end_min": 20.0,
                },
                {
                    "id": "main-1",
                    "name": "太阳翼展开",
                    "kind": DEPLOYMENT_KIND,
                    "mode": "SolarArrayDeploy",
                    "start_min": 30.0,
                    "end_min": 40.0,
                },
            ]
        }
    )

    page._refresh_event_table()

    assert page._table_tabs.tabText(page._table_tabs.indexOf(page._event_table)) == "卫星姿态设置"
    assert page._table_tabs.tabText(page._table_tabs.indexOf(page._major_event_table)) == "主要飞行事件"
    assert page._event_table.rowCount() == 1
    assert page._major_event_table.rowCount() == 1
    assert page._event_table.item(0, 3).text() == "太阳指向巡航"
    assert page._major_event_table.item(0, 2).text() == "太阳翼展开"
    assert "类型" not in [page._event_table.horizontalHeaderItem(i).text() for i in range(page._event_table.columnCount())]
    assert "瞬时" not in [page._event_table.horizontalHeaderItem(i).text() for i in range(page._event_table.columnCount())]
    assert "类型" not in [page._major_event_table.horizontalHeaderItem(i).text() for i in range(page._major_event_table.columnCount())]
    assert "模式" not in [page._major_event_table.horizontalHeaderItem(i).text() for i in range(page._major_event_table.columnCount())]


def test_manual_launch_change_updates_selected_t0_without_tracking_recompute(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    monkeypatch.setattr(page._workspace, "load_tracking_arc_config", lambda: {"rocket_flight_time_s": 120.0})
    monkeypatch.setattr(page._workspace, "load_launch_window_config", lambda: None)
    monkeypatch.setattr(page, "_refresh_reference_segments", lambda: None)
    monkeypatch.setattr(page, "_refresh_all", lambda: None)

    manual_index = page._launch_source_combo.findData("manual")
    page._launch_source_combo.setCurrentIndex(manual_index)
    page._manual_launch_edit.setDateTime(page._utc_to_qdatetime("2026-05-15T00:10:00Z"))

    page._on_manual_launch_changed()

    assert page._program["selected_launch_utc"] == "2026-05-15T00:10:00Z"
    assert page._program["selected_t0_utc"] == "2026-05-15T00:12:00Z"


def test_manual_launch_change_syncs_existing_stk_scene_time(monkeypatch, tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    workspace = ProjectWorkspace()
    workspace.create_project("flight-stk-sync", parent_dir=tmp_path)
    service = _FakeStkSyncService()
    page = FlightProgramPage(I18nManager("zh"), workspace, stk_link_service_factory=lambda: service)  # type: ignore[arg-type]
    monkeypatch.setattr(page._workspace, "load_tracking_arc_config", lambda: {"rocket_flight_time_s": 120.0})
    monkeypatch.setattr(page._workspace, "load_launch_window_config", lambda: None)
    monkeypatch.setattr(page, "_refresh_reference_segments", lambda: None)
    monkeypatch.setattr(page, "_refresh_all", lambda: None)
    monkeypatch.setattr(page, "_save_reference_results", lambda: None)

    manual_index = page._launch_source_combo.findData("manual")
    page._launch_source_combo.blockSignals(True)
    page._launch_source_combo.setCurrentIndex(manual_index)
    page._launch_source_combo.blockSignals(False)
    page._manual_launch_edit.blockSignals(True)
    page._manual_launch_edit.setDateTime(page._utc_to_qdatetime("2026-05-15T00:10:00Z"))
    page._manual_launch_edit.blockSignals(False)

    page._on_manual_launch_changed()

    assert service.calls == 1


def test_playhead_change_syncs_stk_current_time(tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    workspace = ProjectWorkspace()
    workspace.create_project("flight-stk-current-time", parent_dir=tmp_path)
    service = _FakeStkSyncService()
    page = FlightProgramPage(I18nManager("zh"), workspace, stk_link_service_factory=lambda: service)  # type: ignore[arg-type]
    page._program["selected_t0_utc"] = "2026-05-15T00:12:00Z"
    page._refresh_sample_preview = lambda: None  # type: ignore[method-assign]

    page._set_playhead(12.5)

    assert service.current_times == ["2026-05-15T00:24:30Z"]


def test_event_changes_autosave_flight_program_config(tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    workspace = ProjectWorkspace()
    workspace.create_project("flight-autosave", parent_dir=tmp_path)
    page = FlightProgramPage(I18nManager("zh"), workspace)

    page._add_event("SPM", 12.0)

    restored = workspace.load_flight_program_config()
    assert restored is not None
    assert len(restored["events"]) == 1
    assert restored["events"][0]["name"] == "SPM 姿态"
    assert restored["events"][0]["start_min"] == 12.0


def test_saved_reference_results_load_on_refresh(tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    workspace = ProjectWorkspace()
    workspace.create_project("flight-reference-cache", parent_dir=tmp_path)
    page = FlightProgramPage(I18nManager("zh"), workspace)
    page._tracking_results = {"leading": _tracking_result()}
    page._program["selected_orbit_point"] = "leading"
    page._save_reference_results()

    restored_page = FlightProgramPage(I18nManager("zh"), workspace)

    assert "leading" in restored_page._tracking_results
    assert restored_page._reference_table.rowCount() == 0
    assert restored_page._selected_tracking_result() is not None


def test_timeline_drag_updates_playhead() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)
    emitted: list[float] = []
    widget.playhead_changed.connect(emitted.append)

    start = widget.rect().center()
    end = start + QtCore.QPoint(80, 0)
    QtTest.QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseMove(widget, pos=end)
    QtTest.QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)

    assert len(emitted) >= 2
    assert emitted[-1] > emitted[0]


def test_timeline_wheel_zoom_shrinks_visible_range_around_cursor() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    center_x = plot_rect.left() + plot_rect.width() / 2.0
    assert widget._zoom_view(center_x, 0.8) is True
    visible_start, visible_end = widget._visible_range()
    visible_span = visible_end - visible_start
    assert visible_span < 100.0
    assert visible_start <= 50.0 <= visible_end
    assert widget._can_pan() is True


def test_timeline_zoom_resets_when_duration_changes() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    widget._zoom_view(plot_rect.left() + plot_rect.width() / 2.0, 0.5)
    assert widget._can_pan() is True

    widget.set_data(events=[], reference_segments=[], duration_min=240.0, playhead_min=0.0)

    visible_start, visible_end = widget._visible_range()
    assert visible_start == 0.0
    assert visible_end == 240.0
    assert widget._can_pan() is False


def test_timeline_zoom_persists_when_duration_unchanged() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    widget._zoom_view(plot_rect.left() + plot_rect.width() / 2.0, 0.5)
    visible_start_before, visible_end_before = widget._visible_range()

    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=10.0)

    visible_start_after, visible_end_after = widget._visible_range()
    assert visible_start_after == visible_start_before
    assert visible_end_after == visible_end_before


def test_timeline_double_click_blank_resets_zoom() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    widget._zoom_view(plot_rect.left() + plot_rect.width() / 2.0, 0.5)
    assert widget._can_pan() is True

    target = QtCore.QPoint(int(plot_rect.center().x()), int(plot_rect.center().y()))
    QtTest.QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=target)

    assert widget._can_pan() is False
    visible_start, visible_end = widget._visible_range()
    assert visible_start == 0.0
    assert visible_end == 100.0


def test_timeline_middle_button_drag_pans_view() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    widget._zoom_view(plot_rect.left() + plot_rect.width() / 2.0, 0.5)
    visible_start_before, _visible_end_before = widget._visible_range()

    start = QtCore.QPoint(int(plot_rect.center().x()), int(plot_rect.center().y()))
    end = start - QtCore.QPoint(60, 0)
    QtTest.QTest.mousePress(widget, Qt.MouseButton.MiddleButton, pos=start)
    QtTest.QTest.mouseMove(widget, pos=end)
    QtTest.QTest.mouseRelease(widget, Qt.MouseButton.MiddleButton, pos=end)

    visible_start_after, _visible_end_after = widget._visible_range()
    assert visible_start_after > visible_start_before


def test_indicator_left_drag_pans_view() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    widget._zoom_view(plot_rect.left() + plot_rect.width() / 2.0, 0.5)
    handle = widget._indicator_handle_rect()
    assert handle is not None

    visible_start_before, _ = widget._visible_range()
    grab = QtCore.QPoint(int(handle.center().x()), int(handle.center().y()))
    target = grab + QtCore.QPoint(60, 0)
    QtTest.QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=grab)
    QtTest.QTest.mouseMove(widget, pos=target)
    QtTest.QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=target)

    visible_start_after, _ = widget._visible_range()
    assert visible_start_after > visible_start_before


def test_indicator_left_click_outside_handle_jumps_view() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    widget._zoom_view(plot_rect.left() + plot_rect.width() / 2.0, 0.3)
    track = widget._indicator_track_rect()
    handle = widget._indicator_handle_rect()
    assert track is not None and handle is not None

    target_x = track.right() - 6.0
    target = QtCore.QPoint(int(target_x), int(track.center().y()))
    QtTest.QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=target)
    QtTest.QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=target)

    visible_start, visible_end = widget._visible_range()
    assert visible_end >= 100.0 - 1e-6
    assert visible_start > 50.0


def test_timeline_x_to_min_uses_visible_range_after_zoom() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = FlightProgramOverviewWidget()
    widget.resize(500, 270)
    widget.set_data(events=[], reference_segments=[], duration_min=100.0, playhead_min=0.0)

    plot_rect = widget._plot_rect()
    widget._view_start_min = 40.0
    widget._view_end_min = 60.0

    minute_at_left = widget._x_to_min(plot_rect.left())
    minute_at_right = widget._x_to_min(plot_rect.right())
    assert abs(minute_at_left - 40.0) < 1e-3
    assert abs(minute_at_right - 60.0) < 1e-3


def test_right_panel_is_full_realtime_scene_card() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    right_panel = page._build_right_panel()

    assert right_panel.property("role") == "card"
    assert right_panel.layout().count() == 2
    assert right_panel.layout().itemAt(1).widget() is page._scene_view
    assert page._scene_view.minimumHeight() == 480


def test_reference_table_hides_source_column() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())

    assert "来源" not in page._REFERENCE_COLUMNS
    assert "显示" not in page._REFERENCE_COLUMNS
    assert page._reference_table.columnCount() == len(page._REFERENCE_COLUMNS) == 6
    assert page._REFERENCE_COLUMNS[1] == "类型"


def test_attitude_mode_column_uses_preset_combo_delegate() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "测试姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 0.0,
                    "end_min": 10.0,
                }
            ]
        }
    )
    page._refresh_event_table()
    delegate = page._event_table.itemDelegate()
    model_index = page._event_table.model().index(0, 2)
    option = QtWidgets.QStyleOptionViewItem()
    option.rect = page._event_table.visualRect(model_index)

    assert isinstance(delegate, ComboBoxTableEditDelegate)
    editor = delegate.createEditor(page._event_table, option, model_index)
    assert isinstance(editor, QtWidgets.QComboBox)
    assert [editor.itemText(i) for i in range(editor.count())] == ["SPM", "EPM", "AFM", "Transition"]


def test_event_boolean_columns_use_toggle_buttons() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "测试姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 0.0,
                    "end_min": 10.0,
                    "locked": False,
                    "instant": False,
                },
                {
                    "id": "main-1",
                    "name": "太阳翼展开",
                    "kind": DEPLOYMENT_KIND,
                    "mode": "SolarArrayDeploy",
                    "start_min": 20.0,
                    "end_min": 21.0,
                    "locked": False,
                    "instant": False,
                }
            ]
        }
    )
    page._refresh_event_table()

    locked_button = page._event_table.cellWidget(0, 1)
    instant_button = page._major_event_table.cellWidget(0, 6)

    assert isinstance(locked_button, QtWidgets.QPushButton)
    assert isinstance(instant_button, QtWidgets.QPushButton)
    assert locked_button.text() == "否"
    assert instant_button.text() == "否"

    QtTest.QTest.mouseClick(locked_button, Qt.MouseButton.LeftButton)
    QtTest.QTest.mouseClick(instant_button, Qt.MouseButton.LeftButton)

    event = page._event_by_id("attitude-1")
    assert event is not None
    assert bool(event.get("locked")) is True
    major_event = page._event_by_id("main-1")
    assert major_event is not None
    assert bool(major_event.get("instant")) is True
    assert isinstance(page._event_table.cellWidget(0, 1), QtWidgets.QPushButton)
    assert isinstance(page._major_event_table.cellWidget(0, 6), QtWidgets.QPushButton)
    assert page._event_table.cellWidget(0, 1).text() == "是"
    assert page._major_event_table.cellWidget(0, 6).text() == "是"
    assert "#7ff1ff" in page._event_table.cellWidget(0, 1).styleSheet().lower()
    assert "#7ff1ff" in page._major_event_table.cellWidget(0, 6).styleSheet().lower()


def test_locked_event_allows_only_unlock() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-locked",
                    "name": "锁定姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 0.0,
                    "end_min": 10.0,
                    "locked": True,
                },
                {
                    "id": "main-locked",
                    "name": "锁定事件",
                    "kind": DEPLOYMENT_KIND,
                    "mode": "SolarArrayDeploy",
                    "start_min": 20.0,
                    "end_min": 21.0,
                    "locked": True,
                    "instant": False,
                },
            ]
        }
    )
    page._refresh_event_table()

    attitude_name_item = page._event_table.item(0, 3)
    attitude_lock_button = page._event_table.cellWidget(0, 1)
    major_instant_button = page._major_event_table.cellWidget(0, 6)

    assert not bool(attitude_name_item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable)
    assert isinstance(attitude_lock_button, QtWidgets.QPushButton)
    assert attitude_lock_button.isEnabled()
    assert isinstance(major_instant_button, QtWidgets.QPushButton)
    assert not major_instant_button.isEnabled()

    page._select_event("main-locked")
    before_rows = page._major_event_table.rowCount()
    page._duplicate_selected_event()
    assert page._major_event_table.rowCount() == before_rows

    QtTest.QTest.mouseClick(attitude_lock_button, Qt.MouseButton.LeftButton)

    unlocked_name_item = page._event_table.item(0, 3)
    assert bool(unlocked_name_item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable)


def test_jump_to_selected_attitude_segment_moves_playhead() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "测试姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 12.5,
                    "end_min": 22.5,
                }
            ]
        }
    )
    page._refresh_event_table()
    page._set_playhead(0.0)
    page._select_event("attitude-1")

    page._jump_to_selected_event()

    assert page._playhead_min == 12.5


def test_pressing_enter_jumps_to_current_table_row() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "姿态一",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 5.0,
                    "end_min": 15.0,
                },
                {
                    "id": "attitude-2",
                    "name": "姿态二",
                    "kind": "attitude",
                    "mode": "EPM",
                    "start_min": 25.0,
                    "end_min": 35.0,
                },
            ]
        }
    )
    page._refresh_event_table()
    page._event_table.setCurrentCell(1, 0)
    page._event_table.selectRow(1)
    page._set_playhead(0.0)

    QtTest.QTest.keyClick(page._event_table, Qt.Key.Key_Return)

    assert page._playhead_min == 25.0


def test_attitude_start_change_updates_previous_segment_end() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "前一姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 0.0,
                    "end_min": 20.0,
                },
                {
                    "id": "attitude-2",
                    "name": "当前姿态",
                    "kind": "attitude",
                    "mode": "EPM",
                    "start_min": 20.0,
                    "end_min": 40.0,
                },
            ]
        }
    )
    page._refresh_event_table()

    start_item = page._event_table.item(1, 4)
    start_item.setText("15.0")

    previous = page._event_by_id("attitude-1")
    current = page._event_by_id("attitude-2")
    assert previous is not None
    assert current is not None
    assert float(previous["end_min"]) == 15.0
    assert float(current["start_min"]) == 15.0


def test_attitude_start_change_conflicts_when_previous_segment_locked() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "前一姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 0.0,
                    "end_min": 20.0,
                    "locked": True,
                },
                {
                    "id": "attitude-2",
                    "name": "当前姿态",
                    "kind": "attitude",
                    "mode": "EPM",
                    "start_min": 20.0,
                    "end_min": 40.0,
                },
            ]
        }
    )
    page._refresh_event_table()

    start_item = page._event_table.item(1, 4)
    start_item.setText("15.0")

    previous = page._event_by_id("attitude-1")
    current = page._event_by_id("attitude-2")
    assert previous is not None
    assert current is not None
    assert float(previous["end_min"]) == 20.0
    assert float(current["start_min"]) == 20.0
    assert "前一个姿态段已锁定" in page._status_label.text()


def test_attitude_start_change_conflicts_when_earlier_than_previous_start() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "id": "attitude-1",
                    "name": "前一姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 10.0,
                    "end_min": 20.0,
                },
                {
                    "id": "attitude-2",
                    "name": "当前姿态",
                    "kind": "attitude",
                    "mode": "EPM",
                    "start_min": 20.0,
                    "end_min": 40.0,
                },
            ]
        }
    )
    page._refresh_event_table()

    start_item = page._event_table.item(1, 4)
    start_item.setText("5.0")

    previous = page._event_by_id("attitude-1")
    current = page._event_by_id("attitude-2")
    assert previous is not None
    assert current is not None
    assert float(previous["end_min"]) == 20.0
    assert float(current["start_min"]) == 20.0
    assert "不能早于前一个姿态段的开始时间" in page._status_label.text()


def test_timeline_duration_ignores_unreadable_orbit_history(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program = normalize_flight_program_payload(
        {
            "events": [
                {
                    "name": "测试姿态",
                    "kind": "attitude",
                    "mode": "SPM",
                    "start_min": 0.0,
                    "end_min": 75.0,
                }
            ]
        }
    )
    monkeypatch.setattr(page, "_orbit_history_rows", lambda: (_ for _ in ()).throw(ValueError("bad csv")))

    assert page._timeline_duration() == 75.0


def test_flight_program_orbit_preview_uses_full_history_track(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    monkeypatch.setattr(
        page,
        "_orbit_history_rows",
        lambda: [
            {
                "elapsed_time_s": 0.0,
                "elapsed_time_min": 0.0,
                "phase": "coast",
                "is_event_point": 1,
                "position_x_m": 1_000.0,
                "position_y_m": 0.0,
                "position_z_m": 0.0,
                "velocity_x_m_s": 0.0,
                "velocity_y_m_s": 1.0,
                "velocity_z_m_s": 0.0,
            },
            {
                "elapsed_time_s": 600.0,
                "elapsed_time_min": 10.0,
                "phase": "orbit_control",
                "is_event_point": 1,
                "position_x_m": 0.0,
                "position_y_m": 2_000.0,
                "position_z_m": 0.0,
                "velocity_x_m_s": -1.0,
                "velocity_y_m_s": 0.0,
                "velocity_z_m_s": 0.0,
            },
            {
                "elapsed_time_s": 1200.0,
                "elapsed_time_min": 20.0,
                "phase": "coast",
                "is_event_point": 0,
                "position_x_m": 0.0,
                "position_y_m": 0.0,
                "position_z_m": 3_000.0,
                "velocity_x_m_s": 0.0,
                "velocity_y_m_s": -1.0,
                "velocity_z_m_s": 0.0,
            },
        ],
    )
    page._tracking_results = {"leading": _tracking_result()}
    sample = FlightProgramSample(
        elapsed_min=10.0,
        mode="SPM",
        event_name="",
        position_m=(0.0, 2_000.0, 0.0),
        velocity_mps=(-1.0, 0.0, 0.0),
        subsatellite_longitude_deg=0.0,
        subsatellite_latitude_deg=0.0,
        altitude_m=0.0,
        plus_z_ecef=(0.0, 0.0, 1.0),
        sun_ecef=(1.0, 0.0, 0.0),
        earth_ecef=(0.0, -1.0, 0.0),
        in_shadow=False,
    )

    trajectory = page._orbit_trajectory_for_sample(sample)

    assert trajectory is not None
    assert trajectory.positions_km.tolist() == [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 3.0]]
    assert trajectory.current_position_km.tolist() == [0.0, 2.0, 0.0]
    assert trajectory.current_velocity_km_s.tolist() == [-0.001, 0.0, 0.0]
    assert [segment.tolist() for segment in page._maneuver_segments_km(page._orbit_history_rows(), trajectory.positions_km)] == [
        [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0]]
    ]


def test_orbit_view_hides_track_until_reference_arcs_are_calculated(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    monkeypatch.setattr(
        page,
        "_orbit_history_rows",
        lambda: [
            {
                "elapsed_time_s": 0.0,
                "elapsed_time_min": 0.0,
                "position_x_m": 1_000.0,
                "position_y_m": 0.0,
                "position_z_m": 0.0,
                "velocity_x_m_s": 0.0,
                "velocity_y_m_s": 1.0,
                "velocity_z_m_s": 0.0,
            },
            {
                "elapsed_time_s": 600.0,
                "elapsed_time_min": 10.0,
                "position_x_m": 0.0,
                "position_y_m": 2_000.0,
                "position_z_m": 0.0,
                "velocity_x_m_s": -1.0,
                "velocity_y_m_s": 0.0,
                "velocity_z_m_s": 0.0,
            },
        ],
    )
    sample = FlightProgramSample(
        elapsed_min=10.0,
        mode="SPM",
        event_name="",
        position_m=(0.0, 2000.0, 0.0),
        velocity_mps=(0.0, 0.0, 0.0),
        subsatellite_longitude_deg=0.0,
        subsatellite_latitude_deg=0.0,
        altitude_m=0.0,
        plus_z_ecef=(0.0, 0.0, 1.0),
        sun_ecef=(1.0, 0.0, 0.0),
        earth_ecef=(0.0, -1.0, 0.0),
        in_shadow=False,
    )

    assert page._orbit_trajectory_for_sample(sample) is None

    page._tracking_results = {"leading": _tracking_result()}

    assert page._orbit_trajectory_for_sample(sample) is not None


def test_orbit_view_draws_earth_and_sun_direction_vectors(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    monkeypatch.setattr("smart.ui.widgets.flight_program_page._gmst_rad", lambda _epoch: 0.0)
    monkeypatch.setattr(
        page,
        "_orbit_history_rows",
        lambda: [
            {
                "elapsed_time_s": 0.0,
                "elapsed_time_min": 0.0,
                "position_x_m": 1_000.0,
                "position_y_m": 0.0,
                "position_z_m": 0.0,
                "velocity_x_m_s": 0.0,
                "velocity_y_m_s": 1.0,
                "velocity_z_m_s": 0.0,
            },
            {
                "elapsed_time_s": 600.0,
                "elapsed_time_min": 10.0,
                "position_x_m": 0.0,
                "position_y_m": 2_000.0,
                "position_z_m": 0.0,
                "velocity_x_m_s": -1.0,
                "velocity_y_m_s": 0.0,
                "velocity_z_m_s": 0.0,
            },
        ],
    )
    page._tracking_results = {"leading": _tracking_result()}
    sample = FlightProgramSample(
        elapsed_min=10.0,
        mode="SPM",
        event_name="",
        position_m=(0.0, 2000.0, 0.0),
        velocity_mps=(-1.0, 0.0, 0.0),
        subsatellite_longitude_deg=0.0,
        subsatellite_latitude_deg=0.0,
        altitude_m=0.0,
        plus_z_ecef=(0.0, 0.0, 1.0),
        sun_ecef=(1.0, 0.0, 0.0),
        earth_ecef=(0.0, -1.0, 0.0),
        in_shadow=False,
    )
    calls: dict[str, object] = {}

    class FakeSceneView:
        def set_trajectory_overlays(
            self,
            trajectory,
            body_radius_km,
            *,
            maneuver_segments_km=None,
            start_label=None,
            earth_rotation_rad=0.0,
            subsatellite_position_km=None,
        ):
            calls["trajectory"] = trajectory
            calls["body_radius_km"] = body_radius_km
            calls["earth_rotation_rad"] = earth_rotation_rad
            calls["subsatellite_position_km"] = None if subsatellite_position_km is None else subsatellite_position_km.tolist()

        def set_direction_vectors(self, origin_km, vectors):
            calls["origin_km"] = origin_km.tolist()
            calls["vectors"] = vectors

        def set_info_overlays(self, overlays):
            calls["overlays"] = overlays

        def clear_trajectory(self):
            calls["cleared"] = True

    page._scene_view = FakeSceneView()

    page._update_orbit_view_for_sample(sample)

    assert calls["origin_km"] == [0.0, 2.0, 0.0]
    vectors = calls["vectors"]
    assert [item["label"] for item in vectors] == ["Earth", "Sun"]
    assert vectors[0]["direction"].tolist() == [0.0, -2.0, 0.0]
    assert vectors[1]["direction"].tolist() == [1.0, 0.0, 0.0]
    assert calls["earth_rotation_rad"] == 0.0
    assert calls["subsatellite_position_km"] == [EARTH_RADIUS_KM, 0.0, 0.0]
    assert "当前时间（北京）" in calls["overlays"]["bottom_left"]


def test_sample_overlay_shows_beijing_time_subpoint_attitude_and_tracking_event() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._program["selected_t0_utc"] = "2026-05-15T00:00:00Z"
    page._playhead_min = 10.0
    page._reference_segments = [
        {
            "id": "ref-ground",
            "kind": "ground",
            "name": "地面站 Sanya",
            "start_min": 5.0,
            "end_min": 15.0,
        }
    ]
    sample = FlightProgramSample(
        elapsed_min=10.0,
        mode="EPM",
        event_name="测控姿态",
        position_m=(0.0, 2000.0, 0.0),
        velocity_mps=(-1.0, 0.0, 0.0),
        subsatellite_longitude_deg=109.1234,
        subsatellite_latitude_deg=18.5678,
        altitude_m=0.0,
        plus_z_ecef=(0.0, 0.0, 1.0),
        sun_ecef=(1.0, 0.0, 0.0),
        earth_ecef=(0.0, -1.0, 0.0),
        in_shadow=False,
    )

    overlays = page._sample_overlay_sections(sample)

    assert "当前时间（北京）：2026-05-15 08:10:00" in overlays["bottom_left"]
    assert "星下点：经度 109.123° / 纬度 18.568°" in overlays["bottom_right"]
    assert "卫星姿态：EPM / 测控姿态" in overlays["top_right"]
    assert "主要测控事件：当前 地面站可见：地面站 Sanya" in overlays["top_left"]


def test_orbit_view_applies_earth_rotation_from_sample_time(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    monkeypatch.setattr("smart.ui.widgets.flight_program_page._gmst_rad", lambda _epoch: 1.25)
    page._tracking_results = {"leading": _tracking_result()}
    sample = FlightProgramSample(
        elapsed_min=10.0,
        mode="SPM",
        event_name="",
        position_m=(0.0, 2000.0, 0.0),
        velocity_mps=(-1.0, 0.0, 0.0),
        subsatellite_longitude_deg=0.0,
        subsatellite_latitude_deg=0.0,
        altitude_m=0.0,
        plus_z_ecef=(0.0, 0.0, 1.0),
        sun_ecef=(1.0, 0.0, 0.0),
        earth_ecef=(0.0, -1.0, 0.0),
        in_shadow=False,
    )

    assert page._earth_rotation_rad_for_sample(sample.elapsed_min) == 1.25


def test_subsatellite_position_rotates_with_earth(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    monkeypatch.setattr("smart.ui.widgets.flight_program_page._gmst_rad", lambda _epoch: np.pi / 2.0)
    page._tracking_results = {"leading": _tracking_result()}
    sample = FlightProgramSample(
        elapsed_min=10.0,
        mode="SPM",
        event_name="",
        position_m=(0.0, 2000.0, 0.0),
        velocity_mps=(-1.0, 0.0, 0.0),
        subsatellite_longitude_deg=0.0,
        subsatellite_latitude_deg=0.0,
        altitude_m=0.0,
        plus_z_ecef=(0.0, 0.0, 1.0),
        sun_ecef=(1.0, 0.0, 0.0),
        earth_ecef=(0.0, -1.0, 0.0),
        in_shadow=False,
    )

    position = page._subsatellite_position_km_for_sample(sample)

    assert np.isclose(position[0], 0.0)
    assert np.isclose(position[1], EARTH_RADIUS_KM)
    assert np.isclose(position[2], 0.0)


def test_earth_rotation_uses_orbit_history_reference_epoch(monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    page = FlightProgramPage(I18nManager("zh"), ProjectWorkspace())
    page._tracking_results = {"leading": _tracking_result()}
    monkeypatch.setattr(page, "_orbit_history_rows", lambda: [{"elapsed_time_min": 0.0}])
    page._orbit_history_cache_key = ("orbit.csv", 1, 1)
    monkeypatch.setattr("smart.ui.widgets.flight_program_page.derive_scenario_epoch_utc", lambda _rows: "2024-01-01T00:00:00Z")
    captured = {}

    def fake_gmst(epoch):
        captured["epoch"] = epoch
        return 0.75

    monkeypatch.setattr("smart.ui.widgets.flight_program_page._gmst_rad", fake_gmst)

    value = page._earth_rotation_rad_for_sample(10.0)

    assert value == 0.75
    assert captured["epoch"] == parse_utc("2024-01-01T00:10:00Z")


def _tracking_result() -> TrackingArcOrbitResult:
    return TrackingArcOrbitResult(
        point_key="leading",
        point_label="窗口前沿轨道",
        launch_utc="2026-05-15T00:00:00Z",
        t0_utc="2026-05-15T00:00:00Z",
        timeline_start_utc="2026-05-15T00:00:00Z",
        timeline_end_utc="2026-05-15T02:00:00Z",
        row_labels=[],
        segments=[],
        asset_summaries=[],
        shadow_total_min=0.0,
        maneuver_count=0,
    )
