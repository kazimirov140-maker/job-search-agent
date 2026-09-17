"""
scrapers/api_scrapers.py — Сборщики вакансий через открытые API (v4.2).

Реализованные источники:
  - RemoteOK      (https://remoteok.com/api) — pure remote/async jobs
  - Arbeitnow     (https://www.arbeitnow.com/api/job-board-api) — EU/Worldwide remote jobs
  - Jobicy        (https://jobicy.com/api/v2/remote-jobs)
  - Himalayas     (https://himalayas.app/jobs/api)
  - Remotive      (https://remotive.com/api/remote-jobs)
  - Adzuna        (https://api.adzuna.com/v1/api/jobs/{country}/search/{page}) [при наличии ключей]

Каждая функция возвращает список словарей единого формата:
{
    "job_id":        str,
    "source":        str,
    "title":         str,
    "company":       str,
    "location":      str,
    "description":   str,
    "url":           str,
    "date_published": str,   # ISO-8601, например "2026-08-28"
}
"""

import html
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
import requests

from config import SEARCH_KEYWORDS

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT: int = 15  # секунд
FILTER_DAYS: int = 21      # горизонт поиска (дней)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_text(raw_html: str) -> str:
    """Очищает HTML-теги и лишние пробелы."""
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _contains_keyword(text: str, keywords: set[str] | list[str]) -> bool:
    """Возвращает True, если любое из ключевых слов встречается в тексте."""
    for kw in keywords:
        if re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text, flags=re.IGNORECASE | re.UNICODE):
            return True
    return False


# ─────────────────────────────────────────────
# RemoteOK (Pure Remote & Async Jobs)
# ─────────────────────────────────────────────

def fetch_remoteok() -> list[dict]:
    """
    Получает вакансии с RemoteOK API.
    RemoteOK — одна из крупнейших платформ для 100% remote и async работы.
    """
    logger.info("Запрос к RemoteOK API…")
    url = "https://remoteok.com/api"

    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("RemoteOK: ошибка запроса — %s. Возвращаем пустой список.", exc)
        return []

    if not isinstance(data, list):
        logger.error("RemoteOK: неожиданный формат ответа (%s)", type(data))
        return []

    results: list[dict] = []
    keywords_set = set(SEARCH_KEYWORDS)

    for item in data:
        if not isinstance(item, dict) or not item.get("id") or not item.get("position"):
            continue

        raw_id = str(item.get("id"))
        title = (item.get("position") or "").strip()
        company = (item.get("company") or "Unknown").strip()
        location = (item.get("location") or "Worldwide Remote").strip()
        description = _clean_text(item.get("description") or "")
        tags = " ".join(item.get("tags") or [])
        url_link = item.get("url") or f"https://remoteok.com/remote-jobs/{raw_id}"

        # Дата публикации
        pub_date = str(item.get("date") or "")[:10]
        if pub_date:
            try:
                dt = datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if dt < datetime.now(tz=timezone.utc) - timedelta(days=FILTER_DAYS):
                    continue
            except Exception:
                pass

        text_to_search = f"{title} {tags} {description}"
        if not _contains_keyword(text_to_search, keywords_set):
            continue

        results.append({
            "job_id": f"remoteok_{raw_id}",
            "source": "remoteok",
            "title": title,
            "company": company,
            "location": location,
            "description": description[:2500],
            "url": url_link,
            "date_published": pub_date,
        })

    logger.info("RemoteOK: итого %d вакансий после фильтрации.", len(results))
    return results


# ─────────────────────────────────────────────
# Arbeitnow (EU / Worldwide Remote API)
# ─────────────────────────────────────────────

def fetch_arbeitnow() -> list[dict]:
    """
    Получает вакансии с Arbeitnow API (европейские и глобальные remote роли).
    """
    logger.info("Запрос к Arbeitnow API…")
    url = "https://www.arbeitnow.com/api/job-board-api"

    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("Arbeitnow: ошибка запроса — %s", exc)
        return []

    jobs_raw = data.get("data", []) if isinstance(data, dict) else []
    results: list[dict] = []
    keywords_set = set(SEARCH_KEYWORDS)

    for item in jobs_raw:
        if not isinstance(item, dict):
            continue

        title = (item.get("title") or "").strip()
        company = (item.get("company_name") or "Unknown").strip()
        location = (item.get("location") or ("Remote" if item.get("remote") else "")).strip()
        description = _clean_text(item.get("description") or "")
        tags = " ".join(item.get("tags") or [])
        url_link = item.get("url") or ""
        slug = item.get("slug") or str(hash(url_link))

        # Если не remote — пропускаем
        if not item.get("remote", False) and "remote" not in location.lower():
            continue

        text_to_search = f"{title} {tags} {description}"
        if not _contains_keyword(text_to_search, keywords_set):
            continue

        # Дата публикации (timestamp в created_at)
        created_at = item.get("created_at")
        pub_date = ""
        if created_at:
            try:
                dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
                pub_date = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        results.append({
            "job_id": f"arbeitnow_{slug}",
            "source": "arbeitnow",
            "title": title,
            "company": company,
            "location": location,
            "description": description[:2500],
            "url": url_link,
            "date_published": pub_date,
        })

    logger.info("Arbeitnow: итого %d вакансий после фильтрации.", len(results))
    return results


