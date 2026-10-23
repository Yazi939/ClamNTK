"""Главное окно ClamNTK."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cameav import __version__
from cameav.runtime import version_is_older
from cameav.backend import (
    ScanOptions,
    build_engine_upgrade_command,
    build_install_command,
    build_update_command,
    explain_freshclam,
    can_modify,
    database_state,
    database_writable,
    default_excludes,
    discover_engine,
    compact_count,
    format_dt,
    human_size,
    quarantine_locally,
    quick_targets,
)
from cameav.cleanpage import CleanPage
from cameav.daemon import ScanDaemon
from cameav.store import Store
from cameav.update import edition, edition_title, install_command, is_newer, repo_url
from cameav.theme import Backdrop, app_icon, clip_rounded, lift_text, make_icon
from cameav.workers import (
    CountWorker,
    ProcessWorker,
    ReleaseCheckWorker,
    ReleaseDownloadWorker,
    ScanWorker,
    WarmWorker,
)

NAV_ITEMS = (
    ("overview", "Обзор", "overview"),
    ("scan", "Проверка", "scan"),
    ("clean", "Очистка", "clean"),
    ("quarantine", "Карантин", "quarantine"),
    ("history", "Журнал", "history"),
    ("updates", "Обновления", "updates"),
    ("settings", "Настройки", "settings"),
)
DB_TITLES = {"main": "Основные", "daily": "Ежедневные", "bytecode": "Байткод"}


def catalog_phrase(count: int) -> str:
    tail = abs(count) % 100
    if 11 <= tail <= 14 or tail % 10 in (0, 5, 6, 7, 8, 9):
        word = "каталогов"
    elif tail % 10 == 1:
        word = "каталог"
    else:
        word = "каталога"
    return f"{count} {word}"


def format_duration(seconds: float) -> str:
    whole = max(0, int(round(seconds)))
    if whole < 60:
        return f"{whole} с"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes} мин {secs} с" if secs else f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"


def button(text: str, kind: str = "secondary") -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(kind)
    widget.setCursor(Qt.PointingHandCursor)
    widget.setMinimumHeight(36 if kind == "chip" else 40)
    return widget


def make_table(headers: list[str], stretch_column: int = 0) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setFrameShape(QFrame.NoFrame)
    clip_rounded(table)
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(40)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.ElideMiddle)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setStretchLastSection(False)
    for column in range(len(headers)):
        mode = QHeaderView.Stretch if column == stretch_column else QHeaderView.ResizeToContents
        header.setSectionResizeMode(column, mode)
    return table


class MetricCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.value = QLabel("—")
        self.value.setObjectName("cardValue")
        self.value.setWordWrap(True)
        self.hint = QLabel("")
        self.hint.setObjectName("cardHint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_values(self, value: str, hint: str) -> None:
        self.value.setText(value)
        self.hint.setText(hint)


class StatusBanner(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("statusIdle")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)
        self.dot = QLabel()
        self.dot.setFixedSize(14, 14)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.title = QLabel("ClamNTK")
        self.title.setObjectName("bannerTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("bannerSub")
        self.subtitle.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addWidget(self.dot, 0, Qt.AlignVCenter)
        layout.addLayout(text, 1)
        layout.addLayout(self.actions)

    def set_state(self, level: str, title: str, subtitle: str) -> None:
        names = {
            "ok": "statusOk",
            "warn": "statusWarn",
            "bad": "statusBad",
            "idle": "statusIdle",
        }
        colors = {"ok": "#0E8A4A", "warn": "#C47B00", "bad": "#C8102E", "idle": "#98A2B3"}
        self.setObjectName(names[level])
        self.style().unpolish(self)
        self.style().polish(self)
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.dot.setStyleSheet(
            f"background: {colors[level]}; border-radius: 7px; min-width: 14px; max-width: 14px;"
        )


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = Store()
        self.engine = discover_engine()
        self.daemon = ScanDaemon()
        self.scan_thread: QThread | None = None
        self._release_thread: QThread | None = None
        self._download_thread: QThread | None = None
        self._release_manual = False
        self._app_release = None
        self.scan_worker: ScanWorker | None = None
        self.count_thread: QThread | None = None
        self.count_worker: CountWorker | None = None
        self.warm_thread: QThread | None = None
        self.command_thread: QThread | None = None
        self.command_worker: ProcessWorker | None = None
        self.command_kind = ""
        self.scan_targets: list[str] = []
        self.preset_buttons: dict[str, QPushButton] = {}
        self.scan_label = ""
        self.scan_started: datetime | None = None
        self.found: list[dict[str, str]] = []
        self._current_path = ""
        self._scan_total: int | None = None
        self._count_done = False
        self._seen = 0
        self._infected = 0
        self._errors = 0
        self._manual_path = True
        self.setWindowTitle("ClamNTK")
        self.setWindowIcon(app_icon())
        self.resize(1180, 760)
        self.setMinimumSize(1020, 680)
        self._build()
        self.refresh_all()
        QTimer.singleShot(500, self.startup_checks)

    def _build(self) -> None:
        root = Backdrop()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(36, 28, 36, 28)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        layout.addWidget(content, 1)
        self.clean_page = CleanPage(self.store)
        self.pages = {
            "overview": self._build_overview(),
            "scan": self._build_scan(),
            "clean": self.clean_page,
            "quarantine": self._build_quarantine(),
            "history": self._build_history(),
            "updates": self._build_updates(),
            "settings": self._build_settings(),
        }
        for name in ("overview", "scan", "clean", "quarantine", "history", "updates", "settings"):
            self.stack.addWidget(self.pages[name])
        self.show_page("overview")

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(248)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(8)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(42, 42))
        logo.setFixedSize(42, 42)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("ClamNTK")
        title.setObjectName("logoTitle")
        subtitle = QLabel("Антивирус ClamAV")
        subtitle.setObjectName("logoSub")
        names.addWidget(title)
        names.addWidget(subtitle)
        brand.addWidget(logo)
        brand.addLayout(names)
        layout.addLayout(brand)
        layout.addSpacing(16)
        self.nav: dict[str, QPushButton] = {}
        for key, text, icon in NAV_ITEMS:
            nav = QPushButton(text)
            nav.setObjectName("nav")
            nav.setCheckable(True)
            nav.setCursor(Qt.PointingHandCursor)
            nav.setIcon(make_icon(icon, "#2A0B12"))
            nav.setMinimumHeight(42)
            nav.clicked.connect(lambda _checked=False, name=key: self.show_page(name))
            self.nav[key] = nav
            layout.addWidget(nav)
        layout.addStretch(1)
        line = QFrame()
        line.setObjectName("sideLine")
        line.setFixedHeight(1)
        layout.addWidget(line)
        self.side_engine = QLabel("Движок")
        self.side_engine.setObjectName("sideMeta")
        self.side_engine.setWordWrap(True)
        self.side_db = QLabel("Базы")
        self.side_db.setObjectName("sideMeta")
        self.side_db.setWordWrap(True)
        layout.addWidget(self.side_engine)
        layout.addWidget(self.side_db)
        return sidebar

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSub")
        subtitle_label.setWordWrap(True)
        subtitle_label.setMaximumWidth(720)
        lift_text(title_label)
        lift_text(subtitle_label)
        heading.addWidget(title_label)
        heading.addWidget(subtitle_label)
        layout.addLayout(heading)
        return page, layout

    def _build_overview(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(16)
        kicker = QLabel("CLAMNTK")
        kicker.setObjectName("kicker")
        hero = QLabel("Состояние защиты")
        hero.setObjectName("heroTitle")
        hero_sub = QLabel("Антивирус на рабочем столе РЕД ОС")
        hero_sub.setObjectName("heroSub")
        hero_sub.setWordWrap(True)
        hero_sub.setMaximumWidth(640)
        lift_text(hero)
        lift_text(hero_sub)
        layout.addWidget(kicker)
        layout.addWidget(hero)
        layout.addWidget(hero_sub)
        self.banner = StatusBanner()
        quick = button("Быстрая проверка", "primary")
        update = button("Обновить базы", "secondary")
        quick.clicked.connect(self.quick_scan)
        update.clicked.connect(lambda: self.start_update(manual=True))
        self.banner.actions.addWidget(quick)
        self.banner.actions.addWidget(update)
        layout.addWidget(self.banner)
        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        self.card_db = MetricCard("Базы сигнатур")
        self.card_engine = MetricCard("Движок")
        self.card_last = MetricCard("Последняя проверка")
        for card in (self.card_db, self.card_engine, self.card_last):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)
        recent = QFrame()
        recent.setObjectName("card")
        recent_layout = QVBoxLayout(recent)
        recent_layout.setContentsMargins(16, 16, 16, 16)
        recent_layout.setSpacing(10)
        recent_title = QLabel("Недавние проверки")
        recent_title.setObjectName("cardTitle")
        self.recent_table = make_table(["Когда", "Объект", "Файлы", "Угрозы"], stretch_column=1)
        self.recent_table.setSelectionMode(QAbstractItemView.NoSelection)
        recent_layout.addWidget(recent_title)
        recent_layout.addWidget(self.recent_table, 1)
        layout.addWidget(recent, 1)
        return page

    def _build_scan(self) -> QWidget:
        page, layout = self._page("Проверка", "Файл, каталог или весь компьютер")
        page.setAcceptDrops(True)
        page.dragEnterEvent = self._drag_enter  # type: ignore[method-assign]
        page.dropEvent = self._drop  # type: ignore[method-assign]
        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(22, 20, 22, 20)
        form.setSpacing(8)

        def field_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            return label

        form.addWidget(field_label("Что проверить"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Путь к файлу или каталогу")
        self.path_edit.textEdited.connect(self._on_path_edited)
        file_button = button("Файл", "secondary")
        dir_button = button("Папка", "secondary")
        file_button.clicked.connect(self.choose_file)
        dir_button.clicked.connect(self.choose_dir)
        row.addWidget(self.path_edit, 1)
        row.addWidget(file_button)
        row.addWidget(dir_button)
        form.addLayout(row)
        form.addSpacing(12)
        form.addWidget(field_label("Готовый набор"))
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for text, key, tip in (
            ("Быстрая", "quick", "Рабочий стол, загрузки, документы и временные файлы"),
            ("Домашняя папка", "home", "Весь домашний каталог"),
            ("Весь компьютер", "full", "Все доступные файлы на диске"),
        ):
            chip = button(text, "chip")
            chip.setCheckable(True)
            chip.setAutoExclusive(True)
            chip.setToolTip(tip)
            chip.clicked.connect(lambda _checked=False, name=key: self.apply_preset(name))
            self.preset_buttons[key] = chip
            chips.addWidget(chip)
        chips.addStretch(1)
        form.addLayout(chips)
        form.addSpacing(12)
        form.addWidget(field_label("Параметры"))
        options = QGridLayout()
        options.setHorizontalSpacing(28)
        options.setVerticalSpacing(8)
        self.opt_archives = QCheckBox("Архивы")
        self.opt_mail = QCheckBox("Почтовые файлы")
        self.opt_pua = QCheckBox("Потенциально нежелательные")
        self.opt_admin = QCheckBox("С правами администратора")
        options.addWidget(self.opt_archives, 0, 0)
        options.addWidget(self.opt_mail, 0, 1)
        options.addWidget(self.opt_pua, 1, 0)
        options.addWidget(self.opt_admin, 1, 1)
        options.setColumnStretch(2, 1)
        form.addLayout(options)
        self.limit_hint = QLabel()
        self.limit_hint.setObjectName("muted")
        self.limit_hint.setWordWrap(True)
        form.addWidget(self.limit_hint)
        form.addSpacing(8)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.start_button = button("Начать проверку", "primary")
        self.stop_button = button("Остановить", "danger")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addStretch(1)
        form.addLayout(actions)
        layout.addWidget(card)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.scan_counters = QLabel("Готово к проверке")
        self.scan_counters.setObjectName("muted")
        self.scan_current = QLabel("")
        self.scan_current.setObjectName("muted")
        layout.addWidget(self.progress)
        layout.addWidget(self.scan_counters)
        layout.addWidget(self.scan_current)
        self.scan_banner = QLabel("")
        self.scan_banner.setWordWrap(True)
        self.scan_banner.hide()
        layout.addWidget(self.scan_banner)
        self.threat_table = make_table(["Файл", "Угроза"], stretch_column=0)
        self.threat_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.threat_table.itemSelectionChanged.connect(self._update_threat_buttons)
        self.empty_hint = QLabel("Здесь появятся файлы, в которых найдена угроза")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setObjectName("muted")
        layout.addWidget(self.empty_hint)
        layout.addWidget(self.threat_table, 1)
        threat_actions = QHBoxLayout()
        threat_actions.setSpacing(8)
        self.quarantine_selected_button = button("В карантин", "secondary")
        self.quarantine_all_button = button("В карантин все", "secondary")
        self.delete_selected_button = button("Удалить", "danger")
        self.quarantine_selected_button.clicked.connect(self.quarantine_selected)
        self.quarantine_all_button.clicked.connect(self.quarantine_all)
        self.delete_selected_button.clicked.connect(self.delete_selected)
        threat_actions.addWidget(self.quarantine_selected_button)
        threat_actions.addWidget(self.quarantine_all_button)
        threat_actions.addWidget(self.delete_selected_button)
        threat_actions.addStretch(1)
        layout.addLayout(threat_actions)
        self._apply_saved_options()
        self._update_threat_buttons()
        return page

    def _build_quarantine(self) -> QWidget:
        page, layout = self._page("Карантин", "Изолированные файлы не запускаются")
        self.quarantine_table = make_table(
            ["Файл", "Угроза", "Исходный путь", "Дата"], stretch_column=2
        )
        self.quarantine_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.quarantine_table, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        restore = button("Восстановить", "secondary")
        delete = button("Удалить навсегда", "danger")
        restore.clicked.connect(self.restore_selected)
        delete.clicked.connect(self.delete_quarantine)
        actions.addWidget(restore)
        actions.addWidget(delete)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _build_history(self) -> QWidget:
        page, layout = self._page("Журнал", "Результаты прошлых проверок")
        self.history_table = make_table(
            ["Когда", "Объект", "Файлы", "Угрозы", "Результат"], stretch_column=1
        )
        self.history_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history_table.doubleClicked.connect(self.open_history)
        layout.addWidget(self.history_table, 1)
        actions = QHBoxLayout()
        clear = button("Очистить журнал", "secondary")
        clear.clicked.connect(self.clear_history)
        hint = QLabel("Двойной щелчок открывает подробности")
        hint.setObjectName("muted")
        actions.addWidget(clear)
        actions.addStretch(1)
        actions.addWidget(hint)
        layout.addLayout(actions)
        return page

    def _build_updates(self) -> QWidget:
        page, layout = self._page("Обновления", "Версия ClamNTK и пакеты сигнатур ClamAV")
        layout.addWidget(self._build_app_update())
        bases = QLabel("Базы сигнатур")
        bases.setObjectName("fieldLabel")
        layout.addWidget(bases)
        self.db_row = QHBoxLayout()
        self.db_row.setSpacing(14)
        layout.addLayout(self.db_row)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.update_button = button("Обновить базы", "primary")
        self.update_stop_button = button("Остановить", "danger")
        self.update_stop_button.setEnabled(False)
        self.update_button.clicked.connect(lambda: self.start_update(manual=True))
        self.update_stop_button.clicked.connect(self.stop_command)
        self.auto_update_box = QCheckBox("Обновлять базы при запуске, если они старше полутора суток")
        self.auto_update_box.setChecked(self.store.settings["auto_update"])
        self.auto_update_box.toggled.connect(self._toggle_auto_update)
        actions.addWidget(self.update_button)
        actions.addWidget(self.update_stop_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self.auto_update_box)
        self.db_note = QLabel("")
        self.db_note.setObjectName("muted")
        self.db_note.setWordWrap(True)
        layout.addWidget(self.db_note)
        self.update_log = QPlainTextEdit()
        self.update_log.setReadOnly(True)
        self.update_log.setPlainText(self.store.read_update_log())
        layout.addWidget(self.update_log, 1)
        return page

    def _build_app_update(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(22, 18, 22, 18)
        form.setSpacing(8)
        title = QLabel(edition_title())
        title.setObjectName("cardTitle")
        self.app_version = QLabel(f"ClamNTK {__version__}")
        self.app_version.setObjectName("cardValue")
        self.app_update_status = QLabel(self._edition_hint())
        self.app_update_status.setObjectName("muted")
        self.app_update_status.setWordWrap(True)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.check_app_button = button("Проверить версию", "secondary")
        self.install_app_button = button("Установить обновление", "primary")
        self.install_app_button.setEnabled(False)
        self.check_app_button.clicked.connect(lambda: self._check_app_release(True))
        self.install_app_button.clicked.connect(self._install_app_release)
        if edition() != "user":
            self.install_app_button.hide()
        actions.addWidget(self.check_app_button)
        actions.addWidget(self.install_app_button)
        actions.addStretch(1)
        form.addWidget(title)
        form.addWidget(self.app_version)
        form.addWidget(self.app_update_status)
        form.addLayout(actions)
        self._app_release = None
        return card

    def _edition_hint(self) -> str:
        if edition() == "user":
            return "Эта копия установлена как программа. Новая версия скачивается с GitHub и ставится пакетом."
        address = repo_url()
        clone = f"git clone -b dev {address}.git" if address else "git clone -b dev <адрес репозитория>"
        return (
            "Это рабочая копия для доработки. На другом компьютере её забирают так: "
            f"{clone}. "
            "Пользователям отдаётся выпуск ветки main: python3 packaging/publish.py"
        )

    def _build_settings(self) -> QWidget:
        page, layout = self._page("Настройки", "Параметры проверки сохраняются на этом компьютере")
        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(12)
        self.set_archives = QCheckBox("Проверять архивы")
        self.set_mail = QCheckBox("Проверять почтовые файлы")
        self.set_pua = QCheckBox("Искать потенциально нежелательные приложения")
        self.set_admin = QCheckBox("По умолчанию проверять с правами администратора")
        for box, key in (
            (self.set_archives, "scan_archives"),
            (self.set_mail, "scan_mail"),
            (self.set_pua, "detect_pua"),
            (self.set_admin, "as_admin"),
        ):
            box.setChecked(bool(self.store.settings[key]))
            box.toggled.connect(self._save_settings_from_page)
            form.addWidget(box)
        size_row = QHBoxLayout()
        size_row.setSpacing(10)
        size_label = QLabel("Пропускать файлы больше")
        self.max_size = QSpinBox()
        self.max_size.setRange(1, 2048)
        self.max_size.setSuffix(" МБ")
        self.max_size.setButtonSymbols(QSpinBox.NoButtons)
        self.max_size.setAlignment(Qt.AlignCenter)
        self.max_size.setFixedWidth(112)
        self.max_size.setValue(int(self.store.settings["max_filesize_mb"]))
        self.max_size.valueChanged.connect(self._save_settings_from_page)
        size_row.addWidget(size_label)
        size_row.addWidget(self.max_size)
        size_row.addStretch(1)
        form.addLayout(size_row)
        exclude_label = QLabel("Не проверять каталоги")
        self.exclude_list = QListWidget()
        self.exclude_list.setMinimumHeight(120)
        for directory in self.store.settings["exclude_dirs"]:
            self.exclude_list.addItem(directory)
        exclude_buttons = QHBoxLayout()
        add_exclude = button("Добавить", "secondary")
        remove_exclude = button("Убрать", "secondary")
        add_exclude.clicked.connect(self.add_exclude)
        remove_exclude.clicked.connect(self.remove_exclude)
        exclude_buttons.addWidget(add_exclude)
        exclude_buttons.addWidget(remove_exclude)
        exclude_buttons.addStretch(1)
        form.addWidget(exclude_label)
        form.addWidget(self.exclude_list)
        form.addLayout(exclude_buttons)
        layout.addWidget(card)
        self.about_label = QLabel()
        self.about_label.setObjectName("muted")
        self.about_label.setWordWrap(True)
        layout.addWidget(self.about_label)
        layout.addStretch(1)
        return page

    def show_page(self, name: str) -> None:
        self.stack.setCurrentWidget(self.pages[name])
        for key, nav in self.nav.items():
            nav.setChecked(key == name)
        if name == "scan":
            self._apply_saved_options()

    def refresh_all(self) -> None:
        self.refresh_sidebar()
        self.refresh_overview()
        self.refresh_quarantine()
        self.refresh_history()
        self.refresh_updates()
        self.refresh_about()
        self._update_nav_badge()
        self._update_limit_hint()

    def refresh_sidebar(self) -> None:
        if self.engine.engine_version:
            self.side_engine.setText(f"Движок {self.engine.engine_version}")
        elif self.engine.clamscan:
            self.side_engine.setText("Движок найден")
        else:
            self.side_engine.setText("Движок не установлен")
        if self.engine.engine_version and self.daemon.ready():
            self.side_engine.setText(f"Движок {self.engine.engine_version} · базы в памяти")
        daily = self.engine.daily
        if daily and daily.present:
            self.side_db.setText(f"Базы {daily.version} · {format_dt(daily.built)}")
        else:
            self.side_db.setText("Базы не найдены")

    def refresh_overview(self) -> None:
        state = database_state(self.engine)
        last = self.store.history[0] if self.store.history else None
        threats_left = self._threats_still_present(last)
        if state == "missing_engine":
            level, title, subtitle = (
                "bad",
                "Антивирус не установлен",
                "ClamNTK поставит пакет clamav из репозитория РЕД ОС.",
            )
        elif state == "missing_db":
            level, title, subtitle = (
                "bad",
                "Нет баз сигнатур",
                "Нажмите «Обновить базы», чтобы загрузить пакеты ClamAV.",
            )
        elif threats_left:
            level, title, subtitle = (
                "bad",
                "Нужно разобрать угрозы",
                "В последней проверке остались файлы, которые ещё не изолированы.",
            )
        elif state == "stale":
            level, title, subtitle = (
                "warn",
                "Базы пора обновить",
                "Ежедневный пакет сигнатур старше полутора суток.",
            )
        elif last and int(last.get("infected") or 0) > 0:
            level, title, subtitle = (
                "ok",
                "Угрозы из последней проверки разобраны",
                "Новые базы на месте, найденные файлы изолированы или удалены.",
            )
        else:
            level, title, subtitle = (
                "ok",
                "Компьютер защищён",
                "Движок ClamAV готов к проверке, базы сигнатур свежие.",
            )
        self.banner.set_state(level, title, subtitle)
        daily = self.engine.daily
        if daily and daily.present:
            self.card_db.set_values(
                format_dt(daily.built),
                f"ежедневные {daily.version} · {compact_count(self.engine.signature_count)} сигнатур",
            )
        else:
            self.card_db.set_values("Нет баз", "Обновление скачает main, daily и bytecode")
        location = Path(self.engine.clamscan).name if self.engine.clamscan else "не найден"
        self.card_engine.set_values(self.engine.engine_version or "—", location)
        if last:
            infected = int(last.get("infected") or 0)
            value = "Угроз нет" if infected == 0 else f"Угроз: {infected}"
            self.card_last.set_values(value, self._history_when(last))
        else:
            self.card_last.set_values("Ещё не было", "Запустите быструю проверку")
        self._fill_history_table(self.recent_table, self.store.history[:8], detailed=False)

    def refresh_quarantine(self) -> None:
        self.quarantine_table.setRowCount(0)
        for entry in self.store.quarantine:
            row = self.quarantine_table.rowCount()
            self.quarantine_table.insertRow(row)
            values = [
                str(entry.get("name") or ""),
                str(entry.get("virus") or ""),
                str(entry.get("original") or ""),
                self._short_iso(str(entry.get("time") or "")),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, entry.get("id"))
                self.quarantine_table.setItem(row, column, item)

    def refresh_history(self) -> None:
        self._fill_history_table(self.history_table, self.store.history, detailed=True)

    def refresh_updates(self) -> None:
        while self.db_row.count():
            item = self.db_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self.engine.databases:
            empty = MetricCard("Базы")
            empty.set_values("Не найдены", "После обновления здесь появятся три пакета")
            self.db_row.addWidget(empty, 1)
        for database in self.engine.databases:
            card = MetricCard(DB_TITLES.get(database.name, database.name))
            if database.present:
                card.set_values(
                    f"версия {database.version}",
                    f"{format_dt(database.built)} · {compact_count(database.signatures)} · {human_size(database.size)}",
                )
            else:
                card.set_values("Нет файла", "Будет загружен при обновлении")
            self.db_row.addWidget(card, 1)
        self.db_row.addStretch(0)
        if self.engine.engine_version and version_is_older(self.engine.engine_version, "1.4.6"):
            bytecode = next((item for item in self.engine.databases if item.name == "bytecode" and item.present), None)
            number = f" Номер пакета bytecode сейчас {bytecode.version}, это не код ошибки." if bytecode else ""
            self.db_note.setText(
                f"Программа ClamAV {self.engine.engine_version} старше рекомендуемой 1.4.6. "
                "Поэтому в журнале обновления появляется WARNING."
                + number
                + " Сами базы при этом загружены, проверка ими пользуется."
            )
        else:
            self.db_note.setText("")

    def refresh_about(self) -> None:
        self.about_label.setText(
            f"ClamNTK {__version__}. Базы один раз загружаются в память, обновляет их freshclam.\n"
            f"Сканер: {self.engine.clamscan or 'не найден'}\n"
            f"Обновление: {self.engine.freshclam or 'не найден'}\n"
            f"Каталог баз: {self.engine.database_dir or 'не найден'}"
        )

    def startup_checks(self) -> None:
        QTimer.singleShot(1600, lambda: self._check_app_release(False))
        if not self.engine.clamscan:
            self._offer_install()
            return
        state = database_state(self.engine)
        if state == "missing_db":
            answer = self._ask(
                "Нет баз сигнатур",
                "ClamNTK может сейчас загрузить пакеты сигнатур ClamAV.",
                "Обновить",
            )
            if answer:
                self.start_update(manual=True)
            return
        if (
            state == "stale"
            and self.store.settings["auto_update"]
            and database_writable(self.engine)
            and not self.busy()
        ):
            self.start_update(manual=False)
            return
        QTimer.singleShot(200, self._warm_daemon)
        QTimer.singleShot(900, self._auto_clean)

    def _auto_clean(self) -> None:
        if self.busy():
            return
        self.clean_page.maybe_auto()

    def _offer_install(self) -> None:
        if self._ask(
            "Нет ClamAV",
            "На компьютере не найден clamscan. Установить пакет clamav из репозитория РЕД ОС?",
            "Установить",
        ):
            self.install_packages(["clamav"])

    def quick_scan(self) -> None:
        self.show_page("scan")
        self.apply_preset("quick")
        self.start_scan()

    def apply_preset(self, name: str) -> None:
        if name == "full":
            if not self._ask(
                "Полная проверка",
                "Будут проверены все доступные файлы. Это может занять много времени.",
                "Продолжить",
            ):
                self._mark_preset("")
                return
            self._set_targets(["/"], "Весь компьютер", preset="full")
            return
        if name == "home":
            self._set_targets([str(Path.home())], "Домашняя папка", preset="home")
            return
        targets = quick_targets()
        if not targets:
            self._mark_preset("")
            self._tell("Быстрая проверка", "Не найдены каталоги рабочего стола, загрузок и документов.")
            return
        self._set_targets(targets, "Быстрая проверка", preset="quick")

    def choose_file(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Файл для проверки", str(Path.home()))
        if path:
            self._set_targets([path], path)

    def choose_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Каталог для проверки", str(Path.home()))
        if path:
            self._set_targets([path], path)

    def open_path(self, path: str) -> None:
        self.show_page("scan")
        self._set_targets([path], path)

    def start_scan(self) -> None:
        if self.busy():
            self._tell("ClamNTK занят", "Дождитесь окончания текущей операции.")
            return
        targets = self._current_targets()
        missing = [path for path in targets if not os.path.exists(path)]
        if not targets or missing:
            self._tell("Нет пути", "Выберите существующий файл или каталог.")
            return
        if not self.engine.clamscan:
            self._tell("Проверка не запущена", "Не найден clamscan. Установите пакет clamav.")
            return
        if database_state(self.engine) in {"missing_engine", "missing_db"} or not self.engine.database_dir:
            if self._ask(
                "Нет баз сигнатур",
                "Без пакетов сигнатур проверка сразу заканчивается и файлы не смотрит. Загрузить базы сейчас?",
                "Обновить",
            ):
                self.start_update(manual=True)
            return
        options = self._scan_options()
        self.scan_targets = targets
        self.scan_started = datetime.now().astimezone()
        self.found = []
        self._current_path = ""
        self._scan_total = None
        self._count_done = False
        self._seen = 0
        self._infected = 0
        self._errors = 0
        self.threat_table.setRowCount(0)
        self.empty_hint.hide()
        self.scan_banner.hide()
        self.progress.show()
        self.progress.setRange(0, 0)
        self.scan_current.setText("Считаю, сколько файлов проверять…")
        self.scan_counters.setText("Осталось: считаю объём")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_threat_buttons_enabled(False)
        self._start_counter(targets, options)
        worker = ScanWorker(self.engine, targets, options, self.daemon)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.file_seen.connect(self._on_scan_file)
        worker.stats.connect(self._on_scan_stats)
        worker.phase.connect(self._on_scan_phase)
        worker.threat.connect(self._on_threat)
        worker.succeeded.connect(self._on_scan_done)
        worker.failed.connect(self._on_scan_failed)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._scan_thread_finished)
        self.scan_worker = worker
        self.scan_thread = thread
        thread.start()

    def stop_scan(self) -> None:
        if self.scan_worker is not None:
            self.scan_worker.stop()
            self.scan_current.setText("Останавливаю проверку…")
        if self.count_worker is not None:
            self.count_worker.stop()

    def _start_counter(self, targets: list[str], options: ScanOptions) -> None:
        worker = CountWorker(targets, default_excludes(self.engine, options))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_count_progress)
        worker.finished_count.connect(self._on_count_done)
        worker.finished_count.connect(thread.quit)
        thread.finished.connect(self._count_thread_finished)
        self.count_worker = worker
        self.count_thread = thread
        thread.start()

    def _on_count_progress(self, total: int) -> None:
        if self.scan_thread is None or self._count_done:
            return
        self.scan_counters.setText(f"Проверено {self._seen} · найдено файлов {total} · считаю объём")

    def _on_count_done(self, total: int) -> None:
        self._count_done = True
        self._scan_total = total
        if self.scan_thread is not None:
            self._refresh_eta()

    def _count_thread_finished(self) -> None:
        self.count_thread = None
        self.count_worker = None

    def _refresh_eta(self) -> None:
        seen = self._seen
        total = self._scan_total if self._count_done else None
        if total is not None:
            maximum = max(total, seen, 1)
            self.progress.setRange(0, maximum)
            self.progress.setValue(maximum if total == 0 else min(seen, maximum))
            checked = f"Проверено {seen} из {total}"
        else:
            self.progress.setRange(0, 0)
            checked = f"Проверено {seen}"
        parts = [checked, f"угроз {self._infected}"]
        if self._errors:
            parts.append(f"ошибок доступа {self._errors}")
        if total and self.scan_started and 0 < seen < total:
            elapsed = (datetime.now().astimezone() - self.scan_started).total_seconds()
            if elapsed >= 1:
                remain = elapsed / seen * (total - seen)
                parts.append(f"осталось около {format_duration(remain)}")
        elif not self._count_done:
            parts.append("считаю объём")
        self.scan_counters.setText(" · ".join(parts))

    def _warm_daemon(self) -> None:
        if self.busy() or not self.engine.clamscan or not self.engine.database_dir:
            return
        if self.daemon.prepared(self.engine, self._warm_options()):
            self.refresh_sidebar()
            return
        if self.warm_thread is not None:
            return
        worker = WarmWorker(self.daemon, self.engine, self._warm_options())
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished_ok.connect(self._on_warm_done)
        worker.finished_ok.connect(thread.quit)
        thread.finished.connect(self._warm_thread_finished)
        self.warm_thread = thread
        self._warm_worker = worker
        thread.start()

    def _warm_options(self) -> ScanOptions:
        options = self._scan_options()
        options.as_admin = False
        return options

    def _on_warm_done(self, ok: bool) -> None:
        if ok:
            self.refresh_sidebar()

    def _warm_thread_finished(self) -> None:
        self.warm_thread = None
        self._warm_worker = None

    def start_update(self, manual: bool) -> None:
        if self.busy():
            if manual:
                self._tell("ClamNTK занят", "Дождитесь окончания текущей операции.")
            return
        if not self.engine.freshclam:
            if manual:
                self._offer_install()
            return
        try:
            command = build_update_command(self.engine)
        except RuntimeError as exc:
            if manual:
                self._tell("Обновление не запущено", str(exc))
            return
        self.show_page("updates")
        self._start_command(command, "update", "Загрузка пакетов сигнатур…", new_session=False)

    def _start_engine_upgrade(self) -> None:
        if self.busy():
            return
        try:
            command = build_engine_upgrade_command()
        except RuntimeError as exc:
            self._tell("Обновление ClamAV", str(exc))
            return
        self.show_page("updates")
        self._start_command(command, "engine", "Обновление программы ClamAV…", new_session=False)

    def install_packages(self, packages: list[str]) -> None:
        if self.busy():
            self._tell("ClamNTK занят", "Дождитесь окончания текущей операции.")
            return
        try:
            command = build_install_command(packages)
        except RuntimeError as exc:
            self._tell("Установка не запущена", str(exc))
            return
        self.show_page("updates")
        self._start_command(command, "install", "Установка пакетов…")

    def _check_app_release(self, manual: bool) -> None:
        if self._release_thread is not None:
            return
        self._release_manual = manual
        self.app_update_status.setText("Проверяю выпуск на GitHub…")
        worker = ReleaseCheckWorker()
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.found.connect(self._on_release_found)
        worker.failed.connect(self._on_release_failed)
        worker.found.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._release_thread_finished)
        self._release_worker = worker
        self._release_thread = thread
        thread.start()

    def _release_thread_finished(self) -> None:
        self._release_thread = None
        self._release_worker = None

    def _on_release_found(self, release: object) -> None:
        self._app_release = release
        version = str(getattr(release, "version", ""))
        if is_newer(version, __version__):
            if edition() == "user":
                self.app_update_status.setText(
                    f"Доступна версия {version}. Сейчас установлена {__version__}."
                )
                self.install_app_button.setEnabled(True)
                if not self._release_manual:
                    self._install_app_release()
            else:
                self.app_update_status.setText(
                    f"На GitHub опубликован выпуск {version}. "
                    "Эта копия рабочая: пользователи получат пакет после публикации."
                )
            return
        self.install_app_button.setEnabled(False)
        self.app_update_status.setText(f"Версия {__version__} уже последняя.")

    def _on_release_failed(self, message: str) -> None:
        self.install_app_button.setEnabled(False)
        if self._release_manual:
            self.app_update_status.setText(message)
        else:
            self.app_update_status.setText(self._edition_hint())

    def _install_app_release(self) -> None:
        release = self._app_release
        if release is None or edition() != "user":
            return
        if self.busy() or self._download_thread is not None:
            self._tell("ClamNTK занят", "Дождитесь окончания текущей операции.")
            return
        version = str(getattr(release, "version", ""))
        if not self._ask(
            "Обновление ClamNTK",
            f"Скачать и установить версию {version}? После установки программу нужно открыть заново.",
            "Установить",
        ):
            return
        self.app_update_status.setText("Скачиваю пакет…")
        self.install_app_button.setEnabled(False)
        worker = ReleaseDownloadWorker(release)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished_file.connect(self._on_release_downloaded)
        worker.failed.connect(self._on_release_download_failed)
        worker.finished_file.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._download_thread_finished)
        self._download_worker = worker
        self._download_thread = thread
        thread.start()

    def _download_thread_finished(self) -> None:
        self._download_thread = None
        self._download_worker = None

    def _on_release_download_failed(self, message: str) -> None:
        self.app_update_status.setText(message)
        self.install_app_button.setEnabled(True)

    def _on_release_downloaded(self, path: str) -> None:
        try:
            command = install_command(Path(path))
        except Exception as exc:
            self._tell("Обновление ClamNTK", str(exc))
            return
        self.show_page("updates")
        self._start_command(command, "apprelease", f"Установка {Path(path).name}…", new_session=False)

    def stop_command(self) -> None:
        if self.command_worker is not None:
            self.command_worker.stop()

    def quarantine_selected(self) -> None:
        self._quarantine_entries(self._selected_threats())

    def quarantine_all(self) -> None:
        self._quarantine_entries(list(self.found))

    def delete_selected(self) -> None:
        threats = self._selected_threats()
        if not threats:
            return
        if not self._ask(
            "Удаление",
            f"Удалить выбранные файлы ({len(threats)}) без возможности восстановления?",
            "Удалить",
        ):
            return
        errors = []
        removed = []
        for threat in threats:
            path = Path(threat["path"])
            if not path.exists():
                removed.append(threat["path"])
                continue
            try:
                if can_modify(path):
                    path.unlink()
                else:
                    self._run_helper_sync(["delete", "--path", str(path)])
                removed.append(threat["path"])
            except RuntimeError as exc:
                errors.append(str(exc))
        self._drop_threats(removed)
        if errors:
            self._tell("Не все файлы удалены", "\n".join(errors[:8]))
        self.refresh_overview()

    def restore_selected(self) -> None:
        entries = self._selected_quarantine()
        if not entries:
            return
        errors = []
        for entry in entries:
            stored = self._quarantine_file(entry)
            original = str(entry.get("original") or "")
            if stored is None:
                self.store.remove_quarantine(str(entry.get("id")))
                continue
            if not original:
                errors.append(f"{entry.get('name')}: исходный путь неизвестен")
                continue
            destination = Path(original)
            if destination.exists():
                errors.append(f"{original}: файл уже существует")
                continue
            try:
                if can_modify(stored) and os.access(destination.parent, os.W_OK):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stored, destination)
                else:
                    self._run_helper_sync(
                        ["restore", "--src", str(stored), "--dst", str(destination)]
                    )
                self.store.remove_quarantine(str(entry.get("id")))
            except (OSError, RuntimeError) as exc:
                errors.append(str(exc))
        self.refresh_quarantine()
        self._update_nav_badge()
        self.refresh_overview()
        if errors:
            self._tell("Восстановление", "\n".join(errors[:8]))

    def delete_quarantine(self) -> None:
        entries = self._selected_quarantine()
        if not entries:
            return
        if not self._ask(
            "Карантин",
            f"Удалить из карантина выбранные объекты ({len(entries)})?",
            "Удалить",
        ):
            return
        errors = []
        for entry in entries:
            stored = self._quarantine_file(entry)
            try:
                if stored is not None:
                    if can_modify(stored):
                        stored.unlink()
                    else:
                        self._run_helper_sync(["delete", "--path", str(stored)])
                self.store.remove_quarantine(str(entry.get("id")))
            except (OSError, RuntimeError) as exc:
                errors.append(str(exc))
        self.refresh_quarantine()
        self._update_nav_badge()
        if errors:
            self._tell("Карантин", "\n".join(errors[:8]))

    def open_history(self) -> None:
        entry = self._selected_history()
        if entry is None:
            return
        threats = entry.get("threats") or []
        lines = [
            f"Объект: {entry.get('label') or ''}",
            f"Начало: {self._history_when(entry)}",
            f"Файлов: {entry.get('scanned', 0)}",
            f"Угроз: {entry.get('infected', 0)}",
            f"Ошибок доступа: {entry.get('errors', 0)}",
            f"Движок: {entry.get('engine') or '—'}",
            f"База: {entry.get('db_version') or '—'}",
            "",
        ]
        if threats:
            lines.append("Найдено:")
            for threat in threats[:200]:
                lines.append(f"{threat.get('path')} — {threat.get('virus')}")
            if len(threats) > 200:
                lines.append(f"… и ещё {len(threats) - 200}")
        else:
            lines.append("Угроз не обнаружено.")
        box = QMessageBox(self)
        box.setWindowTitle("Результат проверки")
        box.setText(str(entry.get("label") or "Проверка"))
        box.setDetailedText("\n".join(lines))
        box.setIcon(QMessageBox.Information)
        box.exec_()

    def clear_history(self) -> None:
        if self._ask("Журнал", "Удалить сохранённые результаты проверок?", "Очистить"):
            self.store.clear_history()
            self.refresh_history()
            self.refresh_overview()

    def add_exclude(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Каталог-исключение", str(Path.home()))
        if not path:
            return
        existing = [self.exclude_list.item(i).text() for i in range(self.exclude_list.count())]
        if path not in existing:
            self.exclude_list.addItem(path)
            self._save_settings_from_page()

    def remove_exclude(self) -> None:
        for item in self.exclude_list.selectedItems():
            self.exclude_list.takeItem(self.exclude_list.row(item))
        self._save_settings_from_page()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.busy():
            if self.scan_thread is not None:
                question = "Вы действительно хотите закрыть ClamNTK?\nЗакрыв его, вы прервёте проверку."
            elif self.clean_page.busy():
                question = "Вы действительно хотите закрыть ClamNTK?\nЗакрыв его, вы прервёте очистку."
            elif self.command_kind == "apprelease":
                question = "Вы действительно хотите закрыть ClamNTK?\nЗакрыв его, вы прервёте установку обновления."
            else:
                question = "Вы действительно хотите закрыть ClamNTK?\nЗакрыв его, вы прервёте обновление баз."
            if not self._ask("Закрыть ClamNTK", question, "Закрыть", prefer_cancel=True):
                event.ignore()
                return
            self.stop_scan()
            self.stop_command()
            if self.scan_thread is not None:
                self.scan_thread.wait(1500)
            if self.command_thread is not None:
                self.command_thread.wait(1500)
            if self.count_thread is not None:
                self.count_thread.wait(500)
            self.clean_page.stop()
            self.clean_page.wait(1500)
        event.accept()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._current_path:
            self._on_scan_file(self._current_path)

    def busy(self) -> bool:
        return self.scan_thread is not None or self.command_thread is not None or self.clean_page.busy()

    def _start_command(self, command: list[str], kind: str, first_line: str, new_session: bool = True) -> None:
        self.command_kind = kind
        self.update_log.clear()
        self.update_log.appendPlainText(first_line)
        self.update_button.setEnabled(False)
        self.update_stop_button.setEnabled(True)
        worker = ProcessWorker(command, new_session=new_session)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.line.connect(self._on_command_line)
        worker.succeeded.connect(self._on_command_done)
        worker.failed.connect(self._on_command_failed)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._command_thread_finished)
        self.command_worker = worker
        self.command_thread = thread
        thread.start()

    def _on_command_line(self, line: str) -> None:
        self.update_log.appendPlainText(line)
        scrollbar = self.update_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_command_done(self, code: int) -> None:
        self.store.write_update_log(self.update_log.toPlainText())
        self.engine = discover_engine()
        self.refresh_all()
        if self.command_kind == "apprelease":
            if code == 0:
                self.update_log.appendPlainText("Новая версия ClamNTK установлена. Закройте программу и откройте её снова.")
                self.app_update_status.setText("Пакет установлен. Закройте ClamNTK и откройте снова, чтобы запустить новую версию.")
            else:
                self._tell("Обновление ClamNTK", f"dnf завершился с кодом {code}. Подробности в журнале обновления.")
        elif self.command_kind == "install":
            if code == 0 and self.engine.clamscan:
                self.update_log.appendPlainText("Пакеты установлены.")
                if database_state(self.engine) == "missing_db":
                    QTimer.singleShot(300, lambda: self.start_update(manual=True))
            elif code != 0:
                self._tell("Установка", f"dnf завершился с кодом {code}. Подробности в журнале обновления.")
        elif self.command_kind == "update":
            report = explain_freshclam(self.update_log.toPlainText(), code)
            self.update_log.appendPlainText(report.message)
            self.store.write_update_log(self.update_log.toPlainText())
            if report.ok:
                QTimer.singleShot(400, self._warm_daemon)
                if report.outdated and self._ask("Базы загружены", report.message + "\n\nПоставить рекомендуемую сборку ClamAV?", "Обновить ClamAV"):
                    self._start_engine_upgrade()
            else:
                self._tell("Обновление баз", report.message)
        elif self.command_kind == "engine":
            if code == 0:
                self.update_log.appendPlainText("Программа ClamAV обновлена. Закройте ClamNTK и откройте снова.")
                self.db_note.setText("Программа ClamAV обновлена. Закройте ClamNTK и откройте снова, чтобы проверка взяла новую сборку.")
            else:
                self._tell("Обновление ClamAV", f"Сборка не установилась, код {code}. Подробности в журнале обновления.")
        elif code not in (0, 1):
            hint = self._freshclam_hint()
            text = f"freshclam завершился с кодом {code}."
            if hint:
                text = text + "\n" + hint
            self._tell("Обновление баз", text)
        elif code == 0:
            self.update_log.appendPlainText("Базы сигнатур обновлены.")
            QTimer.singleShot(400, self._warm_daemon)

    def _freshclam_hint(self) -> str:
        lines = [line.strip() for line in self.update_log.toPlainText().splitlines() if line.strip()]
        errors = [line for line in lines if line.startswith("ERROR:") or "Initialization" in line]
        if errors:
            return errors[-1]
        return ""

    def _on_command_failed(self, message: str) -> None:
        self.update_log.appendPlainText(message)
        self.store.write_update_log(self.update_log.toPlainText())
        self._tell("Операция не выполнена", message)

    def _command_thread_finished(self) -> None:
        self.command_thread = None
        self.command_worker = None
        self.command_kind = ""
        self.update_button.setEnabled(True)
        self.update_stop_button.setEnabled(False)

    def _on_scan_phase(self, text: str) -> None:
        if not self._current_path:
            self.scan_current.setText(text)

    def _on_scan_file(self, path: str) -> None:
        self._current_path = path
        width = max(240, self.scan_current.width() - 12)
        text = QFontMetrics(self.scan_current.font()).elidedText(
            "Сейчас: " + path, Qt.ElideMiddle, width
        )
        self.scan_current.setText(text)

    def _on_scan_stats(self, scanned: int, infected: int, errors: int) -> None:
        self._seen = scanned
        self._infected = infected
        self._errors = errors
        self._refresh_eta()

    def _on_threat(self, path: str, virus: str) -> None:
        self.found.append({"path": path, "virus": virus})
        self.empty_hint.hide()
        self.threat_table.show()
        if self.threat_table.rowCount() >= 1000:
            return
        row = self.threat_table.rowCount()
        self.threat_table.insertRow(row)
        path_item = QTableWidgetItem(path)
        path_item.setData(Qt.UserRole, path)
        path_item.setToolTip(path)
        virus_item = QTableWidgetItem(virus)
        self.threat_table.setItem(row, 0, path_item)
        self.threat_table.setItem(row, 1, virus_item)

    def _on_scan_done(self, result: dict) -> None:
        cancelled = bool(result.get("cancelled"))
        infected = int(result.get("infected") or 0)
        scanned = int(result.get("scanned") or 0)
        errors = int(result.get("errors") or 0)
        self.progress.hide()
        self._current_path = ""
        if cancelled:
            text = "Проверка остановлена."
            color = "#8A5A00"
        elif infected:
            text = f"Найдено угроз: {infected}. Их можно изолировать или удалить."
            color = "#C8102E"
        else:
            text = "Угроз не обнаружено."
            color = "#0E8A4A"
        self.scan_banner.setText(text)
        self.scan_banner.setStyleSheet(
            f"background: white; border: 1px solid #E6E8EE; border-radius: 12px; "
            f"padding: 12px 14px; color: {color}; font-weight: 456;"
        )
        self.scan_banner.show()
        elapsed = 0.0
        if self.scan_started:
            elapsed = (datetime.now().astimezone() - self.scan_started).total_seconds()
        self.scan_counters.setText(
            f"Проверено {scanned} · угроз {infected} · ошибок доступа {errors}"
        )
        self.scan_current.setText(f"Заняло {format_duration(elapsed)}")
        if not self.found:
            self.empty_hint.setText("Угроз не обнаружено" if not cancelled else "Проверка остановлена")
            self.empty_hint.show()
        else:
            self.empty_hint.hide()
        daily = self.engine.daily
        self.store.add_history(
            {
                "id": uuid.uuid4().hex,
                "started": self.scan_started.isoformat(timespec="seconds") if self.scan_started else "",
                "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
                "label": self.scan_label or ", ".join(self.scan_targets),
                "targets": self.scan_targets,
                "scanned": scanned,
                "infected": infected,
                "errors": errors,
                "threats": result.get("threats") or [],
                "engine": self.engine.engine_version,
                "db_version": daily.version if daily else "",
                "cancelled": cancelled,
                "returncode": result.get("returncode"),
            }
        )
        self.refresh_history()
        self.refresh_overview()
        self._update_threat_buttons()

    def _on_scan_failed(self, message: str) -> None:
        self.progress.hide()
        self.scan_current.setText(message)
        self._tell("Проверка не выполнена", message)

    def _scan_thread_finished(self) -> None:
        self.scan_thread = None
        self.scan_worker = None
        if self.count_worker is not None:
            self.count_worker.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress.hide()
        self.refresh_sidebar()

    def _quarantine_entries(self, threats: list[dict[str, str]]) -> None:
        if not threats:
            return
        moved: list[str] = []
        errors: list[str] = []
        privileged: list[tuple[str, str, str]] = []
        for threat in threats:
            source = Path(threat["path"])
            if not source.exists():
                moved.append(threat["path"])
                continue
            stored_name = f"{uuid.uuid4().hex[:12]}_{source.name}"
            if can_modify(source):
                try:
                    entry = quarantine_locally(threat["path"], threat["virus"])
                    self.store.add_quarantine(entry)
                    moved.append(threat["path"])
                except (OSError, RuntimeError) as exc:
                    errors.append(str(exc))
            else:
                from cameav.backend import quarantine_dir, safe_filename

                stored_name = f"{uuid.uuid4().hex[:12]}_{safe_filename(source.name)}"
                destination = quarantine_dir() / stored_name
                privileged.append((threat["path"], str(destination), threat["virus"]))
        if privileged:
            args = ["quarantine"]
            for source, destination, _virus in privileged:
                args.extend(["--src", source, "--dst", destination])
            try:
                self._run_helper_sync(args)
            except RuntimeError as exc:
                errors.append(str(exc))
            else:
                for source, destination, virus in privileged:
                    if Path(destination).is_file() and not Path(source).exists():
                        self.store.add_quarantine(
                            {
                                "id": uuid.uuid4().hex,
                                "stored": Path(destination).name,
                                "original": source,
                                "name": Path(source).name,
                                "virus": virus,
                                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "size": Path(destination).stat().st_size,
                            }
                        )
                        moved.append(source)
                    else:
                        errors.append(f"Не удалось изолировать {source}")
        self._drop_threats(moved)
        self.refresh_quarantine()
        self._update_nav_badge()
        self.refresh_overview()
        if errors:
            self._tell("Карантин", "\n".join(errors[:8]))

    def _drop_threats(self, paths: list[str]) -> None:
        gone = set(paths)
        self.found = [item for item in self.found if item["path"] not in gone]
        for row in range(self.threat_table.rowCount() - 1, -1, -1):
            item = self.threat_table.item(row, 0)
            if item and item.data(Qt.UserRole) in gone:
                self.threat_table.removeRow(row)
        if not self.found:
            self.empty_hint.setText("Список угроз пуст")
            self.empty_hint.show()
        self._update_threat_buttons()

    def _run_helper_sync(self, args: list[str]) -> None:
        from cameav.backend import admin_command, command_environment

        completed = __import__("subprocess").run(
            admin_command(args),
            text=True,
            capture_output=True,
            env=command_environment(),
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "Служебная команда завершилась с ошибкой").strip()
            raise RuntimeError(message)

    def _current_targets(self) -> list[str]:
        if self.scan_targets and not self._manual_path:
            return list(self.scan_targets)
        text = self.path_edit.text().strip()
        return [text] if text else []

    def _mark_preset(self, name: str) -> None:
        for chip in self.preset_buttons.values():
            chip.blockSignals(True)
            chip.setAutoExclusive(False)
        for key, chip in self.preset_buttons.items():
            chip.setChecked(bool(name) and key == name)
        for chip in self.preset_buttons.values():
            chip.setAutoExclusive(True)
            chip.blockSignals(False)

    def _set_targets(self, targets: list[str], label: str, preset: str = "") -> None:
        self.scan_targets = targets
        self.scan_label = label
        self._manual_path = False
        if preset and len(targets) > 1:
            self.path_edit.setText(f"{label} — {catalog_phrase(len(targets))}")
            self.path_edit.setToolTip("\n".join(targets))
        else:
            shown = targets[0] if len(targets) == 1 else label
            self.path_edit.setText(shown)
            self.path_edit.setToolTip("\n".join(targets) if preset else "")
        self.path_edit.setCursorPosition(0)
        self._mark_preset(preset)

    def _on_path_edited(self, text: str) -> None:
        self._manual_path = True
        self.scan_targets = [text.strip()] if text.strip() else []
        self.scan_label = text.strip()
        self.path_edit.setToolTip("")
        self._mark_preset("")

    def _scan_options(self) -> ScanOptions:
        settings = self.store.settings
        return ScanOptions(
            archives=self.opt_archives.isChecked(),
            mail=self.opt_mail.isChecked(),
            pua=self.opt_pua.isChecked(),
            max_filesize_mb=int(settings["max_filesize_mb"]),
            exclude_dirs=list(settings["exclude_dirs"]),
            as_admin=self.opt_admin.isChecked(),
        )

    def _apply_saved_options(self) -> None:
        settings = self.store.settings
        self.opt_archives.setChecked(bool(settings["scan_archives"]))
        self.opt_mail.setChecked(bool(settings["scan_mail"]))
        self.opt_pua.setChecked(bool(settings["detect_pua"]))
        self.opt_admin.setChecked(bool(settings["as_admin"]))
        self._update_limit_hint()

    def _update_limit_hint(self) -> None:
        if hasattr(self, "limit_hint"):
            self.limit_hint.setText(
                f"Файлы больше {self.store.settings['max_filesize_mb']} МБ пропускаются."
            )

    def _save_settings_from_page(self) -> None:
        self.store.settings["scan_archives"] = self.set_archives.isChecked()
        self.store.settings["scan_mail"] = self.set_mail.isChecked()
        self.store.settings["detect_pua"] = self.set_pua.isChecked()
        self.store.settings["as_admin"] = self.set_admin.isChecked()
        self.store.settings["max_filesize_mb"] = self.max_size.value()
        self.store.settings["exclude_dirs"] = [
            self.exclude_list.item(index).text() for index in range(self.exclude_list.count())
        ]
        self.store.save_settings()
        self._apply_saved_options()

    def _toggle_auto_update(self, checked: bool) -> None:
        self.store.settings["auto_update"] = checked
        self.store.save_settings()

    def _selected_threats(self) -> list[dict[str, str]]:
        rows = sorted({item.row() for item in self.threat_table.selectedItems()})
        threats = []
        for row in rows:
            item = self.threat_table.item(row, 0)
            virus = self.threat_table.item(row, 1)
            if item is None:
                continue
            threats.append(
                {
                    "path": str(item.data(Qt.UserRole) or item.text()),
                    "virus": virus.text() if virus else "",
                }
            )
        return threats

    def _selected_quarantine(self) -> list[dict]:
        rows = sorted({item.row() for item in self.quarantine_table.selectedItems()})
        found = []
        for row in rows:
            item = self.quarantine_table.item(row, 0)
            if item is None:
                continue
            entry_id = item.data(Qt.UserRole)
            for entry in self.store.quarantine:
                if entry.get("id") == entry_id:
                    found.append(entry)
        return found

    def _selected_history(self) -> dict | None:
        rows = sorted({item.row() for item in self.history_table.selectedItems()})
        if not rows:
            return None
        if rows[0] >= len(self.store.history):
            return None
        return self.store.history[rows[0]]

    def _update_threat_buttons(self) -> None:
        has_selection = bool(self.threat_table.selectedItems())
        has_any = bool(self.found)
        running = self.scan_thread is not None
        self._set_threat_buttons_enabled(not running, has_selection, has_any)

    def _set_threat_buttons_enabled(
        self, enabled: bool = True, has_selection: bool | None = None, has_any: bool | None = None
    ) -> None:
        if has_selection is None:
            has_selection = bool(self.threat_table.selectedItems()) if hasattr(self, "threat_table") else False
        if has_any is None:
            has_any = bool(self.found)
        if hasattr(self, "quarantine_selected_button"):
            self.quarantine_selected_button.setEnabled(enabled and has_selection)
            self.delete_selected_button.setEnabled(enabled and has_selection)
            self.quarantine_all_button.setEnabled(enabled and has_any)

    def _update_nav_badge(self) -> None:
        count = len(self.store.quarantine)
        text = "Карантин" if count == 0 else f"Карантин   {count}"
        self.nav["quarantine"].setText(text)

    def _fill_history_table(self, table: QTableWidget, entries: list[dict], detailed: bool) -> None:
        table.setRowCount(0)
        for entry in entries:
            row = table.rowCount()
            table.insertRow(row)
            infected = int(entry.get("infected") or 0)
            if entry.get("cancelled"):
                result = "Остановлена"
            elif infected:
                result = "Есть угрозы"
            else:
                result = "Чисто"
            values = [
                self._history_when(entry),
                str(entry.get("label") or ""),
                str(entry.get("scanned") or 0),
                str(infected),
            ]
            if detailed:
                values.append(result)
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))

    @staticmethod
    def _history_when(entry: dict) -> str:
        raw = str(entry.get("finished") or entry.get("started") or "")
        return MainWindow._short_iso(raw)

    @staticmethod
    def _short_iso(raw: str) -> str:
        if not raw:
            return "—"
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            return raw
        return format_dt(moment)

    @staticmethod
    def _quarantine_file(entry: dict) -> Path | None:
        from cameav.backend import quarantine_dir

        path = quarantine_dir() / str(entry.get("stored") or "")
        return path if path.is_file() else None

    def _threats_still_present(self, entry: dict | None) -> bool:
        if not entry:
            return False
        for threat in entry.get("threats") or []:
            path = str(threat.get("path") or "")
            if path and os.path.exists(path):
                return True
        return False

    def _drag_enter(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self._set_targets([path], path)

    def _ask(self, title: str, text: str, accept: str, prefer_cancel: bool = False) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)
        yes = box.addButton(accept, QMessageBox.AcceptRole)
        cancel = box.addButton("Отмена", QMessageBox.RejectRole)
        if prefer_cancel:
            box.setDefaultButton(cancel)
        box.exec_()
        return box.clickedButton() is yes

    def _tell(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)
