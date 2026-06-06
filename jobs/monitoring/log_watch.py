import os
import time
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

REPO = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO / "logs"
DB_PATH = os.getenv("WATSON_DB", str(REPO / "data" / "watson.db"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_recent_errors():
    conn = _db()
    one_hour_ago = datetime.now() - timedelta(hours=1)
    cursor = conn.execute("SELECT * FROM chat_messages WHERE content LIKE '%ERROR%' AND timestamp > ?", (one_hour_ago.isoformat(),))
    errors = [row for row in cursor]
    conn.close()
    return errors

def send_telegram_summary(context: CallbackContext):
    errors = get_recent_errors()
    if not errors:
        context.bot.send_message(chat_id=context.job.context, text="No ERROR messages found in the last hour.")
        return

    summary = "ERROR Summary:\n"
    for error in errors:
        summary += f"{error['timestamp']}: {error['content']}\n"

    context.bot.send_message(chat_id=context.job.context, text=summary)

def main():
    updater = Updater(token=TELEGRAM_BOT_TOKEN)
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler("start", lambda _, __: send_telegram_summary(__)))
    job_queue = updater.job_queue
    job_queue.run_repeating(send_telegram_summary, interval=3600, first=0, context=TELEGRAM_BOT_CHAT_ID)

    load_dotenv()
    TELEGRAM_BOT_CHAT_ID = os.getenv("TELEGRAM_BOT_CHAT_ID")

    updater.start_polling()

def run() -> str:
    """Check Watson logs for errors in the last hour. Returns a summary string."""
    load_dotenv()
    errors = get_recent_errors()
    if not errors:
        return "No errors found in the last hour."
    summary = f"Found {len(errors)} error(s) in the last hour:\n"
    for row in list(errors)[:10]:
        d = dict(row)
        ts = str(d.get("timestamp") or d.get("created_at", ""))
        content = str(d.get("content", ""))[:120]
        summary += f"• {ts}: {content}\n"
    return summary.strip()


if __name__ == "__main__":
    main()