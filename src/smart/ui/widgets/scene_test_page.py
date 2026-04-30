from __future__ import annotations

from PySide6 import QtWidgets

from smart.domain.models import SatelliteStatusSettings
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState
from smart.ui.widgets.cesium_mission_view import CesiumMissionView


class SceneTestPage(QtWidgets.QWidget):
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

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(10)
        controls.addStretch(1)
        self._reload_button = QtWidgets.QPushButton()
        self._reload_button.clicked.connect(self._reload_scene)
        controls.addWidget(self._reload_button)
        root.addLayout(controls)

        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(10)

        self._card_title_label = QtWidgets.QLabel()
        self._card_title_label.setProperty("role", "cardTitle")
        card_layout.addWidget(self._card_title_label)

        self._card_body_label = QtWidgets.QLabel()
        self._card_body_label.setProperty("role", "cardCaption")
        self._card_body_label.setWordWrap(True)
        card_layout.addWidget(self._card_body_label)

        self._scene_view = CesiumMissionView()
        self._scene_view.setMinimumHeight(680)
        self._scene_view.status_changed.connect(self._on_scene_status_changed)
        card_layout.addWidget(self._scene_view, 1)

        self._scene_status_label = QtWidgets.QLabel()
        self._scene_status_label.setWordWrap(True)
        card_layout.addWidget(self._scene_status_label)
        root.addWidget(card, 1)

        self._scene_status = self._scene_view.last_status
        self._mission_state.trajectory_changed.connect(self._refresh_scene)
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self._refresh_scene()

    def set_satellite_settings(self, settings: SatelliteStatusSettings) -> None:
        self._satellite_settings = settings
        self._refresh_scene()

    def _refresh_scene(self, _trajectory: object | None = None) -> None:
        self._scene_view.set_scene(
            self._mission_state.elements,
            self._mission_state.trajectory,
            self._satellite_settings,
            satellite_label=self._i18n.t("dashboard.scene_satellite_name"),
        )

    def _reload_scene(self) -> None:
        self._scene_view.reload_page()

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
        self._title_label.setText(t("scene_test.title"))
        self._subtitle_label.setText(t("scene_test.subtitle"))
        self._reload_button.setText(t("scene_test.reload_button"))
        self._card_title_label.setText(t("dashboard.scene_title"))
        self._card_body_label.setText(t("scene_test.card_body"))
        self._update_scene_status_label()
        self._refresh_scene()
