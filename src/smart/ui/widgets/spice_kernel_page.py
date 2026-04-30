from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.spice_service import (
    COMMON_KERNEL_PRESETS,
    SpiceKernelManager,
    SpiceUnavailableError,
    download_kernel_file,
    discover_kernel_files,
    infer_kernel_filename,
    runtime_summary,
)
from smart.ui.i18n import I18nManager


@dataclass(frozen=True, slots=True)
class KernelDownloadRequest:
    url: str
    filename: str | None = None


class KernelDownloadDialog(QtWidgets.QDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._presets = COMMON_KERNEL_PRESETS
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._hint_label = QtWidgets.QLabel()
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        self._preset_title_label = QtWidgets.QLabel()
        self._preset_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._preset_title_label)

        self._preset_hint_label = QtWidgets.QLabel()
        self._preset_hint_label.setWordWrap(True)
        layout.addWidget(self._preset_hint_label)

        self._preset_table = QtWidgets.QTableWidget(0, 4)
        self._preset_table.setAlternatingRowColors(True)
        self._preset_table.verticalHeader().setVisible(False)
        self._preset_table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._preset_table.horizontalHeader().setStretchLastSection(True)
        self._preset_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._preset_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._preset_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._preset_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._preset_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self._preset_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preset_table.setWordWrap(True)
        layout.addWidget(self._preset_table)

        self._custom_title_label = QtWidgets.QLabel()
        self._custom_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._custom_title_label)

        self._form = QtWidgets.QFormLayout()
        self._form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self._form.setSpacing(10)
        layout.addLayout(self._form)

        self._url_label = QtWidgets.QLabel()
        self._filename_label = QtWidgets.QLabel()

        self._url_edit = QtWidgets.QLineEdit()
        self._url_edit.setClearButtonEnabled(True)
        self._form.addRow(self._url_label, self._url_edit)

        self._filename_edit = QtWidgets.QLineEdit()
        self._filename_edit.setClearButtonEnabled(True)
        self._form.addRow(self._filename_label, self._filename_edit)

        self._button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

        self.retranslate()
        self.resize(1120, 560)

    def download_requests(self) -> list[KernelDownloadRequest]:
        requests: list[KernelDownloadRequest] = []
        for row, preset in enumerate(self._presets):
            item = self._preset_table.item(row, 0)
            if item is None or item.checkState() != QtCore.Qt.CheckState.Checked:
                continue
            requests.append(KernelDownloadRequest(url=preset.url, filename=preset.filename))

        custom_url = self._url_edit.text().strip()
        custom_filename = self._filename_edit.text().strip() or None
        if custom_url:
            requests.append(KernelDownloadRequest(url=custom_url, filename=custom_filename))
        return requests

    def accept(self) -> None:
        requests = self.download_requests()
        if not requests:
            QtWidgets.QMessageBox.warning(
                self,
                self._i18n.t("spice.dialog.download_invalid_title"),
                self._i18n.t("spice.dialog.download_empty_selection"),
            )
            self._preset_table.setFocus()
            return
        custom_url = self._url_edit.text().strip()
        custom_filename = self._filename_edit.text().strip() or None
        if custom_url:
            try:
                infer_kernel_filename(custom_url, custom_filename)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(
                    self,
                    self._i18n.t("spice.dialog.download_invalid_title"),
                    str(exc),
                )
                self._url_edit.setFocus()
                return
        super().accept()

    def _populate_presets(self) -> None:
        checked_by_key = {
            preset.key: self._preset_table.item(row, 0).checkState() == QtCore.Qt.CheckState.Checked
            for row, preset in enumerate(self._presets)
            if self._preset_table.item(row, 0) is not None
        }
        self._preset_table.setRowCount(0)

        t = self._i18n.t
        for preset in self._presets:
            row = self._preset_table.rowCount()
            self._preset_table.insertRow(row)

            select_item = QtWidgets.QTableWidgetItem()
            select_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            select_item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if checked_by_key.get(preset.key, preset.selected_by_default)
                else QtCore.Qt.CheckState.Unchecked
            )
            self._preset_table.setItem(row, 0, select_item)

            label_item = QtWidgets.QTableWidgetItem(t(f"spice.preset.{preset.key}.label"))
            label_item.setToolTip(t(f"spice.preset.{preset.key}.description"))
            filename_item = QtWidgets.QTableWidgetItem(preset.filename)
            filename_item.setToolTip(t(f"spice.preset.{preset.key}.description"))
            url_item = QtWidgets.QTableWidgetItem(preset.url)
            url_item.setToolTip(preset.url)

            for column, item in enumerate((label_item, filename_item, url_item), start=1):
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self._preset_table.setItem(row, column, item)

        self._preset_table.resizeRowsToContents()

    def retranslate(self) -> None:
        t = self._i18n.t
        self.setWindowTitle(t("spice.dialog.download_title"))
        self._hint_label.setText(t("spice.dialog.download_hint"))
        self._preset_title_label.setText(t("spice.dialog.common_title"))
        self._preset_hint_label.setText(t("spice.dialog.common_hint"))
        self._custom_title_label.setText(t("spice.dialog.custom_title"))
        self._preset_table.setHorizontalHeaderLabels(
            [
                t("spice.dialog.common_select"),
                t("spice.dialog.common_name"),
                t("spice.dialog.common_filename"),
                t("spice.dialog.common_url"),
            ]
        )
        self._populate_presets()
        self._url_label.setText(t("spice.dialog.download_url"))
        self._filename_label.setText(t("spice.dialog.download_filename"))
        self._url_edit.setPlaceholderText(t("spice.dialog.download_url_placeholder"))
        self._filename_edit.setPlaceholderText(t("spice.dialog.download_filename_placeholder"))
        self._button_box.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText(
            t("spice.dialog.download_confirm")
        )
        self._button_box.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(
            t("dialog.cancel")
        )


