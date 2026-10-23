"""Страница очистки: мусор по категориям и карта каталогов."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QThread
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cameav.backend import format_dt, human_size
from cameav.cleanup import (
    CATEGORIES,
    DiskScanWorker,
    JunkDeleteWorker,
    JunkScanWorker,
    ManualDeleteWorker,
    describe_size,
    remove_from_tree,
)
from cameav.store import Store
from cameav.theme import clip_rounded, lift_text
from cameav.treemap import TreeMap


def _button(text: str, kind: str) -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(kind)
    widget.setCursor(Qt.PointingHandCursor)
    widget.setMinimumHeight(36 if kind == "chip" else 40)
    return widget


class CleanPage(QWidget):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self._thread: QThread | None = None
        self._worker = None
        self._job = ""
        self._cancel = False
        self._auto_run = False
        self._pending_delete = False
        self._found: dict = {}
        self._tree: dict | None = None
        self._stack: list[dict] = []
        self._display: list[dict] = []
        self._syncing = False
        self._build()

    def busy(self) -> bool:
        return self._thread is not None

    def stop(self) -> None:
        self._cancel = True
        self._pending_delete = False
        self._auto_run = False
        if self._worker is not None and hasattr(self._worker, "stop"):
            self._worker.stop()

    def wait(self, milliseconds: int) -> None:
        if self._thread is not None:
            self._thread.wait(milliseconds)

    def maybe_auto(self) -> None:
        if self.busy() or not self.store.settings.get("auto_clean"):
            return
        chosen = self._checked_categories()
        if not chosen:
            return
        self._auto_run = True
        self._start_junk_scan(chosen)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QLabel("Очистка")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Автоматически убирает мусор или показывает, какие папки занимают место")
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        subtitle.setMaximumWidth(720)
        lift_text(title)
        lift_text(subtitle)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.mode_auto = _button("Автоматическая", "chip")
        self.mode_map = _button("Карта диска", "chip")
        self.mode_auto.setCheckable(True)
        self.mode_map.setCheckable(True)
        self.mode_auto.setChecked(True)
        self.mode_auto.clicked.connect(lambda: self._set_mode("auto"))
        self.mode_map.clicked.connect(lambda: self._set_mode("map"))
        modes.addWidget(self.mode_auto)
        modes.addWidget(self.mode_map)
        modes.addStretch(1)
        layout.addLayout(modes)
        self.modes = QStackedWidget()
        self.modes.addWidget(self._build_auto())
        self.modes.addWidget(self._build_map())
        layout.addWidget(self.modes, 1)
        self._show_last()

    def _build_auto(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.cat_table = QTableWidget(len(CATEGORIES), 4)
        self.cat_table.setFrameShape(QFrame.NoFrame)
        clip_rounded(self.cat_table)
        self.cat_table.setHorizontalHeaderLabels(["", "Категория", "Найдено", "Что удаляется"])
        self.cat_table.verticalHeader().setVisible(False)
        self.cat_table.verticalHeader().setDefaultSectionSize(42)
        self.cat_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.cat_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cat_table.setAlternatingRowColors(True)
        self.cat_table.setShowGrid(False)
        header = self.cat_table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.cat_table.setColumnWidth(0, 52)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        enabled = set(self.store.settings["clean_categories"])
        for row, (key, title, detail) in enumerate(CATEGORIES):
            box = QCheckBox()
            box.setProperty("category", key)
            box.setChecked(key in enabled)
            box.toggled.connect(self._save_categories)
            holder = QWidget()
            holder.setAttribute(Qt.WA_TranslucentBackground, True)
            place = QHBoxLayout(holder)
            place.setContentsMargins(0, 0, 0, 0)
            place.addStretch(1)
            place.addWidget(box)
            place.addStretch(1)
            self.cat_table.setCellWidget(row, 0, holder)
            self.cat_table.setItem(row, 1, QTableWidgetItem(title))
            self.cat_table.setItem(row, 2, QTableWidgetItem("—"))
            self.cat_table.setItem(row, 3, QTableWidgetItem(detail))
        self.cat_table.cellClicked.connect(self._toggle_category)
        layout.addWidget(self.cat_table, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.find_button = _button("Найти мусор", "primary")
        self.clean_button = _button("Очистить", "danger")
        self.auto_stop = _button("Остановить", "secondary")
        self.auto_stop.setEnabled(False)
        self.find_button.clicked.connect(self._find_clicked)
        self.clean_button.clicked.connect(self._clean_clicked)
        self.auto_stop.clicked.connect(self.stop)
        self.auto_box = QCheckBox("Очищать выбранное при запуске ClamNTK")
        self.auto_box.setChecked(bool(self.store.settings["auto_clean"]))
        self.auto_box.toggled.connect(self._toggle_auto)
        actions.addWidget(self.find_button)
        actions.addWidget(self.clean_button)
        actions.addWidget(self.auto_stop)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self.auto_box)
        self.auto_progress = QProgressBar()
        self.auto_progress.setRange(0, 0)
        self.auto_progress.hide()
        self.auto_status = QLabel("Отметьте категории и нажмите «Найти мусор».")
        self.auto_status.setObjectName("muted")
        self.auto_status.setWordWrap(True)
        layout.addWidget(self.auto_progress)
        layout.addWidget(self.auto_status)
        return page

    def _build_map(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.map_path = QLineEdit(str(Path.home()))
        home_button = _button("Домой", "secondary")
        browse_button = _button("Папка", "secondary")
        self.map_scan = _button("Построить карту", "primary")
        self.map_stop = _button("Остановить", "secondary")
        self.map_stop.setEnabled(False)
        home_button.clicked.connect(lambda: self.map_path.setText(str(Path.home())))
        browse_button.clicked.connect(self._choose_map_dir)
        self.map_scan.clicked.connect(self._start_disk)
        self.map_stop.clicked.connect(self.stop)
        row.addWidget(self.map_path, 1)
        row.addWidget(home_button)
        row.addWidget(browse_button)
        row.addWidget(self.map_scan)
        row.addWidget(self.map_stop)
        layout.addLayout(row)
        self.map_progress = QProgressBar()
        self.map_progress.setRange(0, 0)
        self.map_progress.hide()
        self.map_status = QLabel("Карта показывает самые большие папки. Двойной щелчок открывает папку.")
        self.map_status.setObjectName("muted")
        self.map_status.setWordWrap(True)
        layout.addWidget(self.map_progress)
        layout.addWidget(self.map_status)
        crumb_bar = QFrame()
        crumb_bar.setObjectName("card")
        crumb_bar.setMinimumHeight(64)
        crumb = QHBoxLayout(crumb_bar)
        crumb.setContentsMargins(16, 12, 16, 12)
        crumb.setSpacing(12)
        self.up_button = _button("Наверх", "secondary")
        self.up_button.setEnabled(False)
        self.up_button.clicked.connect(self._go_up)
        self.crumb_label = QLabel(str(Path.home()))
        self.crumb_label.setObjectName("pathBar")
        self.crumb_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        crumb.addWidget(self.up_button)
        crumb.addWidget(self.crumb_label, 1)
        layout.addWidget(crumb_bar)
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        self.treemap = TreeMap()
        self.treemap.chosen.connect(self._on_map_chosen)
        self.treemap.activated.connect(self._on_map_activated)
        card_layout.addWidget(self.treemap)
        layout.addWidget(card, 2)
        self.disk_table = QTableWidget(0, 4)
        self.disk_table.setFrameShape(QFrame.NoFrame)
        clip_rounded(self.disk_table)
        self.disk_table.setHorizontalHeaderLabels(["Имя", "Тип", "Размер", "Доля"])
        self.disk_table.verticalHeader().setVisible(False)
        self.disk_table.verticalHeader().setDefaultSectionSize(36)
        self.disk_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.disk_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.disk_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.disk_table.setAlternatingRowColors(True)
        self.disk_table.setShowGrid(False)
        self.disk_table.setTextElideMode(Qt.ElideMiddle)
        disk_header = self.disk_table.horizontalHeader()
        disk_header.setHighlightSections(False)
        disk_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            disk_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.disk_table.itemSelectionChanged.connect(self._on_table_selected)
        self.disk_table.doubleClicked.connect(self._on_table_activated)
        layout.addWidget(self.disk_table, 3)
        bottom = QHBoxLayout()
        self.delete_button = _button("Удалить выбранное", "danger")
        self.delete_button.clicked.connect(self._delete_selected)
        bottom.addWidget(self.delete_button)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return page

    def _set_mode(self, mode: str) -> None:
        self.mode_auto.setChecked(mode == "auto")
        self.mode_map.setChecked(mode == "map")
        self.modes.setCurrentIndex(0 if mode == "auto" else 1)

    def _category_box(self, row: int) -> QCheckBox | None:
        holder = self.cat_table.cellWidget(row, 0)
        if holder is None:
            return None
        box = holder.findChild(QCheckBox)
        return box if isinstance(box, QCheckBox) else None

    def _toggle_category(self, row: int, column: int) -> None:
        if column == 0:
            return
        box = self._category_box(row)
        if box is not None:
            box.toggle()

    def _checked_categories(self) -> list[str]:
        chosen = []
        for row in range(self.cat_table.rowCount()):
            box = self._category_box(row)
            if box is not None and box.isChecked():
                chosen.append(str(box.property("category")))
        return chosen

    def _save_categories(self, _checked: bool = False) -> None:
        self.store.settings["clean_categories"] = self._checked_categories()
        self.store.save_settings()

    def _toggle_auto(self, checked: bool) -> None:
        self.store.settings["auto_clean"] = checked
        self.store.save_settings()
        self._show_last()

    def _show_last(self) -> None:
        raw = str(self.store.settings.get("last_clean_at") or "")
        if not raw:
            text = "Автоочистка включена." if self.store.settings.get("auto_clean") else "Автоочистка выключена."
            self.auto_status.setText(text + " Пароли, закладки и системные файлы не удаляются.")
            return
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            moment = None
        freed = human_size(int(self.store.settings.get("last_clean_bytes") or 0))
        self.auto_status.setText(f"Последняя очистка {format_dt(moment)} освободила {freed}.")

    def _find_clicked(self) -> None:
        if self.busy():
            return
        chosen = self._checked_categories()
        if not chosen:
            self._tell("Нечего искать", "Отметьте хотя бы одну категорию.")
            return
        self._auto_run = False
        self._start_junk_scan(chosen)

    def _clean_clicked(self) -> None:
        if self.busy():
            return
        chosen = [key for key in self._checked_categories() if (self._found.get(key) or {}).get("count")]
        if not chosen:
            self._tell("Мусор не найден", "Сначала нажмите «Найти мусор».")
            return
        total = sum(int((self._found.get(key) or {}).get("size") or 0) for key in chosen)
        if not self._ask(
            "Очистить мусор",
            f"Удалить найденное в отмеченных категориях? Будет освобождено около {human_size(total)}.",
            "Очистить",
        ):
            return
        self._start_delete(chosen)

    def _start_junk_scan(self, categories: list[str]) -> None:
        self._cancel = False
        self._set_running(True, "auto")
        self.auto_status.setText("Ищу мусор…")
        worker = JunkScanWorker(categories)
        thread = self._prepare(worker, "auto")
        worker.progress.connect(lambda title: self.auto_status.setText(f"Проверяю: {title}"))
        worker.finished_scan.connect(self._on_junk_scanned)
        worker.finished_scan.connect(thread.quit)
        worker.failed.connect(self._on_failed)
        thread.start()

    def _start_delete(self, categories: list[str]) -> None:
        self._cancel = False
        self._set_running(True, "auto")
        self.auto_status.setText("Удаляю мусор…")
        worker = JunkDeleteWorker(self._found, categories)
        thread = self._prepare(worker, "auto")
        worker.finished_delete.connect(self._on_junk_deleted)
        worker.finished_delete.connect(thread.quit)
        worker.failed.connect(self._on_failed)
        thread.start()

    def _on_junk_scanned(self, found: dict) -> None:
        self._found = found
        for row in range(self.cat_table.rowCount()):
            box = self._category_box(row)
            size_item = self.cat_table.item(row, 2)
            if box is None or size_item is None:
                continue
            key = str(box.property("category"))
            size_item.setText(describe_size(found, key) if key in found else "—")
        total = sum(int(group.get("size") or 0) for group in found.values())
        if self._auto_run and not self._cancel:
            self._auto_run = False
            if total:
                self._pending_delete = True
                self.auto_status.setText(f"Найдено {human_size(total)}. Удаляю…")
            else:
                self.auto_status.setText("Мусора не найдено.")
            return
        self._auto_run = False
        self.auto_status.setText(f"Найдено {human_size(total)}. Нажмите «Очистить», чтобы удалить отмеченное.")

    def _on_junk_deleted(self, result: dict) -> None:
        freed = int(result.get("freed") or 0)
        errors = result.get("errors") or []
        self.store.settings["last_clean_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.store.settings["last_clean_bytes"] = freed
        self.store.save_settings()
        for key in list(self._found):
            self._found[key] = {"size": 0, "count": 0, "hits": []}
        self._on_junk_scanned(self._found)
        if result.get("cancelled"):
            self.auto_status.setText(f"Остановлено. Уже освобождено {human_size(freed)}.")
        elif errors:
            self.auto_status.setText(
                f"Освобождено {human_size(freed)}. Часть файлов занята программами и осталась на месте."
            )
        else:
            self.auto_status.setText(f"Готово. Освобождено {human_size(freed)}.")

    def _choose_map_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка для карты", self.map_path.text() or str(Path.home()))
        if path:
            self.map_path.setText(path)

    def _start_disk(self) -> None:
        if self.busy():
            return
        root = self.map_path.text().strip()
        if not root or not Path(root).is_dir():
            self._tell("Нет папки", "Выберите существующий каталог.")
            return
        self._cancel = False
        self._set_running(True, "map")
        self.map_status.setText("Считаю размеры папок…")
        worker = DiskScanWorker(root)
        thread = self._prepare(worker, "map")
        worker.progress.connect(self._on_disk_progress)
        worker.finished_map.connect(self._on_disk_ready)
        worker.finished_map.connect(thread.quit)
        worker.failed.connect(self._on_failed)
        thread.start()

    def _on_disk_progress(self, count: int, path: str) -> None:
        width = max(240, self.map_status.width() - 12)
        shown = QFontMetrics(self.map_status.font()).elidedText(path, Qt.ElideMiddle, width)
        self.map_status.setText(f"Просмотрено {count} · {shown}")

    def _on_disk_ready(self, node: dict) -> None:
        self._tree = node
        self._stack = [node]
        self._show_current()
        if node.get("cancelled"):
            self.map_status.setText("Подсчёт остановлен. Показаны уже просмотренные папки.")

    def _show_current(self) -> None:
        if not self._stack:
            return
        node = self._stack[-1]
        children = list(node.get("children") or [])
        parent_size = int(node.get("size") or 0)
        try:
            usage = shutil.disk_usage(self.map_path.text().strip() or node.get("path") or "/")
            disk = f"На диске занято {human_size(usage.used)} из {human_size(usage.total)}. "
        except OSError:
            disk = ""
        self.map_status.setText(disk + f"В этой папке {human_size(parent_size)}.")
        self.crumb_label.setText(str(node.get("path") or ""))
        self.up_button.setEnabled(len(self._stack) > 1)
        visible = [(index, child) for index, child in enumerate(children) if int(child.get("size") or 0) > 0]
        display = visible[:15]
        self._display = []
        for index, child in display:
            self._display.append(
                {
                    "name": child["name"],
                    "size": int(child["size"]),
                    "size_text": human_size(int(child["size"])),
                    "child_index": index,
                }
            )
        if len(visible) > 15:
            rest = visible[15:]
            rest_size = sum(int(child["size"]) for _index, child in rest)
            self._display.append(
                {
                    "name": "Остальные",
                    "size": rest_size,
                    "size_text": human_size(rest_size),
                    "child_index": -1,
                }
            )
        self.treemap.set_entries(self._display)
        self.disk_table.setRowCount(0)
        kinds = {"dir": "Папка", "file": "Файл", "rollup": "Группа"}
        for index, child in enumerate(children):
            row = self.disk_table.rowCount()
            self.disk_table.insertRow(row)
            name = QTableWidgetItem(str(child["name"]))
            name.setData(Qt.UserRole, index)
            name.setToolTip(str(child.get("path") or child["name"]))
            size = int(child.get("size") or 0)
            share = "—"
            if parent_size:
                share = f"{size / parent_size * 100:.1f}".replace(".", ",") + " %"
            self.disk_table.setItem(row, 0, name)
            self.disk_table.setItem(row, 1, QTableWidgetItem(kinds.get(child.get("kind"), "Файл")))
            self.disk_table.setItem(row, 2, QTableWidgetItem(human_size(size)))
            self.disk_table.setItem(row, 3, QTableWidgetItem(share))

    def _current_children(self) -> list[dict]:
        if not self._stack:
            return []
        return list(self._stack[-1].get("children") or [])

    def _on_map_chosen(self, display_index: int) -> None:
        if display_index < 0 or display_index >= len(self._display):
            return
        child_index = self._display[display_index]["child_index"]
        if child_index < 0:
            return
        self._select_table_row(child_index)

    def _on_map_activated(self, display_index: int) -> None:
        if display_index < 0 or display_index >= len(self._display):
            return
        self._enter_child(self._display[display_index]["child_index"])

    def _on_table_selected(self) -> None:
        if self._syncing:
            return
        rows = sorted({item.row() for item in self.disk_table.selectedItems()})
        if len(rows) != 1:
            return
        name = self.disk_table.item(rows[0], 0)
        if name is None:
            return
        child_index = name.data(Qt.UserRole)
        for display_index, entry in enumerate(self._display):
            if entry["child_index"] == child_index:
                self.treemap.set_selected(display_index)
                return

    def _on_table_activated(self, index) -> None:
        name = self.disk_table.item(index.row(), 0)
        if name is not None:
            self._enter_child(name.data(Qt.UserRole))

    def _select_table_row(self, child_index: int) -> None:
        self._syncing = True
        for row in range(self.disk_table.rowCount()):
            name = self.disk_table.item(row, 0)
            if name is not None and name.data(Qt.UserRole) == child_index:
                self.disk_table.selectRow(row)
                break
        self._syncing = False

    def _enter_child(self, child_index: int) -> None:
        children = self._current_children()
        if not isinstance(child_index, int) or child_index < 0 or child_index >= len(children):
            return
        child = children[child_index]
        if child.get("kind") == "dir" and child.get("path"):
            self._stack.append(child)
            self._show_current()

    def _go_up(self) -> None:
        if len(self._stack) > 1:
            self._stack.pop()
            self._show_current()

    def _delete_selected(self) -> None:
        if self.busy() or not self._tree:
            return
        children = self._current_children()
        paths: list[str] = []
        names: list[str] = []
        for row in sorted({item.row() for item in self.disk_table.selectedItems()}):
            name = self.disk_table.item(row, 0)
            if name is None:
                continue
            child_index = name.data(Qt.UserRole)
            if not isinstance(child_index, int) or child_index < 0 or child_index >= len(children):
                continue
            child = children[child_index]
            path = str(child.get("path") or "")
            if child.get("kind") == "rollup" or not path:
                continue
            paths.append(path)
            names.append(str(child["name"]))
        if not paths:
            self._tell("Нечего удалять", "Выберите папку или файл. Группы мелких файлов так не удаляются.")
            return
        preview = "\n".join(f"• {name}" for name in names[:8])
        if len(names) > 8:
            preview += f"\nи ещё {len(names) - 8}"
        if not self._ask("Удалить с диска", f"Удалить выбранное без возможности восстановления?\n{preview}", "Удалить"):
            return
        self._cancel = False
        self._set_running(True, "map")
        worker = ManualDeleteWorker(paths, str(self._tree.get("path") or ""))
        thread = self._prepare(worker, "map")
        worker.finished_delete.connect(self._on_manual_deleted)
        worker.finished_delete.connect(thread.quit)
        worker.failed.connect(self._on_failed)
        thread.start()

    def _on_manual_deleted(self, result: dict) -> None:
        if self._tree is not None:
            for path in result.get("removed") or []:
                remove_from_tree(self._tree, path)
        self._show_current()
        freed = human_size(int(result.get("freed") or 0))
        if result.get("errors"):
            self.map_status.setText(f"Удалено на {freed}. Некоторые объекты пропущены, потому что они защищены или заняты.")
        else:
            self.map_status.setText(f"Удалено. Освобождено {freed}.")

    def _prepare(self, worker, job: str) -> QThread:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._thread_finished)
        self._worker = worker
        self._thread = thread
        self._job = job
        return thread

    def _thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_running(False, "")
        if self._pending_delete and not self._cancel:
            categories = [key for key in self._checked_categories() if (self._found.get(key) or {}).get("count")]
            self._pending_delete = False
            if categories:
                self._start_delete(categories)
                return
        self._pending_delete = False

    def _set_running(self, running: bool, job: str) -> None:
        self._job = job if running else self._job
        self.find_button.setEnabled(not running)
        self.clean_button.setEnabled(not running)
        self.auto_stop.setEnabled(running and job == "auto")
        self.map_scan.setEnabled(not running)
        self.map_stop.setEnabled(running and job == "map")
        self.delete_button.setEnabled(not running)
        self.auto_progress.setVisible(running and job == "auto")
        self.map_progress.setVisible(running and job == "map")

    def _on_failed(self, message: str) -> None:
        self.auto_status.setText(message)
        self.map_status.setText(message)
        self._tell("Очистка не выполнена", message)

    def _ask(self, title: str, text: str, accept: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)
        yes = box.addButton(accept, QMessageBox.AcceptRole)
        box.addButton("Отмена", QMessageBox.RejectRole)
        box.exec_()
        return box.clickedButton() is yes

    def _tell(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)
