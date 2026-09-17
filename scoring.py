"""
scoring.py — Детерминированный расчёт итогового score (v4.1).

Зачем отдельный модуль:
    Арифметику «+15 за русскоязычную вакансию, −20 за обязательные звонки»
    нельзя отдавать языковой модели. Модели плохо считают в уме, а пороги
    60 / 75 / 85 стоят близко — ошибка в 15 пунктов перебрасывает вакансию
    через порог. Поэтому LLM возвращает только СТРУКТУРНЫЕ ПРИЗНАКИ
    (язык, страна, семейство роли, формат коммуникации, уровень), а
    арифметику выполняет Python — воспроизводимо и тестируемо без сети.

Публичный интерфейс:
    apply_adjustments(base_score, evaluation, job) -> (score, [пояснения])
"""

from __future__ import annotations

from config import SYNC_FIRST_MARKERS, is_en_async_mode
from geo_filter import detect_russophone
from location_filter import detect_timezone_lock

# Семейства ролей: AI-агенты, автоматизация, внедрение и обучение клиентов
ROLE_FAMILY_BONUS = {
    "agent_builder": 14,
    "automation": 14,
    "implementation": 14,
    "customer_success": 12,
    "onboarding": 12,
    "prompt_engineering": 12,
    "advisory": 10,
    "solutions": 10,
    "engineering": -5,
    "other": 0,
}

MAX_REGIONAL_BONUS = 18

LANGUAGE_BONUS = 8
COUNTRY_BONUS = 10
ASYNC_BONUS = 15
REMOTE_BOARD_BONUS = 8
JUNIOR_BONUS = 10
SYNC_FIRST_PENALTY = -30
DATA_ANALYST_PENALTY = -35
SYNC_HEAVY_PENALTY = -20
SENIOR_ENGINEERING_PENALTY = -15

# Привязка к американскому часовому поясу ("overlap with EST", "PST hours").
# Формально это не блокировка — работать можно, но это прямая
# противоположность асинхронному формату, ради которого идёт поиск.
TIMEZONE_LOCK_PENALTY = -25

# Вакансия, куда кандидата из ЕС не возьмут в принципе. Детерминированный
# фильтр ловит явные формулировки, но часть ограничений видна только по
# смыслу текста — эту часть отмечает LLM полем geo_restriction.
REGION_LOCKED_PENALTY = -45

# Бонусы режима en_async. В нём диаспорные бонусы выключены: они добавляли
# украинским вакансиям +18 против +15 за async у английских, и внутри лимита
# в 35 оценок LLM англоязычная выдача проигрывала систематически.
ENGLISH_ASYNC_LANGUAGE_BONUS = 10
NON_ENGLISH_IN_EN_MODE_PENALTY = -25

# Потолок для слабых совпадений.
WEAK_BASE_THRESHOLD = 45
WEAK_BASE_CEILING = 74   # на пункт ниже порога уведомления


def _norm(value, default: str = "") -> str:
    return str(value or default).strip().lower()


