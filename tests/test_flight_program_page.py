from __future__ import annotations

from PySide6 import QtWidgets
from PySide6 import QtCore, QtTest
from PySide6.QtCore import Qt

from smart.services.flight_program import DEPLOYMENT_KIND, FlightProgramSample, normalize_flight_program_payload
from smart.services.project_workspace import ProjectWorkspace
from smart.services.tracking_arc import TrackingArcOrbitResult
from smart.ui.i18n import I18nManager
from smart.ui.widgets.flight_program_page import FlightProgramOverviewWidget, FlightProgramPage


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

    calls = {"event": 0, "reference": 0}
    monkeypatch.setattr(page, "_refresh_event_table", lambda: calls.__setitem__("event", calls["event"] + 1))
    monkeypatch.setattr(page, "_refresh_reference_table", lambda: calls.__setitem__("reference", calls["reference"] + 1))
    monkeypatch.setattr(page, "_refresh_sample_preview", lambda: None)

    page._set_playhead(12.0)

    assert calls == {"event": 0, "reference": 0}
    assert page._event_table.item(0, 10).text() == "当前"
    assert page._reference_table.item(0, 8).text() == "当前"

    page._set_playhead(30.0)

    assert page._event_table.item(0, 10).text() == "正常"
    assert page._reference_table.item(0, 8).text() == "正常"
    assert page._event_table.item(0, 10).foreground().style() == Qt.BrushStyle.NoBrush
    assert page._reference_table.item(0, 8).foreground().style() == Qt.BrushStyle.NoBrush


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
    assert page._event_table.item(0, 4).text() == "太阳指向巡航"
    assert page._major_event_table.item(0, 2).text() == "主要事件"


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
        def set_trajectory_overlays(self, trajectory, body_radius_km, *, maneuver_segments_km=None, start_label=None):
            calls["trajectory"] = trajectory
            calls["body_radius_km"] = body_radius_km

        def set_direction_vectors(self, origin_km, vectors):
            calls["origin_km"] = origin_km.tolist()
            calls["vectors"] = vectors

        def set_info_overlay(self, text):
            calls["overlay"] = text

        def clear_trajectory(self):
            calls["cleared"] = True

    page._scene_view = FakeSceneView()

    page._update_orbit_view_for_sample(sample)

    assert calls["origin_km"] == [0.0, 2.0, 0.0]
    vectors = calls["vectors"]
    assert [item["label"] for item in vectors] == ["Earth", "Sun"]
    assert vectors[0]["direction"].tolist() == [0.0, -2.0, 0.0]
    assert vectors[1]["direction"].tolist() == [1.0, 0.0, 0.0]
    assert "当前时间（北京）" in calls["overlay"]


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

    text = page._sample_overlay_text(sample)

    assert "当前时间（北京）：2026-05-15 08:10:00" in text
    assert "星下点：经度 109.123° / 纬度 18.568°" in text
    assert "卫星姿态：EPM / 测控姿态" in text
    assert "主要测控事件：当前 地面站可见：地面站 Sanya" in text


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
