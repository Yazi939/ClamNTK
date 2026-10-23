"""Постановка ClamAV, если в репозитории РЕД ОС нет пакета clamav."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# Долгая поддержка, сборка совместима с glibc РЕД ОС 8.
CLAMAV_VERSION = "1.4.6"
REPO_PACKAGES = ("python3", "python3-qt5", "python3-qt5-base", "polkit")


def clamav_present() -> bool:
    if shutil.which("clamscan"):
        return True
    completed = subprocess.run(
        ["rpm", "-q", "clamav"],
        text=True,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    return completed.returncode == 0 and "not installed" not in completed.stdout


def repo_has_clamav() -> bool:
    completed = subprocess.run(
        ["dnf", "-q", "repoquery", "--available", "clamav"],
        text=True,
        capture_output=True,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def official_rpm_url(arch: str | None = None) -> str:
    machine = arch or platform.machine()
    if machine not in {"x86_64", "aarch64"}:
        raise RuntimeError(f"Нет официальной сборки ClamAV для {machine}.")
    return (
        "https://www.clamav.net/downloads/production/"
        f"clamav-{CLAMAV_VERSION}.linux.{machine}.rpm"
    )


def _download(url: str, dest: Path) -> None:
    print(f"ClamNTK: загрузка {url}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "ClamNTK"})
    with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        reported = -1
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
                        f"ClamNTK: загружено {done // (1024 * 1024)} из {total // (1024 * 1024)} МиБ",
                        flush=True,
                    )
                else:
                    print(f"ClamNTK: загружено {done // (1024 * 1024)} МиБ", flush=True)


DATABASE_DIR = Path("/usr/local/share/clamav")


def _disable_example(text: str) -> str:
    lines = ["#Example\n" if line.strip() == "Example" else line for line in text.splitlines(True)]
    return "".join(lines)


def _has_directive(text: str, key: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.split(None, 1)[0] == key:
            return True
    return False


def _ensure_directive(text: str, key: str, value: str) -> str:
    if _has_directive(text, key):
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f"{key} {value}\n"


def _account_exists(kind: str, name: str) -> bool:
    database = "group" if kind == "group" else "passwd"
    completed = subprocess.run(["getent", database, name], capture_output=True)
    return completed.returncode == 0


def _nologin() -> str:
    for candidate in ("/sbin/nologin", "/usr/sbin/nologin", "/bin/false"):
        if Path(candidate).is_file():
            return candidate
    return "/bin/false"


def _ensure_clamav_account() -> None:
    if not _account_exists("group", "clamav"):
        subprocess.check_call(["groupadd", "--system", "clamav"])
    if not _account_exists("user", "clamav"):
        subprocess.check_call(
            [
                "useradd",
                "--system",
                "--gid",
                "clamav",
                "--no-create-home",
                "--shell",
                _nologin(),
                "--comment",
                "ClamAV",
                "clamav",
            ]
        )


def _give_database(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    shutil.chown(directory, user="clamav", group="clamav")
    directory.chmod(0o755)
    for child in directory.rglob("*"):
        shutil.chown(child, user="clamav", group="clamav")


def prepare_clamav_runtime() -> None:
    """Убрать Example, завести пользователя clamav и отдать ему каталог баз.

    Без этого freshclam от root завершается с кодом 2.
    """
    _ensure_clamav_account()
    etc = Path("/usr/local/etc")
    etc.mkdir(parents=True, exist_ok=True)
    for name in ("freshclam.conf", "clamd.conf"):
        config = etc / name
        sample = etc / f"{name}.sample"
        if not config.is_file() and sample.is_file():
            config.write_text(sample.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        if not config.is_file():
            continue
        original = config.read_text(encoding="utf-8", errors="replace")
        text = _disable_example(original)
        if name == "freshclam.conf":
            text = _ensure_directive(text, "DatabaseOwner", "clamav")
            text = _ensure_directive(text, "DatabaseDirectory", str(DATABASE_DIR))
            text = _ensure_directive(text, "DatabaseMirror", "database.clamav.net")
        elif name == "clamd.conf":
            text = _ensure_directive(text, "DatabaseDirectory", str(DATABASE_DIR))
        if text != original:
            config.write_text(text, encoding="utf-8")
            print(f"ClamNTK: подготовлен {config}", flush=True)
    _give_database(DATABASE_DIR)


def install_clamav() -> None:
    if clamav_present():
        print("ClamNTK: ClamAV уже установлен", flush=True)
        return
    if repo_has_clamav():
        print("ClamNTK: установка пакета clamav из репозитория РЕД ОС", flush=True)
        subprocess.check_call(["dnf", "install", "-y", "clamav"])
    else:
        print("ClamNTK: в репозитории РЕД ОС нет пакета clamav", flush=True)
        url = official_rpm_url()
        dest = Path(f"/tmp/clamav-{CLAMAV_VERSION}.linux.{platform.machine()}.rpm")
        try:
            _download(url, dest)
        except Exception as exc:
            raise RuntimeError(f"Не удалось скачать ClamAV: {exc}") from exc
        subprocess.check_call(
            ["dnf", "install", "-y", "--setopt=localpkg_gpgcheck=0", str(dest)]
        )
    if not clamav_present():
        raise RuntimeError("ClamAV установлен, но команда clamscan не найдена.")


def install_missing() -> None:
    print("ClamNTK: установка библиотек интерфейса", flush=True)
    subprocess.check_call(["dnf", "install", "-y", *REPO_PACKAGES])
    install_clamav()
    prepare_clamav_runtime()


if __name__ == "__main__":
    try:
        install_missing()
    except Exception as exc:
        sys.stderr.write(str(exc) + "\n")
        raise SystemExit(1) from exc
