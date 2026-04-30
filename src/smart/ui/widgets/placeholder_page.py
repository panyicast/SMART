from __future__ import annotations

from PySide6 import QtWidgets

from smart.ui.i18n import I18nManager


class PlaceholderPage(QtWidgets.QWidget):
    def __init__(
        self,
        i18n: I18nManager,
        section_key: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._section_key = section_key
        self._step_labels: list[QtWidgets.QLabel] = []

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._summary_label = QtWidgets.QLabel()
        self._summary_label.setProperty("role", "pageBody")
        self._summary_label.setWordWrap(True)
        root.addWidget(self._summary_label)

        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(12)

        self._header_label = QtWidgets.QLabel()
        self._header_label.setProperty("role", "cardTitle")
        card_layout.addWidget(self._header_label)

        for _ in range(3):
            label = QtWidgets.QLabel()
            label.setProperty("role", "pageBody")
            label.setWordWrap(True)
            card_layout.addWidget(label)
            self._step_labels.append(label)

        card_layout.addStretch(1)
        root.addWidget(card)
        root.addStretch(1)

        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t(f"{self._section_key}.title"))
        self._summary_label.setText(t(f"{self._section_key}.summary"))
        self._header_label.setText(t("placeholder.header"))
        self._step_labels[0].setText(f"- {t(f'{self._section_key}.step_1')}")
        self._step_labels[1].setText(f"- {t(f'{self._section_key}.step_2')}")
        self._step_labels[2].setText(f"- {t(f'{self._section_key}.step_3')}")
