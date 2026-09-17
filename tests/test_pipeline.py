"""
tests/test_pipeline.py — Проверка фильтра, скоринга и порядка оценки
без обращения к сети и к LLM.

Запуск:  python tests/test_pipeline.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")
# Порог зарплаты — тестовая фикстура: в продакшене он задаётся в .env,
# по умолчанию фильтр выключен (MIN_SALARY_USD = 0).
os.environ.setdefault("MIN_SALARY_USD", "2500")

import config
from scoring import apply_adjustments, describe_language

failures = []


def check(condition: bool, label: str):
    print(("  ok    " if condition else "  ПРОВАЛ ") + label)
    if not condition:
        failures.append(label)


print("=== Пустые переменные окружения не роняют импорт ===")
os.environ["EMAIL_DAYS_BACK"] = ""
os.environ["SCORE_THRESHOLD_NOTIFY"] = ""
os.environ["LLM_REQUEST_DELAY"] = ""
check(config._env_int("EMAIL_DAYS_BACK", 7) == 7, "пустая строка -> значение по умолчанию (int)")
check(config._env_float("LLM_REQUEST_DELAY", 2.0) == 2.0, "пустая строка -> значение по умолчанию (float)")
check(config._env_int("НЕТ_ТАКОЙ", 42) == 42, "отсутствующая переменная -> default")
os.environ["EMAIL_DAYS_BACK"] = "3"
check(config._env_int("EMAIL_DAYS_BACK", 7) == 3, "заданное значение читается")
del os.environ["EMAIL_DAYS_BACK"]

print()
print("=== Детерминированный фильтр ===")
cases_pass = [
    ({"title": "AI Implementation Consultant", "company": "Acme", "location": "Remote, EU",
      "description": "Настройка автоматизации у клиентов, n8n, Make"}, "целевая роль проходит"),
    ({"title": "Customer Success Manager (AI)", "company": "Kolesa", "location": "Алматы, удалённо",
      "description": "Работа с клиентами, внедрение"}, "CSM из Казахстана с удалёнкой проходит"),
    ({"title": "Automation Specialist", "company": "X", "location": "Remote",
      "description": "Nice to have: Golang experience"}, "Golang в описании больше не блокирует"),
    ({"title": "AI Solutions Consultant", "company": "Y", "location": "Remote",
      "description": "Occasional on-site meetup once a year"}, "on-site в описании не блокирует remote-роль"),
    ({"title": "Workflow Automation Specialist", "company": "RemoteCo", "location": "Worldwide",
      "description": "Building Zapier and n8n pipelines for clients. Salary: $2000 - $3000/mo"}, "Workflow Automation с хорошей зарплатой проходит"),
    # Борды из REMOTE_ONLY_SOURCES удалённые по построению: отсутствие слова
    # "remote" в тексте для них не повод для отказа.
    ({"title": "AI Agent Builder", "company": "Acme", "location": "", "source": "remoteok",
      "description": "Build LLM agents with LangChain"}, "remote-борд без слова remote проходит"),
    ({"title": "AI Automation Specialist", "company": "Acme", "location": "Remote (EMEA or USA)",
      "description": "n8n automation"}, "локация «EMEA или США» проходит"),
    ({"title": "Prompt Engineer", "company": "Acme", "location": "Anywhere",
      "description": "AI prompt work. Contact us to join us today."},
     "«us» в join us не считается ограничением"),
]
for job, label in cases_pass:
    reason = config.block_reason(job, job.get("url", ""))
    check(not reason, label + (" -> " + reason if reason else ""))

cases_block = [
    ({"title": "Senior C++ Developer", "company": "X", "location": "Remote", "description": ""},
     "C++ в названии"),
    ({"title": "AI Engineer", "company": "X", "location": "Москва", "description": ""},
     "локация Москва"),
    ({"title": "AI Consultant (Hybrid)", "company": "X", "location": "Berlin", "description": ""},
     "hybrid в названии"),
    ({"title": "Principal Engineer", "company": "X", "location": "Remote", "description": ""},
     "Principal Engineer"),
    ({"title": "AI Automation Specialist", "company": "CheapCo", "location": "Remote",
      "salary": "$500 - $800/mo", "description": "AI automation with n8n"},
     "зарплата ниже порога MIN_SALARY_USD"),
    # Гео-ограничение удалёнки: поле location у Remotive и Jobicy содержит
    # требование к локации кандидата, а не адрес компании.
    ({"title": "AI Automation Specialist", "company": "X", "location": "USA Only",
      "description": "Build n8n automations"},
     "удалёнка только для США (поле location)"),
    ({"title": "Automation Consultant", "company": "X", "location": "Remote",
      "description": "AI automation role. Candidates must be located in the United States."},
     "US-ограничение в тексте описания"),
    ({"title": "AI Solutions Specialist", "company": "X", "location": "LATAM",
      "description": "Zapier and Make automation"},
     "удалёнка только для LATAM"),
    ({"title": "AI Automation Specialist", "company": "X", "location": "Remote",
      "description": "AI automation. You must be authorized to work in the US."},
     "требование work authorization в США"),
    # Формат работы: раньше проверялось только отсутствие слова "onsite",
    # поэтому вакансия без единого упоминания формата считалась удалённой.
    ({"title": "AI Implementation Specialist", "company": "BigCorp", "location": "Berlin, Germany",
      "source": "greenhouse_bigcorp", "description": "You will implement AI automation for our clients."},
     "офисная вакансия без слова remote"),
]
for job, label in cases_block:
    reason = config.block_reason(job, job.get("url", ""))
    check(bool(reason), label + (" -> " + reason if reason else " (НЕ ПОЙМАН)"))

print()
print("=== Скоринг ===")
ua_job = {"title": "AI Implementation Consultant", "company": "SoftServe", "location": "Київ, віддалено"}
score, notes = apply_adjustments(70, {
    "job_language": "uk", "role_family": "implementation",
    "async_evidence": "unknown", "seniority": "mid",
}, ua_job)
check(score >= config.SCORE_THRESHOLD_PRIORITY,
      "украинская роль внедрения попадает в приоритет: 70 -> %d" % score)

# Ограничитель: бонусы не вытягивают слабое совпадение в уведомление
weak, weak_notes = apply_adjustments(45, {
    "job_language": "uk", "role_family": "implementation",
    "async_evidence": "explicit_async", "seniority": "mid",
}, ua_job)
check(weak < config.SCORE_THRESHOLD_NOTIFY,
      "слабое совпадение (45) не проходит порог уведомления: -> %d" % weak)

# Бонус за уровень Junior
junior_job = {"title": "Junior AI Automation Specialist", "company": "Acme", "location": "Remote", "source": "remoteok"}
score_jr, notes_jr = apply_adjustments(70, {
    "job_language": "en", "role_family": "automation",
    "async_evidence": "explicit_async", "seniority": "junior",
}, junior_job)
check(score_jr >= 95, "Junior AI Automation получает бонус: 70 -> %d %s" % (score_jr, notes_jr))

# SYNC_FIRST_MARKERS (-30 баллов)
en_calls_block = {
    "title": "AI Consultant", "company": "Global Corp", "location": "Remote",
    "description": "Requires English C1 and conducting weekly stakeholder presentations with US clients."
}
score_block, notes_block = apply_adjustments(70, {
    "job_language": "en", "role_family": "advisory",
    "async_evidence": "calls_required", "seniority": "mid",
}, en_calls_block)
check(score_block == 50, "SYNC_FIRST_MARKERS штрафует на -30: 70 -> %d %s" % (score_block, notes_block))

# Исключение для SYNC_FIRST_MARKERS при явном async
en_async_exempt = {
    "title": "AI Consultant", "company": "Global Corp", "location": "Remote",
    "description": "Native-level English preferred. We are an async-first company with written communication and no meetings."
}
score_exempt, notes_exempt = apply_adjustments(70, {
    "job_language": "en", "role_family": "advisory",
    "async_evidence": "explicit_async", "seniority": "mid",
}, en_async_exempt)
check(score_exempt >= 85, "Исключение async-first освобождает от штрафа: 70 -> %d %s" % (score_exempt, notes_exempt))

# Для русско-/украиноязычных вакансий блок звонков НЕ применяется
ua_calls = {
    "title": "Customer Success Lead", "company": "Київстар", "location": "Київ, віддалено",
    "description": "Проведення customer meetings та client calls українською мовою."
}
score_ua_calls, notes_ua_calls = apply_adjustments(70, {
    "job_language": "uk", "role_family": "customer_success",
    "async_evidence": "unknown", "seniority": "mid",
}, ua_calls)
check(score_ua_calls >= 90, "Для UK/RU звонки не штрафуются: 70 -> %d" % score_ua_calls)

# DATA_ANALYST_BLOCKERS (-35 баллов при data_analyst_heavy=True)
da_heavy = {
    "title": "Data Operations Specialist", "company": "Analytics Corp", "location": "Remote",
    "description": "Required: Power BI, DAX, complex SQL window functions, ClickHouse and Tableau."
}
score_da, notes_da = apply_adjustments(70, {
    "job_language": "en", "role_family": "other",
    "async_evidence": "text_first", "seniority": "mid",
    "data_analyst_heavy": True,
}, da_heavy)
check(score_da == 50, "DATA_ANALYST_BLOCKERS штрафует на -35: 70 -> %d %s" % (score_da, notes_da))

# Исключение: если те же технологии только в nice-to-have (data_analyst_heavy=False) -> не снижать
da_nice = {
    "title": "AI Workflow Specialist", "company": "Automation Inc", "location": "Remote",
    "description": "Required: n8n, Make, Python. Nice to have: Power BI, Pandas."
}
score_nice, notes_nice = apply_adjustments(70, {
    "job_language": "en", "role_family": "automation",
    "async_evidence": "text_first", "seniority": "mid",
    "data_analyst_heavy": False,
}, da_nice)
check(score_nice == 99, "Nice to have для Data Analyst не штрафуется: 70 -> %d %s" % (score_nice, notes_nice))

en_async = {"title": "AI Onboarding Specialist", "company": "Acme", "location": "Remote"}
score2, notes2 = apply_adjustments(70, {
    "job_language": "en", "role_family": "onboarding",
    "async_evidence": "explicit_async", "seniority": "mid",
}, en_async)
check(score2 == 97, "английская async-роль: 70 -> %d %s" % (score2, notes2))

staff = {"title": "Staff Engineer", "company": "Acme", "location": "Remote"}
score4, _ = apply_adjustments(70, {
    "job_language": "en", "role_family": "engineering",
    "async_evidence": "unknown", "seniority": "staff",
}, staff)
check(score4 == 50, "инженерный Staff понижается: 70 -> %d" % score4)

# Remote-борд больше НЕ подделывает признак async. Раньше scoring
# принудительно ставил explicit_async всем вакансиям с remote-платформ, и
# из-за этого штраф за обязательные английские созвоны не применялся к ним
# никогда — роли с ежедневными клиентскими звонками уходили в Telegram с
# пометкой «async подтверждён».
board_calls = {
    "title": "AI Solutions Consultant", "company": "Acme", "location": "Worldwide",
    "source": "remoteok",
    "description": "You will lead customer meetings and client calls every day.",
}
score_board, notes_board = apply_adjustments(70, {
    "job_language": "en", "role_family": "solutions",
    "async_evidence": "calls_required", "seniority": "mid",
}, board_calls)
check(score_board < 75,
      "remote-борд не отменяет штраф за созвоны: 70 -> %d %s" % (score_board, notes_board))

# Привязка к американскому часовому поясу
tz_job = {
    "title": "AI Automation Specialist", "company": "Acme", "location": "Worldwide",
    "description": "Remote role, but you must overlap with EST business hours daily.",
}
score_tz, notes_tz = apply_adjustments(70, {
    "job_language": "en", "role_family": "automation",
    "async_evidence": "text_first", "seniority": "junior",
}, tz_job)
score_no_tz, _ = apply_adjustments(70, {
    "job_language": "en", "role_family": "automation",
    "async_evidence": "text_first", "seniority": "junior",
}, {"title": tz_job["title"], "company": "Acme", "location": "Worldwide",
    "description": "Remote role with flexible hours."})
check(score_tz < score_no_tz,
      "часовой пояс США штрафуется: %d против %d %s" % (score_tz, score_no_tz, notes_tz))

# Гео-ограничение, замеченное только LLM
score_locked, notes_locked = apply_adjustments(80, {
    "job_language": "en", "role_family": "automation",
    "async_evidence": "explicit_async", "seniority": "junior",
    "geo_restriction": "region_locked",
}, {"title": "AI Automation Specialist", "company": "Acme", "location": "Remote"})
check(score_locked < config.SCORE_THRESHOLD_NOTIFY,
      "region_locked опускает ниже порога уведомления: %d %s" % (score_locked, notes_locked))

print()
print("=== Режим en_async: диаспорные бонусы отключены ===")
ua_job_en_mode = {"title": "AI Implementation Consultant", "company": "SoftServe",
                  "location": "Київ, віддалено"}
ua_eval = {"job_language": "uk", "role_family": "implementation",
           "async_evidence": "unknown", "seniority": "mid"}
en_job_en_mode = {"title": "AI Automation Specialist", "company": "Acme",
                  "location": "Worldwide", "source": "remoteok"}
en_eval = {"job_language": "en", "role_family": "automation",
           "async_evidence": "explicit_async", "seniority": "junior"}

os.environ["RUN_MODE"] = "en_async"
ua_en_mode, _ = apply_adjustments(70, dict(ua_eval), ua_job_en_mode)
en_en_mode, _ = apply_adjustments(70, dict(en_eval), en_job_en_mode)
os.environ["RUN_MODE"] = "standard"
ua_std, _ = apply_adjustments(70, dict(ua_eval), ua_job_en_mode)

check(ua_en_mode < ua_std,
      "украинская вакансия в en_async теряет диаспорный бонус: %d против %d" % (ua_en_mode, ua_std))
check(en_en_mode > ua_en_mode,
      "в en_async английская вакансия обгоняет украинскую: %d против %d" % (en_en_mode, ua_en_mode))
del os.environ["RUN_MODE"]

check(0 <= apply_adjustments(95, {"job_language": "uk", "role_family": "implementation",
                                  "async_evidence": "explicit_async", "seniority": "mid"},
                             ua_job)[0] <= 100, "score не выходит за 0-100")

print()
print("=== Метка языка для карточки ===")
check(describe_language({"job_language": "uk"}) == "Українська", "украинская метка")
check("звонки" in describe_language({"job_language": "en", "async_evidence": "calls_required"}),
      "английский со звонками помечается")
check("не подтверждён" in describe_language({"job_language": "en", "async_evidence": "unknown"}),
      "неизвестный формат честно помечается")

print()
print("=== Порядок оценки и Fair-Share баланс (50% международные) ===")
from main import prescreen_score, select_fair_share_queue
precise = {"title": "AI Automation Consultant", "description": "Внедрение автоматизации.",
           "location": "Remote", "source": "djinni"}
verbose = {"title": "Office Manager", "source": "remotive", "location": "Remote",
           "description": " ".join(["AI automation workflow LLM GPT no-code n8n Zapier"] * 40)}
check(prescreen_score(precise) > prescreen_score(verbose),
      "точная вакансия (%.0f) обгоняет многословную (%.0f)"
      % (prescreen_score(precise), prescreen_score(verbose)))

# Проверка 50/50 очереди Fair-Share
mock_pool = (
    [{"title": f"Djinni Job {i}", "source": "djinni", "prescreen_score": 100 - i} for i in range(50)] +
    [{"title": f"RemoteOK Job {i}", "source": "remoteok", "prescreen_score": 90 - i} for i in range(20)]
)
queue_20 = select_fair_share_queue(mock_pool, 20)
intl_count = sum(1 for j in queue_20 if j["source"] == "remoteok")
reg_count = sum(1 for j in queue_20 if j["source"] == "djinni")
check(intl_count == 10 and reg_count == 10,
      "Fair-Share 50/50 распределение: %d intl, %d regional" % (intl_count, reg_count))

print()
print("=== Хранилище отклонённых вакансий и генерация контекста для LLM ===")
import tempfile
from storage import JobStorage

with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
    test_db_path = tf.name

try:
    test_storage = JobStorage(test_db_path)
    test_storage.save_rejection("job_1", title="Java Lead", company="Legacy Corp", reason="Не тот стек / роль")
    test_storage.save_rejection("job_2", title="Sales Rep", company="CallCenter Inc", reason="Требуются звонки / не async")

    check(test_storage.is_rejected("job_1"), "is_rejected(job_1) == True")
    check(test_storage.is_rejected("job_2"), "is_rejected(job_2) == True")
    check(not test_storage.is_rejected("job_3"), "is_rejected(job_3) == False")

    rejections = test_storage.get_recent_rejections(limit=5)
    check(len(rejections) == 2, "get_recent_rejections вернул 2 записи")
    check(rejections[0]["reason"] == "Требуются звонки / не async", "reason корректно сохранён в БД")

    context = test_storage.get_rejection_prompt_context(limit=5)
    check("ПРИМЕРЫ ОТКЛОНЁННЫХ КАНДИДАТОМ ВАКАНСИЙ" in context, "Заголовок контекста сформирован")
    check('"Java Lead" (Legacy Corp) — Причина отказа: Не тот стек / роль' in context, "Первая вакансия в контексте")
    check('"Sales Rep" (CallCenter Inc) — Причина отказа: Требуются звонки / не async' in context, "Вторая вакансия в контексте")

    test_storage.close()
finally:
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

print()
print("=== Telegram Bot: Клавиатура причин отказа и парсинг метаданных ===")
from telegram_bot import build_reason_keyboard, REJECTION_MAP, _extract_meta

kb = build_reason_keyboard("test_12345")
check("inline_keyboard" in kb, "структура клавиатуры верна")
all_callbacks = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
check(len(all_callbacks) == 5, "5 кнопок выбора причины")
check(all(any(cb.startswith(prefix) for prefix in REJECTION_MAP) for cb in all_callbacks),
      "все callback_data имеют известный префикс причины")

mock_msg = {
    "text": "🔥 ПРИОРИТЕТ\n\n🏢 AI Specialist\n🏭 Acme Corp · 📍 Remote\n🌐 remoteok · ⭐ Score: 85/100"
}
t, c = _extract_meta(mock_msg)
check(t == "AI Specialist", "извлечение title: '%s'" % t)
check(c == "Acme Corp", "извлечение company: '%s'" % c)

print()
if failures:
    print("ПРОВАЛЕНО: %d" % len(failures))
    sys.exit(1)
print("Все проверки пройдены.")
