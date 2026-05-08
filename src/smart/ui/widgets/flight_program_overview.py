"""飞行程序总览的甘特图控件。

从原 ``flight_program_page.py`` 拆分出的可复用 widget：

- ``FlightProgramOverviewWidget`` —— 自绘甘特图，支持滚轮缩放、中键平移、
  双击重置以及底部滚动条拖动。
- ``_FlightProgramScrollArea`` —— 拦截外层滚轮，把事件转发给具备
  ``_zoom_view`` / ``_plot_rect`` 鸭子接口的子控件，避免被外层
  ``QScrollArea`` 抢占。

把这部分提取出来后，``flight_program_page.py`` 可以专注于业务页面装配。
"""

from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.flight_program import (
    DEPLOYMENT_KIND,
    MODE_AFM,
    MODE_EPM,
    MODE_SPM,
    MODE_TRANSITION,
)


class FlightProgramOverviewWidget(QtWidgets.QWidget):
    playhead_changed = QtCore.Signal(float)
    event_selected = QtCore.Signal(str)
    reference_selected = QtCore.Signal(str)

    _ROWS = (
        ("burn", "参考：点火"),
        ("shadow", "参考：地影"),
        ("ground", "参考：地面站"),
        ("relay", "参考：中继星"),
        ("attitude", "程序：姿态"),
        ("deployment", "程序：主要事件"),
    )
    _COLORS = {
        "burn": QtGui.QColor("#D3222A"),
        "shadow": QtGui.QColor("#6B7F88"),
        "ground": QtGui.QColor("#2FC18B"),
        "relay": QtGui.QColor("#3F8FE5"),
        MODE_SPM: QtGui.QColor("#2DBE9B"),
        MODE_EPM: QtGui.QColor("#4AA3FF"),
        MODE_AFM: QtGui.QColor("#E4584F"),
        MODE_TRANSITION: QtGui.QColor("#E8A94B"),
        "deployment": QtGui.QColor("#B887FF"),
    }
    _PLOT_LEFT = 146.0
    _PLOT_RIGHT = 18.0
    _PLOT_TOP = 26.0
    _PLOT_BOTTOM = 20.0
    _MIN_VIEW_SPAN_MIN = 0.5

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._events: list[dict[str, Any]] = []
        self._reference_segments: list[dict[str, Any]] = []
        self._duration_min = 60.0
        self._playhead_min = 0.0
        self._selected_event_id = ""
        self._selected_reference_id = ""
        self._hit_rects: list[tuple[QtCore.QRectF, str, str]] = []
        self._dragging_playhead = False
        self._view_start_min = 0.0
        self._view_end_min = self._duration_min
        self._pan_origin_x: float | None = None
        self._pan_origin_view_start_min: float | None = None
        self._pan_origin_view_end_min: float | None = None
        self._dragging_indicator = False
        self._indicator_grab_offset = 0.0
        self.setMinimumHeight(270)
        self.setMouseTracking(True)
        self.setToolTip(
            "滚轮缩放 / 中键拖动平移 / 双击空白处重置\n"
            "底部指示条可左键拖动平移视图"
        )

    def set_data(
        self,
        *,
        events: list[dict[str, Any]],
        reference_segments: list[dict[str, Any]],
        duration_min: float,
        playhead_min: float,
        selected_event_id: str = "",
        selected_reference_id: str = "",
    ) -> None:
        previous_duration = self._duration_min
        new_duration = max(1.0, float(duration_min))
        self._events = [dict(item) for item in events]
        self._reference_segments = [dict(item) for item in reference_segments]
        self._duration_min = new_duration
        self._playhead_min = min(max(0.0, float(playhead_min)), self._duration_min)
        self._selected_event_id = selected_event_id
        self._selected_reference_id = selected_reference_id
        if abs(new_duration - previous_duration) > 1e-6:
            self._reset_view_range()
        else:
            self._clamp_view_range()
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            if self._plot_rect().contains(event.position()) and self._can_pan():
                self._pan_origin_x = float(event.position().x())
                self._pan_origin_view_start_min = self._view_start_min
                self._pan_origin_view_end_min = self._view_end_min
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
                event.accept()
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        point = event.position()
        track = self._indicator_track_rect()
        if track is not None and track.contains(point):
            handle = self._indicator_handle_rect()
            if handle is not None and handle.contains(point):
                self._indicator_grab_offset = float(point.x() - handle.left())
            else:
                handle_width = handle.width() if handle is not None else 0.0
                self._indicator_grab_offset = handle_width / 2.0
            self._dragging_indicator = True
            self._update_view_from_indicator_x(float(point.x()))
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        for rect, item_kind, item_id in reversed(self._hit_rects):
            if rect.contains(point):
                if item_kind == "event":
                    self.event_selected.emit(item_id)
                else:
                    self.reference_selected.emit(item_id)
                return
        self._dragging_playhead = True
        self.playhead_changed.emit(self._x_to_min(point.x()))

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging_indicator:
            self._update_view_from_indicator_x(float(event.position().x()))
            event.accept()
            return
        if self._pan_origin_x is not None:
            self._pan_view_from_drag(float(event.position().x()))
            event.accept()
            return
        if self._dragging_playhead:
            self.playhead_changed.emit(self._x_to_min(event.position().x()))
            return
        track = self._indicator_track_rect()
        if track is not None and track.contains(event.position()):
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.MiddleButton and self._pan_origin_x is not None:
            self._end_pan()
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if self._dragging_indicator:
                self._dragging_indicator = False
                self._indicator_grab_offset = 0.0
                self.unsetCursor()
                event.accept()
                return
            self._dragging_playhead = False

    def leaveEvent(self, _event: QtCore.QEvent) -> None:
        self._dragging_playhead = False
        if self._dragging_indicator:
            self._dragging_indicator = False
            self._indicator_grab_offset = 0.0
        self.unsetCursor()
        if self._pan_origin_x is not None:
            self._end_pan()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._plot_rect().contains(event.position()) and event.angleDelta().y():
            factor = 0.8 if event.angleDelta().y() > 0 else 1.25
            if self._zoom_view(float(event.position().x()), factor):
                self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._plot_rect().contains(event.position()):
            for rect, _kind, _id in self._hit_rects:
                if rect.contains(event.position()):
                    super().mouseDoubleClickEvent(event)
                    return
            self._reset_view_range()
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, QtGui.QColor("#071016"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#1E3B49"), 1))
        painter.setBrush(QtGui.QColor("#0B1A22"))
        painter.drawRoundedRect(rect, 8, 8)
        left = self._PLOT_LEFT
        right = self._PLOT_RIGHT
        top = 46.0
        row_height = 24.0
        row_gap = 12.0
        plot_width = max(1.0, rect.width() - left - right)
        self._hit_rects = []
        visible_start, visible_end = self._visible_range()
        visible_span = max(self._MIN_VIEW_SPAN_MIN, visible_end - visible_start)
        tick_label_format = self._tick_label_format(visible_span)

        for tick in range(6):
            ratio = tick / 5
            x = left + plot_width * ratio
            minute = visible_start + visible_span * ratio
            painter.setPen(QtGui.QPen(QtGui.QColor("#234958"), 1))
            painter.drawLine(QtCore.QPointF(x, 26), QtCore.QPointF(x, rect.height() - 20))
            painter.setPen(QtGui.QColor("#A7D8E8"))
            painter.drawText(
                QtCore.QRectF(x - 48, 8, 96, 18),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                tick_label_format.format(minute=minute),
            )

        for row_index, (_key, label) in enumerate(self._ROWS):
            y = top + row_index * (row_height + row_gap)
            if row_index % 2:
                painter.fillRect(QtCore.QRectF(1, y - 6, rect.width() - 2, row_height + 12), QtGui.QColor("#0F2530"))
            painter.setPen(QtGui.QColor("#EAF7FB"))
            painter.drawText(QtCore.QRectF(8, y, left - 18, row_height), QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter, label)
            painter.setPen(QtGui.QPen(QtGui.QColor("#244958"), 1))
            painter.drawLine(QtCore.QPointF(left, y + row_height / 2), QtCore.QPointF(left + plot_width, y + row_height / 2))

        for segment in self._reference_segments:
            self._draw_item(painter, segment, left, top, row_height, row_gap, plot_width, item_kind="reference")
        for event in self._events:
            self._draw_item(painter, event, left, top, row_height, row_gap, plot_width, item_kind="event")

        if visible_start - 1e-9 <= self._playhead_min <= visible_end + 1e-9:
            playhead_x = left + ((self._playhead_min - visible_start) / visible_span) * plot_width
            painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 2))
            painter.drawLine(QtCore.QPointF(playhead_x, 28), QtCore.QPointF(playhead_x, rect.height() - 18))
            painter.setBrush(QtGui.QColor("#FFFFFF"))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(QtCore.QPointF(playhead_x, 28), 4, 4)

        track = self._indicator_track_rect()
        if track is not None:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor("#15303C"))
            painter.drawRoundedRect(track, 3, 3)
            handle = self._indicator_handle_rect()
            if handle is not None:
                painter.setBrush(QtGui.QColor("#5DC4D8") if self._dragging_indicator else QtGui.QColor("#3FA8BC"))
                painter.drawRoundedRect(handle, 3, 3)

    def _tick_label_format(self, visible_span: float) -> str:
        if visible_span < 5.0:
            return "T0+{minute:.2f}"
        if visible_span < 30.0:
            return "T0+{minute:.1f}"
        return "T0+{minute:.0f}"

    def _draw_item(
        self,
        painter: QtGui.QPainter,
        item: dict[str, Any],
        left: float,
        top: float,
        row_height: float,
        row_gap: float,
        plot_width: float,
        *,
        item_kind: str,
    ) -> None:
        row_key = self._row_key(item)
        row_index = next((index for index, (key, _label) in enumerate(self._ROWS) if key == row_key), None)
        if row_index is None:
            return
        start = max(0.0, min(self._duration_min, float(item.get("start_min", 0.0))))
        end = max(start, min(self._duration_min, float(item.get("end_min", start))))
        instant = bool(item.get("instant", False))
        visible_start, visible_end = self._visible_range()
        visible_span = max(self._MIN_VIEW_SPAN_MIN, visible_end - visible_start)
        if instant:
            if start < visible_start - 1e-9 or start > visible_end + 1e-9:
                return
        else:
            if end < visible_start - 1e-9 or start > visible_end + 1e-9:
                return
        clipped_start = max(start, visible_start)
        clipped_end = min(end, visible_end)
        x1 = left + ((clipped_start - visible_start) / visible_span) * plot_width
        x2 = left + ((clipped_end - visible_start) / visible_span) * plot_width
        y = top + row_index * (row_height + row_gap)
        width = max(8.0 if instant else 4.0, x2 - x1)
        bar = QtCore.QRectF(x1 - (4.0 if instant else 0.0), y + 4, width, row_height - 8)
        color_key = str(item.get("mode", row_key)) if item_kind == "event" else row_key
        color = self._COLORS.get(color_key, self._COLORS.get(row_key, QtGui.QColor("#8B6F47")))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if instant:
            painter.drawPolygon(
                QtGui.QPolygonF(
                    [
                        QtCore.QPointF(bar.center().x(), y + 1),
                        QtCore.QPointF(bar.right(), y + row_height / 2),
                        QtCore.QPointF(bar.center().x(), y + row_height - 1),
                        QtCore.QPointF(bar.left(), y + row_height / 2),
                    ]
                )
            )
        else:
            painter.drawRoundedRect(bar, 4, 4)
        item_id = str(item.get("id", ""))
        selected = item_id == (self._selected_event_id if item_kind == "event" else self._selected_reference_id)
        if selected:
            painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 2))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(bar.adjusted(-2, -2, 2, 2), 5, 5)
        self._hit_rects.append((bar.adjusted(-4, -4, 4, 4), item_kind, item_id))
        if bar.width() >= 52:
            painter.setPen(QtGui.QColor("#FFFFFF"))
            label = painter.fontMetrics().elidedText(str(item.get("name", item.get("label", ""))), QtCore.Qt.TextElideMode.ElideRight, int(bar.width() - 8))
            painter.drawText(bar.adjusted(4, 0, -4, 0), QtCore.Qt.AlignmentFlag.AlignCenter, label)

    def _row_key(self, item: dict[str, Any]) -> str:
        kind = str(item.get("kind", ""))
        if kind in {"burn", "shadow", "ground", "relay"}:
            return kind
        if kind == DEPLOYMENT_KIND:
            return "deployment"
        return "attitude"

    def _x_to_min(self, x: float) -> float:
        plot_rect = self._plot_rect()
        plot_width = max(1.0, plot_rect.width())
        visible_start, visible_end = self._visible_range()
        visible_span = max(self._MIN_VIEW_SPAN_MIN, visible_end - visible_start)
        minute = visible_start + ((x - plot_rect.left()) / plot_width) * visible_span
        return min(max(0.0, minute), self._duration_min)

    def _plot_rect(self) -> QtCore.QRectF:
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        plot_width = max(1.0, rect.width() - self._PLOT_LEFT - self._PLOT_RIGHT)
        plot_height = max(1.0, rect.height() - self._PLOT_TOP - self._PLOT_BOTTOM)
        return QtCore.QRectF(self._PLOT_LEFT, self._PLOT_TOP, plot_width, plot_height)

    def _visible_range(self) -> tuple[float, float]:
        start = min(max(0.0, self._view_start_min), self._duration_min)
        end = min(max(start + self._MIN_VIEW_SPAN_MIN, self._view_end_min), self._duration_min)
        if end <= start:
            end = min(self._duration_min, start + self._MIN_VIEW_SPAN_MIN)
        return start, end

    def _reset_view_range(self) -> None:
        self._view_start_min = 0.0
        self._view_end_min = self._duration_min
        self._end_pan()

    def _clamp_view_range(self) -> None:
        max_start = max(0.0, self._duration_min - self._MIN_VIEW_SPAN_MIN)
        self._view_start_min = min(max(0.0, self._view_start_min), max_start)
        min_end = self._view_start_min + self._MIN_VIEW_SPAN_MIN
        self._view_end_min = min(self._duration_min, max(min_end, self._view_end_min))

    def _end_pan(self) -> None:
        self._pan_origin_x = None
        self._pan_origin_view_start_min = None
        self._pan_origin_view_end_min = None
        self.unsetCursor()

    def _can_pan(self) -> bool:
        return (self._view_end_min - self._view_start_min) < self._duration_min - 1e-6

    def _zoom_view(self, center_x: float, factor: float) -> bool:
        plot_rect = self._plot_rect()
        if plot_rect.width() <= 1.0:
            return False
        visible_start, visible_end = self._visible_range()
        current_span = max(self._MIN_VIEW_SPAN_MIN, visible_end - visible_start)
        full_span = max(self._MIN_VIEW_SPAN_MIN, self._duration_min)
        target_span = min(full_span, max(self._MIN_VIEW_SPAN_MIN, current_span * float(factor)))
        if abs(target_span - current_span) < 1e-9:
            return False
        ratio = float((center_x - plot_rect.left()) / plot_rect.width())
        ratio = min(max(ratio, 0.0), 1.0)
        center_min = visible_start + current_span * ratio
        candidate_start = center_min - target_span * ratio
        candidate_end = candidate_start + target_span
        if candidate_start < 0.0:
            candidate_start = 0.0
            candidate_end = candidate_start + target_span
        if candidate_end > self._duration_min:
            candidate_end = self._duration_min
            candidate_start = candidate_end - target_span
        self._view_start_min = candidate_start
        self._view_end_min = candidate_end
        return True

    def _indicator_track_rect(self) -> QtCore.QRectF | None:
        if not self._can_pan():
            return None
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        plot_width = max(1.0, rect.width() - self._PLOT_LEFT - self._PLOT_RIGHT)
        track_height = 8.0
        track_top = rect.height() - 14.0
        return QtCore.QRectF(self._PLOT_LEFT, track_top, plot_width, track_height)

    def _indicator_handle_rect(self) -> QtCore.QRectF | None:
        track = self._indicator_track_rect()
        if track is None:
            return None
        full_span = max(self._MIN_VIEW_SPAN_MIN, self._duration_min)
        visible_start, visible_end = self._visible_range()
        visible_span = max(self._MIN_VIEW_SPAN_MIN, visible_end - visible_start)
        handle_left = track.left() + (visible_start / full_span) * track.width()
        handle_width = max(12.0, (visible_span / full_span) * track.width())
        if handle_left + handle_width > track.right():
            handle_left = track.right() - handle_width
        return QtCore.QRectF(handle_left, track.top(), handle_width, track.height())

    def _update_view_from_indicator_x(self, x: float) -> None:
        track = self._indicator_track_rect()
        if track is None or track.width() <= 1.0:
            return
        visible_start, visible_end = self._visible_range()
        visible_span = max(self._MIN_VIEW_SPAN_MIN, visible_end - visible_start)
        full_span = max(self._MIN_VIEW_SPAN_MIN, self._duration_min)
        target_handle_left = x - self._indicator_grab_offset
        target_ratio = (target_handle_left - track.left()) / track.width()
        candidate_start = full_span * target_ratio
        max_start = max(0.0, self._duration_min - visible_span)
        candidate_start = min(max(0.0, candidate_start), max_start)
        self._view_start_min = candidate_start
        self._view_end_min = candidate_start + visible_span
        self.update()

    def _pan_view_from_drag(self, current_x: float) -> None:
        if (
            self._pan_origin_x is None
            or self._pan_origin_view_start_min is None
            or self._pan_origin_view_end_min is None
        ):
            return
        plot_rect = self._plot_rect()
        if plot_rect.width() <= 1.0:
            return
        span = max(self._MIN_VIEW_SPAN_MIN, self._pan_origin_view_end_min - self._pan_origin_view_start_min)
        delta_min = (self._pan_origin_x - current_x) * span / plot_rect.width()
        candidate_start = self._pan_origin_view_start_min + delta_min
        candidate_end = self._pan_origin_view_end_min + delta_min
        if candidate_start < 0.0:
            candidate_end += -candidate_start
            candidate_start = 0.0
        if candidate_end > self._duration_min:
            candidate_start -= candidate_end - self._duration_min
            candidate_end = self._duration_min
        self._view_start_min = candidate_start
        self._view_end_min = candidate_end
        self.update()


