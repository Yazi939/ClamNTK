"""Запасные пакеты сигнатур, если CDN ClamAV не отдаёт базы freshclam.

Файлы в выпуске — те же официальные .cvd. Перед записью проверяется их подпись.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from cameav.runtime import DATABASE_DIR
from cameav.update import USER_AGENT, github_coordinates

NAMES = ("bytecode", "daily", "main")
_VERSION = re.compile(r"^ClamAV-VDB:[^:]*:(\d+):")
_MIN_SIZE = {"bytecode": 10_000, "daily": 1_000_000, "main": 5_000_000}
DOWNLOAD_TIMEOUT = 300
SAVED_MARK = "ClamNTK: пакеты сигнатур сохранены из выпуска программы."
KEPT_MARK = "ClamNTK: пакеты сигнатур на месте, лимит CDN не мешает проверке."


def release_base_url() -> str:
    found = github_coordinates()
    owner, repo = found if found is not None else ("Yazi939", "ClamNTK")
    return f"https://github.com/{owner}/{repo}/releases/latest/download"


def read_cvd_version(path: Path) -> int | None:
    try:
        with path.open("rb") as handle:
            raw = handle.readline(512).decode("ascii", "replace")
    except OSError:
        return None
    match = _VERSION.match(raw.strip())
    if match is None:
        return None
    return int(match.group(1))


def database_path(directory: Path, name: str) -> Path | None:
    """Путь к пакету: freshclam часто меняет .cvd на .cld."""
    for extension in (".cvd", ".cld"):
        path = directory / f"{name}{extension}"
        if path.is_file() and read_cvd_version(path) is not None:
            return path
    return None


def _sigtool() -> str | None:
    for prefix in ("/usr/local/bin", "/usr/bin", "/bin"):
        candidate = Path(prefix) / "sigtool"
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("sigtool")


def signature_ok(path: Path, name: str) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < _MIN_SIZE[name] or read_cvd_version(path) is None:
        return False
    tool = _sigtool()
    if tool is None:
        return True
    completed = subprocess.run(
        [tool, "--info", str(path)],
        text=True,
        capture_output=True,
        timeout=120,
    )
    return completed.returncode == 0 and "Verification OK" in completed.stdout


def database_dir_usable(directory: Path) -> bool:
    return database_path(directory, "main") is not None and database_path(directory, "daily") is not None


def _download_once(url: str, dest: Path, label: str, minimum: int) -> None:
    have = dest.stat().st_size if dest.is_file() else 0
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have >= minimum:
            return
        raise
    with response:
        status = getattr(response, "status", 200)
        if status == 200:
            have = 0
        mode = "ab" if status != 200 and have else "wb"
        incoming = int(response.headers.get("Content-Length") or 0)
        total = incoming + have if status != 200 else incoming
        done = have
        reported = done // (4 * 1024 * 1024)
        with dest.open(mode) as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                milestone = done // (4 * 1024 * 1024)
                if milestone != reported:
                    reported = milestone
                    if total:
                        print(
                            f"ClamNTK: {label} — {done // (1024 * 1024)} из {max(total // (1024 * 1024), 1)} МиБ",
                            flush=True,
                        )
                    else:
                        print(f"ClamNTK: {label} — {done // (1024 * 1024)} МиБ", flush=True)
        if total and done < total:
            raise RuntimeError(
                f"загрузка неполная, {done // (1024 * 1024)} из {max(total // (1024 * 1024), 1)} МиБ"
            )
    if dest.stat().st_size < minimum:
        dest.unlink(missing_ok=True)
        raise RuntimeError("Скачанный файл слишком маленький")


def _download(url: str, dest: Path, label: str, minimum: int, attempts: int = 4) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _download_once(url, dest, label, minimum)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 404, 410}:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, RuntimeError) as exc:
            last_error = exc
        print(
            f"ClamNTK: {label} прервался ({last_error}). Попытка {attempt} из {attempts}.",
            flush=True,
        )
        if attempt < attempts:
            time.sleep(min(5 * attempt, 15))
    raise RuntimeError(
        f"{label}: загрузка оборвалась по времени. Нажмите «Обновить базы» ещё раз — "
        "уже скачанная часть сохранится."
    ) from last_error


def _install_one(directory: Path, name: str, base_url: str) -> bool:
    target = directory / f"{name}.cvd"
    partial = directory / f".{name}.cvd.partial"
    url = f"{base_url}/{name}.cvd"
    print(f"ClamNTK: загрузка {name}.cvd", flush=True)
    try:
        _download(url, partial, f"{name}.cvd", _MIN_SIZE[name])
    except Exception as exc:
        print(f"ClamNTK: не удалось скачать {name}.cvd: {exc}", flush=True)
        if not partial.is_file():
            existing = database_path(directory, name)
            return existing is not None and signature_ok(existing, name)
        # частичный файл без подписи не используем как готовый пакет
        if not signature_ok(partial, name):
            return database_path(directory, name) is not None
    if not signature_ok(partial, name):
        print(f"ClamNTK: {name}.cvd не прошёл проверку подписи и не записан", flush=True)
        partial.unlink(missing_ok=True)
        return database_path(directory, name) is not None
    new_version = read_cvd_version(partial) or 0
    existing = database_path(directory, name)
    old_version = read_cvd_version(existing) if existing is not None else 0
    old_version = old_version or 0
    if existing is not None and old_version >= new_version:
        partial.unlink(missing_ok=True)
        print(f"ClamNTK: местный {name} уже версии {old_version}", flush=True)
        return True
    # Убрать устаревший .cld, иначе clamd может взять его вместо нового .cvd.
    for extension in (".cvd", ".cld"):
        stale = directory / f"{name}{extension}"
        if stale != target and stale.is_file():
            stale.unlink(missing_ok=True)
    os.replace(partial, target)
    os.chmod(target, 0o644)
    if os.geteuid() == 0:
        try:
            shutil.chown(target, user="clamav", group="clamav")
        except (OSError, LookupError):
            pass
    print(f"ClamNTK: записан {name}.cvd версии {new_version}", flush=True)
    return True


def install_release_databases(directory: Path, base_url: str | None = None) -> bool:
    directory.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        try:
            shutil.chown(directory, user="clamav", group="clamav")
        except (OSError, LookupError):
            pass
    directory.chmod(0o755)
    base = base_url or release_base_url()
    saved = {name: _install_one(directory, name, base) for name in NAMES}
    return saved["main"] and saved["daily"]


def _freshclam_bin() -> str:
    for prefix in ("/usr/local/bin", "/usr/bin", "/bin"):
        candidate = Path(prefix) / "freshclam"
        if os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which("freshclam")
    if not found:
        raise RuntimeError("Не найден freshclam")
    return found


def _run_freshclam(directory: Path) -> tuple[int, str]:
    command: list[str] = []
    stdbuf = shutil.which("stdbuf")
    if stdbuf:
        command.extend([stdbuf, "-oL", "-eL"])
    command.extend([_freshclam_bin(), "--stdout", f"--datadir={directory}"])
    environment = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=environment,
    )
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    return process.wait(), "".join(lines)


def update_databases(datadir: str | None = None, base_url: str | None = None) -> int:
    directory = Path(datadir) if datadir else DATABASE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    try:
        code, output = _run_freshclam(directory)
    except OSError as exc:
        print(f"ClamNTK: freshclam не запустился: {exc}", flush=True)
        code, output = 2, ""
    if code in (0, 1) and database_dir_usable(directory):
        _readable_databases(directory)
        return code
    lowered = output.lower()
    limited = any(
        marker in lowered
        for marker in ("429", "403", "cool-down", "cdn", "rate limit", "timed out", "timeout", "connection timed out")
    )
    if limited:
        print("ClamNTK: сервер ClamAV не отдал базы вовремя или поставил лимит.", flush=True)
    else:
        print("ClamNTK: freshclam не записал базы сигнатур.", flush=True)
    if database_dir_usable(directory):
        _readable_databases(directory)
        print(KEPT_MARK, flush=True)
        return 0
    print("ClamNTK: беру официальные пакеты из выпуска ClamNTK.", flush=True)
    if install_release_databases(directory, base_url=base_url):
        _readable_databases(directory)
        print(SAVED_MARK, flush=True)
        return 0
    print("ClamNTK: запасные пакеты тоже не скачались.", flush=True)
    return code if code not in (0, 1) else 1


def _readable_databases(directory: Path) -> None:
    """Сделать CVD/CLD читаемыми для обычного пользователя — иначе проверка даёт код 2."""
    try:
        directory.chmod(0o755)
    except OSError:
        pass
    for child in directory.iterdir():
        try:
            if child.is_file() and child.suffix.lower() in {".cvd", ".cld", ".cdb"}:
                child.chmod(0o644)
                if os.geteuid() == 0:
                    shutil.chown(child, user="clamav", group="clamav")
        except (OSError, LookupError):
            continue
