"""jobs/givebutter/monthly_update.py -- monthly ask for new FMS giving-email
content, and storage for the answer.

On the first Monday of every month, Watson asks Bill (via Telegram) whether
there's anything new to fold into this month's FMS donor thank-you emails
(jobs/givebutter/templates.py) -- a book launch, a ministry update, anything
worth mentioning. Bill's reply is threaded back here via
jobs/telegram/pending.py (action_type "fms_giving_update", routed in
bot.py's _route_tg_pending_reply).

If Bill has nothing new that month (replies "skip"/"no"/"nothing new", or
never replies), no row is stored for that month and templates.py falls back
to a standard, evergreen "thank you for your gift" paragraph with no
specific campaign mention -- see templates.py's _STANDARD_PARAGRAPH.

Storage: fms_giving_updates table in data/donors.db (co-located with the
rest of the Givebutter donor/transaction data this feeds), one row per
calendar month (YYYY-MM key).

Cron (runs daily; no-ops on every day that isn't the first Monday of the
month -- see _is_first_monday. Same "gate in code, not in cron syntax"
convention as everywhere else in this codebase, since cron's day-of-month +
day-of-week fields OR together rather than AND, so there's no single cron
expression for "first Monday"):
  0 8 * * * PYTHONPATH=/home/billyomes/watson \
    /home/billyomes/watson/venv/bin/python -m jobs.givebutter.monthly_update \
    >> /home/billyomes/watson/logs/givebutter_monthly_update.log 2>&1
"""
import logging
import sqlite3
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.telegram.pending import store_pending_action

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "donors.db"
LOG_PATH = BASE_DIR / "logs" / "givebutter_monthly_update.log"

log = logging.getLogger(__name__)

_SKIP_WORDS = {"skip", "no", "nothing", "none", "nothing new", "nope", "n/a", "na"}


def _bootstrap() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fms_giving_updates (
            month       TEXT PRIMARY KEY,
            update_text TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


_bootstrap()


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def get_current_update() -> str | None:
    """This month's update text, or None if Bill hasn't shared anything new
    (or replied skip) this month -- templates.py falls back to the standard
    paragraph in that case."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT update_text FROM fms_giving_updates WHERE month = ?", (_current_month(),)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def save_update(text: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO fms_giving_updates (month, update_text) VALUES (?, ?)
           ON CONFLICT(month) DO UPDATE SET update_text = excluded.update_text,
                                             created_at = datetime('now')""",
        (_current_month(), text),
    )
    conn.commit()
    conn.close()


def _is_first_monday(d: date) -> bool:
    return d.weekday() == 0 and d.day <= 7


def ask_for_update() -> None:
    """Cron entry point. Sends Bill the monthly ask and stores a pending
    action so his reply threads back to handle_reply() below."""
    today = date.today()
    if not _is_first_monday(today):
        log.info("Not the first Monday of the month -- nothing to do.")
        return

    text = (
        "It's the first Monday of the month -- anything new to share in this "
        "month's FMS giving emails? A launch update, a ministry note, anything "
        "worth telling donors about.\n\n"
        "Reply to this message with what you'd like included, or reply \"skip\" "
        "and I'll use the standard thank-you. - Watson"
    )
    if vacation_gate("normal", "jobs.givebutter.monthly_update", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("no Telegram credentials -- cannot send monthly ask")
        return

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        resp.raise_for_status()
        tg_msg_id = resp.json().get("result", {}).get("message_id")
    except Exception as exc:
        log.error("monthly update ask failed to send: %s", exc)
        return

    if tg_msg_id:
        store_pending_action("fms_giving_update", tg_msg_id, {})
        log.info("Monthly FMS giving-email update requested.")


def handle_reply(payload: dict, text: str) -> str:
    """Called from bot.py's _route_tg_pending_reply for action_type
    "fms_giving_update". Returns the confirmation message to send back."""
    stripped = text.strip()
    if stripped.lower() in _SKIP_WORDS:
        return "Got it -- I'll use the standard thank-you for this month's giving emails. - Watson"
    save_update(stripped)
    return "Got it -- I'll fold that into this month's FMS giving emails. - Watson"


if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )
    ask_for_update()
