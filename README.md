# AI Job Search Agent

An autonomous job-hunting pipeline that collects vacancies from 14+ sources, filters them through deterministic gates, scores the survivors with an LLM, and pushes only the relevant ones to Telegram — where a single tap teaches the agent what to reject next time.

Runs unattended on a daily GitHub Actions cron. Also ships with a Flask dashboard for manual runs and funnel inspection.

---

## Why this exists

Job aggregators optimise for volume, not fit. A daily digest of 300 vacancies is noise — reading it is the same work as searching manually. This agent inverts the flow: it reads everything, throws away most of it, and delivers a handful of cards worth opening.

The hard constraint shaping the design is **cost**. Running an LLM over 300 vacancies a day is expensive, and free-tier quotas are small. So the pipeline is built as a funnel where the LLM is the *last* and *narrowest* stage, not the first.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. COLLECT — 14+ sources, isolated failures                     │
│    RSS/HTML: WorkingNomads · Nodesk · WeWorkRemotely · Jobicy   │
│              Himalayas · Arbeitnow · Upwork                     │
│    ATS APIs: Greenhouse · Lever · Ashby                         │
│    Regional: Djinni · DOU · HabrCareer                          │
│    Inbox:    Gmail IMAP (job-alert emails → LLM extraction)     │
└─────────────────────────────────────────────────────────────────┘
                              ↓  ~300 jobs
┌─────────────────────────────────────────────────────────────────┐
│ 2. DETERMINISTIC GATES — free, no tokens spent                  │
│    geo/jurisdiction · remote-vs-onsite · salary floor           │
│    role & stack blocklists                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. DEDUPLICATION                                                │
│    job_id + SHA-256 fingerprint of normalised (title, company)  │
│    → the same job posted to 4 boards is evaluated once          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. PRESCREEN + FAIR-SHARE QUEUE                                 │
│    cheap keyword score sets ORDER, not verdict                  │
│    (title matches weigh 3x description matches)                 │
│    queue split 50/50 international / regional so one loud       │
│    source cannot eat the whole LLM budget                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓  ~40 jobs — the only tokens spent
┌─────────────────────────────────────────────────────────────────┐
│ 5. LLM EVALUATION — provider chain with automatic failover      │
│    Gemini → OpenRouter → Groq → Cerebras                        │
│    per-provider RPM limits, multi-key rotation, JSON mode       │
│    hard-fail checks + 0-100 scoring against a candidate profile │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 6. PYTHON RE-VALIDATION (scoring.py)                            │
│    the verdict is not trusted blindly — geo and work-format     │
│    blockers are re-checked in code before anything is sent      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 7. ROUTING BY SCORE                                             │
│    <70    → dropped                                             │
│    70–81  → stored silently in SQLite (silent_jobs)             │
│    82+    → Telegram card with inline buttons                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 8. FEEDBACK LOOP                                                │
│    "Not relevant" → reason menu → rejections table →            │
│    recent rejections are injected into the next run's prompt    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Engineering decisions worth pointing at

**The LLM is the last stage, not the first.** Everything cheap runs before anything expensive. Roughly 300 collected vacancies reach the model as ~40 — an order-of-magnitude cut for zero tokens.

**Provider chain instead of a single vendor.** `llm_engine.py` defines four OpenAI-compatible providers, each with its own models, RPM limit and API key. An exhausted quota, a 5xx, or an unset key demotes the provider and the chain continues. `GEMINI_API_KEYS` (plural) accepts a comma-separated list, rotating keys to stack free daily quotas.

**The model's answer is re-validated in code.** An LLM asked for a 0-100 score will occasionally pass a vacancy that violates an explicit hard constraint. `scoring.py` re-runs the deterministic geo and work-format blockers over the verdict, so a hallucinated "remote" never reaches the notification.

**Fair-share queueing.** A naive "top N by prescreen score" queue lets one prolific source monopolise the daily LLM budget. The queue reserves capacity per segment instead.

**Failures are isolated.** Every scraper runs inside its own try/except: a board that changes its HTML degrades the run, it does not end it.

**Deduplication survives re-posting.** Beyond `job_id`, a SHA-256 fingerprint over the normalised title + company catches the same role cross-posted under different IDs.

