from __future__ import annotations

from PySide6 import QtWidgets

from smart.domain.models import SatelliteStatusSettings
from smart.services.module_catalog import ModuleDescriptor, build_module_catalog
from smart.services.spice_service import runtime_summary
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState
from smart.ui.widgets.cesium_mission_view import CesiumMissionView


def _card_layout(status_role: str | None = None) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]:
    card = QtWidgets.QFrame()
    card.setProperty("role", "card")
    layout = QtWidgets.QVBoxLayout(card)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(10)

    title_label = QtWidgets.QLabel()
    title_label.setProperty("role", "cardTitle")
    title_label.setWordWrap(True)
    layout.addWidget(title_label)

    status_label = QtWidgets.QLabel()
    status_label.setVisible(status_role is not None)
    if status_role is not None:
        status_label.setProperty("role", f"status{status_role}")
        layout.addWidget(status_label)

    body_label = QtWidgets.QLabel()
    body_label.setProperty("role", "pageBody")
    body_label.setWordWrap(True)
    layout.addWidget(body_label)
    layout.addStretch(1)
    return card, title_label, status_label, body_label


class DashboardPage(QtWidgets.QWidget):
    _SCENE_STATUS_ROLES = {
        "loading": "statusLoading",
        "ready": "statusReady",
        "unavailable": "statusDisconnected",
        "load_failed": "statusDisconnected",
        "library_error": "statusDisconnected",
        "scene_error": "statusDisconnected",
    }

    _SCENE_STATUS_KEYS = {
        "loading": "dashboard.scene_status.loading",
        "ready": "dashboard.scene_status.ready",
        "unavailable": "dashboard.scene_status.unavailable",
        "load_failed": "dashboard.scene_status.load_failed",
        "library_error": "dashboard.scene_status.library_error",
        "scene_error": "dashboard.scene_status.scene_error",
    }

    _SCENE_DETAIL_KEYS = {
        "model_loaded": "dashboard.scene_detail.model_loaded",
        "model_unsupported": "dashboard.scene_detail.model_unsupported",
        "model_missing": "dashboard.scene_detail.model_missing",
    }

    def __init__(
        self,
        mission_state: MissionState,
        i18n: I18nManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mission_state = mission_state
        self._i18n = i18n
        self._satellite_settings = SatelliteStatusSettings()
        self._spice_runtime_status, _ = runtime_summary()
        self._modules = build_module_catalog()
        self._module_widgets: list[
            tuple[ModuleDescriptor, QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]
        ] = []
        self._scene_status: dict[str, str] = {"state": "loading", "detail_key": "", "detail_name": "", "error": ""}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        scene_card = QtWidgets.QFrame()
        scene_card.setProperty("role", "card")
        scene_layout = QtWidgets.QVBoxLayout(scene_card)
        scene_layout.setContentsMargins(18, 18, 18, 18)
        scene_layout.setSpacing(10)

        self._scene_title_label = QtWidgets.QLabel()
        self._scene_title_label.setProperty("role", "cardTitle")
        scene_layout.addWidget(self._scene_title_label)

        self._scene_body_label = QtWidgets.QLabel()
        self._scene_body_label.setProperty("role", "cardCaption")
        self._scene_body_label.setWordWrap(True)
        scene_layout.addWidget(self._scene_body_label)

        self._scene_view = CesiumMissionView()
        self._scene_view.setMinimumHeight(420)
        self._scene_view.status_changed.connect(self._on_scene_status_changed)
        scene_layout.addWidget(self._scene_view, 1)

        self._scene_status_label = QtWidgets.QLabel()
        self._scene_status_label.setWordWrap(True)
        scene_layout.addWidget(self._scene_status_label)
        root.addWidget(scene_card, 1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        root.addWidget(scroll, 1)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        grid = QtWidgets.QGridLayout(canvas)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)

        hero_card, self._hero_title_label, _, self._hero_body_label = _card_layout()
        grid.addWidget(hero_card, 0, 0, 1, 2)

        spice_card, self._spice_title_label, self._spice_status_label, self._spice_body_label = _card_layout(
            status_role=self._spice_runtime_status
        )
        grid.addWidget(spice_card, 1, 0, 1, 2)

        row = 2
        col = 0
        for module in self._modules:
            card, title_label, status_label, body_label = self._module_card(module)
            self._module_widgets.append((module, title_label, status_label, body_label))
            grid.addWidget(card, row, col)
            col += 1
            if col == 2:
                row += 1
                col = 0

        grid.setRowStretch(row + 1, 1)
        self._i18n.language_changed.connect(self.retranslate)
        self._mission_state.trajectory_changed.connect(self._refresh_scene)
        self._scene_status = self._scene_view.last_status
        self.retranslate()
        self._refresh_scene()

    def set_satellite_settings(self, settings: SatelliteStatusSettings) -> None:
        self._satellite_settings = settings
        self._refresh_scene()

    def _module_card(
        self, module: ModuleDescriptor
    ) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]:
        status_role = "Operational" if module.status == "Operational" else "Planned"
        return _card_layout(status_role=status_role)

    def _refresh_scene(self, _trajectory: object | None = None) -> None:
        self._scene_view.set_scene(
            self._mission_state.elements,
            self._mission_state.trajectory,
            self._satellite_settings,
            satellite_label=self._i18n.t("dashboard.scene_satellite_name"),
        )

    def _on_scene_status_changed(self, payload: object) -> None:
        if isinstance(payload, dict):
            self._scene_status = {
                "state": str(payload.get("state", "loading")),
                "detail_key": str(payload.get("detail_key", "")),
                "detail_name": str(payload.get("detail_name", "")),
                "error": str(payload.get("error", "")),
            }
        self._update_scene_status_label()

    def _update_scene_status_label(self) -> None:
        t = self._i18n.t
        state = self._scene_status.get("state", "loading")
        status_key = self._SCENE_STATUS_KEYS.get(state, "dashboard.scene_status.scene_error")
        base_text = t(status_key, error=self._scene_status.get("error", ""))

        detail_key = self._scene_status.get("detail_key", "")
        detail_text = ""
        translation_key = self._SCENE_DETAIL_KEYS.get(detail_key)
        if translation_key is not None:
            detail_text = t(translation_key, name=self._scene_status.get("detail_name", ""))

        text = base_text if not detail_text else f"{base_text} {detail_text}"
        role = self._SCENE_STATUS_ROLES.get(state, "statusDisconnected")
        self._scene_status_label.setProperty("role", role)
        self._scene_status_label.style().unpolish(self._scene_status_label)
        self._scene_status_label.style().polish(self._scene_status_label)
        self._scene_status_label.setText(text)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("dashboard.title"))
        self._subtitle_label.setText(t("dashboard.subtitle"))
        self._hero_title_label.setText(t("dashboard.hero_title"))
        self._hero_body_label.setText(t("dashboard.hero_body"))
        self._scene_title_label.setText(t("dashboard.scene_title"))
        self._scene_body_label.setText(t("dashboard.scene_body"))
        self._spice_title_label.setText(t("dashboard.spice_title"))

        if self._spice_runtime_status == "Ready":
            self._spice_status_label.setText(t("status.ready"))
            self._spice_body_label.setText(t("dashboard.spice_ready_summary"))
        else:
            self._spice_status_label.setText(t("status.disconnected"))
            self._spice_body_label.setText(t("dashboard.spice_disconnected_summary"))

        for module, title_label, status_label, body_label in self._module_widgets:
            prefix = f"module.{module.key}"
            title_label.setText(t(f"{prefix}.name"))
            status_label.setText(
                t("status.operational") if module.status == "Operational" else t("status.planned")
            )
            body_label.setText(
                f"{t(f'{prefix}.description')}\n\n{t('module.focus_prefix', focus=t(f'{prefix}.focus'))}"
            )
        self._update_scene_status_label()
        self._refresh_scene()
