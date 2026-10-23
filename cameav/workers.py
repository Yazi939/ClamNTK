"""Фоновые процессы проверки, обновления баз и установки пакетов."""

from __future__ import annotations

import os
import signal
import subprocess

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from cameav.backend import (
    EngineInfo,
    ScanOptions,
    ScanState,
    build_daemon_scan_command,
    build_scan_command,
    command_environment,
    consume_scan_line,
    final_counts,
)
from cameav.daemon import ScanDaemon


class ProcessWorker(QObject):
    line = pyqtSignal(str)
    succeeded = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, command: list[str], new_session: bool = True) -> None:
        super().__init__()
        self.command = command
        self.new_session = new_session
        self.proc: subprocess.Popen[str] | None = None
        self._stop = False

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=self.new_session,
                env=command_environment(),
            )
        except OSError as exc:
            self.failed.emit(str(exc))
            return
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            self.line.emit(raw.rstrip("\n"))
            if self._stop:
                break
        if self._stop and self.proc.poll() is None:
            _terminate(self.proc, force=True)
        code = self.proc.wait()
        self.succeeded.emit(code)

    def stop(self) -> None:
        self._stop = True
        if self.proc is not None and self.proc.poll() is None:
            _terminate(self.proc, force=False)


class ReleaseCheckWorker(QObject):
    found = pyqtSignal(object)
    failed = pyqtSignal(str)

    @pyqtSlot()
    def run(self) -> None:
        try:
            from cameav.update import fetch_latest

            self.found.emit(fetch_latest())
        except Exception as exc:
            self.failed.emit(str(exc))


class ReleaseDownloadWorker(QObject):
    finished_file = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, release: object) -> None:
        super().__init__()
        self.release = release

    @pyqtSlot()
    def run(self) -> None:
        try:
            from cameav.update import download_release

            self.finished_file.emit(str(download_release(self.release)))  # type: ignore[arg-type]
        except Exception as exc:
            self.failed.emit(str(exc))


class ScanWorker(QObject):
    file_seen = pyqtSignal(str)
    stats = pyqtSignal(int, int, int)
    threat = pyqtSignal(str, str)
    phase = pyqtSignal(str)
    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(
        self,
        engine: EngineInfo,
        targets: list[str],
        options: ScanOptions,
        daemon: ScanDaemon,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.targets = targets
        self.options = options
        self.daemon = daemon
        self.proc: subprocess.Popen[str] | None = None
        self._stop = False

    @pyqtSlot()
    def run(self) -> None:
        command = self._command()
        if command is None:
            return
        if self._stop:
            self.succeeded.emit(self._empty_result(cancelled=True))
            return
        state = ScanState()
        try:
            self.proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
                env=command_environment(),
            )
        except OSError as exc:
            self.failed.emit(str(exc))
            return
        assert self.proc.stdout is not None
        since_emit = 0
        noticed: list[str] = []
        for raw in self.proc.stdout:
            if self._stop:
                break
            stripped = raw.strip()
            if stripped.startswith("ERROR:") or "No supported database" in stripped or "cli_loaddbdir" in stripped:
                noticed.append(stripped)
            kind = consume_scan_line(state, raw)
            if kind == "threat":
                threat = state.threats[-1]
                self.threat.emit(threat["path"], threat["virus"])
                self.file_seen.emit(state.current_file)
                self.stats.emit(state.scanned, state.infected, state.errors)
                since_emit = 0
            elif kind == "ok":
                since_emit += 1
                if since_emit == 1 or since_emit >= 4:
                    self.file_seen.emit(state.current_file)
                    self.stats.emit(state.scanned, state.infected, state.errors)
                    if since_emit >= 4:
                        since_emit = 0
            elif kind == "error":
                since_emit += 1
                if since_emit >= 4:
                    self.stats.emit(state.scanned, state.infected, state.errors)
                    since_emit = 0
        if self._stop and self.proc.poll() is None:
            _terminate(self.proc, force=True)
        code = self.proc.wait()
        if state.current_file and not self._stop:
            self.file_seen.emit(state.current_file)
        scanned, infected, errors = final_counts(state)
        self.stats.emit(scanned, infected, errors)
        if not self._stop and code not in (0, 1):
            self.failed.emit(_scan_failure_text(noticed, code))
            return
        result = self._empty_result(cancelled=self._stop)
        result.update(
            {
                "returncode": code,
                "scanned": scanned,
                "infected": infected,
                "errors": errors,
                "threats": state.threats,
                "summary": state.summary,
            }
        )
        self.succeeded.emit(result)

    def stop(self) -> None:
        self._stop = True
        if self.proc is not None and self.proc.poll() is None:
            _terminate(self.proc, force=False)

    def _empty_result(self, cancelled: bool) -> dict:
        return {
            "returncode": -1,
            "scanned": 0,
            "infected": 0,
            "errors": 0,
            "threats": [],
            "summary": {},
            "cancelled": cancelled,
            "targets": self.targets,
        }

    def _command(self) -> list[str] | None:
        use_daemon = not self.options.as_admin or os.geteuid() == 0
        if use_daemon and not self.daemon.prepared(self.engine, self.options):
            self.phase.emit("Загрузка баз в память — один раз за сеанс…")
        if use_daemon:
            config = self.daemon.ensure(self.engine, self.options)
            if config is not None:
                try:
                    self.phase.emit("Идёт проверка")
                    return build_daemon_scan_command(config, self.targets)
                except RuntimeError:
                    pass
        self.phase.emit("Базы читаются заново, поэтому старт медленнее…")
        try:
            return build_scan_command(self.engine, self.targets, self.options)
        except RuntimeError as exc:
            self.failed.emit(str(exc))
            return None


