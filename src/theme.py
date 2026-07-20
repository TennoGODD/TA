# src/theme.py
# Палитра и шрифты — тёмная лавандовая тема.
# Стилизация делается не через QSS, а через передачу цветов/шрифтов в каждый
# виджет. Здесь — единый источник констант и фабрики готовых наборов kwargs.

COLOR_BG = "#181622"           # фон окна целиком
COLOR_BG_SIDEBAR = "#121020"   # сайдбар — чуть темнее основного фона
COLOR_SURFACE = "#242038"      # карточки/панели
COLOR_SURFACE_2 = "#2d2846"    # вложенные элементы, поля ввода
COLOR_SURFACE_3 = "#3a3358"    # hover / активные элементы
COLOR_BORDER = "#3d3760"       # рамки, разделители
COLOR_BORDER_SOFT = "#2c2745"

COLOR_TEXT = "#f1eefc"         # основной текст
COLOR_TEXT_MUTED = "#9089b8"   # приглушённый текст (подписи, статус)
COLOR_TEXT_DIM = "#6f6894"     # ещё более приглушённый (плейсхолдеры)

COLOR_ACCENT = "#b58bff"       # фирменный лавандовый — акцент приложения
COLOR_ACCENT_HOVER = "#9d6cf5"
COLOR_ACCENT_SOFT = "#372c5c"
COLOR_ACCENT_TEXT_ON = "#1c1730"  # тёмный текст поверх акцентных кнопок

COLOR_PINK = "#ff7ec9"
COLOR_PINK_SOFT = "#4a2c47"

COLOR_CYAN = "#7fe3f7"
COLOR_CYAN_SOFT = "#1f3a45"

COLOR_GREEN = "#5af7ab"
COLOR_GREEN_HOVER = "#3fdb90"
COLOR_GREEN_SOFT = "#1c3a2f"

COLOR_RED = "#ff6b8b"
COLOR_RED_HOVER = "#e84f70"
COLOR_RED_SOFT = "#3d1f2b"

COLOR_ORANGE = "#ffb15c"
COLOR_ORANGE_SOFT = "#3d2f1c"

FONT_TITLE = ("Segoe UI", 21, "bold")
FONT_SUBTITLE = ("Segoe UI", 12)
FONT_SECTION = ("Segoe UI", 15, "bold")
FONT_LABEL = ("Segoe UI", 12)
FONT_SMALL = ("Segoe UI", 11)
FONT_MONO = ("Cascadia Mono", 11)
FONT_MONO_CAM = ("Cascadia Mono", 9)
FONT_ROW_TITLE = ("Segoe UI", 13, "bold")
FONT_ROW_SUB = ("Segoe UI", 11)


def entry_style(**overrides):
    """Готовый набор kwargs для CTkEntry в теме."""
    style = dict(
        fg_color=COLOR_SURFACE_2,
        border_color=COLOR_BORDER,
        text_color=COLOR_TEXT,
        placeholder_text_color=COLOR_TEXT_DIM,
    )
    style.update(overrides)
    return style


def option_menu_style(**overrides):
    """Готовый набор kwargs для CTkOptionMenu в теме."""
    style = dict(
        fg_color=COLOR_SURFACE_2,
        button_color=COLOR_ACCENT,
        button_hover_color=COLOR_ACCENT_HOVER,
        text_color=COLOR_TEXT,
        dropdown_fg_color=COLOR_SURFACE,
        dropdown_hover_color=COLOR_SURFACE_2,
        dropdown_text_color=COLOR_TEXT,
    )
    style.update(overrides)
    return style


def accent_button_style(**overrides):
    """Главная акцентная кнопка (лавандовая)."""
    style = dict(
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        text_color=COLOR_ACCENT_TEXT_ON,
    )
    style.update(overrides)
    return style


def ghost_button_style(**overrides):
    """Прозрачная кнопка (пункт сайдбара / второстепенное действие)."""
    style = dict(
        fg_color="transparent",
        hover_color=COLOR_SURFACE_2,
        text_color=COLOR_TEXT,
    )
    style.update(overrides)
    return style


def autocomplete_style(**overrides):
    """Готовый набор kwargs для CTkAutocompleteEntry в теме."""
    style = dict(
        fg_color=COLOR_SURFACE_2,
        border_color=COLOR_BORDER,
        text_color=COLOR_TEXT,
        placeholder_text_color=COLOR_TEXT_DIM,
        popup_fg_color=COLOR_SURFACE,
        popup_border_color=COLOR_ACCENT_SOFT,
        row_hover_color=COLOR_SURFACE_3,
    )
    style.update(overrides)
    return style
