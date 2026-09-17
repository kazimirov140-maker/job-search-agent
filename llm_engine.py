"""
llm_engine.py — Оценка вакансий через OpenRouter (v4.1).

Что исправлено в v4.1:
  - max_tokens поднят с 700 до 1500, а reasoning ограничен effort="low".
    Прежний `reasoning={"exclude": True}` только ПРЯТАЛ рассуждения из
    ответа, но токены на них всё равно тратились из общего бюджета —
    поэтому JSON приходил обрезанным и не парсился. Это и была причина
    прогона на 745 вакансий с нулём отправленных.
  - Цепочка моделей начинается с instruct-моделей, а не с reasoning.
  - Модель, вернувшая непарсящийся ответ, больше не роняет прогон:
    управление уходит к следующей модели в цепочке.
  - 429 (дневная квота) по-прежнему НЕ ретраится — лимит общий на весь
    free-пул, повторы бессмысленны.
  - finish_reason и начало сырого ответа пишутся в лог на WARNING,
    чтобы такие поломки было видно без включения DEBUG.
  - Извлечение вакансий из писем просит объект {"jobs": [...]}, а не голый
    массив: response_format=json_object требует именно объект.

Функции:
    evaluate_job_with_llm(job)      — структурная оценка вакансии.
    extract_jobs_from_email(text)   — разбор письма-дайджеста.
    test_connection()               — проверка доступности OpenRouter.
"""

import json
import logging
import os
import random
import re
import time

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

from config import MAX_DESCRIPTION_CHARS, MAX_EMAIL_BODY_CHARS, _env_int

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Цепочка бесплатных моделей OpenRouter
#
# Порядок важен: сначала instruct-модели, которые сразу отдают JSON,
# затем reasoning-модели как запасной вариант. Прежняя версия начинала
# с reasoning-модели, и её рассуждения съедали весь бюджет max_tokens.
# Список сверен с https://openrouter.ai/api/v1/models 23.08.2026;
# несуществующий openai/gpt-oss-20b:free удалён.
# ─────────────────────────────────────────────

