import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Paths
DB_PATH    = BASE_DIR / "data" / "watson.db"
ARCHIVE_DIR = BASE_DIR / "data" / "archive"
DEPLOY_DIR = BASE_DIR / "deploy"
DOCS_DIR   = BASE_DIR / "docs"

# GitHub
GITHUB_REPO  = os.getenv("GITHUB_REPO")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Vercel
VERCEL_DEPLOY_HOOK = os.getenv("VERCEL_DEPLOY_HOOK")

# Watson bot → sends the briefing URL to Bill.
# Falls back to TELEGRAM_BOT_TOKEN so existing .env files work without change.
WATSON_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
WATSON_CHAT_ID   = os.getenv("WATSON_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID")

# Legacy aliases (bot.py and other modules import these directly)
TELEGRAM_BOT_TOKEN = WATSON_BOT_TOKEN
TELEGRAM_CHAT_ID   = WATSON_CHAT_ID

# Jenny bot → briefing-page buttons POST directly from JS; this is the backend fallback.
JENNY_BOT_TOKEN = os.getenv("JENNY_BOT_TOKEN")
JENNY_CHAT_ID   = os.getenv("JENNY_CHAT_ID")

# Kit (ConvertKit)
KIT_API_KEY = os.getenv("KIT_API_KEY")

# Briefing schedule
BRIEFING_HOUR   = int(os.getenv("BRIEFING_HOUR",   "6"))

# Freshness window for the briefing filter (set FRESHNESS_DAYS=2 in .env to tighten)
FRESHNESS_DAYS  = int(os.getenv("FRESHNESS_DAYS",  "7"))

# Warn on missing required values
_REQUIRED = {
    "GITHUB_REPO":         GITHUB_REPO,
    "GITHUB_TOKEN":        GITHUB_TOKEN,
    "VERCEL_DEPLOY_HOOK":  VERCEL_DEPLOY_HOOK,
    "WATSON_BOT_TOKEN":    WATSON_BOT_TOKEN,
    "WATSON_CHAT_ID":      WATSON_CHAT_ID,
    "JENNY_BOT_TOKEN":     JENNY_BOT_TOKEN,
    "JENNY_CHAT_ID":       JENNY_CHAT_ID,
}

_missing = [name for name, val in _REQUIRED.items() if not val]
if _missing:
    print(f"[watson] WARNING: missing .env values: {', '.join(_missing)}")
