# src/widgets.py


import customtkinter as ctk

import theme as t
from paths import ICON_FILE


def set_window_icon(win):
    icon = str(ICON_FILE)
    try:
        win.iconbitmap(icon)
    except Exception:
        return
    # customtkinter сбрасывает иконку окна вскоре после создания из-за
    # своей обработки тёмного заголовка — переустанавливаем через паузу.
    win.after(250, lambda: win.iconbitmap(icon) if win.winfo_exists() else None)


def _themed_dialog(master, title, message, buttons):
    """Модальный CTkToplevel в теме приложения. buttons — список
    (текст, kwargs_для_CTkButton, значение_результата)."""
    win = ctk.CTkToplevel(master)
    win.withdraw()
    win.title(title)
    win.configure(fg_color=t.COLOR_BG)
    win.resizable(False, False)
    set_window_icon(win)
    result = {"value": buttons[-1][2]}

    box = ctk.CTkFrame(win, fg_color=t.COLOR_SURFACE, corner_radius=14,
                        border_width=1, border_color=t.COLOR_BORDER_SOFT)
    box.pack(fill="both", expand=True, padx=12, pady=12)
    ctk.CTkLabel(box, text=title, font=t.FONT_SECTION,
                 text_color=t.COLOR_TEXT).pack(padx=28, pady=(18, 6))
    ctk.CTkLabel(box, text=message, font=t.FONT_LABEL, text_color=t.COLOR_TEXT_MUTED,
                 wraplength=360, justify="center").pack(padx=28, pady=(0, 16))

    row = ctk.CTkFrame(box, fg_color="transparent")
    row.pack(pady=(0, 18))

    def pick(value):
        result["value"] = value
        win.destroy()

    for text, kwargs, value in buttons:
        ctk.CTkButton(row, text=text, width=110, height=34,
                      command=lambda v=value: pick(v), **kwargs).pack(side="left", padx=6)

    win.bind("<Escape>", lambda e: pick(buttons[-1][2]))
    win.protocol("WM_DELETE_WINDOW", lambda: pick(buttons[-1][2]))

    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    if master.winfo_ismapped():
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.deiconify()
    win.transient(master)
    try:
        win.grab_set()
    except Exception:
        pass
    win.focus_set()
    master.wait_window(win)
    return result["value"]


def show_error(master, title, message):
    """Themed-замена tkinter.messagebox.showerror."""
    _themed_dialog(master, title, message, [
        ("ОК", t.accent_button_style(fg_color=t.COLOR_RED_SOFT, hover_color=t.COLOR_RED,
                                      text_color=t.COLOR_RED), True),
    ])


def show_info(master, title, message):
    """Themed-замена tkinter.messagebox.showinfo."""
    _themed_dialog(master, title, message, [
        ("ОК", t.accent_button_style(), True),
    ])


def ask_yesno(master, title, message) -> bool:
    """Themed-замена tkinter.messagebox.askyesno."""
    return _themed_dialog(master, title, message, [
        ("Да", t.accent_button_style(), True),
        ("Отмена", dict(fg_color=t.COLOR_SURFACE_3, hover_color=t.COLOR_ACCENT_SOFT,
                        text_color=t.COLOR_TEXT), False),
    ])


# ---------------------------------------------------------------------------
# Неблокирующий тост-попап (уведомление о достижении целевого статуса)
# ---------------------------------------------------------------------------

_active_toasts = []


