"""
geo_filter.py — Разделение ЯЗЫКА и ЮРИСДИКЦИИ (v4.1).

Ключевое правило проекта:
    Русский язык — это ПЛЮС. Блокируется только Россия как юрисдикция.

Русскоязычное сообщество шире России: Украина, Казахстан, Армения, Грузия,
Узбекистан, Кыргызстан, Азербайджан, Молдова, Прибалтика, Кипр, Израиль,
Сербия, Черногория, ОАЭ. Все они — целевой рынок и получают бонус к score.

Почему здесь не используется поиск подстроки:
    Подстрока по всему тексту вакансии даёт катастрофические ложные
    срабатывания на кириллице —
        "интерфейс" содержит "рф",  "инновации" содержит "инн" (ИНН).
    Поэтому везде границы слова с поддержкой Unicode, а «сильные»
    юридические маркеры отделены от простого упоминания страны в тексте.

Публичный интерфейс:
    detect_jurisdiction(job, source_url) -> (blocked: bool, reason: str)
    detect_russophone(job)               -> str | None
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse


def _compile(terms: list[str]) -> re.Pattern:
    """
    Собирает один regex из списка фрагментов.

    Каждый фрагмент оборачивается в границы слова через lookaround, а не
    через \\b: так корректно обрабатывается кириллица и термины, которые
    начинаются не с буквы. Внутри фрагмента разрешён \\w* для словоформ —
    "росси\\w*" ловит Россия / России / Российская / россиян.
    """
    body = "|".join("(?<!\\w)(?:" + t + ")(?!\\w)" for t in terms)
    return re.compile(body, re.IGNORECASE | re.UNICODE)


# ─────────────────────────────────────────────
# Россия — географические маркеры.
# Срабатывают в структурированных полях (location, company, title) и в
# описании только рядом с локационным контекстом. Простое упоминание
# «наши клиенты в России» юрисдикцией не является и НЕ блокирует.
# ─────────────────────────────────────────────

_RU_GEO_TERMS = [
    r"росси\w*", r"рф", r"russia", r"russian\s+federation",
    r"москв\w*", r"московск\w*",
    r"санкт[-\s]?петербург\w*", r"петербург\w*", r"спб",
    r"новосибирск\w*", r"екатеринбург\w*", r"казан[ьи]", r"краснодар\w*",
    r"нижн\w+\s+новгород\w*", r"ростов[-\s]на[-\s]дону", r"самар[аеы]",
    r"челябинск\w*", r"перм[ьи]", r"воронеж\w*", r"волгоград\w*",
    r"сочи", r"владивосток\w*", r"красноярск\w*", r"тюмен[ьи]", r"омск\w*",
    r"иннополис\w*", r"сколков\w*", r"калининград\w*",
    r"moscow", r"saint[-\s]?petersburg", r"st\.?\s?petersburg",
    r"novosibirsk", r"yekaterinburg", r"ekaterinburg", r"kazan",
    r"nizhny\s+novgorod", r"krasnodar", r"rostov[-\s]on[-\s]don",
    r"vladivostok", r"chelyabinsk", r"krasnoyarsk", r"innopolis", r"skolkovo",
]

# «Сильные» маркеры: блокируют ГДЕ УГОДНО, включая описание.
# У нероссийского работодателя они случайно не появятся.
_RU_STRONG_TERMS = [
    r"тк\s?рф", r"оформление\s+по\s+тк", r"трудов\w+\s+кодекс\w*\s+рф",
    r"гражданств\w*\s+рф", r"только\s+граждан\w*\s+рф",
    r"инн", r"снилс", r"огрн",
    r"₽", r"руб", r"рубл\w*", r"rub",
    r"ооо", r"зао", r"оао", r"пао",
]

# Беларусь — отдельный переключатель BLOCK_BELARUS.
# Пользователь сказал «все русскоязычные, кроме России». РБ оставлена
# заблокированной по умолчанию из-за санкций и платёжных ограничений;
# снимается одной строкой в .env: BLOCK_BELARUS=false
_BY_TERMS = [
    r"беларус\w*", r"белорусс\w*", r"belarus\w*",
    r"минск\w*", r"minsk", r"гомел\w*", r"брест\w*",
    r"витебск\w*", r"могил[ёе]в\w*", r"гродн\w*",
    r"гражданств\w*\s+рб", r"byn",
]

_RU_GEO = _compile(_RU_GEO_TERMS)
_RU_STRONG = _compile(_RU_STRONG_TERMS)
_BY = _compile(_BY_TERMS)

# Локационный контекст внутри описания: многие источники не заполняют поле
# location, и единственный намёк лежит в тексте. Ищем не голое название
# города, а «офис в Москве» / «based in Moscow» / «релокация в Москву».
_LOCATION_CONTEXT = re.compile(
    r"(?:локац\w*|город|офис|office|based\s+in|located\s+in|"
    r"relocat\w*|релокац\w*|переезд|проживани\w*|место\s+работы|"
    r"headquarter\w*|work\s+location)[^.\n]{0,50}",
    re.IGNORECASE | re.UNICODE,
)

# ".рф" в punycode — так его видит urlparse
_BLOCKED_TLDS = (".ru", ".su", ".xn--p1ai", ".by")
_BLOCKED_HOSTS = {
    "hh.ru", "superjob.ru", "zarplata.ru", "rabota.ru",
    "getmatch.ru", "habr.com", "career.habr.com", "trud.com",
    "rabota.by", "praca.by",
}


def block_belarus() -> bool:
    """Читается на каждом вызове, чтобы флаг можно было менять без перезапуска."""
    return os.getenv("BLOCK_BELARUS", "true").strip().lower() not in {"0", "false", "no", "off"}


def _host_of(url: str) -> str:
    """Возвращает hostname в нижнем регистре, без www."""
    if not url:
        return ""
    candidate = url if "//" in url else "//" + url
    try:
        host = (urlparse(candidate).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _domain_blocked(url: str) -> str:
    """
    Проверяет домен по разобранному hostname, а не по подстроке в URL.

    Подстрока ".ru" ловила любой адрес вида .../ruby-on-rails или app.run.com —
    именно так прежняя версия резала нормальные вакансии.
    """
    host = _host_of(url)
    if not host:
        return ""
    if host in _BLOCKED_HOSTS:
        return "домен " + host
    for tld in _BLOCKED_TLDS:
        if tld == ".by" and not block_belarus():
            continue
        if host == tld.lstrip(".") or host.endswith(tld):
            return "домен " + host
    return ""


# ─────────────────────────────────────────────
# Русскоязычная диаспора — бонус, не блокировка
# ─────────────────────────────────────────────

_RUSSOPHONE = {
    "Украина":     [r"україн\w*", r"украин\w*", r"ukrain\w*", r"київ\w*", r"киев\w*",
                    r"kyiv", r"kiev", r"львів\w*", r"львов\w*", r"lviv", r"одес\w*",
                    r"odessa", r"харків\w*", r"харьков\w*", r"kharkiv", r"дніпр\w*",
                    r"днепр\w*", r"dnipro"],
    "Казахстан":   [r"казахстан\w*", r"kazakhstan", r"алмат\w*", r"almaty",
                    r"астан[аеы]", r"astana", r"шымкент\w*"],
    "Армения":     [r"армени\w*", r"armenia\w*", r"ереван\w*", r"yerevan"],
    "Грузия":      [r"грузи\w*", r"georgia", r"тбилис\w*", r"tbilisi", r"батуми", r"batumi"],
    "Узбекистан":  [r"узбекистан\w*", r"uzbekistan", r"ташкент\w*", r"tashkent"],
    "Кыргызстан":  [r"кыргызстан\w*", r"киргизи\w*", r"kyrgyzstan", r"бишкек\w*", r"bishkek"],
    "Азербайджан": [r"азербайджан\w*", r"azerbaijan", r"баку", r"baku"],
    "Молдова":     [r"молдов\w*", r"moldova", r"кишин[ёе]в\w*", r"chisinau"],
    "Литва":       [r"литв\w*", r"lithuania\w*", r"вильнюс\w*", r"vilnius", r"каунас\w*"],
    "Латвия":      [r"латви\w*", r"latvia\w*", r"риг[аиуе]", r"riga"],
    "Эстония":     [r"эстони\w*", r"estonia\w*", r"таллин\w*", r"tallinn"],
    "Кипр":        [r"кипр\w*", r"cyprus", r"лимассол\w*", r"limassol", r"никоси\w*", r"nicosia"],
    "Израиль":     [r"израил\w*", r"israel\w*", r"тель[-\s]?авив\w*", r"tel[-\s]?aviv", r"haifa"],
    "Сербия":      [r"серби\w*", r"serbia\w*", r"белград\w*", r"belgrade"],
    "Черногория":  [r"черногори\w*", r"montenegro", r"будв\w*", r"budva", r"подгориц\w*"],
    "ОАЭ":         [r"оаэ", r"эмират\w*", r"uae", r"dubai", r"дуба[йя]", r"abu[-\s]?dhabi"],
    "Польша":      [r"польш\w*", r"poland", r"варшав\w*", r"warsaw", r"краков\w*", r"krakow",
                    r"вроцлав\w*", r"wroclaw"],
}

_RUSSOPHONE_COMPILED = {c: _compile(t) for c, t in _RUSSOPHONE.items()}


def _fields(job: dict) -> tuple[str, str, str, str]:
    """Возвращает (location, company, title, description) в нижнем регистре."""
    return (
        str(job.get("location") or "").lower(),
        str(job.get("company") or "").lower(),
        str(job.get("title") or "").lower(),
        str(job.get("description") or "").lower(),
    )


def detect_jurisdiction(job: dict, source_url: str = "") -> tuple[bool, str]:
    """
    Определяет, относится ли вакансия к заблокированной юрисдикции.

    Возвращает (blocked, reason); reason пустой, если вакансия проходит.
    Слои идут от самого надёжного сигнала к самому слабому:
      1. Домен вакансии.
      2. Сильные юридические и денежные маркеры — где угодно в тексте.
      3. Гео-термины в структурированных полях location / company / title.
      4. Гео-термины в описании, но только в локационном контексте.
      5. Беларусь — отдельным флагом.
    """
    url = source_url or str(job.get("url") or "")

    domain_reason = _domain_blocked(url)
    if domain_reason:
        return True, "РФ/РБ: " + domain_reason

    location, company, title, description = _fields(job)
    everything = " ".join((title, company, location, description))

    strong = _RU_STRONG.search(everything)
    if strong:
        return True, "РФ: маркер «" + strong.group(0) + "»"

    for field_name, value in (("локация", location), ("компания", company), ("название", title)):
        hit = _RU_GEO.search(value)
        if hit:
            return True, "РФ: " + field_name + " «" + hit.group(0) + "»"

    for fragment in _LOCATION_CONTEXT.findall(description):
        hit = _RU_GEO.search(fragment)
        if hit:
            return True, "РФ: локация в описании «" + hit.group(0) + "»"

    if block_belarus():
        for field_name, value in (("локация", location), ("компания", company), ("название", title)):
            hit = _BY.search(value)
            if hit:
                return True, "РБ: " + field_name + " «" + hit.group(0) + "»"

    return False, ""


def detect_russophone(job: dict) -> str | None:
    """
    Возвращает название русскоязычной страны, если вакансия с ней связана.

    Это БОНУС к score, а не фильтр. Описание намеренно исключено, чтобы
    «наши клиенты в Казахстане» не считалось локацией работодателя.
    """
    location, company, title, _ = _fields(job)
    haystack = " ".join((location, company, title))
    for country, pattern in _RUSSOPHONE_COMPILED.items():
        if pattern.search(haystack):
            return country
    return None
