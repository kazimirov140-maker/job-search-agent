"""
storage.py — Хранилище вакансий на базе SQLite (v2.0).

Класс JobStorage обеспечивает:
- Дедупликацию по job_id и по fingerprint (title + company)
- Тихое сохранение вакансий (score 60-74)
- Сохранение отклонений из Telegram (rejections)
- Получение паттернов отклонений для LLM-промпта
"""

import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

# ─────────────────────────────────────────────
# Настройка логирования
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Нормализация для дедупликации
# ─────────────────────────────────────────────

STOP_WORDS = {
    'senior', 'junior', 'mid', 'lead', 'remote',
    'contract', 'part', 'time', 'full', 'staff',
    'the', 'a', 'an', 'and', 'or', 'of', 'in', 'for',
}
COMPANY_SUFFIXES = {'llc', 'inc', 'ltd', 'gmbh', 'srl', 'sl', 'sa', 'corp', 'co'}


def normalize_title(title: str) -> str:
    """Нормализует название вакансии для дедупликации."""
    t = title.lower()
    t = re.sub(r'[^a-z0-9а-яё ]', ' ', t)
    words = [w for w in t.split() if w not in STOP_WORDS]
    return ' '.join(words[:6])


def normalize_company(company: str) -> str:
    """Нормализует название компании для дедупликации."""
    c = company.lower()
    c = re.sub(r'[^a-z0-9а-яё ]', ' ', c)
    words = [w for w in c.split() if w not in COMPANY_SUFFIXES]
    return ' '.join(words[:3])


def make_fingerprint(title: str, company: str) -> str:
    """Создаёт уникальный отпечаток вакансии по названию + компании."""
    key = normalize_title(title) + '|' + normalize_company(company)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────
# Основной класс
# ─────────────────────────────────────────────

