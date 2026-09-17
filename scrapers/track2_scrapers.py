"""
track2_scrapers.py — Скрейперы для Трека 2: AI Evaluation платформы (v2.2).

Стратегия: используем Adzuna API для поиска вакансий по названиям компаний.
Платформы без публичных API (SPA-сайты) ищутся через Adzuna агрегатор.

Платформы:
  - Outlier AI       → Adzuna поиск
  - Alignerr         → Adzuna поиск
  - Mindrift         → Adzuna поиск
  - TELUS International AI → Adzuna поиск
  - Appen            → Adzuna поиск
  - OneForma         → Adzuna поиск
  - Prolific         → заглушка (платформа для участников исследований)

Каждый скрейпер возвращает список словарей с полями:
  - platform: Название платформы
  - title: Название проекта/задачи
  - url: Ссылка
  - rate: Ставка
  - task_type: Тип задачи
  - status: Статус
"""

import logging
import os
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

# ─────────────────────────────────────────────
# Adzuna-based поиск по компаниям
# ─────────────────────────────────────────────

# Компании для поиска через Adzuna
TRACK2_COMPANIES = {
    "outlier": {
        "platform": "Outlier AI",
        "search_terms": ["Outlier", "Outlier AI", "Scale AI"],
        "rate": "$25-40/hr",
        "task_type": "LLM Evaluation",
        "fallback_url": "https://outlier.ai/careers",
    },
    "alignerr": {
        "platform": "Alignerr",
        "search_terms": ["Alignerr"],
        "rate": "$20-35/hr",
        "task_type": "AI Training",
        "fallback_url": "https://alignerr.com/opportunities",
    },
    "mindrift": {
        "platform": "Mindrift",
        "search_terms": ["Mindrift"],
        "rate": "$20-30/hr",
        "task_type": "AI Content",
        "fallback_url": "https://mindrift.ai/become-a-creator",
    },
    "telus": {
        "platform": "TELUS International AI",
        "search_terms": ["TELUS International", "Telus AI", "Telus Digital"],
        "rate": "$15-25/hr",
        "task_type": "Data Annotation",
        "fallback_url": "https://telusinternational.com/programs",
    },
    "appen": {
        "platform": "Appen",
        "search_terms": ["Appen"],
        "rate": "$15-25/hr",
        "task_type": "Data Labeling",
        "fallback_url": "https://connect.appen.com/qrp/public/jobs",
    },
    "oneforma": {
        "platform": "OneForma",
        "search_terms": ["OneForma", "Pactera EDGE"],
        "rate": "$15-25/hr",
        "task_type": "Data Collection",
        "fallback_url": "https://oneforma.com/project-signup",
    },
}

# Ключевые слова для фильтрации Track 2 вакансий
TRACK2_KEYWORDS = [
    "AI", "ML", "data", "annotation", "evaluation", "evaluator",
    "trainer", "labeling", "label", "RLHF", "NLP", "language model",
    "content", "reviewer", "analyst", "research", "survey",
    "remote", "freelance", "contract", "part-time",
]

# Страны для Adzuna поиска
ADZUNA_COUNTRIES = ["gb", "de", "us", "nl", "pl"]


