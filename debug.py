"""
debug.py — Единый диагностический скрипт для AI Job Search Agent (v2.0).
Объединяет: diagnose.py, diag_system.py, diag_parsers.py, diag_telegram.py.

Использование:
    python debug.py                — полная диагностика
    python debug.py --parsers      — только парсеры
    python debug.py --telegram     — только Telegram
    python debug.py --llm          — только LLM
    python debug.py --storage      — только SQLite
"""

import os
import sys
import time
import argparse
import logging

# Windows console encoding fix
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

# Подавляем лишние логи при диагностике
logging.disable(logging.CRITICAL)


def check_env():
    """Проверка переменных окружения."""
    print("=" * 60)
    print("  [1] ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ")
    print("=" * 60)

    required = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "OPENROUTER_API_KEY",
    ]
    optional = [
        "GMAIL_EMAIL",
        "GMAIL_APP_PASSWORD",
    ]

    all_ok = True
    for var in required:
        val = os.getenv(var, "")
        if val:
            preview = val[:15] + "..." if len(val) > 15 else val
            print(f"  ✅ {var} = {preview}")
        else:
            print(f"  ❌ {var} — ОТСУТСТВУЕТ (обязательная)")
            all_ok = False

    for var in optional:
        val = os.getenv(var, "")
        if val:
            preview = val[:15] + "..." if len(val) > 15 else val
            print(f"  ✅ {var} = {preview}")
        else:
            print(f"  ⚠️  {var} — не задана (опциональная)")

    # Проверяем что config импортируется
    try:
        from config import SOURCES, TRACK_1_KEYWORDS, TRACK_2_KEYWORDS, BLOCKLIST_KEYWORDS
        print(f"\n  ✅ config.py импортируется")
        print(f"     SOURCES: {len(SOURCES)}")
        print(f"     TRACK_1_KEYWORDS: {len(TRACK_1_KEYWORDS)}")
        print(f"     TRACK_2_KEYWORDS: {len(TRACK_2_KEYWORDS)}")
        print(f"     BLOCKLIST_KEYWORDS: {len(BLOCKLIST_KEYWORDS)}")
    except Exception as e:
        print(f"\n  ❌ config.py — ошибка: {e}")
        all_ok = False

    return all_ok


def check_telegram():
    """Проверка Telegram Bot API."""
    print("\n" + "=" * 60)
    print("  [2] TELEGRAM BOT")
    print("=" * 60)

    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token:
        print("  ❌ TELEGRAM_BOT_TOKEN не задан")
        return False

    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        info = r.json()
        if info.get("ok"):
            username = info['result'].get('username', '?')
            print(f"  ✅ Бот: @{username}")
        else:
            print(f"  ❌ getMe вернул ошибку: {info}")
            return False
    except Exception as e:
        print(f"  ❌ Не удалось подключиться: {e}")
        return False

    # Тест отправки
    if chat_id:
        try:
            test_msg = "🔧 <b>debug.py</b> — тест подключения"
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": test_msg, "parse_mode": "HTML"},
                timeout=10,
            )
            if r.json().get("ok"):
                print(f"  ✅ Тестовое сообщение отправлено в {chat_id}")
            else:
                print(f"  ❌ Ошибка отправки: {r.json()}")
                return False
        except Exception as e:
            print(f"  ❌ Ошибка отправки: {e}")
            return False

    return True


def check_llm():
    """Проверка OpenRouter LLM."""
    print("\n" + "=" * 60)
    print("  [3] OpenRouter LLM")
    print("=" * 60)

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  ❌ OPENROUTER_API_KEY не задан")
        return False

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        t0 = time.time()
        resp = client.chat.completions.create(
            model="nvidia/nemotron-3-super-120b-a12b:free",
            messages=[{"role": "user", "content": 'Reply with exactly: {"status": "ok"}'}],
            temperature=0.0,
            max_tokens=50,
        )
        elapsed = time.time() - t0
        content = resp.choices[0].message.content.strip()
        print(f"  ✅ LLM работает ({elapsed:.1f}с)")
        print(f"     Ответ: {content[:100]}")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка LLM: {e}")
        return False


