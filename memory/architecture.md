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