def apply_adjustments(base_score: int, evaluation: dict, job: dict) -> tuple[int, list[str]]:
    """
    Пересчитывает score по структурным признакам от LLM и метаданным источника.

    Args:
        base_score:  «сырая» оценка соответствия профилю от LLM (0-100).
        evaluation:  словарь с признаками job_language, company_country,
                     role_family, async_evidence, seniority.
        job:         исходная вакансия — нужна для гео-бонуса по location и источнику.

    Returns:
        (итоговый score 0-100, список пояснений для лога и Telegram)
    """
    score = int(base_score)
    notes: list[str] = []

    language = _norm(evaluation.get("job_language"), "unknown")
    async_evidence = _norm(evaluation.get("async_evidence"), "unknown")
    role_family = _norm(evaluation.get("role_family"), "other")
    seniority = _norm(evaluation.get("seniority"), "unknown")
    source = _norm(job.get("source"), "").split("_")[0]
    
    title_text = _norm(job.get("title"))
    body_text = _norm(job.get("description"))
    combined_text = title_text + " " + body_text

    # ── Семейство роли (Главное содержательное соответствие) ──
    role_bonus = ROLE_FAMILY_BONUS.get(role_family, 0)
    if role_bonus:
        score += role_bonus
        notes.append("%+d роль: %s" % (role_bonus, role_family))

    # ── Бонус за уровень Junior / Trainee / Entry Level ──
    is_junior = (
        seniority in {"junior", "trainee", "entry", "intern"} or
        any(k in title_text for k in ("junior", "trainee", "entry level", "початківець", "стажер", "intern"))
    )
    if is_junior and role_family != "engineering":
        score += JUNIOR_BONUS
        notes.append("+%d уровень Junior/Trainee" % JUNIOR_BONUS)

    # ── Асинхронный и Remote формат (Главный приоритет для международных позиций) ──
    is_remote_board = source in {"remoteok", "workingnomads", "nodesk", "weworkremotely",
                                 "himalayas", "arbeitnow", "jobicy", "remotive", "upwork",
                                 "remote.co", "jobspresso", "dailyremote", "justremote",
                                 "remoteworkhub"}

    # Здесь раньше стояло принудительное async_evidence = "explicit_async"
    # для всех remote-бордов. Признак подделывался, и дальше по функции
    # проверка has_explicit_async становилась истинной — то есть штраф за
    # обязательные английские созвоны НЕ применялся ни к одной вакансии
    # с remote-борда. Именно так в выдачу попадали роли с ежедневными
    # клиентскими звонками, помеченные как «async подтверждён».
    # Удалённость борда даёт свой отдельный бонус ниже и признак async
    # больше не подменяет.

    if async_evidence in {"explicit_async", "text_first"}:
        score += ASYNC_BONUS
        notes.append("+%d async формат" % ASYNC_BONUS)
    elif is_remote_board:
        score += REMOTE_BOARD_BONUS
        notes.append("+%d remote-first платформа" % REMOTE_BOARD_BONUS)

    # ── Проверка SYNC_FIRST_MARKERS (-30 баллов) ──
    # Формат работы, а не язык: применяется только к международным вакансиям,
    # где async-культура не заявлена явно.
    if language not in {"ru", "uk", "ru/uk", "russian", "ukrainian"}:
        # Проверяем явное исключение асинхронности
        has_explicit_async = (
            async_evidence == "explicit_async" or
            any(phrase in combined_text for phrase in ("async-first", "async first", "written communication", "written-first", "no meetings"))
        )

        has_sync_first_marker = any(marker in combined_text for marker in SYNC_FIRST_MARKERS)
        
        if has_sync_first_marker and not has_explicit_async:
            score += SYNC_FIRST_PENALTY
            notes.append("%d sync-first формат: клиентские митинги" % SYNC_FIRST_PENALTY)
        elif async_evidence == "calls_required" and not has_explicit_async:
            score += SYNC_HEAVY_PENALTY
            notes.append("%d постоянные синхронные звонки" % SYNC_HEAVY_PENALTY)

    # ── Проверка DATA_ANALYST_BLOCKERS (-35 баллов) ──
    # Если в обязательных требованиях 2+ маркера Data Analyst / BI / SQL
    if evaluation.get("data_analyst_heavy"):
        score += DATA_ANALYST_PENALTY
        notes.append("%d стек Data Analyst/BI в обязательных требованиях" % DATA_ANALYST_PENALTY)

    # ── Гео-ограничение и часовой пояс ──
    # Два независимых источника сигнала: regex по тексту и признак от LLM.
    # Штраф начисляется один раз, даже если сработали оба.
    geo_restriction = _norm(evaluation.get("geo_restriction"), "unknown")

    if geo_restriction == "region_locked":
        score += REGION_LOCKED_PENALTY
        notes.append("%d вакансия закрыта для ЕС" % REGION_LOCKED_PENALTY)
    elif geo_restriction == "us_timezone" or detect_timezone_lock(job):
        score += TIMEZONE_LOCK_PENALTY
        notes.append("%d привязка к часовому поясу США" % TIMEZONE_LOCK_PENALTY)

    # ── Региональные бонусы ──
    # В режиме en_async они выключены целиком. Диаспорный бонус давал
    # украинской вакансии +18 к тому же базовому баллу, что и английской, и
    # английская выдача проигрывала конкуренцию за 35 слотов LLM всегда.
    is_russophone_language = language in {"ru", "uk", "ru/uk", "russian", "ukrainian"}

    if is_en_async_mode():
        if is_russophone_language:
            score += NON_ENGLISH_IN_EN_MODE_PENALTY
            notes.append("%d не английская вакансия (режим en_async)"
                         % NON_ENGLISH_IN_EN_MODE_PENALTY)
        elif language == "en":
            score += ENGLISH_ASYNC_LANGUAGE_BONUS
            notes.append("+%d английская вакансия" % ENGLISH_ASYNC_LANGUAGE_BONUS)
    else:
        regional = 0
        country = detect_russophone(job) or evaluation.get("company_country_detected")
        if country:
            regional += COUNTRY_BONUS
            notes.append("+%d диаспора (%s)" % (COUNTRY_BONUS, country))

        if is_russophone_language:
            regional += LANGUAGE_BONUS
            notes.append("+%d язык" % LANGUAGE_BONUS)

        if regional > MAX_REGIONAL_BONUS:
            regional = MAX_REGIONAL_BONUS
        score += regional

    # ── Уровень Seniority (Штраф за чистый Senior dev) ──
    if seniority in {"staff", "principal"} or (
        seniority == "senior" and role_family == "engineering"
    ):
        score += SENIOR_ENGINEERING_PENALTY
        notes.append("%d уровень %s" % (SENIOR_ENGINEERING_PENALTY, seniority))

    score = max(0, min(100, score))

    # Бонусы не должны превращать изначально нерелевантную вакансию в уведомление
    if base_score <= WEAK_BASE_THRESHOLD and score > WEAK_BASE_CEILING:
        notes.append("потолок %d: базовое совпадение слабое (%d)" % (WEAK_BASE_CEILING, base_score))
        score = WEAK_BASE_CEILING

    return score, notes


def describe_language(evaluation: dict) -> str:
    """Человекочитаемая метка языка для карточки Telegram."""
    language = _norm(evaluation.get("job_language"), "unknown")
    async_evidence = _norm(evaluation.get("async_evidence"), "unknown")
    mapping = {
        "uk": "Українська",
        "ru": "Русский",
        "en": "English",
    }
    label = mapping.get(language, "не определён")
    if language == "en":
        if async_evidence == "explicit_async":
            label += " · async подтверждён"
        elif async_evidence == "text_first":
            label += " · переписка"
        elif async_evidence == "calls_required":
            label += " · ⚠️ звонки"
        else:
            label += " · async не подтверждён"
    return label
