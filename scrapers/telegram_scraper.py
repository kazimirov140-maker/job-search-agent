"""
scrapers/telegram_scraper.py — Сборщик вакансий из открытых Telegram-каналов (Web Preview).

Не требует токенов ботов или Telegram API/Telethon.
Парсит официальную веб-версию публичных каналов через https://t.me/s/<channel>.
"""

import html
import logging
import re
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

from config import SEARCH_KEYWORDS
from geo_filter import detect_jurisdiction

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 7  # секунд

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Открытые каналы с вакансиями по удаленке, AI и автоматизации
TELEGRAM_CHANNELS = [
    "normrabota",    # Норм работа (крупнейший канал белой удаленки)
    "remoteit",      # Remote IT вакансии
    "ai_jobs",       # AI & Machine Learning вакансии
]


def _clean_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    for kw in keywords:
        if re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text, flags=re.IGNORECASE | re.UNICODE):
            return True
    return False


def fetch_telegram_channels() -> list[dict]:
    """
    Собирает свежие вакансии из открытых веб-превью Telegram-каналов.
    Каждая вакансия проходит проверку на стоп-юрисдикцию РФ/РБ (отсекаются рубли и ТК РФ).
    """
    logger.info("Запрос к Telegram Web Channels (%d каналов)…", len(TELEGRAM_CHANNELS))
    results = []
    seen_ids = set()

    for channel in TELEGRAM_CHANNELS:
        url = f"https://t.me/s/{channel}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.debug("Telegram [%s]: HTTP %d", channel, resp.status_code)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            message_wraps = soup.find_all("div", class_="tgme_widget_message_wrap")

            for wrap in message_wraps:
                msg_div = wrap.find("div", class_="tgme_widget_message")
                if not msg_div:
                    continue

                msg_data = msg_div.get("data-post", "")
                if not msg_data or "/" not in msg_data:
                    continue

                msg_id = msg_data.split("/")[-1]
                job_id = f"telegram_{channel}_{msg_id}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                text_div = wrap.find("div", class_="tgme_widget_message_text")
                if not text_div:
                    continue

                raw_text = text_div.get_text(separator="\n", strip=True)
                if len(raw_text) < 50:
                    continue

                # Проверяем наличие ключевых слов
                if not _contains_keyword(raw_text, SEARCH_KEYWORDS):
                    continue

                lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
                first_line = lines[0] if lines else "AI / Automation Specialist"
                # Заголовок часто вида: "Компания ищет Роль" или "Позиция в Компании"
                title = first_line[:100]
                company = f"@{channel}"

                if " в " in first_line:
                    p = first_line.split(" в ", 1)
                    title, company = p[0].strip(), p[1].strip()
                elif " at " in first_line:
                    p = first_line.split(" at ", 1)
                    title, company = p[0].strip(), p[1].strip()
                elif " | " in first_line:
                    p = first_line.split(" | ", 1)
                    title, company = p[0].strip(), p[1].strip()

                post_url = f"https://t.me/{channel}/{msg_id}"
                date_published = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

                candidate_job = {
                    "job_id": job_id,
                    "source": "telegram",
                    "title": title,
                    "company": company,
                    "location": "Remote",
                    "description": raw_text[:2500],
                    "url": post_url,
                    "date_published": date_published,
                }

                # Отсекаем юрисдикцию РФ (рубли, ТК РФ, города РФ)
                blocked, _ = detect_jurisdiction(candidate_job, post_url)
                if blocked:
                    continue

                results.append(candidate_job)

        except Exception as exc:
            logger.warning("Telegram [%s]: сбой (%s). Пропускаем.", channel, exc)

    logger.info("Telegram Channels: итого %d релевантных вакансий.", len(results))
    return results
