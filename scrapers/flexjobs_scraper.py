"""
scrapers/flexjobs_scraper.py — Сборщик вакансий с FlexJobs.
"""
import logging
from config import SEARCH_KEYWORDS
import time
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def clean_html(raw_html: str) -> str:
    if not raw_html: return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator="\n", strip=True)

def fetch_flexjobs() -> list[dict]:
    logger.info("Сбор вакансий с FlexJobs по %d ключевым словам…", len(SEARCH_KEYWORDS))
    results = []
    seen_ids = set()
    
    for kw in SEARCH_KEYWORDS:
        logger.info("FlexJobs: поиск по запросу «%s»…", kw)
        url = f"https://www.flexjobs.com/search?search={kw}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 403:
                logger.warning("FlexJobs вернул 403 (скорее всего защита от ботов). Пропускаем.")
                continue
            elif resp.status_code != 200:
                logger.error("FlexJobs вернул код %d", resp.status_code)
                continue
                
            soup = BeautifulSoup(resp.text, "html.parser")
            job_list = soup.find("ul", id="job-list")
            if not job_list:
                logger.debug("FlexJobs: список вакансий не найден для '%s'", kw)
                continue
                
            items = job_list.find_all("li", class_="m-0")
            for item in items:
                title_elem = item.find("a", class_="job-title")
                if not title_elem:
                    continue
                    
                title = title_elem.text.strip()
                link = title_elem.get("href", "")
                if link.startswith("/"):
                    link = "https://www.flexjobs.com" + link
                    
                # Идентификатор
                raw_id = link.split('/')[-1].split('?')[0]
                job_id = f"flexjobs_{raw_id}"
                
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                
                # На FlexJobs компания часто скрыта для бесплатных пользователей
                company = "Confidential (FlexJobs)"
                
                desc_elem = item.find("div", class_="job-description")
                description = clean_html(str(desc_elem)) if desc_elem else ""
                
                results.append({
                    "job_id": job_id,
                    "source": "flexjobs",
                    "title": title,
                    "company": company,
                    "description": description[:1000],
                    "url": link,
                    "date_published": "", # Flexjobs hides exact dates for public view
                })
        except Exception as e:
            logger.error("Ошибка при парсинге FlexJobs: %s", e)
            
        time.sleep(2)
        
    logger.info("FlexJobs: собрано %d уникальных вакансий.", len(results))
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    jobs = fetch_flexjobs()
    for j in jobs[:3]:
        print(j)