def show_toast(master, title, message, kind="success", duration_ms=5000):
    """Неблокирующее всплывающее уведомление в правом нижнем углу экрана.

    В отличие от _themed_dialog не перехватывает фокус (нет grab_set),
    самоуничтожается через duration_ms и закрывается по клику. Несколько
    тостов складываются столбиком снизу вверх."""
    win = ctk.CTkToplevel(master)
    win.withdraw()
    win.overrideredirect(True)
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass

    accent = {
        "success": t.COLOR_GREEN,
        "error": t.COLOR_RED,
        "info": t.COLOR_ACCENT,
    }.get(kind, t.COLOR_ACCENT)

    box = ctk.CTkFrame(win, fg_color=t.COLOR_SURFACE, corner_radius=14,
                        border_width=2, border_color=accent)
    box.pack(fill="both", expand=True, padx=2, pady=2)
    ctk.CTkLabel(box, text=title, font=t.FONT_SECTION,
                 text_color=accent).pack(padx=20, pady=(14, 2), anchor="w")
    ctk.CTkLabel(box, text=message, font=t.FONT_LABEL, text_color=t.COLOR_TEXT,
                 wraplength=320, justify="left").pack(padx=20, pady=(0, 14), anchor="w")

    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()

    # Столбик тостов: каждый следующий выше предыдущего.
    _active_toasts[:] = [tw for tw in _active_toasts if tw.winfo_exists()]
    offset = sum(tw.winfo_height() + 10 for tw in _active_toasts)
    x = screen_w - w - 24
    y = screen_h - h - 64 - offset
    win.geometry(f"{w}x{h}+{x}+{y}")
    win.deiconify()
    _active_toasts.append(win)

    def close(_event=None):
        if win in _active_toasts:
            _active_toasts.remove(win)
        if win.winfo_exists():
            win.destroy()

    for widget in (win, box, *box.winfo_children()):
        widget.bind("<Button-1>", close)
    win.after(duration_ms, close)
    return win


def flash_row(frame, flash_color, base_color, settle_color=None,
              interval_ms=250, times=6):
    """Пульсирует рамкой карточки times раз (чередуя flash_color и
    base_color), затем оставляет settle_color (по умолчанию — base_color).

    Используется для визуального выделения строки задания, достигшего
    целевого статуса: мигает зелёным и остаётся зелёной."""
    settle = settle_color if settle_color is not None else base_color
    state = {"count": 0}

    def step():
        if not frame.winfo_exists():
            return
        if state["count"] >= times:
            frame.configure(border_color=settle)
            return
        color = flash_color if state["count"] % 2 == 0 else base_color
        frame.configure(border_color=color)
        state["count"] += 1
        frame.after(interval_ms, step)

    step()


def enable_clipboard_paste(entry):
    """Вставка из буфера по Ctrl+V независимо от раскладки клавиатуры.

    Стандартная вставка Tk не срабатывает на русской раскладке (Ctrl+М),
    поэтому ловим физическую клавишу V по keycode."""
    def do_paste():
        try:
            text = entry.clipboard_get()
        except Exception:
            return "break"
        try:
            entry.delete("sel.first", "sel.last")
        except Exception:
            pass
        entry.insert("insert", text.strip())
        return "break"

    entry.bind(
        "<Control-KeyPress>",
        lambda e: do_paste() if e.keycode == 86 else None,
        add="+",
    )


_IGNORED_KEYSYMS = frozenset({
    "Up", "Down", "Return", "Escape", "Tab", "ISO_Left_Tab",
    "Shift_L", "Shift_R", "Control_L", "Control_R",
    "Alt_L", "Alt_R", "ISO_Level3_Shift", "ISO_Level5_Shift",
    "Caps_Lock", "Num_Lock", "Scroll_Lock",
    "Super_L", "Super_R", "Meta_L", "Meta_R", "Menu",
})


