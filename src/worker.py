# src/worker.py
"""
Фоновый воркер одного задания.

Каждое отслеживаемое задание живёт в собственном daemon-потоке и
независимо проходит алгоритм advancer.advance_task. Ошибка одного
воркера никогда не роняет приложение и не влияет на соседние задания.

Воркеры не трогают виджеты напрямую — обратная связь идёт через
колбэк on_update(taskid, state, phase, error), который GUI обязан
маршалить в главный поток через self.after(0, ...).
"""

import threading

from advancer import AdvanceError, AdvanceStopped, advance_task, wait_until_ready
from api_client import DMCApiError, log


class TaskState:
    IDLE = "idle"          # добавлено, ещё не запускалось
    RUNNING = "running"    # активная фаза (верификация, сборка, PATCH)
    WAITING = "waiting"    # поллинг — ждём, пока сервер DMC продвинет задание
    ERROR = "error"        # ошибка (можно повторить)
    STOPPED = "stopped"    # остановлено пользователем
    DONE = "done"          # целевой статус достигнут


class TaskWorker:
    def __init__(self, taskid, target_status, poll_interval, on_update,
                 wait_for_ready=False):
        self.taskid = taskid
        self.target_status = target_status
        self.poll_interval = poll_interval
        self.wait_for_ready = wait_for_ready
        self.state = TaskState.IDLE
        self.phase = ""
        self.error_message = None
        self.stop_flag = threading.Event()
        self._on_update = on_update
        self._thread = None

    @property
    def is_active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_active:
            return
        self.stop_flag.clear()
        self.error_message = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_flag.set()

    def retry(self):
        self.start()

    def _run(self):
        self._set_state(TaskState.RUNNING, "Запуск")
        try:
            if self.wait_for_ready:
                wait_until_ready(
                    self.taskid,
                    poll_interval=self.poll_interval,
                    on_progress=self._on_progress,
                    stop_flag=self.stop_flag,
                )
            advance_task(
                self.taskid, self.target_status,
                poll_interval=self.poll_interval,
                on_progress=self._on_progress,
                stop_flag=self.stop_flag,
            )
            self._set_state(TaskState.DONE, "Цель достигнута")
        except AdvanceStopped:
            self._set_state(TaskState.STOPPED, "Остановлено")
        except (AdvanceError, DMCApiError) as e:
            self.error_message = str(e)
            self._set_state(TaskState.ERROR, "Ошибка")
        except Exception as e:
            # Никогда не даём воркеру уронить приложение.
            log(f"[#{self.taskid}] Непредвиденная ошибка: {e}")
            self.error_message = f"Непредвиденная ошибка: {e}"
            self._set_state(TaskState.ERROR, "Ошибка")

    def _on_progress(self, phase):
        waiting = phase.startswith("poll:") or phase.startswith("wait_ready")
        state = TaskState.WAITING if waiting else TaskState.RUNNING
        self._set_state(state, phase)

    def _set_state(self, state, phase):
        self.state = state
        self.phase = phase
        try:
            self._on_update(self.taskid, state, phase, self.error_message)
        except Exception as e:
            log(f"[#{self.taskid}] Ошибка колбэка GUI: {e}")
