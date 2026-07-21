# src/paths.py

import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):

        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def resource_path(*parts) -> Path:
    """Путь к бандлованному read-only ресурсу (иконка и т.п.).

    В отличие от BASE_DIR (папка рядом с exe, для пользовательских файлов),
    в onefile-сборке PyInstaller ресурсы из datas распаковываются во
    временную sys._MEIPASS, а не рядом с exe.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


BASE_DIR = get_base_dir()
LOG_DIR = BASE_DIR / "logs"
TASKS_FILE = BASE_DIR / "tasks.json"
DB_CONFIG_FILE = BASE_DIR / "db_config.json"
API_CONFIG_FILE = BASE_DIR / "api_config.json"
APP_CONFIG_FILE = BASE_DIR / "app_config.json"
UI_CONFIG_FILE = BASE_DIR / "ui_config.json"
ICON_FILE = resource_path("assets", "app.ico")

LOG_DIR.mkdir(exist_ok=True)
