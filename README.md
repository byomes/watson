# Watson

Personal research and creative intelligence agent for Bill Yomes.

Runs always-on on an HP Stream home server (Linux Mint XFCE). Three core jobs:

1. **Daily Research Briefing** — digest RSS feeds and web sources into a morning briefing
2. **Thought Library** — structured personal knowledge base with voice capture support
3. **Telegram Voice Capture** — transcribe voice notes via Whisper, store in library

## Setup

```bash
pip install -r requirements.txt
cp .env .env.local  # fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

## Stack

Python 3.11 · SQLite · feedparser · BeautifulSoup · Jinja2 · GitPython · python-telegram-bot · Whisper · cron
