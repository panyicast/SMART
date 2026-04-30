from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from smart.domain.models import (
    AntennaConfig,
    GroundAssetConfig,
    RelaySatelliteConfig,
    SatelliteStatusSettings,
    SatelliteStructureConfig,
)
from smart.ui.i18n import I18nManager
from smart.ui.widgets.satellite_structure_view import SatelliteStructureView
from smart.ui.widgets.spinboxes import NoWheelDoubleSpinBox, NoWheelSpinBox

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[4] / "models" / "space"
_STK_SAMPLE_MODEL_CANDIDATES = (
    Path(r"C:\Program Files\AGI\STK 11\STKData\VO\Models\Space\satellite.glb"),
    Path(r"C:\Program Files\AGI\STK 11\STKData\VO\Models\Space\satellite.gltf"),
    Path(r"C:\Program Files\AGI\STK 11\STKData\VO\Models\Space\satellite.dae"),
)
_STK_SAMPLE_MODEL_PATH = next(
    (path for path in _STK_SAMPLE_MODEL_CANDIDATES if path.exists()),
    _STK_SAMPLE_MODEL_CANDIDATES[-1],
)


@dataclass(slots=True)
class _TableSection:
    key: str
    title_label: QtWidgets.QLabel
    table: QtWidgets.QTableWidget
    add_button: QtWidgets.QPushButton
    remove_button: QtWidgets.QPushButton
    columns: list[str]


