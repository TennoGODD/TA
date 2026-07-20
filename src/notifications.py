# src/notifications.py
"""
Уведомление о достижении целевого статуса: звук + тост + подсветка
строки. Все три вызываются из главного потока Tk (GUI маршалит через
self.after) — сюда попадать из фонового воркера напрямую нельзя.
"""

import theme as t
from api_client import log
from widgets import flash_row, show_toast

try:
    import winsound
except ImportError:  # не-Windows окружение (разработка)
    winsound = None


def play_notification_sound(enabled=True):
    if not enabled or winsound is None:
        return
    try:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception as e:
        # Сбой уведомления никогда не должен ломать основной поток работы.
        log(f"Не удалось воспроизвести звук: {e}")


def notify_target_reached(master, row_frame, taskid, target_label, sound_enabled=True):
    """Полный набор уведомлений о достижении цели по заданию."""
    play_notification_sound(sound_enabled)
    show_toast(
        master,
        f"✅ Задание #{taskid}",
        f"Целевой статус достигнут: {target_label}",
        kind="success",
    )
    if row_frame is not None and row_frame.winfo_exists():
        # Мигаем зелёным и ОСТАЁМСЯ зелёными — как у карточек, которые
        # были завершены на момент перерисовки списка.
        flash_row(row_frame, t.COLOR_GREEN, t.COLOR_BORDER_SOFT,
                  settle_color=t.COLOR_GREEN, times=6)


def notify_error(master, taskid, message, sound_enabled=True):
    """Тост об ошибке по заданию (без модального окна — подробности
    доступны по кнопке на карточке)."""
    if sound_enabled and winsound is not None:
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass
    show_toast(
        master,
        f"❌ Задание #{taskid}",
        message[:200],
        kind="error",
        duration_ms=7000,
    )