# ─────────────────────────────────────────────
# Remotive
# ─────────────────────────────────────────────

def fetch_remotive() -> list[dict]:
    """Получает вакансии с Remotive API."""
    logger.info("Запрос к Remotive API…")
    try:
        response = requests.get("https://remotive.com/api/remote-jobs", headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("Remotive: ошибка — %s. Возвращаем пустой список.", exc)
        return []

    jobs_raw = data.get("jobs", []) if isinstance(data, dict) else []
    results: list[dict] = []
    keywords_set = set(SEARCH_KEYWORDS)

    for job in jobs_raw:
        pub_date = str(job.get("publication_date", ""))[:10]
        if pub_date:
            try:
                dt = datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if dt < datetime.now(tz=timezone.utc) - timedelta(days=FILTER_DAYS):
                    continue
            except Exception:
                pass

        title = (job.get("title") or "").strip()
        description = _clean_text(job.get("description") or "")
        tags = " ".join(job.get("tags") or [])
        location = (job.get("candidate_required_location") or "Remote").strip()

        text_to_search = f"{title} {tags} {description}"
        if not _contains_keyword(text_to_search, keywords_set):
            continue

        raw_id = str(job.get("id", ""))

        results.append({
            "job_id": f"remotive_{raw_id}",
            "source": "remotive",
            "title": title,
            "company": str(job.get("company_name", "Unknown")).strip(),
            "location": location,
            "description": description[:2500],
            "url": job.get("url") or "",
            "date_published": pub_date,
        })

    logger.info("Remotive: итого %d вакансий после фильтрации.", len(results))
    return results


# ─────────────────────────────────────────────
# Jobicy (EMEA & Worldwide)
# ─────────────────────────────────────────────

def fetch_jobicy() -> list[dict]:
    """Получает вакансии с Jobicy API (EMEA и Global)."""
    logger.info("Запрос к Jobicy API…")
    url = "https://jobicy.com/api/v2/remote-jobs"
    params = {"count": 50}

    try:
        response = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("Jobicy: ошибка — %s. Возвращаем пустой список.", exc)
        return []

    jobs_raw = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    results: list[dict] = []
    keywords_set = set(SEARCH_KEYWORDS)

    for job in jobs_raw:
        title = (job.get("jobTitle") or job.get("title") or "").strip()
        description = _clean_text(job.get("jobDescription") or job.get("description") or "")
        company = (job.get("companyName") or job.get("company") or "Unknown").strip()
        location = (job.get("jobGeo") or "Remote").strip()

        pub_date = str(job.get("pubDate") or job.get("date", ""))[:10]
        raw_id = str(job.get("id") or job.get("jobId") or "")

        text_to_search = f"{title} {description}"
        if not _contains_keyword(text_to_search, keywords_set):
            continue

        results.append({
            "job_id": f"jobicy_{raw_id}",
            "source": "jobicy",
            "title": title,
            "company": company,
            "location": location,
            "description": description[:2500],
            "url": job.get("url") or job.get("jobUrl") or "",
            "date_published": pub_date,
        })

    logger.info("Jobicy: итого %d вакансий после фильтрации.", len(results))
    return results


# ─────────────────────────────────────────────
# Himalayas
# ─────────────────────────────────────────────

def fetch_himalayas() -> list[dict]:
    """
    Получает вакансии с Himalayas API.
    """
    logger.info("Запрос к Himalayas API…")
    url = "https://himalayas.app/jobs/api"
    seen_ids = set()
    results: list[dict] = []
    keywords_set = set(SEARCH_KEYWORDS)

    # Запрашиваем свежие вакансии
    for offset in [0, 50]:
        try:
            params = {"limit": 50, "offset": offset}
            response = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            jobs_raw = data.get("jobs", []) if isinstance(data, dict) else data

            for job in jobs_raw:
                if not isinstance(job, dict):
                    continue

                title = (job.get("title") or "").strip()
                company_name = (job.get("companyName") or "Unknown").strip()
                app_link = job.get("applicationLink") or ""
                guid = job.get("guid") or app_link or str(hash(title + company_name))
                raw_id = str(guid).split("/")[-1].split("?")[0]

                if not raw_id or raw_id in seen_ids:
                    continue
                seen_ids.add(raw_id)

                description = _clean_text(job.get("description") or job.get("excerpt") or "")
                location = "Worldwide Remote"
                pub_date = str(job.get("pubDate") or "")[:10]

                text_to_search = f"{title} {description}"
                if not _contains_keyword(text_to_search, keywords_set):
                    continue

                results.append({
                    "job_id": f"himalayas_{raw_id}",
                    "source": "himalayas",
                    "title": title,
                    "company": company_name,
                    "location": location,
                    "description": description[:2500],
                    "url": app_link,
                    "date_published": pub_date,
                })
            time.sleep(0.5)
        except Exception as exc:
            logger.warning("Himalayas (offset=%d): ошибка — %s", offset, exc)

    logger.info("Himalayas: итого %d вакансий после фильтрации.", len(results))
    return results


# ─────────────────────────────────────────────
# Adzuna
# ─────────────────────────────────────────────

ADZUNA_COUNTRIES = ["gb", "de", "nl", "pl", "ua"]

def fetch_adzuna() -> list[dict]:
    """Получает вакансии с Adzuna API по нескольким странам."""
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")

    if not app_id or not app_key:
        return []

    logger.info("Запрос к Adzuna API (%d стран)…", len(ADZUNA_COUNTRIES))
    search_queries = [
        "AI automation remote",
        "prompt engineer remote",
        "LLM solutions remote",
        "AI customer success remote",
    ]

    seen_ids: set[str] = set()
    results: list[dict] = []
    keywords_set = set(SEARCH_KEYWORDS)

    for country in ADZUNA_COUNTRIES:
        for query in search_queries:
            try:
                url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
                params = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": query,
                    "where": "remote",
                    "results_per_page": 15,
                    "sort_by": "date",
                    "max_days_old": 14,
                    "content-type": "application/json",
                }
                resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()

                for job in data.get("results", []):
                    raw_id = str(job.get("id", ""))
                    if raw_id in seen_ids:
                        continue
                    seen_ids.add(raw_id)

                    title = (job.get("title") or "").strip()
                    description = _clean_text(job.get("description") or "")
                    company = job.get("company", {})
                    company_name = company.get("display_name", "Unknown") if isinstance(company, dict) else str(company)
                    created = job.get("created", "")
                    pub_date = created[:10] if created else ""

                    text_to_search = f"{title} {description}"
                    if not _contains_keyword(text_to_search, keywords_set):
                        continue

                    results.append({
                        "job_id": f"adzuna_{raw_id}",
                        "source": "adzuna",
                        "title": title,
                        "company": str(company_name).strip(),
                        "location": "Remote",
                        "description": description[:2500],
                        "url": job.get("redirect_url") or "",
                        "date_published": pub_date,
                    })
            except Exception as exc:
                logger.debug("Adzuna [%s] '%s': %s", country, query, exc)
                continue

    logger.info("Adzuna: итого %d вакансий.", len(results))
    return results


