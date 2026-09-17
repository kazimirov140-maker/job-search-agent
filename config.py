"""
config.py — Конфигурация AI Job Search Agent (v4.1).

Что изменилось в v4.1:
  - Позиционирование: внедрение и настройка автоматизации у клиентов
    (implementation / customer success / onboarding / automation consulting),
    а не разработка ПО.
  - Гео-логика вынесена в geo_filter.py: русский ЯЗЫК — плюс,
    блокируется только Россия как ЮРИСДИКЦИЯ.
  - Все источники и ATS-борды проверены живыми запросами (август 2026).
  - Безопасный разбор переменных окружения: пустая строка больше не роняет
    импорт. GitHub Actions подставляет "" вместо незаданного секрета, и
    int("") раньше валил весь прогон до первой полезной строчки.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Безопасный разбор переменных окружения
# ─────────────────────────────────────────────

def _require(var_name: str) -> str:
    """Возвращает значение обязательной переменной или падает с понятной ошибкой."""
    value = os.getenv(var_name)
    if not value or not value.strip():
        logger.critical(
            "❌ Обязательная переменная окружения '%s' не найдена. "
            "Задайте её в .env или в секретах GitHub Actions.",
            var_name,
        )
        raise EnvironmentError(
            "Отсутствует обязательная переменная окружения: '" + var_name + "'."
        )
    return value.strip()


def _env_int(name: str, default: int) -> int:
    """
    Читает целое число, устойчиво к пустой строке и мусору.

    Именно здесь раньше падал весь прогон: GitHub Actions выставляет
    переменную в "", если секрет не создан, поэтому default в os.getenv
    не срабатывал и int("") бросал ValueError на импорте config.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Переменная %s='%s' не число, беру значение по умолчанию %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    """Читает дробное число, устойчиво к пустой строке и мусору."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Переменная %s='%s' не число, беру значение по умолчанию %s", name, raw, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in (os.getenv(name) or "").split(",") if item.strip()]


# ─────────────────────────────────────────────
# Обязательные секреты
# ─────────────────────────────────────────────

TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _require("TELEGRAM_CHAT_ID")

# Провайдер LLM больше не привязан к одному конкретному сервису: цепочка
# в llm_engine перебирает openrouter → gemini → groq → cerebras. Поэтому
# обязателен не какой-то определённый ключ, а НАЛИЧИЕ ХОТЯ БЫ ОДНОГО.
# Прежнее _require("OPENROUTER_API_KEY") роняло прогон на импорте, если
# в GitHub был заведён только ключ Gemini.
LLM_KEY_ENVS = [
    "OPENROUTER_API_KEY", "OPENROUTER_API_KEYS",
    "GEMINI_API_KEY", "GEMINI_API_KEYS",
    "GROQ_API_KEY", "GROQ_API_KEYS",
    "CEREBRAS_API_KEY", "CEREBRAS_API_KEYS",
]

OPENROUTER_API_KEY: str = (os.getenv("OPENROUTER_API_KEY") or "").strip()

_available_llm_keys = [name for name in LLM_KEY_ENVS if (os.getenv(name) or "").strip()]
if not _available_llm_keys:
    logger.critical(
        "❌ Не задан ни один ключ LLM. Нужен минимум один из: %s",
        ", ".join(LLM_KEY_ENVS),
    )
    raise EnvironmentError(
        "Не задан ни один ключ LLM. Добавьте хотя бы один из: "
        + ", ".join(LLM_KEY_ENVS)
    )
logger.info("Доступные ключи LLM: %s", ", ".join(_available_llm_keys))

# Gmail (IMAP) — опционально
GMAIL_EMAIL: str = (os.getenv("GMAIL_EMAIL") or "").strip()
GMAIL_APP_PASSWORD: str = (os.getenv("GMAIL_APP_PASSWORD") or "").strip()


# ─────────────────────────────────────────────
# Числовые настройки
# ─────────────────────────────────────────────

EMAIL_DAYS_BACK = _env_int("EMAIL_DAYS_BACK", 7)
MAX_EMAILS = _env_int("MAX_EMAILS", 15)
MAX_DESCRIPTION_CHARS = _env_int("MAX_DESCRIPTION_CHARS", 2200)
MAX_EMAIL_BODY_CHARS = _env_int("MAX_EMAIL_BODY_CHARS", 3500)
MAX_LLM_JOBS_PER_RUN = max(1, _env_int("MAX_LLM_JOBS_PER_RUN", 35))
LLM_REQUEST_DELAY = max(0.0, _env_float("LLM_REQUEST_DELAY", 4.5))

# Сколько подряд идущих сбоев LLM допустимо, прежде чем прекратить пакет.
# Одиночный нераспарсенный ответ больше НЕ убивает весь прогон.
MAX_CONSECUTIVE_LLM_FAILURES = max(1, _env_int("MAX_CONSECUTIVE_LLM_FAILURES", 5))

SCORE_THRESHOLD_SILENT = _env_int("SCORE_THRESHOLD_SILENT", 60)
SCORE_THRESHOLD_NOTIFY = _env_int("SCORE_THRESHOLD_NOTIFY", 75)
SCORE_THRESHOLD_PRIORITY = _env_int("SCORE_THRESHOLD_PRIORITY", 85)

# Минимальная допустимая зарплата (USD/мес). Если вилка не указана — вакансия проходит.
# 0 = фильтр по зарплате выключен. Задайте свой порог в .env.
MIN_SALARY_USD = _env_int("MIN_SALARY_USD", 0)

# Лимит вакансий для детальной оценки LLM за один прогон
MAX_LLM_JOBS_PER_RUN = max(5, _env_int("MAX_LLM_JOBS_PER_RUN", 35))

# Форматы работы
REMOTE_ONLY = _env_bool("REMOTE_ONLY", True)
ALLOW_HYBRID = _env_bool("ALLOW_HYBRID", False)
ALLOW_ONSITE = _env_bool("ALLOW_ONSITE", False)
ALLOW_INTERNSHIP = _env_bool("ALLOW_INTERNSHIP", True)
ALLOW_VOLUNTEER = _env_bool("ALLOW_VOLUNTEER", False)

# Отбрасывать вакансии, у которых удалёнка открыта только для США, Канады,
# Австралии, LATAM или APAC. Домашний регион оператора — ЕС, поэтому такая вакансия
# нерелевантна независимо от содержания.
BLOCK_GEO_RESTRICTED = _env_bool("BLOCK_GEO_RESTRICTED", True)

# Требовать ЯВНОГО признака удалёнки. Без этого флага вакансия, где формат
# просто не упомянут, считается удалённой — и офисные роли из ATS-бордов
# проходят фильтр целиком.
REQUIRE_REMOTE_EVIDENCE = _env_bool("REQUIRE_REMOTE_EVIDENCE", True)

# Источники, где ВСЕ вакансии удалённые по построению борда. Для них
# отсутствие слова "remote" в тексте — не повод для отказа.
REMOTE_ONLY_SOURCES = {
    "remoteok", "weworkremotely", "himalayas", "jobicy", "remotive",
    "workingnomads", "nodesk", "remote.co", "jobspresso", "dailyremote",
    "justremote", "remoteworkhub", "arbeitnow",
}

# ─────────────────────────────────────────────
# Режим прогона
#
# standard — весь рынок, включая украино/русскоязычный сегмент.
# en_async — только международные англоязычные async-борды. Русскоязычные
#            источники и бонусы за диаспору отключаются, иначе украинские
#            вакансии стабильно перебивают английские: +18 к score за язык
#            и страну против +15 за async делали англоязычную выдачу
#            неконкурентной внутри лимита в 35 оценок LLM.
# ─────────────────────────────────────────────

def run_mode() -> str:
    """Читается на каждом вызове: web_app передаёт режим через окружение."""
    return (os.getenv("RUN_MODE") or "standard").strip().lower()


def is_en_async_mode() -> bool:
    return run_mode() == "en_async"


EXTRA_BLOCKLIST_KEYWORDS = _env_list("EXTRA_BLOCKLIST_KEYWORDS")
REQUIRED_KEYWORDS = _env_list("REQUIRED_KEYWORDS")
REQUIRED_KEYWORDS_MODE = (os.getenv("REQUIRED_KEYWORDS_MODE") or "any").strip().lower()


# ─────────────────────────────────────────────
# Email-дайджесты
# ─────────────────────────────────────────────

EMAIL_SENDERS = _env_list("EMAIL_SENDERS") or [
    "linkedin.com", "wellfound.com", "angel.co", "otta.com",
    "djinni.co", "jobs.dou.ua", "upwork.com",
]

EMAIL_ALLOWED_SUBJECTS = _env_list("EMAIL_ALLOWED_SUBJECTS") or [
    "job alert", "jobs", "vacancies", "vacancy", "ваканс", "работ", "робот",
    "recommended for you", "job recommendation", "new roles", "opportunities",
    "hiring", "matches",
]

EMAIL_IGNORED_SUBJECTS = _env_list("EMAIL_IGNORED_SUBJECTS") or [
    "profile", "connection", "contact", "viewed", "popular in your network",
    "people you may know", "публикац", "результатах поиска", "newsletter",
    "invitation", "endorsed",
]


# ─────────────────────────────────────────────
# ИСТОЧНИКИ — обновлены и расширены (август 2026)
# ─────────────────────────────────────────────

# Djinni (целевые категории)
DJINNI_FEEDS = [
    "Support",          # Customer Success / Technical Support
    "Product Manager",
    "Project Manager",
    "Data Science",
]
DJINNI_RSS_BASE = "https://djinni.co/jobs/rss/"

# DOU
DOU_FEEDS = [
    {"category": "Data Science"},
    {"category": "Project Manager"},
    {"category": "Product Manager"},
    {"category": "Python"},
    {"search": "AI"},
    {"search": "автоматизац"},
]
DOU_RSS_BASE = "https://jobs.dou.ua/vacancies/feeds/"

# WeWorkRemotely категории
WWR_FEEDS = [
    "https://weworkremotely.com/remote-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
]

SOURCES = {
    # Международные remote/async платформы (высший приоритет)
    "remoteok":       "https://remoteok.com/api",
    "weworkremotely": "https://weworkremotely.com/remote-jobs.rss",
    "himalayas":      "https://himalayas.app/jobs/api",
    "jobicy":         "https://jobicy.com/api/v2/remote-jobs",
    "arbeitnow":      "https://www.arbeitnow.com/api/job-board-api",
    "remotive":       "https://remotive.com/api/remote-jobs",

    # Русскоязычный и украинский рынок
    "djinni":         DJINNI_RSS_BASE,
    "dou":            DOU_RSS_BASE,

    # Email-дайджесты через Gmail IMAP
    "email_digest":   "Gmail IMAP",

    # ATS прямых компаний (AI/Automation/Remote-first)
    "greenhouse":     "https://boards-api.greenhouse.io/v1/boards/{company}/jobs",
    "lever":          "https://api.lever.co/v0/postings/{company}",
    
    # Новые RSS борды (added via task)
    "remote.co":      "https://remote.co/remote-jobs/feed/",
    "jobspresso":     "https://jobspresso.co/remote-work/feed/",
    "dailyremote":    "https://dailyremote.com/remote-jobs.rss",
    "justremote":     "https://justremote.co/remote-jobs/rss",
    "remoteworkhub":  "https://remoteworkhub.com/feed/",
}

UPWORK_RSS_URL = os.getenv("UPWORK_RSS_URL", "").strip()

# ─────────────────────────────────────────────
# ОТКЛЮЧЁННЫЕ ИСТОЧНИКИ — проверены, мертвы или нерелевантны
# ─────────────────────────────────────────────
DEAD_SOURCES = {
    "authentic":                  "RSS отдаёт статьи блога, а не вакансии",
    "asyncjobs.co/feed.xml":      "404",
    "workingnomads.com/api":      "404",
    "workingasync.io/feed":       "200, но text/html — это не фид",
    "asynchub.co":                "домен не резолвится",
    "jobsremote.ai/feed":         "200, но 114 байт — заглушка",
    "europeremotely.com/rss":     "HTTP 436, 0 байт",
    "himalayas.app/api/jobs":     "404 — рабочий адрес himalayas.app/jobs/api",
    "adzuna":                     "нет ключей ADZUNA_APP_ID / ADZUNA_APP_KEY",
}

# ATS-борды компаний с упором на автоматизацию, AI и remote-культуру
# Списки сверены живыми запросами 06.09.2026. Убраны 39 слагов Greenhouse и
# 12 Lever, отдававших 404: у этих компаний либо другой ATS (openai, notion,
# zapier, retool — Ashby), либо другой слаг. Каждый мёртвый слаг стоил
# отдельного HTTP-запроса с таймаутом, то есть примерно двух лишних минут
# на каждый прогон, и не приносил ни одной вакансии.
GREENHOUSE_COMPANIES = [
    "make", "airtable", "typeform", "storyblok", "gitlab", "canonical",
    "elastic", "remote", "turing", "veriff", "similarweb", "intercom",
    "cloudflare", "jetbrains", "stripe", "discord", "figma", "vercel",
    "asana", "anthropic", "scaleai", "stabilityai", "togetherai", "xai",
    "databricks", "descript", "assemblyai", "smartsheet", "wrike",
    "webflow", "contentful", "netlify",
]

LEVER_COMPANIES = [
    "swordhealth", "palantir",
]

ASHBY_COMPANIES = [
    "zapier", "linear", "posthog", "resend", "synthesia", "perplexity", "cursor",
]


# ─────────────────────────────────────────────
# КЛЮЧЕВЫЕ СЛОВА
#
# Это фильтр RECALL на уровне источника, а не precision: он решает, какие
# вакансии вообще попадут в очередь. Точность обеспечивает LLM дальше по
# конвейеру. Поэтому термины короткие и широкие — длинные фразы вроде
# "AI implementation consultant" в текстах вакансий дословно не встречаются.
# ─────────────────────────────────────────────

TRACK_1_KEYWORDS = [
    # ── Ядро: внедрение и настройка автоматизации у клиентов ──
    "automation", "автоматизац", "автоматизація",
    "implementation", "внедрение", "впровадження",
    "onboarding", "adoption", "enablement",
    "customer success", "client success", "customer onboarding",
    "solutions consultant", "solutions engineer", "solutions architect",
    "implementation consultant", "implementation specialist",
    "technical account manager", "integration specialist",
    "professional services", "delivery manager",

    # ── AI / LLM / AI Agents ──
    "AI", "LLM", "GPT", "generative AI", "agentic",
    "prompt engineer", "prompt engineering",
    "AI agent", "AI agents", "agent builder", "agentic workflows",
    "multi-agent", "autonomous agent", "AI automation", "AI consultant",
    "LangChain", "CrewAI", "AutoGen", "LangGraph", "RAG", "embedding", "vector database",
    "OpenAI", "Anthropic", "Claude",
    "chatbot", "conversational AI", "AI assistant",

    # ── No-code / workflow платформы и автоматизация ──
    "no-code", "low-code", "nocode",
    "n8n", "Zapier", "Make.com", "Airtable", "Retool",
    "workflow automation", "process automation",
    "business process", "RPA", "intelligent automation",

    # ── Доменная ниша оператора (настраивается под себя) ──
    "logistics", "supply chain", "freight", "логистик",
    "customs", "forwarding",

    # ── Async-маркеры ──
    "async", "asynchronous", "remote-first", "distributed team",
]

# Трек 2 сохранён для обратной совместимости с debug.py, сам трек отключён.
TRACK_2_KEYWORDS = [
    "AI trainer", "AI evaluator", "LLM evaluator",
    "data annotation", "RLHF", "model evaluation",
]

SEARCH_KEYWORDS = TRACK_1_KEYWORDS


# ─────────────────────────────────────────────
# БЛОК-ЛИСТ — только тематический и только по НАЗВАНИЮ
#
# Гео-блокировка живёт в geo_filter.py и здесь намеренно отсутствует.
# Проверка идёт по title, а не по описанию: подстрока "Golang" в разделе
# "будет плюсом" не повод выбрасывать вакансию по автоматизации, а
# "occasional on-site meetup" не делает remote-вакансию офисной.
# Формат работы и уровень seniority оцениваются в scoring.py, а не режутся
# здесь — иначе правило «понизить score за Senior» не может сработать.
# ─────────────────────────────────────────────

BLOCKLIST_KEYWORDS = [
    # Стеки вне профиля оператора
    "c++", "c#", ".net developer", "java developer", "golang developer",
    "ruby on rails", "php developer", "wordpress developer",
    "android developer", "ios developer", "unity developer",
    "game developer", "embedded engineer", "firmware",
    # Роли, явно не подходящие по профилю
    "principal engineer", "staff engineer",
    "engineering manager", "head of engineering", "cto",
    "sales development representative", "sdr",
    # Явные географические ограничения
    "us only", "usa only", "canada only", "australia only",
    "must be located in the us", "authorized to work in the us",
    "us citizens only", "security clearance",
]

# Форматы работы — проверяются отдельно и только по title и location.
ONSITE_TERMS = ["on-site", "onsite", "in-office", "office-based", "hybrid"]

# ─────────────────────────────────────────────
# МАРКЕРЫ SYNC-FIRST ФОРМАТА РАБОТЫ
#
# Агент нацелен на async / written-first команды: документация, тикеты,
# Slack, Loom вместо постоянных синхронных встреч. Если вакансия содержит
# любой из этих маркеров и при этом не заявляет явный async, её score
# штрафуется как менее подходящая по формату.
# Для украино- и русскоязычных вакансий блок не применяется.
#
# Список расширяется без правки кода: EXTRA_SYNC_FIRST_MARKERS в .env
# принимает дополнительные маркеры через запятую.
# ─────────────────────────────────────────────

SYNC_FIRST_MARKERS = [
    # Клиентские звонки и устные синхронные митинги
    "client calls",
    "customer meetings",
    "kickoff calls",
    "stakeholder presentations",
    "collaborating with clients across",
    "client-facing",
    "customer-facing presentation",
    "lead customer meetings",
    "daily standups",
    "video calls with clients",

    # Консалтинг с западными клиентами
    "consulting environment",
    "clients in western europe",
    "us clients",
    "global clients",
]

# Дополнительные маркеры из окружения (не хранятся в репозитории).
SYNC_FIRST_MARKERS += _env_list("EXTRA_SYNC_FIRST_MARKERS")

# ─────────────────────────────────────────────
# МАРКЕРЫ DATA ANALYST / BI / HEAVY SQL
#
# Если вакансия содержит 2+ маркера в обязательных требованиях,
# её score штрафуется на -35 баллов.
# В разделе "будет плюсом / nice to have" штраф не применяется.
# ─────────────────────────────────────────────

DATA_ANALYST_BLOCKERS = [
    # SQL продвинутого уровня
    "window functions",
    "оконные функции",
    "query optimization",
    "оптимизация запросов",
    "complex joins",
    "сложные join",
    "stored procedures",

    # BI платформы
    "power bi",
    "power query",
    "dax",
    "tableau",
    "looker",
    "metabase",
    "clickhouse",
    "bigquery",
    "snowflake",
    "dbt",

    # Excel как рабочий инструмент
    "xlookup",
    "index/match",
    "сводные таблицы",
    "pivot tables",

    # Data science стек
    "pandas",
    "numpy",
    "scikit-learn",
    "matplotlib",
]

# ─────────────────────────────────────────────
# МАРКЕРЫ ML ENGINEER / DEEP LEARNING / MLOPS
# ─────────────────────────────────────────────

ML_ENGINEER_BLOCKERS = [
    "model training",
    "train models",
    "обучение моделей",
    "ml lifecycle",
    "model evaluation and monitoring",
    "fine-tuning models",
    "pytorch",
    "tensorflow",
    "mlops",
    "mlflow",
    "sagemaker",
    "computer vision",
    "cv engineer",
    "nlp model development",
    "feature engineering",
]


# ─────────────────────────────────────────────
# ТЕМАТИЧЕСКИЙ ГЕЙТ
#
# Роль сама по себе (support, account manager, project manager) ещё не
# делает вакансию целевой: категория Djinni "Support" на 90% состоит из
# саппорт-агентов iGaming. Поэтому требуем хотя бы один признак AI или
# автоматизации. Это единственный жёсткий гейт по описанию — он повышает
# точность там, где лимит LLM-оценок сильно ограничен.
#
# Проверка идёт по ГРАНИЦАМ СЛОВА: подстрока "ai" иначе ловит email,
# maintain, detail и пропускает вообще всё.
# ─────────────────────────────────────────────

REQUIRE_TOPICAL_CORE = _env_bool("REQUIRE_TOPICAL_CORE", True)

TOPICAL_CORE_TERMS = [
    r"ai", r"a\.i\.", r"artificial\s+intelligence", r"machine\s+learning",
    r"ml", r"llm", r"gpt", r"chatgpt", r"claude", r"gemini",
    r"generative", r"genai", r"agentic", r"agent\w*", r"copilot",
    r"rag", r"langchain", r"llamaindex", r"crewai", r"autogen", r"langgraph",
    r"embedding\w*", r"vector\s+database",
    r"prompt\w*", r"chatbot\w*", r"conversational",
    r"automation", r"automate\w*", r"automated",
    r"автоматизац\w*", r"автоматизуват\w*", r"автоматиз\w*",
    r"no[-\s]?code", r"low[-\s]?code", r"nocode",
    r"n8n", r"zapier", r"make\.com", r"retool", r"airtable", r"rpa",
    r"workflow\w*", r"integration\w*", r"интеграц\w*", r"інтеграц\w*",
    r"implementation", r"внедрен\w*", r"впровадж\w*",
    r"onboarding", r"adoption", r"enablement",
    r"обучен\w*", r"training", r"клиент\w*",
    r"data\s+science", r"nlp", r"computer\s+vision",
]

# СИЛЬНЫЕ признаки — только они годятся для второго пути гейта (признак в
# ОПИСАНИИ + целевая роль в названии).
#
# Слабые термины из общего списка — training, onboarding, integration,
# workflow, клиент — встречаются в теле почти любого объявления
# ("training provided", "onboarding process", "integration with our CRM").
# Вместе с широкими ролями вроде "advisor" и "specialist" они пропускали
# откровенный мусор: так в очередь на оценку попадал «Financial Aid
# Advisor». В названии те же слова остаются допустимыми: «Onboarding
# Specialist» — целевая роль, а не случайное совпадение.
STRONG_CORE_TERMS = [
    r"ai", r"a\.i\.", r"artificial\s+intelligence", r"machine\s+learning",
    r"llm", r"gpt", r"chatgpt", r"claude", r"gemini",
    r"generative", r"genai", r"agentic", r"copilot",
    r"rag", r"langchain", r"llamaindex", r"crewai", r"autogen", r"langgraph",
    r"embedding\w*", r"vector\s+database",
    r"prompt\s+engineer\w*", r"chatbot\w*", r"conversational\s+ai",
    r"automation", r"automate\w*", r"automated",
    r"автоматизац\w*", r"автоматизуват\w*",
    r"no[-\s]?code", r"low[-\s]?code", r"nocode",
    r"n8n", r"zapier", r"make\.com", r"retool", r"rpa",
    r"workflow\s+automation", r"process\s+automation",
]

# Целевые семейства ролей — проверяются ТОЛЬКО по названию.
# Нужны для второго пути гейта: вакансия «Customer Success Manager», у
# которой AI-контекст описан в теле, должна проходить, а «Senior Backend
# Engineer» из бойлерплейта крупной компании — нет.
TARGET_ROLE_TERMS = [
    r"consultant", r"consulting", r"консультант\w*",
    r"implementation", r"внедрен\w*", r"впровадж\w*",
    r"customer\s+success", r"client\s+success", r"customer\s+experience",
    r"onboarding", r"adoption", r"enablement",
    r"solutions?\s+(?:engineer|architect|consultant|advisor|specialist|builder)",
    r"account\s+manager", r"technical\s+account",
    r"automation\w*", r"автоматизац\w*",
    r"workflow\w*", r"integration\w*", r"process\s+automation",
    r"zapier", r"n8n", r"make(?:\.com)?",
    r"agent\w*", r"builder",
    r"specialist", r"спеціаліст\w*", r"специалист\w*",
    r"advisor", r"analyst", r"аналітик\w*", r"аналитик\w*",
    r"project\s+(?:manager|coordinator)", r"product\s+manager",
    r"support\s+(?:engineer|specialist|manager)",
    r"operations?\s+(?:manager|specialist|lead|associate)",
    r"prompt\w*", r"ai", r"ml", r"llm",
]

_TOPICAL_CORE_RE = None
_TARGET_ROLE_RE = None
_STRONG_CORE_RE = None


def _build(terms):
    import re
    body = "|".join("(?<!\\w)(?:" + t + ")(?!\\w)" for t in terms)
    return re.compile(body, re.IGNORECASE | re.UNICODE)


def _topical_core_re():
    """Ленивая компиляция: regex собирается один раз при первом вызове."""
    global _TOPICAL_CORE_RE
    if _TOPICAL_CORE_RE is None:
        _TOPICAL_CORE_RE = _build(TOPICAL_CORE_TERMS)
    return _TOPICAL_CORE_RE


def _target_role_re():
    global _TARGET_ROLE_RE
    if _TARGET_ROLE_RE is None:
        _TARGET_ROLE_RE = _build(TARGET_ROLE_TERMS)
    return _TARGET_ROLE_RE


def _strong_core_re():
    global _STRONG_CORE_RE
    if _STRONG_CORE_RE is None:
        _STRONG_CORE_RE = _build(STRONG_CORE_TERMS)
    return _STRONG_CORE_RE


def has_topical_core(job: dict) -> bool:
    """
    Двухступенчатый тематический гейт.

    Проходит, если:
      1. признак AI/автоматизации есть прямо в НАЗВАНИИ (любой из общего
         списка), либо
      2. СИЛЬНЫЙ признак есть в описании И название относится к целевому
         семейству ролей.

    Одного упоминания в описании недостаточно: у крупных компаний
    (Cloudflare, MongoDB, Canonical) слово «AI» стоит в бойлерплейте
    почти каждой вакансии, и односоставный гейт пропускал по 150-250
    нерелевантных бэкенд-ролей с каждого борда.

    Во втором пути используется сокращённый список: слабые термины
    ("training", "onboarding process", "integration with our CRM") есть в
    теле почти любого объявления и вместе с широкой ролью в названии
    ("advisor", "specialist") протаскивали вакансии без всякого отношения
    к AI и автоматизации.
    """
    title = str(job.get("title") or "")

    if _topical_core_re().search(title):
        return True

    description = str(job.get("description") or "")
    return bool(_strong_core_re().search(description) and _target_role_re().search(title))


def _title_blocklist() -> list[str]:
    return [kw.lower() for kw in (BLOCKLIST_KEYWORDS + EXTRA_BLOCKLIST_KEYWORDS)]


def is_blocked(job: dict, source_url: str = "") -> bool:
    """
    Детерминированный фильтр до вызова LLM.

    Возвращает True, если вакансию нужно отбросить. Диагностическая причина
    доступна через block_reason() — она пишется в лог, чтобы ложные отказы
    можно было увидеть, а не гадать о них.
    """
    return bool(block_reason(job, source_url))


def is_salary_below_threshold(job: dict) -> bool:
    """
    Проверяет, указана ли зарплата явно ниже MIN_SALARY_USD.
    Если вилка не указана — возвращает False (пропускаем вакансию).
    """
    import re
    sal_max = job.get("salary_max")
    if isinstance(sal_max, (int, float)) and 0 < sal_max < MIN_SALARY_USD:
        return True

    sal_text = str(job.get("salary") or "").strip()
    if sal_text:
        matches = re.findall(r'\$\s*(\d+(?:[,\.]\d+)?)', sal_text)
        if matches:
            vals = []
            for m in matches:
                try:
                    v = float(m.replace(',', ''))
                    if 'k' in sal_text.lower() or 'к' in sal_text.lower():
                        v *= 1000
                    vals.append(v)
                except ValueError:
                    pass
            # Если все распознанные суммы строго меньше порога и не являются часовыми ставками (например, $20-$50/час)
            if vals and all(50 < v < MIN_SALARY_USD for v in vals):
                return True
    return False


def block_reason(job: dict, source_url: str = "") -> str:
    """Возвращает причину блокировки или пустую строку."""
    # Импорт внутри функции: geo_filter не должен тянуть config при импорте.
    from geo_filter import detect_jurisdiction
    from location_filter import detect_geo_restriction, detect_remote_status

    blocked, reason = detect_jurisdiction(job, source_url)
    if blocked:
        return reason

    title = str(job.get("title") or "").lower()

    for kw in _title_blocklist():
        if kw in title:
            return "тематика: «" + kw + "» в названии"

    if is_salary_below_threshold(job):
        return "зарплата: ниже $%d/мес" % MIN_SALARY_USD

    # ── Гео-ограничение удалёнки ──
    # Именно этой проверки не хватало: поле location у Remotive и Jobicy
    # содержит требование к локации КАНДИДАТА ("USA Only", "LATAM"), а не
    # адрес компании. Раньше оно нигде не читалось, и «удалёнка только для
    # США» доходила до Telegram как обычная remote-вакансия.
    if BLOCK_GEO_RESTRICTED:
        geo_blocked, geo_reason = detect_geo_restriction(job)
        if geo_blocked:
            return "гео: " + geo_reason

    # ── Формат работы ──
    # Проверяется НАЛИЧИЕ признака удалёнки, а не отсутствие слова "onsite".
    # Прежняя версия пропускала любую вакансию, в которой формат просто не
    # упомянут, — так в выдачу попадали офисные роли из Greenhouse и Lever.
    if REMOTE_ONLY and not ALLOW_ONSITE:
        source = str(job.get("source") or "").split("_")[0].lower()
        board_is_remote_only = source in REMOTE_ONLY_SOURCES

        status = detect_remote_status(job, strict=not board_is_remote_only)
        if status == "onsite":
            return "формат: офис"
        if status == "hybrid" and not ALLOW_HYBRID:
            return "формат: гибрид"
        if status == "unknown" and REQUIRE_REMOTE_EVIDENCE and not board_is_remote_only:
            return "формат: удалёнка не подтверждена"

    if REQUIRE_TOPICAL_CORE and not has_topical_core(job):
        return "нет признаков AI или автоматизации"

    if not ALLOW_INTERNSHIP and ("internship" in title or "intern " in title or "стажер" in title):
        return "стажировка"
    if not ALLOW_VOLUNTEER and ("volunteer" in title or "unpaid" in title):
        return "волонтёрство"

    if REQUIRED_KEYWORDS:
        text = " ".join(str(job.get(f) or "") for f in ("title", "description", "location")).lower()
        hits = sum(1 for kw in REQUIRED_KEYWORDS if kw.lower() in text)
        required_ok = hits == len(REQUIRED_KEYWORDS) if REQUIRED_KEYWORDS_MODE == "all" else hits > 0
        if not required_ok:
            return "нет обязательных ключевых слов"

    return ""


# ─────────────────────────────────────────────
# Веса источников — влияют на ПОРЯДОК оценки, не на итоговый score
# ─────────────────────────────────────────────

SOURCE_WEIGHTS = {
    "remoteok": 1.5,
    "workingnomads": 1.5,
    "nodesk": 1.4,
    "weworkremotely": 1.5,
    "himalayas": 1.4,
    "jobicy": 1.4,
    "arbeitnow": 1.3,
    "remotive": 1.3,
    "telegram": 1.3,
    "habrcareer": 1.2,
    "email_digest": 1.4,
    "lever": 1.2,
    "greenhouse": 1.2,
    "djinni": 1.0,
    "dou": 1.0,
    "remote.co": 1.4,
    "jobspresso": 1.4,
    "dailyremote": 1.4,
    "justremote": 1.3,
    "remoteworkhub": 1.3,
}
