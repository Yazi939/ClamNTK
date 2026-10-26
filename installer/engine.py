"""Проверка РЕД ОС и установка ClamNTK через dnf."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

from cameav.runtime import CLAMAV_VERSION, clamav_present

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# ClamAV идёт первым: оболочка ставится только после него.
RUNTIME_PACKAGES = ("clamav", "python3", "python3-qt5", "python3-qt5-base", "polkit")


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


def source_tree_ready() -> bool:
    return (PROJECT_ROOT / "cameav" / "__main__.py").is_file() and (PROJECT_ROOT / "bin" / "cameav").is_file()


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


def _dependency_row(name: str) -> dict[str, str]:
    if name == "clamav" and not installed_nevra(name):
        if clamav_present():
            return {
                "name": name,
                "detail": "clamscan найден",
                "state": "уже в системе",
                "source": "установлен",
            }
        return {
            "name": name,
            "detail": f"сборка {CLAMAV_VERSION}",
            "state": "будет загружен",
            "source": "clamav.net",
        }
    current = installed_nevra(name)
    if current:
        return {
            "name": name,
            "detail": short_evr(current.removeprefix(name + "-")),
            "state": "уже в системе",
            "source": "установлен",
        }
    return {
        "name": name,
        "detail": "из репозитория РЕД ОС",
        "state": "будет загружен",
        "source": "репозиторий",
    }


def folder_plan() -> list[dict[str, str]]:
    """Зависимости, и последней строкой оболочка."""
    rows = [_dependency_row(name) for name in RUNTIME_PACKAGES]
    rows.append(
        {
            "name": "ClamNTK",
            "detail": "оболочка",
            "state": "будет установлена после зависимостей",
            "source": "эта папка",
        }
    )
    return rows


def package_plan(path: Path) -> list[dict[str, str]]:
    info = query_rpm_file(path)
    names = requirement_names(path)
    ordered = [name for name in RUNTIME_PACKAGES if name in names]
    ordered.extend(name for name in names if name not in ordered)
    rows = [_dependency_row(name) for name in ordered]
    rows.append(
        {
            "name": info["name"],
            "detail": info["label"],
            "state": "будет установлена после зависимостей"
            if installed_nevra(info["name"]) is None
            else "будет переустановлена после зависимостей",
            "source": "локальный пакет",
        }
    )
    return rows


def _shell_install_lines(rpm_path: Path | None) -> str:
    if rpm_path is not None:
        quoted = shlex.quote(str(rpm_path))
        return (
            "dnf install -y --setopt=timeout=300 --setopt=minrate=100 "
            "--setopt=retries=10 --setopt=skip_if_unavailable=True "
            f"--setopt=localpkg_gpgcheck=0 {quoted}"
        )
    root = "$ROOT"
    return f"""install -d /usr/share/cameav
rm -rf /usr/share/cameav/cameav
cp -a "{root}/cameav" /usr/share/cameav/cameav
find /usr/share/cameav -type d -name __pycache__ -print0 | xargs -0 -r rm -rf
install -D -m 755 "{root}/bin/cameav" /usr/bin/cameav
install -D -m 755 "{root}/bin/cameav-uninstall" /usr/bin/cameav-uninstall
install -D -m 755 "{root}/libexec/cameav-privileged" /usr/libexec/cameav-privileged
rm -rf /usr/share/cameav/installer
cp -a "{root}/installer" /usr/share/cameav/installer
find /usr/share/cameav/installer -type d -name __pycache__ -print0 | xargs -0 -r rm -rf
install -D -m 644 "{root}/share/applications/cameav.desktop" /usr/share/applications/cameav.desktop
install -D -m 644 "{root}/share/applications/cameav-uninstall.desktop" /usr/share/applications/cameav-uninstall.desktop
install -D -m 644 "{root}/share/polkit/ru.redos.cameav.policy" /usr/share/polkit-1/actions/ru.redos.cameav.policy
mkdir -p /usr/share/icons/hicolor
cp -a "{root}/share/icons/hicolor/." /usr/share/icons/hicolor/
update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true"""


def write_install_script(rpm_path: Path | None = None) -> Path:
    """Скрипт для pkexec: сначала ClamAV, и только потом оболочка."""
    root = shlex.quote(str(PROJECT_ROOT))
    body = f"""#!/bin/bash