FREE_MODELS = [
    "openrouter/free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]

MODEL_CHAIN = [os.getenv("OPENROUTER_MODEL").strip()] if (os.getenv("OPENROUTER_MODEL") or "").strip() else FREE_MODELS

MAX_EVALUATION_TOKENS = 1500

# ─────────────────────────────────────────────
# ПРОВАЙДЕРЫ
#
# Gemini Flash стоит на 1-м месте: сверхбыстрый отклик (2-3 сек),
# надёжный JSON, 1500 бесплатных запросов в сутки на каждый ключ.
# При исчерпании переходит к OpenRouter, Groq и Cerebras.
# ─────────────────────────────────────────────

PROVIDERS = [
    {
        "name": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_envs": ["GEMINI_API_KEY", "GEMINI_API_KEYS"],
        "models": [m.strip() for m in (os.getenv("GEMINI_MODELS") or
                   "gemini-3.6-flash,gemini-flash-latest,gemini-flash-lite-latest"
                   ).split(",") if m.strip()],
        "supports_reasoning_control": False,
        "rpm": _env_int("GEMINI_RPM", 12),
    },
    {
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_envs": ["OPENROUTER_API_KEY", "OPENROUTER_API_KEYS"],
        "models": MODEL_CHAIN,
        "supports_reasoning_control": True,
        "rpm": _env_int("OPENROUTER_RPM", 20),
    },
    {
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key_envs": ["GROQ_API_KEY", "GROQ_API_KEYS"],
        "models": [m.strip() for m in (os.getenv("GROQ_MODELS") or
                   "llama-3.3-70b-versatile,llama-3.1-8b-instant").split(",") if m.strip()],
        "supports_reasoning_control": False,
        "rpm": _env_int("GROQ_RPM", 25),
    },
    {
        "name": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "key_envs": ["CEREBRAS_API_KEY", "CEREBRAS_API_KEYS"],
        "models": [m.strip() for m in (os.getenv("CEREBRAS_MODELS") or
                   "llama-3.3-70b").split(",") if m.strip()],
        "supports_reasoning_control": False,
        "rpm": _env_int("CEREBRAS_RPM", 25),
    },
]


def _provider_keys(provider: dict) -> list[str]:
    """
    Собирает все ключи провайдера из его переменных окружения.

    Переменная может содержать несколько ключей через запятую. Каждый ключ
    даёт СВОЙ дневной лимит, поэтому при исчерпании одного цепочка берёт
    следующий — это кратно расширяет бесплатную квоту без смены провайдера.
    """
    keys: list[str] = []
    for env_name in provider.get("key_envs", []):
        for part in (os.getenv(env_name) or "").split(","):
            part = part.strip()
            if part and part not in keys:
                keys.append(part)
    return keys

# ─────────────────────────────────────────────
# ОГРАНИЧЕНИЕ ЧАСТОТЫ ЗАПРОСОВ
#
# У каждого провайдера свой лимит запросов в минуту. Скользящее окно
# держит темп ниже лимита ПРЕВЕНТИВНО, не дожидаясь 429: это надёжнее,
# чем ловить отказы и откатываться.
#
# Минутный rate limit и дневная квота — разные вещи и обрабатываются
# по-разному: первый значит «подожди», вторая — «на сегодня всё».
# ─────────────────────────────────────────────

RATE_LIMIT_RETRIES = _env_int("RATE_LIMIT_RETRIES", 3)

_call_times: dict[str, list[float]] = {}


def _throttle(provider: dict, slot: str | None = None) -> None:
    """
    Придерживает запрос, если за последнюю минуту их уже слишком много.

    Окно ведётся по СЛОТУ «провайдер + ключ», а не по провайдеру целиком:
    лимит запросов в минуту у Gemini и Groq считается на ключ, поэтому три
    ключа дают три независимых окна.
    """
    key = slot or provider["name"]
    rpm = max(1, int(provider.get("rpm", 20)))
    window = _call_times.setdefault(key, [])

    now = time.monotonic()
    window[:] = [t for t in window if t > now - 60.0]

    if len(window) >= rpm:
        wait = 60.0 - (now - window[0]) + 0.2
        if wait > 0:
            logger.info("[%s] достигнут лимит %d запросов/мин — пауза %.1f с", key, rpm, wait)
            time.sleep(wait)
            now = time.monotonic()
            window[:] = [t for t in window if t > now - 60.0]

    window.append(time.monotonic())

# Квота отслеживается ОТДЕЛЬНО по каждому провайдеру: исчерпание одного
# больше не останавливает весь прогон, как было в v3.
_exhausted_providers: set[str] = set()
_clients: dict[str, OpenAI] = {}

_FALLBACK_EVALUATION: dict = {
    "match_score": 0,
    "passed_filter": False,
    "evaluation_status": "unavailable",
    "reason": "Оценка недоступна — LLM временно недоступна.",
}


# ─────────────────────────────────────────────
# Профиль кандидата
#
# Профиль — это конфигурация конкретного инстанса, а не часть логики агента,
# поэтому он лежит в отдельном файле и в репозитории не хранится.
# Порядок поиска:
#   1. путь из переменной окружения PROFILE_PATH
#   2. profile.md рядом с кодом (в .gitignore)
#   3. profile.example.md — обезличенный образец из репозитория
# ─────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_profile() -> str:
    """Читает профиль кандидата из первого найденного файла."""
    for path in (os.getenv("PROFILE_PATH"),
                 os.path.join(_HERE, "profile.md"),
                 os.path.join(_HERE, "profile.example.md")):
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
    raise FileNotFoundError(
        "Не найден файл профиля. Скопируйте profile.example.md в profile.md "
        "и заполните его, либо укажите путь в переменной PROFILE_PATH."
    )


CANDIDATE_PROFILE = _load_profile()

_EVALUATION_SYSTEM_PROMPT = CANDIDATE_PROFILE + """
ЗАДАЧА: оценить вакансию и вернуть ТОЛЬКО валидный JSON-объект.
Не рассуждай. Ответ начинается с { и заканчивается }.

match_score — ЧИСТОЕ соответствие профилю и задачам, 0-100.
НЕ добавляй бонусы за язык, страну или async — это считается отдельно,
вне модели в Python. Оценивай только содержательное совпадение с профилем:
  90-100 — прямое создание AI-агентов, оркестрация мультиагентных систем, сборка автоматизаций (n8n/Make/Python), Prompt Engineering ИЛИ управление AI-продуктами (Product Management), внедрение/обучение клиентов на русском/украинском
  75-89  — customer success, onboarding, solutions, обучение клиентов (для ru/uk) ИЛИ разработка AI-воркфлоу, интеграция LLM, agentic workflows (для en/ru/uk)
  60-74  — смежные роли (Operations, Data, Automation support)
  40-59  — слабое совпадение
  0-39   — не подходит (чистая разработка ПО на Java/C++, холодные телефонные продажи, рутинный call-center)

passed_filter = false, ТОЛЬКО ЕСЛИ:
  - компания/юрисдикция РФ или РБ, оплата в рублях, требование гражданства РФ
  - строгий офисный формат on-site вне домашнего региона кандидата (регион указан в профиле выше)
  - требуется строгий US / Canada / Australia security clearance / citizenship
  - чистая разработка ПО без автоматизации и AI (например, Senior C++ / Java / iOS Dev)
  - уровень Staff / Principal, либо строго требуется 3 и более лет опыта работы (вилки до 3 лет допустимы)
  - роль требует постоянных синхронных голосовых холодных звонков на английском языке (телефонные продажи, диспетчер колл-центра).

ПРАВИЛО ГЕОГРАФИИ И ФОРМАТА (домашний регион и часовой пояс кандидата указаны в профиле выше):
  - Удалёнка, открытая только для резидентов США, Канады, Австралии, LATAM, APAC, Индии или любой
    другой страны за пределами ЕС -> geo_restriction = "region_locked" и passed_filter = false.
    Формулировки-маркеры: "US only", "must reside in the United States", "authorized to work in the US",
    "Remote (USA)", "US-based candidates", "W2", "green card", "security clearance", "LATAM only".
  - Требование работать в американских рабочих часах ("overlap with EST/PST", "US business hours",
    "core hours 9-5 ET") -> geo_restriction = "us_timezone". Это НЕ отказ сам по себе, но обязательно
    отметь: разница с CET 6-9 часов противоречит асинхронному формату.
  - Если открыто для worldwide / anywhere / EU / EMEA / Europe -> geo_restriction = "worldwide".
  - Если география не указана вовсе -> geo_restriction = "unknown".
  - Вакансия без признаков удалённой работы (офис, гибрид, конкретный город без слова remote)
    -> passed_filter = false.

ПРАВИЛО КОММУНИКАЦИИ И ЯЗЫКА:
  - Для англоязычных ролей (en): целевой фокус — создание AI-агентов, сборка сценариев автоматизации, Prompt Engineering, воркфлоу, документация и текстовое общение (Slack, Loom, Notion, Jira).
    * Если заявлен async / remote-first / no meetings / written communication / flexible hours -> async_evidence = "explicit_async".
    * Если стандартная удаленка (включая требования хорошего английского, командные синки, демо, работу с тикетами) -> async_evidence = "text_first".
    * ТОЛЬКО если вакансия требует непрерывных голосовых телефонных звонков (холодные продажи, outbound SDR, телефонный диспетчер) -> async_evidence = "calls_required" и passed_filter = false.
  - Для украино- и русскоязычных команд (uk/ru): фокус — как сборка агентов/автоматизаций, так и внедрение, обучение клиентов, онбординг, Customer Success, переговоры и митинги. Ставь passed_filter = true.

ПРАВИЛО ДЛЯ DATA ANALYST / BI СТЕКА:
  - Если вакансия содержит 2+ обязательных требования из стека классического Data Analyst / BI (SQL window functions, оптимизация запросов, сложные joins, Power BI, DAX, Tableau, Looker, Metabase, ClickHouse, BigQuery, Snowflake, dbt, Pandas/NumPy, Excel pivot tables / XLOOKUP) в ОСНОВНЫХ требованиях -> установи data_analyst_heavy = true.
  - Если те же технологии указаны ТОЛЬКО в разделе 'будет плюсом' / 'nice to have' -> установи data_analyst_heavy = false.

ПОЛЯ ОТВЕТА:
  passed_filter    — true / false
  match_score      — целое 0-100
  role_family      — agent_builder | automation | implementation | customer_success |
                     onboarding | prompt_engineering | solutions | advisory | engineering | other
  job_language     — uk | ru | en | es | other  (язык текста вакансии)
  company_country  — страна работодателя или "Unknown"
  async_evidence   — explicit_async | text_first | calls_required | unknown
  geo_restriction  — worldwide | eu_ok | us_timezone | region_locked | unknown
  work_format      — remote | hybrid | onsite | unknown
  seniority        — junior | mid | senior | staff | principal | unknown
  data_analyst_heavy — true / false  (2+ маркера Data Analyst / BI в обязательных требованиях)
  priority_category— короткая метка направления
  why_match        — 1-2 предложения на русском, почему подходит кандидату
  red_flags        — стоп-факторы строкой или null
  location         — локация из вакансии или "Remote"
"""

_EMAIL_EXTRACTION_PROMPT = """\
Ты — парсер вакансий из email-рассылок.
Верни ТОЛЬКО валидный JSON-объект вида {"jobs": [...]}, без markdown.
Каждый элемент массива:
{"title": "...", "company": "...", "location": "...", "url": "...", "description": "..."}
Если вакансий нет — верни {"jobs": []}.
"""


# ─────────────────────────────────────────────
# Клиент
# ─────────────────────────────────────────────

def _get_client(provider: dict, api_key: str, slot: str) -> OpenAI | None:
    """Возвращает клиент для конкретной пары «провайдер + ключ»."""
    if slot in _clients:
        return _clients[slot]

    name = provider["name"]

    # Адрес должен быть настоящим URL. Без этой проверки опечатка в .env
    # (например, ключ, вставленный и в BASE_URL) превращалась бы в поток
    # непонятных сетевых ошибок вместо внятного сообщения.
    base_url = (provider.get("base_url") or "").strip()
    if not base_url.startswith(("http://", "https://")):
        logger.warning(
            "[%s] пропущен: base_url не похож на адрес (%r). "
            "Ожидается что-то вида https://api.example.com/v1",
            name, base_url[:40],
        )
        return None

    if not provider.get("models"):
        logger.warning("[%s] пропущен: не задано ни одной модели", name)
        return None

    _clients[slot] = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,   # ретраи 429 бессмысленны при дневной квоте
        timeout=45.0,
    )
    return _clients[slot]


