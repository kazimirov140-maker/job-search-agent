"""
scrapers/email_scrapers.py — Чтение писем (Otta, Wellfound) через IMAP и извлечение вакансий.
"""

import imaplib
import email
from email.header import decode_header
import logging
from datetime import datetime, timedelta
import hashlib
import config
from llm_engine import extract_jobs_from_email
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def get_text_from_email(msg) -> str:
    """Извлекает текст из объекта email."""
    text_content = ""
    
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            if "attachment" not in content_disposition:
                if content_type == "text/plain":
                    try:
                        text_content += part.get_payload(decode=True).decode()
                    except:
                        pass
                elif content_type == "text/html":
                    try:
                        html = part.get_payload(decode=True).decode()
                        soup = BeautifulSoup(html, "html.parser")
                        text_content += soup.get_text(separator="\n", strip=True)
                    except:
                        pass
    else:
        content_type = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True).decode()
            if content_type == "text/html":
                soup = BeautifulSoup(payload, "html.parser")
                text_content = soup.get_text(separator="\n", strip=True)
            else:
                text_content = payload
        except:
            pass
            
    return text_content

def fetch_from_emails(days_back: int = 7, max_emails: int = 20) -> list[dict]:
    """
    Подключается к Gmail, ищет рассылки за последние days_back дней 
    и вытаскивает из них вакансии через LLM.
    """
    if not config.GMAIL_EMAIL or not config.GMAIL_APP_PASSWORD:
        logger.info("Учетные данные GMAIL не заданы, пропуск email парсера.")
        return []
        
    logger.info("Подключение к Gmail (%s)...", config.GMAIL_EMAIL)
    
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(config.GMAIL_EMAIL, config.GMAIL_APP_PASSWORD)
        mail.select("inbox")
        
        # Ищем письма за последние дни
        date_since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
        
        # Поиск писем
        search_criteria = f'(SINCE "{date_since}")'
        status, messages = mail.search(None, search_criteria)
        
        if status != "OK":
            logger.error("Ошибка поиска писем.")
            return []
            
        email_ids = messages[0].split()
        
        all_jobs = []
        # Список отправителей вынесен в конфиг: раньше здесь было жёстко
        # прошито только linkedin.com, из-за чего дайджесты Wellfound, Otta
        # и Djinni молча игнорировались.
        valid_senders = [s.lower() for s in config.EMAIL_SENDERS]
        
        processed = 0
        for e_id in reversed(email_ids): # с конца (самые новые)
            if processed >= max_emails: # проверяем максимум N писем
                break
                
            status, msg_data = mail.fetch(e_id, "(RFC822)")
            if status != "OK":
                continue
                
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    sender = str(msg.get("From", "")).lower()
                    if not any(v in sender for v in valid_senders):
                        continue
                        
                    subject_header = decode_header(msg.get("Subject", ""))[0]
                    subject = subject_header[0]
                    encoding = subject_header[1]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                    
                    subject_lower = subject.lower()
                    if any(term.lower() in subject_lower for term in config.EMAIL_IGNORED_SUBJECTS):
                        logger.info("⏭ Пропуск нерелевантного письма: %s", subject)
                        continue
                    if not any(term.lower() in subject_lower for term in config.EMAIL_ALLOWED_SUBJECTS):
                        logger.info("⏭ Пропуск письма без признаков вакансии: %s", subject)
                        continue

                    processed += 1
                    logger.info("📩 Обработка письма: %s от %s", subject, sender)
                    
                    email_text = get_text_from_email(msg)
                    if not email_text:
                        continue
                        
                    jobs = extract_jobs_from_email(email_text)
                    logger.info("   -> Найдено вакансий в письме: %d", len(jobs))
                    
                    for job in jobs:
                        job_url = str(job.get("url", ""))
                        identity = "|".join([
                            job_url,
                            str(job.get("title", "")),
                            str(job.get("company", "")),
                        ])
                        url_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
                        formatted_job = {
                            "job_id": f"email_{url_hash}",
                            "source": "email_digest",
                            "title": job.get("title", "Unknown"),
                            "company": job.get("company", "Unknown"),
                            "url": job_url,
                            "description": job.get("description", ""),
                        }
                        all_jobs.append(formatted_job)
                        
        mail.logout()
        logger.info("Сбор из email завершен. Всего извлечено: %d", len(all_jobs))
        return all_jobs
        
    except Exception as exc:
        logger.error("Ошибка при работе с IMAP: %s", exc)
        return []

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    jobs = fetch_from_emails()
    for j in jobs:
        print(j)
