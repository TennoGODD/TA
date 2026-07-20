# src/storage.py
"""
Персистентность списка отслеживаемых заданий и настроек поведения.

tasks.json — список словарей:
  {"taskid": int, "target_status": int, "added_at": iso, "reached_at": iso|null,
   "state": str}
Хранится только членство в списке и итог; живое состояние воркера
(текущая фаза, текст ошибки) — в памяти, не на диске.

app_config.json — настройки поведения (интервал опроса, звук).
"""

import json

from paths import TASKS_FILE, APP_CONFIG_FILE

DEFAULT_APP_SETTINGS = {
    "poll_interval": 3,
    "sound_enabled": True,
}


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        if isinstance(items, list):
            return [it for it in items if isinstance(it, dict) and "taskid" in it]
    except Exception:
        pass
    return []


def save_tasks(tasks):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def _load_app_settings():
    settings = dict(DEFAULT_APP_SETTINGS)
    if APP_CONFIG_FILE.exists():
        try:
            with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in DEFAULT_APP_SETTINGS:
                if key in data:
                    settings[key] = data[key]
        except Exception:
            pass
    return settings


_app_settings = _load_app_settings()


def get_app_settings():
    return dict(_app_settings)


def save_app_settings(poll_interval, sound_enabled):
    global _app_settings
    _app_settings = {
        "poll_interval": max(1, int(poll_interval)),
        "sound_enabled": bool(sound_enabled),
    }
    with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_app_settings, f, ensure_ascii=False, indent=2)
