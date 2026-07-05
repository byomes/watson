# Watson

Personal AI assistant system for Bill Yomes — an orchestrated ecosystem of jobs, hardware, and interfaces. Watson runs jobs, not agents or bots.

Runs always-on on a Beelink EQi12 home server (Linux Mint XFCE). Primary interfaces: Watson Dashboard (`watson.tail0243ff.ts.net`, port 5200) and Telegram. Current major systems:

1. **ARC reader program** — signup, commitment tracking, manuscript access
2. **Writing Room** — private partner community hub
3. **Dev Loop** — Ollama-driven code generation, triggered via Telegram
4. **Congregation management** — pastoral CRM, connect cards, attendance, shepherding reports
5. **Content pipeline** — sermon-to-blog/social pipeline, daily briefing

## Setup

```bash
pip install -r requirements.txt
cp .env .env.local  # fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

## Stack

Python 3.11 · SQLite · feedparser · BeautifulSoup · Jinja2 · GitPython · python-telegram-bot · Whisper · cron
