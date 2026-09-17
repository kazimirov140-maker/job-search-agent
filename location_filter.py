"""
location_filter.py — Формат работы и географические ограничения (v4.3).

Зачем отдельный модуль:
    geo_filter.py отвечает на вопрос «это юрисдикция РФ/РБ?».
    Здесь отвечаем на два других вопроса, из-за которых в Telegram
    приходил мусор:

      1. Это вообще удалёнка? Раньше проверялись только слова "onsite" и
         "hybrid" в title и location. Вакансия без единого упоминания
         формата считалась удалённой и проходила дальше — так в выдачу
         попадали офисные роли из Greenhouse, Lever и email-дайджестов.

      2. Открыта ли удалёнка для человека из домашнего региона? Remotive отдаёт
         поле candidate_required_location = "USA Only", Jobicy — jobGeo =
         "USA". Оба попадали в job["location"], но нигде не проверялись.
         Отсюда «удалёнка, но только для США».

Публичный интерфейс:
    detect_remote_status(job)     -> "remote" | "hybrid" | "onsite" | "unknown"
    detect_geo_restriction(job)   -> (blocked: bool, reason: str)
    detect_timezone_lock(job)     -> str  (пустая строка, если привязки нет)

Домашний регион оператора по умолчанию — Испания (ЕС, часовой пояс CET).
Настраивается списками HOME_* ниже.
Поэтому «worldwide / anywhere / Europe / EMEA / EU» — подходит,
а «US only / Canada only / APAC / LATAM» — нет.
"""

from __future__ import annotations

import re


def _compile(terms: list[str]) -> re.Pattern:
    """Собирает regex с границами слова, корректными для кириллицы."""
    body = "|".join("(?<!\\w)(?:" + t + ")(?!\\w)" for t in terms)
    return re.compile(body, re.IGNORECASE | re.UNICODE)


# ─────────────────────────────────────────────
# 1. Формат работы
# ─────────────────────────────────────────────

_REMOTE_TERMS = [
    r"remote(?:ly)?", r"fully[-\s]remote", r"remote[-\s]first",
    r"work\s+from\s+(?:home|anywhere)", r"wfh", r"home[-\s]office",
    r"telecommut\w*", r"distributed\s+team", r"anywhere", r"worldwide",
    r"удал[её]нн?\w*", r"удал[её]нк\w*", r"дистанцион\w*",
    r"віддален\w*", r"дистанційн\w*",
]

_HYBRID_TERMS = [r"hybrid", r"гибридн\w*", r"гібридн\w*"]

_ONSITE_TERMS = [
    r"on[-\s]?site", r"onsite", r"in[-\s]?office", r"office[-\s]based",
    r"in[-\s]person", r"на\s+месте", r"в\s+офисе", r"в\s+офісі",
    r"relocation\s+(?:is\s+)?required", r"переезд\s+обязателен",
]

_REMOTE = _compile(_REMOTE_TERMS)
_HYBRID = _compile(_HYBRID_TERMS)
_ONSITE = _compile(_ONSITE_TERMS)


def _fields(job: dict) -> tuple[str, str, str]:
    """Возвращает (title, location, description) в нижнем регистре."""
    return (
        str(job.get("title") or "").lower(),
        str(job.get("location") or "").lower(),
        str(job.get("description") or "").lower(),
    )


def detect_remote_status(job: dict, strict: bool = False) -> str:
    """
    Определяет формат работы по трём полям.

    Порядок важен: структурированные поля (title, location) надёжнее
    описания. Единственное слово "hybrid" в конце длинного описания не
    должно перебивать "Remote" в заголовке — иначе теряются нормальные
    удалённые вакансии, где гибридный офис упомянут для местных.

    strict=True — для источников, где удалёнка НЕ гарантирована бордом
    (Greenhouse, Lever, email-дайджесты, Djinni, DOU). Там заполненная
    локация вида "Berlin, Germany" означает офис, даже если слово "remote"
    встречается где-то в теле объявления: у корпоративных описаний почти
    всегда есть абзац про гибкость и remote-friendly культуру.
    """
    title, location, description = _fields(job)
    structured = title + " " + location

    if _REMOTE.search(structured):
        return "remote"
    if _ONSITE.search(structured):
        return "onsite"
    if _HYBRID.search(structured):
        return "hybrid"

    if strict and location.strip():
        return "onsite"

    if _REMOTE.search(description):
        return "remote"
    if _HYBRID.search(description):
        return "hybrid"
    if _ONSITE.search(description):
        return "onsite"

    return "unknown"


# ─────────────────────────────────────────────
# 2. Географическое ограничение удалёнки
# ─────────────────────────────────────────────

