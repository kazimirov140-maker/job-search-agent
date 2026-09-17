"""
maintenance.py — Разовое обслуживание базы (v4.1).

Запуск:
    python maintenance.py --stats     показать состояние базы
    python maintenance.py --purge     удалить записи seen_jobs со score=0

Зачем нужен --purge:
    Прежние версии агента помечали вакансию просмотренной ДО того, как её
    успевала оценить LLM. В базе накопились записи с нулевой оценкой,
    которые навсегда закрывают дедупликацией нормальные вакансии: новый
    профиль просто не увидит подходящие роли, если они уже «просмотрены».

Операция удаляет строки безвозвратно, поэтому запускается вручную и
делает резервную копию файла базы рядом.
"""

import shutil
import sys
from datetime import datetime

from storage import JobStorage

DB_PATH = "jobs.db"


def show_stats() -> None:
    db = JobStorage(DB_PATH)
    stats = db.get_stats()
    print("Состояние базы:")
    for table, count in stats.items():
        print("  %-14s %d" % (table, count))

    cur = db.conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE score = 0 OR score IS NULL")
    zero = cur.fetchone()[0]
    total = stats.get("seen_jobs", 0)
    print()
    print("  записей с нулевой оценкой: %d из %d" % (zero, total))
    if zero:
        print("  эти записи блокируют повторную оценку — снять их: python maintenance.py --purge")
    db.close()


def purge() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = "jobs.db.backup_" + stamp
    shutil.copy2(DB_PATH, backup)
    print("Резервная копия: " + backup)

    db = JobStorage(DB_PATH)
    removed = db.purge_unscored()
    print("Удалено записей: %d" % removed)
    stats = db.get_stats()
    print("Осталось в seen_jobs: %d" % stats.get("seen_jobs", 0))
    db.close()


if __name__ == "__main__":
    if "--purge" in sys.argv:
        purge()
    else:
        show_stats()
