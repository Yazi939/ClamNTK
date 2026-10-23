"""Окно мастера установки ClamNTK."""

from __future__ import annotations

import os
import subprocess

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from installer.engine import (
    cameav_registered,
    command_for,
    files_copied_without_package,
    find_rpm,
    folder_plan,
    clamav_present,
    installed_nevra,
    package_plan,
    query_rpm_file,
    redos_ok,
    source_tree_ready,
    system_summary,
    tool_exists,
)

STYLESHEET = """
QWidget#setupRoot { background: #F3F4F6; color: #1C1F26; }
QLabel { background: transparent; color: #1C1F26; }
QFrame#rail { background: #171A21; }
QFrame#rail QLabel { background: transparent; color: #C9CDD6; }
QFrame#rail QLabel#railTitle { color: #FFFFFF; font-size: 22px; font-weight: 700; }
QFrame#rail QLabel#railSub { color: #9AA3B2; font-size: 12px; }
QFrame#rail QLabel#stepOn {
    color: #FFFFFF;
    background: #C8102E;
    border-radius: 10px;
    padding: 9px 12px;
    font-weight: 700;
}
QFrame#rail QLabel#stepOff {
    color: #9AA3B2;
    background: transparent;
    padding: 9px 12px;
}
QLabel#title { font-size: 26px; font-weight: 700; color: #16181D; background: transparent; }
QLabel#lead, QLabel#muted { color: #667085; font-size: 13px; background: transparent; }
QLabel#body { color: #1C1F26; font-size: 14px; background: transparent; }
QFrame#card { background: #FFFFFF; border: 1px solid #E6E8EE; border-radius: 16px; }
QFrame#ok { background: #E8F7EE; border: 1px solid #B7E4C7; border-radius: 14px; }
QFrame#bad { background: #FDECEE; border: 1px solid #F3C1C7; border-radius: 14px; }
QFrame#warn { background: #FFF6E6; border: 1px solid #F3D7A1; border-radius: 14px; }
QPushButton#primary {
    background: #C8102E; color: white; border: none; border-radius: 10px;
    padding: 8px 18px; font-weight: 700; min-height: 38px;
}
QPushButton#primary:hover { background: #A50D26; }
QPushButton#primary:disabled { background: #E7B3BA; }
QPushButton#secondary, QPushButton#danger {
    background: white; border-radius: 10px; padding: 8px 16px; font-weight: 700; min-height: 38px;
}
QPushButton#secondary { color: #1C1F26; border: 1px solid #D8DCE3; }
QPushButton#secondary:hover { background: #F7F8FA; }
QPushButton#danger { color: #C8102E; border: 1px solid #F0C2C8; }
QPushButton#danger:hover { background: #FFF5F6; }
QPushButton#danger:disabled, QPushButton#secondary:disabled { color: #98A2B3; }
QTableWidget {
    background: white; alternate-background-color: #FAFBFC;
    border: 1px solid #E6E8EE; border-radius: 12px; gridline-color: transparent;
    selection-background-color: #FDECEE; selection-color: #1C1F26;
}
QHeaderView::section {
    background: #FAFBFC; color: #667085; border: none;
    border-bottom: 1px solid #E6E8EE; padding: 8px 10px; font-weight: 700;
}
QPlainTextEdit {
    background: #171A21; color: #E8EAF0; border: none; border-radius: 12px;
    padding: 12px; font-family: "Noto Sans Mono", "DejaVu Sans Mono"; font-size: 12px;
}
QProgressBar {
    border: none; background: #E6E8EE; border-radius: 5px;
    min-height: 10px; max-height: 10px; color: transparent;
}
QProgressBar::chunk { background: #C8102E; border-radius: 5px; }
"""

STEPS = ("Приветствие", "Система", "Пакеты", "Установка")


