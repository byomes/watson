import os
import sqlite3
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")

# Post at 9am on Mon, Wed, Fri, Sat (weekday numbers: 0=Mon, 2=Wed, 4=Fri, 5=Sat)
SLOT_DAYS = [0, 2, 4, 5]
SLOT_HOUR = 9


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


def post_to_facebook(text):
    """Post text to the Faith Makes Sense Facebook page."""
    response = requests.post(
        f"https://graph.facebook.com/v25.0/{FB_PAGE_ID}/feed",
        data={"message": text, "access_token": FB_ACCESS_TOKEN}
    )
    return response.json()


def run_due_posts():
    """Check for due posts and fire them."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    due = conn.execute(
        "SELECT id, draft_text FROM facebook_queue WHERE status='approved' AND scheduled_time <= ?",
        (now,)
    ).fetchall()
    conn.close()

    for post_id, draft_text in due:
        result = post_to_facebook(draft_text)
        conn = sqlite3.connect(DB_PATH)
        if "id" in result:
            conn.execute(
                "UPDATE facebook_queue SET status='posted', posted_time=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), post_id)
            )
            print(f"Posted ID {post_id}: {result['id']}")
        else:
            print(f"Failed ID {post_id}: {result}")
        conn.commit()
        conn.close()


if __name__ == "__main__":
    init_db()
    run_due_posts()