class SpiceKernelPage(QtWidgets.QWidget):
    def __init__(
        self,
        kernel_manager: SpiceKernelManager,
        i18n: I18nManager,
        *,
        initial_kernel_root: Path,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kernel_manager = kernel_manager
        self._i18n = i18n
        self._kernel_root = initial_kernel_root.resolve()
        self._inventory_paths: list[Path] = []
        self._status_role = "statusDisconnected"

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

        summary_card = QtWidgets.QFrame()
        summary_card.setProperty("role", "card")
        summary_layout = QtWidgets.QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 18, 18, 18)
        summary_layout.setSpacing(10)

        self._runtime_title_label = QtWidgets.QLabel()
        self._runtime_title_label.setProperty("role", "cardTitle")
        summary_layout.addWidget(self._runtime_title_label)

        self._runtime_status_label = QtWidgets.QLabel()
        self._runtime_status_label.setWordWrap(True)
        summary_layout.addWidget(self._runtime_status_label)

        self._kernel_dir_label = QtWidgets.QLabel()
        self._kernel_dir_label.setProperty("role", "pageBody")
        self._kernel_dir_label.setWordWrap(True)
        summary_layout.addWidget(self._kernel_dir_label)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(10)
        self._rescan_button = QtWidgets.QPushButton()
        self._rescan_button.clicked.connect(self.refresh_inventory)
        action_row.addWidget(self._rescan_button)

        self._open_dir_button = QtWidgets.QPushButton()
        self._open_dir_button.clicked.connect(self._open_kernel_directory)
        action_row.addWidget(self._open_dir_button)

        self._download_button = QtWidgets.QPushButton()
        self._download_button.clicked.connect(self._download_kernel)
        action_row.addWidget(self._download_button)

        self._load_directory_button = QtWidgets.QPushButton()
        self._load_directory_button.clicked.connect(self._load_directory)
        action_row.addWidget(self._load_directory_button)

        self._clear_button = QtWidgets.QPushButton()
        self._clear_button.clicked.connect(self._clear_loaded_kernels)
        action_row.addWidget(self._clear_button)
        action_row.addStretch(1)
        summary_layout.addLayout(action_row)

        root.addWidget(summary_card)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_inventory_card())
        splitter.addWidget(self._build_loaded_card())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 620])

        self._status_label = QtWidgets.QLabel()
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._inventory_table.itemSelectionChanged.connect(self._sync_button_state)
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self.set_kernel_root(self._kernel_root)

    def _build_inventory_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._inventory_title_label = QtWidgets.QLabel()
        self._inventory_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._inventory_title_label)

        self._inventory_caption_label = QtWidgets.QLabel()
        self._inventory_caption_label.setProperty("role", "cardCaption")
        self._inventory_caption_label.setWordWrap(True)
        layout.addWidget(self._inventory_caption_label)

        self._inventory_table = QtWidgets.QTableWidget(0, 4)
        self._inventory_table.setAlternatingRowColors(True)
        self._inventory_table.verticalHeader().setVisible(False)
        self._inventory_table.horizontalHeader().setStretchLastSection(True)
        self._inventory_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._inventory_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._inventory_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._inventory_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._inventory_table, 1)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        self._load_selected_button = QtWidgets.QPushButton()
        self._load_selected_button.clicked.connect(self._load_selected_kernel)
        button_row.addWidget(self._load_selected_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return card

    def _build_loaded_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._loaded_title_label = QtWidgets.QLabel()
        self._loaded_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._loaded_title_label)

        self._loaded_caption_label = QtWidgets.QLabel()
        self._loaded_caption_label.setProperty("role", "cardCaption")
        self._loaded_caption_label.setWordWrap(True)
        layout.addWidget(self._loaded_caption_label)

        self._loaded_list = QtWidgets.QListWidget()
        layout.addWidget(self._loaded_list, 1)
        return card

    def set_kernel_root(self, kernel_root: Path) -> None:
        self._kernel_root = kernel_root.expanduser().resolve()
        self._kernel_root.mkdir(parents=True, exist_ok=True)
        self._refresh_runtime_status()
        self.refresh_inventory()
        self._refresh_loaded_kernels()
        self._set_status(
            "statusLoading",
            self._i18n.t("spice.page_status.root_ready", path=str(self._kernel_root)),
        )

    def refresh_inventory(self) -> None:
        self._kernel_root.mkdir(parents=True, exist_ok=True)
        self._inventory_paths = discover_kernel_files(self._kernel_root)
        self._inventory_table.setRowCount(0)
        for path in self._inventory_paths:
            row = self._inventory_table.rowCount()
            self._inventory_table.insertRow(row)
            relative = path.relative_to(self._kernel_root)
            items = (
                QtWidgets.QTableWidgetItem(path.name),
                QtWidgets.QTableWidgetItem(path.suffix.lstrip(".").upper()),
                QtWidgets.QTableWidgetItem(str(relative.parent) if str(relative.parent) != "." else "."),
                QtWidgets.QTableWidgetItem(f"{path.stat().st_size / 1024.0:.1f} KB"),
            )
            for column, item in enumerate(items):
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self._inventory_table.setItem(row, column, item)
        self._sync_button_state()
        self._update_inventory_caption()

    def _refresh_runtime_status(self) -> None:
        runtime_state, runtime_message = runtime_summary()
        role = "statusReady" if runtime_state == "Ready" else "statusDisconnected"
        self._runtime_status_label.setProperty("role", role)
        self._runtime_status_label.style().unpolish(self._runtime_status_label)
        self._runtime_status_label.style().polish(self._runtime_status_label)
        self._runtime_status_label.setText(runtime_message)

    def _refresh_loaded_kernels(self) -> None:
        self._loaded_list.clear()
        for kernel in self._kernel_manager.loaded_kernels:
            self._loaded_list.addItem(str(kernel))
        self._loaded_caption_label.setText(
            self._i18n.t("spice.loaded_caption", count=len(self._kernel_manager.loaded_kernels))
        )
        self._sync_button_state()

    def _update_inventory_caption(self) -> None:
        self._inventory_caption_label.setText(
            self._i18n.t(
                "spice.inventory_caption",
                count=len(self._inventory_paths),
                path=str(self._kernel_root),
            )
        )
        self._kernel_dir_label.setText(self._i18n.t("spice.kernel_dir", path=str(self._kernel_root)))

    def _load_selected_kernel(self) -> None:
        row = self._inventory_table.currentRow()
        if row < 0 or row >= len(self._inventory_paths):
            self._set_status("statusDisconnected", self._i18n.t("spice.page_status.no_selection"))
            return

        try:
            loaded = self._kernel_manager.load_kernel(self._inventory_paths[row])
        except (FileNotFoundError, SpiceUnavailableError, RuntimeError) as exc:
            self._set_status("statusDisconnected", self._i18n.t("spice.page_status.action_failed", error=str(exc)))
            return

        self._refresh_loaded_kernels()
        self._set_status("statusReady", self._i18n.t("spice.page_status.loaded_one", path=str(loaded)))

    def _load_directory(self) -> None:
        try:
            loaded = self._kernel_manager.load_directory(self._kernel_root)
        except (FileNotFoundError, SpiceUnavailableError, RuntimeError) as exc:
            self._set_status("statusDisconnected", self._i18n.t("spice.page_status.action_failed", error=str(exc)))
            return

        self._refresh_loaded_kernels()
        self._set_status("statusReady", self._i18n.t("spice.page_status.loaded_count", count=len(loaded)))

    def _clear_loaded_kernels(self) -> None:
        try:
            self._kernel_manager.clear()
        except (SpiceUnavailableError, RuntimeError) as exc:
            self._set_status("statusDisconnected", self._i18n.t("spice.page_status.action_failed", error=str(exc)))
            return

        self._refresh_loaded_kernels()
        self._set_status("statusLoading", self._i18n.t("spice.page_status.cleared"))

    def _open_kernel_directory(self) -> None:
        self._kernel_root.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._kernel_root)))

    def _download_kernel(self) -> None:
        dialog = KernelDownloadDialog(self._i18n, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        requests = dialog.download_requests()
        if not requests:
            return

        app = QtWidgets.QApplication.instance()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        if app is not None:
            app.processEvents()
        downloaded_paths: list[Path] = []
        try:
            for request in requests:
                target_name = infer_kernel_filename(request.url, request.filename)
                overwrite = False
                target_path = self._kernel_root / target_name
                if target_path.exists():
                    answer = QtWidgets.QMessageBox.question(
                        self,
                        self._i18n.t("spice.dialog.overwrite_title"),
                        self._i18n.t("spice.dialog.overwrite_body", path=str(target_path)),
                        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                        QtWidgets.QMessageBox.StandardButton.No,
                    )
                    if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                        continue
                    overwrite = True

                self._set_status("statusLoading", self._i18n.t("spice.page_status.downloading", url=request.url))
                if app is not None:
                    app.processEvents()
                downloaded_paths.append(
                    download_kernel_file(request.url, self._kernel_root, request.filename, overwrite=overwrite)
                )
        except (ValueError, OSError) as exc:
            self.refresh_inventory()
            if downloaded_paths:
                self._set_status(
                    "statusDisconnected",
                    self._i18n.t(
                        "spice.page_status.download_partial",
                        count=len(downloaded_paths),
                        error=str(exc),
                    ),
                )
            else:
                self._set_status("statusDisconnected", self._i18n.t("spice.page_status.action_failed", error=str(exc)))
            return
        finally:
            if QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()

        self.refresh_inventory()
        if not downloaded_paths:
            self._set_status("statusLoading", self._i18n.t("spice.page_status.download_skipped"))
            return
        if len(downloaded_paths) == 1:
            self._set_status("statusReady", self._i18n.t("spice.page_status.downloaded", path=str(downloaded_paths[0])))
            return
        self._set_status(
            "statusReady",
            self._i18n.t("spice.page_status.downloaded_count", count=len(downloaded_paths), path=str(self._kernel_root)),
        )

    def _sync_button_state(self) -> None:
        available = self._kernel_manager.available
        has_inventory = len(self._inventory_paths) > 0
        has_loaded = len(self._kernel_manager.loaded_kernels) > 0
        has_selection = self._inventory_table.currentRow() >= 0
        self._load_selected_button.setEnabled(available and has_selection)
        self._load_directory_button.setEnabled(available and has_inventory)
        self._clear_button.setEnabled(available and has_loaded)
        self._download_button.setEnabled(True)

    def _set_status(self, role: str, text: str) -> None:
        self._status_role = role
        self._status_label.setProperty("role", role)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setText(text)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("spice.title"))
        self._subtitle_label.setText(t("spice.subtitle"))
        self._runtime_title_label.setText(t("spice.runtime_title"))
        self._rescan_button.setText(t("spice.button.rescan"))
        self._open_dir_button.setText(t("spice.button.open_dir"))
        self._download_button.setText(t("spice.button.download"))
        self._load_directory_button.setText(t("spice.button.load_directory"))
        self._clear_button.setText(t("spice.button.clear"))
        self._inventory_title_label.setText(t("spice.inventory_title"))
        self._loaded_title_label.setText(t("spice.loaded_title"))
        self._load_selected_button.setText(t("spice.button.load_selected"))
        self._inventory_table.setHorizontalHeaderLabels(
            [
                t("spice.table.filename"),
                t("spice.table.type"),
                t("spice.table.location"),
                t("spice.table.size"),
            ]
        )
        self._refresh_runtime_status()
        self._update_inventory_caption()
        self._refresh_loaded_kernels()
        self._set_status(self._status_role, self._status_label.text())