class DnfWorker(QObject):
    line = pyqtSignal(str)
    finished_code = pyqtSignal(int)

    def __init__(self, command: list[str]) -> None:
        super().__init__()
        self.command = command

    def run(self) -> None:
        try:
            process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.line.emit(str(exc))
            self.finished_code.emit(1)
            return
        assert process.stdout is not None
        for raw in process.stdout:
            self.line.emit(raw.rstrip("\n"))
        self.finished_code.emit(process.wait())


def button(text: str, kind: str) -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(kind)
    widget.setCursor(Qt.PointingHandCursor)
    return widget


def banner(level: str, text: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName({"ok": "ok", "bad": "bad", "warn": "warn"}[level])
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 12)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("body")
    layout.addWidget(label)
    return frame


class SetupWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.info = system_summary()
        self.supported, self.support_text = redos_ok(self.info)
        self.rpm_path = find_rpm()
        self.rpm_info = None
        self.plan: list[dict[str, str]] = []
        self.worker_thread: QThread | None = None
        self.action = ""
        self.success = False
        if self.rpm_path is not None:
            try:
                self.rpm_info = query_rpm_file(self.rpm_path)
                self.plan = package_plan(self.rpm_path)
            except (OSError, subprocess.SubprocessError) as exc:
                self.rpm_info = None
                self.rpm_path = None
                if source_tree_ready():
                    self.plan = folder_plan()
                    self.rpm_error = ""
                else:
                    self.plan = []
                    self.rpm_error = str(exc)
            else:
                self.rpm_error = ""
        elif source_tree_ready():
            self.plan = folder_plan()
            self.rpm_error = ""
        else:
            self.rpm_error = "Рядом с установщиком нет файлов оболочки ClamNTK."
        self.setWindowTitle("Установка ClamNTK")
        self.resize(920, 600)
        self.setMinimumSize(860, 560)
        self._build()
        self.show_step(0)

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("setupRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._rail())
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(28, 24, 28, 20)
        right_layout.setSpacing(16)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome())
        self.stack.addWidget(self._system_page())
        self.stack.addWidget(self._packages_page())
        self.stack.addWidget(self._progress_page())
        right_layout.addWidget(self.stack, 1)
        right_layout.addLayout(self._buttons())
        layout.addWidget(right, 1)
        self._apply_icon()

    def _apply_icon(self) -> None:
        try:
            from cameav.theme import app_icon
        except Exception:
            return
        icon = app_icon()
        self.setWindowIcon(icon)

    def _rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(250)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(18, 22, 18, 18)
        layout.setSpacing(6)
        brand = QLabel("ClamNTK")
        brand.setObjectName("railTitle")
        sub = QLabel("Установщик для РЕД ОС")
        sub.setObjectName("railSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addSpacing(18)
        self.step_labels: list[QLabel] = []
        for index, name in enumerate(STEPS, start=1):
            label = QLabel(f"{index}   {name}")
            label.setObjectName("stepOff")
            self.step_labels.append(label)
            layout.addWidget(label)
        layout.addStretch(1)
        foot = QLabel(self.info["pretty"])
        foot.setWordWrap(True)
        foot.setObjectName("railSub")
        layout.addWidget(foot)
        return rail

    def _welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        title = QLabel("Установка ClamNTK")
        title.setObjectName("title")
        lead = QLabel("Антивирусная оболочка ClamAV для рабочего стола РЕД ОС.")
        lead.setObjectName("lead")
        lead.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(lead)
        text = QLabel(
            "Если на компьютере нет ClamAV, мастер сначала скачает официальную сборку "
            "с clamav.net: в репозитории РЕД ОС такого пакета нет. Оболочка ставится "
            "только после этого и появляется в меню «Администрирование»."
        )
        text.setWordWrap(True)
        text.setObjectName("body")
        layout.addWidget(text)
        if self.rpm_info:
            note = ("ok", f"Локальный пакет: {self.rpm_info['label']}. ClamAV будет поставлен до него.")
        elif source_tree_ready():
            note = ("ok", "ClamAV будет загружен с clamav.net до установки оболочки.")
        else:
            note = ("bad", self.rpm_error)
        layout.addWidget(banner(*note))
        layout.addStretch(1)
        return page

    def _system_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Проверка системы")
        title.setObjectName("title")
        layout.addWidget(title)
        registered = cameav_registered()
        rows = [
            (self.supported, "Операционная система", self.support_text),
            (tool_exists("dnf"), "Пакетный менеджер", "dnf найден" if tool_exists("dnf") else "dnf не найден"),
            (
                tool_exists("pkexec") or os.geteuid() == 0,
                "Права администратора",
                "Подтверждение через polkit" if tool_exists("pkexec") else "Нет pkexec",
            ),
            self._shell_row(),
            self._clamav_row(),
        ]
        if registered:
            rows.append((True, "ClamNTK в системе", f"Установлен пакет {registered}"))
        elif files_copied_without_package():
            rows.append((True, "ClamNTK в системе", "Файлы уже скопированы, но пакет dnf ещё не зарегистрирован"))
        else:
            rows.append((True, "ClamNTK в системе", "Пока не установлен"))
        for ok, heading, detail in rows:
            layout.addWidget(self._check_card(ok, heading, detail))
        layout.addStretch(1)
        return page

    def _packages_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Состав установки")
        title.setObjectName("title")
        hint = QLabel(
            "Сначала ставится ClamAV. В репозитории РЕД ОС его нет, поэтому берётся сборка с clamav.net. "
            "Оболочка — последней. Для загрузки нужен интернет."
        )
        hint.setWordWrap(True)
        hint.setObjectName("lead")
        layout.addWidget(title)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Пакет", "Версия", "Состояние", "Источник"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        for row_data in self.plan:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                row_data["name"],
                row_data["detail"],
                row_data["state"],
                row_data["source"],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        return page

    def _progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.progress_title = QLabel("Установка")
        self.progress_title.setObjectName("title")
        self.progress_lead = QLabel("Подтвердите права администратора в окне РЕД ОС.")
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
        row.setSpacing(8)
        self.back_button = button("Назад", "secondary")
        self.remove_button = button("Удалить", "danger")
        self.next_button = button("Далее", "primary")
        self.back_button.clicked.connect(self.go_back)
        self.remove_button.clicked.connect(self.remove_package)
        self.next_button.clicked.connect(self.go_next)
        row.addWidget(self.back_button)
        row.addStretch(1)
        row.addWidget(self.remove_button)
        row.addWidget(self.next_button)
        return row

    def show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for step, label in enumerate(self.step_labels):
            label.setObjectName("stepOn" if step == index else "stepOff")
            label.style().unpolish(label)
            label.style().polish(label)
        running = self.worker_thread is not None
        self.back_button.setEnabled(index > 0 and not running and index < 3)
        self.remove_button.setVisible(
            index == 2 and (cameav_registered() is not None or files_copied_without_package())
        )
        self.remove_button.setEnabled(not running)
        if index == 0:
            self.next_button.setText("Далее")
            self.next_button.setEnabled(True)
        elif index == 1:
            self.next_button.setText("Далее")
            self.next_button.setEnabled(self._can_install())
        elif index == 2:
            self.next_button.setText("Переустановить" if cameav_registered() else "Установить")
            self.next_button.setEnabled(self._can_install() and not running)
        else:
            self.next_button.setText("Запустить ClamNTK" if self.success and self.action != "remove" else "Закрыть")
            self.next_button.setEnabled(not running)

    def go_back(self) -> None:
        current = self.stack.currentIndex()
        if current > 0:
            self.show_step(current - 1)

    def go_next(self) -> None:
        current = self.stack.currentIndex()
        if current < 2:
            self.show_step(current + 1)
            return
        if current == 2:
            self.start_transaction("install")
            return
        if self.success and self.action == "install":
            subprocess.Popen(["cameav"], start_new_session=True)
        self.close()

    def remove_package(self) -> None:
        answer = QMessageBox.question(
            self,
            "Удаление ClamNTK",
            "Удалить оболочку ClamNTK? ClamAV останется. Полный выбор есть в «Удалить ClamNTK».",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.start_transaction("remove")

    def start_transaction(self, action: str) -> None:
        try:
            command = command_for(action, self.rpm_path if self.rpm_info else None)
        except RuntimeError as exc:
            QMessageBox.warning(self, "ClamNTK", str(exc))
            return
        self.action = action
        self.success = False
        self.log.clear()
        if action == "install":
            self.progress_title.setText("Установка")
            self.progress_lead.setText(
                "Подтвердите права администратора. Сначала будет загружен и установлен ClamAV, затем оболочка."
            )
        else:
            self.progress_title.setText("Удаление")
            self.progress_lead.setText("Подтвердите права администратора. Будет удалён только ClamNTK.")
        self.progress.setRange(0, 0)
        self.progress.show()
        self.log.appendPlainText("Команда: " + " ".join(command))
        worker = DnfWorker(command)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.line.connect(self._append_log)
        worker.finished_code.connect(self._transaction_finished)
        worker.finished_code.connect(thread.quit)
        thread.finished.connect(self._thread_finished)
        self.worker = worker
        self.worker_thread = thread
        self.show_step(3)
        thread.start()

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _transaction_finished(self, code: int) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if code == 0:
            self.success = True
            if self.action == "remove":
                self.progress_title.setText("ClamNTK удалён")
                self.progress_lead.setText("Пакет снят с системы. ClamAV не удалялся.")
            else:
                self.progress_title.setText("ClamNTK установлен")
                self.progress_lead.setText("Пункт появится в меню приложений. Можно запустить программу сразу.")
            return
        self.success = False
        if code in {126, 127}:
            text = "Права администратора не получены."
        else:
            text = f"dnf завершился с кодом {code}. Текст выше подскажет причину."
        self.progress_title.setText("Не выполнено")
        self.progress_lead.setText(text)

    def _thread_finished(self) -> None:
        self.worker_thread = None
        self.show_step(3)

    def _shell_row(self) -> tuple[bool, str, str]:
        if self.rpm_path is not None and not self.rpm_error:
            return True, "Оболочка", f"Пакет {self.rpm_path.name}, после зависимостей"
        if source_tree_ready():
            return True, "Оболочка", "Будет поставлена из этой папки после ClamAV"
        return False, "Оболочка", self.rpm_error

    def _clamav_row(self) -> tuple[bool, str, str]:
        current = installed_nevra("clamav")
        if current:
            return True, "ClamAV", f"Уже установлен: {current}"
        if clamav_present():
            return True, "ClamAV", "Команда clamscan уже есть в системе"
        return True, "ClamAV", "Нет в системе. Установщик скачает сборку с clamav.net до оболочки."

    def _can_install(self) -> bool:
        ready = (self.rpm_path is not None and not self.rpm_error) or source_tree_ready()
        return self.supported and ready and tool_exists("dnf") and (
            tool_exists("pkexec") or os.geteuid() == 0
        )

    @staticmethod
    def _check_card(ok: bool, title: str, detail: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {'#0E8A4A' if ok else '#C8102E'}; font-size: 16px; background: transparent;")
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("body")
        heading.setStyleSheet("font-weight: 700;")
        sub = QLabel(detail)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        text_box.addWidget(heading)
        text_box.addWidget(sub)
        layout.addWidget(dot, 0, Qt.AlignTop)
        layout.addLayout(text_box, 1)
        return frame

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.worker_thread is not None:
            QMessageBox.information(self, "ClamNTK", "Дождитесь окончания работы dnf.")
            event.ignore()
            return
        event.accept()


def main() -> int:
    import sys

    from PyQt5.QtWidgets import QApplication

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Установка ClamNTK")
    app.setStyle("Fusion")
    app.setFont(QFont("Inter", 10))
    app.setStyleSheet(STYLESHEET)
    window = SetupWindow()
    window.show()
    return app.exec_()