def active_providers() -> list[str]:
    """Провайдеры с заданными ключами и количество ключей — для диагностики."""
    result = []
    for provider in PROVIDERS:
        count = len(_provider_keys(provider))
        if count:
            result.append("%s(%d)" % (provider["name"], count))
    return result


def _is_daily_quota_error(text: str) -> bool:
    """
    Дневная квота исчерпана — ждать внутри прогона бессмысленно.

    Отличается от минутного rate limit: там нужно просто притормозить,
    здесь — переходить к следующему провайдеру.
    """
    lowered = text.lower()
    markers = (
        "free-models-per-day", "per-day", "daily limit", "daily quota",
        "quota exceeded", "insufficient_quota", "insufficient credits",
        "out of credits", "billing",
    )
    return any(m in lowered for m in markers)


def _is_rate_limit_error(text: str) -> bool:
    """Минутный лимит частоты — лечится паузой и повтором."""
    lowered = text.lower()
    return "429" in text or "rate limit" in lowered or "rate_limited" in lowered \
        or "too many requests" in lowered


def _parse_json_response(text: str) -> dict | list | None:
    """Извлекает JSON из ответа модели, обрезая рассуждения и markdown-обёртки."""
    if not text:
        return None
    # Убираем теги <think>...</think> и <thought>...</thought> (reasoning models)
    cleaned = re.sub(r"<(?:think|thought)>[\s\S]*?</(?:think|thought)>", "", text, flags=re.IGNORECASE).strip()
    # Убираем markdown ```json ... ```
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for opening in ("{", "["):
            start = cleaned.find(opening)
            while start >= 0:
                try:
                    value, _ = decoder.raw_decode(cleaned[start:])
                    return value
                except json.JSONDecodeError:
                    start = cleaned.find(opening, start + 1)
    return None


