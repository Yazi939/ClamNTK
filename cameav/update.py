"""Проверка выпуска ClameNTK на GitHub.

Пользовательская версия стоит из RPM и обновляется выпуском ветки main.
Рабочая копия — это клон ветки dev: с неё готовят и публикуют следующий выпуск.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_OWNER = "Yazi939"
GITHUB_REPO = "ClameNTK"
USER_BRANCH = "main"
DEV_BRANCH = "dev"
USER_AGENT = "ClameNTK"


class UpdateError(RuntimeError):
    pass


@dataclass
class AppRelease:
    version: str
    title: str
    notes: str
    asset_name: str
    asset_url: str


def edition() -> str:
    """user — пакет в системе, dev — каталог с исходниками."""
    path = Path(__file__).resolve()
    if str(path).startswith("/usr/share/cameav/"):
        return "user"
    return "dev"


def edition_title() -> str:
    if edition() == "user":
        return "Установленная версия"
    return "Рабочая копия"


def repo_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"


def parse_version(text: str) -> tuple[int, ...]:
    numbers: list[int] = []
    for piece in text.strip().lstrip("vV").split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers) or (0,)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _request(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Выпуск ClameNTK на GitHub ещё не опубликован.") from exc
        raise UpdateError(f"GitHub ответил ошибкой {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError("Не удалось связаться с GitHub.") from exc


def fetch_latest() -> AppRelease:
    payload = _request(f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest")
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError("GitHub прислал непонятный ответ.") from exc
    version = str(data.get("tag_name") or "").lstrip("vV")
    asset = _rpm_asset(data.get("assets") or [])
    if not version or asset is None:
        raise UpdateError("В последнем выпуске нет пакета для установки.")
    return AppRelease(
        version=version,
        title=str(data.get("name") or version),
        notes=str(data.get("body") or "").strip(),
        asset_name=asset[0],
        asset_url=asset[1],
    )


def _rpm_asset(assets: list[dict]) -> tuple[str, str] | None:
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name.startswith("cameav-") and name.endswith(".rpm") and url:
            return name, url
    return None


def download_release(release: AppRelease) -> Path:
    folder = Path.home() / ".cache" / "cameav"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / release.asset_name
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(release.asset_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
    except urllib.error.URLError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError("Не удалось скачать пакет обновления.") from exc
    partial.replace(target)
    return target


def install_command(rpm_path: Path) -> list[str]:
    command = ["dnf", "install", "-y", "--setopt=localpkg_gpgcheck=0", str(rpm_path)]
    if os.geteuid() == 0:
        return command
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise UpdateError("Не найден pkexec. Установите пакет polkit.")
    return [pkexec, *command]