# Регионы, из которых оператор работать МОЖЕТ. Если такой маркер есть,
# вакансия проходит независимо от остального — «Europe or USA» подходит.
_CANDIDATE_OK_TERMS = [
    r"worldwide", r"anywhere", r"any\s+country", r"global(?:ly)?",
    r"international", r"any\s+time\s?zone", r"location\s+independent",
    r"europe(?:an)?", r"eu", r"emea", r"eea", r"cet", r"cest",
    r"spain", r"españa", r"espana", r"madrid", r"barcelona", r"valencia",
    r"испани\w*", r"portugal", r"iberia",
    r"uk", r"united\s+kingdom", r"ireland", r"germany", r"france", r"italy",
    r"netherlands", r"poland", r"romania", r"bulgaria", r"czech\w*",
    r"estonia", r"latvia", r"lithuania", r"cyprus", r"greece", r"sweden",
    r"norway", r"denmark", r"finland", r"switzerland", r"austria",
    r"belgium", r"hungary", r"serbia", r"croatia", r"slovakia", r"slovenia",
    r"armenia", r"kazakhstan", r"turkey", r"israel", r"uae", r"dubai",
    r"australia", r"new\s+zealand", r"canada", r"asia(?:[-\s]pacific)?", r"apac",
    r"latam", r"latin\s+america", r"south\s+america",
]

# Регионы, которые оператора ИСКЛЮЧАЮТ. Проверяются только после того,
# как в локации не нашлось ни одного подходящего маркера.
_CANDIDATE_BAD_TERMS = [
    r"u\.?s\.?a?", r"usa", r"united\s+states", r"north\s+america",
    r"mexico", r"india", r"pakistan", r"bangladesh",
    r"philippines", r"indonesia", r"vietnam", r"thailand", r"malaysia",
    r"singapore", r"japan", r"china", r"hong\s+kong", r"taiwan", r"korea",
    r"nigeria", r"kenya", r"south\s+africa", r"egypt",
    # Крупные города США и Канады: в поле location они встречаются без
    # названия страны ("San Francisco", "Austin"), и по списку стран
    # такая вакансия не ловится.
    r"san\s+francisco", r"new\s+york", r"los\s+angeles", r"chicago",
    r"boston", r"seattle", r"austin", r"denver", r"atlanta", r"dallas",
    r"houston", r"miami", r"san\s+diego", r"san\s+jose", r"philadelphia",
    r"phoenix", r"minneapolis", r"portland", r"nashville", r"charlotte",
    r"toronto", r"vancouver", r"montreal", r"ottawa", r"calgary",
    # Штаты США полным словом: "Remote - Pennsylvania" по коду штата не
    # ловится, а страна в таких локациях не указывается.
    r"alabama", r"alaska", r"arizona", r"arkansas", r"california",
    r"colorado", r"connecticut", r"delaware", r"florida",
    r"illinois", r"indiana", r"iowa", r"kansas", r"kentucky", r"louisiana",
    r"maryland", r"massachusetts", r"michigan", r"minnesota", r"mississippi",
    r"missouri", r"montana", r"nebraska", r"nevada", r"new\s+hampshire",
    r"new\s+jersey", r"new\s+mexico", r"north\s+carolina", r"north\s+dakota",
    r"ohio", r"oklahoma", r"oregon", r"pennsylvania", r"rhode\s+island",
    r"south\s+carolina", r"south\s+dakota", r"tennessee", r"texas", r"utah",
    r"vermont", r"virginia", r"washington", r"west\s+virginia", r"wisconsin",
    r"wyoming",
]

_OK_REGION = _compile(_CANDIDATE_OK_TERMS)
_BAD_REGION = _compile(_CANDIDATE_BAD_TERMS)

# Формат "City, ST" — американский и канадский адрес. Названия штатов в
# списке стран не поймать: локация "Seattle, WA" не содержит ни "usa", ни
# "united states", и такие вакансии проходили гео-фильтр насквозь.
_US_STATE_LOC = re.compile(
    r"(?:,|^|\s)\s*(?:"
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|MA|MD|ME|MI|MN|"
    r"MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|"
    r"WA|WI|WV|WY|DC"
    r"|ON|BC|QC|AB|MB|SK|NS|NB"
    r")\s*(?:,|$)",
    re.UNICODE,
)

