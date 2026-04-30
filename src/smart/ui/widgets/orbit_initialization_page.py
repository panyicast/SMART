from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from smart.domain.models import OrbitInitializationSettings, OrbitalElements
from smart.services.orbit_initialization import (
    OrbitInitializationError,
    build_classical_initialization,
    load_stk_ephemeris_file,
    parse_tle_text,
)
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState
from smart.ui.widgets.spinboxes import NoWheelDateTimeEdit, NoWheelDoubleSpinBox

_VALIDATION_ERROR_KEYS = {
    "Orbit initialization mode is not supported.": "orbit_init.error.mode",
    "Orbit epoch is required.": "orbit_init.error.epoch_required",
    "Orbit epoch must be an ISO-8601 UTC time.": "orbit_init.error.epoch_invalid",
    "TLE text must contain line 1 and line 2 records.": "orbit_init.error.tle_missing_lines",
    "TLE line 1 must start with '1 '.": "orbit_init.error.tle_line1",
    "TLE line 2 must start with '2 '.": "orbit_init.error.tle_line2",
    "TLE records are malformed.": "orbit_init.error.tle_malformed",
    "STK ephemeris file path is required.": "orbit_init.error.ephemeris_path_required",
    "STK ephemeris must define ScenarioEpoch.": "orbit_init.error.ephemeris_epoch_missing",
    "STK ephemeris ScenarioEpoch is not recognized.": "orbit_init.error.ephemeris_epoch_invalid",
    "STK ephemeris must contain EphemerisTimePosVel or numeric TimePosVel samples.": "orbit_init.error.ephemeris_format",
    "STK ephemeris must provide position and velocity columns.": "orbit_init.error.ephemeris_columns",
    "Only Earth-centered STK ephemeris files are currently supported.": "orbit_init.error.ephemeris_central_body",
    "STK ephemeris coordinate system is not supported by SPICE conversion.": "orbit_init.error.ephemeris_frame",
    "STK ephemeris frame conversion requires local SPICE kernels.": "orbit_init.error.ephemeris_kernels",
    "State vector must provide three position and velocity components.": "orbit_init.error.state_vector",
    "State vector radius must be greater than zero.": "orbit_init.error.state_vector",
    "State vector cannot produce a valid orbital plane.": "orbit_init.error.state_vector",
    "Parabolic trajectories are not supported.": "orbit_init.error.state_vector",
    "Only bound elliptical trajectories are currently supported.": "orbit_init.error.state_vector",
    "Semi-major axis must be larger than the central-body radius.": "orbit.error.semi_major_axis",
    "Eccentricity must satisfy 0 <= e < 1 for an elliptical orbit.": "orbit.error.eccentricity",
    "Periapsis must remain above the central-body surface.": "orbit.error.periapsis",
}