class _FlightProgramScrollArea(QtWidgets.QScrollArea):
    """拦截外层滚轮事件，转发给具备 ``_zoom_view`` / ``_plot_rect`` 接口的子控件。

    使用鸭子类型避免硬绑定特定 widget 类，同样适用于其它甘特图控件。
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.viewport() and event.type() == QtCore.QEvent.Type.Wheel:
            wheel_event = event
            if isinstance(wheel_event, QtGui.QWheelEvent):
                if self._forward_wheel_to_zoomable(wheel_event):
                    wheel_event.accept()
                    return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._forward_wheel_to_zoomable(event):
            event.accept()
            return
        super().wheelEvent(event)

    def _forward_wheel_to_zoomable(self, event: QtGui.QWheelEvent) -> bool:
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            return False
        global_pos = event.globalPosition().toPoint()
        widget = QtWidgets.QApplication.widgetAt(global_pos)
        while widget is not None and widget is not self:
            zoom_view = getattr(widget, "_zoom_view", None)
            plot_rect = getattr(widget, "_plot_rect", None)
            if callable(zoom_view) and callable(plot_rect):
                local_point = QtCore.QPointF(widget.mapFromGlobal(global_pos))
                rect = plot_rect()
                if rect.contains(local_point):
                    factor = 0.8 if delta_y > 0 else 1.25
                    if zoom_view(float(local_point.x()), factor):
                        widget.update()
                    return True
                return False
            widget = widget.parentWidget()
        return False


__all__ = ["FlightProgramOverviewWidget", "_FlightProgramScrollArea"]
