"""
telegram_bot.py — Long-polling бот для обработки callback-кнопок (v2.0).

Запускается отдельно: python telegram_bot.py

При нажатии:
  - "✅ Интересно" → убирает кнопки, помечает в SQLite
  - "👎 Не то" → удаляет сообщение, сохраняет в rejections SQLite
"""

import os
import sys
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("telegram_bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API = f"https://api.telegram.org/bot{TOKEN}"

# ── SQLite подключение ────────────────────────────────────────────────

def get_storage():
    """Создаёт и возвращает JobStorage."""
    from storage import JobStorage
    return JobStorage()


# ── Telegram API helpers ─────────────────────────────────────────────

def answer_callback(callback_query_id: str, text: str = ""):
    """Отвечает на callback query (убирает loading)."""
    requests.post(f"{API}/answerCallbackQuery", json={
        "callback_query_id": callback_query_id,
        "text": text,
    }, timeout=10)


def delete_message(chat_id: str, message_id: int):
    """Удаляет сообщение."""
    r = requests.post(f"{API}/deleteMessage", json={
        "chat_id": chat_id,
        "message_id": message_id,
    }, timeout=10)
    return r.json().get("ok", False)


REJECTION_MAP = {
    "rej_stk_": "Не тот стек / роль",
    "rej_cal_": "Требуются звонки / не async",
    "rej_pay_": "Низкая ставка / нет вилки",
    "rej_loc_": "Офис / неподходящая локация",
    "rej_skp_": "Скрыто без указания причины",
}


def build_reason_keyboard(job_id: str) -> dict:
    """Создаёт inline-клавиатуру для быстрого выбора причины отказа."""
    safe_id = str(job_id or "")[:45]
    return {
        "inline_keyboard": [
            [
                {"text": "🛠 Стек / роль", "callback_data": f"rej_stk_{safe_id}"},
                {"text": "📞 Созвоны / не async", "callback_data": f"rej_cal_{safe_id}"},
            ],
            [
                {"text": "💵 Зарплата / вилка", "callback_data": f"rej_pay_{safe_id}"},
                {"text": "🏢 Офис / локация", "callback_data": f"rej_loc_{safe_id}"},
            ],
            [
                {"text": "❌ Скрыть без причины", "callback_data": f"rej_skp_{safe_id}"},
            ],
        ]
    }


def edit_message_reply_markup(chat_id: str, message_id: int, reply_markup: dict | None = None):
    """Обновляет или убирает inline-кнопки из сообщения."""
    markup = reply_markup if reply_markup is not None else {"inline_keyboard": []}
    r = requests.post(f"{API}/editMessageReplyMarkup", json={
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": markup,
    }, timeout=10)
    return r.json().get("ok", False)


def edit_message_text(chat_id: str, message_id: int, text: str):
    """Редактирует текст сообщения."""
    r = requests.post(f"{API}/editMessageText", json={
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }, timeout=10)
    return r.json().get("ok", False)


def _extract_meta(msg: dict) -> tuple[str, str]:
    """Извлекает название и компанию из текста сообщения."""
    title = ""
    company = ""
    if msg.get("text"):
        lines = msg["text"].split("\n")
        for line in lines:
            if "🏢" in line:
                title = line.replace("🏢", "").strip()
            elif "🏭" in line:
                company = line.split("·")[0].replace("🏭", "").strip()
    return title, company


# ── Main loop ────────────────────────────────────────────────────────

def main():
    logger.info("=== Telegram Bot запущен (long polling, v2.1) ===")
    logger.info("Бот: %s, Chat ID: %s", TOKEN[:10] + "..." if TOKEN else "?", CHAT_ID)

    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан!")
        return

    storage = get_storage()
    offset = 0

    while True:
        try:
            resp = requests.get(f"{API}/getUpdates", params={
                "offset": offset,
                "timeout": 30,
            }, timeout=35)
            data = resp.json()

            if not data.get("ok"):
                logger.error("getUpdates error: %s", data)
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                if "callback_query" not in update:
                    continue

                callback = update["callback_query"]
                callback_id = callback["id"]
                data_str = callback.get("data", "")
                msg = callback.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                message_id = msg.get("message_id")

                logger.info("Callback: %s", data_str)

                # ── Обработка "Интересно" ────────────────────────────
                if data_str.startswith("like_") or data_str.startswith("fb_up_"):
                    job_id = data_str[5:] if data_str.startswith("like_") else data_str[6:]
                    answer_callback(callback_id, "✅ Сохранено в избранное!")

                    if message_id:
                        edit_message_reply_markup(chat_id, message_id)
                        logger.info("✅ Интересно: %s", job_id)

                # ── Этап 1: Нажатие "Не то" -> Показываем меню причин ─
                elif data_str.startswith("reject_") or data_str.startswith("fb_down_"):
                    job_id = data_str[7:] if data_str.startswith("reject_") else data_str[8:]
                    answer_callback(callback_id, "Укажите причину отказа")

                    if message_id:
                        reason_kb = build_reason_keyboard(job_id)
                        edit_message_reply_markup(chat_id, message_id, reason_kb)
                        logger.info("👎 Запрос причины отказа для: %s", job_id)

                # ── Этап 2: Выбор конкретной причины отказа ──────────
                elif any(data_str.startswith(prefix) for prefix in REJECTION_MAP):
                    matched_prefix = next(p for p in REJECTION_MAP if data_str.startswith(p))
                    reason_label = REJECTION_MAP[matched_prefix]
                    job_id = data_str[len(matched_prefix):]

                    answer_callback(callback_id, f"Принято: {reason_label}")

                    title, company = _extract_meta(msg)
                    try:
                        storage.save_rejection(job_id, title=title, company=company, reason=reason_label)
                        logger.info("👎 Сохранено отклонение: %s — %s (причина: %s)", company, title, reason_label)
                    except Exception as e:
                        logger.error("Ошибка сохранения отклонения: %s", e)

                    # Удаляем сообщение из чата
                    if message_id:
                        deleted = delete_message(chat_id, message_id)
                        if not deleted:
                            edit_message_text(chat_id, message_id, f"❌ <i>Вакансия отклонена: {reason_label}</i>")
                            edit_message_reply_markup(chat_id, message_id)
                            logger.info("Сообщение отредактировано (удаление недоступно)")
                        else:
                            logger.info("Сообщение %d успешно удалено из чата", message_id)

                # ── Неизвестный callback ─────────────────────────────
                else:
                    answer_callback(callback_id, "Действие принято")
                    logger.warning("Неизвестный callback: %s", data_str)

        except requests.exceptions.Timeout:
            logger.debug("Long poll timeout (нормально)")
            continue
        except requests.exceptions.ConnectionError as e:
            logger.error("Ошибка соединения: %s. Повтор через 10 сек...", e)
            time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем.")
            break
        except Exception as e:
            logger.error("Неожиданная ошибка: %s", e, exc_info=True)
            time.sleep(5)

    storage.close()
    logger.info("=== Telegram Bot остановлен ===")


if __name__ == "__main__":
    main()
