import os
import sqlite3
import time
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Post at 9am on Mon, Wed, Fri, Sat (weekday numbers: 0=Mon, 2=Wed, 4=Fri, 5=Sat)
SLOT_DAYS = [0, 2, 4, 5]
SLOT_HOUR = 9


def check_token_expiry():
    try:
        resp = requests.get(
            "https://graph.facebook.com/debug_token",
            params={"input_token": FB_ACCESS_TOKEN, "access_token": FB_ACCESS_TOKEN},
            timeout=10,
        )
        data = resp.json().get("data", {})
        expires_at = data.get("expires_at", 0)

        if not data.get("is_valid", False):
            raise ValueError("token marked invalid by API")

        if expires_at == 0:
            print("Facebook token check: OK (non-expiring token)")
            return

        days_remaining = round((expires_at - time.time()) / 86400)
        expiry_date = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d")

        if days_remaining <= 0:
            raise ValueError(f"token expired {abs(days_remaining)} days ago")

        if days_remaining <= 7:
            msg = (
                f"⚠️ Facebook token expires in {days_remaining} days. "
                "Renew at developers.facebook.com/tools/explorer"
            )
            print(f"Facebook token check: {days_remaining} days remaining — Telegram warning sent")
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                timeout=10,
            )
        else:
            print(f"Facebook token check: OK ({days_remaining} days remaining, expires {expiry_date})")

    except Exception as e:
        print(f"Facebook token check failed: {e}")
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": "🚨 Facebook token is expired or invalid. Posts will not go out.",
                },
                timeout=10,
            )
        except Exception:
            pass


def init_db():
    """Create facebook_queue table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facebook_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            summary TEXT,
            url TEXT,
            draft_text TEXT,
            status TEXT DEFAULT 'pending',
            scheduled_time DATETIME,
            posted_time DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def next_available_slot():
    """Find the next open Mon/Wed/Fri/Sat at 9am slot with no existing post."""
    conn = sqlite3.connect(DB_PATH)
    booked = [
        row[0] for row in conn.execute(
            "SELECT scheduled_time FROM facebook_queue WHERE status IN ('approved', 'posted')"
        ).fetchall()
    ]
    conn.close()

    booked_times = set(booked)
    now = datetime.now()

    for days_ahead in range(28):
        candidate_date = now + timedelta(days=days_ahead)
        if candidate_date.weekday() not in SLOT_DAYS:
            continue
        slot = candidate_date.replace(hour=SLOT_HOUR, minute=0, second=0, microsecond=0)
        if slot > now and slot.strftime("%Y-%m-%d %H:%M:%S") not in booked_times:
            return slot

    return None


def add_to_queue(title, summary, url, draft_text):
    """Add an approved post to the queue with the next available slot."""
    slot = next_available_slot()
    if not slot:
        return None

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO facebook_queue (title, summary, url, draft_text, status, scheduled_time)
           VALUES (?, ?, ?, ?, 'approved', ?)""",
        (title, summary, url, draft_text, slot.strftime("%Y-%m-%d %H:%M:%S"))
    )
    post_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return post_id, slot


def post_to_facebook(text, image_path=None):
    """Post to the Faith Makes Sense Facebook page.
    If image_path is provided and exists, posts as a photo with caption.
    Otherwise falls back to a plain text/link post.
    """
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as img_file:
            response = requests.post(
                f"https://graph.facebook.com/v25.0/{FB_PAGE_ID}/photos",
                data={"caption": text, "access_token": FB_ACCESS_TOKEN},
                files={"source": img_file},
                timeout=30,
            )
        return response.json()

    response = requests.post(
        f"https://graph.facebook.com/v25.0/{FB_PAGE_ID}/feed",
        data={"message": text, "access_token": FB_ACCESS_TOKEN},
        timeout=30,
    )
    return response.json()


def run_due_posts():
    """Check for due posts and fire them."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    due = conn.execute(
        "SELECT id, draft_text, image_path FROM facebook_queue "
        "WHERE status='approved' AND scheduled_time <= ?",
        (now,)
    ).fetchall()
    conn.close()

    for post_id, draft_text, image_path in due:
        result = post_to_facebook(draft_text, image_path)
        conn = sqlite3.connect(DB_PATH)
        if "id" in result or "post_id" in result:
            conn.execute(
                "UPDATE facebook_queue SET status='posted', posted_time=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), post_id)
            )
            print(f"Posted ID {post_id}: {result.get('id') or result.get('post_id')}")
        else:
            print(f"Failed ID {post_id}: {result}")
        conn.commit()
        conn.close()


if __name__ == "__main__":
    check_token_expiry()
    init_db()
    run_due_posts()