def _scan_failure_text(lines: list[str], code: int) -> str:
    blob = "\n".join(lines)
    if "No supported database" in blob or "cli_loaddbdir" in blob:
        return "Нет баз сигнатур. Откройте «Обновления» и нажмите «Обновить базы», иначе проверка файлы не смотрит."
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return "Проверка не выполнена.\n" + line
    return f"Проверка завершилась с кодом {code} и не просмотрела файлы."


class CountWorker(QObject):
    progress = pyqtSignal(int)
    finished_count = pyqtSignal(int)

    def __init__(self, targets: list[str], excludes: list[str]) -> None:
        super().__init__()
        self.targets = targets
        self.excludes = [os.path.abspath(item) for item in excludes if item]
        self._stop = False

    @pyqtSlot()
    def run(self) -> None:
        total = 0
        for target in self.targets:
            if self._stop:
                break
            total += self._walk(target, total)
        self.finished_count.emit(total)

    def stop(self) -> None:
        self._stop = True

    def _excluded(self, path: str) -> bool:
        absolute = os.path.abspath(path)
        for root in self.excludes:
            if absolute == root or absolute.startswith(root + os.sep):
                return True
        return False

    def _walk(self, target: str, base: int) -> int:
        if self._excluded(target) or not os.path.exists(target):
            return 0
        if os.path.isfile(target):
            return 1
        count = 0
        for current, dirs, files in os.walk(target, followlinks=False, onerror=lambda _error: None):
            if self._stop:
                break
            dirs[:] = [
                name for name in dirs if not self._excluded(os.path.join(current, name))
            ]
            count += len(files)
            if count and count % 1500 == 0:
                self.progress.emit(base + count)
        return count


class WarmWorker(QObject):
    finished_ok = pyqtSignal(bool)

    def __init__(self, daemon: ScanDaemon, engine: EngineInfo, options: ScanOptions) -> None:
        super().__init__()
        self.daemon = daemon
        self.engine = engine
        self.options = options

    @pyqtSlot()
    def run(self) -> None:
        self.finished_ok.emit(self.daemon.ensure(self.engine, self.options) is not None)


def _terminate(proc: subprocess.Popen[str], force: bool) -> None:
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        try:
            proc.kill() if force else proc.terminate()
        except OSError:
            pass
