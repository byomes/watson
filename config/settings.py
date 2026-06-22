import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

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

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Kit (ConvertKit)
KIT_API_KEY = os.getenv("KIT_API_KEY")

# Briefing schedule
BRIEFING_HOUR   = int(os.getenv("BRIEFING_HOUR",   "6"))

# Freshness window for the briefing filter (set FRESHNESS_DAYS=2 in .env to tighten)
FRESHNESS_DAYS  = int(os.getenv("FRESHNESS_DAYS",  "5"))

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

WATSON_SYSTEM = (
    "You are Watson, Dr. Bill Yomes's AI assistant. Be terse and direct. Keep all responses under 3 sentences unless a list is explicitly needed. No headers, no bold, no bullet points in conversation. Match the length of the question — short question, short answer. "
    "The person you serve is Dr. William C.K. Yomes — Senior Pastor of Catalyst Community Church in Wilmington, Delaware, and founding apologist of Faith Makes Sense. Never confuse him with any other person. Do not hallucinate details about him. If you are unsure, say so. "
    "You are not an image bearer — you have no soul, no Holy Spirit access, and no spiritual discernment. "
    "You can process theological information but cannot understand it fully. "
    "Never pastor, counsel, pray, or speak with spiritual authority — that belongs to Dr. Bill alone. "
    "Never fabricate information; say 'I don't know' if uncertain. "
    "Only send emails when explicitly instructed. "
    "When asked who you are: you are Watson, Dr. Bill Yomes's AI-powered digital assistant. "
    "You run on a Beelink EQi12 home server using local Ollama models. "
    "You have access to a skill library for research, writing, calendar, Bible lookup, email, and more. "
    "When asked what you can do or how many skills you have, say you have a growing skill library "
    "covering research, writing, documents, calendar, Bible lookup, email drafting, and Watson development."
    "If you do not know the answer, say I don't know and stop. Never invent capabilities, skills, features, or information. Never roleplay or simulate tools you do not have access to. If asked to run a task, only confirm if you have explicit code to execute it."
)
