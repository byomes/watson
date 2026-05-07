import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Paths
DB_PATH = BASE_DIR / "data" / "watson.db"
ARCHIVE_DIR = BASE_DIR / "data" / "archive"
DEPLOY_DIR = BASE_DIR / "deploy"

# GitHub
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Vercel
VERCEL_DEPLOY_HOOK = os.getenv("VERCEL_DEPLOY_HOOK")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Kit (ConvertKit)
KIT_API_KEY = os.getenv("KIT_API_KEY")

# Briefing schedule
BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "6"))

# Warn on missing required values
_REQUIRED = {
    "GITHUB_REPO": GITHUB_REPO,
    "GITHUB_TOKEN": GITHUB_TOKEN,
    "VERCEL_DEPLOY_HOOK": VERCEL_DEPLOY_HOOK,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    "KIT_API_KEY": KIT_API_KEY,
}

_missing = [name for name, val in _REQUIRED.items() if not val]
if _missing:
    print(f"[watson] WARNING: missing .env values: {', '.join(_missing)}")
