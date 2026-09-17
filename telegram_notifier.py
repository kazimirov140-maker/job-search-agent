"""
telegram_notifier.py — Отправка уведомлений о вакансиях в Telegram (v2.0).

Изменения v2.0:
  - Новый формат карточки (priority / обычное)
  - Убрана кнопка "Скопировать письмо" (cover letter)
  - Добавлены кнопки "Интересно" / "Не то"
  - Пороги: 75-84 обычное, 85+ приоритетное
"""

import logging
import os

import requests

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
# Константы
# ─────────────────────────────────────────────

REQUEST_TIMEOUT: int = 15  # секунд
_TG_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Максимальная длина одного сообщения Telegram (лимит API — 4096 символов)
_TG_MAX_LENGTH: int = 4096


# ─────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────

def _escape_html(text: str) -> str:
    """Экранирует спецсимволы HTML для безопасной вставки в parse_mode=HTML."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_message(job_data: dict, priority: bool = False) -> str:
    """
    Формирует HTML-сообщение для Telegram (v2.0).

    Args:
        job_data:  Словарь с данными вакансии.
        priority:  True для приоритетных вакансий (score 85+).

    Returns:
        Строка в HTML-разметке Telegram.
    """
    title       = _escape_html(job_data.get("title", "—"))
    company     = _escape_html(job_data.get("company", "—"))
    url         = job_data.get("url", "")
    match_score = job_data.get("match_score", "—")
    source      = _escape_html(job_data.get("source", "—"))
    location    = _escape_html(job_data.get("location", "Remote"))

    priority_category = _escape_html(job_data.get("priority_category", "—"))
    why_match   = _escape_html(job_data.get("why_match", "—"))
    red_flags   = _escape_html(str(job_data.get("red_flags", "")))

    # Заголовок в зависимости от приоритета
    if priority:
        header = "🔥 <b>ПРИОРИТЕТ — AI AUTOMATION</b>"
    else:
        header = "🤖 <b>AI AUTOMATION JOB</b>"

    language_label = _escape_html(str(job_data.get("language_label", "")))
    base_score = job_data.get("base_score")
    notes = job_data.get("score_notes") or []

    message = (
        f"{header}\n"
        f"\n"
        f"🏢 <b>{title}</b>\n"
        f"🏭 {company} · 📍 {location}\n"
        f"🌐 {source} · ⭐ Score: {match_score}/100\n"
    )

    if language_label:
        message += f"🗣 Язык: {language_label}\n"

    # Показываем, из чего сложился итоговый балл: базовая оценка соответствия
    # профилю плюс детерминированные надбавки. Без этого непонятно, почему
    # вакансия перешагнула порог.
    if base_score is not None and notes:
        detail = _escape_html(" ".join(str(n) for n in notes))
        message += f"🧮 База {base_score} → {detail}\n"

    message += (
        f"\n"
        f"✅ <b>Почему подходит:</b>\n"
        f"{why_match}\n"
    )

    if red_flags and red_flags.lower() not in ("null", "none", "—", ""):
        message += (
            f"\n"
            f"⚠️ <b>Стоп-факторы:</b>\n"
            f"{red_flags}\n"
        )
    return message


def _build_track2_message(platform_data: dict) -> str:
    """
    Формирует HTML-сообщение для Трека 2 (AI Evaluation / подработка).

    Args:
        platform_data: Словарь с полями:
            - platform: Название платформы (Outlier, Alignerr и т.д.)
            - title:    Название проекта/задачи
            - url:      Ссылка
            - rate:     Ставка ($25-40/hr)
            - task_type: Тип задачи (LLM Evaluation, Data Labeling и т.д.)
            - status:   Статус (Новый проект, Набор открыт и т.д.)

    Returns:
        Строка в HTML-разметке Telegram.
    """
    platform  = _escape_html(platform_data.get("platform", "—"))
    title     = _escape_html(platform_data.get("title", "Новый проект доступен"))
    rate      = _escape_html(platform_data.get("rate", "$20-40/hr"))
    task_type = _escape_html(platform_data.get("task_type", "AI Evaluation"))
    status    = _escape_html(platform_data.get("status", "Доступно"))

    message = (
        f"💰 <b>AI EVALUATION [{rate}]</b>\n"
        f"\n"
        f"🏢 {platform}\n"
        f"📋 {title} · 💼 Freelance · 🌍 Remote\n"
        f"📌 Статус: {status}\n"
    )
    return message


def _build_keyboard(job_id: str, url: str) -> dict:
    """
    Формирует inline-клавиатуру с кнопками.

    Telegram Bot API строго ограничивает callback_data размером в 64 байта.
    Обрезаем job_id до 50 символов, чтобы `like_` и `reject_` гарантированно
    укладывались в лимит.
    """
    safe_job_id = str(job_id or "")[:50]
    rows = [
        [
            {"text": "✅ Интересно", "callback_data": f"like_{safe_job_id}"},
            {"text": "👎 Не то", "callback_data": f"reject_{safe_job_id}"},
        ]
    ]

    # Telegram отвечает 400 BUTTON_URL_INVALID на кнопку с пустым или
    # относительным адресом, и всё сообщение не доставляется. У вакансий
    # из email-дайджестов ссылка часто отсутствует, поэтому кнопка
    # добавляется только когда адрес действительно пригоден.
    if url and url.startswith(("http://", "https://")):
        rows.append([{"text": "🔗 Открыть вакансию", "url": url}])

    return {"inline_keyboard": rows}


def _send_text(token: str, chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
    """
    Отправляет одно HTML-сообщение через Telegram Bot API.

    Returns:
        True при успехе, False при ошибке.
    """
    url = _TG_API_BASE.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat_id,
        "text": text[:_TG_MAX_LENGTH],
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if not response.ok:
            logger.error("Telegram API вернул ошибку (%d): %s", response.status_code, response.text)
            return False
        logger.debug("Telegram API ответил: %s", response.json())
        return True

    except requests.exceptions.Timeout:
        logger.error("Telegram API: таймаут (%d с).", REQUEST_TIMEOUT)
        return False
    except requests.exceptions.ConnectionError as exc:
        logger.error("Telegram API: ошибка соединения — %s", exc)
        return False
    except requests.exceptions.HTTPError as exc:
        logger.error("Telegram API: HTTP ошибка — %s", exc)
        return False
    except Exception as exc:
        logger.error("Telegram API: неожиданная ошибка — %s", exc)
        return False


# ─────────────────────────────────────────────
# Публичные функции
# ─────────────────────────────────────────────

def send_job_to_telegram(job_data: dict, priority: bool = False) -> bool:
    """
    Отправляет карточку вакансии в Telegram с кнопками.

    Args:
        job_data:  Словарь с данными вакансии (title, company, url, match_score и др.)
        priority:  True для приоритетных вакансий (score 85+)

    Returns:
        True при успехе, False при ошибке.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы.")
        return False

    message = _build_message(job_data, priority=priority)
    job_id = job_data.get("job_id", "unknown")
    url = job_data.get("url", "")
    keyboard = _build_keyboard(job_id, url)

    success = _send_text(token, chat_id, message, reply_markup=keyboard)

    if success:
        score = job_data.get("match_score", "?")
        title = job_data.get("title", "?")
        if priority:
            logger.info("🔥 ПРИОРИТЕТ [%s]: %s (score=%s)", job_id, title, score)
        else:
            logger.info("📨 Отправлено [%s]: %s (score=%s)", job_id, title, score)
    else:
        logger.error("❌ Не удалось отправить: %s", job_data.get("title", "?"))

    return success