# ─────────────────────────────────────────────
# Working Nomads (100% Remote & Async API)
# ─────────────────────────────────────────────

def fetch_workingnomads() -> list[dict]:
    """
    Получает вакансии с открытого API Working Nomads (чистый Remote/Async).
    URL: https://www.workingnomads.com/api/exposed_jobs/
    """
    logger.info("Запрос к Working Nomads API…")
    url = "https://www.workingnomads.com/api/exposed_jobs/"
    results: list[dict] = []

    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=7)
        resp.raise_for_status()
        jobs_raw = resp.json()
    except Exception as exc:
        logger.warning("Working Nomads: сбой запроса (%s). Пропускаем.", exc)
        return []

    if not isinstance(jobs_raw, list):
        return []

    keywords_set = set(SEARCH_KEYWORDS)
    seen_ids = set()

    for item in jobs_raw:
        if not isinstance(item, dict):
            continue

        title = (item.get("title") or "").strip()
        url_link = item.get("url") or ""
        raw_id = url_link.rstrip("/").split("/")[-1] if url_link else str(hash(title))
        job_id = f"workingnomads_{raw_id}"

        if job_id in seen_ids or not title:
            continue
        seen_ids.add(job_id)

        company = (item.get("company_name") or "Unknown").strip()
        location = (item.get("location") or "Worldwide Remote").strip()
        description = _clean_text(item.get("description") or "")
        tags = str(item.get("tags") or "")
        pub_date = str(item.get("pub_date") or "")[:10]

        # Фильтрация по ключевым словам
        text_to_search = f"{title} {tags} {description}"
        if not _contains_keyword(text_to_search, keywords_set):
            continue

        results.append({
            "job_id": job_id,
            "source": "workingnomads",
            "title": title,
            "company": company,
            "location": location,
            "description": description[:2500],
            "url": url_link,
            "date_published": pub_date,
        })

    logger.info("Working Nomads: итого %d вакансий после фильтрации.", len(results))
    return results
