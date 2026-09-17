import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

secrets = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GEMINI_API_KEYS",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "GROQ_API_KEY",
    "GMAIL_EMAIL",
    "GMAIL_APP_PASSWORD",
]

for secret in secrets:
    val = os.getenv(secret, "")
    if val:
        print(f"Setting {secret}...")
        subprocess.run(["gh", "secret", "set", secret], input=val.encode('utf-8'), check=True)
    else:
        print(f"Warning: {secret} is empty!")

print("All secrets set successfully!")