**CI caches the database.** The GitHub Actions workflow restores `jobs.db` between runs via `actions/cache`. Without it the dedup table is recreated every night and the whole quota is burnt re-evaluating yesterday's vacancies.

---

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11 |
| LLM providers | Gemini, OpenRouter, Groq, Cerebras (OpenAI-compatible clients) |
| Storage | SQLite — `seen_jobs`, `silent_jobs`, `rejections`, `job_states` |
| Delivery | Telegram Bot API (`pyTelegramBotAPI`), inline-keyboard feedback |
| Ingestion | `requests`, `beautifulsoup4`, `feedparser`, IMAP |
| Dashboard | Flask + SSE log streaming, `gunicorn` for deployment |
| Scheduling | GitHub Actions cron (daily), `schedule` for local runs |
| Resilience | `tenacity` retries, per-provider rate limiting |

---

## Repository layout

```
main.py               pipeline orchestration, source registry, fair-share queue
config.py             thresholds, keyword lists, filters, env parsing
llm_engine.py         provider chain, failover, rate limits, JSON parsing
profile.example.md    candidate profile template — copy to profile.md
scoring.py            deterministic re-validation of LLM verdicts
matching.py           profile/vacancy matching helpers
storage.py            SQLite layer, deduplication, rejection history
geo_filter.py         jurisdiction and country gating
location_filter.py    remote / hybrid / on-site classification
telegram_notifier.py  job cards, inline keyboards, callback handling
telegram_bot.py       bot process and command handling
web_app.py            Flask dashboard: status, stats, funnel, live logs
scrapers/             one module per source family
tests/                pytest suite
```

---

## Running it

```bash
git clone https://github.com/kazimirov140-maker/ai-job-search-agent.git
cd ai-job-search-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # fill in your keys
cp profile.example.md profile.md   # describe the candidate the agent screens for
python main.py
```

The dashboard:

```bash
python start_server.py             # then open http://localhost:8080
```

### Configuration

Everything is driven by environment variables — see [.env.example](.env.example) for the full annotated list. The ones that matter most:

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEYS` | Comma-separated keys, rotated to stack free quotas | — |
| `OPENROUTER_API_KEY` / `GROQ_API_KEY` / `CEREBRAS_API_KEY` | Fallback providers; unset = silently skipped | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Notification target | — |
| `GMAIL_EMAIL` / `GMAIL_APP_PASSWORD` | IMAP ingestion of job-alert emails | — |
| `MAX_LLM_JOBS_PER_RUN` | Hard ceiling on the LLM budget per run | `25` |
| `SCORE_THRESHOLD_SILENT` | Store quietly above this score | `70` |
| `SCORE_THRESHOLD_NOTIFY` | Send to Telegram above this score | `82` |
| `REMOTE_ONLY` | Drop anything that is not fully remote | `true` |
| `MIN_SALARY_USD` | Minimum monthly salary; `0` disables the check | `0` |
| `EXTRA_SYNC_FIRST_MARKERS` | Extra sync-first markers, comma-separated | — |

At least one LLM provider key is required; the rest are optional and simply extend the failover chain.

**The candidate profile is data, not code.** `llm_engine.py` loads it from
`profile.md` (git-ignored), falling back to the `profile.example.md` template
shipped here. The prompt that drives every evaluation is therefore editable
without touching Python, and no one's personal details live in version control.

**Personal thresholds stay out of the repository.** Salary floors and the
marker lists that encode one person's working preferences are configuration,
not logic: they live in `.env`, default to "disabled" in code, and extend the
built-in lists at import time. Forking this agent means editing `.env`, not
hunting for someone else's numbers inside the source.

### Scheduled runs

[.github/workflows/job_agent.yml](.github/workflows/job_agent.yml) runs the agent on manual dispatch, with a daily 07:00 UTC schedule that is
commented out until the provider and Telegram secrets are configured.
Provider keys come from repository secrets; non-secret tuning is set as literals in the workflow — an unset GitHub secret expands to an empty string, which used to crash `int()` at import time.

---

## Tests

```bash
pytest tests/
```

Covers geo filtering, the scoring pipeline and prompt behaviour.

---

## Status

Working and in daily use. Actively iterated — the filter lists and scoring weights are tuned from the rejection feedback the bot collects.
