from __future__ import annotations

from html import unescape
import re

from PySide6 import QtCore, QtGui, QtWidgets


_GROUND_TRACK_LABEL_TAG_RE = re.compile(r"<[^>]+>")


def _plain_ground_track_label_text(payload: object) -> str:
    text = unescape(_GROUND_TRACK_LABEL_TAG_RE.sub("", str(payload))).strip()
    return " ".join(text.split())


def _ground_track_label_payload(args: tuple[object, ...], kwargs: dict[str, object]) -> object:
    if kwargs.get("html") is not None:
        return kwargs["html"]
    if kwargs.get("text") is not None:
        return kwargs["text"]
    if args and isinstance(args[0], str):
        return args[0]
    return ""


def _looks_like_ground_track_maneuver_label(args: tuple[object, ...], kwargs: dict[str, object]) -> bool:
    if "border" not in kwargs and "fill" not in kwargs:
        return False
    text = _plain_ground_track_label_text(_ground_track_label_payload(args, kwargs))
    number_text = text.replace("第", "").replace("次", "").strip()
    return bool(number_text) and number_text.isdigit() and len(number_text) <= 3


class _OutlinedGroundTrackLabelItem(QtWidgets.QGraphicsTextItem):
    _outline_width = 4.0

    def boundingRect(self) -> QtCore.QRectF:
        padding = self._outline_width + 1.0
        return super().boundingRect().adjusted(-padding, -padding, padding, padding)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        text = self.toPlainText().strip()
        if not text:
            super().paint(painter, option, widget)
            return
        font = self.font()
        font.setBold(True)
        metrics = QtGui.QFontMetricsF(font)
        path = QtGui.QPainterPath()
        path.addText(0.0, metrics.ascent(), font, text)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(
            QtGui.QPen(
                QtGui.QColor("#02060a"),
                self._outline_width,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(self.defaultTextColor())
        painter.drawPath(path)
        painter.restore()


def _install_ground_track_label_text_item_patch() -> None:
    try:
        import pyqtgraph as pg
    except Exception:
        return

    original_text_item = getattr(pg, "TextItem", None)
    if original_text_item is None or getattr(original_text_item, "_smart_ground_track_label_patch", False):
        return

    class SmartGroundTrackTextItem(original_text_item):  # type: ignore[misc, valid-type]
        _smart_ground_track_label_patch = True

        def __init__(self, *args: object, **kwargs: object) -> None:
            args_list = list(args)
            self._smart_ground_track_outline = _looks_like_ground_track_maneuver_label(tuple(args_list), kwargs)
            if self._smart_ground_track_outline:
                label_text = _plain_ground_track_label_text(_ground_track_label_payload(tuple(args_list), kwargs))
                kwargs.pop("html", None)
                kwargs["border"] = None
                kwargs["fill"] = None
                if args_list and isinstance(args_list[0], str):
                    args_list[0] = label_text
                else:
                    kwargs["text"] = label_text
            super().__init__(*tuple(args_list), **kwargs)
            if self._smart_ground_track_outline:
                self._replace_text_item_with_outline_item()

        def _replace_text_item_with_outline_item(self) -> None:
            old_text_item = getattr(self, "textItem", None)
            if old_text_item is None:
                return
            label_text = old_text_item.toPlainText().strip()
            font = old_text_item.font()
            font.setBold(True)
            text_color = old_text_item.defaultTextColor()
            if not text_color.isValid():
                text_color = QtGui.QColor("#f8fbff")
            old_text_item.setParentItem(None)
            old_text_item.hide()

            self.textItem = _OutlinedGroundTrackLabelItem()
            self.textItem.setParentItem(self)
            self.textItem.setFont(font)
            self.textItem.setDefaultTextColor(text_color)
            self.textItem.setPlainText(label_text)
            self.updateText()

    pg.TextItem = SmartGroundTrackTextItem


_install_ground_track_label_text_item_patch()


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()


class NoWheelSpinBox(QtWidgets.QSpinBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()


class NoWheelDateTimeEdit(QtWidgets.QDateTimeEdit):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()


class NoWheelComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()
