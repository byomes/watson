# Watson

Watson is Bill's personal research and creative intelligence agent, running always-on on an HP Stream home server (Linux Mint XFCE).

## Three Core Jobs

### 1. Daily Research Briefing
Fetches and digests content from configured RSS/web sources, summarizes key developments, and renders a daily briefing using Jinja2 templates. Delivered via Telegram and committed to the library via GitPython.

### 2. Thought Library
A structured personal knowledge base. Captures notes, article excerpts, and tagged ideas into SQLite. Supports search and recall. Content committed to git automatically.

### 3. Telegram Voice Capture
Listens for voice messages via the Telegram bot. Transcribes audio using Whisper, extracts key thoughts, and stores them in the library.

## Stack

- **Runtime**: Python 3.11
- **Database**: SQLite (`data/watson.db`)
- **Feed parsing**: feedparser + BeautifulSoup
- **Templating**: Jinja2
- **Version control**: GitPython
- **Messaging**: python-telegram-bot
- **Transcription**: Whisper
- **Scheduling**: cron (system) + schedule (in-process)

## Project Layout

```
watson/
├── config/          # sources.yaml (feeds), settings.py (env/config loading)
├── core/            # shared utilities, DB access, base classes
├── briefing/        # daily briefing pipeline + Jinja2 templates
├── library/         # thought library: ingest, search, storage
├── telegram/        # bot handler: commands, voice message pipeline
├── cron/            # cron job scripts
├── data/            # SQLite DB and archive (gitignored)
└── deploy/          # server setup scripts, systemd units
```

## Key Rules

- **Nothing goes to Broadcaster without Bill's explicit approval.** Watson prepares and stages content; Bill reviews and triggers any outbound distribution.
- Secrets live in `.env` only — never hardcoded.
- `data/` is local-only (gitignored). Schema migrations go in `core/`.

## Development Machines

- **Desktop**: `D:\OneDrive\Claude\agents\watson`
- **Laptop**: `C:\Users\billy\OneDrive\Claude\agents\watson`
- Both sync via OneDrive. Production runs on the HP Stream home server (Linux Mint XFCE).
