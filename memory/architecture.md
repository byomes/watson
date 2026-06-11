# Watson Architecture

## Hardware
- Beelink EQi12, Intel i5 12th gen, 32GB DDR4, 500GB NVMe, Linux Mint XFCE, hostname: watson, user: billyomes, IP: 192.168.1.204, Tailscale IP: 100.117.237.96
- Home network; accessed via SSH and Tailscale
- Managed via Git (github.com/byomes/watson)
- Systemd service: watson-bot.service

## Stack
- Python 3.12, Flask 3.0.3, SQLite, python-telegram-bot 20.7
- httpx MUST stay pinned at 0.25.2 — do not change; it locks python-telegram-bot compatibility
- Ollama local LLM at http://localhost:11434
- Gemini 2.5 Flash via google-genai (GEMINI_API_KEY)
- Claude API via anthropic SDK (ANTHROPIC_API_KEY)
- All imports must be PYTHONPATH-safe: from jobs.x.y import ...

## Codebase Structure
```
/home/billyomes/watson/
  bot/           — Telegram bot (bot.py — OFF LIMITS)
  briefing/      — Daily briefing research pipeline
  config/        — settings.py (central config, all env vars)
  core/          — database.py, fetcher.py, scorer.py, summarizer.py, pipeline.py
  data/          — watson.db, congregation.db, exports/, qr/, chroma/
  jobs/          — all feature jobs (subdirs), plus watcher.py, transcribe.py, cleanup.py, generate.py
  kb/            — archive transcripts (storage only)
  library/       — knowledge base ingest + search
  memory/        — skills.json, core.md, architecture.md, coding/, projects/
  outputs/       — transcripts/raw/, transcripts/clean/, drafts/blog/, drafts/social/
  web/           — Next.js review app (deployed to Vercel)
```

## Dashboard
- Entry: jobs/dashboard/app.py (Flask, port 5200, Tailscale-only)
- Frontend: jobs/dashboard/static/app.js (OFF LIMITS), jobs/dashboard/templates/index.html (OFF LIMITS)
- SSE streaming pattern: _sse_response() and _stream_simple() helpers in app.py
- Auth: FLASK_SECRET_KEY from .env; session-based

## Jobs
Sermon pipeline (PC-side, requires GPU for Whisper):
- jobs/watcher.py — watches SERMON_INCOMING_DIR and SERMON_ARCHIVE_DIR
- jobs/transcribe.py — Whisper large model → outputs/transcripts/raw/
- jobs/cleanup.py — Claude API → outputs/transcripts/clean/
- jobs/generate.py — Claude API → outputs/drafts/blog/ + outputs/drafts/social/

Key other jobs:
- jobs/skillbuilder/router.py — skill dispatch; reads memory/skills.json
- jobs/dev/gemini_coder.py — Gemini build-planning → Claude Code execution pipeline
- jobs/dashboard/app.py — main web dashboard
- jobs/congregation/ — connect cards, attendance, follow-ups
- jobs/email/, jobs/social/, jobs/people/, jobs/reminders/ — feature jobs

## DB Schema

### watson.db (main)
blog_drafts, briefing_items, build_attempts, capability_gaps, chat_messages, chat_sessions,
code_agent_jobs, congregation, connect_cards, email_queue, facebook_queue, gemini_builds,
gmail_inbox, items, memory_coding, memory_core, memory_projects, notes_pending,
pastoral_notes, pending_actions, people, qr_cache, reading_list, rejection_patterns,
reminders, research_archive, research_library, skill_acquisitions, tasks,
telegram_last_message, thought_library, voice_notes, builds

### congregation.db
members, connect_cards, attendance, follow_ups, prayer_requests, next_steps, duplicate_flags

## Key Files
| File | Purpose |
|------|---------|
| config/settings.py | Central config; all env vars; import from here |
| core/database.py | get_connection() for watson.db — use this, not raw sqlite3.connect |
| memory/skills.json | Skill registry; all skills must be registered here |
| jobs/skillbuilder/router.py | Routes incoming requests to skills |
| bot/bot.py | Telegram bot — OFF LIMITS, never modify directly |

## Conventions
- Credentials: always os.environ.get() from .env, never hardcoded
- DB access: use get_connection() from core.database for watson.db; raw sqlite3 only for congregation.db
- New jobs: all go under jobs/ directory
- New skills: go in jobs/, register in memory/skills.json
- Frontend JS: vanilla JS only — no frameworks, no npm, no localStorage/sessionStorage
- Always read a target file before modifying it
- Imports: PYTHONPATH-safe only (from jobs.x.y import ..., from config.settings import ...)
- Telegram: python-telegram-bot 20.7 async patterns

## Off-Limits Files — NEVER modify
- jobs/dashboard/static/app.js
- jobs/dashboard/templates/index.html
- bot/bot.py

## Recent Changes (June 10, 2026)

### New Jobs
- `jobs/time_check.py` — returns current Eastern time. Triggered by "what time is it" via pre-check in app.py and bot.py before skill router.
- `jobs/dadjoke/joke.py` — returns random dad joke. Triggered by "tell me a joke". Registered in memory/skills.json.
- `jobs/reminders/daily_summary.py` — sends active reminder list via Telegram. Cron: 10am, 1:30pm, 5pm Mon-Thu/Sat.
- `jobs/reminders/check_timed.py` — fires timed reminders within ±5 min window. Cron: every 5 min.
- `jobs/dev/gemini_coder.py` — updated. Gemini now outputs plain English descriptions only. Claude Code handles all implementation. build: and debug: prefixes supported.

### New Endpoints
- `POST /api/siri` — receives message from Siri Shortcut, processes through Watson routing, responds via Telegram. Background thread, returns immediately.

### Static Files
- `app.js` renamed to `watson.js`. Flask serves with no-cache headers via custom route to prevent browser caching.

### Gemini Rules (Hardcoded)
- Gemini outputs plain English "what to build" descriptions only
- No code, no file paths, no function names, no JSON, no implementation details
- Claude Code owns all implementation
- Gemini never touches app.js (watson.js), index.html, or bot.py

### Image Search
- `jobs/social/image_search.py` — returns 3 image URLs (not base64). Format: [IMAGE_URL] and [IMAGE_LINK] lines.
- Dashboard renders all 3 inline with Open links.

### Reminders System
- watson.db reminders table: id, title, created_at, reminder_time, status, sort_order, updated_at
- Intake: "remind me [thing]" and "remind me at [time] [thing]" via Telegram and dashboard
- Dashboard reminders tab: edit, mark done, delete, drag reorder
- Summaries: Mon/Tue/Wed/Thu/Sat only. No Fri/Sun.

### Siri Shortcut
- "Hey Siri, Tell Watson" → Dictate Text → POST to http://192.168.1.204:5200/api/siri → Watson responds in Telegram
