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


def _prepare_configs() -> None:
    etc = Path("/usr/local/etc")
    for name in ("freshclam.conf", "clamd.conf"):
        config = etc / name
        sample = etc / f"{name}.sample"
        if config.is_file() or not sample.is_file():
            continue
        lines: list[str] = []
        for line in sample.read_text(encoding="utf-8", errors="replace").splitlines(True):
            lines.append("#Example\n" if line.strip() == "Example" else line)
        config.write_text("".join(lines), encoding="utf-8")
        print(f"ClamNTK: подготовлен {config}", flush=True)
    for directory in (Path("/usr/local/share/clamav"), Path("/var/lib/clamav")):
        directory.mkdir(parents=True, exist_ok=True)


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
    _prepare_configs()
    if not clamav_present():
        raise RuntimeError("ClamAV установлен, но команда clamscan не найдена.")


def install_missing() -> None:
    print("ClamNTK: установка библиотек интерфейса", flush=True)
    subprocess.check_call(["dnf", "install", "-y", *REPO_PACKAGES])
    install_clamav()


if __name__ == "__main__":
    try:
        install_missing()
    except Exception as exc:
        sys.stderr.write(str(exc) + "\n")
        raise SystemExit(1) from exc