class SatelliteStatusPage(QtWidgets.QWidget):
    settings_changed = QtCore.Signal(object)

    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._suppress_emit = False
        self._field_labels: dict[str, QtWidgets.QLabel] = {}
        self._numeric_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._structure_field_labels: dict[str, QtWidgets.QLabel] = {}
        self._structure_numeric_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._structure_count_fields: dict[str, QtWidgets.QSpinBox] = {}
        self._sections: dict[str, _TableSection] = {}
        self._preview_status_payload: dict[str, str] = {"state": "parametric_model", "path": ""}

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

        self._splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        root.addWidget(self._splitter, 1)

        self._splitter.addWidget(self._build_left_panel())
        self._splitter.addWidget(self._build_right_panel())
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([920, 520])

        self._i18n.language_changed.connect(self.retranslate)
        self._connect_change_signals()
        self.apply_settings(SatelliteStatusSettings())
        self.retranslate()

    def _build_left_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)

        body = QtWidgets.QVBoxLayout(canvas)
        body.setContentsMargins(0, 0, 12, 0)
        body.setSpacing(14)

        body.addWidget(self._build_mass_propulsion_card())
        body.addWidget(
            self._build_table_card(
                key="ttc_antennas",
                columns=[
                    "satellite.table.column.name",
                    "satellite.table.column.band",
                    "satellite.table.column.gain_dbi",
                    "satellite.table.column.beamwidth_deg",
                ],
            )
        )
        body.addWidget(
            self._build_table_card(
                key="relay_antennas",
                columns=[
                    "satellite.table.column.name",
                    "satellite.table.column.band",
                    "satellite.table.column.gain_dbi",
                    "satellite.table.column.beamwidth_deg",
                ],
            )
        )
        body.addWidget(
            self._build_table_card(
                key="ground_assets",
                columns=[
                    "satellite.table.column.name",
                    "satellite.table.column.asset_type",
                    "satellite.table.column.longitude_deg",
                    "satellite.table.column.latitude_deg",
                    "satellite.table.column.altitude_m",
                ],
            )
        )
        body.addWidget(
            self._build_table_card(
                key="relay_satellites",
                columns=[
                    "satellite.table.column.name",
                    "satellite.table.column.orbit",
                    "satellite.table.column.band",
                    "satellite.table.column.note",
                ],
            )
        )
        body.addStretch(1)
        return scroll

    def _build_right_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)

        body = QtWidgets.QVBoxLayout(canvas)
        body.setContentsMargins(12, 0, 0, 0)
        body.setSpacing(14)

        body.addWidget(self._build_structure_preview_card())
        body.addWidget(self._build_structure_card())

        self._apply_button = QtWidgets.QPushButton()
        self._apply_button.clicked.connect(self._emit_settings_changed)
        body.addWidget(self._apply_button)
        body.addStretch(1)
        return scroll

    def _build_mass_propulsion_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._mass_header_label = QtWidgets.QLabel()
        self._mass_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._mass_header_label)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        numeric_definitions = {
            "launch_mass_kg": (100.0, 30000.0, 10.0, 1),
            "fuel_load_kg": (0.0, 20000.0, 10.0, 1),
            "helium_load_kg": (0.0, 2000.0, 1.0, 1),
            "orbit_engine_thrust_n": (0.0, 50000.0, 1.0, 2),
            "orbit_engine_isp_s": (0.0, 1000.0, 1.0, 2),
            "settle_engine_thrust_n": (0.0, 10000.0, 0.5, 2),
            "settle_engine_isp_s": (0.0, 1000.0, 1.0, 2),
        }
        for key, (minimum, maximum, step, decimals) in numeric_definitions.items():
            label = QtWidgets.QLabel()
            spin = NoWheelDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            form.addRow(label, spin)
            self._field_labels[key] = label
            self._numeric_fields[key] = spin
        layout.addLayout(form)
        return card

    def _build_structure_preview_card(self) -> QtWidgets.QWidget:
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
        self._structure_preview.setMinimumHeight(360)
        layout.addWidget(self._structure_preview, 1)

        self._preview_status_label = QtWidgets.QLabel()
        self._preview_status_label.setProperty("role", "cardCaption")
        self._preview_status_label.setWordWrap(True)
        layout.addWidget(self._preview_status_label)
        return card

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

    def _build_table_card(self, key: str, columns: list[str]) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QtWidgets.QLabel()
        title.setProperty("role", "cardTitle")
        layout.addWidget(title)

        table = QtWidgets.QTableWidget(0, len(columns))
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(table, 1)

        row_buttons = QtWidgets.QHBoxLayout()
        add_button = QtWidgets.QPushButton()
        remove_button = QtWidgets.QPushButton()
        add_button.clicked.connect(lambda: self._append_table_row(key))
        remove_button.clicked.connect(lambda: self._remove_selected_table_row(key))
        row_buttons.addWidget(add_button)
        row_buttons.addWidget(remove_button)
        row_buttons.addStretch(1)
        layout.addLayout(row_buttons)

        self._sections[key] = _TableSection(
            key=key,
            title_label=title,
            table=table,
            add_button=add_button,
            remove_button=remove_button,
            columns=columns,
        )
        return card

    def _connect_change_signals(self) -> None:
        for field in self._numeric_fields.values():
            field.valueChanged.connect(lambda _value: self._emit_settings_changed())
        for field in self._structure_numeric_fields.values():
            field.valueChanged.connect(lambda _value: self._emit_settings_changed())
        for field in self._structure_count_fields.values():
            field.valueChanged.connect(lambda _value: self._emit_settings_changed())
        self._model_path_edit.editingFinished.connect(self._emit_settings_changed)
        for section in self._sections.values():
            section.table.itemChanged.connect(lambda _item: self._emit_settings_changed())

    def _append_table_row(self, key: str) -> None:
        section = self._sections[key]
        row = section.table.rowCount()
        section.table.insertRow(row)
        for column in range(section.table.columnCount()):
            section.table.setItem(row, column, QtWidgets.QTableWidgetItem(""))
        self._emit_settings_changed()

    def _remove_selected_table_row(self, key: str) -> None:
        section = self._sections[key]
        row = section.table.currentRow()
        if row < 0:
            row = section.table.rowCount() - 1
        if row < 0:
            return
        section.table.removeRow(row)
        self._emit_settings_changed()

    def _set_table_rows(self, key: str, rows: list[list[str]]) -> None:
        section = self._sections[key]
        table = section.table
        table.blockSignals(True)
        table.setRowCount(0)
        for row_data in rows:
            row = table.rowCount()
            table.insertRow(row)
            for column, value in enumerate(row_data):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        table.blockSignals(False)

    def _read_table_rows(self, key: str) -> list[list[str]]:
        section = self._sections[key]
        table = section.table
        rows: list[list[str]] = []
        for row in range(table.rowCount()):
            row_values: list[str] = []
            for column in range(table.columnCount()):
                item = table.item(row, column)
                row_values.append("" if item is None else item.text().strip())
            rows.append(row_values)
        return rows

    def _structure_settings(self) -> SatelliteStructureConfig:
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

    def settings(self) -> SatelliteStatusSettings:
        ttc = [
            AntennaConfig(
                name=row[0],
                band=row[1],
                gain_dbi=self._to_float(row[2]),
                beamwidth_deg=self._to_float(row[3]),
            )
            for row in self._read_table_rows("ttc_antennas")
            if any(cell for cell in row)
        ]
        relay_antennas = [
            AntennaConfig(
                name=row[0],
                band=row[1],
                gain_dbi=self._to_float(row[2]),
                beamwidth_deg=self._to_float(row[3]),
            )
            for row in self._read_table_rows("relay_antennas")
            if any(cell for cell in row)
        ]
        ground_assets = [
            GroundAssetConfig(
                name=row[0],
                asset_type=row[1],
                longitude_deg=self._to_float(row[2]),
                latitude_deg=self._to_float(row[3]),
                altitude_m=self._to_float(row[4]),
            )
            for row in self._read_table_rows("ground_assets")
            if any(cell for cell in row)
        ]
        relay_satellites = [
            RelaySatelliteConfig(
                name=row[0],
                orbital_slot_orbit=row[1],
                band=row[2],
                note=row[3],
            )
            for row in self._read_table_rows("relay_satellites")
            if any(cell for cell in row)
        ]
        return SatelliteStatusSettings(
            launch_mass_kg=self._numeric_fields["launch_mass_kg"].value(),
            fuel_load_kg=self._numeric_fields["fuel_load_kg"].value(),
            helium_load_kg=self._numeric_fields["helium_load_kg"].value(),
            orbit_engine_thrust_n=self._numeric_fields["orbit_engine_thrust_n"].value(),
            orbit_engine_isp_s=self._numeric_fields["orbit_engine_isp_s"].value(),
            settle_engine_thrust_n=self._numeric_fields["settle_engine_thrust_n"].value(),
            settle_engine_isp_s=self._numeric_fields["settle_engine_isp_s"].value(),
            structure=self._structure_settings(),
            ttc_antennas=ttc,
            relay_antennas=relay_antennas,
            ground_assets=ground_assets,
            relay_satellites=relay_satellites,
        )

    def apply_settings(self, settings: SatelliteStatusSettings) -> None:
        self._suppress_emit = True
        try:
            self._numeric_fields["launch_mass_kg"].setValue(settings.launch_mass_kg)
            self._numeric_fields["fuel_load_kg"].setValue(settings.fuel_load_kg)
            self._numeric_fields["helium_load_kg"].setValue(settings.helium_load_kg)
            self._numeric_fields["orbit_engine_thrust_n"].setValue(settings.orbit_engine_thrust_n)
            self._numeric_fields["orbit_engine_isp_s"].setValue(settings.orbit_engine_isp_s)
            self._numeric_fields["settle_engine_thrust_n"].setValue(settings.settle_engine_thrust_n)
            self._numeric_fields["settle_engine_isp_s"].setValue(settings.settle_engine_isp_s)

            structure = settings.structure
            self._structure_numeric_fields["body_size_x_m"].setValue(structure.body_size_x_m)
            self._structure_numeric_fields["body_size_y_m"].setValue(structure.body_size_y_m)
            self._structure_numeric_fields["body_size_z_m"].setValue(structure.body_size_z_m)
            self._model_path_edit.setText(structure.model_path)
            self._structure_numeric_fields["antenna_major_axis_m"].setValue(structure.antenna_major_axis_m)
            self._structure_numeric_fields["antenna_minor_axis_m"].setValue(structure.antenna_minor_axis_m)
            self._structure_numeric_fields["antenna_depth_m"].setValue(structure.antenna_depth_m)
            self._structure_numeric_fields["solar_panel_span_m"].setValue(structure.solar_panel_span_m)
            self._structure_numeric_fields["solar_panel_width_m"].setValue(structure.solar_panel_width_m)
            self._structure_numeric_fields["solar_panel_gap_m"].setValue(structure.solar_panel_gap_m)
            self._structure_count_fields["east_antenna_count"].setValue(structure.east_antenna_count)
            self._structure_count_fields["west_antenna_count"].setValue(structure.west_antenna_count)
            self._structure_count_fields["north_wing_count"].setValue(structure.north_wing_count)
            self._structure_count_fields["south_wing_count"].setValue(structure.south_wing_count)
            self._structure_count_fields["solar_panels_per_wing"].setValue(structure.solar_panels_per_wing)

            self._set_table_rows(
                "ttc_antennas",
                [
                    [
                        item.name,
                        item.band,
                        f"{item.gain_dbi:.3f}",
                        f"{item.beamwidth_deg:.3f}",
                    ]
                    for item in settings.ttc_antennas
                ],
            )
            self._set_table_rows(
                "relay_antennas",
                [
                    [
                        item.name,
                        item.band,
                        f"{item.gain_dbi:.3f}",
                        f"{item.beamwidth_deg:.3f}",
                    ]
                    for item in settings.relay_antennas
                ],
            )
            self._set_table_rows(
                "ground_assets",
                [
                    [
                        item.name,
                        item.asset_type,
                        f"{item.longitude_deg:.6f}",
                        f"{item.latitude_deg:.6f}",
                        f"{item.altitude_m:.3f}",
                    ]
                    for item in settings.ground_assets
                ],
            )
            self._set_table_rows(
                "relay_satellites",
                [[item.name, item.orbital_slot_orbit, item.band, item.note] for item in settings.relay_satellites],
            )
        finally:
            self._suppress_emit = False
        self._update_structure_preview()
        self._emit_settings_changed()

    def _update_structure_preview(self) -> None:
        self._structure_preview.set_structure(self._structure_settings())

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
        label_text = self._i18n.t(f"satellite.preview_status.{state}", path=path)
        self._preview_status_label.setText(label_text)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("satellite.title"))
        self._subtitle_label.setText(t("satellite.subtitle"))
        self._mass_header_label.setText(t("satellite.section.mass_propulsion"))
        self._preview_header_label.setText(t("satellite.section.structure_preview"))
        self._preview_caption_label.setText(t("satellite.structure.preview_caption"))
        self._structure_header_label.setText(t("satellite.section.structure"))
        self._structure_caption_label.setText(t("satellite.structure.caption"))
        self._model_path_label.setText(t("satellite.field.model_path"))
        self._browse_model_button.setText(t("satellite.button.browse_model"))
        self._use_stk_sample_button.setText(t("satellite.button.use_stk_sample"))
        self._field_labels["launch_mass_kg"].setText(t("satellite.field.launch_mass_kg"))
        self._field_labels["fuel_load_kg"].setText(t("satellite.field.fuel_load_kg"))
        self._field_labels["helium_load_kg"].setText(t("satellite.field.helium_load_kg"))
        self._field_labels["orbit_engine_thrust_n"].setText(t("satellite.field.orbit_engine_thrust_n"))
        self._field_labels["orbit_engine_isp_s"].setText(t("satellite.field.orbit_engine_isp_s"))
        self._field_labels["settle_engine_thrust_n"].setText(t("satellite.field.settle_engine_thrust_n"))
        self._field_labels["settle_engine_isp_s"].setText(t("satellite.field.settle_engine_isp_s"))
        for key in self._structure_numeric_fields:
            self._structure_field_labels[key].setText(t(f"satellite.field.{key}"))
        for key in self._structure_count_fields:
            self._structure_field_labels[key].setText(t(f"satellite.field.{key}"))
        self._apply_button.setText(t("satellite.button.apply"))
        self._refresh_preview_status_label()

        for key, section in self._sections.items():
            section.title_label.setText(t(f"satellite.section.{key}"))
            section.add_button.setText(t("satellite.button.add_row"))
            section.remove_button.setText(t("satellite.button.remove_row"))
            headers = [t(column_key) for column_key in section.columns]
            section.table.setHorizontalHeaderLabels(headers)

    @staticmethod
    def _to_float(value: str) -> float:
        try:
            return float(value)
        except ValueError:
            return 0.0
