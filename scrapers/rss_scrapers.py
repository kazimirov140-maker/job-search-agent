"""
scrapers/rss_scrapers.py — Сборщики вакансий через RSS-ленты (v2.1).

Источники:
  - Djinni           (djinni.co/jobs/rss/)
  - DOU              (jobs.dou.ua/vacancies/feeds/)
  - WeWorkRemotely   (weworkremotely.com/remote-jobs.rss)
  - Authentic        (authenticjobs.com/feed/)

УДАЛЕНЫ (мёртвые):
  - Layboard         (502)
  - CeeHire          (404)
  - Europeremotely   (436)
  - JobsRemote.ai    (feed redirects to landing)
"""

import html
import logging
import time
from datetime import datetime, timezone

import feedparser
import re
import requests
from bs4 import BeautifulSoup

from config import (
    SEARCH_KEYWORDS,
    DJINNI_FEEDS, DJINNI_RSS_BASE,
    DOU_FEEDS, DOU_RSS_BASE,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

# Browser headers для сайтов, блокирующих requests
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def clean_html(raw_html: str) -> str:
    """Очищает текст от HTML-тегов."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def _clean_title(raw_title: str) -> str:
    """
    Приводит заголовок из RSS в человеческий вид.

    Заголовок брался из фида как есть, вместе с HTML-сущностями, и
    «AI &amp; Automation» в таком виде уезжало и в базу, и в карточку
    Telegram, и в промпт модели. Заодно ломался поиск по ключевым словам:
    «R&D» и «R&amp;D» — разные строки.
    """
    if not raw_title:
        return "Unknown Title"
    cleaned = re.sub(r"\s+", " ", html.unescape(str(raw_title))).strip()
    return cleaned or "Unknown Title"


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    """Возвращает True, если хотя бы одно ключевое слово найдено в тексте."""
    for kw in keywords:
        if re.search(r'\b' + re.escape(kw) + r'\b', text, flags=re.IGNORECASE):
            return True
    return False


# ─────────────────────────────────────────────
# Djinni
# ─────────────────────────────────────────────

def fetch_djinni() -> list[dict]:
    """
    Собирает вакансии с Djinni.co через категорийные RSS-фиды.

    Раньше здесь выполнялся отдельный запрос на КАЖДОЕ ключевое слово с
    параметром ?keywords=. Проверка живыми запросами показала, что Djinni
    этот параметр игнорирует: все сто ответов были байт-в-байт одинаковой
    нефильтрованной лентой. Отсюда и таймауты источника.

    Реально работает ?primary_keyword= со значениями из справочника Djinni;
    "AI", "ML" и "Machine Learning" в нём отсутствуют. Валидные значения
    подобраны в config.DJINNI_FEEDS под консалтинговое позиционирование.
    """
    logger.info("Запрос к Djinni RSS по %d категориям…", len(DJINNI_FEEDS))
    results = []
    seen_ids = set()

    for category in DJINNI_FEEDS:
        try:
            resp = requests.get(
                DJINNI_RSS_BASE,
                params={"primary_keyword": category},
                timeout=REQUEST_TIMEOUT,
                headers=BROWSER_HEADERS,
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries:
                link = getattr(entry, "link", "")
                job_id = f"djinni_{link.split('/')[-2]}" if "/jobs/" in link else f"djinni_{link}"

                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = _clean_title(getattr(entry, "title", ""))
                description = clean_html(
                    getattr(entry, "summary", "") or getattr(entry, "description", "")
                )

                # В RSS Djinni названия компании нет вообще — ни в заголовке,
                # ни отдельным полем. Оставляем Unknown: main.py в этом случае
                # дедуплицирует только по job_id, иначе разные вакансии с
                # одинаковым названием схлопнулись бы в одну.
                company = "Unknown"

                # Теги Djinni (стек, категория) полезны для оценки — дописываем
                # их к описанию, чтобы LLM видела контекст.
                tags = [t.get("term", "") for t in getattr(entry, "tags", []) if t.get("term")]
                if tags:
                    description = "Теги: " + ", ".join(tags) + "\n" + description

                # Отсекаем вакансии строго только в офисе
                desc_lower = description.lower()
                if "тільки офіс" in desc_lower or "only office" in desc_lower or "on-site only" in desc_lower:
                    continue

                date_published = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                    date_published = dt.strftime("%Y-%m-%d")

                results.append({
                    "job_id": job_id,
                    "source": "djinni",
                    "title": title.strip(),
                    "company": company,
                    "location": "Remote",
                    "description": description[:2500],
                    "url": link,
                    "date_published": date_published,
                })

            logger.info("  Djinni «%s»: %d записей в фиде", category, len(feed.entries))
        except Exception as e:
            logger.error("Ошибка Djinni RSS «%s»: %s", category, e)

        time.sleep(1)

    logger.info("Djinni: %d уникальных вакансий.", len(results))
    return results


# ─────────────────────────────────────────────
# DOU (jobs.dou.ua)
# ─────────────────────────────────────────────

def fetch_dou() -> list[dict]:
    """
    Собирает вакансии с DOU через RSS.

    Прежний параметр ?q= DOU игнорирует — сто запросов возвращали одну и ту же
    нефильтрованную ленту. Рабочие параметры проверены живыми запросами:
    ?category=<название> и ?search=<строка>, причём search корректно
    отрабатывает кириллицу. Наборы заданы в config.DOU_FEEDS.
    """
    logger.info("Запрос к DOU RSS по %d фидам…", len(DOU_FEEDS))
    results = []
    seen_ids = set()

    for params in DOU_FEEDS:
        label = ", ".join(f"{k}={v}" for k, v in params.items())
        try:
            resp = requests.get(
                DOU_RSS_BASE, params=params,
                timeout=REQUEST_TIMEOUT, headers=BROWSER_HEADERS,
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries:
                link = getattr(entry, "link", "")
                if not link:
                    continue

                # Все ссылки DOU заканчиваются на ?utm_source=jobsrss, поэтому
                # прежнее split('/')[-1] давало ОДИН И ТОТ ЖЕ идентификатор для
                # всех вакансий — из 150 записей до пайплайна доходила одна.
                # Берём числовой id вакансии из пути.
                id_match = re.search(r"/vacancies/(\d+)", link)
                job_id = f"dou_{id_match.group(1)}" if id_match else f"dou_{link.split('?')[0].rstrip('/').split('/')[-1]}"

                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = _clean_title(getattr(entry, "title", ""))
                company = "Unknown"
                location = ""

                raw_desc = getattr(entry, "summary", "") or getattr(entry, "description", "")
                description = clean_html(raw_desc)

                # Формат заголовка DOU: "Позиція в Компанія, Місто, віддалено"
                head_match = re.match(r"^(.*?)\s+в\s+(.+)$", title)
                if head_match:
                    title = head_match.group(1).strip()
                    tail = [part.strip() for part in head_match.group(2).split(",")]
                    company = tail[0] if tail else "Unknown"
                    location = ", ".join(tail[1:]) if len(tail) > 1 else ""

                # Строгая проверка на удаленный формат:
                # На DOU удаленные вакансии всегда содержат "віддалено" / "remote" в локации или описании
                full_text = f"{title} {location} {description}".lower()
                is_remote = any(w in full_text for w in ["віддалено", "remote", "remotely", "дистанційно", "удаленно", "telecommute"])
                if not is_remote:
                    # Чисто офисная вакансия в городе без удаленки — отсекаем
                    continue

                date_published = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                    date_published = dt.strftime("%Y-%m-%d")

                results.append({
                    "job_id": job_id,
                    "source": "dou",
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": location or "Remote",
                    "description": description[:2500],
                    "url": link,
                    "date_published": date_published,
                })

            logger.info("  DOU «%s»: %d записей в фиде", label, len(feed.entries))
        except Exception as e:
            logger.error("Ошибка DOU RSS «%s»: %s", label, e)

        time.sleep(1)

    logger.info("DOU: %d уникальных вакансий.", len(results))
    return results


from config import (
    SEARCH_KEYWORDS,
    DJINNI_FEEDS, DJINNI_RSS_BASE,
    DOU_FEEDS, DOU_RSS_BASE,
    WWR_FEEDS,
)

# ─────────────────────────────────────────────
# WeWorkRemotely (Category RSS Feeds)
# ─────────────────────────────────────────────

def fetch_weworkremotely() -> list[dict]:
    """
    Собирает вакансии с WeWorkRemotely через категорийные RSS-фиды.
    Охватывает general feed, customer support, product, management & operations.
    """
    logger.info("Запрос к WeWorkRemotely RSS по %d лентам…", len(WWR_FEEDS))
    results = []
    seen_ids = set()

    for feed_url in WWR_FEEDS:
        try:
            resp = requests.get(feed_url, timeout=REQUEST_TIMEOUT, headers=BROWSER_HEADERS)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                link = getattr(entry, "link", "")
                raw_slug = link.rstrip('/').split('/')[-1] if link else str(hash(getattr(entry, "title", "")))
                job_id = f"weworkremotely_{raw_slug}"

                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = _clean_title(getattr(entry, "title", ""))
                description = clean_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))

                text_to_search = title + " " + description
                if not _contains_keyword(text_to_search, SEARCH_KEYWORDS):
                    continue

                company = "Unknown"
                if ": " in title:
                    parts = title.split(": ", 1)
                    company = parts[0].strip()
                    title = parts[1].strip()

                date_published = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                    date_published = dt.strftime("%Y-%m-%d")

                results.append({
                    "job_id": job_id,
                    "source": "weworkremotely",
                    "title": title.strip(),
                    "company": company,
                    "location": "Worldwide Remote",
                    "description": description[:2500],
                    "url": link,
                    "date_published": date_published,
                })
            time.sleep(0.5)
        except Exception as e:
            logger.error("Ошибка WeWorkRemotely RSS (%s): %s", feed_url, e)

    logger.info("WeWorkRemotely: %d релевантных вакансий.", len(results))
    return results


# ─────────────────────────────────────────────
# Nodesk (Международный Remote-first RSS)
# ─────────────────────────────────────────────

def fetch_nodesk() -> list[dict]:
    """
    Собирает вакансии с открытого RSS-фида Nodesk (чистый remote).
    URL: https://nodesk.co/remote-jobs/index.xml
    """
    logger.info("Запрос к Nodesk RSS…")
    url = "https://nodesk.co/remote-jobs/index.xml"
    results = []
    seen_ids = set()

    try:
        resp = requests.get(url, timeout=7, headers=BROWSER_HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        for entry in feed.entries:
            link = getattr(entry, "link", "")
            raw_id = link.rstrip("/").split("/")[-1] if link else str(hash(getattr(entry, "title", "")))
            job_id = f"nodesk_{raw_id}"

            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            title = _clean_title(getattr(entry, "title", ""))
            description = clean_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))

            # Проверка ключевых слов
            text_to_search = f"{title} {description}"
            if not _contains_keyword(text_to_search, SEARCH_KEYWORDS):
                continue

            company = "Unknown"
            if " at " in title:
                parts = title.split(" at ", 1)
                title = parts[0].strip()
                company = parts[1].strip()

            date_published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                date_published = dt.strftime("%Y-%m-%d")

            results.append({
                "job_id": job_id,
                "source": "nodesk",
                "title": title.strip(),
                "company": company.strip(),
                "location": "Worldwide Remote",
                "description": description[:2500],
                "url": link,
                "date_published": date_published,
            })
    except Exception as exc:
        logger.warning("Nodesk: сбой запроса (%s). Пропускаем.", exc)

    logger.info("Nodesk: %d релевантных вакансий.", len(results))
    return results


# ─────────────────────────────────────────────
# Хабр Карьера (career.habr.com) — Валютная удаленка диаспоры
# ─────────────────────────────────────────────

def fetch_habr_career() -> list[dict]:
    """
    Собирает удаленные вакансии с Хабр Карьеры (career.habr.com).
    Все вакансии автоматически проверяются через geo_filter:
    внутрироссийские (с рублями и ТК РФ) отсекаются, а международная
    удаленка русскоязычной диаспоры (Кипр, Казахстан, Армения, Грузия, ЕС) проходит.
    """
    logger.info("Запрос к Хабр Карьера RSS (remote=1)…")
    url = "https://career.habr.com/vacancies/rss?remote=1"
    results = []
    seen_ids = set()

    # Импорт детектора гео-юрисдикции для мгновенного отсева РФ
    from geo_filter import detect_jurisdiction

    try:
        resp = requests.get(url, timeout=7, headers=BROWSER_HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        for entry in feed.entries:
            link = getattr(entry, "link", "")
            raw_id = link.rstrip("/").split("/")[-1] if link else str(hash(getattr(entry, "title", "")))
            job_id = f"habr_{raw_id}"

            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            title = _clean_title(getattr(entry, "title", ""))
            description = clean_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))

            # Отсекаем по ключевым словам
            text_to_search = f"{title} {description}"
            if not _contains_keyword(text_to_search, SEARCH_KEYWORDS):
                continue

            company = "Unknown"
            if " в " in title:
                parts = title.split(" в ", 1)
                title = parts[0].strip()
                company = parts[1].strip()
            elif " at " in title:
                parts = title.split(" at ", 1)
                title = parts[0].strip()
                company = parts[1].strip()

            date_published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                date_published = dt.strftime("%Y-%m-%d")

            candidate_job = {
                "job_id": job_id,
                "source": "habrcareer",
                "title": title.strip(),
                "company": company.strip(),
                "location": "Remote",
                "description": description[:2500],
                "url": link,
                "date_published": date_published,
            }

            # Мгновенная проверка гео-юрисдикции: блокируем юрисдикцию РФ
            blocked, _ = detect_jurisdiction(candidate_job, link)
            if blocked:
                continue

            results.append(candidate_job)
    except Exception as exc:
        logger.warning("Хабр Карьера: сбой запроса (%s). Пропускаем.", exc)

    logger.info("Хабр Карьера: %d релевантных вакансий диаспоры.", len(results))
    return results

# ─────────────────────────────────────────────
# New Generic RSS Scrapers (Added via expansion task)
# ─────────────────────────────────────────────

def _fetch_generic_rss(url: str, source_name: str) -> list[dict]:
    logger.info("Запрос к %s RSS…", source_name)
    results = []
    seen_ids = set()
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=BROWSER_HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            link = getattr(entry, "link", "")
            raw_id = link.rstrip("/").split("/")[-1] if link else str(hash(getattr(entry, "title", "")))
            job_id = f"{source_name}_{raw_id}"

            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            title = _clean_title(getattr(entry, "title", ""))
            description = clean_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))

            # Проверка ключевых слов
            text_to_search = f"{title} {description}"
            if not _contains_keyword(text_to_search, SEARCH_KEYWORDS):
                continue

            company = "Unknown"
            if " at " in title:
                parts = title.split(" at ", 1)
                title = parts[0].strip()
                company = parts[1].strip()

            date_published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                date_published = dt.strftime("%Y-%m-%d")

            results.append({
                "job_id": job_id,
                "source": source_name,
                "title": title.strip(),
                "company": company.strip(),
                "location": "Worldwide Remote",
                "description": description[:2500],
                "url": link,
                "date_published": date_published,
            })
    except Exception as exc:
        logger.warning("%s: сбой запроса (%s). Пропускаем.", source_name, exc)

    logger.info("%s: %d релевантных вакансий.", source_name, len(results))
    return results

def fetch_remoteco() -> list[dict]:
    return _fetch_generic_rss("https://remote.co/remote-jobs/feed/", "remote.co")

def fetch_jobspresso() -> list[dict]:
    return _fetch_generic_rss("https://jobspresso.co/remote-work/feed/", "jobspresso")

def fetch_dailyremote() -> list[dict]:
    return _fetch_generic_rss("https://dailyremote.com/remote-jobs.rss", "dailyremote")

def fetch_justremote() -> list[dict]:
    return _fetch_generic_rss("https://justremote.co/remote-jobs/rss", "justremote")

def fetch_remoteworkhub() -> list[dict]:
    return _fetch_generic_rss("https://remoteworkhub.com/feed/", "remoteworkhub")

from config import UPWORK_RSS_URL

def fetch_upwork() -> list[dict]:
    if not UPWORK_RSS_URL:
        logger.debug("Upwork RSS URL не задан, пропускаем.")
        return []
    return _fetch_generic_rss(UPWORK_RSS_URL, "upwork")