def check_storage():
    """Проверка SQLite хранилища."""
    print("\n" + "=" * 60)
    print("  [4] SQLITE ХРАНИЛИЩЕ")
    print("=" * 60)

    try:
        from storage import JobStorage, normalize_title, normalize_company, make_fingerprint

        storage = JobStorage()

        # Проверяем нормализацию
        t1 = normalize_title("Senior AI Automation Engineer")
        c1 = normalize_company("Acme Corp LLC")
        fp = make_fingerprint("Senior AI Automation Engineer", "Acme Corp LLC")
        print(f"  ✅ normalize_title:  '{t1}'")
        print(f"  ✅ normalize_company: '{c1}'")
        print(f"  ✅ fingerprint:       '{fp}'")

        # Тест дедупликации
        storage.save_seen("debug_test_001", "Test Job", "Test Corp", "debug", 75)
        is_dup = storage.is_duplicate("Test Job", "Test Corp")
        print(f"  ✅ Дедупликация: {'работает' if is_dup else 'НЕ работает'}")

        # Тест is_job_seen
        is_seen = storage.is_job_seen("debug_test_001")
        print(f"  ✅ is_job_seen: {'работает' if is_seen else 'НЕ работает'}")

        storage.close()
        return True
    except Exception as e:
        print(f"  ❌ Ошибка SQLite: {e}")
        return False


def check_parsers():
    """Проверка каждого парсера по отдельности."""
    print("\n" + "=" * 60)
    print("  [5] ПАРСЕРЫ (по одному)")
    print("=" * 60)

    from scrapers.api_scrapers import fetch_jobicy, fetch_remotive, fetch_himalayas, fetch_workingnomads
    from scrapers.rss_scrapers import fetch_djinni, fetch_dou, fetch_layboard, fetch_ceehire, fetch_europeremotely
    from scrapers.ats_scrapers import fetch_greenhouse, fetch_lever
    from scrapers.email_scrapers import fetch_from_emails
    from scrapers.track2_scrapers import fetch_all_track2

    # Трек 1 — API
    parsers_t1_api = [
        ("Jobicy (API)", fetch_jobicy),
        ("Remotive (API)", fetch_remotive),
        ("Himalayas (API)", fetch_himalayas),
        ("WorkingNomads (API)", fetch_workingnomads),
    ]

    # Трек 1 — RSS
    parsers_t1_rss = [
        ("Djinni (RSS)", fetch_djinni),
        ("DOU (RSS)", fetch_dou),
        ("Layboard (RSS)", fetch_layboard),
        ("CeeHire (RSS)", fetch_ceehire),
        ("Europeremotely (RSS)", fetch_europeremotely),
    ]

    # Трек 1 — ATS
    parsers_t1_ats = [
        ("Greenhouse (ATS)", fetch_greenhouse),
        ("Lever (ATS)", fetch_lever),
    ]

    # Трек 1 — Email
    parsers_t1_email = [
        ("Email (Gmail)", fetch_from_emails),
    ]

    all_parsers = parsers_t1_api + parsers_t1_rss + parsers_t1_ats + parsers_t1_email
    results = []

    print("\n  ── ТРЕК 1: AI Automation ──")
    for name, func in all_parsers:
        try:
            t0 = time.time()
            jobs = func()
            elapsed = time.time() - t0
            icon = "✅" if jobs else "⚠️"
            print(f"  {icon} {name}: {len(jobs)} вакансий ({elapsed:.1f}с)")
            if jobs:
                j = jobs[0]
                print(f"      → {j.get('title', '?')} @ {j.get('company', '?')}")
            results.extend(jobs)
        except Exception as e:
            print(f"  ❌ {name}: ОШИБКА — {e}")

    # Трек 2
    print("\n  ── ТРЕК 2: AI Evaluation ──")
    try:
        t0 = time.time()
        track2 = fetch_all_track2()
        elapsed = time.time() - t0
        print(f"  {'✅' if track2 else '⚠️'} Track 2: {len(track2)} платформ ({elapsed:.1f}с)")
        for p in track2:
            print(f"      → {p.get('platform', '?')}: {p.get('title', '?')}")
    except Exception as e:
        print(f"  ❌ Track 2: ОШИБКА — {e}")

    total = len(results)
    print(f"\n  ИТОГО Трек 1: {total} вакансий")
    return total > 0


