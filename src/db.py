# src/db.py
"""
Прямое подключение к PostgreSQL DMC.

Через БД выполняются ЧТЕНИЯ (статус задания, размерности агрегации,
списки кодов) и ВЕРИФИКАЦИЯ кодов/агрегатов (UPDATE status=30) — это
точечные операции без серверной бизнес-логики. Официальные мутации
(смена статуса задания, сборка агрегатов) идут через REST API v2 —
см. api_client.py.
"""

import json
import psycopg2

from paths import DB_CONFIG_FILE

# Статус кода "Верифицирован (Сериализован)" — libs/dm_states.py DMC.
DM_STATUS_VERIFIED = 30

DEFAULT_DB_SETTINGS = {
    "host": "localhost",
    "port": "5432",
    "dbname": "",
    "user": "postgres",
    "password": "",
}


def _load_db_settings():
    settings = dict(DEFAULT_DB_SETTINGS)
    if DB_CONFIG_FILE.exists():
        try:
            with open(DB_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in DEFAULT_DB_SETTINGS:
                if key in data:
                    settings[key] = data[key]
        except Exception:
            pass
    return settings


_settings = _load_db_settings()


def get_db_settings():
    return dict(_settings)


def save_db_settings(host, port, dbname, user, password):
    global _settings
    _settings = {
        "host": host.strip(),
        "port": str(port).strip(),
        "dbname": dbname.strip(),
        "user": user.strip(),
        "password": password,
    }
    with open(DB_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_settings, f, ensure_ascii=False, indent=2)


def get_connection():
    return psycopg2.connect(
        host=_settings["host"],
        port=_settings["port"],
        dbname=_settings["dbname"],
        user=_settings["user"],
        password=_settings["password"],
        connect_timeout=5,
    )


def test_connection():
    """Пробное подключение с ТЕКУЩИМИ (сохранёнными) параметрами."""
    try:
        conn = get_connection()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def test_connection_with(host, port, dbname, user, password, timeout=5):
    """Пробное подключение с ПРОИЗВОЛЬНЫМИ параметрами (например, ещё не
    сохранёнными, которые пользователь только что ввёл в форму настроек).
    Не изменяет текущие активные настройки приложения."""
    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password,
            connect_timeout=timeout,
        )
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def _run(query, params, fetch, conn=None):
    """fetch: "all" | "one" | None. Для None (UPDATE) делает commit и
    возвращает rowcount."""
    own_conn = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch == "all":
                return cur.fetchall()
            elif fetch == "one":
                return cur.fetchone()
            rowcount = cur.rowcount
        conn.commit()
        return rowcount
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Задание
# ---------------------------------------------------------------------------

def fetch_available_taskids(conn=None):
    """Номера заданий, доступных для продвижения (статус 3 «готово к
    печати» или 6 «готово к сериализации») — для автодополнения в поле
    «Номер задания». Новые сверху."""
    rows = _run(
        "SELECT taskid FROM tasks WHERE status IN (3, 6) ORDER BY taskid DESC",
        None, "all", conn,
    )
    return [str(r[0]) for r in rows]


def fetch_task_row(taskid, conn=None):
    """(taskid, status, product_gtin, line_id, marking_system, amount,
    error_state, msg) или None, если задание не найдено."""
    return _run(
        "SELECT taskid, status, product, line, marking_system, amount, "
        "error_state, msg "
        "FROM tasks WHERE taskid = %s",
        (taskid,), "one", conn,
    )


def fetch_task_status(taskid, conn=None):
    """Текущий целочисленный статус задания или None."""
    row = _run("SELECT status FROM tasks WHERE taskid = %s", (taskid,), "one", conn)
    return row[0] if row else None


def fetch_task_state(taskid, conn=None):
    """(status, error_state, msg) задания или None — для поллинга:
    статус и признак серверной ошибки одним запросом."""
    return _run(
        "SELECT status, error_state, msg FROM tasks WHERE taskid = %s",
        (taskid,), "one", conn,
    )