def _try_one_slot(client, provider: dict, slot: str,
                  messages: list[dict], max_tokens: int) -> tuple[bool, object]:
    """
    Пробует все модели одного слота «провайдер + ключ».

    Возвращает (slot_exhausted, result):
      slot_exhausted — у этого ключа кончилась дневная квота, дальше
                       по нему стучаться бессмысленно;
      result         — разобранный JSON или None.
    """
    name = provider["name"]

    for model in provider["models"]:
        request = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if provider.get("supports_reasoning_control"):
            # effort="low" реально ограничивает объём рассуждений;
            # exclude только убирает их из ответа, но бюджет не экономит.
            request["extra_body"] = {"reasoning": {"effort": "low", "exclude": True}}

        for attempt in range(RATE_LIMIT_RETRIES):
            # Превентивная выдержка темпа: не даём себе превысить лимит
            # запросов в минуту, а не ловим 429 постфактум.
            _throttle(provider, slot)

            try:
                response = client.chat.completions.create(**request)
                choice = response.choices[0]
                content = (choice.message.content or "").strip()
                finish_reason = getattr(choice, "finish_reason", "unknown")

                if not content:
                    logger.warning("[%s] %s вернула пустой ответ (finish_reason=%s)",
                                   slot, model, finish_reason)
                    break

                parsed = _parse_json_response(content)
                if parsed is None:
                    logger.warning("[%s] %s вернула непарсящийся ответ (finish_reason=%s): %s",
                                   slot, model, finish_reason,
                                   content[:300].replace("\n", " "))
                    break

                logger.debug("[%s] %s ответила успешно", slot, model)
                return False, parsed

            except Exception as exc:
                error_text = str(exc)

                # Дневная квота: ждать внутри прогона нечего.
                if _is_daily_quota_error(error_text):
                    logger.warning("[%s] дневная квота исчерпана — беру следующий ключ или провайдера", slot)
                    return True, None

                # Минутный лимит: пауза с экспоненциальным ростом и
                # случайной добавкой, чтобы повторы не били в такт.
                if _is_rate_limit_error(error_text):
                    if attempt < RATE_LIMIT_RETRIES - 1:
                        delay = (2 ** attempt) * 2.0 + random.uniform(0, 1.0)
                        logger.info("[%s] 429 от %s — пауза %.1f с (попытка %d из %d)",
                                    slot, model, delay, attempt + 1, RATE_LIMIT_RETRIES)
                        time.sleep(delay)
                        continue
                    logger.warning("[%s] 429 для модели %s — пробую следующую модель",
                                   slot, model)
                    break

                logger.warning("[%s] %s недоступна: %s", slot, model, error_text[:180])
                break

    return False, None


