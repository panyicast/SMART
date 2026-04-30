from __future__ import annotations

import sys

from PySide6 import QtWidgets

from smart.app_runtime import configure_graphics_backend, load_app_icon


def main() -> int:
    configure_graphics_backend()

    import pyqtgraph as pg

    from smart.ui.main_window import MainWindow
    from smart.ui.theme import apply_theme

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("SMART")
    app.setOrganizationName("SMART")
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    pg.setConfigOptions(antialias=True)
    apply_theme(app)

    window = MainWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
