"""
main.py — Главный цикл AI Job Search Agent (v4.1).

Порядок работы:
  1. Сбор вакансий со всех проверенных источников.
  2. Детерминированная фильтрация: юрисдикция РФ/РБ, тематика, формат.
  3. Дедупликация по job_id и по нормализованным title + company.
  4. Сортировка по дешёвому prescreen-баллу.
  5. Оценка LLM: чистое соответствие профилю + структурные признаки.
  6. Пересчёт score в Python: бонусы за язык, страну, async; штрафы.
  7. Пороги: 60-74 тихо, 75-84 Telegram, 85+ Telegram 🔥.

Ключевые отличия от v3:
  - Сбой LLM на одной вакансии больше НЕ прекращает прогон.
  - Веб-скрейпер LinkedIn убран: нарушал ToS, банился по IP CI и съедал
    70% времени прогона ради данных, которые потом выбрасывались.
  - Причины детерминированных отказов пишутся в лог и в сводку, чтобы
    ложные отказы было видно, а не приходилось о них догадываться.
"""

import logging
import sys
import time

from dotenv import load_dotenv

load_dotenv()

import config
from config import (
    SEARCH_KEYWORDS, SOURCE_WEIGHTS, REQUIRED_KEYWORDS,
    SCORE_THRESHOLD_SILENT, SCORE_THRESHOLD_NOTIFY, SCORE_THRESHOLD_PRIORITY,
    EMAIL_DAYS_BACK, MAX_EMAILS, MAX_LLM_JOBS_PER_RUN, LLM_REQUEST_DELAY,
    MAX_CONSECUTIVE_LLM_FAILURES, ALLOW_HYBRID,
    block_reason,
)
from storage import JobStorage, make_fingerprint
from scoring import apply_adjustments, describe_language
from llm_engine import evaluate_job_with_llm
from telegram_notifier import send_job_to_telegram, send_summary_to_telegram, send_text_to_telegram

from scrapers.api_scrapers import (
    fetch_jobicy, fetch_himalayas,
    fetch_arbeitnow, fetch_workingnomads,
)
from scrapers.rss_scrapers import (
    fetch_djinni, fetch_dou, fetch_weworkremotely, fetch_nodesk,
    fetch_habr_career, fetch_upwork,
)
from scrapers.ats_scrapers import fetch_greenhouse, fetch_lever, fetch_ashby
from scrapers.email_scrapers import fetch_from_emails
from scrapers.linkedin_scraper import fetch_linkedin

# ─────────────────────────────────────────────
# Логирование
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


# ─────────────────────────────────────────────
# Сбор
# ─────────────────────────────────────────────

# Международные англоязычные борды (проверенные, быстрые и без блокировок)
INTERNATIONAL_SOURCES = [
    ("WorkingNomads", fetch_workingnomads),
    ("Nodesk", fetch_nodesk),
    ("WeWorkRemotely", fetch_weworkremotely),
    ("Jobicy", fetch_jobicy),
    ("Himalayas", fetch_himalayas),
    ("Arbeitnow", fetch_arbeitnow),
    ("Upwork", fetch_upwork),
]

ATS_SOURCES = [
    ("Greenhouse", fetch_greenhouse),
    ("Lever", fetch_lever),
    ("Ashby", fetch_ashby),
]

RUSSOPHONE_SOURCES = [
    ("Djinni", fetch_djinni),
    ("DOU", fetch_dou),
    ("HabrCareer", fetch_habr_career),
]

# Источники, дающие преимущественно украино/русскоязычные вакансии.
# Нужны для честного деления лимита LLM между сегментами.
RUSSOPHONE_SOURCE_NAMES = {"djinni", "dou", "habrcareer", "telegram", "email", "email_digest"}


