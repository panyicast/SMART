from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from smart.domain.models import SatelliteStructureConfig
from smart.ui.i18n import I18nManager
from smart.ui.widgets.satellite_structure_view import SatelliteStructureView
from smart.ui.widgets.spinboxes import NoWheelDoubleSpinBox, NoWheelSpinBox

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[4] / "models" / "space"
_STK_SAMPLE_MODEL_CANDIDATES = (
    Path(r"D:\Program Files\AGI\STK 116\STKData\VO\Models\Space\satellite.mdl"),
    Path(r"D:\Program Files\AGI\STK 116\STKData\VO\Models\Space\satellite.dae"),
    Path(r"C:\Program Files\AGI\STK 11\STKData\VO\Models\Space\satellite.mdl"),
    Path(r"C:\Program Files\AGI\STK 11\STKData\VO\Models\Space\satellite.dae"),
)
_STK_SAMPLE_MODEL_PATH = next(
    (path for path in _STK_SAMPLE_MODEL_CANDIDATES if path.exists()),
    _STK_SAMPLE_MODEL_CANDIDATES[0],
)


class Satellite3DModelPage(QtWidgets.QWidget):
    settings_changed = QtCore.Signal(object)

    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._suppress_emit = False
        self._structure_field_labels: dict[str, QtWidgets.QLabel] = {}
        self._structure_numeric_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._structure_count_fields: dict[str, QtWidgets.QSpinBox] = {}
        self._preview_status_payload: dict[str, str] = {"state": "parametric_model", "path": ""}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        eyebrow = QtWidgets.QLabel("SMART · SATELLITE 3D MODEL")
        eyebrow.setProperty("role", "pageEyebrow")
        root.addWidget(eyebrow)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        accent_rule = QtWidgets.QFrame()
        accent_rule.setProperty("role", "accentRule")
        accent_rule.setFixedHeight(2)
        accent_rule.setMaximumWidth(220)
        root.addWidget(accent_rule)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_preview_panel())
        splitter.addWidget(self._build_design_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([900, 520])

        self._i18n.language_changed.connect(self.retranslate)
        self._connect_change_signals()
        self.apply_settings(SatelliteStructureConfig())
        self.retranslate()

    def _build_preview_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        body = QtWidgets.QVBoxLayout(canvas)
        body.setContentsMargins(0, 0, 12, 0)
        body.setSpacing(14)

        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._preview_header_label = QtWidgets.QLabel()
        self._preview_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._preview_header_label)

        self._preview_caption_label = QtWidgets.QLabel()
        self._preview_caption_label.setProperty("role", "cardCaption")
        self._preview_caption_label.setWordWrap(True)
        layout.addWidget(self._preview_caption_label)

        self._structure_preview = SatelliteStructureView()
        self._structure_preview.status_changed.connect(self._on_preview_status_changed)
        self._structure_preview.setMinimumHeight(540)
        layout.addWidget(self._structure_preview, 1)

        self._preview_status_label = QtWidgets.QLabel()
        self._preview_status_label.setProperty("role", "cardCaption")
        self._preview_status_label.setWordWrap(True)
        layout.addWidget(self._preview_status_label)

        body.addWidget(card, 1)
        return scroll

    def _build_design_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        body = QtWidgets.QVBoxLayout(canvas)
        body.setContentsMargins(12, 0, 0, 0)
        body.setSpacing(14)

        body.addWidget(self._build_structure_card())

        self._apply_button = QtWidgets.QPushButton()
        self._apply_button.clicked.connect(self._emit_settings_changed)
        body.addWidget(self._apply_button)
        body.addStretch(1)
        return scroll

    def _build_structure_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._structure_header_label = QtWidgets.QLabel()
        self._structure_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._structure_header_label)

        self._structure_caption_label = QtWidgets.QLabel()
        self._structure_caption_label.setProperty("role", "cardCaption")
        self._structure_caption_label.setWordWrap(True)
        layout.addWidget(self._structure_caption_label)

        model_form = QtWidgets.QFormLayout()
        model_form.setSpacing(10)
        self._model_path_label = QtWidgets.QLabel()
        self._model_path_edit = QtWidgets.QLineEdit()
        self._model_path_edit.setClearButtonEnabled(True)
        self._browse_model_button = QtWidgets.QPushButton()
        self._browse_model_button.clicked.connect(self._choose_model_file)
        self._use_stk_sample_button = QtWidgets.QPushButton()
        self._use_stk_sample_button.clicked.connect(self._apply_stk_sample_model)
        self._use_stk_sample_button.setEnabled(_STK_SAMPLE_MODEL_PATH.exists())

        model_actions = QtWidgets.QHBoxLayout()
        model_actions.setSpacing(8)
        model_actions.addWidget(self._model_path_edit, 1)
        model_actions.addWidget(self._browse_model_button)
        model_actions.addWidget(self._use_stk_sample_button)

        model_row = QtWidgets.QWidget()
        model_row.setLayout(model_actions)
        model_form.addRow(self._model_path_label, model_row)
        layout.addLayout(model_form)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        numeric_definitions = {
            "body_size_x_m": (0.1, 30.0, 0.05, 2),
            "body_size_y_m": (0.1, 30.0, 0.05, 2),
            "body_size_z_m": (0.1, 30.0, 0.05, 2),
            "antenna_major_axis_m": (0.1, 10.0, 0.05, 2),
            "antenna_minor_axis_m": (0.1, 10.0, 0.05, 2),
            "antenna_depth_m": (0.02, 3.0, 0.02, 2),
            "solar_panel_span_m": (0.1, 20.0, 0.05, 2),
            "solar_panel_width_m": (0.1, 20.0, 0.05, 2),
            "solar_panel_gap_m": (0.0, 5.0, 0.01, 2),
        }
        for key, (minimum, maximum, step, decimals) in numeric_definitions.items():
            label = QtWidgets.QLabel()
            spin = NoWheelDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            form.addRow(label, spin)
            self._structure_field_labels[key] = label
            self._structure_numeric_fields[key] = spin

        count_definitions = {
            "east_antenna_count": (0, 6, 1),
            "west_antenna_count": (0, 6, 1),
            "north_wing_count": (0, 4, 1),
            "south_wing_count": (0, 4, 1),
            "solar_panels_per_wing": (1, 8, 1),
        }
        for key, (minimum, maximum, step) in count_definitions.items():
            label = QtWidgets.QLabel()
            spin = NoWheelSpinBox()
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            form.addRow(label, spin)
            self._structure_field_labels[key] = label
            self._structure_count_fields[key] = spin

        layout.addLayout(form)
        return card

    def _connect_change_signals(self) -> None:
        for field in self._structure_numeric_fields.values():
            field.valueChanged.connect(lambda _value: self._emit_settings_changed())
        for field in self._structure_count_fields.values():
            field.valueChanged.connect(lambda _value: self._emit_settings_changed())
        self._model_path_edit.editingFinished.connect(self._emit_settings_changed)

    def settings(self) -> SatelliteStructureConfig:
        return SatelliteStructureConfig(
            body_size_x_m=self._structure_numeric_fields["body_size_x_m"].value(),
            body_size_y_m=self._structure_numeric_fields["body_size_y_m"].value(),
            body_size_z_m=self._structure_numeric_fields["body_size_z_m"].value(),
            model_path=self._model_path_edit.text().strip(),
            antenna_major_axis_m=self._structure_numeric_fields["antenna_major_axis_m"].value(),
            antenna_minor_axis_m=self._structure_numeric_fields["antenna_minor_axis_m"].value(),
            antenna_depth_m=self._structure_numeric_fields["antenna_depth_m"].value(),
            east_antenna_count=self._structure_count_fields["east_antenna_count"].value(),
            west_antenna_count=self._structure_count_fields["west_antenna_count"].value(),
            north_wing_count=self._structure_count_fields["north_wing_count"].value(),
            south_wing_count=self._structure_count_fields["south_wing_count"].value(),
            solar_panels_per_wing=self._structure_count_fields["solar_panels_per_wing"].value(),
            solar_panel_span_m=self._structure_numeric_fields["solar_panel_span_m"].value(),
            solar_panel_width_m=self._structure_numeric_fields["solar_panel_width_m"].value(),
            solar_panel_gap_m=self._structure_numeric_fields["solar_panel_gap_m"].value(),
        )

    def apply_settings(self, settings: SatelliteStructureConfig) -> None:
        self._suppress_emit = True
        try:
            self._structure_numeric_fields["body_size_x_m"].setValue(settings.body_size_x_m)
            self._structure_numeric_fields["body_size_y_m"].setValue(settings.body_size_y_m)
            self._structure_numeric_fields["body_size_z_m"].setValue(settings.body_size_z_m)
            self._model_path_edit.setText(settings.model_path)
            self._structure_numeric_fields["antenna_major_axis_m"].setValue(settings.antenna_major_axis_m)
            self._structure_numeric_fields["antenna_minor_axis_m"].setValue(settings.antenna_minor_axis_m)
            self._structure_numeric_fields["antenna_depth_m"].setValue(settings.antenna_depth_m)
            self._structure_numeric_fields["solar_panel_span_m"].setValue(settings.solar_panel_span_m)
            self._structure_numeric_fields["solar_panel_width_m"].setValue(settings.solar_panel_width_m)
            self._structure_numeric_fields["solar_panel_gap_m"].setValue(settings.solar_panel_gap_m)
            self._structure_count_fields["east_antenna_count"].setValue(settings.east_antenna_count)
            self._structure_count_fields["west_antenna_count"].setValue(settings.west_antenna_count)
            self._structure_count_fields["north_wing_count"].setValue(settings.north_wing_count)
            self._structure_count_fields["south_wing_count"].setValue(settings.south_wing_count)
            self._structure_count_fields["solar_panels_per_wing"].setValue(settings.solar_panels_per_wing)
        finally:
            self._suppress_emit = False
        self._update_structure_preview()
        self._emit_settings_changed()

    def _update_structure_preview(self) -> None:
        self._structure_preview.set_structure(self.settings())

    def _choose_model_file(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self._i18n.t("satellite.dialog.choose_model_title"),
            str(self._resolve_model_dialog_dir()),
            self._i18n.t("satellite.dialog.choose_model_filter"),
        )
        if not file_path:
            return
        self._model_path_edit.setText(file_path)
        self._emit_settings_changed()

    def _resolve_model_dialog_dir(self) -> Path:
        current_text = self._model_path_edit.text().strip()
        if current_text:
            current_path = Path(current_text).expanduser()
            if current_path.exists():
                return current_path.parent if current_path.is_file() else current_path
        if _DEFAULT_MODEL_DIR.exists():
            return _DEFAULT_MODEL_DIR
        return _STK_SAMPLE_MODEL_PATH.parent

    def _apply_stk_sample_model(self) -> None:
        if not _STK_SAMPLE_MODEL_PATH.exists():
            return
        self._model_path_edit.setText(str(_STK_SAMPLE_MODEL_PATH))
        self._emit_settings_changed()

    def _emit_settings_changed(self) -> None:
        if self._suppress_emit:
            return
        self._update_structure_preview()
        self.settings_changed.emit(self.settings())

    def _on_preview_status_changed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        state = str(payload.get("state", "parametric_model"))
        path = str(payload.get("path", ""))
        self._preview_status_payload = {"state": state, "path": path}
        self._sync_structure_controls_state()
        self._refresh_preview_status_label()

    def _sync_structure_controls_state(self) -> None:
        external_model_loaded = self._preview_status_payload.get("state") == "model_loaded"
        enabled = not external_model_loaded
        for key, field in self._structure_numeric_fields.items():
            self._structure_field_labels[key].setEnabled(enabled)
            field.setEnabled(enabled)
        for key, field in self._structure_count_fields.items():
            self._structure_field_labels[key].setEnabled(enabled)
            field.setEnabled(enabled)

    def _refresh_preview_status_label(self) -> None:
        payload = self._preview_status_payload
        state = payload.get("state", "parametric_model")
        path = payload.get("path", "")
        self._preview_status_label.setText(self._i18n.t(f"satellite.preview_status.{state}", path=path))

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("satellite.title"))
        self._subtitle_label.setText(t("satellite.subtitle"))
        self._preview_header_label.setText(t("satellite.section.structure_preview"))
        self._preview_caption_label.setText(t("satellite.structure.preview_caption"))
        self._structure_header_label.setText(t("satellite.section.structure"))
        self._structure_caption_label.setText(t("satellite.structure.caption"))
        self._model_path_label.setText(t("satellite.field.model_path"))
        self._browse_model_button.setText(t("satellite.button.browse_model"))
        self._use_stk_sample_button.setText(t("satellite.button.use_stk_sample"))
        for key in self._structure_numeric_fields:
            self._structure_field_labels[key].setText(t(f"satellite.field.{key}"))
        for key in self._structure_count_fields:
            self._structure_field_labels[key].setText(t(f"satellite.field.{key}"))
        self._apply_button.setText(t("satellite.button.apply"))
        self._refresh_preview_status_label()


SatelliteStatusPage = Satellite3DModelPage