def send_text_to_telegram(text: str) -> bool:
    """
    Отправляет произвольный текст в Telegram (для системных сообщений).

    Args:
        text: Текст сообщения (HTML)

    Returns:
        True при успехе, False при ошибке.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы.")
        return False

    return _send_text(token, chat_id, text)


def send_track2_to_telegram(platform_data: dict) -> bool:
    """
    Отправляет карточку Трека 2 (AI Evaluation) в Telegram.

    Args:
        platform_data: Словарь с полями platform, title, url, rate, task_type, status.

    Returns:
        True при успехе, False при ошибке.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы.")
        return False

    message = _build_track2_message(platform_data)
    url = platform_data.get("url", "")
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔗 Открыть платформу", "url": url}],
        ]
    } if url else None

    success = _send_text(token, chat_id, message, reply_markup=keyboard)

    if success:
        logger.info("💰 Трек 2 [%s]: %s", platform_data.get("platform"), platform_data.get("title"))
    else:
        logger.error("❌ Трек 2 не отправлен: %s", platform_data.get("platform"))

    return success


def send_summary_to_telegram(total_found: int, total_sent: int, total_priority: int,
                              sources_stats: dict | None = None) -> bool:
    """
    Отправляет итоговую сводку после завершения поиска.

    Args:
        total_found:    Всего найдено вакансий
        total_sent:     Отправлено в Telegram (score >= 75)
        total_priority: Приоритетных (score >= 85)
        sources_stats:  Статистика по источникам (опционально)
    """
    message = (
        f"📊 <b>Сводка поиска</b>\n"
        f"{'─' * 32}\n"
        f"🔍 Найдено вакансий: {total_found}\n"
        f"📨 Отправлено в Telegram: {total_sent}\n"
        f"🔥 Из них приоритетных: {total_priority}\n"
    )

    if sources_stats:
        message += f"\n<b>По источникам:</b>\n"
        for source, count in sorted(sources_stats.items(), key=lambda x: x[1], reverse=True):
            message += f"  • {source}: {count}\n"

    return send_text_to_telegram(message)
