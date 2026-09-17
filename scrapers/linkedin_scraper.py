"""
scrapers/linkedin_scraper.py — Сборщик вакансий с LinkedIn (через публичный веб).

ВНИМАНИЕ: LinkedIn агрессивно блокирует автоматические скрипты.
Используем публичный эндпоинт jobs-guest, чтобы обходить некоторые блокировки.
"""

import logging
from config import SEARCH_KEYWORDS
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Публичные эндпоинты, которые используются для гостей (без авторизации)
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def clean_html(raw_html: str) -> str:
    """Очищает HTML-теги."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator="\n", strip=True)

def fetch_job_description(job_id: str) -> str:
    """
    Скачивает полное описание вакансии по её ID.
    Если LinkedIn блокирует запрос, возвращаем пустую строку.
    """
    url = JOB_URL.format(job_id)
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Описание обычно лежит в блоке с классом show-more-less-html__markup
            desc_block = soup.find("div", class_="show-more-less-html__markup")
            if desc_block:
                return clean_html(str(desc_block))
            
            # Фолбэк на весь body (может быть мусорно, но лучше чем ничего)
            body = soup.find("body")
            return clean_html(str(body)) if body else ""
        elif response.status_code == 429:
            logger.warning("LinkedIn Rate Limit (429) при загрузке описания %s", job_id)
        else:
            logger.debug("LinkedIn вернул %d для %s", response.status_code, job_id)
    except Exception as exc:
        logger.debug("Ошибка загрузки описания %s: %s", job_id, exc)
    return ""

def fetch_linkedin() -> list[dict]:
    """
    Собирает вакансии с LinkedIn (публичный поиск).
    """
    logger.info("Сбор вакансий с LinkedIn (публичный API) по %d ключевым словам…", len(SEARCH_KEYWORDS))
    results = []
    seen_ids = set()

    for kw in SEARCH_KEYWORDS:
        logger.info("LinkedIn: поиск по запросу «%s»…", kw)
        
        # Берем только первую страницу (start=0) чтобы не дразнить антифрод
        params = {
            "keywords": kw,
            "f_TPR": "r604800",  # За последнюю неделю (604800 секунд)
            "start": 0
        }
        
        try:
            response = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
            
            if response.status_code != 200:
                logger.error("LinkedIn вернул код %d для поиска '%s'", response.status_code, kw)
                continue
                
            soup = BeautifulSoup(response.text, "html.parser")
            # Находим карточки вакансий
            cards = soup.find_all("div", class_="base-search-card")
            
            for card in cards:
                try:
                    # Извлекаем job_id из data-entity-urn: urn:li:jobPosting:3842183204
                    urn = card.get("data-entity-urn", "")
                    if not urn:
                        continue
                    
                    raw_id = urn.split(":")[-1]
                    job_id = f"linkedin_{raw_id}"
                    
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    
                    # Название
                    title_elem = card.find("h3", class_="base-search-card__title")
                    title = title_elem.text.strip() if title_elem else "Unknown Title"
                    
                    # Компания
                    company_elem = card.find("h4", class_="base-search-card__subtitle")
                    company = company_elem.text.strip() if company_elem else "Unknown Company"

                    # Локация (On-site, Hybrid, Remote и город)
                    loc_elem = card.find("span", class_="job-search-card__location")
                    location = loc_elem.text.strip() if loc_elem else ""
                    
                    # Ссылка
                    link_elem = card.find("a", class_="base-card__full-link")
                    url = link_elem.get("href", "") if link_elem else f"https://www.linkedin.com/jobs/view/{raw_id}/"
                    
                    # Убираем параметры из URL
                    if "?" in url:
                        url = url.split("?")[0]
                        
                    # Дата
                    date_elem = card.find("time", class_="job-search-card__listdate")
                    date_published = ""
                    if date_elem and date_elem.get("datetime"):
                        date_published = date_elem.get("datetime")
                        
                    # Внимание: для экономии запросов мы пока оставляем описание пустым или подтягиваем
                    # Если LinkedIn забанит — мы просто получим пустые описания
                    
                    description = fetch_job_description(raw_id)
                    time.sleep(1) # Важно! Пауза между запросами описаний
                    
                    if not description:
                        logger.warning("LinkedIn: пустое описание для %s, пропускаем", job_id)
                        continue
                    
                    results.append({
                        "job_id": job_id,
                        "source": "linkedin",
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": description[:1000],
                        "url": url,
                        "date_published": date_published,
                    })
                except Exception as e:
                    logger.debug("LinkedIn: ошибка парсинга одной карточки: %s", e)
                    
        except requests.exceptions.Timeout:
            logger.error("LinkedIn: таймаут для запроса '%s'", kw)
        except Exception as e:
            logger.error("LinkedIn: ошибка запроса '%s': %s", kw, e)
            
        time.sleep(2) # Пауза между страницами поиска
        
    logger.info("LinkedIn: собрано %d уникальных вакансий.", len(results))
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    jobs = fetch_linkedin()
    for j in jobs[:2]:
        print(j)
