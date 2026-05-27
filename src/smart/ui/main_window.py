from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.domain.models import OrbitInitializationSettings, SatelliteStructureConfig
from smart.services.project_workspace import ProjectInfo, ProjectWorkspace
from smart.services.spice_service import SpiceKernelManager
from smart.services.stk_link import StkLinkService
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState
from smart.ui.nav_icons import chevron_icon, nav_icon
from smart.ui.widgets.dashboard_page import DashboardPage
from smart.ui.widgets.data_visualization_page import DataVisualizationPage
from smart.ui.widgets.design_maneuver_strategy_page import DesignManeuverStrategyPage
from smart.ui.widgets.ai_project_analysis_page import AIProjectAnalysisPage
from smart.ui.widgets.flight_program_page import FlightProgramPage
from smart.ui.widgets.launch_window_page import LaunchWindowPage
from smart.ui.widgets.maneuver_page import ManeuverPage
from smart.ui.widgets.satellite_status_page import Satellite3DModelPage
from smart.ui.widgets.spice_kernel_page import SpiceKernelPage
from smart.ui.widgets.stk_link_page import StkLinkPage
from smart.ui.widgets.tracking_arc_page import TrackingArcPage
from smart.ui.widgets.common_orbital_tools import (
    AnomalyConversionDialog,
    ApsisParametersDialog,
    CircularOrbitPeriodDialog,
    HohmannTransferDialog,
    LambertTransferDialog,
    OrbitalConversionDialog,
    PlaneChangeDialog,
    SolarLunarPositionDialog,
    TwoBodyPropagationDialog,
)

