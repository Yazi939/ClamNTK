"""Оформление ClameNTK."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QEvent, QObject, QRectF, Qt
from PyQt5.QtGui import QColor, QFontDatabase, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap, QRegion
from PyQt5.QtWidgets import QWidget

_CHECK_ICON = (Path(__file__).resolve().parent / "assets" / "check.png").as_posix()


def light_palette() -> QPalette:
    """Светлые уведомления: тёмный текст на светлом фоне, а не чёрный на тёмном."""
    palette = QPalette()
    ink = QColor("#2A0B12")
    palette.setColor(QPalette.Window, QColor("#F4F4F5"))
    palette.setColor(QPalette.WindowText, ink)
    palette.setColor(QPalette.Base, QColor("#FFFFFF"))
    palette.setColor(QPalette.AlternateBase, QColor("#F3F3F5"))
    palette.setColor(QPalette.Text, ink)
    palette.setColor(QPalette.Button, QColor("#FFFFFF"))
    palette.setColor(QPalette.ButtonText, ink)
    palette.setColor(QPalette.ToolTipBase, QColor("#2A0B12"))
    palette.setColor(QPalette.ToolTipText, QColor("#FFFFFF"))
    palette.setColor(QPalette.Highlight, QColor("#FF3B4A"))
    palette.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.PlaceholderText, QColor("#667085"))
    return palette


def load_fonts() -> None:
    """Подключает настоящие начертания Inter.

    В Qt 5 значение font-weight делится на 8, поэтому в таблице стилей
    456 — это Medium, 504 — SemiBold, а 600 уже Bold.
    """
    folder = Path(__file__).resolve().parent / "assets" / "fonts"
    for path in sorted(folder.glob("Inter-*.ttf")):
        QFontDatabase.addApplicationFont(str(path))
STYLESHEET = """
QWidget#root, QWidget#content, QWidget#page, QStackedWidget {
    background: transparent;
    color: #2A0B12;
}
QFrame#sidebar {
    background: rgba(255, 255, 255, 0.58);
    border-right: 1px solid rgba(255, 255, 255, 0.78);
}
QFrame#sidebar QLabel {
    background: transparent;
    color: #2A0B12;
}
QLabel#logoTitle {
    color: #2A0B12;
    font-size: 22px;
    font-weight: 504;
}
QLabel#logoSub, QLabel#sideMeta {
    color: #475467;
    font-size: 12px;
}
QFrame#sideLine {
    background: rgba(42, 11, 18, 0.14);
    border: none;
    max-height: 1px;
}
QLabel#kicker {
    color: #8A5A00;
    font-size: 13px;
    font-weight: 456;
    background: transparent;
}
QLabel#heroTitle {
    color: #2A0B12;
    font-size: 52px;
    font-weight: 504;
    background: transparent;
}
QLabel#heroSub {
    color: #475467;
    font-size: 16px;
    font-weight: 400;
    background: transparent;
}
QLabel#pageTitle {
    font-size: 40px;
    font-weight: 504;
    color: #2A0B12;
    background: transparent;
}
QLabel#pageSub, QLabel#muted {
    color: #475467;
    font-size: 14px;
    font-weight: 400;
    background: transparent;
}
QLabel#pathBar {
    color: #2A0B12;
    font-size: 18px;
    font-weight: 456;
    background: transparent;
}
QLabel#fieldLabel {
    color: #475467;
    font-size: 12px;
    font-weight: 456;
    background: transparent;
}
QFrame#card QLabel#fieldLabel {
    color: #475467;
    font-size: 12px;
    font-weight: 456;
}
QLabel#cardTitle, QLabel#cardHint {
    color: #475467;
    font-size: 12px;
    font-weight: 456;
    background: transparent;
}
QLabel#cardValue {
    font-size: 22px;
    font-weight: 504;
    color: #2A0B12;
    background: transparent;
}
QLabel {
    background: transparent;
    color: #2A0B12;
}
QFrame#card QLabel, QFrame#statusOk QLabel, QFrame#statusWarn QLabel, QFrame#statusBad QLabel, QFrame#statusIdle QLabel {
    color: #2A0B12;
}
QFrame#card QLabel#cardTitle, QFrame#card QLabel#cardHint, QLabel#bannerSub {
    color: #475467;
    font-weight: 456;
}
QFrame#card QLabel#muted {
    color: #475467;
    font-weight: 400;
}
QCheckBox {
    background: transparent;
    color: #2A0B12;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
}
QCheckBox::indicator:unchecked {
    background: #FFFFFF;
    border: 1px solid #B8BEC8;
}
QCheckBox::indicator:checked {
    background: #FF3B4A;
    border: 1px solid #FF3B4A;
    image: url(__CHECK__);
}
QFrame#card, QFrame#statusOk, QFrame#statusWarn, QFrame#statusBad, QFrame#statusIdle {
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.96);
    border-radius: 22px;
}
QFrame#statusOk QLabel, QFrame#statusWarn QLabel, QFrame#statusBad QLabel, QFrame#statusIdle QLabel {
    background: transparent;
}
QLabel#bannerTitle {
    font-size: 26px;
    font-weight: 504;
    color: #2A0B12;
}
QLabel#bannerSub {
    font-size: 14px;
    font-weight: 400;
    color: #475467;
}
QPushButton#nav {
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 14px;
    color: #2A0B12;
    background: transparent;
    font-size: 15px;
    font-weight: 456;
}
QPushButton#nav:hover {
    background: rgba(255, 255, 255, 0.55);
}
QPushButton#nav:checked {
    background: rgba(255, 255, 255, 0.88);
    color: #2A0B12;
    border: 1px solid rgba(255, 59, 74, 0.35);
}
QPushButton#primary {
    background: #FF3B4A;
    color: #FFFFFF;
    border: none;
    border-radius: 20px;
    padding: 8px 18px;
    font-weight: 504;
}
QPushButton#primary:hover { background: #E11D38; }
QPushButton#primary:disabled { background: #F3B4BA; color: #FFFFFF; }
QPushButton#secondary {
    background: #FFFFFF;
    color: #2A0B12;
    border: 1px solid rgba(42, 11, 18, 0.14);
    border-radius: 20px;
    padding: 8px 18px;
    font-weight: 456;
}
QPushButton#secondary:hover { background: rgba(255, 255, 255, 0.92); }
QPushButton#secondary:disabled { color: #98A2B3; background: rgba(255, 255, 255, 0.45); }
QPushButton#danger {
    background: rgba(255, 255, 255, 0.72);
    color: #C4172E;
    border: 1px solid rgba(255, 59, 74, 0.35);
    border-radius: 20px;
    padding: 8px 18px;
    font-weight: 504;
}
QPushButton#danger:hover { background: rgba(255, 255, 255, 0.92); }
QPushButton#danger:disabled { color: #E7B3BA; border-color: rgba(255, 255, 255, 0.6); }
QPushButton#chip {
    background: #F3F4F6;
    border: 1px solid rgba(42, 11, 18, 0.12);
    border-radius: 18px;
    padding: 6px 14px;
    color: #2A0B12;
    font-weight: 456;
}
QPushButton#chip:hover { background: #FFFFFF; }
QPushButton#chip:pressed { background: #E4E4E7; }
QPushButton#chip:checked {
    background: #2A0B12;
    color: #FFFFFF;
    border-color: #2A0B12;
}
QPushButton#chip:checked:hover { background: #3D1520; }
QLineEdit, QPlainTextEdit, QListWidget {
    background: #FFFFFF;
    color: #2A0B12;
    border: 1px solid rgba(42, 11, 18, 0.14);
    border-radius: 14px;
    padding: 8px 12px;
    selection-background-color: rgba(255, 59, 74, 0.22);
    selection-color: #2A0B12;
}
QPlainTextEdit {
    font-family: "Noto Sans Mono", "DejaVu Sans Mono";
    font-size: 12px;
}
QSpinBox {
    background: #FFFFFF;
    color: #2A0B12;
    border: 1px solid rgba(42, 11, 18, 0.16);
    border-radius: 12px;
    padding: 4px 12px;
    min-height: 36px;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 0;
    height: 0;
    border: none;
    background: transparent;
}
QProgressBar {
    border: none;
    background: rgba(255, 255, 255, 0.45);
    border-radius: 5px;
    min-height: 10px;
    max-height: 10px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #FF3B4A;
    border-radius: 5px;
}
QTableWidget {
    background: rgba(255, 255, 255, 0.94);
    alternate-background-color: rgba(243, 243, 245, 0.94);
    color: #2A0B12;
    border: none;
    border-radius: 18px;
    gridline-color: transparent;
    selection-background-color: rgba(255, 59, 74, 0.16);
    selection-color: #2A0B12;
}
QHeaderView {
    background: transparent;
    border: none;
}
QHeaderView::section {
    background: #E6E7EB;
    color: #475467;
    border: none;
    padding: 10px 16px;
    font-weight: 456;
}
QHeaderView::section:first {
    border-top-left-radius: 18px;
}
QHeaderView::section:last {
    border-top-right-radius: 18px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px 2px;
}
QScrollBar::handle:vertical {
    background: #D0D5DD;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QToolTip {
    background: rgba(42, 11, 18, 0.92);
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 6px 8px;
}
QMessageBox {
    background: #F4F4F5;
    color: #2A0B12;
}
QMessageBox QLabel {
    color: #2A0B12;
    background: transparent;
    font-weight: 400;
}
QMessageBox QPushButton {
    background: #FFFFFF;
    color: #2A0B12;
    border: 1px solid rgba(42, 11, 18, 0.16);
    border-radius: 14px;
    padding: 6px 16px;
    min-width: 96px;
    min-height: 34px;
    font-weight: 456;
}
""".replace("__CHECK__", _CHECK_ICON)


class _RoundMask(QObject):
    def __init__(self, radius: int) -> None:
        super().__init__()
        self.radius = radius

    def eventFilter(self, watched, event):  # type: ignore[override]
        if event.type() == QEvent.Resize and watched.width() > 0 and watched.height() > 0:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, watched.width(), watched.height()), self.radius, self.radius)
            watched.setMask(QRegion(path.toFillPolygon().toPolygon()))
        return False


def clip_rounded(widget, radius: int = 18) -> None:
    mask = _RoundMask(radius)
    mask.setParent(widget)
    widget.installEventFilter(mask)


class Backdrop(QWidget):
    """Серый фон и цифровая красная спираль у правого края."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setAttribute(Qt.WA_StyledBackground, False)
        image = Path(__file__).resolve().parent / "assets" / "spiral.jpg"
        self._pix = QPixmap(str(image))

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#E4E4E7"))
        if not self._pix.isNull():
            scaled = self._pix.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(self.width() - scaled.width(), (self.height() - scaled.height()) // 2, scaled)
            fade = QLinearGradient(self.width() * 0.42, 0, self.width() * 0.78, 0)
            fade.setColorAt(0.0, QColor("#E4E4E7"))
            fade.setColorAt(0.62, QColor(228, 228, 231, 210))
            fade.setColorAt(1.0, QColor(228, 228, 231, 0))
            painter.fillRect(self.rect(), fade)
        painter.end()


def lift_text(widget) -> None:
    return


def make_icon(kind: str, color: str = "#2A0B12") -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    if kind == "overview":
        painter.drawRoundedRect(6, 6, 8, 8, 2, 2)
        painter.drawRoundedRect(18, 6, 8, 8, 2, 2)
        painter.drawRoundedRect(6, 18, 8, 8, 2, 2)
        painter.drawRoundedRect(18, 18, 8, 8, 2, 2)
    elif kind == "scan":
        painter.drawEllipse(6, 6, 14, 14)
        painter.drawLine(18, 18, 26, 26)
    elif kind == "quarantine":
        painter.drawRoundedRect(8, 12, 16, 12, 2, 2)
        painter.drawLine(8, 16, 24, 16)
        painter.drawArc(12, 6, 8, 8, 0, 180 * 16)
    elif kind == "history":
        painter.drawEllipse(6, 6, 20, 20)
        painter.drawLine(16, 10, 16, 17)
        painter.drawLine(16, 17, 21, 20)
    elif kind == "updates":
        painter.drawArc(8, 8, 16, 16, 40 * 16, 250 * 16)
        painter.drawLine(22, 8, 22, 13)
        painter.drawLine(22, 8, 17, 8)
    elif kind == "clean":
        painter.drawLine(8, 12, 24, 12)
        painter.drawRoundedRect(10, 12, 12, 12, 2, 2)
        painter.drawLine(13, 8, 19, 8)
        painter.drawLine(14, 16, 14, 21)
        painter.drawLine(16, 16, 16, 21)
        painter.drawLine(18, 16, 18, 21)
    else:
        painter.drawLine(8, 10, 24, 10)
        painter.drawLine(8, 16, 24, 16)
        painter.drawLine(8, 22, 24, 22)
        painter.setBrush(QColor(color))
        painter.drawEllipse(15, 7, 6, 6)
        painter.drawEllipse(8, 13, 6, 6)
        painter.drawEllipse(17, 19, 6, 6)
    painter.end()
    return QIcon(pixmap)


def app_icon() -> QIcon:
    svg = Path(__file__).resolve().parent / "assets" / "cameav.svg"
    icon = QIcon()
    if not svg.is_file():
        return icon
    try:
        from PyQt5.QtSvg import QSvgRenderer
    except Exception:
        return icon
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        return icon
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
