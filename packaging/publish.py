#!/usr/bin/env python3
"""Публикация пользовательского выпуска из рабочей копии.

На другом компьютере:

    git clone -b dev https://github.com/Yazi939/ClameNTK.git
    cd ClameNTK
    python3 packaging/publish.py          # поднять последнюю цифру версии
    python3 packaging/publish.py 1.2.0    # поставить номер явно

Команда обновляет версию, собирает RPM, отправляет ветку dev и ветку main
и создаёт выпуск GitHub с пакетом для установки.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "cameav" / "__init__.py"
SPEC = ROOT / "packaging" / "cameav.spec"
sys.path.insert(0, str(ROOT))

from cameav.update import DEV_BRANCH, USER_BRANCH, parse_version  # noqa: E402


def read_version() -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', INIT.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit("В cameav/__init__.py нет версии.")
    return match.group(1)


def write_version(version: str) -> None:
    text = INIT.read_text(encoding="utf-8")
    updated = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', text, count=1)
    INIT.write_text(updated, encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    spec = re.sub(r"^Version:\s+\S+", f"Version:        {version}", spec, count=1, flags=re.M)
    today = datetime.date.today().strftime("%a %b %d %Y")
    entry = (
        f"* {today} ClameNTK <cameav@localhost> - {version}-1\n"
        f"- Выпуск ClameNTK {version}\n"
    )
    spec = spec.replace("%changelog\n", "%changelog\n" + entry, 1)
    SPEC.write_text(spec, encoding="utf-8")


def next_version(current: str) -> str:
    parts = list(parse_version(current))
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(part) for part in parts[:3])


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=check)


def ensure_dev_branch() -> None:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != DEV_BRANCH:
        raise SystemExit(f"Публиковать нужно из ветки {DEV_BRANCH}. Сейчас открыта {branch}.")
    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        raise SystemExit("В рабочей копии есть несохранённые правки. Сначала закоммитьте их.")


def build_rpm(version: str) -> Path:
    if shutil.which("rpmbuild") is None:
        raise SystemExit("Не найден rpmbuild. Установите пакет rpm-build.")
    top = ROOT / "build" / "rpm"
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        (top / name).mkdir(parents=True, exist_ok=True)
    dist = subprocess.run(["rpm", "--eval", "%{dist}"], text=True, capture_output=True, check=False)
    dist_tag = (dist.stdout or ".red80").strip() or ".red80"
    subprocess.check_call(
        [
            "rpmbuild",
            "-bb",
            str(SPEC),
            "--define",
            f"cameav_root {ROOT}",
            "--define",
            f"_topdir {top}",
            "--define",
            f"dist {dist_tag}",
        ],
        cwd=ROOT,
    )
    found = list((top / "RPMS").rglob(f"cameav-{version}-*.rpm"))
    if not found:
        raise SystemExit("rpmbuild не создал пакет.")
    package = found[0]
    dist_dir = ROOT / "dist"
    dist_dir.mkdir(exist_ok=True)
    target = dist_dir / package.name
    shutil.copy2(package, target)
    return target


def publish(version: str, package: Path) -> None:
    git("add", "cameav/__init__.py", "packaging/cameav.spec")
    git("commit", "-m", f"Выпуск ClameNTK {version}")
    git("tag", "-a", f"v{version}", "-m", f"ClameNTK {version}")
    git("push", "origin", DEV_BRANCH)
    git("push", "origin", f"HEAD:{USER_BRANCH}")
    git("push", "origin", f"v{version}")
    if shutil.which("gh") is None:
        raise SystemExit(f"Пакет {package} собран, но нет команды gh, чтобы создать выпуск.")
    subprocess.check_call(
        [
            "gh",
            "release",
            "create",
            f"v{version}",
            str(package),
            "--repo",
            "Yazi939/ClameNTK",
            "--target",
            USER_BRANCH,
            "--title",
            f"ClameNTK {version}",
            "--notes",
            f"Пользовательская сборка {version} для установки на РЕД ОС.",
        ],
        cwd=ROOT,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Собрать и опубликовать пользовательский выпуск ClameNTK")
    parser.add_argument("version", nargs="?", help="Номер версии, например 1.2.0")
    parser.add_argument("--build-only", action="store_true", help="Только собрать RPM, без GitHub")
    args = parser.parse_args()
    os.chdir(ROOT)
    if not args.build_only:
        ensure_dev_branch()
    version = args.version or next_version(read_version())
    if parse_version(version) <= parse_version(read_version()) and args.version:
        raise SystemExit(f"Версия {version} не новее текущей {read_version()}.")
    write_version(version)
    package = build_rpm(version)
    print(f"Собран {package}")
    if args.build_only:
        return 0
    publish(version, package)
    print(f"Выпуск v{version} опубликован. Пользователи увидят его в разделе «Обновления».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
