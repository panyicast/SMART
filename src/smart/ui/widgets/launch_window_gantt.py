"""发射窗口分析的甘特图控件。

从 ``launch_window_page.py`` 拆出，包含：

- ``_GanttSegment`` —— 单条窗口/通过段的不可变描述。
- ``LaunchWindowGanttWidget`` —— 自绘甘特图，支持滚轮缩放、左键拖动平移、
  双击重置；会把每个候选样本展开为窗口行 + 各约束行，并用红 / 绿区分窗口 /
  通过段。
- ``_GanttScrollArea`` —— 拦截外层 ``QScrollArea`` 的滚轮事件，把它转发到
  内部图表，避免被滚动条吞掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.earth_orientation import parse_utc

BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class _GanttSegment:
    row_label: str
    start_utc: Any
    end_utc: Any
    status: str
    tooltip: str


class LaunchWindowGanttWidget(QtWidgets.QWidget):
    _WINDOW_ROW_LABEL = "发射窗口计算结果"
    _WINDOW_COLOR = QtGui.QColor("#D3222A")
    _PASS_ROW_LABEL = "测控条件通过"
    _PASS_COLOR = QtGui.QColor("#2FC18B")
    _BACKGROUND = QtGui.QColor("#071016")
    _PANEL = QtGui.QColor("#0B1A22")
    _ROW_ALT = QtGui.QColor("#0F2530")
    _BORDER = QtGui.QColor("#1E3B49")
    _GRID = QtGui.QColor("#244958")
    _TEXT = QtGui.QColor("#D8E7EF")
    _MUTED = QtGui.QColor("#8FA8B4")
    _MIN_VIEW_SPAN_SECONDS = 60.0

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[_GanttSegment] = []
        self._row_labels: list[str] = []
        self._start_utc: Any | None = None
        self._end_utc: Any | None = None
        self._view_start_utc: Any | None = None
        self._view_end_utc: Any | None = None
        self._segment_rects: list[tuple[QtCore.QRectF, _GanttSegment]] = []
        self._drag_origin_x: float | None = None
        self._drag_view_start_utc: Any | None = None
        self._drag_view_end_utc: Any | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.WheelFocus)
        self.setMinimumHeight(220)
        self.setMinimumWidth(980)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.MinimumExpanding)

    def clear(self) -> None:
        self._segments = []
        self._row_labels = []
        self._start_utc = None
        self._end_utc = None
        self._view_start_utc = None
        self._view_end_utc = None
        self._segment_rects = []
        self.setMinimumHeight(220)
        self.updateGeometry()
        self.update()

    def set_samples(self, samples: list[dict[str, Any]]) -> None:
        self._segments, self._row_labels, self._start_utc, self._end_utc = self._build_segments(samples)
        if self._start_utc is None or self._end_utc is None:
            self._start_utc = None
            self._end_utc = None
        self._reset_view_range()
        self.setMinimumHeight(max(220, 92 + len(self._row_labels) * 38))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(980, max(220, 92 + len(self._row_labels) * 38))

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_origin_x is not None:
            self._pan_view_from_drag(event.position().x())
            event.accept()
            return
        point = event.position()
        for rect, segment in self._segment_rects:
            if rect.contains(point):
                self.setToolTip(segment.tooltip)
                return
        self.setToolTip("")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and self._can_pan()
            and self._plot_rect().contains(event.position())
        ):
            self._drag_origin_x = event.position().x()
            self._drag_view_start_utc, self._drag_view_end_utc = self._visible_range()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._drag_origin_x is not None:
            self._drag_origin_x = None
            self._drag_view_start_utc = None
            self._drag_view_end_utc = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._plot_rect().contains(event.position()):
            self._reset_view_range()
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._plot_rect().contains(event.position()) and event.angleDelta().y():
            factor = 0.8 if event.angleDelta().y() > 0 else 1.25
            if self._zoom_view(event.position().x(), factor):
                self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, self._BACKGROUND)
        painter.setBrush(self._PANEL)
        painter.setPen(QtGui.QPen(self._BORDER, 1))
        painter.drawRoundedRect(rect, 10, 10)

        if self._start_utc is None or self._end_utc is None:
            painter.setPen(self._MUTED)
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "暂无发射窗口计算结果")
            return

        left = min(300.0, max(168.0, rect.width() * 0.24))
        right = 18.0
        top = 44.0
        bottom = 34.0
        plot_width = max(1.0, rect.width() - left - right)
        row_height = 28.0
        row_gap = 10.0
        visible_start, visible_end = self._visible_range()
        span_seconds = max(self._MIN_VIEW_SPAN_SECONDS, (visible_end - visible_start).total_seconds())
        axis_y = top - 16.0
        self._segment_rects = []

        painter.setPen(QtGui.QPen(self._GRID, 1))
        painter.drawLine(QtCore.QPointF(left, axis_y), QtCore.QPointF(left + plot_width, axis_y))
        tick_count = 5
        for index in range(tick_count + 1):
            ratio = index / tick_count
            x = left + plot_width * ratio
            tick_utc = visible_start + timedelta(seconds=span_seconds * ratio)
            painter.drawLine(QtCore.QPointF(x, axis_y - 4), QtCore.QPointF(x, axis_y + 4))
            label = tick_utc.astimezone(BEIJING_TZ).strftime("%m-%d %H:%M")
            painter.setPen(self._TEXT)
            painter.drawText(
                QtCore.QRectF(x - 42, 8, 84, 18),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                label,
            )
            painter.setPen(QtGui.QPen(self._GRID, 1))

        for row_index, row_label in enumerate(self._row_labels):
            row_top = top + row_index * (row_height + row_gap)
            row_rect = QtCore.QRectF(left, row_top, plot_width, row_height)
            if row_index % 2:
                painter.fillRect(
                    QtCore.QRectF(1, row_top - 4, rect.width() - 2, row_height + 8),
                    self._ROW_ALT,
                )
            painter.setPen(self._TEXT)
            label_text = painter.fontMetrics().elidedText(
                row_label,
                QtCore.Qt.TextElideMode.ElideRight,
                int(left - 18),
            )
            painter.drawText(
                QtCore.QRectF(10, row_top, left - 20, row_height),
                QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight,
                label_text,
            )
            painter.setPen(QtGui.QPen(self._GRID, 1))
            painter.drawLine(
                QtCore.QPointF(left, row_rect.center().y()),
                QtCore.QPointF(left + plot_width, row_rect.center().y()),
            )

        for segment in self._segments:
            row_index = self._row_labels.index(segment.row_label)
            row_top = top + row_index * (row_height + row_gap)
            if segment.end_utc <= visible_start or segment.start_utc >= visible_end:
                continue
            clipped_start = max(segment.start_utc, visible_start)
            clipped_end = min(segment.end_utc, visible_end)
            x1 = left + ((clipped_start - visible_start).total_seconds() / span_seconds) * plot_width
            x2 = left + ((clipped_end - visible_start).total_seconds() / span_seconds) * plot_width
            bar_rect = QtCore.QRectF(x1, row_top + 4, max(3.0, x2 - x1), row_height - 8)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(self._segment_color(segment))
            painter.drawRoundedRect(bar_rect, 4, 4)
            self._segment_rects.append((bar_rect, segment))
            if bar_rect.width() >= 42:
                painter.setPen(QtGui.QColor("#FFFFFF"))
                minutes = max(0.0, (segment.end_utc - segment.start_utc).total_seconds() / 60.0)
                text = f"{minutes:.0f} min"
                painter.drawText(
                    bar_rect.adjusted(5, 0, -5, 0),
                    QtCore.Qt.AlignmentFlag.AlignCenter,
                    text,
                )

        painter.setPen(self._MUTED)
        painter.drawText(
            QtCore.QRectF(left, rect.height() - bottom + 4, plot_width, 20),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            "北京时间",
        )

    def _segment_color(self, segment: _GanttSegment) -> QtGui.QColor:
        return self._WINDOW_COLOR if segment.status == "window" else self._PASS_COLOR

    def _build_segments(
        self,
        samples: list[dict[str, Any]],
    ) -> tuple[list[_GanttSegment], list[str], Any | None, Any | None]:
        if not samples:
            return [], [], None, None
        parsed_samples: list[tuple[Any, dict[str, Any]]] = []
        for sample in samples:
            try:
                parsed_samples.append((parse_utc(str(sample["launch_utc"])), sample))
            except Exception:
                continue
        if not parsed_samples:
            return [], [], None, None

        default_step = self._infer_sample_step(parsed_samples)
        timeline_start = parsed_samples[0][0]
        timeline_end = parsed_samples[-1][0] + default_step
        row_labels: list[str] = [self._WINDOW_ROW_LABEL]
        segments_by_label: dict[str, list[_GanttSegment]] = {self._WINDOW_ROW_LABEL: []}
        for index, (start_utc, sample) in enumerate(parsed_samples):
            next_start = parsed_samples[index + 1][0] if index + 1 < len(parsed_samples) else start_utc + default_step
            end_utc = next_start if next_start > start_utc else start_utc + default_step
            if bool(sample.get("ok")):
                self._append_pass_segment(
                    segments_by_label[self._WINDOW_ROW_LABEL],
                    row_label=self._WINDOW_ROW_LABEL,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    status="window",
                )
            for result in self._sample_constraint_results(sample):
                row_label = str(result.get("name") or self._PASS_ROW_LABEL)
                if row_label not in segments_by_label:
                    row_labels.append(row_label)
                    segments_by_label[row_label] = []
                if not bool(result.get("passed")):
                    continue
                self._append_pass_segment(
                    segments_by_label[row_label],
                    row_label=row_label,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    status="pass",
                )
        segments = [segment for label in row_labels for segment in segments_by_label[label]]
        return segments, row_labels, timeline_start, timeline_end

    def _append_pass_segment(
        self,
        row_segments: list[_GanttSegment],
        *,
        row_label: str,
        start_utc: Any,
        end_utc: Any,
        status: str,
    ) -> None:
        if row_segments and abs((start_utc - row_segments[-1].end_utc).total_seconds()) < 1e-6:
            previous = row_segments[-1]
            row_segments[-1] = _GanttSegment(
                row_label=previous.row_label,
                start_utc=previous.start_utc,
                end_utc=end_utc,
                status=previous.status,
                tooltip=self._segment_tooltip(row_label, previous.start_utc, end_utc),
            )
            return
        row_segments.append(
            _GanttSegment(
                row_label=row_label,
                start_utc=start_utc,
                end_utc=end_utc,
                status=status,
                tooltip=self._segment_tooltip(row_label, start_utc, end_utc),
            )
        )

    @classmethod
    def _sample_constraint_results(cls, sample: dict[str, Any]) -> list[dict[str, Any]]:
        raw_results = sample.get("constraint_results")
        if isinstance(raw_results, list):
            results = [
                result
                for result in raw_results
                if isinstance(result, dict) and bool(result.get("enabled", True))
            ]
            if results:
                return results
        return [
            {
                "name": cls._PASS_ROW_LABEL,
                "passed": bool(sample.get("ok")),
                "enabled": True,
            }
        ]

    @staticmethod
    def _infer_sample_step(parsed_samples: list[tuple[Any, dict[str, Any]]]) -> timedelta:
        for index in range(1, len(parsed_samples)):
            delta = parsed_samples[index][0] - parsed_samples[index - 1][0]
            if delta.total_seconds() > 0:
                return delta
        return timedelta(minutes=10)

    @staticmethod
    def _segment_tooltip(row_label: str, start_utc: Any, end_utc: Any) -> str:
        start_text = start_utc.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        end_text = end_utc.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        minutes = max(0.0, (end_utc - start_utc).total_seconds() / 60.0)
        return f"{row_label}\n{start_text} - {end_text}\n通过，{minutes:.1f} min"

    def _reset_view_range(self) -> None:
        self._view_start_utc = self._start_utc
        self._view_end_utc = self._end_utc
        self._drag_origin_x = None
        self._drag_view_start_utc = None
        self._drag_view_end_utc = None
        self.unsetCursor()

    def _visible_range(self) -> tuple[Any | None, Any | None]:
        return self._view_start_utc or self._start_utc, self._view_end_utc or self._end_utc

    def _full_span_seconds(self) -> float:
        if self._start_utc is None or self._end_utc is None:
            return self._MIN_VIEW_SPAN_SECONDS
        return max(self._MIN_VIEW_SPAN_SECONDS, (self._end_utc - self._start_utc).total_seconds())

    def _plot_rect(self) -> QtCore.QRectF:
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        left = min(300.0, max(168.0, rect.width() * 0.24))
        right = 18.0
        top = 44.0
        bottom = 34.0
        return QtCore.QRectF(left, top, max(1.0, rect.width() - left - right), max(1.0, rect.height() - top - bottom))

    def _can_pan(self) -> bool:
        visible_start, visible_end = self._visible_range()
        if visible_start is None or visible_end is None or self._start_utc is None or self._end_utc is None:
            return False
        return (visible_end - visible_start).total_seconds() < (self._end_utc - self._start_utc).total_seconds() - 1e-6

    def _zoom_view(self, center_x: float, factor: float) -> bool:
        visible_start, visible_end = self._visible_range()
        if visible_start is None or visible_end is None or self._start_utc is None or self._end_utc is None:
            return False
        plot_rect = self._plot_rect()
        if plot_rect.width() <= 1.0:
            return False
        ratio = float((center_x - plot_rect.left()) / plot_rect.width())
        ratio = min(max(ratio, 0.0), 1.0)
        current_span = max(self._MIN_VIEW_SPAN_SECONDS, (visible_end - visible_start).total_seconds())
        target_span = min(self._full_span_seconds(), max(self._MIN_VIEW_SPAN_SECONDS, current_span * float(factor)))
        if abs(target_span - current_span) < 1e-6:
            return False
        center_utc = visible_start + timedelta(seconds=current_span * ratio)
        candidate_start = center_utc - timedelta(seconds=target_span * ratio)
        candidate_end = candidate_start + timedelta(seconds=target_span)
        if candidate_start < self._start_utc:
            candidate_start = self._start_utc
            candidate_end = candidate_start + timedelta(seconds=target_span)
        if candidate_end > self._end_utc:
            candidate_end = self._end_utc
            candidate_start = candidate_end - timedelta(seconds=target_span)
        self._view_start_utc = candidate_start
        self._view_end_utc = candidate_end
        return True

    def _pan_view_from_drag(self, current_x: float) -> None:
        if (
            self._drag_origin_x is None
            or self._drag_view_start_utc is None
            or self._drag_view_end_utc is None
            or self._start_utc is None
            or self._end_utc is None
        ):
            return
        plot_rect = self._plot_rect()
        if plot_rect.width() <= 1.0:
            return
        span_seconds = max(self._MIN_VIEW_SPAN_SECONDS, (self._drag_view_end_utc - self._drag_view_start_utc).total_seconds())
        delta_seconds = (self._drag_origin_x - current_x) * span_seconds / plot_rect.width()
        candidate_start = self._drag_view_start_utc + timedelta(seconds=delta_seconds)
        candidate_end = self._drag_view_end_utc + timedelta(seconds=delta_seconds)
        if candidate_start < self._start_utc:
            candidate_end += self._start_utc - candidate_start
            candidate_start = self._start_utc
        if candidate_end > self._end_utc:
            candidate_start -= candidate_end - self._end_utc
            candidate_end = self._end_utc
        self._view_start_utc = candidate_start
        self._view_end_utc = candidate_end
        self.update()


class _GanttScrollArea(QtWidgets.QScrollArea):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewport().installEventFilter(self)
        self._chart_widget: QtWidgets.QWidget | None = None

    def setWidget(self, widget: QtWidgets.QWidget) -> None:
        if self._chart_widget is not None:
            self._chart_widget.removeEventFilter(self)
        super().setWidget(widget)
        self._chart_widget = widget
        if self._chart_widget is not None:
            self._chart_widget.installEventFilter(self)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched in {self.viewport(), self._chart_widget} and event.type() == QtCore.QEvent.Type.Wheel:
            wheel_event = event
            if isinstance(wheel_event, QtGui.QWheelEvent):
                if watched is self.viewport():
                    accepted = self._forward_wheel_to_chart_viewport_pos(
                        wheel_event.position(),
                        wheel_event.angleDelta().y(),
                    )
                else:
                    accepted = self._forward_wheel_to_chart_x(
                        wheel_event.position().x(),
                        wheel_event.angleDelta().y(),
                    )
                if accepted:
                    wheel_event.accept()
                    return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        chart_pos = self.widget().mapFromGlobal(event.globalPosition().toPoint()) if self.widget() is not None else None
        if chart_pos is not None and self._forward_wheel_to_chart_x(float(chart_pos.x()), event.angleDelta().y()):
            event.accept()
            return
        super().wheelEvent(event)

    def _forward_wheel_to_chart_viewport_pos(self, viewport_pos: QtCore.QPointF, delta_y: int) -> bool:
        chart = self.widget()
        if chart is None or delta_y == 0:
            return False
        chart_pos = chart.mapFrom(self.viewport(), viewport_pos.toPoint())
        return self._forward_wheel_to_chart_x(float(chart_pos.x()), delta_y)

    def _forward_wheel_to_chart_x(self, chart_x: float, delta_y: int) -> bool:
        chart = self.widget()
        if chart is None or delta_y == 0:
            return False
        plot_rect = getattr(chart, "_plot_rect", None)
        zoom_view = getattr(chart, "_zoom_view", None)
        if not callable(plot_rect) or not callable(zoom_view):
            return False
        chart_point = QtCore.QPointF(chart_x, plot_rect().center().y())
        if not plot_rect().contains(chart_point):
            return False
        factor = 0.8 if delta_y > 0 else 1.25
        zoom_view(chart_x, factor)
        chart.update()
        return True


__all__ = ["LaunchWindowGanttWidget", "_GanttScrollArea", "_GanttSegment"]
