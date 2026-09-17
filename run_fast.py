"""
run_fast.py — Быстрый запуск агента с пропуском зависающих скраперов (LinkedIn, FlexJobs).
Остальные источники + email + LLM работают как обычно.
"""
import sys
import logging

# Monkey-patch: пропускаем LinkedIn и FlexJobs
import scrapers.linkedin_scraper as li
import scrapers.flexjobs_scraper as fj

_original_linkedin = li.fetch_linkedin
_original_flexjobs = fj.fetch_flexjobs

def _skip_linkedin():
    logging.getLogger("run_fast").info("⏭ LinkedIn пропущен (быстрый режим)")
    return []

def _skip_flexjobs():
    logging.getLogger("run_fast").info("⏭ FlexJobs пропущен (быстрый режим)")
    return []

li.fetch_linkedin = _skip_linkedin
fj.fetch_flexjobs = _skip_flexjobs

# Запускаем main
sys.argv = ['main.py']
from main import run_search
run_search()
