"""Постановка ClamAV, если в репозитории РЕД ОС нет пакета clamav."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Долгая поддержка, сборка совместима с glibc РЕД ОС 8.
CLAMAV_VERSION = "1.4.6"
REPO_PACKAGES = ("python3", "python3-qt5", "python3-qt5-base", "polkit")


def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = []
    for piece in text.replace("/", " ").split():
        if piece[:1].isdigit():
            numbers = [int(part) for part in piece.split(".") if part.isdigit()]
            break
    return tuple(numbers[:3]) or (0,)


def clamav_version() -> str:
    binary = shutil.which("clamscan")
    if not binary:
        return ""
    try:
        output = subprocess.check_output(
            [binary, "--version"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = output.strip().split()
    if len(parts) >= 2:
        return parts[1].split("/")[0]
    return ""


def clamav_present() -> bool:
    if clamav_version():
        return True
    completed = subprocess.run(
        ["rpm", "-q", "clamav"],
        text=True,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    return completed.returncode == 0 and "not installed" not in completed.stdout


def clamav_is_current() -> bool:
    found = clamav_version()
    return bool(found) and _version_tuple(found) >= _version_tuple(CLAMAV_VERSION)


def version_is_older(found: str, required: str) -> bool:
    return _version_tuple(found) < _version_tuple(required)


# Короткий таймаут dnf обрывает установку на обычной сети: код 1 и timeout.
DNF_OPTS = (
    "--setopt=timeout=300",
    "--setopt=minrate=100",
    "--setopt=retries=10",
    "--setopt=skip_if_unavailable=True",
)
DOWNLOAD_TIMEOUT = 300


def dnf_install(packages: list[str], *, local: bool = False) -> None:
    command = ["dnf", "install", "-y", *DNF_OPTS]
    if local:
        command.append("--setopt=localpkg_gpgcheck=0")
    command.extend(packages)
    subprocess.check_call(command)


def repo_has_clamav() -> bool:
    try:
        completed = subprocess.run(
            ["dnf", "-q", *DNF_OPTS, "repoquery", "--available", "clamav"],
            text=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("ClamNTK: репозиторий не ответил вовремя, сборка ClamAV будет скачана отдельно", flush=True)
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


def official_rpm_url(arch: str | None = None) -> str:
    machine = arch or platform.machine()
    if machine not in {"x86_64", "aarch64"}:
        raise RuntimeError(f"Нет официальной сборки ClamAV для {machine}.")
    return (
        "https://www.clamav.net/downloads/production/"
        f"clamav-{CLAMAV_VERSION}.linux.{machine}.rpm"
    )


def clamav_rpm_urls(arch: str | None = None) -> list[str]:
    """Сначала сайт ClamAV, затем копия в выпуске ClamNTK, если сайт не ответил."""
    machine = arch or platform.machine()
    name = f"clamav-{CLAMAV_VERSION}.linux.{machine}.rpm"
    urls = [
        "https://www.clamav.net/downloads/production/" + name,
    ]
    try:
        from cameav.update import github_coordinates

        found = github_coordinates()
    except Exception:
        found = None
    owner, repo = found or ("Yazi939", "ClamNTK")
    urls.append(f"https://github.com/{owner}/{repo}/releases/latest/download/{name}")
    return urls


def _download_once(url: str, dest: Path) -> None:
    have = dest.stat().st_size if dest.is_file() else 0
    headers = {"User-Agent": "ClamNTK"}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have > 1_000_000:
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
        print(f"ClamNTK: загрузка {url}", flush=True)
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
                            f"ClamNTK: загружено {done // (1024 * 1024)} из {max(total // (1024 * 1024), 1)} МиБ",
                            flush=True,
                        )
                    else:
                        print(f"ClamNTK: загружено {done // (1024 * 1024)} МиБ", flush=True)
        if total and done < total:
            raise RuntimeError(f"загрузка неполная, {done // (1024 * 1024)} из {max(total // (1024 * 1024), 1)} МиБ")
    if dest.stat().st_size < 1_000_000:
        dest.unlink(missing_ok=True)
        raise RuntimeError("Скачанный файл слишком маленький")


def _download(url: str, dest: Path, attempts: int = 4) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _download_once(url, dest)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 404, 410}:
                raise
            last_error = exc
            print(
                f"ClamNTK: загрузка прервалась ({exc}). Попытка {attempt} из {attempts}.",
                flush=True,
            )
            if attempt < attempts:
                time.sleep(min(5 * attempt, 15))
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, RuntimeError) as exc:
            last_error = exc
            print(
                f"ClamNTK: загрузка прервалась ({exc}). Попытка {attempt} из {attempts}.",
                flush=True,
            )
            if attempt < attempts:
                time.sleep(min(5 * attempt, 15))
    raise RuntimeError(
        "Загрузка оборвалась по времени. Нажмите «Установить» ещё раз: уже скачанная часть сохранится."
    ) from last_error


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
        # CVD должны читаться и обычным пользователем: иначе clamscan/clamd
        # завершаются с кодом 2 и «Access denied».
        try:
            if child.is_dir():
                child.chmod(0o755)
            elif child.is_file():
                child.chmod(0o644)
        except OSError:
            pass


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
    if clamav_is_current():
        print(f"ClamNTK: ClamAV {clamav_version()} уже подходит", flush=True)
        return
    found = clamav_version()
    if found:
        print(
            f"ClamNTK: ClamAV {found} старше рекомендуемой {CLAMAV_VERSION}, ставлю новую сборку",
            flush=True,
        )
    if repo_has_clamav():
        print("ClamNTK: установка пакета clamav из репозитория РЕД ОС", flush=True)
        dnf_install(["clamav"])
    else:
        print("ClamNTK: в репозитории РЕД ОС нет пакета clamav", flush=True)
        dest = Path(f"/tmp/clamav-{CLAMAV_VERSION}.linux.{platform.machine()}.rpm")
        saved: Exception | None = None
        for url in clamav_rpm_urls():
            try:
                _download(url, dest)
                saved = None
                break
            except Exception as exc:
                saved = exc
                print(f"ClamNTK: адрес не ответил, пробую следующий", flush=True)
        if saved is not None:
            raise RuntimeError(f"Не удалось скачать ClamAV: {saved}") from saved
        dnf_install([str(dest)], local=True)
    if not clamav_present():
        raise RuntimeError("ClamAV установлен, но команда clamscan не найдена.")


def install_missing() -> None:
    print("ClamNTK: установка библиотек интерфейса", flush=True)
    dnf_install(list(REPO_PACKAGES))
    install_clamav()
    prepare_clamav_runtime()


if __name__ == "__main__":
    try:
        install_missing()
    except Exception as exc:
        sys.stderr.write(str(exc) + "\n")
        raise SystemExit(1) from exc
