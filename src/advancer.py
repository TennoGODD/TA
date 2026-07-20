# src/advancer.py
"""
Алгоритм продвижения задания DMC по статусам.

Последовательность (полная, цель 99):
  вход 3 -> PATCH 6 -> верификация КМ и агрегатов -> PATCH 8
  -> сервер DMC сам переводит 8 -> 9 (READY_FOR_APPLY)         [цель 9]
  -> PATCH 10 -> сервер шлёт отчёт о нанесении, сам 10->11->12  [цель 12]
  -> PATCH 13 -> сервер шлёт ввод в оборот, сам 13->...->99     [цель 99]

Переходы 10 и 13 запускают на сервере РЕАЛЬНУЮ отправку документов в
"Честный знак" — на стенде с демо-контуром ЧЗ это безопасно.

Сборка агрегатов — строго СНИЗУ ВВЕРХ: build_aggregate требует, чтобы
все дочерние коды уже существовали в БД (dm для уровня 0, aggregates
для уровней выше). КИГУ (уровень 0 с собственным GTIN) предзарезервированы
сервером ещё на этапе WAIT_FOR_DM — их только верифицируем; КИТУ и
паллеты собираем сами.
"""

import time

import db
from api_client import (
    BUILD_AGGREGATE_MAX_CONTENT, DMCApiError, TaskExecutor,
    generate_dmc_code, log,
)

# Статусы задания (libs/dmcObjects/tasks.py DMC)
STATUS_READY_TO_PRINT = 3           # Готово к печати
STATUS_READY_FOR_SERIALIZATION = 6  # Готово к сериализации
STATUS_DONE_SERIALIZING = 8         # Сериализация завершена
STATUS_READY_FOR_APPLY = 9          # Требуется отправить отчёт о нанесении
STATUS_SENT_FOR_APPLY = 10          # Отчёт о нанесении передан
STATUS_READY_FOR_INTRODUCE = 12     # Можно передать данные для ввода в оборот
STATUS_SENT_FOR_INTRODUCE = 13      # Данные о вводе в оборот переданы
STATUS_FINISHED = 99                # Завершено

ALLOWED_ENTRY_STATUSES = (STATUS_READY_TO_PRINT, STATUS_READY_FOR_SERIALIZATION)
# Подготовительные статусы (создание, новое, резерв КМ): задание ещё не
# готово к продвижению, но можно дождаться, пока сервер доведёт его до 3/6.
PREP_STATUSES = (0, 1, 2)
ALLOWED_TARGETS = (STATUS_READY_FOR_APPLY, STATUS_READY_FOR_INTRODUCE, STATUS_FINISHED)

# Сколько секунд задание может простоять в статусе 9 после нашего
# перевода в 10, прежде чем повторить перевод.
STUCK_RETRY_SECONDS = 5.0

# «Магистраль» статусов после завершения сериализации, в порядке
# прохождения (libs/dmcObjects/tasks.py DMC): нанесение -> ввод в
# оборот -> агрегаты -> списание -> экспорт/очистка -> завершено.
# Нужна, чтобы поллинг не завис, если сервер с включённой автоотправкой
# проскочил ожидаемый статус между опросами.
PIPELINE_AFTER_SERIALIZING = (
    9, 10, 11, 12, 13, 14, 15, 16, 17, 21, 22, 23, 60, 61, 62, 80, 90, 99,
)


def _statuses_at_or_after(status):
    """Множество статусов «магистрали», означающих, что status уже
    достигнут или пройден."""
    if status in PIPELINE_AFTER_SERIALIZING:
        idx = PIPELINE_AFTER_SERIALIZING.index(status)
        return set(PIPELINE_AFTER_SERIALIZING[idx:])
    return {status}

TARGET_LABELS = {
    STATUS_READY_FOR_APPLY: "Отчёт о нанесении (9)",
    STATUS_READY_FOR_INTRODUCE: "Ввод в оборот (12)",
    STATUS_FINISHED: "Завершено (99)",
}


class AdvanceError(Exception):
    """Ошибка продвижения задания (валидация, неожиданные данные)."""
    pass


