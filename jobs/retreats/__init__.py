"""jobs/retreats — discovers pastor-retreat lodging listings and pushes candidates
to the wcky /retreats database. No local DB: Watson only holds the ingest key and
pushes over HTTPS, same shape as Writing Room/bodyrec's Watson-API pattern, just
inverted (Watson calls out to wcky instead of wcky calling in)."""
import os

import requests
from dotenv import load_dotenv

# RETREATS_API_KEY was appended as the last line of ~/watson/.env, and several
# earlier lines in that file (unquoted "<", stray spaces) aren't valid POSIX
# shell syntax — cron's `set -a && . .env && set +a` sourcing silently stops
# before reaching it (confirmed live 2026-08-16: push_listings() got "not set"
# even with the var present in the file). load_dotenv() parses .env directly
# rather than relying on the shell, same pattern already used by
# jobs/transcribe.py, jobs/generate.py, jobs/batch.py — sidesteps the issue
# entirely rather than depending on the rest of .env being shell-safe.
load_dotenv(os.path.expanduser("~/watson/.env"))

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"  # accuracy-sensitive background job — see LLM Stack in WATSON_ARCHITECTURE.md

WCKY_BASE = os.getenv("WCKY_BASE_URL", "https://williamckyomes.com")
RETREATS_API_KEY = os.getenv("RETREATS_API_KEY")

HOME_LOCATION = "Newark, DE"
MAX_DRIVE_HOURS = 8
TARGET_CAPACITY = 5  # 2 adults + 3 teens

# Per-source config: sitemap URL to enumerate candidate pages, plus a keyword
# filter so a directory site's unrelated pages (blog posts, staff bios) don't
# get pushed through Ollama extraction just to be rejected. `None` = no filter
# needed (every post on the site is already a retreat-center listing).
SOURCES = [
    {
        "name": "my-pastor.com",
        "sitemap_url": "https://www.my-pastor.com/ICJNOSdt.xml",
        "keyword_filter": r"retreat|getaway|cabin|sabbatical|lodge|refuge|haven|hospitality",
        "exclude": r"^https://www\.my-pastor\.com/(pastor-vacation|mini-vacations|ministerial-staff-vacations|mens-retreat|pastors-retreat|pastor-retreat-centers)\.html$",
    },
    {
        "name": "pastorgetaways.com",
        "sitemap_url": "https://pastorgetaways.com/post-sitemap.xml",
        "keyword_filter": None,
        "exclude": r"^https://pastorgetaways\.com/?$",
    },
    {
        "name": "hopeforpastorswives.com",
        "sitemap_url": "https://hopeforpastorswives.com/wp-sitemap.xml",
        "keyword_filter": r"retreat|getaway|cabin|lodging|respite",
        "exclude": None,
    },
    # shepherdsfoldministries.com does not resolve (DNS failure, confirmed
    # 2026-08-16). shepherdsfoldministries.org exists but is a bare JS-redirect
    # shell with no real content — can't confirm it's the same ministry.
    # Disabled until the real domain is confirmed with Bill, not guessed.
    #
    # ag.org sits behind a bot-verification JS challenge page (plain requests
    # gets a 403 "Verifying Browser..." interstitial, confirmed 2026-08-16) —
    # the same class of blocker jobs/curator/research.py already solves for
    # romance.io via the existing FlareSolverr container (localhost:8191).
    # Disabled here rather than silently failing every run; wire through
    # FlareSolverr the same way if this source is worth the extra hop — ag.org
    # is a large denominational site, not a purpose-built retreat directory
    # like the other three, so the win is smaller than for the others.
]

_BOT_TOKEN = lambda: os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID = lambda: os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(text: str) -> None:
    token, chat_id = _BOT_TOKEN(), _CHAT_ID()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception:
        pass


def call_ollama(system: str, prompt: str, timeout: int = 90) -> str:
    # temperature=0: structured extraction needs to be deterministic. Confirmed live
    # 2026-08-16 — at default temperature, the same clear listing page flipped between
    # confident=true and confident=false across identical repeated calls (~1 in 4
    # calls), silently dropping a good listing for no reason but sampling luck.
    payload = {
        "model": MODEL, "system": system, "prompt": prompt, "stream": False,
        "options": {"temperature": 0},
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def parse_json(raw: str) -> dict | None:
    import json
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
