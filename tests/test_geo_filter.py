"""
tests/test_geo_filter.py — Проверка разделения языка и юрисдикции.

Запуск:  python tests/test_geo_filter.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from geo_filter import detect_jurisdiction, detect_russophone, _domain_blocked

failures = []


def check(condition: bool, label: str):
    print(("  ok    " if condition else "  ПРОВАЛ ") + label)
    if not condition:
        failures.append(label)


print("=== НЕ должны блокироваться: ложные срабатывания старой версии ===")
for job, label in [
    ({"title": "Розробка інтерфейсу для AI-агента", "company": "SoftServe",
      "location": "Kyiv, Ukraine", "description": "проєктування інтерфейсу"},
     "укр. 'інтерфейс' не должен ловиться как 'рф'"),
    ({"title": "AI Automation Consultant", "company": "Innova",
      "location": "Remote", "description": "Инновации в области автоматизации процессов"},
     "'инновации' не должно ловиться как 'ИНН'"),
    ({"title": "AI Implementation Consultant", "company": "CloudCo",
      "location": "Cyprus", "description": "Наши клиенты в Европе и России"},
     "упоминание клиентов в России не блокирует кипрскую компанию"),
    ({"title": "Customer Success Manager", "company": "Acme",
      "location": "Remote", "description": "We serve clients across Europe and Russia"},
     "англ. упоминание Russia в описании не блокирует"),
    ({"title": "AI консультант", "company": "Kolesa Group",
      "location": "Алматы, Казахстан", "description": "русскоязычная команда"},
     "русскоязычная вакансия из Казахстана проходит"),
]:
    blocked, reason = detect_jurisdiction(job)
    check(not blocked, label + (" -> " + reason if blocked else ""))

print()
print("=== ДОЛЖНЫ блокироваться ===")
for job, label in [
    ({"title": "AI-инженер", "company": "ООО Ромашка", "location": "Москва", "description": ""},
     "Москва в локации"),
    ({"title": "Python dev", "company": "X", "location": "Remote",
      "description": "Оформление по ТК РФ, оплата в рублях"},
     "ТК РФ и рубли в описании"),
    ({"title": "AI Specialist", "company": "Y", "location": "Remote",
      "description": "Офис в Санкт-Петербурге, возможна релокация."},
     "город в локационном контексте описания"),
    ({"title": "Dev", "company": "Z", "location": "Remote", "description": "зарплата 200 тыс. руб"},
     "рубли в описании"),
    ({"title": "Engineer", "company": "W", "location": "Minsk, Belarus", "description": ""},
     "Минск при BLOCK_BELARUS=true"),
    ({"title": "Dev", "company": "V", "location": "Remote",
      "url": "https://hh.ru/vacancy/123", "description": ""},
     "домен hh.ru"),
    ({"title": "Разработчик", "company": "Компания", "location": "Россия, удалённо",
      "description": ""},
     "Россия в локации"),
]:
    blocked, reason = detect_jurisdiction(job)
    check(blocked, label + (" -> " + reason if blocked else " (НЕ ПОЙМАН)"))

print()
print("=== Домены: не ловить ложно ===")
for url, expect_blocked in [
    ("https://jobs.rubyonrails.org/x", False),
    ("https://app.run.com/job/1", False),
    ("https://djinni.co/jobs/1", False),
    ("https://careers.truecaller.com/x", False),
    ("https://hh.ru/vacancy/1", True),
    ("https://example.ru/job", True),
    ("https://rabota.by/1", True),
]:
    got = bool(_domain_blocked(url))
    check(got == expect_blocked, url + " -> " + ("блок" if got else "ok"))

print()
print("=== Беларусь снимается флагом ===")
os.environ["BLOCK_BELARUS"] = "false"
blocked, _ = detect_jurisdiction({"title": "Dev", "company": "X", "location": "Minsk", "description": ""})
check(not blocked, "BLOCK_BELARUS=false пропускает Минск")
os.environ["BLOCK_BELARUS"] = "true"

print()
print("=== Бонус русскоязычных стран ===")
for job, expected in [
    ({"title": "AI Consultant", "company": "Kolesa", "location": "Almaty, Kazakhstan"}, "Казахстан"),
    ({"title": "Dev", "company": "X", "location": "Tbilisi, Georgia"}, "Грузия"),
    ({"title": "AI спеціаліст", "company": "X", "location": "Київ"}, "Украина"),
    ({"title": "Dev", "company": "X", "location": "Berlin, Germany"}, None),
]:
    got = detect_russophone(job)
    check(got == expected, str(job.get("location")) + " -> " + str(got))

print()
if failures:
    print("ПРОВАЛЕНО: " + str(len(failures)))
    sys.exit(1)
print("Все проверки пройдены.")