def _matches_track2_keywords(text: str) -> bool:
    """Проверяет, содержит ли текст ключевые слова Track 2."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TRACK2_KEYWORDS)


def _search_adzuna_for_company(company_config: dict) -> list[dict]:
    """
    Ищет вакансии компании через Adzuna API.

    Args:
        company_config: конфигурация компании из TRACK2_COMPANIES

    Returns:
        Список найденных вакансий в формате Track 2
    """
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")

    if not app_id or not app_key:
        return []

    results = []
    seen_titles = set()

    for search_term in company_config["search_terms"]:
        for country in ADZUNA_COUNTRIES:
            try:
                url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
                params = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": search_term,
                    "results_per_page": 10,
                    "sort_by": "date",
                    "max_days_old": 30,
                    "content-type": "application/json",
                }
                headers = {"Accept": "application/json"}

                resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()

                for job in data.get("results", []):
                    title = (job.get("title") or "").strip()
                    description = (job.get("description") or "").strip()
                    company_data = job.get("company", {})
                    company_name = company_data.get("display_name", "") if isinstance(company_data, dict) else str(company_data)

                    # Проверяем что компания совпадает (или ключевые слова совпадают)
                    title_lower = title.lower()
                    company_lower = company_name.lower()
                    search_lower = search_term.lower()

                    is_company_match = (
                        search_lower in company_lower
                        or search_lower in title_lower
                    )

                    if not is_company_match:
                        continue

                    # Дедупликация по названию
                    dedup_key = f"{title_lower}_{company_lower}"
                    if dedup_key in seen_titles:
                        continue
                    seen_titles.add(dedup_key)

                    # Проверяем Track 2 ключевые слова
                    text_to_check = f"{title} {description}"
                    if not _matches_track2_keywords(text_to_check):
                        continue

                    redirect_url = job.get("redirect_url") or company_config["fallback_url"]

                    results.append({
                        "platform": company_config["platform"],
                        "title": title,
                        "url": redirect_url,
                        "rate": company_config["rate"],
                        "task_type": company_config["task_type"],
                        "status": "Найдено на Adzuna",
                    })

            except Exception as exc:
                logger.debug("Adzuna [%s] '%s': %s", country, search_term, exc)
                continue

    return results


# ─────────────────────────────────────────────
# Основные fetch-функции
# ─────────────────────────────────────────────

def _fetch_company_via_adzuna(source_key: str) -> list[dict]:
    """
    Универсальная функция: ищет компанию через Adzuna,
    если не находит — возвращает fallback-заглушку.
    """
    config = TRACK2_COMPANIES.get(source_key)
    if not config:
        return []

    # Пробуем Adzuna
    results = _search_adzuna_for_company(config)

    if results:
        return results

    # Fallback — заглушка с прямой ссылкой
    return [{
        "platform": config["platform"],
        "title": "Проверь доступные проекты",
        "url": config["fallback_url"],
        "rate": config["rate"],
        "task_type": config["task_type"],
        "status": "Проверь вручную",
    }]


def fetch_outlier() -> list[dict]:
    """Ищет вакансии Outlier AI через Adzuna."""
    return _fetch_company_via_adzuna("outlier")


def fetch_alignerr() -> list[dict]:
    """Ищет вакансии Alignerr через Adzuna."""
    return _fetch_company_via_adzuna("alignerr")


def fetch_mindrift() -> list[dict]:
    """Ищет вакансии Mindrift через Adzuna."""
    return _fetch_company_via_adzuna("mindrift")


def fetch_telus() -> list[dict]:
    """Ищет вакансии TELUS International AI через Adzuna."""
    return _fetch_company_via_adzuna("telus")


def fetch_appen() -> list[dict]:
    """Ищет вакансии Appen через Adzuna."""
    return _fetch_company_via_adzuna("appen")


def fetch_oneforma() -> list[dict]:
    """Ищет вакансии OneForma через Adzuna."""
    return _fetch_company_via_adzuna("oneforma")


def fetch_prolific() -> list[dict]:
    """
    Prolific — платформа для участников научных исследований.
    Нет публичного API для поиска вакансий.
    Участники регистрируются и получают приглашения на исследования.
    """
    return [{
        "platform": "Prolific",
        "title": "Зарегистрируйся как участник исследований",
        "url": "https://app.prolific.com/register/participant",
        "rate": "$8-20/hr",
        "task_type": "Research Studies",
        "status": "Регистрация открыта",
    }]


# Маппинг источников на функции
TRACK2_FETCHERS = {
    "outlier": fetch_outlier,
    "alignerr": fetch_alignerr,
    "mindrift": fetch_mindrift,
    "telus": fetch_telus,
    "appen": fetch_appen,
    "oneforma": fetch_oneforma,
    "prolific": fetch_prolific,
}


def fetch_all_track2() -> list[dict]:
    """
    Запускает все скрейперы Трека 2 и возвращает объединённый список.

    Returns:
        Список словарей с данными о проектах.
    """
    all_results = []
    for source_name, fetcher in TRACK2_FETCHERS.items():
        try:
            results = fetcher()
            all_results.extend(results)
            logger.info("Track2 [%s]: %d проектов", source_name, len(results))
        except Exception as e:
            logger.error("Track2 [%s]: критическая ошибка — %s", source_name, e)

    return all_results