def check_block_filter():
    """Проверка фильтра блокировки."""
    print("\n" + "=" * 60)
    print("  [6] ФИЛЬТР БЛОКИРОВКИ")
    print("=" * 60)

    from config import is_blocked

    tests = [
        # (job, url, expected, description)
        ({"title": "Dev", "description": "", "location": ""}, "https://hh.ru/job/123", True, "hh.ru URL"),
        ({"title": "Dev", "description": "", "location": ""}, "https://djinni.co/job/456", False, "djinni URL"),
        ({"title": "Dev", "description": "Москва, офис", "location": ""}, "https://example.com", True, "Москва в description"),
        ({"title": "Dev", "description": "US only position", "location": ""}, "https://example.com", True, "US only"),
        ({"title": "Dev", "description": "async remote worldwide", "location": "Remote"}, "https://example.com", False, "Remote worldwide"),
        ({"title": "Dev", "description": "", "location": ""}, "https://superjob.ru/vacancy/1", True, "superjob.ru"),
    ]

    all_ok = True
    for job, url, expected, desc in tests:
        result = is_blocked(job, url)
        icon = "✅" if result == expected else "❌"
        if result != expected:
            all_ok = False
        print(f"  {icon} {desc}: blocked={result} (expected={expected})")

    return all_ok


def check_linkedin_api():
    """Проверка подключения к LinkedIn API (RapidAPI)."""
    print("\n" + "=" * 60)
    print("  [7] LINKEDIN API (RapidAPI)")
    print("=" * 60)

    rapidapi_key = os.getenv("RAPIDAPI_KEY", "")
    if not rapidapi_key:
        print("  ⚠️  RAPIDAPI_KEY не задан — linkedin_api пропущен")
        print("     (опционально, ~$10/мес)")
        return True  # не критично

    try:
        import requests
        r = requests.get(
            "https://linkedin-jobs-search.p.rapidapi.com/",
            headers={
                "X-RapidAPI-Key": rapidapi_key,
                "X-RapidAPI-Host": "linkedin-jobs-search.p.rapidapi.com",
            },
            params={"search_query": "AI automation remote", "location": "worldwide", "page": "1"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            print(f"  ✅ LinkedIn API работает, найдено: {len(data)} вакансий")
            return True
        else:
            print(f"  ❌ HTTP {r.status_code}: {r.text[:100]}")
            return False
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        return False


# ─────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Диагностика AI Job Search Agent v2.0")
    parser.add_argument("--env", action="store_true", help="Только переменные окружения")
    parser.add_argument("--telegram", action="store_true", help="Только Telegram")
    parser.add_argument("--llm", action="store_true", help="Только LLM")
    parser.add_argument("--storage", action="store_true", help="Только SQLite")
    parser.add_argument("--parsers", action="store_true", help="Только парсеры")
    parser.add_argument("--block", action="store_true", help="Только фильтр блокировки")
    parser.add_argument("--linkedin", action="store_true", help="Только LinkedIn API")
    args = parser.parse_args()

    any_specific = any([args.env, args.telegram, args.llm, args.storage, args.parsers, args.block, args.linkedin])

    results = {}

    if not any_specific or args.env:
        results["env"] = check_env()

    if not any_specific or args.telegram:
        results["telegram"] = check_telegram()

    if not any_specific or args.llm:
        results["llm"] = check_llm()

    if not any_specific or args.storage:
        results["storage"] = check_storage()

    if not any_specific or args.parsers:
        results["parsers"] = check_parsers()

    if not any_specific or args.block:
        results["block_filter"] = check_block_filter()

    if not any_specific or args.linkedin:
        results["linkedin_api"] = check_linkedin_api()

    # Итоговая сводка
    if not any_specific:
        print("\n" + "=" * 60)
        print("  ИТОГОВАЯ СВОДКА")
        print("=" * 60)
        for name, ok in results.items():
            icon = "✅" if ok else "❌"
            print(f"  {icon} {name}")
        total_ok = sum(1 for v in results.values() if v)
        print(f"\n  {total_ok}/{len(results)} компонентов работают")
        if total_ok == len(results):
            print("  🎉 ВСЁ ГОТОВО К ЗАПУСКУ!")
        else:
            print("  ⚠️  Есть проблемы — см. детали выше")


if __name__ == "__main__":
    main()
