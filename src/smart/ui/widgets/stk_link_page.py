from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.project_workspace import ProjectWorkspace
from smart.services.stk_link import StkLinkResult, StkLinkService
from smart.ui.i18n import I18nManager


@dataclass(frozen=True, slots=True)
class _OperationResult:
    message: str
    detail: str = ""
    result: StkLinkResult | None = None


class _StkOperationWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, operation: Callable[[], _OperationResult]) -> None:
        super().__init__()
        self._operation = operation

    @QtCore.Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self._operation())
        except Exception as exc:
            self.failed.emit(str(exc))


class StkLinkPage(QtWidgets.QWidget):
    def __init__(
        self,
        i18n: I18nManager,
        workspace: ProjectWorkspace,
        stk_link_service: StkLinkService | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._workspace = workspace
        self._stk_link_service = stk_link_service or StkLinkService(self._workspace)
        self._thread: QtCore.QThread | None = None
        self._worker: _StkOperationWorker | None = None
        self._status_role = "statusDisconnected"

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        self._title_label = QtWidgets.QLabel("STK 联动")
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel(
            "启动本机 STK 11.6，创建场景，并把当前 SMART 项目的主星轨道、姿态、测控站和中继星同步到 STK。"
        )
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_control_card())
        splitter.addWidget(self._build_log_card())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 900])

        self._status_label = QtWidgets.QLabel("未连接 STK。")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)
        self.refresh_from_workspace()

    def refresh_from_workspace(self) -> None:
        project = self._workspace.current_project
        if project is None:
            self._project_label.setText("当前项目：--")
            self._history_label.setText("轨道数据：--")
            self._attitude_label.setText("姿态配置：--")
            self._config_label.setText("测控/中继配置：--")
            self._asset_table.setRowCount(0)
            self._create_scene_button.setEnabled(False)
            self._sync_button.setEnabled(False)
            return

        self._project_label.setText(f"当前项目：{project.name}\n{project.root_dir}")
        self._history_label.setText(f"轨道数据：{self._workspace.data_dir() / 'full_orbit_history.csv'}")
        self._attitude_label.setText(f"姿态配置：{self._workspace.flight_program_path()}")
        self._config_label.setText(f"测控/中继配置：{self._workspace.satellite_status_path()}")
        self._populate_asset_table()
        enabled = self._thread is None
        self._create_scene_button.setEnabled(enabled)
        self._sync_button.setEnabled(enabled)

    def _build_control_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = self._card_title("操作")
        layout.addWidget(title)

        self._project_label = self._path_label()
        self._history_label = self._path_label()
        self._attitude_label = self._path_label()
        self._config_label = self._path_label()
        layout.addWidget(self._project_label)
        layout.addWidget(self._history_label)
        layout.addWidget(self._attitude_label)
        layout.addWidget(self._config_label)

        button_grid = QtWidgets.QGridLayout()
        button_grid.setHorizontalSpacing(10)
        button_grid.setVerticalSpacing(10)

        self._launch_button = QtWidgets.QPushButton("启动本地 STK 11.6")
        self._launch_button.clicked.connect(self._launch_stk)
        button_grid.addWidget(self._launch_button, 0, 0)

        self._create_scene_button = QtWidgets.QPushButton("建立新场景")
        self._create_scene_button.setProperty("variant", "secondary")
        self._create_scene_button.clicked.connect(self._create_scenario)
        button_grid.addWidget(self._create_scene_button, 0, 1)

        self._sync_button = QtWidgets.QPushButton("同步当前项目到 STK")
        self._sync_button.clicked.connect(self._sync_project)
        button_grid.addWidget(self._sync_button, 1, 0, 1, 2)
        layout.addLayout(button_grid)

        asset_title = self._card_title("将同步的测控资源")
        layout.addWidget(asset_title)
        self._asset_table = QtWidgets.QTableWidget(0, 4)
        self._asset_table.setHorizontalHeaderLabels(["类型", "名称", "经度/轨位", "纬度"])
        self._asset_table.verticalHeader().setVisible(False)
        self._asset_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._asset_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._asset_table.horizontalHeader().setStretchLastSection(True)
        self._asset_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._asset_table, 1)
        return card

    def _build_log_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(self._card_title("执行日志"))

        self._log = QtWidgets.QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setWordWrapMode(QtGui.QTextOption.WrapMode.NoWrap)
        self._log.setPlaceholderText("STK 命令和生成文件会显示在这里。")
        layout.addWidget(self._log, 1)

        clear_button = QtWidgets.QPushButton("清空日志")
        clear_button.setProperty("variant", "secondary")
        clear_button.clicked.connect(self._log.clear)
        layout.addWidget(clear_button, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        return card

    def _launch_stk(self) -> None:
        self._run_operation(
            lambda: _OperationResult(
                "已连接本地 STK 11.6。",
                detail=str(getattr(self._stk_link_service.connect(), "root", "")),
            )
        )

    def _create_scenario(self) -> None:
        self._run_operation(
            lambda: _OperationResult(f"已建立新场景：{self._stk_link_service.create_new_scenario()}")
        )

    def _sync_project(self) -> None:
        def operation() -> _OperationResult:
            result = self._stk_link_service.import_project_to_stk()
            detail_lines = [
                f"场景：{result.scenario_name}",
                f"主星：{result.satellite_name}",
                f"地面站：{result.ground_station_count}",
                f"中继星：{result.relay_satellite_count}",
            ]
            artifacts = result.artifacts
            for path in (
                artifacts.orbit_ephemeris_path,
                artifacts.attitude_path,
                *artifacts.relay_ephemeris_paths,
            ):
                if path is not None:
                    detail_lines.append(f"文件：{path}")
            detail_lines.extend(result.commands)
            return _OperationResult("当前项目已同步到 STK。", "\n".join(detail_lines), result)

        self._run_operation(operation)

    def _run_operation(self, operation: Callable[[], _OperationResult]) -> None:
        if self._thread is not None:
            return
        self._set_busy(True)
        self._set_status("statusLoading", "STK 操作执行中。")
        self._thread = QtCore.QThread(self)
        self._worker = _StkOperationWorker(self._fresh_stk_operation(operation))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_operation_finished)
        self._worker.failed.connect(self._on_operation_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _fresh_stk_operation(self, operation: Callable[[], _OperationResult]) -> Callable[[], _OperationResult]:
        def wrapped() -> _OperationResult:
            self._stk_link_service.clear_executor()
            try:
                return operation()
            finally:
                self._stk_link_service.clear_executor()

        return wrapped

    @QtCore.Slot(object)
    def _on_operation_finished(self, payload: object) -> None:
        result = payload if isinstance(payload, _OperationResult) else _OperationResult(str(payload))
        self._append_log(result.message)
        if result.detail:
            self._append_log(result.detail)
        self._set_status("statusReady", result.message)

    @QtCore.Slot(str)
    def _on_operation_failed(self, error: str) -> None:
        self._append_log(f"失败：{error}")
        self._set_status("statusDisconnected", f"STK 操作失败：{error}")

    @QtCore.Slot()
    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._set_busy(False)
        self.refresh_from_workspace()

    def _set_busy(self, busy: bool) -> None:
        for widget in (self._launch_button, self._create_scene_button, self._sync_button):
            widget.setEnabled(not busy)

    def _append_log(self, text: str) -> None:
        if not text:
            return
        self._log.appendPlainText(text)
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

    def _populate_asset_table(self) -> None:
        self._asset_table.setRowCount(0)
        try:
            settings = self._workspace.load_satellite_status()
        except Exception:
            settings = None
        if settings is None:
            return
        for asset in settings.ground_assets:
            self._append_asset_row(
                "地面站/船",
                asset.name,
                f"{float(asset.longitude_deg):.6f}",
                f"{float(asset.latitude_deg):.6f}",
            )
        for relay in settings.relay_satellites:
            self._append_asset_row("中继星", relay.name, relay.orbital_slot_orbit, "0.000000")

    def _append_asset_row(self, asset_type: str, name: str, lon_or_slot: str, lat: str) -> None:
        row = self._asset_table.rowCount()
        self._asset_table.insertRow(row)
        for column, value in enumerate((asset_type, name, lon_or_slot, lat)):
            item = QtWidgets.QTableWidgetItem(value)
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._asset_table.setItem(row, column, item)

    def _set_status(self, role: str, text: str) -> None:
        self._status_role = role
        self._status_label.setProperty("role", role)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setText(text)

    @staticmethod
    def _card() -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        return card

    @staticmethod
    def _card_title(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "cardTitle")
        return label

    @staticmethod
    def _path_label() -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setProperty("role", "cardCaption")
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)
