from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from smart.ui.main_window import MainWindow, _NAV_KEYS
from smart.ui.nav_icons import chevron_icon, has_icon, nav_icon


def test_each_nav_key_has_an_icon() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    assert _NAV_KEYS.index("nav.design_maneuver_strategy") < _NAV_KEYS.index("nav.maneuver_strategy")

    for key in _NAV_KEYS:
        assert has_icon(key), f"nav key {key} is missing an icon"
        icon = nav_icon(key)
        assert not icon.isNull(), f"nav icon for {key} should not be empty"

    assert not chevron_icon("left").isNull()
    assert not chevron_icon("right").isNull()


def test_nav_items_carry_icons_and_tooltips() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = MainWindow()
    try:
        nav = window._nav_list
        assert nav.count() == len(_NAV_KEYS)
        for index, key in enumerate(_NAV_KEYS):
            item = nav.item(index)
            assert item is not None
            assert not item.icon().isNull(), f"nav item for {key} should have an icon"
            assert item.toolTip(), f"nav item for {key} should have a tooltip"
            assert item.data(QtCore.Qt.ItemDataRole.UserRole) == key
        assert not hasattr(window, "_subtitle_label")
        assert not hasattr(window, "_footer_label")
        assert window._project_name_label.property("role") == "sidebarProjectName"
        assert window._project_path_label.property("role") == "sidebarProjectPath"
    finally:
        window.deleteLater()


def test_main_window_minimum_height_fits_common_desktop() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = MainWindow()
    try:
        assert window.minimumSizeHint().height() <= 1000
    finally:
        window.deleteLater()


def test_project_menu_has_save_as_and_close_actions(tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = MainWindow()
    try:
        assert window._save_project_as_action.text() == "项目另存为..."
        assert window._close_project_action.text() == "关闭当前项目"
        assert window._save_project_as_action.isEnabled() is False
        assert window._close_project_action.isEnabled() is False

        window._projects_root = tmp_path
        window._workspace.create_project("menu-actions", parent_dir=tmp_path)
        window._refresh_project_actions()

        assert window._save_project_as_action.isEnabled() is True
        assert window._close_project_action.isEnabled() is True

        window._workspace.close_project()
        window._refresh_project_actions()

        assert window._save_project_as_action.isEnabled() is False
        assert window._close_project_action.isEnabled() is False
    finally:
        window.deleteLater()


def test_common_tools_menu_exposes_orbital_analysis_actions() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = MainWindow()
    try:
        assert window._common_tools_menu.title() == "常用工具"
        assert [action.text() for action in window._common_tools_menu.actions()] == [
            "轨道六根数 / 状态矢量转换",
            "太阳月亮位置计算",
            "霍夫曼转移计算",
        ]
    finally:
        window.deleteLater()


def test_sidebar_toggle_collapses_and_restores_labels() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = MainWindow()
    try:
        if window._sidebar_collapsed:
            window._toggle_sidebar_collapsed()
        assert window._sidebar_collapsed is False
        expanded_width = window._sidebar_frame.width()
        first_item_text_expanded = window._nav_list.item(0).text()
        assert first_item_text_expanded != ""
        assert window._project_header_label.isHidden() is False

        window._toggle_sidebar_collapsed()
        assert window._sidebar_collapsed is True
        collapsed_width = window._sidebar_frame.width()
        assert collapsed_width < expanded_width
        assert window._nav_list.item(0).text() == ""
        assert window._nav_list.item(0).toolTip() != ""
        assert window._project_header_label.isHidden() is True
        assert window._brand_title_label.isHidden() is True

        window._toggle_sidebar_collapsed()
        assert window._sidebar_collapsed is False
        assert window._sidebar_frame.width() == expanded_width
        assert window._nav_list.item(0).text() == first_item_text_expanded
        assert window._brand_title_label.isHidden() is False
    finally:
        window.deleteLater()


def test_sidebar_collapsed_state_persists_via_qsettings() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    settings = QtCore.QSettings("SMART", "SMART")
    previous = settings.value("sidebar/collapsed", False, type=bool)
    try:
        window = MainWindow()
        try:
            if window._sidebar_collapsed:
                window._toggle_sidebar_collapsed()
            assert window._sidebar_collapsed is False
            window._toggle_sidebar_collapsed()
            assert settings.value("sidebar/collapsed", False, type=bool) is True
        finally:
            window.deleteLater()

        window2 = MainWindow()
        try:
            assert window2._sidebar_collapsed is True
            assert window2._sidebar_frame.width() == window2._sidebar_collapsed_width
        finally:
            window2.deleteLater()
    finally:
        settings.setValue("sidebar/collapsed", bool(previous))
