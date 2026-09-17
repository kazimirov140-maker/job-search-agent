"""Диагностика систем AI Job Search Agent."""
import os, sys, json, time
from dotenv import load_dotenv
load_dotenv()

print("=" * 60)
print("  ДИАГНОСТИКА — ПРОВЕРКА СИСТЕМ")
print("=" * 60)

# ── 1. Переменные окружения ──
print("\n[1] Переменные окружения:")
for var in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEYS", "GEMINI_API_KEY",
            "OPENROUTER_API_KEY", "GMAIL_EMAIL", "GMAIL_APP_PASSWORD", "GROQ_API_KEY"]:
    val = os.getenv(var, "")
    status = "OK" if val else "MISSING"
    preview = val[:25] + "..." if len(val) > 25 else val
    print(f"  {var}: {status} ({preview})")

# ── 2. Telegram ──
print("\n[2] Telegram Bot:")
try:
    import requests
    r = requests.get(f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/getMe", timeout=10)
    info = r.json()
    print(f"  OK @{info['result']['username']}" if info.get("ok") else f"  FAIL {info}")
except Exception as e:
    print(f"  FAIL {e}")

# ── 3. LLM Engine Connection ──
print("\n[3] LLM Engine (Gemini / OpenRouter / Fallback Chain):")
try:
    from llm_engine import test_connection, active_providers
    print(f"  Активные провайдеры: {', '.join(active_providers())}")
    ok = test_connection()
    print(f"  Статус подключения: {'OK' if ok else 'FAIL'}")
except Exception as e:
    print(f"  FAIL {e}")

# ── 4. SQLite Storage ──
print("\n[4] SQLite Storage:")
try:
    from storage import JobStorage
    storage = JobStorage()
    stats = storage.get_stats()
    print(f"  OK, статистика БД: {stats}")
    storage.close()
except Exception as e:
    print(f"  FAIL {e}")

print("\nDone.")

