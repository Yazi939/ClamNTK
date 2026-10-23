"""Поиск мусора и карта каталогов. Удаляются только заранее известные кэши и выбранные папки."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from cameav.backend import human_size, is_protected

TEMP_MIN_AGE = 60 * 60
MAX_CHILDREN = 250
TOP_FILES = 40
SKIP_WALK = ("/proc", "/sys", "/dev", "/run")
BROWSER_CACHE_NAMES = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "ShaderCache",
    "GPUPersistentCache",
    "Tablo Cache",
}
CRASH_DIR_NAMES = {"Crash Reports", "BrowserMetrics", "DeferredBrowserMetrics"}
APP_CACHE_NAMES = (
    "fontconfig",
    "mesa_shader_cache",
    "mesa_shader_cache_db",
    "pip",
    "gstreamer-1.0",
)
CATEGORIES = (
    ("trash", "Корзина", "Файлы, которые уже отправлены в корзину"),
    ("temp", "Временные файлы", "Свои файлы в /tmp и /var/tmp старше часа"),
    ("thumbs", "Эскизы", "Кэш уменьшенных картинок рабочего стола"),
    ("browser", "Кэш браузеров", "Временные файлы Chromium, Chrome, Яндекса и Firefox. Пароли и закладки не трогаются"),
    ("appcache", "Кэш программ", "Кэш шрифтов, шейдеров, pip и gstreamer"),
    ("crashes", "Отчёты о сбоях", "Старые отчёты браузеров о падениях"),
    ("recent", "Недавние документы", "Список недавно открытых файлов"),
)


def category_title(category_id: str) -> str:
    for key, title, _detail in CATEGORIES:
        if key == category_id:
            return title
    return category_id


def collect_junk(category_ids: list[str], stop) -> dict[str, dict]:
    home = Path.home()
    found: dict[str, dict] = {}
    for category_id in category_ids:
        if stop():
            break
        hits = _collect_category(category_id, home, stop)
        found[category_id] = {
            "size": sum(item["size"] for item in hits),
            "count": len(hits),
            "hits": hits,
        }
    return found


def delete_junk(found: dict[str, dict], category_ids: list[str], stop) -> dict:
    freed = 0
    removed = 0
    errors: list[str] = []
    allowed = _allowed_roots()
    for category_id in category_ids:
        group = found.get(category_id) or {}
        for hit in group.get("hits") or []:
            if stop():
                return {"freed": freed, "removed": removed, "errors": errors, "cancelled": True}
            path = Path(hit["path"])
            if not _deletable(path, hit["kind"], allowed):
                errors.append(str(path))
                continue
            try:
                freed += _delete_hit(path, hit["kind"])
                removed += 1
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    return {"freed": freed, "removed": removed, "errors": errors, "cancelled": False}


def scan_disk(root: Path, stop, progress) -> dict:
    node = _empty_node(root.name or str(root), root)
    seen = {"files": 0}

    def walk(current: dict) -> None:
        if stop():
            return
        path = Path(current["path"])
        directories: list[dict] = []
        large: list[tuple[int, str, str]] = []
        small_size = 0
        small_count = 0
        file_count = 0
        try:
            entries = list(os.scandir(path))
        except OSError:
            entries = []
        for entry in entries:
            if stop():
                return
            if _skip_walk(entry.path) or entry.is_symlink():
                continue
            seen["files"] += 1
            if seen["files"] % 400 == 0:
                progress(seen["files"], entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    child = _empty_node(entry.name, Path(entry.path))
                    walk(child)
                    directories.append(child)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            file_count += 1
            if len(large) < TOP_FILES:
                large.append((size, entry.name, entry.path))
                large.sort()
            elif size > large[0][0]:
                small_size += large[0][0]
                small_count += 1
                large[0] = (size, entry.name, entry.path)
                large.sort()
            else:
                small_size += size
                small_count += 1
        children = directories
        for size, name, file_path in large:
            children.append(_file_node(name, file_path, size))
        if small_count and small_size:
            children.append(
                {
                    "name": "Мелкие файлы",
                    "path": "",
                    "size": small_size,
                    "files": small_count,
                    "kind": "rollup",
                    "children": [],
                }
            )
        children.sort(key=lambda item: item["size"], reverse=True)
        if len(children) > MAX_CHILDREN:
            rest = children[MAX_CHILDREN - 1 :]
            children = children[: MAX_CHILDREN - 1]
            children.append(
                {
                    "name": "Остальные",
                    "path": "",
                    "size": sum(item["size"] for item in rest),
                    "files": sum(int(item["files"]) for item in rest),
                    "kind": "rollup",
                    "children": [],
                }
            )
        current["children"] = children
        current["files"] = file_count + sum(int(item["files"]) for item in directories)
        current["size"] = sum(item["size"] for item in children)

    walk(node)
    progress(seen["files"], str(root))
    return node


def remove_from_tree(node: dict, target: str) -> int:
    children = node.get("children") or []
    for index, child in enumerate(children):
        if child.get("path") == target:
            size = int(child.get("size") or 0)
            del children[index]
            node["size"] = max(0, int(node.get("size") or 0) - size)
            return size
        if child.get("children"):
            size = remove_from_tree(child, target)
            if size:
                node["size"] = max(0, int(node.get("size") or 0) - size)
                return size
    return 0


def manual_delete_allowed(path: Path, root: Path) -> bool:
    if not path or not root:
        return False
    absolute = path.absolute()
    base = root.absolute()
    if absolute == base or absolute == Path.home().absolute() or absolute == Path("/"):
        return False
    try:
        absolute.relative_to(base)
    except ValueError:
        return False
    return not is_protected(absolute)


def remove_path(path: Path) -> int:
    if path.is_symlink():
        size = path.lstat().st_size
        path.unlink()
        return size
    if path.is_file():
        size = path.stat().st_size
        path.unlink()
        return size
    if not path.is_dir():
        path.unlink(missing_ok=True)
        return 0
    freed = 0
    for current, dirs, files in os.walk(path, topdown=False, followlinks=False, onerror=lambda _error: None):
        for name in files:
            freed += _unlink(Path(current) / name)
        for name in dirs:
            directory = Path(current) / name
            if directory.is_symlink():
                freed += _unlink(directory)
            else:
                try:
                    directory.rmdir()
                except OSError:
                    pass
    try:
        path.rmdir()
    except OSError:
        pass
    return freed


def _unlink(path: Path) -> int:
    try:
        size = path.lstat().st_size if path.is_symlink() or path.is_file() else 0
        path.unlink()
        return size
    except OSError:
        return 0


def _empty_node(name: str, path: Path) -> dict:
    return {
        "name": name,
        "path": str(path),
        "size": 0,
        "files": 0,
        "kind": "dir",
        "children": [],
    }


def _file_node(name: str, path: str, size: int) -> dict:
    return {"name": name, "path": path, "size": size, "files": 1, "kind": "file", "children": []}


def _skip_walk(path: str) -> bool:
    for prefix in SKIP_WALK:
        if path == prefix or path.startswith(prefix + os.sep):
            return True
    return False


def _collect_category(category_id: str, home: Path, stop) -> list[dict]:
    if category_id == "trash":
        return _content_hits(_trash_dirs(home), stop)
    if category_id == "temp":
        return _temp_hits(stop)
    if category_id == "thumbs":
        return _content_hits([home / ".cache" / "thumbnails"], stop)
    if category_id == "browser":
        return _content_hits(_browser_caches(home), stop)
    if category_id == "appcache":
        cache = home / ".cache"
        return _content_hits([cache / name for name in APP_CACHE_NAMES], stop)
    if category_id == "crashes":
        return _crash_hits(home, stop)
    if category_id == "recent":
        recent = home / ".local" / "share" / "recently-used.xbel"
        if recent.is_file():
            try:
                return [{"path": str(recent), "size": recent.stat().st_size, "kind": "file"}]
            except OSError:
                return []
    return []


def _trash_dirs(home: Path) -> list[Path]:
    root = home / ".local" / "share" / "Trash"
    return [root / name for name in ("files", "info", "expunged")]


def _content_hits(directories: list[Path], stop) -> list[dict]:
    hits = []
    for directory in directories:
        if stop() or not directory.is_dir():
            continue
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if stop():
                break
            if child.is_symlink():
                try:
                    hits.append({"path": str(child), "size": child.lstat().st_size, "kind": "file"})
                except OSError:
                    continue
                continue
            hits.append({"path": str(child), "size": _measure(child), "kind": "tree"})
    return [item for item in hits if item["size"] >= 0]


def _temp_hits(stop) -> list[dict]:
    cutoff = time.time() - TEMP_MIN_AGE
    uid = os.getuid()
    hits = []
    for root in (Path("/tmp"), Path("/var/tmp")):
        if stop() or not root.is_dir():
            continue
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if stop():
                break
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if info.st_uid != uid or info.st_mtime > cutoff:
                continue
            if entry.is_symlink():
                hits.append({"path": str(path), "size": info.st_size, "kind": "file"})
                continue
            if entry.is_file(follow_symlinks=False) and stat.S_ISREG(info.st_mode):
                hits.append({"path": str(path), "size": info.st_size, "kind": "file"})
                continue
            if entry.is_dir(follow_symlinks=False) and _tree_is_old(path, cutoff, uid):
                hits.append({"path": str(path), "size": _measure(path), "kind": "tree"})
    return hits


def _tree_is_old(path: Path, cutoff: float, uid: int) -> bool:
    for current, dirs, files in os.walk(path, followlinks=False, onerror=lambda _error: None):
        directory = Path(current)
        try:
            info = directory.lstat()
        except OSError:
            return False
        if info.st_uid != uid or info.st_mtime > cutoff:
            return False
        for name in files:
            file_path = directory / name
            try:
                file_info = file_path.lstat()
            except OSError:
                return False
            if file_info.st_uid != uid or file_info.st_mtime > cutoff:
                return False
        for name in list(dirs):
            child = directory / name
            if child.is_symlink():
                dirs.remove(name)
    return True


def _browser_roots(home: Path) -> list[Path]:
    names = (
        "chromium",
        "google-chrome",
        "yandex-browser",
        "microsoft-edge",
        "brave",
        "opera",
    )
    roots = []
    for name in names:
        roots.append(home / ".cache" / name)
        roots.append(home / ".config" / name)
    roots.append(home / ".cache" / "mozilla" / "firefox")
    roots.append(home / ".mozilla" / "firefox")
    return [path for path in roots if path.is_dir()]


def _browser_caches(home: Path) -> list[Path]:
    found: list[Path] = []
    for root in _browser_roots(home):
        if root.name == "firefox":
            for profile in _subdirs(root):
                cache = profile / "cache2"
                if cache.is_dir():
                    found.append(cache)
            continue
        found.extend(_named_dirs(root, BROWSER_CACHE_NAMES))
        for profile in _subdirs(root):
            found.extend(_named_dirs(profile, BROWSER_CACHE_NAMES))
            cache = profile / "Service Worker" / "CacheStorage"
            if cache.is_dir():
                found.append(cache)
    return _unique(found)


def _crash_hits(home: Path, stop) -> list[dict]:
    directories: list[Path] = []
    files: list[Path] = []
    for root in _browser_roots(home):
        if stop():
            break
        directories.extend(_named_dirs(root, CRASH_DIR_NAMES))
        for profile in _subdirs(root):
            directories.extend(_named_dirs(profile, CRASH_DIR_NAMES))
        try:
            for entry in root.iterdir():
                if entry.is_file() and _metrics_file(entry.name):
                    files.append(entry)
        except OSError:
            continue
    hits = _content_hits(directories, stop)
    for file_path in files:
        try:
            hits.append({"path": str(file_path), "size": file_path.stat().st_size, "kind": "file"})
        except OSError:
            continue
    return hits


def _metrics_file(name: str) -> bool:
    return name.startswith(("BrowserMetrics", "CrashpadMetrics")) and ".pma" in name


def _named_dirs(root: Path, names: set[str]) -> list[Path]:
    found = []
    for name in names:
        candidate = root / name
        if candidate.is_dir():
            found.append(candidate)
    return found


def _subdirs(root: Path) -> list[Path]:
    try:
        return [path for path in root.iterdir() if path.is_dir() and not path.is_symlink()]
    except OSError:
        return []


def _unique(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _measure(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for current, _dirs, files in os.walk(path, followlinks=False, onerror=lambda _error: None):
        for name in files:
            file_path = Path(current) / name
            try:
                total += file_path.lstat().st_size
            except OSError:
                continue
    return total


def _allowed_roots() -> list[Path]:
    home = Path.home()
    roots = [Path("/tmp"), Path("/var/tmp"), home / ".cache", home / ".config", home / ".mozilla", home / ".local" / "share" / "Trash"]
    recent = home / ".local" / "share" / "recently-used.xbel"
    return [path for path in roots if path.exists() or path == recent.parent]


def _deletable(path: Path, kind: str, allowed: list[Path]) -> bool:
    absolute = path.absolute()
    if absolute == Path("/") or absolute == Path.home().absolute() or is_protected(absolute):
        return False
    recent = Path.home() / ".local" / "share" / "recently-used.xbel"
    if absolute == recent.absolute() and kind == "file":
        return True
    if kind not in {"file", "tree"}:
        return False
    for root in allowed:
        try:
            absolute.relative_to(root.absolute())
        except ValueError:
            continue
        if absolute != root.absolute():
            return True
    return False


def _delete_hit(path: Path, kind: str) -> int:
    if kind == "file" or path.is_symlink() or path.is_file():
        size = path.lstat().st_size if path.exists() or path.is_symlink() else 0
        path.unlink()
        return size
    return remove_path(path)


class JunkScanWorker(QObject):
    progress = pyqtSignal(str)
    finished_scan = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, category_ids: list[str]) -> None:
        super().__init__()
        self.category_ids = category_ids
        self._stop = False

    @pyqtSlot()
    def run(self) -> None:
        try:
            def report(category_id: str) -> None:
                self.progress.emit(category_title(category_id))

            found = {}
            for category_id in self.category_ids:
                if self._stop:
                    break
                report(category_id)
                found.update(collect_junk([category_id], lambda: self._stop))
            self.finished_scan.emit(found)
        except Exception as exc:  # noqa: BLE001 - показать сбой поиска в окне
            self.failed.emit(str(exc))

    def stop(self) -> None:
        self._stop = True


class JunkDeleteWorker(QObject):
    progress = pyqtSignal(str)
    finished_delete = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, found: dict, category_ids: list[str]) -> None:
        super().__init__()
        self.found = found
        self.category_ids = category_ids
        self._stop = False

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = delete_junk(self.found, self.category_ids, lambda: self._stop)
            self.finished_delete.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def stop(self) -> None:
        self._stop = True


class DiskScanWorker(QObject):
    progress = pyqtSignal(int, str)
    finished_map = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, root: str) -> None:
        super().__init__()
        self.root = root
        self._stop = False

    @pyqtSlot()
    def run(self) -> None:
        previous = sys.getrecursionlimit()
        sys.setrecursionlimit(max(previous, 10000))
        try:
            node = scan_disk(Path(self.root), lambda: self._stop, lambda count, path: self.progress.emit(count, path))
            node["cancelled"] = self._stop
            self.finished_map.emit(node)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            sys.setrecursionlimit(previous)

    def stop(self) -> None:
        self._stop = True


class ManualDeleteWorker(QObject):
    finished_delete = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, paths: list[str], root: str) -> None:
        super().__init__()
        self.paths = paths
        self.root = root

    @pyqtSlot()
    def run(self) -> None:
        freed = 0
        removed: list[str] = []
        errors: list[str] = []
        base = Path(self.root)
        try:
            for raw in self.paths:
                path = Path(raw)
                if not manual_delete_allowed(path, base):
                    errors.append(str(path))
                    continue
                try:
                    freed += remove_path(path)
                    removed.append(str(path))
                except OSError as exc:
                    errors.append(f"{path}: {exc}")
            self.finished_delete.emit({"freed": freed, "removed": removed, "errors": errors})
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def describe_size(found: dict, category_id: str) -> str:
    group = found.get(category_id)
    if not group:
        return "—"
    if not group["count"]:
        return "пусто"
    return f"{human_size(int(group['size']))} · {group['count']}"
