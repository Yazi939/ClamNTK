"""Фоновый clamd: базы читаются один раз и остаются в памяти до конца сеанса."""

from __future__ import annotations

import hashlib
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from cameav.backend import (
    EngineInfo,
    ScanOptions,
    default_excludes,
    exclude_regex,
    which,
)


def runtime_dir() -> Path | None:
    base = os.environ.get("XDG_RUNTIME_DIR")
    path = Path(base) / "cameav" if base else Path(f"/tmp/cameav-{os.getuid()}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


class ScanDaemon:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.config_path: Path | None = None
        self.socket_path: Path | None = None
        self.signature = ""

    def ready(self) -> bool:
        return self._ping(self._socket())

    def prepared(self, engine: EngineInfo, options: ScanOptions) -> bool:
        folder = runtime_dir()
        if folder is None or not self._ping(folder / "clamd.sock"):
            return False
        return _stored_signature(folder) == _signature(engine, options)

    def ensure(self, engine: EngineInfo, options: ScanOptions) -> Path | None:
        folder = runtime_dir()
        clamd = which("clamd")
        if folder is None or not clamd or not which("clamdscan") or not engine.database_dir:
            return None
        signature = _signature(engine, options)
        config_path = folder / "clamd.conf"
        socket_path = folder / "clamd.sock"
        pid_path = folder / "clamd.pid"
        if len(str(socket_path)) > 100:
            return None
        with self._lock:
            if _stored_signature(folder) == signature and self._ping(socket_path):
                self.config_path = config_path
                self.socket_path = socket_path
                self.signature = signature
                return config_path
            if not self._spawn(clamd, folder, config_path, socket_path, pid_path, engine, options):
                return None
            try:
                (folder / "signature").write_text(signature + "\n", encoding="utf-8")
            except OSError:
                self._stop_locked(pid_path, socket_path)
                return None
            self.config_path = config_path
            self.socket_path = socket_path
            self.signature = signature
            return config_path

    def _socket(self) -> Path | None:
        if self.socket_path is not None:
            return self.socket_path
        folder = runtime_dir()
        return folder / "clamd.sock" if folder else None

    def _wait_until_ready(self, socket_path: Path, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                return False
            if self._ping(socket_path):
                return True
            time.sleep(0.3)
        return False

    def _spawn(
        self,
        clamd: str,
        folder: Path,
        config_path: Path,
        socket_path: Path,
        pid_path: Path,
        engine: EngineInfo,
        options: ScanOptions,
    ) -> bool:
        for _attempt in range(2):
            self._stop_locked(pid_path, socket_path)
            _write_config(config_path, socket_path, pid_path, engine, options)
            error_log = folder / "clamd.err"
            try:
                error_file = error_log.open("w", encoding="utf-8")
            except OSError:
                error_file = subprocess.DEVNULL
            try:
                self.process = subprocess.Popen(
                    [clamd, f"--config-file={config_path}"],
                    stdout=subprocess.DEVNULL,
                    stderr=error_file,
                    start_new_session=True,
                )
            except OSError:
                if error_file is not subprocess.DEVNULL:
                    error_file.close()
                return False
            if error_file is not subprocess.DEVNULL:
                error_file.close()
            if self._wait_until_ready(socket_path, 90):
                return True
            time.sleep(0.4)
        self._stop_locked(pid_path, socket_path)
        return False

    def _stop_locked(self, pid_path: Path, socket_path: Path) -> None:
        _kill_by_config(pid_path.with_name("clamd.conf"))
        _kill_recorded(pid_path)
        process = self.process
        self.process = None
        self.signature = ""
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
        for path in (socket_path, pid_path):
            try:
                path.unlink()
            except OSError:
                pass
        folder = pid_path.parent
        try:
            (folder / "signature").unlink()
        except OSError:
            pass

    @staticmethod
    def _ping(socket_path: Path | None) -> bool:
        if socket_path is None or not socket_path.exists():
            return False
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(socket_path))
                client.sendall(b"zPING\0")
                return b"PONG" in client.recv(32)
        except OSError:
            return False


def _signature(engine: EngineInfo, options: ScanOptions) -> str:
    payload = "\n".join(
        [
            engine.database_dir or "",
            engine.engine_version,
            *(f"{item.name}:{item.version}" for item in engine.databases),
            str(options.archives),
            str(options.mail),
            str(options.pua),
            str(int(options.max_filesize_mb)),
            *default_excludes(engine, options),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _stored_signature(folder: Path) -> str:
    try:
        return (folder / "signature").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _is_clamd(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"clamd" in command


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_by_config(config_path: Path) -> None:
    needle = str(config_path).encode()
    victims: list[int] = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"clamd" in command and needle in command:
            victims.append(int(entry.name))
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(_pid_alive(pid) for pid in victims):
        time.sleep(0.1)
    for pid in victims:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _kill_recorded(pid_path: Path) -> None:
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pid <= 1 or not _is_clamd(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(25):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _write_config(
    path: Path,
    socket_path: Path,
    pid_path: Path,
    engine: EngineInfo,
    options: ScanOptions,
) -> None:
    threads = min(8, os.cpu_count() or 2)
    lines = [
        f"DatabaseDirectory {engine.database_dir}",
        f"LocalSocket {socket_path}",
        "LocalSocketMode 660",
        "Foreground yes",
        f"PidFile {pid_path}",
        "LogTime yes",
        "LogFileUnlock yes",
        f"LogFile {path.with_name('clamd.log')}",
        "TemporaryDirectory /tmp",
        f"MaxThreads {threads}",
        f"MaxFileSize {max(1, int(options.max_filesize_mb))}M",
        "MaxScanSize 400M",
        f"StreamMaxLength {max(1, int(options.max_filesize_mb))}M",
        "ReadTimeout 300",
        "CommandReadTimeout 30",
        "SelfCheck 120",
        f"ScanArchive {'yes' if options.archives else 'no'}",
        f"ScanMail {'yes' if options.mail else 'no'}",
        f"DetectPUA {'yes' if options.pua else 'no'}",
        "Bytecode yes",
    ]
    for directory in default_excludes(engine, options):
        lines.append(f"ExcludePath {exclude_regex(directory)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