def set_task_status(taskid, status, conn=None):
    """Прямая смена статуса задания в БД (минуя REST API).

    Используется для «финальных» переходов (8->9, 9->10, 12->13):
    PATCH через REST на некоторых стендах провоцирует сервер продвигать
    задание дальше целевого статуса, а прямой UPDATE — нет."""
    return _run(
        "UPDATE tasks SET status = %s WHERE taskid = %s",
        (status, taskid), None, conn,
    )


def fetch_task_dimensions(taskid, conn=None):
    """Уровни агрегации задания по возрастанию — этот порядок и есть
    порядок сборки (снизу вверх). Каждая строка:
    (level, aggr_id, size, aggr_gtin)."""
    return _run(
        "SELECT pa.level, pa.id AS aggr_id, pa.size, pa.aggr_gtin "
        "FROM tasks_dimensions td "
        "JOIN product_aggregates pa ON pa.id = td.aggr_id "
        "WHERE td.taskid = %s ORDER BY pa.level ASC",
        (taskid,), "all", conn,
    )


# ---------------------------------------------------------------------------
# Коды маркировки (dm)
# ---------------------------------------------------------------------------

def verify_all_dm(taskid, conn=None):
    """Верифицирует все КМ задания (status=30). Возвращает число
    обновлённых строк."""
    return _run(
        "UPDATE dm SET status = %s WHERE taskid = %s AND status <> %s",
        (DM_STATUS_VERIFIED, taskid, DM_STATUS_VERIFIED), None, conn,
    )


def fetch_dm_ids(taskid, conn=None):
    """Список кодов КМ задания (значения колонки dm) — используются как
    content при сборке агрегатов 1-го уровня, когда 0-й уровень без
    собственных кодов."""
    rows = _run(
        "SELECT dm FROM dm WHERE taskid = %s ORDER BY dm",
        (taskid,), "all", conn,
    )
    return [r[0] for r in rows]


def count_dm(taskid, conn=None):
    row = _run("SELECT count(*) FROM dm WHERE taskid = %s", (taskid,), "one", conn)
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Агрегаты
# ---------------------------------------------------------------------------

def fetch_aggregates_by_level(taskid, level, conn=None):
    """unit_id всех агрегатов задания на уровне (КИГУ предзарезервированы
    системой ещё на этапе WAIT_FOR_DM; КИТУ появляются после сборки)."""
    rows = _run(
        "SELECT unit_id FROM aggregates WHERE taskid = %s AND level = %s "
        "ORDER BY unit_id",
        (taskid, level), "all", conn,
    )
    return [r[0] for r in rows]


def assign_dm_to_level0(taskid, unit_ids, size, conn=None):
    """Привязывает КМ задания к агрегатам уровня 0 (КИГУ).

    У кода в dm поле-«родитель» называется aggregate; на реальной линии
    его заполняет Monitor при сборке. Для предзарезервированных КИГУ
    сборка не выполняется, поэтому раскладываем свободные КМ пачками по
    size в каждый unit_id сами. Идемпотентно: трогаем только КМ с
    aggregate IS NULL. Возвращает число привязанных КМ."""
    own_conn = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dm FROM dm WHERE taskid = %s AND aggregate IS NULL "
                "ORDER BY dm",
                (taskid,),
            )
            free_codes = [r[0] for r in cur.fetchall()]
            assigned = 0
            idx = 0
            for unit_id in unit_ids:
                if idx >= len(free_codes):
                    break
                chunk = free_codes[idx: idx + size]
                idx += len(chunk)
                cur.execute(
                    "UPDATE dm SET aggregate = %s WHERE dm = ANY(%s)",
                    (unit_id, chunk),
                )
                cur.execute(
                    "UPDATE aggregates SET content_count = content_count + %s "
                    "WHERE unit_id = %s",
                    (len(chunk), unit_id),
                )
                assigned += len(chunk)
        conn.commit()
        return assigned
    finally:
        if own_conn:
            conn.close()


def verify_aggregates(taskid, level, conn=None):
    """Верифицирует агрегаты задания на уровне (status=30). Возвращает
    число обновлённых строк."""
    return _run(
        "UPDATE aggregates SET status = %s "
        "WHERE taskid = %s AND level = %s AND status <> %s",
        (DM_STATUS_VERIFIED, taskid, level, DM_STATUS_VERIFIED), None, conn,
    )
