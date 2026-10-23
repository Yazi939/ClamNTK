"""Проверка РЕД ОС и установка ClamNTK через dnf."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def system_summary() -> dict[str, str]:
    os_release = read_os_release()
    return {
        "id": os_release.get("ID", ""),
        "version": os_release.get("VERSION_ID", ""),
        "pretty": os_release.get("PRETTY_NAME", "неизвестная система"),
        "arch": platform.machine(),
    }


def redos_ok(info: dict[str, str] | None = None) -> tuple[bool, str]:
    info = info or system_summary()
    if info["id"] != "redos":
        return False, f"Нужна РЕД ОС. Сейчас обнаружено: {info['pretty']}."
    major = info["version"].split(".", 1)[0]
    if major not in {"7", "8"}:
        return False, f"Проверен установщик на РЕД ОС 7 и 8. Сейчас версия {info['version'] or 'неизвестна'}."
    return True, f"{info['pretty']}, {info['arch']}"


def find_rpm() -> Path | None:
    candidates: list[Path] = []
    dist = PROJECT_ROOT / "dist"
    if dist.is_dir():
        candidates.extend(dist.glob("cameav-*.rpm"))
    candidates.extend(PROJECT_ROOT.glob("cameav-*.rpm"))
    files = [path for path in candidates if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def query_rpm_file(path: Path) -> dict[str, str]:
    output = subprocess.check_output(
        ["rpm", "-qp", "--qf", "%{NAME}|%{VERSION}|%{RELEASE}|%{ARCH}|%{SUMMARY}", str(path)],
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    ).strip()
    name, version, release, arch, summary = output.split("|", 4)
    return {
        "name": name,
        "version": version,
        "release": release,
        "arch": arch,
        "summary": summary,
        "nevra": f"{name}-{version}-{release}.{arch}",
        "label": f"{version}-{release}",
    }


def installed_nevra(name: str) -> str | None:
    completed = subprocess.run(
        ["rpm", "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", name],
        text=True,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    if not value or value.startswith("package ") or "is not installed" in value:
        return None
    return value


def requirement_names(path: Path) -> list[str]:
    output = subprocess.check_output(
        ["rpm", "-qp", "--requires", str(path)],
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    names: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("rpmlib(") or stripped.startswith("/"):
            continue
        name = stripped.split()[0]
        if name not in names:
            names.append(name)
    return names


def short_evr(value: str) -> str:
    for suffix in (".x86_64", ".aarch64", ".i686", ".noarch"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def package_plan(path: Path) -> list[dict[str, str]]:
    info = query_rpm_file(path)
    rows = [
        {
            "name": info["name"],
            "detail": info["label"],
            "state": "будет установлен" if installed_nevra(info["name"]) is None else "будет переустановлен",
            "source": "локальный пакет",
        }
    ]
    for name in requirement_names(path):
        current = installed_nevra(name)
        if current:
            state = "уже в системе"
            detail = short_evr(current.removeprefix(name + "-"))
            source = "установлен"
        else:
            state = "будет загружен"
            detail = "из репозитория РЕД ОС"
            source = "репозиторий"
        rows.append({"name": name, "detail": detail, "state": state, "source": source})
    return rows


def cameav_registered() -> str | None:
    return installed_nevra("cameav")


def files_copied_without_package() -> bool:
    return cameav_registered() is None and Path("/usr/bin/cameav").is_file()


def command_for(action: str, rpm_path: Path | None = None) -> list[str]:
    if action == "install":
        if rpm_path is None:
            raise RuntimeError("Не найден RPM-пакет ClamNTK.")
        command = [
            "dnf",
            "install",
            "-y",
            "--setopt=localpkg_gpgcheck=0",
            str(rpm_path),
        ]
    elif action == "remove":
        command = ["dnf", "remove", "-y", "cameav"]
    else:
        raise RuntimeError(f"Неизвестное действие: {action}")
    if os.geteuid() == 0:
        return command
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise RuntimeError("Не найден pkexec. Установите пакет polkit.")
    stdbuf = shutil.which("stdbuf")
    if stdbuf:
        return [pkexec, stdbuf, "-oL", "-eL", *command]
    return [pkexec, *command]


def tool_exists(name: str) -> bool:
    return shutil.which(name) is not None
