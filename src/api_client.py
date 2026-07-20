# src/api_client.py
"""
Взаимодействие с REST API v2 DMC для продвижения задания по статусам.

Все МУТИРУЮЩИЕ операции (PATCH статуса задания, POST сборка агрегата)
идут через официальный REST API — так же делает сам DMC Monitor при
работе на линии. Верификационные чтения и UPDATE статусов кодов идут
напрямую в БД через db.py — это точечные операции без серверной
бизнес-логики.

Код агрегата (КИТУ) генерируется локально по формуле штатного
"Стандартного генератора DMC" (libs/code_generators/sscc.py DMC):
уровень (2 цифры) + GTIN (14) + id линии (3) + дата ддммгг + 8
случайных символов. Формула чистая (без БД и сети), поэтому дергать
отдельный secondary-воркер API (порт 8026) не нужно.
"""

import json
import time
import random
import string
import requests
from datetime import datetime

from paths import LOG_DIR, API_CONFIG_FILE

LOG_FILE = str(LOG_DIR / "taskadvancer_log.txt")

# Максимальный размер content в одном вызове build_aggregate
# (build_aggregate_schema.py DMC).
BUILD_AGGREGATE_MAX_CONTENT = 512


def log(message):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} - {message}\n")


class DMCApiError(Exception):
    """Ошибка при обращении к REST API DMC."""
    pass


# ---------------------------------------------------------------------------
# Настройки подключения к REST API DMC (хранятся отдельно от настроек БД)
# ---------------------------------------------------------------------------

DEFAULT_API_SETTINGS = {
    "api_host": "localhost",
    "api_port": "8025",
}


def _load_api_settings():
    settings = dict(DEFAULT_API_SETTINGS)
    if API_CONFIG_FILE.exists():
        try:
            with open(API_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in DEFAULT_API_SETTINGS:
                if key in data:
                    settings[key] = data[key]
        except Exception:
            pass
    return settings


_api_settings = _load_api_settings()


def get_api_settings():
    """Текущие параметры REST API (для отображения в окне настроек)."""
    return dict(_api_settings)


def save_api_settings(api_host, api_port):
    """Сохраняет параметры REST API на диск и применяет сразу же."""
    global _api_settings
    _api_settings = {"api_host": api_host.strip(), "api_port": str(api_port).strip()}
    with open(API_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_api_settings, f, ensure_ascii=False, indent=2)


def test_api_connection(api_host, api_port, timeout=5):
    """Пробный запрос к REST API DMC (список линий) — используется кнопкой
    'Проверить API'. Возвращает (True, None) при успехе или (False, ошибка)."""
    try:
        url = f"http://{api_host}:{api_port}/api/v2/lines/?size=1&number=0"
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        data = resp.json()
        if not data.get("success", True):
            return False, f"API вернул success=false: {data}"
        return True, None
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Генерация кода агрегата (КИТУ)
# ---------------------------------------------------------------------------

def generate_dmc_code(level: int, gtin: str, line_id: int) -> str:
    """Код агрегата в формате штатного "Стандартного генератора DMC":
    уровень + GTIN + id линии + ддммгг + 8 случайных букв/цифр."""
    date_part = datetime.now().strftime("%d%m%y")
    random_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    gtin_digits = gtin[-14:] if len(gtin) >= 14 else gtin.zfill(14)
    return f"{level:02d}{gtin_digits}{int(line_id):03d}{date_part}{random_part}"


# ---------------------------------------------------------------------------
# REST-клиент
# ---------------------------------------------------------------------------

class TaskExecutor:
    """Мутирующие вызовы REST API v2 DMC (см. docstring модуля)."""

    def __init__(self):
        self.session = None

    def start(self):
        log("Открытие HTTP-сессии к REST API DMC...")
        self.session = requests.Session()

    def stop(self):
        if self.session:
            try:
                self.session.close()
                log("HTTP-сессия к API DMC закрыта")
            except Exception as e:
                log(f"Ошибка при закрытии сессии: {e}")
            finally:
                self.session = None

    def _base_url(self):
        s = get_api_settings()
        return f"http://{s['api_host']}:{s['api_port']}"

    def _request(self, method, path, payload=None):
        if self.session is None:
            raise RuntimeError("Сессия к API не открыта. Сначала вызовите start().")
        url = f"{self._base_url()}{path}"
        try:
            # (5, 20) — 5 секунд на установку соединения, до 20 секунд на ответ.
            resp = self.session.request(method, url, json=payload, timeout=(5, 20))
        except requests.RequestException as e:
            raise DMCApiError(f"Не удалось подключиться к API DMC ({url}): {e}")
        return self._parse_response(resp, url)

    @staticmethod
    def _parse_response(resp, url):
        try:
            data = resp.json()
        except Exception:
            raise DMCApiError(f"API DMC вернул не-JSON ответ ({url}), HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400 or not data.get("success", True):
            detail = data.get("data", data)
            raise DMCApiError(f"Ошибка API DMC ({url}), HTTP {resp.status_code}: {detail}")
        return data.get("data", data)

    # ------------------------------------------------------------------
    # Вызовы
    # ------------------------------------------------------------------

    def get_task(self, taskid):
        """GET /api/v2/tasks/{taskid} — чтение задания (для поллинга
        статуса тем же путём, каким пишется PATCH)."""
        return self._request("GET", f"/api/v2/tasks/{taskid}")

    def patch_task_status(self, taskid, status):
        """PATCH /api/v2/tasks/{taskid} {"status": N}. Сервер не
        валидирует переходы — так же меняет статус сам Monitor."""
        log(f"[#{taskid}] PATCH status={status}")
        return self._request("PATCH", f"/api/v2/tasks/{taskid}", {"status": status})

    def build_aggregate(self, level, parent_code, content):
        """POST /api/v2/build_aggregate/ {level, parent, content}.

        content должен УЖЕ существовать в БД: коды КМ (dm) для level=1,
        unit_id агрегатов уровня ниже для level>1 — поэтому сборка
        строго снизу вверх. Сервер сам создаёт запись родителя,
        привязывает потомков и обновляет счётчики задания."""
        if len(content) > BUILD_AGGREGATE_MAX_CONTENT:
            raise DMCApiError(
                f"build_aggregate: батч слишком большой "
                f"({len(content)} > {BUILD_AGGREGATE_MAX_CONTENT})"
            )
        log(f"build_aggregate: level={level} parent={parent_code} детей={len(content)}")
        return self._request("POST", "/api/v2/build_aggregate/", {
            "level": level,
            "parent": parent_code,
            "content": content,
        })