class CTkAutocompleteEntry(ctk.CTkFrame):
    MAX_VISIBLE = 8
    MAX_MATCHES = 60
    ROW_HEIGHT = 30

    _open_instances = []
    _bound_toplevels = set()

    def __init__(self, master, values=None, width=280, height=36,
                 placeholder_text="", on_select=None, font=None,
                 fg_color=None, border_color=None, text_color=None,
                 placeholder_text_color=None, popup_fg_color=None,
                 popup_border_color=None, row_hover_color=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.all_values = list(values or [])
        self.on_select_callback = on_select

        self._popup_fg_color = popup_fg_color
        self._popup_border_color = popup_border_color
        self._row_hover_color = row_hover_color or t.COLOR_SURFACE_3

        entry_font = font or ctk.CTkFont(size=13)
        entry_kwargs = {}
        if fg_color is not None:
            entry_kwargs["fg_color"] = fg_color
        if border_color is not None:
            entry_kwargs["border_color"] = border_color
        if text_color is not None:
            entry_kwargs["text_color"] = text_color
        if placeholder_text_color is not None:
            entry_kwargs["placeholder_text_color"] = placeholder_text_color
        self.entry = ctk.CTkEntry(
            self, width=width, height=height,
            placeholder_text=placeholder_text, font=entry_font,
            **entry_kwargs,
        )
        self.entry.pack(fill="both", expand=True)

        self.popup = None
        self.popup_frame = None
        self._row_widgets = []
        self._matches = []
        self._highlight = -1

        self.entry.bind("<KeyRelease>", self._on_keyrelease)
        self.entry.bind("<Down>", self._on_arrow_down)
        self.entry.bind("<Up>", self._on_arrow_up)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Escape>", lambda e: self._hide_popup())
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<Destroy>", lambda e: self._destroy_popup())

        self._register_click_watcher()

    def _register_click_watcher(self):

        root = self.winfo_toplevel()
        if root in CTkAutocompleteEntry._bound_toplevels:
            return
        root.bind("<ButtonPress-1>", CTkAutocompleteEntry._on_any_click, add="+")
        CTkAutocompleteEntry._bound_toplevels.add(root)

    # ---------------- публичный API ----------------
    def get(self):
        return self.entry.get()

    def set(self, value):
        self.entry.delete(0, "end")
        self.entry.insert(0, value or "")

    def set_values(self, values):
        self.all_values = list(values or [])

    def focus_set(self):
        self.entry.focus_set()

    # ---------------- фильтрация ----------------
    def _match(self, typed):
        typed = typed.strip().lower()
        if not typed:
            return self.all_values[: self.MAX_MATCHES]
        starts = [v for v in self.all_values if v.lower().startswith(typed)]
        contains = [v for v in self.all_values if typed in v.lower() and v not in starts]
        return (starts + contains)[: self.MAX_MATCHES]

    def _on_keyrelease(self, event):
        if event.keysym in _IGNORED_KEYSYMS:
            return
        self._matches = self._match(self.get())
        if self._matches:
            self._show_popup()
        else:
            self._hide_popup()

    # ---------------- всплывающий список ----------------
    def _ensure_popup(self):
        if self.popup is not None:
            return
        self.popup = ctk.CTkToplevel(self)
        self.popup.overrideredirect(True)
        try:
            self.popup.attributes("-topmost", True)
        except Exception:
            pass
        frame_kwargs = {"corner_radius": 8, "border_width": 1}
        if self._popup_fg_color is not None:
            frame_kwargs["fg_color"] = self._popup_fg_color
        if self._popup_border_color is not None:
            frame_kwargs["border_color"] = self._popup_border_color
        self.popup_frame = ctk.CTkFrame(self.popup, **frame_kwargs)
        self.popup_frame.pack(fill="both", expand=True)
        self.popup.withdraw()

    def _show_popup(self):
        self._ensure_popup()
        for w in self.popup_frame.winfo_children():
            w.destroy()
        self._row_widgets = []
        self._highlight = -1

        shown = self._matches[: self.MAX_VISIBLE]
        for i, val in enumerate(shown):
            row = ctk.CTkLabel(
                self.popup_frame, text=val, anchor="w",
                height=self.ROW_HEIGHT, corner_radius=6,
                fg_color="transparent", cursor="hand2",
            )
            row.pack(fill="x", padx=3, pady=1)
            row.bind("<Button-1>", lambda e, v=val: self._commit(v))
            row.bind("<Enter>", lambda e, idx=i: self._set_highlight(idx))
            self._row_widgets.append(row)

        extra = len(self._matches) - len(shown)
        if extra > 0:
            more = ctk.CTkLabel(
                self.popup_frame, text=f"...ещё {extra}", anchor="w",
                height=20, text_color="gray50", font=ctk.CTkFont(size=11),
            )
            more.pack(fill="x", padx=6)

        self.update_idletasks()
        rows = len(shown)
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height() + 3
        w = max(self.entry.winfo_width(), 240)
        h = rows * (self.ROW_HEIGHT + 2) + (22 if extra > 0 else 0) + 10
        self.popup.geometry(f"{w}x{h}+{x}+{y}")
        self.popup.deiconify()
        self.popup.lift()

        if self not in CTkAutocompleteEntry._open_instances:
            CTkAutocompleteEntry._open_instances.append(self)

    def _hide_popup(self):
        if self.popup is not None:
            self.popup.withdraw()
        self._highlight = -1
        if self in CTkAutocompleteEntry._open_instances:
            CTkAutocompleteEntry._open_instances.remove(self)

    def _destroy_popup(self):
        if self in CTkAutocompleteEntry._open_instances:
            CTkAutocompleteEntry._open_instances.remove(self)
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass
        self.popup = None
        self.popup_frame = None

    def _set_highlight(self, idx):
        for i, row in enumerate(self._row_widgets):
            if i == idx:
                row.configure(fg_color=self._row_hover_color)
            else:
                row.configure(fg_color="transparent")
        self._highlight = idx

    # ---------------- закрытие по клику куда угодно в этом тулевеле ----------------
    @classmethod
    def _on_any_click(cls, event):
        for inst in list(cls._open_instances):
            inst._maybe_close_for_click(event)

    def _maybe_close_for_click(self, event):
        if self.popup is None or not self.popup.winfo_ismapped():
            return

        def inside(widget):
            try:
                wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
                ww, wh = widget.winfo_width(), widget.winfo_height()
                return wx <= event.x_root <= wx + ww and wy <= event.y_root <= wy + wh
            except Exception:
                return False

        if inside(self.entry) or inside(self.popup):
            return  # клик по самому полю или по списку — не закрываем здесь

        self._hide_popup()
        typed = self.get().strip()
        if typed and self.on_select_callback:
            self.on_select_callback(typed)

    @classmethod
    def hide_all(cls):
        for inst in list(cls._open_instances):
            inst._hide_popup()

    # ---------------- клавиатура ----------------
    def _on_arrow_down(self, event):
        if self.popup is None or not self.popup.winfo_viewable():
            self._matches = self._match(self.get())
            if self._matches:
                self._show_popup()
            return "break"
        shown_count = min(len(self._matches), self.MAX_VISIBLE)
        if shown_count == 0:
            return "break"
        self._highlight = min(self._highlight + 1, shown_count - 1)
        self._set_highlight(self._highlight)
        return "break"

    def _on_arrow_up(self, event):
        if self.popup is None or not self.popup.winfo_viewable():
            return "break"
        self._highlight = max(self._highlight - 1, 0)
        self._set_highlight(self._highlight)
        return "break"

    def _on_return(self, event):
        if self.popup is not None and self.popup.winfo_viewable() and self._highlight >= 0:
            shown = self._matches[: self.MAX_VISIBLE]
            if self._highlight < len(shown):
                self._commit(shown[self._highlight])
                return "break"
        self._commit(self.get())
        return "break"

    def _commit(self, value):
        self.set(value)
        self._hide_popup()
        if self.on_select_callback:
            self.on_select_callback(value)

    # ---------------- потеря фокуса (например, Tab) ----------------
    def _on_focus_out(self, event=None):
        self.after(150, self._maybe_close_on_blur)

    def _maybe_close_on_blur(self):
        if self.popup is None or not self.popup.winfo_ismapped():
            return
        try:
            focused = self.focus_get()
        except Exception:
            focused = None
        if focused is self.entry:
            return
        self._hide_popup()
        if self.on_select_callback:
            self.on_select_callback(self.get())