def _call_model_json(messages: list[dict], max_tokens: int) -> dict | list | None:
    """
    Обходит провайдеров, их ключи и модели; возвращает первый валидный JSON.

    Порядок отступления, от узкого к широкому:
      пустой или непарсящийся ответ -> следующая модель;
      429 «слишком часто»           -> пауза и повтор той же модели;
      дневная квота                 -> следующий КЛЮЧ того же провайдера;
      ключи кончились               -> следующий провайдер.
    Прогон останавливается, только когда исчерпано всё.
    """
    attempted = False

    for provider in PROVIDERS:
        name = provider["name"]

        # Ключи перебираются по очереди: у каждого свой дневной лимит,
        # поэтому исчерпание одного не выводит из игры весь провайдер.
        for key_index, api_key in enumerate(_provider_keys(provider)):
            slot = "%s#%d" % (name, key_index)
            if slot in _exhausted_providers:
                continue

            client = _get_client(provider, api_key, slot)
            if client is None:
                break   # конфигурация провайдера кривая — другие ключи не помогут

            attempted = True
            exhausted, result = _try_one_slot(client, provider, slot, messages, max_tokens)

            if result is not None:
                return result
            if exhausted:
                _exhausted_providers.add(slot)

    if not attempted:
        logger.error(
            "Ни один LLM-провайдер не доступен. Заданы ключи: %s",
            ", ".join(active_providers()) or "нет",
        )
    else:
        logger.error("Все доступные провайдеры и ключи не дали валидного JSON")
    return None



# ─────────────────────────────────────────────
# Публичные функции
# ─────────────────────────────────────────────