def collect_vacancies() -> list[dict]:
    """Собирает вакансии со всех источников; сбой одного не мешает остальным."""
    import os
    mode = os.environ.get("RUN_MODE", "standard")
    is_en_async = mode == "en_async"

    all_jobs: list[dict] = []

    sources = list(INTERNATIONAL_SOURCES) + list(ATS_SOURCES)

    if not is_en_async:
        sources = list(RUSSOPHONE_SOURCES) + sources

    for name, func in sources:
        logger.info("── Сбор: %s ──", name)
        try:
            jobs = func()
            logger.info("%s: получено %d", name, len(jobs))
            all_jobs.extend(jobs)
        except Exception as exc:
            logger.error("Парсер %s упал: %s", name, exc)

    if not is_en_async:
        logger.info("── Сбор: Email (Gmail IMAP) ──")
        try:
            email_jobs = fetch_from_emails(days_back=EMAIL_DAYS_BACK, max_emails=MAX_EMAILS)
            logger.info("Email: получено %d", len(email_jobs))
            all_jobs.extend(email_jobs)
        except Exception as exc:
            logger.error("Email-парсер упал: %s", exc)
    else:
        logger.info("── Сбор: Email пропущен (режим en_async) ──")

    logger.info("═" * 50)
    logger.info("ИТОГО собрано: %d вакансий", len(all_jobs))
    return all_jobs


# ─────────────────────────────────────────────
# Дешёвый предварительный балл
# ─────────────────────────────────────────────

def prescreen_score(job: dict) -> float:
    """
    Определяет ПОРЯДОК оценки, а не пригодность вакансии.

    Совпадения в названии весят втрое больше, чем в описании, а вклад
    описания ограничен пятью попаданиями. Иначе побеждает не самая
    подходящая вакансия, а самая многословная — длинный SEO-постинг
    набирал больше очков, чем короткое точное описание роли.
    """
    title = str(job.get("title") or "").lower()
    body = " ".join(str(job.get(f) or "") for f in ("description", "location")).lower()
    keywords = [kw.lower() for kw in list(SEARCH_KEYWORDS) + list(REQUIRED_KEYWORDS)]

    title_hits = sum(1 for kw in keywords if kw in title)
    body_hits = min(5, sum(1 for kw in keywords if kw in body))

    remote_bonus = 3 if any(t in title or t in body for t in ("remote", "worldwide", "anywhere", "async")) else 0
    source_weight = SOURCE_WEIGHTS.get(job.get("source", ""), 1.0)

    return (title_hits * 30 + body_hits * 5 + remote_bonus) * source_weight


def _is_russophone_source(job: dict) -> bool:
    source = str(job.get("source") or "").split("_")[0].lower()
    return source in RUSSOPHONE_SOURCE_NAMES


def select_fair_share_queue(jobs: list[dict], limit: int, intl_share: float = 0.5) -> list[dict]:
    """
    Делит лимит оценок LLM между международным и русскоязычным сегментами.

    Просто взять топ-N по prescreen_score нельзя: заголовки Djinni и DOU
    состоят почти целиком из целевых ключевых слов («AI Automation
    Specialist»), а англоязычные борды дают более общие названия. При
    лимите в 35 оценок украинские вакансии забирали почти все слоты, и до
    международных дело не доходило вовсе — отсюда выдача «в основном
    украинское».

    Каждый сегмент сохраняет свой порядок по prescreen_score; недобранная
    доля одного сегмента переходит другому, поэтому лимит не простаивает.
    """
    if limit <= 0 or not jobs:
        return []

    international = [j for j in jobs if not _is_russophone_source(j)]
    russophone = [j for j in jobs if _is_russophone_source(j)]

    if not international or not russophone:
        return jobs[:limit]

    intl_quota = min(len(international), max(1, round(limit * intl_share)))
    ru_quota = min(len(russophone), limit - intl_quota)
    # Незанятые слоты возвращаются международному сегменту.
    intl_quota = min(len(international), limit - ru_quota)

    selected = international[:intl_quota] + russophone[:ru_quota]
    selected.sort(key=lambda item: item.get("prescreen_score", 0), reverse=True)
    logger.info(
        "Лимит LLM поделён: %d международных + %d русскоязычных",
        intl_quota, ru_quota,
    )
    return selected


