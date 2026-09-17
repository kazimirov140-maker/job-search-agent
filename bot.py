"""
bot.py — Интерактивный Telegram-бот для Job Search Agent (Webhook версия).

Этот скрипт работает как веб-сервер (Flask) в Google Cloud Run.
Telegram присылает POST-запросы (webhooks) при нажатии на кнопки или ответах.
"""

import logging
import os
import sys

from dotenv import load_dotenv
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, abort

from storage import JobStorage

# ─────────────────────────────────────────────
# Логирование
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Инициализация
# ─────────────────────────────────────────────
load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
    logger.error("TELEGRAM_BOT_TOKEN не задан в .env")
    sys.exit(1)

bot = telebot.TeleBot(token)
storage = JobStorage()
app = Flask(__name__)

# ─────────────────────────────────────────────
# Обработчики Callback-запросов (кнопок)
# ─────────────────────────────────────────────

def build_reason_keyboard(job_id: str):
    safe_id = str(job_id or "")[:45]
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🛠 Стек / роль", callback_data=f"rej_stk_{safe_id}"),
        InlineKeyboardButton("📞 Созвоны / не async", callback_data=f"rej_cal_{safe_id}")
    )
    markup.row(
        InlineKeyboardButton("💵 Зарплата / вилка", callback_data=f"rej_pay_{safe_id}"),
        InlineKeyboardButton("🏢 Офис / локация", callback_data=f"rej_loc_{safe_id}")
    )
    markup.row(
        InlineKeyboardButton("❌ Скрыть без причины", callback_data=f"rej_skp_{safe_id}")
    )
    return markup


@bot.callback_query_handler(func=lambda call: True)
def handle_feedback(call):
    data_str = call.data or ""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # ── Этап 1: Нажатие "Не то" -> Показываем меню причин ──
    if data_str.startswith("reject_") or data_str.startswith("fb_down_"):
        job_id = data_str[7:] if data_str.startswith("reject_") else data_str[8:]
        bot.answer_callback_query(call.id, "Укажите причину отказа")
        reason_kb = build_reason_keyboard(job_id)
        try:
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reason_kb)
            logger.info("👎 Запрос причины отказа для: %s", job_id)
        except Exception as e:
            logger.error("Не удалось обновить кнопки (причины): %s", e)
        return

    # ── Этап 2: Выбор конкретной причины отказа ──────────
    REJECTION_MAP = {
        "rej_stk_": "Не тот стек / роль",
        "rej_cal_": "Требуются звонки / не async",
        "rej_pay_": "Низкая ставка / нет вилки",
        "rej_loc_": "Офис / неподходящая локация",
        "rej_skp_": "Скрыто без указания причины",
    }
    
    if any(data_str.startswith(prefix) for prefix in REJECTION_MAP):
        matched_prefix = next(p for p in REJECTION_MAP if data_str.startswith(p))
        reason_label = REJECTION_MAP[matched_prefix]
        job_id = data_str[len(matched_prefix):]

        bot.answer_callback_query(call.id, f"Принято: {reason_label}")
        
        # Сохраняем отклонение в SQLite
        title = ""
        company = ""
        try:
            if call.message.text:
                for line in call.message.text.split("\n"):
                    if "🏢" in line:
                        title = line.replace("🏢", "").strip()
                    elif "🏭" in line:
                        company = line.split("·")[0].replace("🏭", "").strip()
            storage.save_rejection(job_id, title=title, company=company, reason=reason_label)
            logger.info("👎 Сохранено отклонение: %s — %s (причина: %s)", company, title, reason_label)
        except Exception as e:
            logger.error("Ошибка сохранения отклонения: %s", e)

        # Удаляем сообщение
        try:
            bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.warning("Не удалось удалить сообщение, редактируем: %s", e)
            try:
                bot.edit_message_text(
                    f"❌ <i>Вакансия отклонена: {reason_label}</i>",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode="HTML",
                    reply_markup=None
                )
            except Exception as edit_err:
                logger.error("Не удалось отредактировать сообщение: %s", edit_err)
        return

    # ── Обработка "Интересно" ────────────────────────────
    if data_str.startswith("like_") or data_str.startswith("fb_up_"):
        job_id = data_str[5:] if data_str.startswith("like_") else data_str[6:]
        bot.answer_callback_query(call.id, "✅ Отмечено как интересное!")
        try:
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
            logger.info("✅ Интересно: %s", job_id)
        except Exception as e:
            logger.error("Не удалось обновить кнопки: %s", e)
        return

    # ── Неизвестный callback ─────────────────────────────
    bot.answer_callback_query(call.id, "Действие принято")

# ─────────────────────────────────────────────
# Flask Webhook Endpoints
# ─────────────────────────────────────────────

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/', methods=['GET'])
def index():
    return "AI Job Search Agent Telegram Bot is running.", 200

if __name__ == "__main__":
    # Локальный запуск (в Cloud Run будет использоваться gunicorn)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
