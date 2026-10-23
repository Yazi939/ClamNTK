"""Окно удаления ClamNTK."""

from __future__ import annotations

import os
import sys

from PyQt5.QtCore import Qt, QThread
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from cameav.runtime import DATABASE_DIR, clamav_present
from installer.engine import (
    cameav_registered,
    command_for,
    files_copied_without_package,
    shell_present,
    system_summary,
    tool_exists,
)
from installer.wizard import DnfWorker, STYLESHEET, button

_CHECK = ""
try:
    from pathlib import Path

    _candidate = Path(__file__).resolve().parents[1] / "cameav" / "assets" / "check.png"
    if _candidate.is_file():
        _CHECK = _candidate.as_posix()
except Exception:
    _CHECK = ""

_CHECK_IMAGE = f' image: url("{_CHECK}");' if _CHECK else ""
UNINSTALL_STYLE = STYLESHEET + f"""
QCheckBox {{ background: transparent; color: #1C1F26; spacing: 8px; font-size: 14px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; }}
QCheckBox::indicator:unchecked {{ background: #FFFFFF; border: 1px solid #B8BEC8; }}
QCheckBox::indicator:checked {{ background: #C8102E; border: 1px solid #C8102E;{_CHECK_IMAGE} }}
"""


class UninstallWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.info = system_summary()
        self.worker_thread: QThread | None = None
        self.worker: DnfWorker | None = None
        self.success = False
        self.setWindowTitle("Удаление ClamNTK")
        self.resize(860, 560)
        self.setMinimumSize(800, 520)
        self._build()

    def _build(self) -> None:
        self.root = QWidget()
        self.root.setObjectName("setupRoot")
        self.setCentralWidget(self.root)
        layout = QHBoxLayout(self.root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.rail = self._rail()
        layout.addWidget(self.rail)
        self.right = QWidget()
        right_layout = QVBoxLayout(self.right)
        right_layout.setContentsMargins(28, 24, 28, 20)
        right_layout.setSpacing(16)
        self.choices = self._choices()
        right_layout.addWidget(self.choices, 1)
        self.progress_box = self._progress()
        self.progress_box.hide()
        right_layout.addWidget(self.progress_box, 1)
        self.button_row = self._buttons()
        right_layout.addLayout(self.button_row)
        layout.addWidget(self.right, 1)
        try:
            from cameav.theme import app_icon

            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self._refresh()

    def _rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(250)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(18, 22, 18, 18)
        brand = QLabel("ClamNTK")
        brand.setObjectName("railTitle")
        sub = QLabel("Удаление из РЕД ОС")
        sub.setObjectName("railSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addStretch(1)
        foot = QLabel(self.info["pretty"])
        foot.setWordWrap(True)
        foot.setObjectName("railSub")
        layout.addWidget(foot)
        return rail

    def _choices(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Удаление ClamNTK")
        title.setObjectName("title")
        lead = QLabel(
            "Оболочка будет снята из меню «Администрирование». "
            "Python, Qt и polkit останутся: ими пользуются другие программы."
        )
        lead.setObjectName("lead")
        lead.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(lead)
        self.summary = QLabel("")
        self.summary.setObjectName("body")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.clamav_box = QCheckBox("Удалить также программу ClamAV и базы сигнатур")
        self.data_box = QCheckBox("Удалить карантин и настройки ClamNTK в домашней папке")
        hint = QLabel("Пароли, закладки и системные файлы не затрагиваются.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(self.clamav_box)
        layout.addWidget(self.data_box)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _progress(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.progress_title = QLabel("Удаление")
        self.progress_title.setObjectName("title")
        self.progress_lead = QLabel("Подтвердите права администратора.")
        self.progress_lead.setObjectName("lead")
        self.progress_lead.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.progress_title)
        layout.addWidget(self.progress_lead)
        layout.addWidget(self.progress)
        layout.addWidget(self.log, 1)
        return page

    def _buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.remove_button = button("Удалить", "danger")
        self.close_button = button("Закрыть", "secondary")
        self.remove_button.clicked.connect(self.start_remove)
        self.close_button.clicked.connect(self.close)
        row.addStretch(1)
        row.addWidget(self.close_button)
        row.addWidget(self.remove_button)
        return row

    def _refresh(self) -> None:
        registered = cameav_registered()
        if registered:
            shell = f"Оболочка установлена пакетом {registered}."
        elif files_copied_without_package() or shell_present():
            shell = "Оболочка скопирована в систему без пакета dnf. Будут удалены её файлы."
        else:
            shell = "Оболочка ClamNTK в системе не найдена."
        if clamav_present() or DATABASE_DIR.is_dir():
            shell += " Программа ClamAV на компьютере есть."
        self.summary.setText(shell)
        can_admin = tool_exists("pkexec") or os.geteuid() == 0
        self.remove_button.setEnabled(shell_present() and can_admin and self.worker_thread is None and not self.success)

    def start_remove(self) -> None:
        if self.worker_thread is not None:
            return
        parts = ["оболочку ClamNTK"]
        if self.clamav_box.isChecked():
            parts.append("программу ClamAV и базы сигнатур")
        if self.data_box.isChecked():
            parts.append("карантин и настройки в домашней папке")
        answer = QMessageBox.question(
            self,
            "Удаление ClamNTK",
            "Удалить " + ", ".join(parts) + "?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            command = command_for(
                "remove",
                remove_clamav=self.clamav_box.isChecked(),
                remove_user_data=self.data_box.isChecked(),
            )
        except RuntimeError as exc:
            self.summary.setText(str(exc))
            return
        self.choices.hide()
        self.progress_box.show()
        self.log.clear()
        self.log.appendPlainText("Команда: " + " ".join(command))
        self.remove_button.setEnabled(False)
        worker = DnfWorker(command)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.line.connect(self.log.appendPlainText)
        worker.finished_code.connect(self._finished)
        worker.finished_code.connect(thread.quit)
        thread.finished.connect(self._thread_finished)
        self.worker = worker
        self.worker_thread = thread
        thread.start()

    def _finished(self, code: int) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if code == 0:
            self.success = True
            self.progress_title.setText("ClamNTK удалён")
            extra = " ClamAV тоже снят." if self.clamav_box.isChecked() else " ClamAV не удалялся."
            self.progress_lead.setText("Оболочка снята с системы." + extra)
            return
        if code in {126, 127}:
            text = "Права администратора не получены."
        else:
            text = f"Удаление завершилось с кодом {code}."
        self.progress_title.setText("Не выполнено")
        self.progress_lead.setText(text)
        self.remove_button.setEnabled(True)

    def _thread_finished(self) -> None:
        self.worker_thread = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.worker_thread is not None:
            QMessageBox.information(self, "ClamNTK", "Дождитесь окончания удаления.")
            event.ignore()
            return
        event.accept()


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Удаление ClamNTK")
    app.setStyle("Fusion")
    app.setFont(QFont("Inter", 10))
    app.setStyleSheet(UNINSTALL_STYLE)
    window = UninstallWindow()
    window.show()
    return app.exec_()
