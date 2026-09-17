import logging
import sys
import time
from dotenv import load_dotenv

load_dotenv()

from storage import JobStorage
from config import SOURCE_WEIGHTS, is_blocked, SCORE_THRESHOLD_SILENT, SCORE_THRESHOLD_NOTIFY, SCORE_THRESHOLD_PRIORITY
from scrapers.email_scrapers import fetch_from_emails
from llm_engine import evaluate_job_with_llm
from telegram_notifier import send_job_to_telegram

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("email_only_scan")

def run_email_scan(days_back=11, max_emails=50):
    logger.info("🚀 Запуск сканирования только почты за последние %d дней...", days_back)
    
    db = JobStorage()
    
    logger.info("── Сбор вакансий: Email (Gmail) ───────────────")
    try:
        all_jobs = fetch_from_emails(days_back=days_back, max_emails=max_emails)
        logger.info("Email: %d вакансий получено.", len(all_jobs))
    except Exception as exc:
        logger.error("Ошибка email парсера: %s", exc)
        return
        
    if not all_jobs:
        logger.warning("Не удалось собрать вакансии с почты.")
        db.close()
        return

    unique_jobs = []
    for job in all_jobs:
        title = job.get("title", "")
        company = job.get("company", "")
        
        if is_blocked(job, job.get("url", "")):
            continue
            
        if db.is_duplicate(title, company):
            continue
            
        job_id = job.get("job_id", "")
        if job_id and db.is_job_seen(job_id):
            continue
            
        unique_jobs.append(job)

    logger.info("К оценке после дедупликации: %d", len(unique_jobs))
    
    for i, job in enumerate(unique_jobs, 1):
        title = job.get("title", "?")
        logger.info("[%d/%d] Оценка: %s", i, len(unique_jobs), title)
        
        evaluation = evaluate_job_with_llm(job)
        
        raw_score = evaluation.get("match_score", 0)
        passed = evaluation.get("passed_filter", False)
        
        weight = SOURCE_WEIGHTS.get("linkedin_email", 1.2)
        score = min(100, int(raw_score * weight))
        
        job["match_score"] = score
        job["raw_score"] = raw_score
        job["weight"] = weight
        job["passed_filter"] = passed
        job["priority_category"] = evaluation.get("priority_category", "")
        job["language_condition"] = evaluation.get("language_condition", "")
        job["why_match"] = evaluation.get("why_match", "")
        job["red_flags"] = evaluation.get("red_flags", "")
        job["location"] = evaluation.get("location", "")
        
        job_id = job.get("job_id", f"unknown_email_{i}")
        
        db.save_seen(job_id, title, job.get("company", ""), "email_digest", score)
        
        if score < SCORE_THRESHOLD_SILENT:
            continue
            
        if score < SCORE_THRESHOLD_NOTIFY:
            db.save_silent(job)
            continue
            
        priority = score >= SCORE_THRESHOLD_PRIORITY
        send_job_to_telegram(job, priority=priority)
        
        time.sleep(3.0)

    db.close()
    logger.info("Сканирование завершено.")

if __name__ == "__main__":
    run_email_scan(days_back=11, max_emails=100)
