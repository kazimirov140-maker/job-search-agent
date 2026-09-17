import logging
from config import SEARCH_KEYWORDS
import re
import requests
import time
import html
from datetime import datetime

from config import GREENHOUSE_COMPANIES, LEVER_COMPANIES, ASHBY_COMPANIES, has_topical_core

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

def fetch_greenhouse() -> list[dict]:
    """Скачивает вакансии напрямую из Greenhouse Boards API."""
    logger.info("Запрос к Greenhouse API для %d компаний...", len(GREENHOUSE_COMPANIES))
    results: list[dict] = []
    
    for company in GREENHOUSE_COMPANIES:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
            response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "AIJobSearchAgent/1.0"})
            if response.status_code == 404:
                logger.warning("Greenhouse: Компания %s не найдена.", company)
                continue
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.error("Greenhouse [%s]: ошибка — %s", company, exc)
            continue
            
        jobs = data.get("jobs", [])
        for job in jobs:
            title = job.get("title", "").strip()
            # Clean HTML from description
            description_html = job.get("content", "")
            description = re.sub(r'<[^>]+>', ' ', description_html)
            description = html.unescape(description).strip()
            
            job_obj = {"title": title, "description": description}
            if not has_topical_core(job_obj):
                continue
                
            raw_id = str(job.get("id", ""))
            job_id = f"greenhouse_{company}_{raw_id}"
            loc = (job.get("location") or {}).get("name", "Remote")

            results.append({
                "job_id": job_id,
                "source": f"greenhouse_{company}",
                "title": title,
                "company": company.capitalize(),
                "location": loc,
                "description": description[:2500],
                "url": job.get("absolute_url", ""),
                "date_published": datetime.now().strftime('%Y-%m-%d'),
            })
        time.sleep(0.5)

    logger.info("Greenhouse: собрано %d целевых вакансий.", len(results))
    return results

def fetch_lever() -> list[dict]:
    """Скачивает вакансии напрямую из Lever API."""
    logger.info("Запрос к Lever API для %d компаний...", len(LEVER_COMPANIES))
    results: list[dict] = []
    
    for company in LEVER_COMPANIES:
        try:
            url = f"https://api.lever.co/v0/postings/{company}?mode=json"
            response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "AIJobSearchAgent/1.0"})
            if response.status_code == 404:
                logger.warning("Lever: Компания %s не найдена.", company)
                continue
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.error("Lever [%s]: ошибка — %s", company, exc)
            continue
            
        for job in data:
            title = job.get("text", "").strip()
            description = job.get("descriptionPlain", "")
            if not description:
                description = job.get("description", "")
                description = re.sub(r'<[^>]+>', ' ', description)
                description = html.unescape(description).strip()
            
            job_obj = {"title": title, "description": description}
            if not has_topical_core(job_obj):
                continue
                
            raw_id = str(job.get("id", ""))
            job_id = f"lever_{company}_{raw_id}"
            
            pub_date = str(job.get("createdAt", ""))[:10] if job.get("createdAt") else datetime.now().strftime('%Y-%m-%d')
            loc = (job.get("categories") or {}).get("location", "Remote")
            
            results.append({
                "job_id": job_id,
                "source": f"lever_{company}",
                "title": title,
                "company": company.capitalize(),
                "location": loc,
                "description": description[:2500],
                "url": job.get("hostedUrl", ""),
                "date_published": pub_date,
            })
        time.sleep(0.5)

    logger.info("Lever: собрано %d целевых вакансий.", len(results))
    return results


def fetch_ashby() -> list[dict]:
    """Скачивает вакансии напрямую из Ashby API (Zapier, Linear, PostHog, Perplexity, Synthesia, Cursor, Resend)."""
    logger.info("Запрос к Ashby API для %d компаний...", len(ASHBY_COMPANIES))
    results: list[dict] = []

    for company in ASHBY_COMPANIES:
        try:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
            response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "AIJobSearchAgent/1.0"})
            if response.status_code == 404:
                logger.warning("Ashby: Компания %s не найдена.", company)
                continue
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.error("Ashby [%s]: ошибка — %s", company, exc)
            continue

        jobs = data.get("jobs", [])
        for job in jobs:
            title = (job.get("title") or "").strip()
            description = (job.get("descriptionPlain") or "").strip()
            if not description:
                description_html = job.get("descriptionHtml", "")
                description = re.sub(r'<[^>]+>', ' ', description_html)
                description = html.unescape(description).strip()

            job_obj = {"title": title, "description": description}
            if not has_topical_core(job_obj):
                continue

            raw_id = str(job.get("id", ""))
            job_id = f"ashby_{company}_{raw_id}"

            loc = job.get("location") or "Remote"
            is_remote = job.get("isRemote", False)
            workplace_type = job.get("workplaceType") or ""
            if is_remote or "remote" in workplace_type.lower():
                if "remote" not in loc.lower():
                    loc = f"{loc} (Remote)" if loc else "Remote"

            pub_date = str(job.get("publishedAt", ""))[:10] if job.get("publishedAt") else datetime.now().strftime('%Y-%m-%d')

            results.append({
                "job_id": job_id,
                "source": f"ashby_{company}",
                "title": title,
                "company": company.capitalize(),
                "location": loc,
                "description": description[:2500],
                "url": job.get("jobUrl") or job.get("applyUrl", ""),
                "date_published": pub_date,
            })
        time.sleep(0.3)

    logger.info("Ashby: собрано %d целевых вакансий.", len(results))
    return results
