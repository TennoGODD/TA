# src/gui.py

import os
import sys
import json
import subprocess
import threading
from datetime import datetime

import customtkinter as ctk

import db
import theme as t
import storage
import notifications
from paths import TASKS_FILE, LOG_DIR, UI_CONFIG_FILE
from api_client import (
    LOG_FILE, get_api_settings, save_api_settings, test_api_connection,
)
from worker import TaskState, TaskWorker
from widgets import (
    CTkAutocompleteEntry, enable_clipboard_paste, set_window_icon,
    show_error, show_info, ask_yesno,
)

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

TASKS_FILE = str(TASKS_FILE)

SIDEBAR_EXPANDED_WIDTH = 230
SIDEBAR_COLLAPSED_WIDTH = 48


def _load_sidebar_collapsed() -> bool:
    if UI_CONFIG_FILE.exists():
        try:
            data = json.loads(UI_CONFIG_FILE.read_text(encoding="utf-8"))
            return bool(data.get("sidebar_collapsed", False))
        except Exception:
            pass
    return False


def _save_sidebar_collapsed(value: bool) -> None:
    try:
        UI_CONFIG_FILE.write_text(
            json.dumps({"sidebar_collapsed": value}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

# Подписи целей в выпадающем списке (описания статусов из DMC)
TARGET_CHOICES = {
    "Требуется отправить отчёт о нанесении КМ (9)": 9,
    "Можно передать данные для ввода в оборот (12)": 12,
    "Завершено (99)": 99,
}
# Короткие подписи для бейджа на карточке и уведомлений
TARGET_BY_STATUS = {
    9: "статус 9 — отчёт о нанесении",
    12: "статус 12 — ввод в оборот",
    99: "статус 99 — завершено",
}

STATE_COLORS = {
    TaskState.IDLE: t.COLOR_TEXT_MUTED,
    TaskState.RUNNING: t.COLOR_ACCENT,
    TaskState.WAITING: t.COLOR_CYAN,
    TaskState.ERROR: t.COLOR_RED,
    TaskState.STOPPED: t.COLOR_ORANGE,
    TaskState.DONE: t.COLOR_GREEN,
}

STATE_LABELS = {
    TaskState.IDLE: "Ожидает запуска",
    TaskState.RUNNING: "Выполняется",
    TaskState.WAITING: "Ждём сервер DMC",
    TaskState.ERROR: "Ошибка",
    TaskState.STOPPED: "Остановлено",
    TaskState.DONE: "Готово",
}


def _phase_text(phase):
    """Человекочитаемое описание фазы воркера."""
    if not phase:
        return ""
    if phase.startswith("wait_ready"):
        return "Ожидание готовности задания (статус 3/6)..."
    if phase.startswith("verify_dm"):
        return "Верификация КМ..."
    if phase.startswith("verify_aggregates:level"):
        rest = phase.split(":", 1)[1]
        return f"Верификация агрегатов ({rest})"
    if phase.startswith("build:level"):
        return f"Сборка агрегатов ({phase.split(':', 1)[1]})"
    if phase.startswith("patch:"):
        return f"Перевод в статус {phase.split(':', 1)[1]} (API)"
    if phase.startswith("sql:"):
        return f"Перевод в статус {phase.split(':', 1)[1]} (БД)"
    if phase.startswith("poll:"):
        rest = phase.split(":", 1)[1]
        if "(" in rest:
            num, note = rest.split("(", 1)
            return f"Ожидание статуса {num.strip()} — {note.rstrip(')')}"
        return f"Ожидание статуса {rest}"
    return phase


class TaskAdvancerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Task Advancer")
        self.geometry("1320x920")
        self.minsize(1100, 760)
        self.configure(fg_color=t.COLOR_BG)
        set_window_icon(self)

        # ---- состояние ----
        self.tasks = {}        # taskid -> запись {taskid, target_status, added_at, reached_at, state}
        self.workers = {}      # taskid -> TaskWorker
        self.row_frames = {}   # taskid -> CTkFrame (карточка)
        self.row_widgets = {}  # taskid -> {"dot", "state_var", "phase_var", "buttons_frame"}
        self.available_taskids = []  # задания в статусе 3/6 для автодополнения

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_area()

        self.after(150, self.auto_load_tasks)
        self.after(50, self._async_reload_taskids)
        self.after(200, self._check_db_api_on_startup)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.bind("<Unmap>", self._on_main_window_state_changed)
        self.bind("<FocusOut>", self._on_main_window_state_changed)

        for delay in (0, 150, 400, 900):
            self.after(delay, self._maximize_window)

    def _on_main_window_state_changed(self, event):
        if event.widget is self:
            CTkAutocompleteEntry.hide_all()

    def _maximize_window(self):
        try:
            self.state("zoomed")  # Windows
        except Exception:
            try:
                self.attributes("-zoomed", True)  # некоторые Linux-DE
            except Exception:
                pass

    def _setup_modal_dialog(self, win, width, height):
        win.geometry(f"{width}x{height}")
        win.minsize(width, height)
        set_window_icon(win)
        win.grab_set()

        state = {"warned_at": 0}

        def notice(text):
            import time as _time
            now = _time.time()
            if now - state["warned_at"] < 1.2:
                return
            state["warned_at"] = now
            try:
                win.bell()
            except Exception:
                pass
            label = ctk.CTkLabel(
                win, text=text, fg_color=t.COLOR_RED, text_color="#1c1013",
                corner_radius=8, font=t.FONT_LABEL,
            )
            label.place(relx=0.5, rely=0.03, anchor="n")
            win.after(1800, lambda: label.destroy() if label.winfo_exists() else None)

        def on_focus_out(event):
            if event.widget is not win:
                return

            def check():
                if not win.winfo_exists():
                    return
                focused = win.focus_get()
                if focused is None or str(focused).find(str(win)) != 0:
                    win.lift()
                    win.focus_force()
                    notice("Сначала закройте это окно, чтобы вернуться к основному")

            win.after(120, check)

        def on_unmap(event):
            if event.widget is not win:
                return

            def restore():
                if not win.winfo_exists():
                    return
                win.deiconify()
                win.lift()
                win.focus_force()
                notice("Это окно нельзя свернуть, пока оно открыто")

            win.after(10, restore)

        win.bind("<FocusOut>", on_focus_out)
        win.bind("<Unmap>", on_unmap)

    # ======================================================================
    # Сайдбар
    # ======================================================================
    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=SIDEBAR_EXPANDED_WIDTH, corner_radius=0,
                                fg_color=t.COLOR_BG_SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nswe")
        sidebar.grid_propagate(False)
        self.sidebar = sidebar

        # Кнопка-стрелка сворачивания — единственное, что остаётся видимым
        # в свёрнутой узкой полоске; пакуется ДО sidebar_body, поэтому
        # переживает скрытие содержимого.
        self.sidebar_toggle_btn = ctk.CTkButton(
            sidebar, text="◀", width=28, height=28, font=t.FONT_LABEL,
            command=self.toggle_sidebar, **t.ghost_button_style(),
        )
        self.sidebar_toggle_btn.pack(pady=(14, 4))

        # Всё остальное содержимое сайдбара живёт в отдельном фрейме —
        # его целиком прячем при сворачивании, ничего больше не трогая.
        body = ctk.CTkFrame(sidebar, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.sidebar_body = body

        ctk.CTkLabel(body, text="⏩ Task Advancer", font=t.FONT_TITLE, text_color=t.COLOR_ACCENT).pack(
            padx=20, pady=(4, 0), anchor="w")
        ctk.CTkLabel(body, text="Продвижение заданий по статусам", font=t.FONT_SUBTITLE,
                     text_color=t.COLOR_TEXT_MUTED).pack(padx=20, pady=(0, 20), anchor="w")

        ctk.CTkFrame(body, height=1, fg_color=t.COLOR_BORDER).pack(fill="x", padx=16, pady=(0, 16))

        def side_btn(text, command):
            return ctk.CTkButton(
                body, text=text, command=command, anchor="w",
                fg_color="transparent", text_color=t.COLOR_TEXT,
                hover_color=t.COLOR_SURFACE_2, font=t.FONT_LABEL, height=38,
            )

        side_btn("🔄  Обновить список заданий", self.refresh_taskids).pack(fill="x", padx=12, pady=3)
        side_btn("🧹  Убрать завершённые", self.clear_finished).pack(fill="x", padx=12, pady=3)
        ctk.CTkFrame(body, height=1, fg_color=t.COLOR_BORDER).pack(fill="x", padx=16, pady=16)
        side_btn("⚙️  Настройки", self.open_settings).pack(fill="x", padx=12, pady=3)
        side_btn("📁  Открыть папку логов", self.open_logs).pack(fill="x", padx=12, pady=3)
        # 🚮 вместо 🗑️ — та же корзина, но без модификатора стиля (U+FE0F),
        # который у этого конкретного эмодзи сбивал расчёт ширины текста
        # в Tk и сдвигал подпись вправо (заметно на широкой кнопке
        # сайдбара с anchor="w").
        side_btn("🚮  Очистить лог", self.clear_log).pack(fill="x", padx=12, pady=3)

        ctk.CTkFrame(body, height=1, fg_color=t.COLOR_BORDER).pack(fill="x", padx=16, pady=16)
        side_btn("🔁  Перезапустить приложение", self.restart_application).pack(fill="x", padx=12, pady=3)

        self._sidebar_collapsed = _load_sidebar_collapsed()
        if self._sidebar_collapsed:
            # Применяем сохранённое состояние сразу, без анимации.
            self.sidebar_body.pack_forget()
            self.sidebar.configure(width=SIDEBAR_COLLAPSED_WIDTH)
            self.sidebar_toggle_btn.configure(text="▶")

    def toggle_sidebar(self):
        collapsed = not self._sidebar_collapsed
        self._sidebar_collapsed = collapsed
        _save_sidebar_collapsed(collapsed)
        self.sidebar_toggle_btn.configure(text="▶" if collapsed else "◀")

        # Мгновенная смена ширины одним шагом — пошаговая анимация
        # заставляла главную область пересчитывать раскладку на каждом
        # кадре и это выглядело как мерцание, а не плавное движение.
        if collapsed:
            self.sidebar_body.pack_forget()
            self.sidebar.configure(width=SIDEBAR_COLLAPSED_WIDTH)
        else:
            self.sidebar.configure(width=SIDEBAR_EXPANDED_WIDTH)
            self.sidebar_body.pack(fill="both", expand=True)

        # Короткая вспышка цвета самой кнопки — единственное, что реально
        # анимируется. Она не требует relayout'а (просто перекраска одной
        # маленькой кнопки), поэтому не мерцает, но даёт понять, что клик
        # сработал, а не выглядит немым мгновенным рывком.
        self.sidebar_toggle_btn.configure(fg_color=t.COLOR_ACCENT_SOFT)
        self.sidebar_toggle_btn.after(
            120, lambda: self.sidebar_toggle_btn.configure(fg_color="transparent")
        )

    # ======================================================================
    # Основная область
    # ======================================================================
    def _build_main_area(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nswe", padx=20, pady=20)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        self._build_add_form(main)
        self._build_tasks_section(main)
        self._build_status_bar(main)

    # ---------------- Карточка "Добавить задание" ----------------
    def _build_add_form(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=14, fg_color=t.COLOR_SURFACE,
                             border_width=1, border_color=t.COLOR_BORDER_SOFT)
        card.grid(row=0, column=0, sticky="we", pady=(0, 16))

        ctk.CTkLabel(card, text="Добавить задание", font=t.FONT_SECTION, text_color=t.COLOR_TEXT).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(16, 10))

        card.grid_columnconfigure(2, weight=1)

        id_box = ctk.CTkFrame(card, fg_color="transparent")
        id_box.grid(row=1, column=0, sticky="w", padx=(20, 12))
        ctk.CTkLabel(id_box, text="Номер задания (taskid)", font=t.FONT_LABEL,
                     text_color=t.COLOR_TEXT_MUTED).pack(anchor="w")
        self.taskid_field = CTkAutocompleteEntry(
            id_box, values=self.available_taskids, width=200,
            placeholder_text="Начните вводить номер...",
            on_select=lambda v: None,
            **t.autocomplete_style(),
        )
        self.taskid_field.pack(anchor="w", pady=(4, 0))
        enable_clipboard_paste(self.taskid_field.entry)

        target_box = ctk.CTkFrame(card, fg_color="transparent")
        target_box.grid(row=1, column=1, sticky="w", padx=(0, 12))
        ctk.CTkLabel(target_box, text="Довести до статуса", font=t.FONT_LABEL,
                     text_color=t.COLOR_TEXT_MUTED).pack(anchor="w")
        self.target_var = ctk.StringVar(value=list(TARGET_CHOICES)[0])
        ctk.CTkOptionMenu(
            target_box, values=list(TARGET_CHOICES), variable=self.target_var,
            width=380, **t.option_menu_style(),
        ).pack(anchor="w", pady=(4, 0))

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.grid(row=1, column=2, sticky="e", padx=(0, 20))
        ctk.CTkButton(
            btn_box, text="➕  Добавить и запустить", command=self.add_task,
            height=40, font=("Segoe UI", 13, "bold"),
            fg_color=t.COLOR_ACCENT, hover_color=t.COLOR_ACCENT_HOVER,
            text_color=t.COLOR_ACCENT_TEXT_ON,
        ).pack(anchor="e")

        ctk.CTkLabel(
            card,
            text="ℹ️ Задание должно быть в статусе «Готово к печати» или «Готово к сериализации».",
            font=t.FONT_SMALL, text_color=t.COLOR_TEXT_MUTED,
            wraplength=980, justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=20, pady=(10, 16))

    # ---------------- Секция отслеживаемых заданий ----------------
    def _build_tasks_section(self, parent):
        section = ctk.CTkFrame(parent, corner_radius=14, fg_color=t.COLOR_SURFACE,
                                border_width=1, border_color=t.COLOR_BORDER_SOFT)
        section.grid(row=1, column=0, sticky="nswe", pady=(0, 16))
        section.grid_rowconfigure(1, weight=1)
        section.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(section, text="📋 Отслеживаемые задания", font=t.FONT_SECTION,
                     text_color=t.COLOR_TEXT).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 8))

        self.tasks_scroll = ctk.CTkScrollableFrame(
            section, fg_color="transparent", height=340,
            scrollbar_button_color=t.COLOR_SURFACE_3,
            scrollbar_button_hover_color=t.COLOR_ACCENT,
        )
        self.tasks_scroll.grid(row=1, column=0, sticky="nswe", padx=12, pady=(0, 12))
        self.tasks_scroll.grid_columnconfigure(0, weight=1)

        def _on_mousewheel(event):
            self.tasks_scroll._parent_canvas.yview_scroll(int(-18 * (event.delta / 120)), "units")

        section.bind("<MouseWheel>", _on_mousewheel)
        self.tasks_scroll.bind("<MouseWheel>", _on_mousewheel)
        self.tasks_scroll.bind_all(
            "<MouseWheel>",
            lambda e: _on_mousewheel(e) if section.winfo_containing(e.x_root, e.y_root) else None,
        )

        self._refresh_tasks_view()

    def _build_status_bar(self, parent):
        bar = ctk.CTkFrame(parent, corner_radius=14, fg_color=t.COLOR_SURFACE,
                            border_width=1, border_color=t.COLOR_BORDER_SOFT)
        bar.grid(row=2, column=0, sticky="we")
        self.status_var = ctk.StringVar(value="Готов к работе")
        ctk.CTkLabel(bar, textvariable=self.status_var, font=t.FONT_SMALL,
                     text_color=t.COLOR_TEXT_MUTED).pack(padx=16, pady=10, anchor="w")

    # ======================================================================
    # Карточки заданий
    # ======================================================================
    def _refresh_tasks_view(self):
        for w in self.tasks_scroll.winfo_children():
            w.destroy()
        self.row_frames = {}
        self.row_widgets = {}

        if not self.tasks:
            ctk.CTkLabel(
                self.tasks_scroll, text="Список пуст — добавьте задание выше",
                text_color=t.COLOR_TEXT_MUTED, font=t.FONT_LABEL,
            ).pack(pady=30)
            return

        for taskid in self.tasks:
            self._build_task_row(taskid)

    def _build_task_row(self, taskid):
        rec = self.tasks[taskid]
        worker = self.workers.get(taskid)
        state = worker.state if worker else rec.get("state", TaskState.IDLE)

        row = ctk.CTkFrame(
            self.tasks_scroll, corner_radius=10, fg_color=t.COLOR_SURFACE_2,
            border_width=2,
            border_color=(t.COLOR_GREEN if state == TaskState.DONE else t.COLOR_BORDER_SOFT),
        )
        row.pack(fill="x", padx=4, pady=5)
        self.row_frames[taskid] = row

        # info пакуется ПОСЛЕ кнопок и бейджа (см. ниже): pack отдаёт
        # место в порядке упаковки, поэтому длинный текст фазы не может
        # вытеснить кнопки за край карточки.
        info = ctk.CTkFrame(row, fg_color="transparent")

        title_row = ctk.CTkFrame(info, fg_color="transparent")
        title_row.pack(fill="x")
        dot = ctk.CTkLabel(title_row, text="●", font=t.FONT_ROW_TITLE,
                            text_color=STATE_COLORS.get(state, t.COLOR_TEXT_MUTED), width=18)
        dot.pack(side="left")
        ctk.CTkLabel(title_row, text=f"Задание #{taskid}", font=t.FONT_ROW_TITLE,
                     text_color=t.COLOR_TEXT, anchor="w").pack(side="left", padx=(4, 0))

        state_var = ctk.StringVar(value=STATE_LABELS.get(state, state))
        phase_var = ctk.StringVar(value=_phase_text(worker.phase) if worker else "")
        sub_row = ctk.CTkFrame(info, fg_color="transparent")
        sub_row.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(sub_row, textvariable=state_var, font=t.FONT_ROW_SUB,
                     text_color=t.COLOR_TEXT_MUTED, anchor="w").pack(side="left")
        ctk.CTkLabel(sub_row, text="   •   ", font=t.FONT_ROW_SUB,
                     text_color=t.COLOR_TEXT_DIM).pack(side="left")
        ctk.CTkLabel(sub_row, textvariable=phase_var, font=t.FONT_ROW_SUB,
                     text_color=t.COLOR_TEXT_MUTED, anchor="w").pack(side="left")

        target_label = TARGET_BY_STATUS.get(rec["target_status"], str(rec["target_status"]))
        badge = ctk.CTkLabel(
            row, text=f"🎯 {target_label}", font=t.FONT_SMALL, corner_radius=8,
            fg_color=t.COLOR_ACCENT_SOFT, text_color=t.COLOR_ACCENT, width=230, height=26,
        )

        actions = ctk.CTkFrame(row, fg_color="transparent")
        # Порядок упаковки = приоритет места: кнопки, затем бейдж,
        # и только потом растягивающийся info.
        actions.pack(side="right", padx=10)
        badge.pack(side="right", padx=(0, 4))
        info.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        self.row_widgets[taskid] = {
            "dot": dot, "state_var": state_var, "phase_var": phase_var,
            "actions": actions,
        }
        self._rebuild_row_actions(taskid, state)

    def _rebuild_row_actions(self, taskid, state):
        widgets = self.row_widgets.get(taskid)
        if not widgets:
            return
        actions = widgets["actions"]
        for w in actions.winfo_children():
            w.destroy()

        worker = self.workers.get(taskid)
        active = worker is not None and worker.is_active

        if active:
            ctk.CTkButton(
                actions, text="⏹ Стоп", width=90, height=32,
                fg_color=t.COLOR_ORANGE_SOFT, hover_color=t.COLOR_ORANGE,
                text_color=t.COLOR_ORANGE,
                command=lambda i=taskid: self.stop_task(i),
            ).pack(side="left", padx=3)
        else:
            if state in (TaskState.IDLE, TaskState.STOPPED):
                rec = self.tasks.get(taskid, {})
                if rec.get("wait_ready"):
                    btn_text, btn_width = "⏳ Ждать готовности", 160
                elif state == TaskState.STOPPED:
                    btn_text, btn_width = "▶️ Продолжить", 130
                else:
                    btn_text, btn_width = "▶️ Запустить", 110
                ctk.CTkButton(
                    actions, text=btn_text, width=btn_width, height=32,
                    fg_color=t.COLOR_GREEN_SOFT, hover_color=t.COLOR_GREEN,
                    text_color=t.COLOR_GREEN,
                    command=lambda i=taskid: self.start_task(i),
                ).pack(side="left", padx=3)
            elif state == TaskState.ERROR:
                ctk.CTkButton(
                    actions, text="❓ Ошибка", width=100, height=32,
                    fg_color=t.COLOR_SURFACE_3, hover_color=t.COLOR_ACCENT_SOFT,
                    text_color=t.COLOR_TEXT,
                    command=lambda i=taskid: self.show_task_error(i),
                ).pack(side="left", padx=3)
            ctk.CTkButton(
                actions, text="🗑️ Удалить", width=105, height=32,
                fg_color=t.COLOR_RED_SOFT, hover_color=t.COLOR_RED, text_color=t.COLOR_RED,
                command=lambda i=taskid: self.remove_task(i),
            ).pack(side="left", padx=3)

    # ======================================================================
    # Действия с заданиями
    # ======================================================================
    def add_task(self):
        raw = self.taskid_field.get().strip()
        if not raw.isdigit():
            show_error(self, "Ошибка", "Введите числовой номер задания (taskid).")
            return
        taskid = int(raw)
        if taskid in self.tasks:
            show_error(self, "Ошибка", f"Задание #{taskid} уже есть в списке.")
            return
        target_status = TARGET_CHOICES[self.target_var.get()]
        self.status_var.set(f"Проверка задания #{taskid}...")

        def worker():
            try:
                status = db.fetch_task_status(taskid)
                err = None
            except Exception as e:
                status, err = None, str(e)
            self.after(0, lambda: self._finish_add_task(taskid, target_status, status, err))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_add_task(self, taskid, target_status, status, err):
        if err:
            show_error(self, "Ошибка БД", f"Не удалось проверить задание #{taskid}:\n{err}")
            self.status_var.set("Готов к работе")
            return
        if status is None:
            show_error(self, "Ошибка", f"Задание #{taskid} не найдено в БД.")
            self.status_var.set("Готов к работе")
            return

        from advancer import ALLOWED_ENTRY_STATUSES, PREP_STATUSES
        if status in ALLOWED_ENTRY_STATUSES:
            wait_ready = False
        elif status in PREP_STATUSES:
            wait_ready = True
        else:
            show_error(
                self, "Ошибка",
                f"Задание #{taskid} в статусе {status}.\n\n"
                "Добавить можно только задания в статусах 3 (готово к печати), "
                "6 (готово к сериализации) или подготовительных 0/1/2 "
                "(с ожиданием готовности).",
            )
            self.status_var.set("Готов к работе")
            return

        self.tasks[taskid] = {
            "taskid": taskid,
            "target_status": target_status,
            "added_at": datetime.now().isoformat(timespec="seconds"),
            "reached_at": None,
            "state": TaskState.IDLE,
            "wait_ready": wait_ready,
        }
        self.taskid_field.set("")
        self._refresh_tasks_view()
        self.save_tasks()
        # Запускаем сразу в обоих случаях: для подготовительных статусов
        # (0/1/2) воркер сначала сам дождётся готовности (3/6), а затем
        # автоматически начнёт продвижение — дополнительных нажатий не нужно.
        self.start_task(taskid)
        if wait_ready:
            self.status_var.set(
                f"Задание #{taskid} в подготовительном статусе {status} — "
                f"ждём готовности (3/6), запуск будет автоматически"
            )
        else:
            # Задание ушло в работу — из списка доступных (статус 3/6) оно
            # скоро пропадёт, обновляем подсказки.
            self._async_reload_taskids()

    def start_task(self, taskid):
        rec = self.tasks.get(taskid)
        if rec is None:
            return
        settings = storage.get_app_settings()
        worker = self.workers.get(taskid)
        if worker is not None and worker.is_active:
            return
        worker = TaskWorker(
            taskid, rec["target_status"],
            poll_interval=settings["poll_interval"],
            on_update=self._on_worker_update,
            wait_for_ready=rec.get("wait_ready", False),
        )
        self.workers[taskid] = worker
        rec["state"] = TaskState.RUNNING
        worker.start()
        self._rebuild_row_actions(taskid, TaskState.RUNNING)
        self.status_var.set(f"Задание #{taskid} запущено")

    def stop_task(self, taskid):
        worker = self.workers.get(taskid)
        if worker is not None:
            worker.stop()
            self.status_var.set(f"Задание #{taskid}: остановка...")

    def remove_task(self, taskid):
        worker = self.workers.get(taskid)
        if worker is not None and worker.is_active:
            if not ask_yesno(self, "Удалить задание",
                             f"Задание #{taskid} ещё выполняется. Остановить и удалить?"):
                return
            worker.stop()
        self.workers.pop(taskid, None)
        self.tasks.pop(taskid, None)
        self._refresh_tasks_view()
        self.save_tasks()
        self.status_var.set(f"Задание #{taskid} удалено из списка")

    def clear_finished(self):
        done_ids = [i for i, r in self.tasks.items() if r.get("state") == TaskState.DONE]
        if not done_ids:
            show_info(self, "Очистка", "Завершённых заданий в списке нет.")
            return
        for taskid in done_ids:
            self.workers.pop(taskid, None)
            self.tasks.pop(taskid, None)
        self._refresh_tasks_view()
        self.save_tasks()
        self.status_var.set(f"Убрано завершённых заданий: {len(done_ids)}")

    def show_task_error(self, taskid):
        worker = self.workers.get(taskid)
        message = (worker.error_message if worker else None) or "Подробности недоступны."
        show_error(self, f"Задание #{taskid} — ошибка", message)

    # ======================================================================
    # Обратная связь от воркеров (вызывается из фонового потока!)
    # ======================================================================
    def _on_worker_update(self, taskid, state, phase, error):
        self.after(0, lambda: self._apply_worker_update(taskid, state, phase, error))

    def _apply_worker_update(self, taskid, state, phase, error):
        rec = self.tasks.get(taskid)
        if rec is None:
            return
        rec["state"] = state

        widgets = self.row_widgets.get(taskid)
        if widgets:
            widgets["state_var"].set(STATE_LABELS.get(state, state))
            widgets["phase_var"].set(_phase_text(phase))
            widgets["dot"].configure(text_color=STATE_COLORS.get(state, t.COLOR_TEXT_MUTED))

        if state in (TaskState.DONE, TaskState.ERROR, TaskState.STOPPED):
            self._rebuild_row_actions(taskid, state)
            self.save_tasks()

        if state == TaskState.DONE:
            rec["reached_at"] = datetime.now().isoformat(timespec="seconds")
            self.save_tasks()
            settings = storage.get_app_settings()
            target_label = TARGET_BY_STATUS.get(rec["target_status"], str(rec["target_status"]))
            notifications.notify_target_reached(
                self, self.row_frames.get(taskid), taskid, target_label,
                sound_enabled=settings["sound_enabled"],
            )
            self.status_var.set(f"Задание #{taskid}: цель достигнута ({target_label})")
        elif state == TaskState.ERROR:
            settings = storage.get_app_settings()
            notifications.notify_error(
                self, taskid, error or "Неизвестная ошибка",
                sound_enabled=settings["sound_enabled"],
            )
            self.status_var.set(f"Задание #{taskid}: ошибка")

    # ======================================================================
    # Персистентность списка
    # ======================================================================
    def save_tasks(self):
        storage.save_tasks(list(self.tasks.values()))

    def auto_load_tasks(self):
        items = storage.load_tasks()
        for rec in items:
            taskid = int(rec["taskid"])
            # Незавершённые прошлые состояния при загрузке сбрасываем в
            # "ожидает запуска" — воркеры не переживают перезапуск.
            state = rec.get("state")
            if state not in (TaskState.DONE,):
                state = TaskState.IDLE
            self.tasks[taskid] = {
                "taskid": taskid,
                "target_status": int(rec.get("target_status", 9)),
                "added_at": rec.get("added_at"),
                "reached_at": rec.get("reached_at"),
                "state": state,
                "wait_ready": bool(rec.get("wait_ready", False)),
            }
        if items:
            self._refresh_tasks_view()
            self.status_var.set(f"Загружено заданий из списка: {len(items)}")

    # ======================================================================
    # Список доступных заданий (статус 3/6) для автодополнения
    # ======================================================================
    def refresh_taskids(self):
        self._async_reload_taskids(show_status=True)

    def _async_reload_taskids(self, show_status=False):
        def worker():
            try:
                taskids = db.fetch_available_taskids()
                err = None
            except Exception as e:
                taskids, err = [], str(e)
            self.after(0, lambda: self._apply_taskids(taskids, err, show_status))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_taskids(self, taskids, err, show_status):
        self.available_taskids = taskids
        if hasattr(self, "taskid_field"):
            self.taskid_field.set_values(taskids)
        if err:
            if show_status:
                show_error(self, "Ошибка БД",
                           f"Не удалось загрузить список заданий:\n{err}")
            self.status_var.set("Подключитесь к БД через ⚙️ Настройки")
        elif show_status:
            self.status_var.set(f"Доступных заданий (статус 3/6): {len(taskids)}")

    # ======================================================================
    # Проверка БД и API при запуске
    # ======================================================================
    def _check_db_api_on_startup(self):
        def worker():
            db_ok = False
            api_ok = False
            try:
                ok, _ = db.test_connection()
                db_ok = ok
            except Exception:
                db_ok = False
            try:
                api_settings = get_api_settings()
                ok, _ = test_api_connection(api_settings["api_host"], api_settings["api_port"])
                api_ok = ok
            except Exception:
                api_ok = False

            self.after(0, lambda: self._handle_startup_connection_check(db_ok, api_ok))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_startup_connection_check(self, db_ok, api_ok):
        if not db_ok or not api_ok:
            win = ctk.CTkToplevel(self)
            win.title("Настройка подключений")
            self._setup_modal_dialog(win, 500, 320)

            msg_frame = ctk.CTkFrame(win, fg_color="transparent")
            msg_frame.pack(fill="both", expand=True, padx=24, pady=24)

            ctk.CTkLabel(msg_frame, text="⚠️ Ошибка подключения", font=t.FONT_SECTION,
                         text_color=t.COLOR_TEXT).pack(anchor="w", pady=(0, 12))

            status_text = "Пожалуйста, настройте подключения для работы приложения:\n\n"
            if not db_ok:
                status_text += "❌ База данных недоступна\n"
            if not api_ok:
                status_text += "❌ REST API недоступен\n"

            ctk.CTkLabel(msg_frame, text=status_text, font=t.FONT_LABEL, text_color=t.COLOR_TEXT_MUTED,
                         justify="left").pack(anchor="w", pady=(0, 12))

            btn_frame = ctk.CTkFrame(win, fg_color="transparent")
            btn_frame.pack(side="bottom", pady=16, padx=24, fill="x")

            def open_settings():
                win.destroy()
                self.open_settings()

            ctk.CTkButton(
                btn_frame, text="⚙️  Открыть настройки", command=open_settings,
                fg_color=t.COLOR_ACCENT, hover_color=t.COLOR_ACCENT_HOVER,
                text_color=t.COLOR_ACCENT_TEXT_ON, height=40,
            ).pack(fill="x", padx=(0, 10), side="left", expand=True)

            ctk.CTkButton(
                btn_frame, text="Закрыть", command=win.destroy,
                fg_color=t.COLOR_SURFACE_2, hover_color=t.COLOR_SURFACE_3,
                text_color=t.COLOR_TEXT, height=40,
            ).pack(fill="x", side="left", expand=True)

    # ======================================================================
    # Настройки
    # ======================================================================
    def open_settings(self):
        current = db.get_db_settings()
        current_api = get_api_settings()
        current_app = storage.get_app_settings()

        win = ctk.CTkToplevel(self)
        win.title("Настройки")
        self._setup_modal_dialog(win, 600, 880)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(side="bottom", pady=16)

        status_label = ctk.CTkLabel(win, text="", font=t.FONT_SMALL, wraplength=420, justify="left")
        status_label.pack(side="bottom", padx=24, pady=(4, 0), anchor="w")

        content = ctk.CTkScrollableFrame(win, fg_color="transparent")
        content.pack(side="top", fill="both", expand=True)

        fields = {}
        api_fields = {}

        def add_field(parent_frame, label_text, key, store, show=None):
            ctk.CTkLabel(parent_frame, text=label_text, font=t.FONT_LABEL, text_color=t.COLOR_TEXT_MUTED).pack(
                anchor="w", padx=24, pady=(14, 2))
            var = ctk.StringVar(value=str(store.get(key, "")))
            entry = ctk.CTkEntry(parent_frame, textvariable=var, width=520, show=show, **t.entry_style())
            entry.pack(padx=24, fill="x")
            return var

        ctk.CTkLabel(content, text="База данных DMC (PostgreSQL)", font=t.FONT_SECTION,
                     text_color=t.COLOR_TEXT).pack(anchor="w", padx=24, pady=(16, 0))
        fields["host"] = add_field(content, "Хост (host)", "host", current)
        fields["port"] = add_field(content, "Порт (port)", "port", current)
        fields["dbname"] = add_field(content, "Имя базы данных (dbname)", "dbname", current)
        fields["user"] = add_field(content, "Пользователь (user)", "user", current)
        fields["password"] = add_field(content, "Пароль (password)", "password", current, show="*")

        ctk.CTkFrame(content, height=1, fg_color=t.COLOR_BORDER).pack(fill="x", padx=24, pady=(18, 0))
        ctk.CTkLabel(content, text="REST API v2 DMC", font=t.FONT_SECTION,
                     text_color=t.COLOR_TEXT).pack(anchor="w", padx=24, pady=(14, 0))
        api_fields["api_host"] = add_field(content, "Хост API (host)", "api_host", current_api)
        api_fields["api_port"] = add_field(content, "Порт API (port)", "api_port", current_api)

        ctk.CTkFrame(content, height=1, fg_color=t.COLOR_BORDER).pack(fill="x", padx=24, pady=(18, 0))
        ctk.CTkLabel(content, text="Поведение", font=t.FONT_SECTION,
                     text_color=t.COLOR_TEXT).pack(anchor="w", padx=24, pady=(14, 0))
        poll_var = add_field(content, "Интервал опроса статуса, сек", "poll_interval", current_app)

        sound_var = ctk.BooleanVar(value=bool(current_app["sound_enabled"]))
        sound_row = ctk.CTkFrame(content, fg_color="transparent")
        sound_row.pack(anchor="w", padx=24, pady=(14, 8), fill="x")
        ctk.CTkSwitch(
            sound_row, text="Звук уведомления о достижении цели", variable=sound_var,
            onvalue=True, offvalue=False,
            fg_color=t.COLOR_SURFACE_3, progress_color=t.COLOR_ACCENT, button_color=t.COLOR_TEXT,
            font=t.FONT_LABEL, text_color=t.COLOR_TEXT,
        ).pack(anchor="w")

        buttons = {}

        def set_buttons_busy(busy):
            state = "disabled" if busy else "normal"
            for b in buttons.values():
                b.configure(state=state)

        def do_test_db():
            host = fields["host"].get().strip()
            port = fields["port"].get().strip()
            dbname = fields["dbname"].get().strip()
            user = fields["user"].get().strip()
            password = fields["password"].get()

            status_label.configure(text="Проверка подключения к БД...", text_color=t.COLOR_TEXT_MUTED)
            set_buttons_busy(True)

            def worker():
                ok, err = db.test_connection_with(host, port, dbname, user, password)
                win.after(0, lambda: on_test_done("БД", ok, err))

            threading.Thread(target=worker, daemon=True).start()

        def do_test_api():
            api_host = api_fields["api_host"].get().strip()
            api_port = api_fields["api_port"].get().strip()

            status_label.configure(text="Проверка подключения к API...", text_color=t.COLOR_TEXT_MUTED)
            set_buttons_busy(True)

            def worker():
                ok, err = test_api_connection(api_host, api_port)
                win.after(0, lambda: on_test_done("API", ok, err))

            threading.Thread(target=worker, daemon=True).start()

        def on_test_done(label, ok, err):
            set_buttons_busy(False)
            if ok:
                status_label.configure(text=f"✅ Подключение к {label} успешно", text_color=t.COLOR_GREEN)
            else:
                status_label.configure(text=f"❌ Ошибка подключения к {label}: {err}", text_color=t.COLOR_RED)

        def do_save():
            host = fields["host"].get().strip()
            port = fields["port"].get().strip()
            dbname = fields["dbname"].get().strip()
            user = fields["user"].get().strip()
            password = fields["password"].get()
            api_host = api_fields["api_host"].get().strip()
            api_port = api_fields["api_port"].get().strip()
            if not host or not port or not dbname or not user:
                status_label.configure(text="Заполните хост, порт, имя БД и пользователя.", text_color=t.COLOR_RED)
                return
            if not api_host or not api_port:
                status_label.configure(text="Заполните хост и порт REST API.", text_color=t.COLOR_RED)
                return
            try:
                poll_interval = int(poll_var.get().strip())
                if poll_interval < 1:
                    raise ValueError
            except ValueError:
                status_label.configure(text="Интервал опроса — целое число секунд (минимум 1).",
                                       text_color=t.COLOR_RED)
                return
            db.save_db_settings(host, port, dbname, user, password)
            save_api_settings(api_host, api_port)
            storage.save_app_settings(poll_interval, sound_var.get())
            win.destroy()
            self.status_var.set("Настройки сохранены")

        buttons["test_db"] = ctk.CTkButton(btns, text="Проверить БД", command=do_test_db, width=140,
                                            fg_color=t.COLOR_CYAN_SOFT, hover_color=t.COLOR_SURFACE_3,
                                            text_color=t.COLOR_CYAN, border_width=1, border_color=t.COLOR_CYAN)
        buttons["test_api"] = ctk.CTkButton(btns, text="Проверить API", command=do_test_api, width=140,
                                             fg_color=t.COLOR_CYAN_SOFT, hover_color=t.COLOR_SURFACE_3,
                                             text_color=t.COLOR_CYAN, border_width=1, border_color=t.COLOR_CYAN)
        buttons["save"] = ctk.CTkButton(btns, text="💾 Сохранить", command=do_save, width=130,
                                         fg_color=t.COLOR_ACCENT, hover_color=t.COLOR_ACCENT_HOVER,
                                         text_color=t.COLOR_ACCENT_TEXT_ON)
        buttons["cancel"] = ctk.CTkButton(btns, text="Отмена", command=win.destroy, width=100,
                                           fg_color=t.COLOR_SURFACE_2, hover_color=t.COLOR_SURFACE_3,
                                           text_color=t.COLOR_TEXT)
        for b in (buttons["test_db"], buttons["test_api"], buttons["save"], buttons["cancel"]):
            b.pack(side="left", padx=6)

    # ======================================================================
    # Логи
    # ======================================================================
    def open_logs(self):
        log_dir = str(LOG_DIR)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        if os.name == "nt":
            os.startfile(log_dir)
        else:
            subprocess.run(["xdg-open", log_dir])

    def clear_log(self):
        if ask_yesno(self, "Очистка лога", "Удалить содержимое файла лога?"):
            try:
                open(LOG_FILE, "w").close()
                self.status_var.set("Лог очищен")
            except Exception as e:
                show_error(self, "Ошибка", f"Не удалось очистить лог: {e}")

    # ======================================================================
    # Закрытие и перезапуск
    # ======================================================================
    def on_close(self):
        active = [i for i, w in self.workers.items() if w.is_active]
        if active:
            ids = ", ".join(f"#{i}" for i in active)
            if not ask_yesno(self, "Выход",
                             f"Ещё выполняются задания: {ids}.\n\n"
                             "Выйти? Выполнение прервётся, задания останутся в списке "
                             "и их можно будет запустить заново."):
                return
            for w in self.workers.values():
                w.stop()
        self.save_tasks()
        self.destroy()

    def restart_application(self):
        if not ask_yesno(self,
            "Перезапуск приложения",
            "Перезапустить Task Advancer?\n\n"
            "Текущий список заданий будет автоматически сохранён и "
            "подгрузится заново после перезапуска."
        ):
            return

        try:
            self.save_tasks()
        except Exception as e:
            if not ask_yesno(self,
                "Ошибка сохранения списка",
                f"Не удалось сохранить список:\n{e}\n\nВсё равно перезапустить?"
            ):
                return

        for w in self.workers.values():
            w.stop()

        try:
            python = sys.executable
            args = [python] + (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
            subprocess.Popen(args)
        except Exception as e:
            show_error(self, "Ошибка перезапуска", f"Не удалось запустить новый процесс:\n{e}")
            return

        self.destroy()
        os._exit(0)


if __name__ == "__main__":
    app = TaskAdvancerApp()
    app.mainloop()
