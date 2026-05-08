from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from smart.ui.main_window import MainWindow, _NAV_KEYS
from smart.ui.nav_icons import chevron_icon, has_icon, nav_icon


def test_each_nav_key_has_an_icon() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

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
        assert window._subtitle_label.isHidden() is False

        window._toggle_sidebar_collapsed()
        assert window._sidebar_collapsed is True
        collapsed_width = window._sidebar_frame.width()
        assert collapsed_width < expanded_width
        assert window._nav_list.item(0).text() == ""
        assert window._nav_list.item(0).toolTip() != ""
        assert window._subtitle_label.isHidden() is True
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