# Явные ограничения в ТЕКСТЕ описания. Формулировки намеренно длинные:
# короткое "us" в описании ловит десятки ложных срабатываний
# ("contact us", "join us", "us-based clients of our partner").
_RESTRICTION_PHRASES = [
    r"\b(?:u\.?s\.?a?|usa|united\s+states|canada|australia|india|latam|"
    r"latin\s+america|brazil|philippines|apac|singapore)[\s\-]*only\b",

    r"\bonly\s+(?:open\s+to\s+)?(?:candidates?|applicants?|residents?)?\s*"
    r"(?:in|from|located\s+in|residing\s+in|based\s+in)\s+(?:the\s+)?"
    r"(?:u\.?s\.?a?|usa|united\s+states|canada|australia|india|latam|"
    r"latin\s+america|brazil|philippines|apac)\b",

    r"\bmust\s+(?:be\s+)?(?:currently\s+)?(?:located|based|reside|residing|"
    r"live|living)\s+(?:in|within)\s+(?:the\s+)?"
    r"(?:u\.?s\.?a?|usa|united\s+states|canada|australia|india|"
    r"latin\s+america|brazil|philippines)\b",

    r"\b(?:legally\s+)?(?:authoriz|authoris|eligib)\w*\s+to\s+work\s+in\s+"
    r"(?:the\s+)?(?:u\.?s\.?a?|usa|united\s+states|canada|australia)\b",

    r"\bwork\s+authoriz\w*\s+in\s+(?:the\s+)?(?:u\.?s\.?a?|usa|united\s+states|canada)\b",

    r"\b(?:u\.?s\.?|usa|united\s+states)\s+(?:citizen|citizenship|work\s+visa|"
    r"permanent\s+resident)\w*\s+(?:is\s+)?(?:required|only|a\s+must)\b",

    r"\bmust\s+be\s+a\s+(?:u\.?s\.?|usa|united\s+states|canadian)\s+"
    r"(?:citizen|resident|national)\b",

    r"\bgreen\s+card\b", r"\bsecurity\s+clearance\b",
    r"\b(?:ts/sci|public\s+trust)\b",

    r"\bthis\s+(?:role|position|job)\s+is\s+(?:only\s+)?(?:open|available|"
    r"limited)\s+to\s+(?:candidates?\s+)?(?:in|from|located\s+in)\s+"
    r"(?:the\s+)?(?:u\.?s\.?a?|usa|united\s+states|canada|australia)\b",

    r"\bremote\s*[\(\[]\s*(?:u\.?s\.?a?|usa|united\s+states|canada|australia)"
    r"(?:\s+only)?\s*[\)\]]",

    r"\b(?:u\.?s\.?|usa|united\s+states|canada|australia)[\s\-]based\s+"
    r"(?:candidates?|applicants?|employees?)\b",

    r"\bw2\s+(?:employee|contract|position)\b", r"\b1099\s+contractor\b",
]

_RESTRICTION = re.compile("|".join(_RESTRICTION_PHRASES), re.IGNORECASE | re.UNICODE)

# Спасательный круг: даже при жёсткой фразе вакансия проходит, если рядом
# прямо сказано, что открыты и другие регионы.
_OPEN_WORLDWIDE = _compile([
    r"worldwide", r"anywhere\s+in\s+the\s+world", r"any\s+time\s?zone",
    r"globally\s+distributed", r"open\s+to\s+all\s+(?:countries|locations)",
    r"hire\s+(?:from\s+)?anywhere",
])


def detect_geo_restriction(job: dict) -> tuple[bool, str]:
    """
    Определяет, закрыта ли удалённая вакансия для кандидата из домашнего региона.

    Два независимых слоя:
      1. Поле location — самый надёжный сигнал. Remotive и Jobicy кладут
         туда именно требование к локации кандидата ("USA Only", "LATAM").
      2. Текст описания — только по длинным явным формулировкам.

    Возвращает (blocked, reason).
    """
    title, location, description = _fields(job)

    if location.strip() and not _OK_REGION.search(location):
        bad = _BAD_REGION.search(location)
        if bad:
            return True, "локация только «" + bad.group(0) + "»"

        state = _US_STATE_LOC.search(str(job.get("location") or ""))
        if state:
            return True, "локация США/Канада «" + state.group(0).strip(" ,") + "»"

    # Регион в НАЗВАНИИ: "Senior Solutions Engineer - LATAM", "AI Lead (US)".
    # Борды вроде WeWorkRemotely всегда пишут в локации "Worldwide Remote",
    # и единственный признак ограничения остаётся в заголовке.
    if not _OK_REGION.search(title):
        bad_title = _BAD_REGION.search(title)
        if bad_title:
            return True, "регион в названии «" + bad_title.group(0) + "»"

    text = title + " " + description
    hit = _RESTRICTION.search(text)
    if hit and not _OPEN_WORLDWIDE.search(text):
        phrase = re.sub(r"\s+", " ", hit.group(0)).strip()
        return True, "требование «" + phrase[:60] + "»"

    return False, ""


# ─────────────────────────────────────────────
# 3. Привязка к американскому часовому поясу
# ─────────────────────────────────────────────
#
# Это НЕ блокировка, а штраф в scoring.py. Работать из ЕС со сдвигом
# в 6-9 часов физически можно, но это прямо противоречит запросу на
# асинхронный формат, поэтому такие вакансии опускаются ниже порога.

_TZ_LOCK = re.compile(
    r"(?:"
    r"\b(?:pst|pdt|est|edt|cst|cdt|mst|mdt)\b"
    r"|\b(?:pacific|eastern|central|mountain)\s+(?:standard\s+|daylight\s+)?time\b"
    r"|\b(?:us|u\.s\.|american|north\s+american)\s+time\s?zones?\b"
    r")",
    re.IGNORECASE | re.UNICODE,
)

_TZ_OK = _compile([
    r"cet", r"cest", r"european\s+time\s?zones?", r"any\s+time\s?zone",
    r"time\s?zone\s+(?:agnostic|independent)", r"async(?:hronous)?",
])


def detect_timezone_lock(job: dict) -> str:
    """Возвращает найденный маркер американского часового пояса или ''."""
    title, location, description = _fields(job)
    text = title + " " + location + " " + description
    hit = _TZ_LOCK.search(text)
    if not hit:
        return ""
    if _TZ_OK.search(text):
        return ""
    return hit.group(0)
