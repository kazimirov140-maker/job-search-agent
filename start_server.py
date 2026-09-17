import os
import sys
import time
import subprocess
import re
import urllib.request
import signal
import threading

CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

# Пути считаются от файла скрипта, а не от текущей папки.
# Раньше здесь было просто "cloudflared.exe": Windows не ищет исполняемый
# файл в текущем каталоге, и запуск падал с FileNotFoundError [WinError 2]
# ещё до того, как туннель успевал подняться. Flask при этом уже стартовал
# и оставался висеть без туннеля, занимая порт 5000.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLOUDFLARED_EXE = os.path.join(BASE_DIR, "cloudflared.exe")
WEB_APP = os.path.join(BASE_DIR, "web_app.py")

def download_cloudflared():
    if not os.path.exists(CLOUDFLARED_EXE):
        print(f"[*] Downloading Cloudflare Tunnel ({CLOUDFLARED_EXE})...")
        urllib.request.urlretrieve(CLOUDFLARED_URL, CLOUDFLARED_EXE)
        print("[*] Download complete.")

def start_services():
    download_cloudflared()

    print("[*] Starting local web dashboard...")
    flask_process = subprocess.Popen(
        [sys.executable, WEB_APP],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=BASE_DIR,
    )

    time.sleep(2) # Give Flask a moment to start

    print("[*] Starting Cloudflare Tunnel...")
    # Cloudflared outputs its logs to stderr
    try:
        tunnel_process = subprocess.Popen(
            [CLOUDFLARED_EXE, "tunnel", "--url", "http://127.0.0.1:5000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=BASE_DIR,
        )
    except OSError as exc:
        # Без этого Flask оставался запущенным сиротой и держал порт.
        print(f"[!] Не удалось запустить туннель: {exc}")
        flask_process.terminate()
        sys.exit(1)

    def handle_exit(signum, frame):
        print("\n[*] Shutting down services...")
        tunnel_process.terminate()
        flask_process.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # Read stderr to find the public URL
    url_found = False
    
    print("\n" + "="*60)
    print("Waiting for public URL from Cloudflare...")
    
    try:
        # We read line by line from tunnel's stderr
        while True:
            line = tunnel_process.stderr.readline()
            if not line:
                break
            line_str = line.decode('utf-8', errors='ignore')
            
            # Looking for a line like:  |  https://some-random-words.trycloudflare.com  |
            match = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line_str)
            if match and not url_found:
                public_url = match.group(1)
                print("\n" + "=== "*15, flush=True)
                print(f"YOUR DASHBOARD IS LIVE AT:\n--> {public_url}", flush=True)
                print("=== "*15 + "\n", flush=True)
                print("Press Ctrl+C to stop the server.", flush=True)
                url_found = True
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[*] Shutting down services...")
        tunnel_process.terminate()
        flask_process.terminate()
        sys.exit(0)

if __name__ == "__main__":
    start_services()
