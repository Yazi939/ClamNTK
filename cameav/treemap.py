"""Прямоугольная карта места на диске."""

from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QWidget

COLORS = (
    "#E11D38",
    "#FFBB02",
    "#7A2433",
    "#475467",
    "#FF8A94",
    "#2A0B12",
    "#C45C4A",
    "#8C3A4A",
    "#D4A24E",
    "#6E2430",
)


def squarify(values: list[float], x: float, y: float, width: float, height: float) -> list[tuple[int, float, float, float, float]]:
    ranked = [(float(value), index) for index, value in enumerate(values) if value > 0]
    ranked.sort(reverse=True)
    total = sum(value for value, _index in ranked)
    if total <= 0 or width <= 1 or height <= 1:
        return []
    area = width * height
    scaled = [(value / total * area, index) for value, index in ranked]
    rects: list[tuple[int, float, float, float, float]] = []
    _layout(scaled, x, y, width, height, rects)
    return rects


def _worst(row: list[tuple[float, int]], side: float) -> float:
    if not row or side <= 0:
        return float("inf")
    total = sum(value for value, _index in row)
    if total <= 0:
        return float("inf")
    side2 = side * side
    total2 = total * total
    score = 0.0
    for value, _index in row:
        if value <= 0:
            continue
        score = max(score, side2 * value / total2, total2 / (side2 * value))
    return score


def _layout(
    items: list[tuple[float, int]],
    x: float,
    y: float,
    width: float,
    height: float,
    rects: list[tuple[int, float, float, float, float]],
) -> None:
    if not items or width <= 1 or height <= 1:
        return
    if len(items) == 1:
        rects.append((items[0][1], x, y, width, height))
        return
    short = min(width, height)
    row = [items[0]]
    rest = items[1:]
    while rest and _worst(row + [rest[0]], short) <= _worst(row, short):
        row.append(rest.pop(0))
    row_area = sum(value for value, _index in row)
    if width >= height:
        used = row_area / height if height else 0
        _place_row(row, x, y, used, height, True, rects)
        _layout(rest, x + used, y, max(0, width - used), height, rects)
        return
    used = row_area / width if width else 0
    _place_row(row, x, y, width, used, False, rects)
    _layout(rest, x, y + used, width, max(0, height - used), rects)


def _place_row(
    row: list[tuple[float, int]],
    x: float,
    y: float,
    width: float,
    height: float,
    vertical: bool,
    rects: list[tuple[int, float, float, float, float]],
) -> None:
    if width <= 0 or height <= 0:
        return
    if vertical:
        offset = y
        for value, index in row:
            block = value / width if width else 0
            rects.append((index, x, offset, width, block))
            offset += block
        return
    offset = x
    for value, index in row:
        block = value / height if height else 0
        rects.append((index, offset, y, block, height))
        offset += block


class TreeMap(QWidget):
    activated = pyqtSignal(int)
    chosen = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self._entries: list[dict] = []
        self._blocks: list[tuple[int, QRectF]] = []
        self._hover = -1
        self._selected = -1
        self.setAutoFillBackground(False)

    def set_entries(self, entries: list[dict]) -> None:
        self._entries = entries
        self._hover = -1
        self._selected = -1
        self._blocks = []
        self.update()

    def set_selected(self, index: int) -> None:
        self._selected = index
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        self._blocks = []
        if not self._entries:
            painter.setPen(QColor("#667085"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Здесь появится карта папок")
            painter.end()
            return
        gap = 3.0
        rects = squarify(
            [float(entry["size"]) for entry in self._entries],
            8,
            8,
            max(1, self.width() - 16),
            max(1, self.height() - 16),
        )
        metrics = QFontMetrics(self.font())
        for index, left, top, width, height in rects:
            box = QRectF(left + gap / 2, top + gap / 2, max(0, width - gap), max(0, height - gap))
            if box.width() < 2 or box.height() < 2:
                continue
            self._blocks.append((index, box))
            color = QColor(COLORS[index % len(COLORS)])
            if index == self._hover:
                color = color.lighter(115)
            painter.setBrush(color)
            if index == self._selected:
                painter.setPen(QPen(QColor("#FFFFFF"), 2))
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(box, 6, 6)
            if box.width() > 64 and box.height() > 28:
                painter.setPen(QColor("#FFFFFF"))
                name = metrics.elidedText(str(self._entries[index]["name"]), Qt.ElideRight, int(box.width()) - 12)
                painter.drawText(box.adjusted(6, 4, -6, -4), Qt.AlignLeft | Qt.AlignTop, name)
        painter.end()

    def _index_at(self, x: float, y: float) -> int:
        for index, box in self._blocks:
            if box.contains(x, y):
                return index
        return -1

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        index = self._index_at(event.x(), event.y())
        if index != self._hover:
            self._hover = index
            if index >= 0:
                entry = self._entries[index]
                self.setToolTip(f"{entry['name']} — {entry.get('size_text', '')}")
            else:
                self.setToolTip("")
            self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        index = self._index_at(event.x(), event.y())
        self._selected = index
        self.update()
        if index >= 0:
            self.chosen.emit(index)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        index = self._index_at(event.x(), event.y())
        if index >= 0:
            self.activated.emit(index)
