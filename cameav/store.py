"""Настройки, журнал проверок и список карантина."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from cameav.backend import config_dir, data_dir, quarantine_dir

DEFAULT_CLEAN_CATEGORIES = [
    "trash",
    "temp",
    "thumbs",
    "browser",
    "appcache",
    "crashes",
]
CLEAN_CATEGORY_IDS = set(DEFAULT_CLEAN_CATEGORIES) | {"recent"}
DEFAULT_SETTINGS = {
    "auto_update": True,
    "scan_archives": True,
    "scan_mail": True,
    "detect_pua": False,
    "as_admin": False,
    "max_filesize_mb": 100,
    "exclude_dirs": [],
    "auto_clean": False,
    "clean_categories": list(DEFAULT_CLEAN_CATEGORIES),
    "last_clean_at": "",
    "last_clean_bytes": 0,
}
HISTORY_LIMIT = 80


class Store:
    def __init__(self) -> None:
        self.settings_path = config_dir() / "settings.json"
        self.history_path = data_dir() / "history.json"
        self.quarantine_path = data_dir() / "quarantine.json"
        self.update_log_path = data_dir() / "update.log"
        self.settings = self._load_settings()
        self.history = self._load_list(self.history_path)
        self.quarantine = self._load_quarantine()

    def _load_settings(self) -> dict:
        raw = self._read_json(self.settings_path, {})
        if not isinstance(raw, dict):
            raw = {}
        merged = dict(DEFAULT_SETTINGS)
        for key in DEFAULT_SETTINGS:
            if key in raw:
                merged[key] = raw[key]
        if not isinstance(merged["exclude_dirs"], list):
            merged["exclude_dirs"] = []
        merged["exclude_dirs"] = [str(item) for item in merged["exclude_dirs"] if str(item).strip()]
        try:
            merged["max_filesize_mb"] = max(1, min(2048, int(merged["max_filesize_mb"])))
        except (TypeError, ValueError):
            merged["max_filesize_mb"] = 100
        for key in ("auto_update", "scan_archives", "scan_mail", "detect_pua", "as_admin", "auto_clean"):
            merged[key] = bool(merged[key])
        chosen = merged.get("clean_categories")
        if not isinstance(chosen, list):
            chosen = list(DEFAULT_CLEAN_CATEGORIES)
        merged["clean_categories"] = [str(item) for item in chosen if str(item) in CLEAN_CATEGORY_IDS]
        merged["last_clean_at"] = str(merged.get("last_clean_at") or "")
        try:
            merged["last_clean_bytes"] = max(0, int(merged.get("last_clean_bytes") or 0))
        except (TypeError, ValueError):
            merged["last_clean_bytes"] = 0
        return merged

    def _load_list(self, path: Path) -> list:
        raw = self._read_json(path, [])
        return raw if isinstance(raw, list) else []

    def _load_quarantine(self) -> list:
        items = self._load_list(self.quarantine_path)
        known = {str(item.get("stored")) for item in items if isinstance(item, dict)}
        changed = False
        for file in sorted(quarantine_dir().iterdir(), key=lambda item: item.name):
            if not file.is_file() or file.name in known:
                continue
            items.append(
                {
                    "id": uuid.uuid4().hex,
                    "stored": file.name,
                    "original": "",
                    "name": file.name,
                    "virus": "не записано",
                    "time": "",
                    "size": file.stat().st_size,
                }
            )
            changed = True
        items = [item for item in items if isinstance(item, dict)]
        items = [item for item in items if (quarantine_dir() / str(item.get("stored", ""))).is_file()]
        if changed or len(items) != len(self._load_list(self.quarantine_path)):
            self._write_json(self.quarantine_path, items)
        return items

    @staticmethod
    def _read_json(path: Path, fallback):
        if not path.is_file():
            return fallback
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _write_json(path: Path, payload) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def save_settings(self) -> None:
        self._write_json(self.settings_path, self.settings)

    def add_history(self, entry: dict) -> None:
        self.history.insert(0, entry)
        del self.history[HISTORY_LIMIT:]
        self._write_json(self.history_path, self.history)

    def clear_history(self) -> None:
        self.history = []
        self._write_json(self.history_path, self.history)

    def add_quarantine(self, entry: dict) -> None:
        self.quarantine.insert(0, entry)
        self._write_json(self.quarantine_path, self.quarantine)

    def remove_quarantine(self, entry_id: str) -> None:
        self.quarantine = [item for item in self.quarantine if item.get("id") != entry_id]
        self._write_json(self.quarantine_path, self.quarantine)

    def write_update_log(self, text: str) -> None:
        self.update_log_path.write_text(text, encoding="utf-8")

    def read_update_log(self) -> str:
        if not self.update_log_path.is_file():
            return ""
        try:
            return self.update_log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