set -euo pipefail
ROOT={root}
python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from cameav.runtime import install_missing; install_missing()' "$ROOT"
python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from cameav.update import write_channel_file; write_channel_file()' "$ROOT"
echo "ClamNTK: зависимости готовы, ставится оболочка"
{_shell_install_lines(rpm_path)}
echo "ClamNTK установлен"
"""
    path = Path(f"/tmp/clamntk-install-{os.getuid()}.sh")
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def cameav_registered() -> str | None:
    return installed_nevra("cameav")


def files_copied_without_package() -> bool:
    return cameav_registered() is None and Path("/usr/bin/cameav").is_file()


def shell_present() -> bool:
    return cameav_registered() is not None or Path("/usr/bin/cameav").is_file() or Path("/usr/share/cameav").is_dir()


def write_remove_script(*, clamav: bool = False, user_data: bool = False) -> Path:
    """Скрипт для pkexec: снять оболочку и только отмеченные части."""
    clamav_flag = "1" if clamav else "0"
    data_flag = "1" if user_data else "0"
    body = f"""#!/bin/bash
set -euo pipefail
if rpm -q cameav >/dev/null 2>&1; then
  dnf remove -y cameav
fi
rm -f /usr/bin/cameav /usr/bin/cameav-uninstall
rm -f /usr/libexec/cameav-privileged
rm -rf /usr/share/cameav
rm -f /usr/share/applications/cameav.desktop
rm -f /usr/share/applications/cameav-uninstall.desktop
rm -f /usr/share/polkit-1/actions/ru.redos.cameav.policy
rm -f /usr/share/icons/hicolor/scalable/apps/cameav.svg
find /usr/share/icons/hicolor -path '*/apps/cameav.png' -delete
update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
if [[ "{clamav_flag}" == 1 ]]; then
  if rpm -q clamav >/dev/null 2>&1; then
    dnf remove -y clamav
  fi
  rm -rf /usr/local/share/clamav
  rm -f /usr/local/etc/freshclam.conf /usr/local/etc/clamd.conf
  rm -f /usr/local/etc/freshclam.conf.sample /usr/local/etc/clamd.conf.sample
fi
if [[ "{data_flag}" == 1 ]]; then
  uid="${{PKEXEC_UID:-}}"
  if [[ -n "$uid" && "$uid" != 0 ]]; then
    home=$(getent passwd "$uid" | cut -d: -f6)
    case "$home" in
      /home/*|/root)
        rm -rf "$home/.config/cameav" "$home/.local/share/cameav"
        runtime="/run/user/${{uid}}/cameav"
        if [[ -f "$runtime/clamd.pid" ]]; then
          pid=$(tr -cd '0-9' < "$runtime/clamd.pid")
          if [[ -n "$pid" && -r "/proc/$pid/cmdline" ]] && tr '\\0' ' ' < "/proc/$pid/cmdline" | grep -q clamd; then
            kill "$pid" || true
          fi
        fi
        rm -rf "$runtime"
        ;;
    esac
  fi
fi
echo "ClamNTK снят"
"""
    path = Path(f"/tmp/clamntk-remove-{os.getuid()}.sh")
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)
    return path


def command_for(
    action: str,
    rpm_path: Path | None = None,
    *,
    remove_clamav: bool = False,
    remove_user_data: bool = False,
) -> list[str]:
    if action == "install":
        if source_tree_ready():
            command = ["bash", str(write_install_script(rpm_path))]
        elif rpm_path is not None:
            command = [
                "dnf",
                "install",
                "-y",
                "--setopt=localpkg_gpgcheck=0",
                str(rpm_path),
            ]
        else:
            raise RuntimeError("Не найдены файлы оболочки ClamNTK.")
    elif action == "remove":
        command = ["bash", str(write_remove_script(clamav=remove_clamav, user_data=remove_user_data))]
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