# ─────────────────────────────────────────────
# Основной цикл
# ─────────────────────────────────────────────

def run_search():
    start_time = time.time()
    logger.info("🚀 Запуск поиска вакансий (v4.1)")
    
    import os
    mode = os.environ.get("RUN_MODE", "standard")
    if mode == "en_async":
        logger.info("🔧 Режим «Английские + Async»: русскоязычные источники и диаспорные бонусы отключены.")
        # Список намеренно шире прежнего. Раньше он требовал буквального
        # "async" / "remote-first" / "timezones", и почти вся англоязычная
        # выдача отсекалась ещё до LLM: реальные объявления описывают
        # асинхронность другими словами. Режим "any" — достаточно одного
        # совпадения, а точность обеспечивают гео- и remote-гейты.
        config.REQUIRED_KEYWORDS = [
            "async", "asynchronous", "asynchronously",
            "remote-first", "remote first", "fully remote", "work from anywhere",
            "distributed team", "globally distributed",
            "written communication", "documentation-first", "no meetings",
            "flexible hours", "flexible schedule", "own hours",
            "timezone", "timezones", "time zone", "time zones",
            "worldwide", "anywhere",
        ]
        config.REQUIRED_KEYWORDS_MODE = "any"

    db = JobStorage()

    all_jobs = collect_vacancies()
    if not all_jobs:
        logger.warning("Не собрано ни одной вакансии.")
        db.close()
        return

    # ── Фильтрация и дедупликация ────────────────────────────────
    unique_jobs: list[dict] = []
    duplicates = 0
    rejected = 0
    reject_reasons: dict[str, int] = {}

    # Дедупликация ВНУТРИ прогона.
    # Проверки по базе ловят только повторы между запусками: вакансия
    # попадает в seen_jobs лишь после оценки LLM. Без этих двух множеств
    # одна и та же вакансия, пришедшая в одном прогоне из двух источников,
    # проходила обе проверки и уходила в Telegram дважды.
    seen_ids_in_run: set[str] = set()
    seen_fingerprints_in_run: set[str] = set()

    for job in all_jobs:
        title = job.get("title", "")
        company = job.get("company", "")
        job_id = job.get("job_id", "")
        source = job.get("source", "")

        reason = block_reason(job, job.get("url", ""))
        if reason:
            rejected += 1
            bucket = reason.split(":")[0]
            reject_reasons[bucket] = reject_reasons.get(bucket, 0) + 1
            logger.debug("Отклонено «%s»: %s", title[:60], reason)
            db.record_state(job_id, title, company, source, "rejected_deterministic", reason, commit=False)
            continue

        # Дедупликация: комбинируем нормализованную компанию и первые 200 букв описания.
        # Это решает проблему репостов на Djinni и DOU, когда меняется ID или слегка
        # меняется заголовок, но основной текст остается тем же.
        import hashlib
        import re
        company_known = bool(company) and company.strip().lower() not in {"unknown", "n/a", "-"}
        comp_str = company.lower() if company_known else "unknown"
        desc_clean = re.sub(r'[^a-z0-9а-яё]', '', job.get("description", "").lower())
        
        fingerprint_key = f"{comp_str}|{desc_clean[:200]}"
        fingerprint = hashlib.sha256(fingerprint_key.encode()).hexdigest()[:16]

        # 1. Повтор внутри текущего прогона (между источниками)
        if job_id and job_id in seen_ids_in_run:
            duplicates += 1
            continue
        if fingerprint and fingerprint in seen_fingerprints_in_run:
            duplicates += 1
            logger.debug("Повтор внутри прогона: «%s» (%s)", title[:60], source)
            continue

        # 2. Повтор относительно предыдущих запусков
        if (company_known and db.is_duplicate(title, company)) or \
           (job_id and db.is_job_seen(job_id)) or \
           (fingerprint and db.is_fingerprint_seen(fingerprint)):
            duplicates += 1
            continue

        if job_id:
            seen_ids_in_run.add(job_id)
        if fingerprint:
            seen_fingerprints_in_run.add(fingerprint)

        job["prescreen_score"] = prescreen_score(job)
        db.record_state(job_id, title, company, source, "discovered",
                        commit=False, url=job.get("url", ""))
        unique_jobs.append(job)

    db.commit()
    unique_jobs.sort(key=lambda item: item.get("prescreen_score", 0), reverse=True)

    logger.info(
        "Фильтрация: %d → %d (дубли: %d, отклонено: %d)",
        len(all_jobs), len(unique_jobs), duplicates, rejected,
    )
    for bucket, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
        logger.info("  причина отказа «%s»: %d", bucket, count)

    # ── Оценка LLM ───────────────────────────────────────────────
    jobs_to_evaluate = select_fair_share_queue(unique_jobs, MAX_LLM_JOBS_PER_RUN)
    deferred_jobs = len(unique_jobs) - len(jobs_to_evaluate)
    if deferred_jobs:
        logger.info("Лимит LLM: оцениваем %d, откладываем %d", len(jobs_to_evaluate), deferred_jobs)

    rejections_context = db.get_rejection_prompt_context()

    total_sent = 0
    total_priority = 0
    total_silent = 0
    llm_failures = 0
    consecutive_failures = 0
    evaluated_ok = 0
    sources_stats: dict[str, int] = {}

    for i, job in enumerate(jobs_to_evaluate, 1):
        title = job.get("title", "?")
        source = job.get("source", "unknown")
        job_id = job.get("job_id") or ("unknown_%d" % i)
        company = job.get("company", "")

        logger.info("[%d/%d] Оценка: %s (%s)", i, len(jobs_to_evaluate), title[:70], source)
        db.record_state(job_id, title, company, source, "pending_llm")

        evaluation = evaluate_job_with_llm(job, rejections_context)
        status = evaluation.get("evaluation_status", "ok")

        if status != "ok":
            # Сбой одной вакансии больше не убивает прогон: вакансия НЕ
            # помечается просмотренной и вернётся в следующем запуске.
            # Останавливаемся только когда сбои идут подряд — это признак,
            # что упала сама LLM, а не конкретный ответ.
            llm_failures += 1
            consecutive_failures += 1
            db.record_state(job_id, title, company, source, "deferred", status)
            logger.warning("LLM не оценила «%s» (%s); вакансия отложена", title[:60], status)

            if consecutive_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                logger.error(
                    "%d сбоев LLM подряд — прекращаем пакет, остальное перенесено на следующий запуск",
                    consecutive_failures,
                )
                break
            continue

        consecutive_failures = 0
        evaluated_ok += 1

        base_score = evaluation.get("match_score", 0)
        score, notes = apply_adjustments(base_score, evaluation, job)

        job.update({
            "match_score": score,
            "base_score": base_score,
            "score_notes": notes,
            "passed_filter": evaluation.get("passed_filter", False),
            "priority_category": evaluation.get("priority_category", ""),
            "role_family": evaluation.get("role_family", ""),
            "language_label": describe_language(evaluation),
            "why_match": evaluation.get("why_match", ""),
            "red_flags": evaluation.get("red_flags", ""),
            "location": evaluation.get("location", job.get("location", "")),
        })

        if notes:
            logger.info("  score %d = %d %s", score, base_score, " ".join(notes))

        db.save_seen(job_id, title, company, source, score)
        db.record_state(job_id, title, company, source, "evaluated")

        # Жёсткие стоп-факторы, которые LLM видит по смыслу текста, а
        # детерминированный фильтр — нет. Балл здесь не помогает: даже
        # идеальная по содержанию вакансия бесполезна, если из ЕС на неё
        # не берут или это офис.
        llm_geo = str(evaluation.get("geo_restriction") or "").strip().lower()
        llm_format = str(evaluation.get("work_format") or "").strip().lower()

        if llm_geo == "region_locked":
            logger.info("  ❌ Гео: вакансия закрыта для кандидата из ЕС")
            db.record_state(job_id, title, company, source, "rejected_geo", "region_locked")
            continue

        rejected_formats = {"onsite"} if ALLOW_HYBRID else {"onsite", "hybrid"}
        if llm_format in rejected_formats:
            logger.info("  ❌ Формат: %s, а не удалёнка", llm_format)
            db.record_state(job_id, title, company, source, "rejected_format", llm_format)
            continue

        if not job["passed_filter"]:
            logger.info("  ❌ LLM-фильтр отклонил при score %d", score)
            continue

        if score < SCORE_THRESHOLD_SILENT:
            logger.info("  ❌ Score %d ниже %d — игнорируем", score, SCORE_THRESHOLD_SILENT)
            continue

        if score < SCORE_THRESHOLD_NOTIFY:
            db.save_silent(job)
            total_silent += 1
            logger.info("  🔇 Score %d — тихое сохранение", score)
            continue

        priority = score >= SCORE_THRESHOLD_PRIORITY
        if send_job_to_telegram(job, priority=priority):
            db.record_state(job_id, title, company, source, "notified")
            total_sent += 1
            total_priority += 1 if priority else 0
            sources_stats[source] = sources_stats.get(source, 0) + 1
            logger.info("  ✅ Score %d%s — отправлено", score, " 🔥" if priority else "")
        else:
            logger.error("  ❌ Ошибка отправки: %s", title[:60])

        if i < len(jobs_to_evaluate):
            time.sleep(LLM_REQUEST_DELAY)

    # ── Сводка ───────────────────────────────────────────────────
    elapsed = time.time() - start_time
    logger.info("═" * 50)
    logger.info("ЗАВЕРШЕНО за %.1f сек", elapsed)
    logger.info("  Собрано: %d", len(all_jobs))
    logger.info("  Уникальных: %d", len(unique_jobs))
    logger.info("  Отклонено детерминированно: %d", rejected)
    logger.info("  Оценено LLM: %d", evaluated_ok)
    logger.info("  Сбоев LLM: %d", llm_failures)
    logger.info("  Отложено: %d", deferred_jobs)
    logger.info("  Тихо сохранено: %d", total_silent)
    logger.info("  Отправлено: %d (приоритетных: %d)", total_sent, total_priority)

    try:
        send_summary_to_telegram(
            total_found=len(all_jobs),
            total_sent=total_sent,
            total_priority=total_priority,
            sources_stats=sources_stats,
        )
    except Exception as exc:
        logger.error("Ошибка отправки сводки: %s", exc)

    # Явный сигнал о поломке. Раньше прогон мог отработать 25 минут, выдать
    # ноль и сообщить об этом как о штатном результате — из-за этого поломка
    # LLM оставалась незамеченной неделями.
    if evaluated_ok == 0 and jobs_to_evaluate:
        try:
            send_text_to_telegram(
                "⚠️ <b>Агент не смог оценить ни одной вакансии</b>\n"
                "Собрано: %d, в очереди: %d, сбоев LLM: %d.\n"
                "Проверьте квоту OpenRouter и доступность моделей."
                % (len(all_jobs), len(unique_jobs), llm_failures)
            )
        except Exception as exc:
            logger.error("Не удалось отправить предупреждение: %s", exc)

    db.close()

    # Cloud Storage backup
    import cloud_storage
    cloud_storage.upload_db()

if __name__ == "__main__":
    run_search()
