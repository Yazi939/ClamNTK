"""Обнаружение ClamAV, разбор отчётов, карантин и служебные команды."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

APP_ID = "cameav"

MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
MONTHS_EN = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

CVD_RE = re.compile(r"^ClamAV-VDB:(?P<built>.+?):(?P<version>\d+):(?P<sigs>\d+):")
BUILT_RE = re.compile(
    r"(\d{1,2}) ([A-Za-z]{3}) (\d{4}) (\d{2})-(\d{2})(?:-(\d{2}))?\s*([+-]\d{4})?"
)
INFECTED_RE = re.compile(r"^(?P<path>.+): (?P<virus>.+) FOUND\s*$")
OK_RE = re.compile(r"^(?P<path>.+): OK\s*$")
ERROR_RE = re.compile(r"^(?P<path>.+): (?P<msg>.+) ERROR\s*$")
SUMMARY_KEYS = {
    "Known viruses": "known_viruses",
    "Engine version": "engine",
    "Scanned directories": "directories",
    "Scanned files": "files",
    "Infected files": "infected",
    "Data scanned": "data_scanned",
    "Time": "time",
}
PROTECTED_PREFIXES = (
    "/bin/",
    "/sbin/",
    "/lib/",
    "/lib64/",
    "/usr/bin/",
    "/usr/sbin/",
    "/usr/lib/",
    "/usr/lib64/",
    "/boot/",
    "/etc/",
    "/proc/",
    "/sys/",
    "/dev/",
)
ALLOWED_PACKAGES = {
    "clamav",
    "clamav-update",
    "clamav-data",
    "clamav-filesystem",
    "clamav-lib",
    "clamd",
    "python3",
    "python3-qt5",
    "python3-qt5-base",
    "polkit",
}
STALE_HOURS = 36


def format_dt(moment: datetime | None) -> str:
    if moment is None:
        return "нет данных"
    local = moment.astimezone()
    return f"{local.day} {MONTHS_RU[local.month - 1]} {local.year}, {local:%H:%M}"


def compact_count(value: int) -> str:
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}".replace(".", ",")
        return f"{text} млн"
    if value >= 10_000:
        return f"{round(value / 1_000)} тыс"
    return f"{value:,}".replace(",", " ")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:.1f}".replace(".", ",") + f" {unit}"
        value /= 1024
    return f"{size} Б"


def app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    path = root / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def quarantine_dir() -> Path:
    path = data_dir() / "quarantine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def which(name: str) -> str | None:
    for prefix in ("/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin"):
        candidate = Path(prefix) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def user_dirs() -> dict[str, Path]:
    mapping = {
        "DESKTOP": Path.home() / "Desktop",
        "DOWNLOAD": Path.home() / "Downloads",
        "DOCUMENTS": Path.home() / "Documents",
    }
    config = Path.home() / ".config" / "user-dirs.dirs"
    if not config.is_file():
        return mapping
    for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().removeprefix("XDG_").removesuffix("_DIR")
        value = value.strip().strip('"').replace("$HOME", str(Path.home()))
        if key in mapping:
            mapping[key] = Path(value)
    return mapping


def quick_targets() -> list[str]:
    folders = user_dirs()
    candidates = [
        folders.get("DESKTOP"),
        folders.get("DOWNLOAD"),
        folders.get("DOCUMENTS"),
        Path.home() / ".local" / "share" / "Trash",
        Path("/tmp"),
        Path("/var/tmp"),
    ]
    found: list[str] = []
    for path in candidates:
        if path and path.exists():
            found.append(str(path))
    return found


def helper_path() -> Path | None:
    candidates = (
        Path("/usr/libexec/cameav-privileged"),
        app_root() / "libexec" / "cameav-privileged",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _parse_built(text: str) -> datetime | None:
    match = BUILT_RE.search(text.strip())
    if not match:
        return None
    day, month_name, year, hour, minute, second, zone = match.groups()
    month = MONTHS_EN.get(month_name)
    if month is None:
        return None
    tzinfo = datetime.strptime(zone or "+0000", "%z").tzinfo
    return datetime(
        int(year),
        month,
        int(day),
        int(hour),
        int(minute),
        int(second or 0),
        tzinfo=tzinfo,
    )


@dataclass
class DatabaseInfo:
    name: str
    path: str
    version: str = ""
    signatures: int = 0
    built: datetime | None = None
    present: bool = False
    size: int = 0

    @property
    def age_hours(self) -> float | None:
        if self.built is None:
            return None
        delta = datetime.now(timezone.utc) - self.built.astimezone(timezone.utc)
        return delta.total_seconds() / 3600


@dataclass
class EngineInfo:
    clamscan: str | None
    freshclam: str | None
    database_dir: str | None
    config_file: str | None
    databases: list[DatabaseInfo] = field(default_factory=list)
    engine_version: str = ""

    @property
    def daily(self) -> DatabaseInfo | None:
        for database in self.databases:
            if database.name == "daily":
                return database
        return None

    @property
    def signature_count(self) -> int:
        return sum(item.signatures for item in self.databases if item.present)


def read_cvd(path: Path) -> DatabaseInfo:
    info = DatabaseInfo(name=path.name.split(".")[0], path=str(path))
    try:
        info.size = path.stat().st_size
        with path.open("rb") as handle:
            raw = handle.readline(8192).decode("ascii", "replace").strip()
    except OSError:
        return info
    match = CVD_RE.match(raw)
    if not match:
        return info
    info.present = True
    info.version = match.group("version")
    info.signatures = int(match.group("sigs"))
    info.built = _parse_built(match.group("built"))
    return info


def _database_dir_from_config(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2 and parts[0] == "DatabaseDirectory":
            return parts[1].strip().strip('"')
    return None


def discover_engine() -> EngineInfo:
    clamscan = which("clamscan")
    freshclam = which("freshclam")
    config_file = None
    database_dir = None
    for candidate in (
        "/usr/local/etc/freshclam.conf",
        "/etc/freshclam.conf",
        "/etc/clamav/freshclam.conf",
    ):
        path = Path(candidate)
        if not path.is_file():
            continue
        config_file = str(path)
        database_dir = _database_dir_from_config(path)
        if database_dir:
            break
    if not database_dir:
        for candidate in ("/usr/local/share/clamav", "/var/lib/clamav"):
            if Path(candidate).is_dir():
                database_dir = candidate
                break
    databases: list[DatabaseInfo] = []
    if database_dir:
        base = Path(database_dir)
        for name in ("main", "daily", "bytecode"):
            found = None
            for extension in (".cvd", ".cld"):
                candidate = base / f"{name}{extension}"
                if candidate.is_file():
                    found = read_cvd(candidate)
                    break
            databases.append(found or DatabaseInfo(name=name, path=str(base / f"{name}.cvd")))
    engine_version = ""
    if clamscan:
        try:
            output = subprocess.check_output(
                [clamscan, "--version"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=20,
                env={**os.environ, "LC_ALL": "C"},
            ).strip()
            engine_version = output.split("/")[0].replace("ClamAV", "", 1).strip()
        except (OSError, subprocess.SubprocessError):
            engine_version = ""
    return EngineInfo(
        clamscan=clamscan,
        freshclam=freshclam,
        database_dir=database_dir,
        config_file=config_file,
        databases=databases,
        engine_version=engine_version,
    )


_UP_TO_DATE = re.compile(
    r"(?P<name>daily|main|bytecode)\.cvd database is up-to-date \(version: (?P<version>\d+)"
)
_LOCAL_VERSION = re.compile(r"Local version:\s*([0-9.]+)")
_RECOMMENDED_VERSION = re.compile(r"Recommended version:\s*([0-9.]+)")
_PACKAGE_TITLES = {"daily": "ежедневные", "main": "основные", "bytecode": "bytecode"}


@dataclass
class FreshclamReport:
    ok: bool
    outdated: bool
    message: str


def explain_freshclam(text: str, code: int) -> FreshclamReport:
    """Перевести журнал freshclam: WARNING и номер пакета не означают сбой загрузки."""
    packages = [(match.group("name"), match.group("version")) for match in _UP_TO_DATE.finditer(text)]
    local = _LOCAL_VERSION.search(text)
    recommended = _RECOMMENDED_VERSION.search(text)
    lowered = text.lower()
    blocked = any(
        marker in lowered
        for marker in ("error code 429", "error code 403", "cool-down", "blocked by cdn", "rate limited")
    )
    downloaded = bool(packages) or "database updated" in lowered
    outdated = "outdated" in lowered
    if "сохранены из выпуска программы" in text:
        return FreshclamReport(
            ok=True,
            outdated=False,
            message=(
                "Сервер ClamAV поставил лимит CDN и не отдал базы напрямую. "
                "Пакеты сигнатур взяты из выпуска ClamNTK, их подпись проверена. Проверка может идти."
            ),
        )
    if "лимит CDN не мешает проверке" in text:
        return FreshclamReport(
            ok=True,
            outdated=False,
            message="Сервер ClamAV ограничил обновление. Уже загруженные пакеты на месте, проверка может идти.",
        )
    if blocked and not downloaded:
        return FreshclamReport(
            ok=False,
            outdated=outdated,
            message=(
                "Сервер баз ClamAV не отдал файлы и просит подождать. "
                "Это ограничение сети, в журнале оно выглядит как WARNING и код 403 или 429. "
                "Нажмите «Обновить базы» ещё раз не раньше чем через час, без повторных нажатий подряд."
            ),
        )
    if code not in (0, 1) and not downloaded:
        detail = ""
        for line in reversed([item.strip() for item in text.splitlines() if item.strip()]):
            if line.startswith(("WARNING:", "ERROR:")):
                detail = line
                break
        message = "Базы сигнатур не загрузились."
        if detail:
            message = message + "\n" + detail
        return FreshclamReport(ok=False, outdated=outdated, message=message)
    names = ", ".join(f"{_PACKAGE_TITLES.get(name, name)} {version}" for name, version in packages)
    if outdated:
        local_text = local.group(1) if local else "старая"
        recommended_text = recommended.group(1) if recommended else "более новая"
        bytecode = next((version for name, version in packages if name == "bytecode"), "")
        number = (
            f" Номер {bytecode} у bytecode — версия этого пакета, не код ошибки."
            if bytecode
            else " Число у bytecode — версия пакета, не код ошибки."
        )
        return FreshclamReport(
            ok=True,
            outdated=True,
            message=(
                f"Базы сигнатур на месте{': ' + names if names else ''}.{number} "
                f"WARNING в журнале значит другое: программа ClamAV версии {local_text}, "
                f"а сайт рекомендует {recommended_text}. Проверка этими базами уже может идти."
            ),
        )
    if names:
        return FreshclamReport(ok=True, outdated=False, message=f"Базы сигнатур обновлены: {names}.")
    return FreshclamReport(ok=code in (0, 1), outdated=False, message="Базы сигнатур обновлены.")


def database_state(engine: EngineInfo) -> str:
    if not engine.clamscan:
        return "missing_engine"
    daily = engine.daily
    if daily is None or not daily.present:
        return "missing_db"
    if daily.age_hours is None or daily.age_hours > STALE_HOURS:
        return "stale"
    return "fresh"


def database_writable(engine: EngineInfo) -> bool:
    if os.geteuid() == 0:
        return True
    if not engine.database_dir:
        return False
    return os.access(engine.database_dir, os.W_OK)


@dataclass
class ScanOptions:
    archives: bool = True
    mail: bool = True
    pua: bool = False
    max_filesize_mb: int = 100
    exclude_dirs: list[str] = field(default_factory=list)
    as_admin: bool = False


def exclude_regex(path: str) -> str:
    absolute = os.path.abspath(path)
    escaped = re.escape(absolute).replace(r"\-", "-")
    return f"^{escaped}(/|$)"


def default_excludes(engine: EngineInfo, options: ScanOptions) -> list[str]:
    found = ["/proc", "/sys", "/dev", "/run"]
    if engine.database_dir:
        found.append(engine.database_dir)
    found.append(str(quarantine_dir()))
    found.extend(options.exclude_dirs)
    unique: list[str] = []
    seen: set[str] = set()
    for directory in found:
        if not directory or directory in seen:
            continue
        seen.add(directory)
        unique.append(directory)
    return unique


def scan_flags(engine: EngineInfo, options: ScanOptions) -> list[str]:
    flags = [
        "--stdout",
        "--recursive=yes",
        f"--scan-archive={'yes' if options.archives else 'no'}",
        f"--scan-mail={'yes' if options.mail else 'no'}",
        f"--detect-pua={'yes' if options.pua else 'no'}",
        f"--max-filesize={max(1, int(options.max_filesize_mb))}M",
        "--max-scansize=400M",
    ]
    for directory in default_excludes(engine, options):
        flags.append(f"--exclude-dir={exclude_regex(directory)}")
    return flags


def admin_command(args: list[str]) -> list[str]:
    helper = helper_path()
    if helper is None:
        raise RuntimeError(
            "Не найден служебный компонент ClamNTK. Установите программу скриптом install.py."
        )
    if os.geteuid() == 0:
        return [str(helper), *args]
    pkexec = which("pkexec")
    if not pkexec:
        raise RuntimeError("Не найден pkexec. Установите пакет polkit.")
    return [pkexec, str(helper), *args]


def build_scan_command(engine: EngineInfo, targets: list[str], options: ScanOptions) -> list[str]:
    if not engine.clamscan:
        raise RuntimeError("Не найден clamscan. Установите пакет clamav.")
    if not targets:
        raise RuntimeError("Не выбран путь для проверки.")
    flags = scan_flags(engine, options)
    if options.as_admin and os.geteuid() != 0:
        return [*admin_command(["scan", *flags, "--", *targets])]
    command: list[str] = []
    stdbuf = which("stdbuf")
    if stdbuf:
        command.extend([stdbuf, "-oL", "-eL"])
    command.append(engine.clamscan)
    command.extend(flags)
    command.extend(targets)
    return command


def build_daemon_scan_command(config_path: Path, targets: list[str]) -> list[str]:
    clamdscan = which("clamdscan")
    if not clamdscan:
        raise RuntimeError("Не найден clamdscan.")
    command: list[str] = []
    stdbuf = which("stdbuf")
    if stdbuf:
        command.extend([stdbuf, "-oL", "-eL"])
    command.extend(
        [
            clamdscan,
            f"--config-file={config_path}",
            "--fdpass",
            "--multiscan",
            "--stdout",
            *targets,
        ]
    )
    return command


def helper_supports(command: str) -> bool:
    """Старый /usr/libexec/cameav-privileged мог остаться без новых команд."""
    path = helper_path()
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f'"{command}": cmd_{command}' in text


def build_engine_upgrade_command() -> list[str]:
    """Обновить программу ClamAV до рекомендуемой сборки.

    Если служебный helper уже умеет команду clamav — зовём его.
    Иначе ставим через скрипт с актуальным кодом из /usr/share/cameav:
    на части ПК helper остался от старой версии, а оболочка уже новая.
    """
    if helper_supports("clamav"):
        return admin_command(["clamav"])
    return _engine_upgrade_script_command()


def _engine_upgrade_script_command() -> list[str]:
    root = app_root()
    script = Path(f"/tmp/clamntk-engine-{os.getuid()}.sh")
    helper_src = root / "libexec" / "cameav-privileged"
    script.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                "set -euo pipefail",
                f'export PYTHONPATH="{root}${{PYTHONPATH:+:$PYTHONPATH}}"',
                "python3 - <<'PY'",
                "from cameav.runtime import install_clamav, prepare_clamav_runtime",
                "prepare_clamav_runtime()",
                "install_clamav()",
                'print("ClamNTK: программа ClamAV обновлена", flush=True)',
                "PY",
                f'src="{helper_src}"',
                'if [[ -f "$src" ]]; then',
                "  install -D -m 755 \"$src\" /usr/libexec/cameav-privileged",
                "fi",
                "",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o700)
    if os.geteuid() == 0:
        return ["bash", str(script)]
    pkexec = which("pkexec")
    if not pkexec:
        raise RuntimeError("Не найден pkexec. Установите пакет polkit.")
    return [pkexec, "bash", str(script)]


def build_update_command(engine: EngineInfo) -> list[str]:
    if not engine.freshclam:
        raise RuntimeError("Не найден freshclam. Установите пакет clamav.")
    if helper_path() is not None:
        update_args = ["update"]
        if engine.database_dir:
            update_args.extend(["--datadir", engine.database_dir])
        return admin_command(update_args)
    direct: list[str] = []
    stdbuf = which("stdbuf")
    if stdbuf:
        direct.extend([stdbuf, "-oL", "-eL"])
    direct.append(engine.freshclam)
    direct.append("--stdout")
    if engine.database_dir:
        direct.append(f"--datadir={engine.database_dir}")
    return direct


def build_install_command(packages: list[str]) -> list[str]:
    unknown = [package for package in packages if package not in ALLOWED_PACKAGES]
    if unknown:
        raise RuntimeError("Пакет не входит в список разрешённых: " + ", ".join(unknown))
    if not packages:
        raise RuntimeError("Не указаны пакеты.")
    if os.geteuid() == 0 and helper_path() is None:
        dnf = which("dnf")
        if not dnf:
            raise RuntimeError("Не найден dnf.")
        return [dnf, "install", "-y", *packages]
    return admin_command(["install", *packages])


@dataclass
class ScanState:
    in_summary: bool = False
    scanned: int = 0
    infected: int = 0
    errors: int = 0
    threats: list[dict[str, str]] = field(default_factory=list)
    summary: dict[str, str] = field(default_factory=dict)
    current_file: str = ""


def consume_scan_line(state: ScanState, line: str) -> str:
    """Разобрать одну строку clamscan. Вернуть тип события."""
    line = line.strip()
    if not line:
        return "ignore"
    if "SCAN SUMMARY" in line:
        state.in_summary = True
        return "summary"
    if state.in_summary:
        if ":" in line:
            key, value = line.split(":", 1)
            mapped = SUMMARY_KEYS.get(key.strip())
            if mapped:
                state.summary[mapped] = value.strip()
        return "summary"
    if line.startswith("LibClamAV"):
        return "ignore"
    infected = INFECTED_RE.match(line)
    if infected:
        state.scanned += 1
        state.infected += 1
        state.current_file = infected.group("path")
        state.threats.append(
            {"path": infected.group("path"), "virus": infected.group("virus")}
        )
        return "threat"
    ok = OK_RE.match(line)
    if ok:
        state.scanned += 1
        state.current_file = ok.group("path")
        return "ok"
    if "Failed to open file" in line or line.startswith("WARNING: Can't open"):
        state.scanned += 1
        state.errors += 1
        return "error"
    error = ERROR_RE.match(line)
    if error or line.startswith("ERROR:"):
        state.scanned += 1
        state.errors += 1
        return "error"
    return "ignore"


def final_counts(state: ScanState) -> tuple[int, int, int]:
    scanned = _first_int(state.summary.get("files"), state.scanned)
    infected = _first_int(state.summary.get("infected"), state.infected)
    return scanned, infected, state.errors


def _first_int(value: str | None, fallback: int) -> int:
    if not value:
        return fallback
    match = re.search(r"\d+", value)
    return int(match.group()) if match else fallback


def safe_filename(name: str) -> str:
    cleaned = []
    for char in name:
        if char.isalnum() or char in "._-":
            cleaned.append(char)
        else:
            cleaned.append("_")
    compact = "".join(cleaned).strip("._")[:80]
    return compact or "file"


def is_protected(path: Path) -> bool:
    resolved = str(path.resolve())
    for prefix in PROTECTED_PREFIXES:
        root = prefix.rstrip("/")
        if resolved == root or resolved.startswith(prefix):
            return True
    return False


def can_modify(path: Path) -> bool:
    return os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)


def _move_into_quarantine(src: Path, dest: Path) -> None:
    if is_protected(src):
        raise RuntimeError(f"Системный файл не изолируется: {src}")
    if src.is_symlink() or not src.is_file():
        raise RuntimeError(f"Можно изолировать только обычный файл: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    os.chmod(dest, 0o600)


def quarantine_locally(path: str, virus: str) -> dict:
    source = Path(path)
    stored = f"{uuid.uuid4().hex[:12]}_{safe_filename(source.name)}"
    destination = quarantine_dir() / stored
    _move_into_quarantine(source, destination)
    return {
        "id": uuid.uuid4().hex,
        "stored": stored,
        "original": str(source),
        "name": source.name,
        "virus": virus,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "size": destination.stat().st_size if destination.exists() else 0,
    }


def command_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env
