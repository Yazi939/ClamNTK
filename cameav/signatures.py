"""Запасные пакеты сигнатур, если CDN ClamAV не отдаёт базы freshclam.

Файлы в выпуске — те же официальные .cvd. Перед записью проверяется их подпись.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from cameav.runtime import DATABASE_DIR
from cameav.update import USER_AGENT, github_coordinates

NAMES = ("bytecode", "daily", "main")
_VERSION = re.compile(r"^ClamAV-VDB:[^:]*:(\d+):")
_MIN_SIZE = {"bytecode": 10_000, "daily": 1_000_000, "main": 5_000_000}
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
    return all(read_cvd_version(directory / f"{name}.cvd") is not None for name in ("main", "daily"))


def _download(url: str, dest: Path, label: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        reported = 0
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            milestone = done // (4 * 1024 * 1024)
            if milestone > reported:
                reported = milestone
                if total:
                    print(
                        f"ClamNTK: {label} — {done // (1024 * 1024)} из {max(total // (1024 * 1024), 1)} МиБ",
                        flush=True,
                    )
                else:
                    print(f"ClamNTK: {label} — {done // (1024 * 1024)} МиБ", flush=True)


def _install_one(directory: Path, name: str, base_url: str) -> bool:
    target = directory / f"{name}.cvd"
    partial = directory / f".{name}.cvd.partial"
    url = f"{base_url}/{name}.cvd"
    print(f"ClamNTK: загрузка {name}.cvd", flush=True)
    try:
        _download(url, partial, f"{name}.cvd")
    except Exception as exc:
        print(f"ClamNTK: не удалось скачать {name}.cvd: {exc}", flush=True)
        partial.unlink(missing_ok=True)
        return signature_ok(target, name) if target.is_file() else False
    if not signature_ok(partial, name):
        print(f"ClamNTK: {name}.cvd не прошёл проверку подписи и не записан", flush=True)
        partial.unlink(missing_ok=True)
        return False
    new_version = read_cvd_version(partial) or 0
    old_version = read_cvd_version(target) or 0
    if target.is_file() and old_version >= new_version:
        partial.unlink(missing_ok=True)
        print(f"ClamNTK: местный {name}.cvd уже версии {old_version}", flush=True)
        return True
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
        return code
    lowered = output.lower()
    limited = any(marker in lowered for marker in ("429", "403", "cool-down", "cdn", "rate limit"))
    if limited:
        print("ClamNTK: сервер ClamAV поставил лимит CDN и не отдал базы.", flush=True)
    else:
        print("ClamNTK: freshclam не записал базы сигнатур.", flush=True)
    if database_dir_usable(directory):
        print(KEPT_MARK, flush=True)
        return 0
    print("ClamNTK: беру официальные пакеты из выпуска ClamNTK.", flush=True)
    if install_release_databases(directory, base_url=base_url):
        print(SAVED_MARK, flush=True)
        return 0
    print("ClamNTK: запасные пакеты тоже не скачались.", flush=True)
    return code if code not in (0, 1) else 1
