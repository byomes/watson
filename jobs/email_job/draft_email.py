"""
draft_email.py — Watson weekly email drafter.
Runs every Thursday at 7am via cron.
Pulls queued articles, drafts a newsletter email, creates a broadcast in Kit.
Cron: 0 7 * * 4 cd /home/billyomes/watson && python3 -m jobs.email.draft_email >> /home/billyomes/watson/logs/email_draft.log 2>&1
"""

import logging
import os
import requests
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from core.vacation import vacation_gate
load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
KIT_API_SECRET = os.getenv("KIT_API_SECRET")
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


def fetch_and_summarize(article):
    """Fetch article URL, extract text, and summarize with Ollama. Falls back to DB summary on error."""
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            article["url"],
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Watson/1.0)"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)[:3000]
        result = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5:14b",
                "prompt": (
                    "Write 1-2 sentences summarizing this article for a Christian pastor's weekly email newsletter. "
                    f"Be concise and clear.\n\n{text}"
                ),
                "stream": False,
            },
            timeout=60,
        )
        result.raise_for_status()
        return result.json().get("response", "").strip()
    except Exception as exc:
        log.warning("fetch_and_summarize failed for %s: %s", article.get("url"), exc)
        return article.get("summary") or ""


def draft_intro(articles):
    """Draft an intro paragraph using Ollama. Falls back to hardcoded intro on error."""
    try:
        titles = ", ".join(a["title"] for a in articles if a.get("title"))
        result = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5:14b",
                "prompt": (
                    "Write a 2-3 sentence intro paragraph for a Christian pastor's weekly email newsletter. "
                    f"The articles this week cover: {titles}. "
                    "Keep it warm, pastoral, and brief."
                ),
                "stream": False,
            },
            timeout=60,
        )
        result.raise_for_status()
        return result.json().get("response", "").strip()
    except Exception as exc:
        log.warning("draft_intro failed: %s", exc)
        return "Here's what caught my attention this week — articles, podcasts, and resources worth your time."


def build_email_body(articles):
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"Faith & Ideas — {today}"

    intro = draft_intro(articles)
    body_lines = [f"{intro}\n"]

    for article in articles:
        title = article["title"] or "Untitled"
        summary = fetch_and_summarize(article)
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
            "api_secret": KIT_API_SECRET,
            "subject": subject,
            "content": body,
            "description": f"Weekly email draft — {datetime.now().strftime('%Y-%m-%d')}",
            "public": False,
        }
    )
    return response.json()


def notify_telegram(message):
    if vacation_gate("normal", "jobs.email_job.draft_email", message):
        return
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