class OrbitInitializationPage(QtWidgets.QWidget):
    def __init__(
        self,
        mission_state: MissionState,
        i18n: I18nManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mission_state = mission_state
        self._i18n = i18n
        self._suppress_sync = False
        self._last_error_message: str | None = None

        self._classical_field_labels: dict[str, QtWidgets.QLabel] = {}
        self._summary_caption_labels: dict[str, QtWidgets.QLabel] = {}
        self._summary_value_labels: dict[str, QtWidgets.QLabel] = {}

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

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        self._tab_widget = QtWidgets.QTabWidget()
        self._build_classical_tab()
        self._build_tle_tab()
        self._build_stk_tab()
        splitter.addWidget(self._tab_widget)

        summary_panel = QtWidgets.QWidget()
        summary_layout = QtWidgets.QVBoxLayout(summary_panel)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(14)
        summary_layout.addWidget(self._build_current_summary_card())
        summary_layout.addWidget(self._build_status_card())
        summary_layout.addStretch(1)
        splitter.addWidget(summary_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([980, 420])

        self._mission_state.initialization_changed.connect(self._apply_settings)
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self._apply_settings(self._mission_state.initialization)

    def _build_classical_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(12)

        self._classical_intro_label = QtWidgets.QLabel()
        self._classical_intro_label.setProperty("role", "cardCaption")
        self._classical_intro_label.setWordWrap(True)
        card_layout.addWidget(self._classical_intro_label)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)

        self._epoch_label = QtWidgets.QLabel()
        self._epoch_edit = NoWheelDateTimeEdit()
        self._epoch_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss 'UTC'")
        self._epoch_edit.setCalendarPopup(True)
        self._epoch_edit.setTimeSpec(QtCore.Qt.TimeSpec.UTC)
        self._epoch_edit.setDateTime(QtCore.QDateTime.currentDateTimeUtc())
        form.addRow(self._epoch_label, self._epoch_edit)

        spinbox_specs = {
            "semi_major_axis": (7000.0, 6600.0, 120000.0, 10.0, 1),
            "eccentricity": (0.05, 0.0, 0.95, 0.001, 6),
            "inclination": (28.5, 0.0, 180.0, 0.1, 3),
            "raan": (40.0, 0.0, 360.0, 0.1, 3),
            "argp": (10.0, 0.0, 360.0, 0.1, 3),
            "true_anomaly": (0.0, 0.0, 360.0, 0.1, 3),
        }
        self._classical_spinboxes: dict[str, QtWidgets.QDoubleSpinBox] = {}
        for key, (value, minimum, maximum, step, decimals) in spinbox_specs.items():
            label = QtWidgets.QLabel()
            spin = NoWheelDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setValue(value)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            form.addRow(label, spin)
            self._classical_field_labels[key] = label
            self._classical_spinboxes[key] = spin

        card_layout.addLayout(form)

        self._apply_classical_button = QtWidgets.QPushButton()
        self._apply_classical_button.clicked.connect(self._apply_classical)
        card_layout.addWidget(self._apply_classical_button)

        layout.addWidget(card)
        layout.addStretch(1)
        self._tab_widget.addTab(tab, "")

    def _build_tle_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(12)

        self._tle_intro_label = QtWidgets.QLabel()
        self._tle_intro_label.setProperty("role", "cardCaption")
        self._tle_intro_label.setWordWrap(True)
        card_layout.addWidget(self._tle_intro_label)

        self._tle_path_label = QtWidgets.QLabel()
        self._tle_path_edit = QtWidgets.QLineEdit()
        self._browse_tle_button = QtWidgets.QPushButton()
        self._browse_tle_button.clicked.connect(self._browse_tle_file)
        self._load_tle_button = QtWidgets.QPushButton()
        self._load_tle_button.clicked.connect(self._load_tle_file)

        path_row = QtWidgets.QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self._tle_path_edit, 1)
        path_row.addWidget(self._browse_tle_button)
        path_row.addWidget(self._load_tle_button)

        path_widget = QtWidgets.QWidget()
        path_widget.setLayout(path_row)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        form.addRow(self._tle_path_label, path_widget)

        self._tle_text_label = QtWidgets.QLabel()
        self._tle_text_edit = QtWidgets.QPlainTextEdit()
        self._tle_text_edit.setMinimumHeight(220)
        self._tle_text_edit.setPlaceholderText("1 ...\n2 ...")
        form.addRow(self._tle_text_label, self._tle_text_edit)
        card_layout.addLayout(form)

        self._apply_tle_button = QtWidgets.QPushButton()
        self._apply_tle_button.clicked.connect(self._apply_tle)
        card_layout.addWidget(self._apply_tle_button)

        layout.addWidget(card)
        layout.addStretch(1)
        self._tab_widget.addTab(tab, "")

    def _build_stk_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(12)

        self._stk_intro_label = QtWidgets.QLabel()
        self._stk_intro_label.setProperty("role", "cardCaption")
        self._stk_intro_label.setWordWrap(True)
        card_layout.addWidget(self._stk_intro_label)

        self._ephemeris_path_label = QtWidgets.QLabel()
        self._ephemeris_path_edit = QtWidgets.QLineEdit()
        self._browse_ephemeris_button = QtWidgets.QPushButton()
        self._browse_ephemeris_button.clicked.connect(self._browse_ephemeris_file)

        ephemeris_row = QtWidgets.QHBoxLayout()
        ephemeris_row.setSpacing(8)
        ephemeris_row.addWidget(self._ephemeris_path_edit, 1)
        ephemeris_row.addWidget(self._browse_ephemeris_button)

        ephemeris_widget = QtWidgets.QWidget()
        ephemeris_widget.setLayout(ephemeris_row)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        form.addRow(self._ephemeris_path_label, ephemeris_widget)
        card_layout.addLayout(form)

        self._stk_hint_label = QtWidgets.QLabel()
        self._stk_hint_label.setProperty("role", "pageBody")
        self._stk_hint_label.setWordWrap(True)
        card_layout.addWidget(self._stk_hint_label)

        self._apply_ephemeris_button = QtWidgets.QPushButton()
        self._apply_ephemeris_button.clicked.connect(self._apply_stk_ephemeris)
        card_layout.addWidget(self._apply_ephemeris_button)

        layout.addWidget(card)
        layout.addStretch(1)
        self._tab_widget.addTab(tab, "")

    def _build_current_summary_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._current_title_label = QtWidgets.QLabel()
        self._current_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._current_title_label)

        for key in ("mode", "epoch", "source", "elements"):
            caption = QtWidgets.QLabel()
            caption.setProperty("role", "cardCaption")
            value = QtWidgets.QLabel()
            value.setProperty("role", "pageBody")
            value.setWordWrap(True)
            layout.addWidget(caption)
            layout.addWidget(value)
            self._summary_caption_labels[key] = caption
            self._summary_value_labels[key] = value
        return card

    def _build_status_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._status_title_label = QtWidgets.QLabel()
        self._status_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._status_title_label)

        self._status_label = QtWidgets.QLabel()
        self._status_label.setProperty("role", "pageBody")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        return card

    def _apply_classical(self) -> None:
        elements = OrbitalElements(
            semi_major_axis_km=self._classical_spinboxes["semi_major_axis"].value(),
            eccentricity=self._classical_spinboxes["eccentricity"].value(),
            inclination_deg=self._classical_spinboxes["inclination"].value(),
            raan_deg=self._classical_spinboxes["raan"].value(),
            argument_of_periapsis_deg=self._classical_spinboxes["argp"].value(),
            true_anomaly_deg=self._classical_spinboxes["true_anomaly"].value(),
        )
        try:
            settings = build_classical_initialization(self._epoch_iso_text(), elements)
            self._mission_state.update_initialization(settings)
        except (OrbitInitializationError, ValueError) as exc:
            self._set_error(str(exc))
            return
        self._set_status(
            self._i18n.t(
                "orbit_init.status.applied",
                mode=self._mode_text(settings.mode),
                epoch=settings.epoch_utc,
            )
        )

    def _browse_tle_file(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self._i18n.t("orbit_init.dialog.tle_title"),
            str(self._suggest_open_dir(self._tle_path_edit.text())),
            self._i18n.t("orbit_init.dialog.tle_filter"),
        )
        if not file_path:
            return
        self._tle_path_edit.setText(file_path)
        self._load_tle_file()

    def _load_tle_file(self) -> None:
        path_text = self._tle_path_edit.text().strip()
        if not path_text:
            self._set_error(self._i18n.t("orbit_init.error.file_required"))
            return
        try:
            content = Path(path_text).expanduser().read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            self._set_error(str(exc))
            return
        self._tle_text_edit.setPlainText(content.strip())
        self._set_status(self._i18n.t("orbit_init.status.tle_loaded", path=path_text))

    def _apply_tle(self) -> None:
        try:
            settings = parse_tle_text(self._tle_text_edit.toPlainText())
            self._mission_state.update_initialization(settings)
        except (OrbitInitializationError, ValueError) as exc:
            self._set_error(str(exc))
            return
        self._set_status(
            self._i18n.t(
                "orbit_init.status.applied",
                mode=self._mode_text(settings.mode),
                epoch=settings.epoch_utc,
            )
        )

    def _browse_ephemeris_file(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self._i18n.t("orbit_init.dialog.ephemeris_title"),
            str(self._suggest_open_dir(self._ephemeris_path_edit.text())),
            self._i18n.t("orbit_init.dialog.ephemeris_filter"),
        )
        if file_path:
            self._ephemeris_path_edit.setText(file_path)

    def _apply_stk_ephemeris(self) -> None:
        try:
            settings = load_stk_ephemeris_file(self._ephemeris_path_edit.text().strip())
            self._mission_state.update_initialization(settings)
        except (OrbitInitializationError, ValueError, OSError) as exc:
            self._set_error(str(exc))
            return
        self._set_status(
            self._i18n.t(
                "orbit_init.status.applied",
                mode=self._mode_text(settings.mode),
                epoch=settings.epoch_utc,
            )
        )

    def _apply_settings(self, settings: object) -> None:
        if not isinstance(settings, OrbitInitializationSettings):
            return

        self._suppress_sync = True
        try:
            self._set_epoch_value(settings.epoch_utc)
            elements = settings.elements
            self._classical_spinboxes["semi_major_axis"].setValue(elements.semi_major_axis_km)
            self._classical_spinboxes["eccentricity"].setValue(elements.eccentricity)
            self._classical_spinboxes["inclination"].setValue(elements.inclination_deg)
            self._classical_spinboxes["raan"].setValue(elements.raan_deg)
            self._classical_spinboxes["argp"].setValue(elements.argument_of_periapsis_deg)
            self._classical_spinboxes["true_anomaly"].setValue(elements.true_anomaly_deg)
            self._tle_text_edit.setPlainText(
                "\n".join(line for line in (settings.tle_line1, settings.tle_line2) if line)
            )
            self._ephemeris_path_edit.setText(settings.ephemeris_file_path)
            self._tab_widget.setCurrentIndex({"classical": 0, "tle": 1, "stk_ephemeris": 2}.get(settings.mode, 0))
        finally:
            self._suppress_sync = False

        self._summary_value_labels["mode"].setText(self._mode_text(settings.mode))
        self._summary_value_labels["epoch"].setText(settings.epoch_utc)
        self._summary_value_labels["source"].setText(self._source_text(settings))
        self._summary_value_labels["elements"].setText(self._elements_text(settings.elements))

    def _epoch_iso_text(self) -> str:
        dt = self._epoch_edit.dateTime().toUTC()
        return dt.toString("yyyy-MM-ddTHH:mm:ss'Z'")

    def _set_epoch_value(self, epoch_utc: str) -> None:
        parsed = QtCore.QDateTime.fromString(epoch_utc, QtCore.Qt.DateFormat.ISODate)
        if parsed.isValid():
            self._epoch_edit.setDateTime(parsed.toUTC())

    def _mode_text(self, mode: str) -> str:
        return self._i18n.t(f"orbit_init.mode.{mode}")

    def _source_text(self, settings: OrbitInitializationSettings) -> str:
        if settings.mode == "tle":
            return settings.tle_line1 or self._i18n.t("orbit_init.source.tle")
        if settings.mode == "stk_ephemeris":
            return settings.ephemeris_file_path or self._i18n.t("orbit_init.source.stk_ephemeris")
        return self._i18n.t("orbit_init.source.classical")

    def _elements_text(self, elements: OrbitalElements) -> str:
        return self._i18n.t(
            "orbit_init.summary.elements",
            a=elements.semi_major_axis_km,
            e=elements.eccentricity,
            inc=elements.inclination_deg,
            raan=elements.raan_deg,
            argp=elements.argument_of_periapsis_deg,
            ta=elements.true_anomaly_deg,
        )

    def _set_status(self, message: str) -> None:
        self._last_error_message = None
        self._status_label.setStyleSheet("")
        self._status_label.setText(message)

    def _set_error(self, message: str) -> None:
        self._last_error_message = message
        translated = self._translate_error(message)
        self._status_label.setStyleSheet("color: #a13f22;")
        self._status_label.setText(translated)

    def _translate_error(self, message: str) -> str:
        key = _VALIDATION_ERROR_KEYS.get(message)
        if key is None and message.startswith("STK ephemeris distance unit '"):
            key = "orbit_init.error.ephemeris_units"
        if key is None:
            return message
        if key == "orbit_init.error.ephemeris_units":
            unit = message.split("'")[1] if "'" in message else ""
            return self._i18n.t(key, unit=unit)
        return self._i18n.t(key)

    def _suggest_open_dir(self, current_text: str) -> Path:
        if current_text:
            current_path = Path(current_text).expanduser()
            if current_path.exists():
                return current_path.parent if current_path.is_file() else current_path
        return Path.cwd()

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("orbit_init.title"))
        self._subtitle_label.setText(t("orbit_init.subtitle"))
        self._tab_widget.setTabText(0, t("orbit_init.mode.classical"))
        self._tab_widget.setTabText(1, t("orbit_init.mode.tle"))
        self._tab_widget.setTabText(2, t("orbit_init.mode.stk_ephemeris"))

        self._classical_intro_label.setText(t("orbit_init.classical.instructions"))
        self._epoch_label.setText(t("orbit_init.field.epoch"))
        self._classical_field_labels["semi_major_axis"].setText(t("orbit.field.semi_major_axis"))
        self._classical_field_labels["eccentricity"].setText(t("orbit.field.eccentricity"))
        self._classical_field_labels["inclination"].setText(t("orbit.field.inclination"))
        self._classical_field_labels["raan"].setText(t("orbit.field.raan"))
        self._classical_field_labels["argp"].setText(t("orbit.field.argp"))
        self._classical_field_labels["true_anomaly"].setText(t("orbit.field.true_anomaly"))
        self._apply_classical_button.setText(t("orbit_init.button.apply_classical"))

        self._tle_intro_label.setText(t("orbit_init.tle.instructions"))
        self._tle_path_label.setText(t("orbit_init.field.tle_path"))
        self._tle_text_label.setText(t("orbit_init.field.tle_text"))
        self._browse_tle_button.setText(t("orbit_init.button.browse_tle"))
        self._load_tle_button.setText(t("orbit_init.button.load_tle"))
        self._apply_tle_button.setText(t("orbit_init.button.apply_tle"))

        self._stk_intro_label.setText(t("orbit_init.stk.instructions"))
        self._ephemeris_path_label.setText(t("orbit_init.field.ephemeris_path"))
        self._stk_hint_label.setText(t("orbit_init.stk.hint"))
        self._browse_ephemeris_button.setText(t("orbit_init.button.browse_ephemeris"))
        self._apply_ephemeris_button.setText(t("orbit_init.button.apply_ephemeris"))

        self._current_title_label.setText(t("orbit_init.current_title"))
        self._summary_caption_labels["mode"].setText(t("orbit_init.current_mode"))
        self._summary_caption_labels["epoch"].setText(t("orbit_init.current_epoch"))
        self._summary_caption_labels["source"].setText(t("orbit_init.current_source"))
        self._summary_caption_labels["elements"].setText(t("orbit_init.current_elements"))
        self._status_title_label.setText(t("orbit_init.status_title"))

        if self._last_error_message:
            self._set_error(self._last_error_message)
        elif not self._status_label.text():
            self._set_status(t("orbit_init.status.idle"))
        self._apply_settings(self._mission_state.initialization)