def evaluate_job_with_llm(job: dict, rejections_context: str = "") -> dict:
    """
    Оценивает вакансию и возвращает структурные признаки.

    Итоговый score считается не здесь, а в scoring.apply_adjustments():
    модель отдаёт только чистое соответствие профилю и признаки,
    арифметика бонусов выполняется в Python.
    """
    title = job.get("title", "")
    description = str(job.get("description", ""))[:MAX_DESCRIPTION_CHARS]

    user_prompt = (
        "Оцени вакансию.\n\n"
        "Название: " + str(title) + "\n"
        "Компания: " + str(job.get("company", "")) + "\n"
        "Локация: " + str(job.get("location", "")) + "\n"
        "Источник: " + str(job.get("source", "")) + "\n"
        "Описание: " + description
    )

    system_content = _EVALUATION_SYSTEM_PROMPT
    if rejections_context and rejections_context.strip():
        system_content = system_content + "\n\n" + rejections_context.strip()

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_prompt},
    ]

    parsed = _call_model_json(messages, max_tokens=MAX_EVALUATION_TOKENS)

    if parsed is None:
        result = _FALLBACK_EVALUATION.copy()
        # "unavailable" — кончились все провайдеры (повторять в этом прогоне
        # нечем). "invalid_response" — провайдеры живы, но ответ не разобран.
        # "unavailable" — исчерпаны все слоты «провайдер + ключ», повторять
        # в этом прогоне нечем. "invalid_response" — слоты живы, но ответ
        # не разобран, и вакансию имеет смысл вернуть в очередь.
        all_gone = True
        for provider in PROVIDERS:
            for key_index, _ in enumerate(_provider_keys(provider)):
                if "%s#%d" % (provider["name"], key_index) not in _exhausted_providers:
                    all_gone = False
                    break
            if not all_gone:
                break
        result["evaluation_status"] = "unavailable" if all_gone else "invalid_response"
        return result

    if not isinstance(parsed, dict):
        result = _FALLBACK_EVALUATION.copy()
        result["evaluation_status"] = "invalid_response"
        return result

    try:
        match_score = int(parsed.get("match_score", 0))
    except (TypeError, ValueError):
        match_score = 0

    return {
        "match_score": max(0, min(100, match_score)),
        "passed_filter": bool(parsed.get("passed_filter", False)),
        "role_family": parsed.get("role_family", "other"),
        "job_language": parsed.get("job_language", "unknown"),
        "company_country": parsed.get("company_country", "Unknown"),
        "async_evidence": parsed.get("async_evidence", "unknown"),
        # Ответ модели фильтруется по белому списку полей: без этих двух
        # строк geo_restriction и work_format молча терялись бы и никакая
        # гео-проверка на стороне LLM не срабатывала.
        "geo_restriction": parsed.get("geo_restriction", "unknown"),
        "work_format": parsed.get("work_format", "unknown"),
        "seniority": parsed.get("seniority", "unknown"),
        "data_analyst_heavy": bool(parsed.get("data_analyst_heavy", False)),
        "priority_category": parsed.get("priority_category", "Other"),
        "why_match": parsed.get("why_match", ""),
        "red_flags": parsed.get("red_flags"),
        "location": parsed.get("location") or job.get("location") or "Remote",
        "evaluation_status": "ok",
    }


def extract_jobs_from_email(email_body: str) -> list[dict]:
    """Извлекает вакансии из текста письма-дайджеста."""
    if not email_body or len(email_body.strip()) < 50:
        return []

    messages = [
        {"role": "system", "content": _EMAIL_EXTRACTION_PROMPT},
        {"role": "user", "content": email_body[:MAX_EMAIL_BODY_CHARS]},
    ]

    parsed = _call_model_json(messages, max_tokens=1200)
    if parsed is None:
        return []

    # Модель может вернуть и {"jobs": [...]}, и голый массив — принимаем оба.
    if isinstance(parsed, dict):
        jobs = parsed.get("jobs")
    elif isinstance(parsed, list):
        jobs = parsed
    else:
        jobs = None

    if not isinstance(jobs, list):
        return []
    return [j for j in jobs if isinstance(j, dict)]


def test_connection() -> bool:
    """Проверяет доступность OpenRouter на первой рабочей модели цепочки."""
    parsed = _call_model_json(
        [{"role": "user", "content": 'Верни ровно такой JSON: {"status": "ok"}'}],
        max_tokens=200,
    )
    if parsed:
        logger.info("OpenRouter работает. Ответ: %s", parsed)
        return True
    logger.error("OpenRouter: все модели недоступны")
    return False
