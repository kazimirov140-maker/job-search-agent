import os
import sys
import time
import subprocess
from threading import Thread, Lock
from flask import Flask, jsonify, request, Response, send_from_directory
from storage import JobStorage
import cloud_storage
import telegram_bot

app = Flask(__name__, static_folder='static', template_folder='templates')

# Cloud Run initialization
cloud_storage.ensure_bucket_exists()
cloud_storage.download_db()

# Start telegram bot listener in background
bot_listener_thread = Thread(target=telegram_bot.main)
bot_listener_thread.daemon = True
bot_listener_thread.start()

# State variables for background process
bot_process = None
process_lock = Lock()
log_subscribers = []
log_history = []
current_run = {"mode": None, "started_at": None, "finished_at": None}

def notify_subscribers(line):
    for sub in log_subscribers:
        sub.append(line)

def run_bot_thread(mode):
    global bot_process
    
    env = os.environ.copy()
    if mode:
        env["RUN_MODE"] = mode
        
    # We use python -u to unbuffer stdout
    bot_process = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env
    )
    
    current_run["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    current_run["finished_at"] = None
    notify_subscribers(f"--- Bot Started (Mode: {mode}) ---\n")

    for line in iter(bot_process.stdout.readline, ''):
        log_history.append(line)
        if len(log_history) > 1000:
            log_history.pop(0)
        notify_subscribers(line)
        
    bot_process.stdout.close()
    bot_process.wait()
    
    notify_subscribers(f"--- Bot Finished (Code: {bot_process.returncode}) ---\n")

    current_run["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    with process_lock:
        bot_process = None

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/api/status')
def status():
    with process_lock:
        is_running = bot_process is not None
    return jsonify({
        "running": is_running,
        "mode": current_run["mode"],
        "started_at": current_run["started_at"],
        "finished_at": current_run["finished_at"],
    })

@app.route('/api/run', methods=['POST'])
def run_bot():
    global bot_process
    with process_lock:
        if bot_process is not None:
            return jsonify({"status": "already_running"}), 400

        data = request.get_json() or {}
        mode = data.get('mode', 'standard')

        # Clear log history for new run
        log_history.clear()
        current_run["mode"] = mode

        thread = Thread(target=run_bot_thread, args=(mode,))
        thread.daemon = True
        thread.start()

    return jsonify({"status": "started", "mode": mode})


@app.route('/api/stop', methods=['POST'])
def stop_bot():
    """
    Останавливает текущий прогон.

    Прогон может идти 20-30 минут, и без этой ручки единственным способом
    его прервать было убить весь сервер вместе с телеграм-ботом.
    """
    with process_lock:
        process = bot_process

    if process is None:
        return jsonify({"status": "not_running"}), 400

    try:
        process.terminate()
        notify_subscribers("--- Остановлено пользователем ---\n")
        return jsonify({"status": "stopping"})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500

@app.route('/api/stream-logs')
def stream_logs():
    def generate():
        queue = []
        log_subscribers.append(queue)
        
        # Send history first
        for line in log_history:
            yield f"data: {line}\n\n"
            
        try:
            while True:
                if queue:
                    line = queue.pop(0)
                    yield f"data: {line}\n\n"
                else:
                    time.sleep(0.1)
        except GeneratorExit:
            log_subscribers.remove(queue)

    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/stats')
def get_stats():
    db = JobStorage()
    conn = db.get_connection()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM seen_jobs")
    total_seen = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM seen_jobs WHERE score >= 75")
    total_high_score = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM rejections")
    total_rejected = c.fetchone()[0]
    
    db.close()
    
    return jsonify({
        "total_seen": total_seen,
        "total_high_score": total_high_score,
        "total_rejected": total_rejected
    })

@app.route('/api/jobs')
def get_jobs():
    """
    Топ вакансий по баллу.

    Раньше здесь стоял запрос к seen_jobs по колонкам title, company и
    date_added, которых в этой таблице нет: она хранит только
    НОРМАЛИЗОВАННЫЕ title_norm / company_norm для дедупликации. Запрос
    падал на каждом вызове, и панель «Top Matches» всегда показывала
    «Error loading jobs». Читаемые название, компания и ссылка лежат в
    job_states — оттуда их и берём.
    """
    limit = request.args.get('limit', 50, type=int)
    min_score = request.args.get('min_score', 70, type=int)

    db = JobStorage()
    conn = db.get_connection()
    c = conn.cursor()

    c.execute('''
        SELECT s.job_id,
               COALESCE(NULLIF(js.title, ''), s.title_norm)     AS title,
               COALESCE(NULLIF(js.company, ''), s.company_norm) AS company,
               s.source, s.score, s.seen_at,
               COALESCE(js.url, '')    AS url,
               COALESCE(js.status, '') AS status
        FROM seen_jobs s
        LEFT JOIN job_states js ON js.job_id = s.job_id
        WHERE s.score >= ?
        ORDER BY s.score DESC, s.seen_at DESC
        LIMIT ?
    ''', (min_score, limit))

    rows = c.fetchall()
    db.close()

    return jsonify([{
        "job_id": row[0],
        "title": row[1],
        "company": row[2],
        "source": row[3],
        "score": row[4],
        "date_added": row[5],
        "url": row[6],
        "status": row[7],
    } for row in rows])


@app.route('/api/funnel')
def get_funnel():
    """
    Воронка последнего состояния базы: сколько вакансий на каком этапе
    отсеялось и по какой причине.

    Без этой сводки непонятно, почему прогон на полторы тысячи вакансий
    заканчивается тремя уведомлениями — а именно этот вопрос возникает
    первым, когда выдача выглядит неправильной.
    """
    db = JobStorage()
    conn = db.get_connection()
    c = conn.cursor()

    c.execute("SELECT status, COUNT(*) FROM job_states GROUP BY status")
    by_status = {row[0]: row[1] for row in c.fetchall()}

    # Причина хранится целиком («гео: локация только «usa»»); для сводки
    # достаточно её категории до двоеточия.
    c.execute('''
        SELECT last_error, COUNT(*) AS n
        FROM job_states
        WHERE status = 'rejected_deterministic' AND last_error <> ''
        GROUP BY last_error
        ORDER BY n DESC
        LIMIT 60
    ''')
    buckets = {}
    for reason, count in c.fetchall():
        bucket = str(reason).split(":")[0].strip() or "прочее"
        buckets[bucket] = buckets.get(bucket, 0) + count

    c.execute('''
        SELECT source, COUNT(*) AS n
        FROM seen_jobs
        WHERE score >= 75
        GROUP BY source
        ORDER BY n DESC
        LIMIT 15
    ''')
    top_sources = [{"source": row[0] or "?", "count": row[1]} for row in c.fetchall()]

    db.close()

    return jsonify({
        "by_status": by_status,
        "reject_reasons": sorted(
            [{"reason": k, "count": v} for k, v in buckets.items()],
            key=lambda item: -item["count"],
        ),
        "top_sources": top_sources,
    })

if __name__ == '__main__':
    # Ensure static and templates exist
    os.makedirs('static', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    app.run(port=5000)
