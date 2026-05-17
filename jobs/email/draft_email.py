"""
draft_email.py — Watson weekly email drafter.
Runs every Thursday at 9am via cron.
Pulls queued articles, drafts a newsletter email, creates a broadcast in Kit.
Cron: 0 9 * * 4 cd /home/billyomes/watson && python3 -m jobs.email.draft_email >> /home/billyomes/watson/logs/email_draft.log 2>&1
"""

import logging
import os
import requests
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
KIT_API_KEY = os.getenv("KIT_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

log = logging.getLogger(__name__)

def get_queued_articles():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, title, summary, url FROM email_queue
           WHERE status = 'queued'
           ORDER BY created_at ASC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_articles_used(article_ids, draft_id):
    conn = sqlite3.connect(DB_PATH)
    conn.executemany(
        "UPDATE email_queue SET status='used', used_in_draft=? WHERE id=?",
        [(draft_id, id_) for id_ in article_ids]
    )
    conn.commit()
    conn.close()

def build_email_body(articles):
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"Faith & Ideas — {today}"

    body_lines = [
        f"Here's what caught my attention this week — articles, podcasts, and resources worth your time.\n"
    ]

    for article in articles:
        title = article["title"] or "Untitled"
        summary = (article["summary"] or "")[:300]
        url = article["url"] or ""
        body_lines.append(f"<h3>{title}</h3>")
        if summary:
            body_lines.append(f"<p>{summary}</p>")
        if url:
            body_lines.append(f'<p><a href="{url}">Read more →</a></p>')
        body_lines.append("")

    body_lines.append("<p>— Bill</p>")
    body = "\n".join(body_lines)
    return subject, body

def create_kit_broadcast(subject, body):
    """Create a draft broadcast in Kit (ConvertKit)."""
    response = requests.post(
        "https://api.convertkit.com/v3/broadcasts",
        json={
            "api_secret": KIT_API_KEY,
            "subject": subject,
            "content": body,
            "description": f"Weekly email draft — {datetime.now().strftime('%Y-%m-%d')}",
            "public": False,
        }
    )
    return response.json()

def notify_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    )

def run():
    articles = get_queued_articles()
    if not articles:
        log.info("No articles in email queue — skipping draft.")
        notify_telegram("📧 Thursday email draft: no articles queued. Add some from the briefing.")
        return

    log.info("Drafting weekly email with %d articles.", len(articles))
    subject, body = build_email_body(articles)

    result = create_kit_broadcast(subject, body)

    if "broadcast" in result:
        broadcast_id = result["broadcast"]["id"]
        mark_articles_used([a["id"] for a in articles], str(broadcast_id))
        kit_url = f"https://app.convertkit.com/broadcasts/{broadcast_id}/edit"
        notify_telegram(
            f"📧 <b>Weekly email draft ready</b>\n\n"
            f"Subject: {subject}\n"
            f"{len(articles)} articles included.\n\n"
            f"<a href='{kit_url}'>Edit in Kit →</a>"
        )
        log.info("Kit broadcast created: %s", broadcast_id)
    else:
        error = result.get("message") or str(result)
        notify_telegram(f"📧 Email draft failed: {error}")
        log.error("Kit broadcast failed: %s", result)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run()