class AdvanceStopped(Exception):
    """Продвижение остановлено пользователем."""
    pass


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def wait_until_ready(taskid, poll_interval=3.0, on_progress=None, stop_flag=None):
    """Ждёт, пока задание из подготовительного статуса (0/1/2) не
    перейдёт в 3 или 6 (сервер сам резервирует КМ и готовит задание).

    Возвращает достигнутый статус. Если задание пропало или оказалось в
    неожиданном статусе — AdvanceError."""

    def progress(phase):
        log(f"[#{taskid}] {phase}")
        if on_progress:
            on_progress(phase)

    progress("wait_ready")
    while True:
        if stop_flag is not None and stop_flag.is_set():
            raise AdvanceStopped("Остановлено пользователем")
        status = db.fetch_task_status(taskid)
        if status is None:
            raise AdvanceError(f"Задание #{taskid} не найдено в БД")
        if status in ALLOWED_ENTRY_STATUSES:
            progress(f"Задание готово (статус {status})")
            return status
        if status not in PREP_STATUSES:
            raise AdvanceError(
                f"Задание #{taskid} перешло в неожиданный статус {status} "
                f"(ожидались подготовительные 0/1/2 или готовность 3/6)"
            )
        time.sleep(poll_interval)


def advance_task(taskid, target_status, poll_interval=3.0,
                 on_progress=None, stop_flag=None):
    """Продвигает задание taskid до target_status (9 / 12 / 99).

    on_progress(phase: str) — обратная связь для GUI ("verify_dm",
    "build:level1", "patch:8", "poll:9", ...).
    stop_flag — threading.Event; если взведён, продвижение прерывается
    с AdvanceStopped.
    """

    def progress(phase):
        log(f"[#{taskid}] {phase}")
        if on_progress:
            on_progress(phase)

    def check_stop():
        if stop_flag is not None and stop_flag.is_set():
            raise AdvanceStopped("Остановлено пользователем")

    if target_status not in ALLOWED_TARGETS:
        raise AdvanceError(f"Недопустимая цель: {target_status} (ожидалось 9, 12 или 99)")

    # ---- Шаг 0. Валидация входа ----
    progress("Проверка задания")
    row = db.fetch_task_row(taskid)
    if row is None:
        raise AdvanceError(f"Задание #{taskid} не найдено в БД")
    _, entry_status, product_gtin, line_id, marking_system, amount = row
    if entry_status not in ALLOWED_ENTRY_STATUSES:
        raise AdvanceError(
            f"Задание #{taskid} в статусе {entry_status}, "
            f"ожидался {STATUS_READY_TO_PRINT} (готово к печати) или "
            f"{STATUS_READY_FOR_SERIALIZATION} (готово к сериализации)"
        )

    api = TaskExecutor()
    api.start()
    try:
        # ---- Шаг 1. Верификация всех КМ ----
        check_stop()
        progress("verify_dm")
        updated = db.verify_all_dm(taskid)
        progress(f"Верифицировано КМ: {updated}")

        # ---- Шаги 2-3. Агрегация (снизу вверх) ----
        check_stop()
        dims = db.fetch_task_dimensions(taskid)
        prev_level_ids = []
        for level, aggr_id, size, aggr_gtin in dims:
            check_stop()
            existing = db.fetch_aggregates_by_level(taskid, level)
            if existing:
                # Уровень уже с кодами (КИГУ предзарезервированы сервером,
                # либо повторный запуск после частичного прогона) —
                # верифицируем и, для уровня 0, привязываем КМ к агрегатам
                # (dm.aggregate — «parent_id» кода; на реальной линии эту
                # привязку делает Monitor при сборке).
                progress(f"verify_aggregates:level{level} ({len(existing)} шт.)")
                db.verify_aggregates(taskid, level)
                if level == 0 and size > 0:
                    assigned = db.assign_dm_to_level0(taskid, existing, size)
                    if assigned:
                        progress(f"Привязано КМ к агрегатам ур.0: {assigned}")
                prev_level_ids = existing
                continue

            # Кодов уровня нет — собираем через build_aggregate.
            # Дети: агрегаты уровня ниже, а если их нет — сами КМ.
            children = prev_level_ids or db.fetch_dm_ids(taskid)
            if not children:
                raise AdvanceError(
                    f"Уровень {level}: нет дочерних кодов для сборки "
                    f"(ни агрегатов ниже, ни КМ задания)"
                )
            if size <= 0:
                raise AdvanceError(f"Уровень {level}: некорректный size={size}")
            chunk_size = min(size, BUILD_AGGREGATE_MAX_CONTENT)
            code_gtin = aggr_gtin or product_gtin
            progress(f"build:level{level} ({len(children)} детей по {chunk_size})")
            for chunk in _chunked(children, chunk_size):
                check_stop()
                parent_code = generate_dmc_code(level, code_gtin, line_id)
                api.build_aggregate(level, parent_code, chunk)
            db.verify_aggregates(taskid, level)
            prev_level_ids = db.fetch_aggregates_by_level(taskid, level)
            progress(f"build:level{level} готово ({len(prev_level_ids)} агрегатов)")

        # ---- Шаг 4. Продвижение по статусам ----
        def poll_until(expected_status, phase, retry_from=None, retry_push=None):
            """Ждёт, пока задание не окажется в expected_status ИЛИ дальше
            него по магистрали. Сервер с включённой автоотправкой может
            проскочить ожидаемый статус между опросами (например, 9 -> 10
            автоматически) — это не ошибка, продвижение состоялось.

            retry_from/retry_push: если задание застряло в retry_from
            дольше STUCK_RETRY_SECONDS (например, откатилось в 9 после
            нашего перевода в 10) — повторяем перевод в retry_push.
            Возвращает фактический статус."""
            progress(f"poll:{expected_status} ({phase})")
            accepted = _statuses_at_or_after(expected_status)
            stuck_since = None
            while True:
                check_stop()
                current = db.fetch_task_status(taskid)
                if current in accepted:
                    if current != expected_status:
                        progress(
                            f"Статус {current} — сервер прошёл {expected_status} "
                            f"автоматически (автоотправка)"
                        )
                    return current
                if retry_from is not None and current == retry_from:
                    now = time.monotonic()
                    if stuck_since is None:
                        stuck_since = now
                    elif now - stuck_since >= STUCK_RETRY_SECONDS:
                        progress(
                            f"patch:{retry_push} (повтор — задание застряло "
                            f"в статусе {retry_from})"
                        )
                        api.patch_task_status(taskid, retry_push)
                        stuck_since = None
                else:
                    stuck_since = None
                time.sleep(poll_interval)

        check_stop()
        if entry_status == STATUS_READY_TO_PRINT:
            progress("patch:6")
            api.patch_task_status(taskid, STATUS_READY_FOR_SERIALIZATION)

        progress("patch:8")
        api.patch_task_status(taskid, STATUS_DONE_SERIALIZING)
        # Даём серверу один интервал на обработку статуса 8 (пересчёт
        # счётчиков и т.п.); если он сам не продвинул задание дальше —
        # переводим в 9 самостоятельно через API.
        time.sleep(poll_interval)
        check_stop()
        current = db.fetch_task_status(taskid)
        if current == STATUS_DONE_SERIALIZING:
            progress("patch:9")
            api.patch_task_status(taskid, STATUS_READY_FOR_APPLY)
        current = poll_until(STATUS_READY_FOR_APPLY, "подтверждение статуса 9")
        if target_status == STATUS_READY_FOR_APPLY:
            progress("Цель достигнута: 9")
            return

        # Переводим в 10 только если сервер ещё не ушёл дальше сам.
        if current == STATUS_READY_FOR_APPLY:
            progress("patch:10")
            api.patch_task_status(taskid, STATUS_SENT_FOR_APPLY)
        # Сервер отправляет отчёт о нанесении в ЧЗ и сам ведёт 10 -> 11 -> 12.
        # Если задание застревает в 9 (перевод не подхватился или сервер
        # откатил) — повторяем перевод в 10.
        current = poll_until(
            STATUS_READY_FOR_INTRODUCE, "ЧЗ обрабатывает отчёт о нанесении",
            retry_from=STATUS_READY_FOR_APPLY, retry_push=STATUS_SENT_FOR_APPLY,
        )
        if target_status == STATUS_READY_FOR_INTRODUCE:
            progress("Цель достигнута: 12")
            return

        if current == STATUS_READY_FOR_INTRODUCE:
            progress("patch:13")
            api.patch_task_status(taskid, STATUS_SENT_FOR_INTRODUCE)
        # Сервер отправляет ввод в оборот и сам ведёт 13 -> ... -> 99
        # (включая агрегационные отчёты, списание и очистку).
        poll_until(STATUS_FINISHED, "ЧЗ обрабатывает ввод в оборот и агрегаты")
        progress("Цель достигнута: 99")
    finally:
        api.stop()