_MAX_RECENT_PROJECTS = 8
_NAV_KEYS = [
    "nav.dashboard",
    "nav.orbit_design",
    "nav.design_maneuver_strategy",
    "nav.maneuver_strategy",
    "nav.launch_window",
    "nav.tracking_arc",
    "nav.flight_program",
    "nav.data_visualization",
    "nav.stk_link",
    "nav.ai_project_analysis",
]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(1144, 720)
        self.resize(1560, 920)
        self._workspace_root = Path.cwd()
        self._i18n = I18nManager(language="zh")
        self._mission_state = MissionState()
        self._workspace = ProjectWorkspace()
        self._stk_link_service = StkLinkService(self._workspace)
        self._projects_root = self._workspace.projects_dir(self._workspace_root)
        self._spice_manager = SpiceKernelManager()
        self._latest_satellite_model_config: SatelliteStructureConfig | None = None
        self._latest_maneuver_strategy: dict[str, Any] | None = None
        self._autosave_enabled = True
        self._settings = QtCore.QSettings("SMART", "SMART")
        self._recent_project_paths = self._load_recent_project_paths()
        self._recent_project_actions: list[QtGui.QAction] = []
        self._sidebar_expanded_width = 280
        self._sidebar_collapsed_width = 72
        self._sidebar_collapsed = bool(self._settings.value("sidebar/collapsed", False, type=bool))
        self._stack = QtWidgets.QStackedWidget()
        self._stack.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)

        shell = QtWidgets.QWidget()
        self.setCentralWidget(shell)
        layout = QtWidgets.QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar(), 0)
        layout.addWidget(self._stack, 1)

        self._dashboard_page = DashboardPage(self._mission_state, self._i18n)
        self._dashboard_page.new_project_requested.connect(self._create_project)
        self._dashboard_page.open_project_requested.connect(self._open_project)
        self._dashboard_page.recent_project_requested.connect(self._open_recent_project)
        self._dashboard_page.set_recent_projects(self._recent_project_paths)
        self._satellite_page = Satellite3DModelPage(self._i18n)
        self._maneuver_page = ManeuverPage(self._i18n, self._workspace)
        self._design_maneuver_page = DesignManeuverStrategyPage(self._i18n, self._workspace)
        self._launch_window_page = LaunchWindowPage(self._i18n, self._workspace)
        self._ai_project_page = AIProjectAnalysisPage(self._i18n, self._workspace, self._settings)
        self._tracking_arc_page = TrackingArcPage(self._i18n, self._workspace)
        self._flight_program_page = FlightProgramPage(
            self._i18n,
            self._workspace,
            stk_link_service_factory=lambda: self._stk_link_service,
        )
        self._viz_page = DataVisualizationPage(self._mission_state, self._i18n, self._workspace)
        self._stk_link_page = StkLinkPage(self._i18n, self._workspace, self._stk_link_service)
        self._spice_page = SpiceKernelPage(
            self._spice_manager,
            self._i18n,
            initial_kernel_root=(Path.cwd() / "data" / "kernels"),
        )

        self._pages = [
            self._dashboard_page,
            self._satellite_page,
            self._design_maneuver_page,
            self._maneuver_page,
            self._launch_window_page,
            self._tracking_arc_page,
            self._flight_program_page,
            self._viz_page,
            self._stk_link_page,
            self._ai_project_page,
        ]
        for page in self._pages:
            self._stack.addWidget(page)
        self._stack.addWidget(self._spice_page)

        self._build_menu()
        self._mission_state.trajectory_changed.connect(self._on_trajectory_changed)
        self._satellite_page.settings_changed.connect(self._on_satellite_settings_changed)
        self._maneuver_page.strategy_changed.connect(self._on_maneuver_strategy_changed)
        self._latest_satellite_model_config = self._satellite_page.settings()
        self._reset_spice_workspace(self._workspace_root / "data" / "kernels")

        self.retranslate()
        self._nav_list.setCurrentRow(0)
        self._dashboard_page.set_project(None)
        self.statusBar().showMessage(self._i18n.t("project.status.no_project"))

    def _build_menu(self) -> None:
        self._project_menu = self.menuBar().addMenu("")
        self._new_project_action = QtGui.QAction(self)
        self._new_project_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+N"))
        self._new_project_action.triggered.connect(self._create_project)
        self._project_menu.addAction(self._new_project_action)

        self._open_project_action = QtGui.QAction(self)
        self._open_project_action.setShortcut(QtGui.QKeySequence.Open)
        self._open_project_action.triggered.connect(self._open_project)
        self._project_menu.addAction(self._open_project_action)

        self._save_project_as_action = QtGui.QAction(self)
        self._save_project_as_action.setShortcut(QtGui.QKeySequence.SaveAs)
        self._save_project_as_action.triggered.connect(self._save_project_as)
        self._project_menu.addAction(self._save_project_as_action)

        self._close_project_action = QtGui.QAction(self)
        self._close_project_action.setShortcut(QtGui.QKeySequence.Close)
        self._close_project_action.triggered.connect(self._close_current_project)
        self._project_menu.addAction(self._close_project_action)

        self._project_menu.addSeparator()
        self._recent_projects_header_action = QtGui.QAction(self)
        self._recent_projects_header_action.setEnabled(False)
        self._project_menu.addAction(self._recent_projects_header_action)

        self._no_recent_projects_action = QtGui.QAction(self)
        self._no_recent_projects_action.setEnabled(False)
        self._project_menu.addAction(self._no_recent_projects_action)

        self._refresh_recent_project_actions()
        self._refresh_project_actions()

        self._common_tools_menu = self.menuBar().addMenu("")

        self._orbit_conversion_action = QtGui.QAction(self)
        self._orbit_conversion_action.triggered.connect(self._open_orbit_conversion_tool)
        self._common_tools_menu.addAction(self._orbit_conversion_action)

        self._apsis_parameters_action = QtGui.QAction(self)
        self._apsis_parameters_action.triggered.connect(self._open_apsis_parameters_tool)
        self._common_tools_menu.addAction(self._apsis_parameters_action)

        self._circular_period_action = QtGui.QAction(self)
        self._circular_period_action.triggered.connect(self._open_circular_period_tool)
        self._common_tools_menu.addAction(self._circular_period_action)

        self._anomaly_conversion_action = QtGui.QAction(self)
        self._anomaly_conversion_action.triggered.connect(self._open_anomaly_conversion_tool)
        self._common_tools_menu.addAction(self._anomaly_conversion_action)

        self._sun_moon_position_action = QtGui.QAction(self)
        self._sun_moon_position_action.triggered.connect(self._open_sun_moon_position_tool)
        self._common_tools_menu.addAction(self._sun_moon_position_action)

        self._hohmann_transfer_action = QtGui.QAction(self)
        self._hohmann_transfer_action.triggered.connect(self._open_hohmann_transfer_tool)
        self._common_tools_menu.addAction(self._hohmann_transfer_action)

        self._plane_change_action = QtGui.QAction(self)
        self._plane_change_action.triggered.connect(self._open_plane_change_tool)
        self._common_tools_menu.addAction(self._plane_change_action)

        self._lambert_transfer_action = QtGui.QAction(self)
        self._lambert_transfer_action.triggered.connect(self._open_lambert_transfer_tool)
        self._common_tools_menu.addAction(self._lambert_transfer_action)

        self._two_body_propagation_action = QtGui.QAction(self)
        self._two_body_propagation_action.triggered.connect(self._open_two_body_propagation_tool)
        self._common_tools_menu.addAction(self._two_body_propagation_action)

        self._settings_menu = self.menuBar().addMenu("")
        self._spice_kernels_action = QtGui.QAction(self)
        self._spice_kernels_action.triggered.connect(self._open_spice_kernel_settings)
        self._settings_menu.addAction(self._spice_kernels_action)

    def _build_sidebar(self) -> QtWidgets.QWidget:
        sidebar = QtWidgets.QFrame()
        sidebar.setProperty("role", "sidebar")
        self._sidebar_frame = sidebar

        layout = QtWidgets.QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 22, 20, 22)
        layout.setSpacing(16)
        self._sidebar_layout = layout

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        self._brand_title_label = QtWidgets.QLabel("SMART")
        self._brand_title_label.setProperty("role", "brandTitle")
        header_row.addWidget(self._brand_title_label, 1)

        self._sidebar_toggle_button = QtWidgets.QToolButton()
        self._sidebar_toggle_button.setProperty("role", "sidebarToggle")
        self._sidebar_toggle_button.setAutoRaise(True)
        self._sidebar_toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._sidebar_toggle_button.setIconSize(QtCore.QSize(20, 20))
        self._sidebar_toggle_button.setFixedSize(32, 32)
        self._sidebar_toggle_button.clicked.connect(self._toggle_sidebar_collapsed)
        header_row.addWidget(self._sidebar_toggle_button, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header_row)

        self._project_header_label = QtWidgets.QLabel()
        self._project_header_label.setProperty("role", "brandSubtitle")
        layout.addWidget(self._project_header_label)

        self._project_name_label = QtWidgets.QLabel()
        self._project_name_label.setProperty("role", "sidebarProjectName")
        self._project_name_label.setWordWrap(True)
        layout.addWidget(self._project_name_label)

        self._project_path_label = QtWidgets.QLabel()
        self._project_path_label.setProperty("role", "sidebarProjectPath")
        self._project_path_label.setWordWrap(True)
        layout.addWidget(self._project_path_label)

        self._nav_list = QtWidgets.QListWidget()
        self._nav_list.setProperty("role", "nav")
        self._nav_list.setSpacing(2)
        self._nav_list.setIconSize(QtCore.QSize(22, 22))
        self._nav_list.setUniformItemSizes(True)
        for key in _NAV_KEYS:
            item = QtWidgets.QListWidgetItem()
            item.setIcon(nav_icon(key))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
            self._nav_list.addItem(item)
        self._nav_list.currentRowChanged.connect(self._stack.setCurrentIndex)
        layout.addWidget(self._nav_list, 1)

        # Collapsible labels grouped for easy hide/show.
        self._sidebar_collapsible_widgets: list[QtWidgets.QWidget] = [
            self._project_header_label,
            self._project_name_label,
            self._project_path_label,
        ]

        self._apply_sidebar_collapsed(self._sidebar_collapsed, persist=False)
        return sidebar

    def _open_orbit_conversion_tool(self) -> None:
        OrbitalConversionDialog(self._i18n, self).exec()

    def _open_sun_moon_position_tool(self) -> None:
        SolarLunarPositionDialog(self._i18n, self._spice_manager, self).exec()

    def _open_apsis_parameters_tool(self) -> None:
        ApsisParametersDialog(self._i18n, self).exec()

    def _open_circular_period_tool(self) -> None:
        CircularOrbitPeriodDialog(self._i18n, self).exec()

    def _open_anomaly_conversion_tool(self) -> None:
        AnomalyConversionDialog(self._i18n, self).exec()

    def _open_hohmann_transfer_tool(self) -> None:
        HohmannTransferDialog(self._i18n, self).exec()

    def _open_plane_change_tool(self) -> None:
        PlaneChangeDialog(self._i18n, self).exec()

    def _open_lambert_transfer_tool(self) -> None:
        LambertTransferDialog(self._i18n, self).exec()

    def _open_two_body_propagation_tool(self) -> None:
        TwoBodyPropagationDialog(self._i18n, self).exec()

    def _open_spice_kernel_settings(self) -> None:
        self._nav_list.blockSignals(True)
        self._nav_list.setCurrentRow(-1)
        self._nav_list.blockSignals(False)
        self._stack.setCurrentWidget(self._spice_page)

    def _toggle_sidebar_collapsed(self) -> None:
        self._apply_sidebar_collapsed(not self._sidebar_collapsed, persist=True)

    def _apply_sidebar_collapsed(self, collapsed: bool, *, persist: bool) -> None:
        self._sidebar_collapsed = bool(collapsed)
        target_width = self._sidebar_collapsed_width if collapsed else self._sidebar_expanded_width
        self._sidebar_frame.setFixedWidth(target_width)
        self._sidebar_frame.setProperty("collapsed", "true" if collapsed else "false")
        self._nav_list.setProperty("collapsed", "true" if collapsed else "false")

        if collapsed:
            self._sidebar_layout.setContentsMargins(8, 18, 8, 18)
            self._sidebar_layout.setSpacing(10)
            self._brand_title_label.setVisible(False)
        else:
            self._sidebar_layout.setContentsMargins(20, 22, 20, 22)
            self._sidebar_layout.setSpacing(16)
            self._brand_title_label.setVisible(True)

        for widget in self._sidebar_collapsible_widgets:
            widget.setVisible(not collapsed)

        # Force style refresh because dynamic property changed.
        for widget in (self._sidebar_frame, self._nav_list):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

        self._refresh_nav_item_labels()
        self._refresh_sidebar_toggle_button()

        if persist:
            self._settings.setValue("sidebar/collapsed", self._sidebar_collapsed)

    def _refresh_nav_item_labels(self) -> None:
        t = self._i18n.t
        for index, key in enumerate(_NAV_KEYS):
            item = self._nav_list.item(index)
            if item is None:
                continue
            label = t(key)
            item.setText("" if self._sidebar_collapsed else label)
            item.setToolTip(label)
            item.setTextAlignment(
                QtCore.Qt.AlignmentFlag.AlignCenter
                if self._sidebar_collapsed
                else QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft
            )

    def _refresh_sidebar_toggle_button(self) -> None:
        t = self._i18n.t
        if self._sidebar_collapsed:
            self._sidebar_toggle_button.setIcon(chevron_icon("right"))
            self._sidebar_toggle_button.setToolTip(t("sidebar.toggle_expand"))
        else:
            self._sidebar_toggle_button.setIcon(chevron_icon("left"))
            self._sidebar_toggle_button.setToolTip(t("sidebar.toggle_collapse"))

    def _create_project(self) -> None:
        t = self._i18n.t
        project_name, ok = QtWidgets.QInputDialog.getText(
            self,
            t("project.new_title"),
            t("project.new_prompt"),
        )
        if not ok:
            return

        normalized = project_name.strip()
        if not normalized:
            QtWidgets.QMessageBox.warning(
                self,
                t("project.error.invalid_name_title"),
                t("project.error.invalid_name_body"),
            )
            return

        try:
            info = self._workspace.create_project(normalized, self._projects_root)
        except FileExistsError:
            QtWidgets.QMessageBox.warning(
                self,
                t("project.error.exists_title"),
                t("project.error.exists_body", path=str((self._projects_root / normalized).resolve())),
            )
            return
        except Exception as exc:  # pragma: no cover - UI path
            QtWidgets.QMessageBox.critical(
                self,
                t("project.error.create_failed_title"),
                t("project.error.create_failed_body", error=str(exc)),
            )
            return

        self._activate_project(info, load_saved_data=False)
        self.statusBar().showMessage(t("project.status.created", name=info.name), 5000)

    def _open_project(self) -> None:
        t = self._i18n.t
        start_dir = self._projects_root if self._projects_root.exists() else self._workspace_root
        selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            t("project.open_title"),
            str(start_dir),
        )
        if not selected_dir:
            return

        try:
            info = self._workspace.open_project(selected_dir)
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(
                self,
                t("project.error.not_project_title"),
                t("project.error.not_project_body"),
            )
            return
        except Exception as exc:  # pragma: no cover - UI path
            QtWidgets.QMessageBox.critical(
                self,
                t("project.error.open_failed_title"),
                t("project.error.open_failed_body", error=str(exc)),
            )
            return

        self._activate_project(info, load_saved_data=True)
        self.statusBar().showMessage(t("project.status.loaded", name=info.name), 5000)

    def _open_recent_project(self, project_path: str) -> None:
        t = self._i18n.t
        try:
            info = self._workspace.open_project(project_path)
        except FileNotFoundError:
            self._remove_recent_project_path(project_path)
            QtWidgets.QMessageBox.warning(
                self,
                t("project.error.not_project_title"),
                t("project.error.not_project_body"),
            )
            return
        except Exception as exc:  # pragma: no cover - UI path
            QtWidgets.QMessageBox.critical(
                self,
                t("project.error.open_failed_title"),
                t("project.error.open_failed_body", error=str(exc)),
            )
            return

        self._activate_project(info, load_saved_data=True)
        self.statusBar().showMessage(t("project.status.loaded", name=info.name), 5000)

    def _save_project_as(self) -> None:
        t = self._i18n.t
        if self._workspace.current_project is None:
            return

        current_project = self._workspace.current_project
        start_dir = current_project.root_dir.parent if current_project is not None else self._projects_root
        selected_dir = self._select_project_save_as_directory(start_dir)
        if selected_dir is None:
            return

        self._persist_project_outputs()
        self._persist_satellite_config()
        try:
            info = self._workspace.save_project_as(selected_dir)
        except Exception as exc:  # pragma: no cover - UI path
            QtWidgets.QMessageBox.critical(
                self,
                t("project.error.save_as_failed_title"),
                t("project.error.save_as_failed_body", error=str(exc)),
            )
            return

        self._activate_project(info, load_saved_data=True)
        self.statusBar().showMessage(t("project.status.saved_as", name=info.name), 5000)

    def _select_project_save_as_directory(self, start_dir: Path) -> Path | None:
        dialog = QtWidgets.QFileDialog(self, self._i18n.t("project.save_as_title"), str(start_dir))
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.AnyFile)
        dialog.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
        dialog.setLabelText(QtWidgets.QFileDialog.DialogLabel.Accept, self._i18n.t("project.save_as_accept"))
        current_project = self._workspace.current_project
        if current_project is not None:
            dialog.selectFile(f"{current_project.name}_copy")
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        selected_files = dialog.selectedFiles()
        if not selected_files:
            return None
        return Path(selected_files[0]).expanduser().resolve()

    def _close_current_project(self) -> None:
        if self._workspace.current_project is None:
            return

        self._persist_project_outputs()
        self._persist_satellite_config()
        self._workspace.close_project()
        self._latest_maneuver_strategy = None
        self._reset_spice_workspace(self._workspace_root / "data" / "kernels")

        self._autosave_enabled = False
        try:
            self._maneuver_page.refresh_from_workspace()
            self._design_maneuver_page.refresh_from_workspace()
            self._launch_window_page.refresh_from_workspace()
            self._tracking_arc_page.refresh_from_workspace()
            self._flight_program_page.refresh_from_workspace()
            self._viz_page.refresh_from_workspace()
            self._stk_link_page.refresh_from_workspace()
        finally:
            self._autosave_enabled = True

        self._refresh_project_labels()
        self._refresh_project_actions()
        self._dashboard_page.set_project(None)
        self._nav_list.setCurrentRow(0)
        self.statusBar().showMessage(self._i18n.t("project.status.closed"), 5000)

    def _activate_project(self, info: ProjectInfo, load_saved_data: bool) -> None:
        self._autosave_enabled = False
        self._latest_satellite_model_config = None
        self._latest_maneuver_strategy = None
        try:
            self._reset_spice_workspace(self._workspace.kernels_dir())
            if load_saved_data:
                saved_initialization = self._workspace.load_orbit_initialization()
                if saved_initialization is not None:
                    self._mission_state.update_initialization(saved_initialization)
                else:
                    saved_elements = self._workspace.load_orbit_elements()
                    if saved_elements is not None:
                        self._mission_state.update_initialization(
                            OrbitInitializationSettings(
                                mode="classical",
                                epoch_utc=self._mission_state.initialization.epoch_utc,
                                elements=saved_elements,
                            )
                        )

                saved_satellite_model = self._workspace.load_satellite_3d_model_config()
                if saved_satellite_model is not None:
                    self._satellite_page.apply_settings(saved_satellite_model)
                    self._latest_satellite_model_config = saved_satellite_model
                else:
                    self._latest_satellite_model_config = self._satellite_page.settings()

                self._maneuver_page.refresh_from_workspace()
                self._design_maneuver_page.refresh_from_workspace()
                self._launch_window_page.refresh_from_workspace()
                self._tracking_arc_page.refresh_from_workspace()
                self._flight_program_page.refresh_from_workspace()
                self._viz_page.refresh_from_workspace()
                self._stk_link_page.refresh_from_workspace()
                self._latest_maneuver_strategy = self._maneuver_page.strategy()
            else:
                self._latest_satellite_model_config = self._satellite_page.settings()
                self._maneuver_page.refresh_from_workspace()
                self._design_maneuver_page.refresh_from_workspace()
                self._launch_window_page.refresh_from_workspace()
                self._tracking_arc_page.refresh_from_workspace()
                self._flight_program_page.refresh_from_workspace()
                self._viz_page.refresh_from_workspace()
                self._stk_link_page.refresh_from_workspace()
                self._latest_maneuver_strategy = self._maneuver_page.strategy()
        finally:
            self._autosave_enabled = True

        self._persist_project_outputs()
        self._persist_satellite_config()
        self._refresh_project_labels()
        self.setWindowTitle(f"{self._i18n.t('app.window_title')} - {info.name}")
        self._dashboard_page.set_project(info)
        self._record_recent_project_path(info.root_dir)
        self._refresh_project_actions()

    def _reset_spice_workspace(self, kernels_dir: Path) -> None:
        default_kernels_dir = (self._workspace_root / "data" / "kernels").resolve()
        self._spice_manager.configure_local_kernel_roots([kernels_dir, default_kernels_dir])
        if self._spice_manager.available:
            try:
                self._spice_manager.clear()
            except Exception as exc:  # pragma: no cover - UI path
                self.statusBar().showMessage(
                    self._i18n.t("spice.page_status.action_failed", error=str(exc)),
                    8000,
                )
            try:
                self._spice_manager.ensure_local_kernels_loaded()
            except Exception as exc:  # pragma: no cover - UI path
                self.statusBar().showMessage(
                    self._i18n.t("spice.page_status.action_failed", error=str(exc)),
                    8000,
                )
        self._spice_page.set_kernel_root(kernels_dir)

    def _on_trajectory_changed(self, _trajectory: object) -> None:
        if not self._autosave_enabled:
            return
        self._persist_project_outputs()

    def _on_satellite_settings_changed(self, settings: SatelliteStructureConfig) -> None:
        self._latest_satellite_model_config = settings
        if not self._autosave_enabled:
            return
        self._persist_satellite_config()

    def _on_maneuver_strategy_changed(self, strategy: dict[str, Any]) -> None:
        self._latest_maneuver_strategy = dict(strategy)
        if not self._autosave_enabled:
            return
        self._persist_maneuver_strategy()

    def _persist_project_outputs(self) -> None:
        if self._workspace.current_project is None:
            return

        try:
            self._workspace.save_orbit_initialization(self._mission_state.initialization)
            self._workspace.save_orbit_elements(self._mission_state.elements)
            if self._latest_maneuver_strategy is not None:
                self._workspace.save_maneuver_strategy(self._latest_maneuver_strategy)
            self._viz_page.export_charts(self._workspace.charts_dir())
        except Exception as exc:  # pragma: no cover - UI path
            self.statusBar().showMessage(
                self._i18n.t("project.status.autosave_failed", error=str(exc)),
                8000,
            )

    def _persist_maneuver_strategy(self) -> None:
        if self._workspace.current_project is None:
            return
        if self._latest_maneuver_strategy is None:
            return

        try:
            self._workspace.save_maneuver_strategy(self._latest_maneuver_strategy)
        except Exception as exc:  # pragma: no cover - UI path
            self.statusBar().showMessage(
                self._i18n.t("project.status.autosave_failed", error=str(exc)),
                8000,
            )

    def _persist_satellite_config(self) -> None:
        if self._workspace.current_project is None:
            return
        if self._latest_satellite_model_config is None:
            return

        try:
            self._workspace.save_satellite_3d_model_config(self._latest_satellite_model_config)
        except Exception as exc:  # pragma: no cover - UI path
            self.statusBar().showMessage(
                self._i18n.t("project.status.autosave_failed", error=str(exc)),
                8000,
            )

    def _refresh_project_labels(self) -> None:
        t = self._i18n.t
        project = self._workspace.current_project
        if project is None:
            self._project_name_label.setText(t("project.status.no_project"))
            self._project_path_label.setText("")
            self.setWindowTitle(t("app.window_title"))
            self._refresh_project_actions()
            return

        self._project_name_label.setText(project.name)
        self._project_path_label.setText(str(project.root_dir))
        self._refresh_project_actions()

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        project = self._workspace.current_project
        if project is None:
            self.setWindowTitle(t("app.window_title"))
        else:
            self.setWindowTitle(f"{t('app.window_title')} - {project.name}")

        self._project_header_label.setText(t("sidebar.project_label"))

        self._project_menu.setTitle(t("project.menu_title"))
        self._new_project_action.setText(t("project.action_new"))
        self._open_project_action.setText(t("project.action_open"))
        self._save_project_as_action.setText(t("project.action_save_as"))
        self._close_project_action.setText(t("project.action_close"))
        self._recent_projects_header_action.setText(t("project.recent_header"))
        self._no_recent_projects_action.setText(t("project.recent_empty"))
        self._common_tools_menu.setTitle(t("common_tools.menu_title"))
        self._orbit_conversion_action.setText(t("common_tools.action.orbit_conversion"))
        self._apsis_parameters_action.setText(t("common_tools.action.apsis_parameters"))
        self._circular_period_action.setText(t("common_tools.action.circular_period"))
        self._anomaly_conversion_action.setText(t("common_tools.action.anomaly_conversion"))
        self._sun_moon_position_action.setText(t("common_tools.action.sun_moon_position"))
        self._hohmann_transfer_action.setText(t("common_tools.action.hohmann_transfer"))
        self._plane_change_action.setText(t("common_tools.action.plane_change"))
        self._lambert_transfer_action.setText(t("common_tools.action.lambert_transfer"))
        self._two_body_propagation_action.setText(t("common_tools.action.two_body_propagation"))
        self._settings_menu.setTitle(t("settings.menu_title"))
        self._spice_kernels_action.setText(t("settings.action.spice_kernels"))
        self._refresh_recent_project_actions()
        self._refresh_project_actions()

        self._refresh_nav_item_labels()
        self._refresh_sidebar_toggle_button()

        self._refresh_project_labels()

    def _load_recent_project_paths(self) -> list[str]:
        raw_paths = self._settings.value("recent_projects", [], type=list)
        if not isinstance(raw_paths, list):
            return []

        paths: list[str] = []
        seen: set[str] = set()
        for raw_path in raw_paths:
            try:
                path = Path(str(raw_path)).expanduser().resolve()
            except OSError:
                continue
            key = str(path).casefold()
            if key in seen or not (path / "smart_project.json").exists():
                continue
            seen.add(key)
            paths.append(str(path))
            if len(paths) >= _MAX_RECENT_PROJECTS:
                break
        return paths

    def _save_recent_project_paths(self) -> None:
        self._settings.setValue("recent_projects", self._recent_project_paths)

    def _record_recent_project_path(self, project_path: str | Path) -> None:
        path = Path(project_path).expanduser().resolve()
        key = str(path).casefold()
        self._recent_project_paths = [
            existing
            for existing in self._recent_project_paths
            if str(Path(existing).expanduser().resolve()).casefold() != key
        ]
        self._recent_project_paths.insert(0, str(path))
        self._recent_project_paths = self._recent_project_paths[:_MAX_RECENT_PROJECTS]
        self._save_recent_project_paths()
        self._refresh_recent_project_actions()

    def _remove_recent_project_path(self, project_path: str | Path) -> None:
        path = Path(project_path).expanduser().resolve()
        key = str(path).casefold()
        self._recent_project_paths = [
            existing
            for existing in self._recent_project_paths
            if str(Path(existing).expanduser().resolve()).casefold() != key
        ]
        self._save_recent_project_paths()
        self._refresh_recent_project_actions()

    def _refresh_recent_project_actions(self) -> None:
        for action in self._recent_project_actions:
            self._project_menu.removeAction(action)
        self._recent_project_actions.clear()

        self._no_recent_projects_action.setVisible(not self._recent_project_paths)
        for path_text in self._recent_project_paths:
            path = Path(path_text)
            label = f"{path.name} - {path}"
            action = QtGui.QAction(label, self)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, item_path=path_text: self._open_recent_project(item_path))
            self._project_menu.addAction(action)
            self._recent_project_actions.append(action)
        if hasattr(self, "_dashboard_page"):
            self._dashboard_page.set_recent_projects(self._recent_project_paths)

    def _refresh_project_actions(self) -> None:
        has_project = self._workspace.current_project is not None
        self._save_project_as_action.setEnabled(has_project)
        self._close_project_action.setEnabled(has_project)
