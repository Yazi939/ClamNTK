"""Точка входа графической оболочки ClamNTK."""

from __future__ import annotations

import os
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from cameav.theme import STYLESHEET, app_icon, light_palette, load_fonts
from cameav.window import MainWindow


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("ClamNTK")
    app.setOrganizationName("ClamNTK")
    app.setDesktopFileName("cameav")
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    load_fonts()
    font = QFont("Inter", 11)
    font.setWeight(QFont.Normal)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)
    icon = app_icon()
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    paths = [arg for arg in sys.argv[1:] if arg and not arg.startswith("-") and os.path.exists(arg)]
    if paths:
        window.open_path(paths[0])
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