class JobStorage:
    """
    Хранилище вакансий на базе SQLite.

    Пример использования:
        storage = JobStorage()
        if not storage.is_duplicate("AI Engineer", "Acme Corp"):
            storage.save_seen("remoteok_123", "AI Engineer", "Acme Corp", "remoteok", 82)
    """

    def __init__(self, db_path: str = "jobs.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("SQLite хранилище инициализировано: %s", db_path)

    def _create_tables(self):
        """Создаёт таблицы если они не существуют."""
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS seen_jobs (
                job_id TEXT PRIMARY KEY,
                title_norm TEXT NOT NULL,
                company_norm TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                source TEXT,
                score INTEGER,
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_fingerprint ON seen_jobs(fingerprint);

            CREATE TABLE IF NOT EXISTS silent_jobs (
                job_id TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                source TEXT,
                score INTEGER,
                url TEXT,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rejections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                title TEXT,
                company TEXT,
                reason TEXT,
                rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS job_states (
                job_id TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                source TEXT,
                status TEXT NOT NULL,
                last_error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self):
        """Выполняет плавную миграцию существующих таблиц."""
        try:
            cur = self.conn.execute("PRAGMA table_info(rejections)")
            columns = [row["name"] for row in cur.fetchall()]
            if "reason" not in columns:
                self.conn.execute("ALTER TABLE rejections ADD COLUMN reason TEXT")
                logger.info("Миграция: добавлена колонка 'reason' в таблицу rejections")
        except Exception as e:
            logger.warning("Ошибка при проверке миграции rejections: %s", e)

        # Ссылка на вакансию: без неё карточки на дашборде нельзя открыть,
        # а seen_jobs хранит только нормализованные title/company.
        try:
            cur = self.conn.execute("PRAGMA table_info(job_states)")
            columns = [row["name"] for row in cur.fetchall()]
            if "url" not in columns:
                self.conn.execute("ALTER TABLE job_states ADD COLUMN url TEXT")
                logger.info("Миграция: добавлена колонка 'url' в таблицу job_states")
        except Exception as e:
            logger.warning("Ошибка при проверке миграции job_states: %s", e)

    # ─────────────────────────────────────────
    # Дедупликация
    # ─────────────────────────────────────────

    def is_duplicate(self, title: str, company: str) -> bool:
        """Проверяет, была ли уже такая вакансия (по нормализованным title + company)."""
        cur = self.conn.execute(
            '''SELECT 1 FROM seen_jobs
               WHERE title_norm = ? AND company_norm = ?''',
            (normalize_title(title), normalize_company(company))
        )
        return cur.fetchone() is not None

    def is_job_seen(self, job_id: str) -> bool:
        """Проверяет, был ли уже такой job_id."""
        cur = self.conn.execute(
            'SELECT 1 FROM seen_jobs WHERE job_id = ?',
            (job_id,)
        )
        return cur.fetchone() is not None

    def is_fingerprint_seen(self, fingerprint: str) -> bool:
        """Проверяет, был ли уже такой fingerprint."""
        if not fingerprint:
            return False
        cur = self.conn.execute(
            'SELECT 1 FROM seen_jobs WHERE fingerprint = ?',
            (fingerprint,)
        )
        return cur.fetchone() is not None

    def record_state(self, job_id: str, title: str, company: str,
                     source: str, status: str, last_error: str = "",
                     commit: bool = True, url: str = ""):
        """Фиксирует жизненный цикл вакансии отдельно от seen_jobs."""
        if not job_id:
            return
        self.conn.execute(
            """INSERT INTO job_states
               (job_id, title, company, source, status, last_error, attempts, url)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                 title=excluded.title, company=excluded.company, source=excluded.source,
                 status=excluded.status, last_error=excluded.last_error,
                 attempts=job_states.attempts + 1,
                 -- пустая ссылка не должна затирать уже сохранённую
                 url=COALESCE(NULLIF(excluded.url, ''), job_states.url),
                 updated_at=CURRENT_TIMESTAMP""",
            (job_id, title, company, source, status, last_error, url),
        )
        if commit:
            self.conn.commit()

    def commit(self):
        """Принудительно фиксирует транзакцию."""
        self.conn.commit()

    def get_connection(self) -> sqlite3.Connection:
        """
        Открытое соединение для произвольных запросов.

        Метода не было вовсе, а web_app вызывал его в /api/stats и
        /api/jobs — оба эндпоинта падали с AttributeError, из-за чего
        дашборд показывал прочерки в метриках и ошибку в списке вакансий.
        """
        return self.conn

    # ─────────────────────────────────────────
    # Сохранение
    # ─────────────────────────────────────────

    def save_seen(self, job_id: str, title: str, company: str,
                  source: str = "", score: int = 0):
        """Сохраняет вакансию как просмотренную (для дедупликации)."""
        title_norm = normalize_title(title)
        company_norm = normalize_company(company)
        fingerprint = make_fingerprint(title, company)
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO seen_jobs
                   (job_id, title_norm, company_norm, fingerprint, source, score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job_id, title_norm, company_norm, fingerprint, source, score)
            )
            self.conn.commit()
        except Exception as e:
            logger.error("Ошибка сохранения seen_jobs: %s", e)

    def save_silent(self, job: dict):
        """Сохраняет вакансию тихо (score 60-74, не отправляется в Telegram)."""
        job_id = job.get("job_id", "")
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO silent_jobs
                   (job_id, title, company, source, score, url)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                # main.py кладёт итоговую оценку в match_score; ключа "score"
                # в вакансии нет, из-за чего все тихие сохранения получали 0.
                (job_id, job.get("title", ""), job.get("company", ""),
                 job.get("source", ""),
                 job.get("match_score", job.get("score", 0)),
                 job.get("url", ""))
            )
            self.conn.commit()
            logger.debug("Тихое сохранение: %s (%s)", job.get("title"), job.get("score"))
        except Exception as e:
            logger.error("Ошибка тихого сохранения: %s", e)

    # ─────────────────────────────────────────
    # Отклонения (rejections)
    # ─────────────────────────────────────────

    def save_rejection(self, job_id: str, title: str = "", company: str = "", reason: str = ""):
        """Сохраняет отклонение вакансии из Telegram вместе с причиной."""
        job_id = str(job_id)[:45]
        try:
            self.conn.execute(
                """INSERT OR REPLACE INTO rejections (job_id, title, company, reason)
                   VALUES (?, ?, ?, ?)""",
                (job_id, title, company, reason)
            )
            self.conn.commit()
            logger.info("Отклонение сохранено: %s — %s (причина: %s)", company, title, reason or "не указана")
        except Exception as e:
            logger.error("Ошибка сохранения отклонения: %s", e)

    def is_rejected(self, job_id: str) -> bool:
        """Проверяет, была ли вакансия отклонена пользователем."""
        if not job_id:
            return False
        job_id = str(job_id)[:45]
        cur = self.conn.execute("SELECT 1 FROM rejections WHERE job_id = ? LIMIT 1", (job_id,))
        return cur.fetchone() is not None

    def get_recent_rejections(self, limit: int = 15) -> list[dict]:
        """Возвращает список последних отклонённых вакансий с причинами."""
        cur = self.conn.execute(
            """SELECT job_id, title, company, reason, rejected_at FROM rejections
               ORDER BY id DESC, rejected_at DESC LIMIT ?""",
            (limit,)
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_rejection_prompt_context(self, limit: int = 12) -> str:
        """
        Формирует текстовый контекст для системного промпта LLM
        на основе последних отклонённых пользователем вакансий.
        """
        rejections = self.get_recent_rejections(limit=limit)
        if not rejections:
            return ""

        lines = [
            "ПРИМЕРЫ ОТКЛОНЁННЫХ КАНДИДАТОМ ВАКАНСИЙ (УЧИТЫВАЙ ПРИЧИНЫ И СНИЖАЙ ОЦЕНКУ / СТАВЬ passed_filter=false ДЛЯ АНАЛОГИЧНЫХ):"
        ]
        for r in rejections:
            title = (r.get("title") or "").strip()
            company = (r.get("company") or "").strip()
            reason = (r.get("reason") or "Не подошла").strip()
            if not title and not company:
                continue
            entity = f'"{title}"' if title else "Вакансия"
            if company and company.lower() not in {"unknown", "n/a", "-"}:
                entity += f" ({company})"
            lines.append(f"- {entity} — Причина отказа: {reason}")

        if len(lines) == 1:
            return ""

        return "\n".join(lines)

    def get_rejection_patterns(self, limit: int = 10) -> list[dict]:
        """
        Анализирует паттерны отклонений.
        Возвращает топ слов из названий отклонённых вакансий.
        """
        cur = self.conn.execute(
            """SELECT title, company FROM rejections
               ORDER BY rejected_at DESC LIMIT 50"""
        )
        rows = cur.fetchall()

        if not rows:
            return []

        # Считаем частоту слов в названиях отклонённых вакансий
        word_freq: dict[str, int] = {}
        for row in rows:
            title = row['title'].lower() if row['title'] else ""
            words = re.findall(r'[a-z]+', title)
            for w in words:
                if w not in STOP_WORDS and len(w) > 2:
                    word_freq[w] = word_freq.get(w, 0) + 1

        # Сортируем по частоте
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [{"word": w, "count": c} for w, c in sorted_words[:limit]]

    # ─────────────────────────────────────────
    # Обслуживание
    # ─────────────────────────────────────────

    def purge_unscored(self) -> int:
        """
        Удаляет записи seen_jobs с нулевой оценкой.

        Прежние версии помечали вакансию просмотренной ДО того, как LLM
        успевала её оценить. В базе накопились записи со score=0, которые
        навсегда закрывали дедупликацией нормальные вакансии. Такие записи
        нужно убрать, иначе новый профиль просто не увидит подходящие роли.
        """
        cur = self.conn.execute("DELETE FROM seen_jobs WHERE score = 0 OR score IS NULL")
        self.conn.commit()
        removed = cur.rowcount or 0
        if removed:
            logger.info("Очищено %d записей seen_jobs с нулевой оценкой", removed)
        return removed

    # ─────────────────────────────────────────
    # Статистика
    # ─────────────────────────────────────────

    def get_stats(self) -> dict:
        """Возвращает статистику хранилища."""
        stats = {}
        for table in ['seen_jobs', 'silent_jobs', 'rejections', 'job_states']:
            cur = self.conn.execute(f'SELECT COUNT(*) FROM {table}')
            stats[table] = cur.fetchone()[0]
        return stats

    # ─────────────────────────────────────────
    # Закрытие
    # ─────────────────────────────────────────

    def close(self):
        """Закрывает соединение с базой данных."""
        self.conn.close()
        logger.info("SQLite соединение закрыто.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
