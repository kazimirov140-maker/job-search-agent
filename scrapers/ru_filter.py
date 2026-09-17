"""
scrapers/ru_filter.py — Многосигнальный фильтр российских компаний.

Функция is_russian_company() проверяет вакансию по 6 категориям сигналов:
  1. Домен URL вакансии (.ru, hh.ru, superjob.ru и т.д.)
  2. Скрытые .ru-ссылки и email в тексте описания (ГЛАВНЫЙ СИГНАЛ)
  3. Юридические формы РФ (ООО, ПАО, АО, ЗАО...)
  4. Официальные идентификаторы (ИНН, ОГРН, Сколково...)
  5. Города РФ в связке с маркерами локации
  6. Текстовые маркеры принадлежности к России + валюта

Возвращает (True, причина) при первом сработавшем сигнале.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 1. RU-ДОМЕНЫ В URL ВАКАНСИИ
# ─────────────────────────────────────────────────────────────────────────────

_RU_JOB_DOMAINS = re.compile(
    r"(^|[./])(hh|superjob|zarplata|trudvsem|rabota|careerist|avito\.ru|job|work)\.ru([/?#]|$)",
    re.IGNORECASE,
)

def _check_job_url(url: str) -> tuple[bool, str]:
    """Проверяет URL вакансии на .ru-домены."""
    url_lower = url.lower()
    if ".ru/" in url_lower or url_lower.endswith(".ru"):
        return True, f"URL вакансии содержит .ru: {url[:80]}"
    if _RU_JOB_DOMAINS.search(url_lower):
        return True, f"URL вакансии — российская доска вакансий: {url[:80]}"
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. СКРЫТЫЕ .RU-ССЫЛКИ И EMAIL В ТЕКСТЕ (КЛЮЧЕВОЙ СИГНАЛ)
# ─────────────────────────────────────────────────────────────────────────────

# Ложные срабатывания: слова, которые оканчиваются на "ru" но не являются .ru-доменами
_FALSE_POSITIVE_RU = {
    "guru", "bureau", "menu", "crew", "true", "configure", "structure",
    "procedure", "signature", "hardware", "software", "failure", "feature",
    "infrastructure", "architecture", "venture", "secure", "culture",
    "nature", "future", "figure", "picture", "capture", "rupture",
    "azure", "closure", "measure", "treasure", "pleasure",
}

# HTTP(S) ссылки с .ru
_PATTERN_HTTP_RU = re.compile(
    r'https?://[^\s<>"\']*\.ru[^\s<>"\']*',
    re.IGNORECASE,
)

# Email с .ru доменом
_PATTERN_EMAIL_RU = re.compile(
    r'\b[\w.+%-]+@[\w.-]+\.ru\b',
    re.IGNORECASE,
)

# Голые домены типа company.ru или www.startup.ru
_PATTERN_BARE_RU = re.compile(
    r'\b([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)\.(ru)\b',
    re.IGNORECASE,
)


def _check_embedded_ru_links(text: str) -> tuple[bool, str]:
    """Сканирует текст описания на скрытые .ru-ссылки и email."""
    if not text:
        return False, ""

    # Проверка HTTP-ссылок
    http_match = _PATTERN_HTTP_RU.search(text)
    if http_match:
        return True, f"В описании найдена .ru-ссылка: {http_match.group()[:60]}"

    # Проверка email с .ru
    email_match = _PATTERN_EMAIL_RU.search(text)
    if email_match:
        return True, f"В описании найден .ru-email: {email_match.group()[:60]}"

    # Проверка голых доменов (с защитой от ложных срабатываний)
    for match in _PATTERN_BARE_RU.finditer(text):
        domain_word = match.group(1).lower()
        if domain_word not in _FALSE_POSITIVE_RU:
            return True, f"В описании найден .ru-домен: {match.group()[:60]}"

    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 3. ЮРИДИЧЕСКИЕ ФОРМЫ РФ
# ─────────────────────────────────────────────────────────────────────────────

_PATTERN_LEGAL_FORMS = re.compile(
    r'\b(ООО|ОАО|ПАО|ЗАО|АНО|НКО|ФГУП|МУП|ГУП|ИП|КФХ|АО)\b',
    re.UNICODE,
)


def _check_legal_forms(text: str) -> tuple[bool, str]:
    """Проверяет наличие российских юридических форм в тексте."""
    match = _PATTERN_LEGAL_FORMS.search(text)
    if match:
        return True, f"Юридическая форма РФ: «{match.group()}»"
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. ИДЕНТИФИКАТОРЫ И ГОСПРОГРАММЫ РФ
# ─────────────────────────────────────────────────────────────────────────────

_PATTERN_RU_IDS = re.compile(
    r'\b(ИНН|ОГРН|КПП|ОКПО|Сколково|Роснано|Ростех|Роскосмос|Росатом|Росгосстрах)\b',
    re.IGNORECASE | re.UNICODE,
)


def _check_ru_identifiers(text: str) -> tuple[bool, str]:
    """Проверяет наличие российских идентификаторов и госпрограмм."""
    match = _PATTERN_RU_IDS.search(text)
    if match:
        return True, f"Российский идентификатор/госпрограмма: «{match.group()}»"
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 5. ГОРОДА РФ В КОНТЕКСТЕ ЛОКАЦИИ
# ─────────────────────────────────────────────────────────────────────────────

# Паттерн: маркер локации + город РФ
_LOCATION_MARKERS = r'(?:г\.?\s*|город\s+|офис\s+(?:в|во)\s+|работа\s+(?:в|во)\s+|location[:\s]+|office[:\s]+)'
_RU_CITIES = (
    r'Москв[аеу]?|Санкт.?Петербург[еа]?|Питер[еа]?|'
    r'Новосибирск[еа]?|Екатеринбург[еа]?|Казан[ьи]?|'
    r'Нижн\w+ Новгород[еа]?|Самар[аеу]?|Уф[аеу]?|'
    r'Омск[еа]?|Красноярск[еа]?|Воронеж[еа]?|Пермь?|'
    r'Волгоград[еа]?|Краснодар[еа]?|Ростов.на.Дону?|'
    r'Тюмен[ьи]?|Хабаровск[еа]?|Владивосток[еа]?'
)

_PATTERN_RU_CITY = re.compile(
    rf'(?:{_LOCATION_MARKERS})(?:{_RU_CITIES})',
    re.IGNORECASE | re.UNICODE,
)


def _check_ru_cities(text: str) -> tuple[bool, str]:
    """Проверяет наличие городов РФ в контексте локации офиса."""
    match = _PATTERN_RU_CITY.search(text)
    if match:
        return True, f"Офис/работа в городе РФ: «{match.group()[:40]}»"
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 6. ТЕКСТОВЫЕ МАРКЕРЫ + ВАЛЮТА
# ─────────────────────────────────────────────────────────────────────────────

_PATTERN_RU_TEXT = re.compile(
    r'('
    r'российская\s+компания|'
    r'работа\s+в\s+Росси[ия]|'
    r'резидент\s+РФ|'
    r'резидент\s+России|'
    r'зарегистрирован\w*\s+в\s+РФ|'
    r'зарегистрирован\w*\s+в\s+России|'
    r'головной\s+офис\s+в\s+Росси[ия]|'
    r'российский\s+стартап|'
    r'компания\s+из\s+Росси[ия]'
    r')',
    re.IGNORECASE | re.UNICODE,
)

_PATTERN_RU_CURRENCY = re.compile(
    r'('
    r'₽|'
    r'\d[\d\s]*\s*руб(?:\.|\b)|'
    r'тыс\.\s*руб|'
    r'рублей\s+в\s+месяц|'
    r'\d[\d\s]*\s*р\./мес|'
    r'\bRUB\b|'
    r'\bRUR\b|'
    r'\brubles\b'
    r')',
    re.IGNORECASE | re.UNICODE,
)


def _check_ru_text_markers(text: str) -> tuple[bool, str]:
    """Проверяет явные текстовые маркеры принадлежности к России."""
    match = _PATTERN_RU_TEXT.search(text)
    if match:
        return True, f"Текстовый маркер РФ: «{match.group()[:60]}»"

    currency_match = _PATTERN_RU_CURRENCY.search(text)
    if currency_match:
        return True, f"Зарплата в рублях: «{currency_match.group()[:30]}»"

    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# ПУБЛИЧНЫЙ API
# ─────────────────────────────────────────────────────────────────────────────

def is_russian_company(job: dict) -> tuple[bool, str]:
    """
    Определяет, является ли вакансия российской по множеству сигналов.

    Проверяет 6 категорий сигналов в порядке убывания надёжности:
      1. Домен URL вакансии
      2. Скрытые .ru-ссылки и email в description (ключевой сигнал)
      3. Юридические формы РФ в тексте
      4. ИНН/ОГРН и госпрограммы РФ
      5. Города РФ в контексте локации офиса
      6. Явные текстовые маркеры + рублёвая зарплата

    Args:
        job: Словарь вакансии с полями url, title, company, description.

    Returns:
        (True, причина) — если вакансия определена как российская.
        (False, "")     — если вакансия прошла все проверки.
    """
    url         = job.get("url", "")
    title       = job.get("title", "")
    company     = job.get("company", "")
    description = job.get("description", "")

    # Полный текст для поиска (кроме URL — он проверяется отдельно)
    full_text = f"{title} {company} {description}"

    # ── Проверки по порядку ──────────────────────────────────────────────────
    checks = [
        _check_job_url(url),
        _check_embedded_ru_links(full_text),   # ⭐ ГЛАВНЫЙ: .ru в тексте описания
        _check_legal_forms(full_text),
        _check_ru_identifiers(full_text),
        _check_ru_cities(full_text),
        _check_ru_text_markers(full_text),
    ]

    for is_ru, reason in checks:
        if is_ru:
            logger.debug("RU-фильтр сработал: %s | %s", job.get("job_id", "?"), reason)
            return True, reason

    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Ручное тестирование
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    test_cases = [
        # Должны быть отфильтрованы
        {"job_id": "t1", "url": "https://hh.ru/vacancy/123", "title": "Python Dev", "company": "Romashka", "description": ""},
        {"job_id": "t2", "url": "https://remotive.com/job/999", "title": "AI Engineer", "company": "Tech", "description": "Visit us at https://company.ru/careers for details"},
        {"job_id": "t3", "url": "https://linkedin.com/jobs/456", "title": "ML Engineer", "company": "ООО Стартап", "description": "Remote position"},
        {"job_id": "t4", "url": "https://remotive.com/job/789", "title": "Prompt Engineer", "company": "AI Labs", "description": "Apply at hr@startup.ru or visit startup.ru"},
        {"job_id": "t5", "url": "https://weworkremotely.com/job/1", "title": "Data Scientist", "company": "Corp", "description": "Зарплата 150 000 рублей в месяц"},
        {"job_id": "t6", "url": "https://remotive.com/job/2", "title": "DevOps", "company": "Skolkovo Resident", "description": "Резидент Сколково, офис в Москве"},
        # НЕ должны быть отфильтрованы
        {"job_id": "t7", "url": "https://remoteok.com/job/777", "title": "AI Automation Specialist", "company": "Acme Corp", "description": "Fully async team, written communication only. Apply at careers@acme.com"},
        {"job_id": "t8", "url": "https://himalayas.app/jobs/888", "title": "Logistics AI", "company": "FreightTech GmbH", "description": "Remote-first, Europe/US timezone. Salary: $80k-120k"},
    ]

    print("\n" + "=" * 60)
    print("RU-FILTER TEST RESULTS")
    print("=" * 60)
    for tc in test_cases:
        result, reason = is_russian_company(tc)
        status = "[BLOCKED]" if result else "[PASSED ]"
        print(f"{status} [{tc['job_id']}] {tc['title']} @ {tc['company']}")
        if result:
            print(f"         Причина: {reason}")
    print("=" * 60)
