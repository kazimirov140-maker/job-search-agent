"""
scrapers/upwork_scraper.py — Сборщик проектов с Upwork через публичный поиск.

Использует публичный JSON API Upwork (без авторизации) для мониторинга свежих
заказов по ключевым словам. SLA отклика — в идеале < 5 минут после публикации.
"""

import logging
import os
import sys
import time
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# Поддержка запуска как скрипта напрямую (python scrapers/upwork_scraper.py)
if __name__ == "__main__" or "config" not in sys.modules:
    _parent = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    if _parent not in sys.path:
        sys.path.insert(0, _parent)

from config import SEARCH_KEYWORDS

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Ключевые слова специально под Upwork (короткие, высокочастотные)
# Используем только самые релевантные, чтобы не жечь requests
UPWORK_KEYWORDS = [
    "AI automation",
    "LLM",
    "prompt engineering",
    "n8n",
    "Make.com",
    "no-code automation",
    "AI agent",
    "Python automation",
    "workflow automation",
    "Zapier",
    "AI solutions",
]


def clean_html(raw: str) -> str:
    """Удаляет HTML-теги из текста."""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def _fetch_via_html(kw: str, seen_ids: set) -> list[dict]:
    """
    Попытка получить проекты через HTML-страницу поиска Upwork.
    Upwork — SPA на React, поэтому этот метод может вернуть 0 результатов,
    если страница требует JS-рендеринга.
    """
    results = []
    # Актуальный URL поиска (старый /search/jobs/ → 410 Gone)
    url = (
        "https://www.upwork.com/nx/search/jobs/"
        f"?q={requests.utils.quote(kw)}"
        "&sort=recency"
        "&remote_job=1"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)

        if resp.status_code == 429:
            logger.warning("Upwork: Rate limit (429) для '%s'.", kw)
            time.sleep(15)
            return results

        if resp.status_code in (403, 410):
            logger.debug("Upwork HTML: HTTP %d для '%s' — страница недоступна без JS.", resp.status_code, kw)
            return results

        if resp.status_code != 200:
            logger.error("Upwork HTML: HTTP %d для '%s'.", resp.status_code, kw)
            return results

        soup = BeautifulSoup(resp.text, "html.parser")

        # Upwork SPA — ищем pre-rendered JSON в <script type="application/json">
        for script in soup.find_all("script", type="application/json"):
            try:
                data = __import__("json").loads(script.string or "")
                # Ищем массив jobs в структуре данных
                jobs_data = None
                if isinstance(data, dict):
                    jobs_data = (
                        data.get("results") or
                        data.get("jobs") or
                        (data.get("data", {}) or {}).get("jobs", {}).get("results")
                    )
                if not jobs_data:
                    continue
                for item in jobs_data:
                    job_id = f"upwork_{item.get('id', item.get('uid', ''))}"
                    if job_id in seen_ids or not item.get("id"):
                        continue
                    seen_ids.add(job_id)
                    results.append({
                        "job_id": job_id,
                        "source": "upwork",
                        "title": item.get("title", "Unknown"),
                        "company": "Client on Upwork",
                        "description": (item.get("description") or "")[:2500],
                        "url": f"https://www.upwork.com/jobs/~{item.get('id', '')}",
                        "date_published": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
                    })
            except Exception:
                continue

        # Резервный вариант — классические HTML-блоки (если Upwork вдруг отдаст HTML)
        if not results:
            job_tiles = (
                soup.find_all("section", attrs={"data-test": "job-tile"}) or
                soup.find_all("div", class_=re.compile(r"job-tile"))
            )
            for tile in job_tiles:
                title_elem = (
                    tile.find("a", attrs={"data-test": "job-tile-title-link"}) or
                    tile.find("h2") or
                    tile.find("a", class_=re.compile(r"title"))
                )
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                href = title_elem.get("href", "")
                if href.startswith("/"):
                    href = "https://www.upwork.com" + href
                uid_match = re.search(r"~([0-9a-f]{16,})", href)
                raw_id = uid_match.group(1) if uid_match else href.split("/")[-1].split("?")[0]
                job_id = f"upwork_{raw_id}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                desc_elem = tile.find(attrs={"data-test": "job-description-text"})
                results.append({
                    "job_id": job_id,
                    "source": "upwork",
                    "title": title,
                    "company": "Client on Upwork",
                    "description": clean_html(str(desc_elem))[:2500] if desc_elem else "",
                    "url": href,
                    "date_published": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
                })

    except requests.exceptions.Timeout:
        logger.error("Upwork HTML: таймаут для '%s'.", kw)
    except Exception as exc:
        logger.error("Upwork HTML: ошибка для '%s': %s", kw, exc)

    return results


def fetch_upwork() -> list[dict]:
    """
    Собирает проекты с Upwork по ключевым словам.

    Метод: HTML-парсинг страницы /nx/search/jobs/ с попыткой извлечь
    pre-rendered JSON из <script> тегов.

    Если Upwork заблокирует HTML-доступ — используй email-алерты:
    В настройках Upwork включи «Job Alerts» → письма придут от noreply@upwork.com
    и будут автоматически обработаны email_scrapers.py (upwork.com уже в valid_senders).
    """
    logger.info("Сбор проектов с Upwork по %d ключевым словам…", len(UPWORK_KEYWORDS))
    results: list[dict] = []
    seen_ids: set[str] = set()

    for kw in UPWORK_KEYWORDS:
        batch = _fetch_via_html(kw, seen_ids)
        results.extend(batch)
        logger.debug("Upwork: '%s' → %d новых проектов.", kw, len(batch))
        time.sleep(3)  # вежливая пауза

    logger.info("Upwork: собрано %d уникальных проектов.", len(results))
    if len(results) == 0:
        logger.info(
            "Upwork: 0 проектов (JS-рендеринг). "
            "Для надёжного мониторинга настрой Job Alerts в Upwork — "
            "письма от upwork.com будут обработаны автоматически через email_scrapers."
        )
    return results



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    jobs = fetch_upwork()
    print(f"\nНайдено проектов: {len(jobs)}")
    for j in jobs[:3]:
        print(f"\n{'─'*60}")
        print(f"[{j['source']}] {j['title']}")
        print(f"URL: {j['url']}")
        print(f"Описание: {j['description'][:200]}")
