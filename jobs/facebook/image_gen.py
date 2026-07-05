import os
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
IMAGE_DIR = os.path.expanduser("~/watson/data/facebook_images")

TELEGRAM_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

SLOT_DAYS = [0, 2, 4, 5]  # Mon, Wed, Fri, Sat
SLOT_HOUR = 9


def generate_image(prompt: str, post_id: int, width=1080, height=1080) -> str:
    os.makedirs(IMAGE_DIR, exist_ok=True)
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true"

    req = urllib.request.Request(url, headers={"User-Agent": "Watson/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    out_path = os.path.join(IMAGE_DIR, f"fb_{post_id}.jpg")
    with open(out_path, "wb") as f:
        f.write(data)

    return out_path


def next_available_slot():
    conn = sqlite3.connect(DB_PATH)
    booked = [
        row[0] for row in conn.execute(
            "SELECT scheduled_time FROM facebook_queue WHERE status IN ('approved', 'posted')"
        ).fetchall()
    ]
    conn.close()

    booked_times = set(booked)
    now = datetime.now()

    for days_ahead in range(60):
        candidate_date = now + timedelta(days=days_ahead)
        if candidate_date.weekday() not in SLOT_DAYS:
            continue
        slot = candidate_date.replace(hour=SLOT_HOUR, minute=0, second=0, microsecond=0)
        if slot > now and slot.strftime("%Y-%m-%d %H:%M:%S") not in booked_times:
            return slot

    return None


def _review_keyboard(post_id: int) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"fb_img_approve:{post_id}"},
            {"text": "\U0001F504 Regenerate", "callback_data": f"fb_img_regen:{post_id}"},
            {"text": "❌ Discard", "callback_data": f"fb_img_discard:{post_id}"},
        ]]
    }


def send_for_review(post_id: int, image_path: str, title: str, draft_text: str) -> bool:
    """Send the generated image to Telegram with Approve/Regenerate/Discard buttons."""
    import json
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    caption = f"\U0001F4F0 {title}\n\n{draft_text[:800]}"
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "reply_markup": json.dumps(_review_keyboard(post_id)),
                },
                files={"photo": f},
                timeout=30,
            )
        return resp.ok
    except Exception:
        return False


def queue_post(title: str, summary: str, url: str, draft_text: str, image_prompt: str):
    """Create a pending queue row, generate the image, and send it to
    Telegram for review. Status stays 'pending' until Bill approves —
    it will NOT be picked up by facebook_post.py until then.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO facebook_queue (title, summary, url, draft_text, image_prompt, status)
           VALUES (?, ?, ?, ?, ?, 'pending')""",
        (title, summary, url, draft_text, image_prompt)
    )
    post_id = cursor.lastrowid
    conn.commit()
    conn.close()

    image_path = generate_image(image_prompt, post_id)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE facebook_queue SET image_path = ? WHERE id = ?", (image_path, post_id))
    conn.commit()
    conn.close()

    send_for_review(post_id, image_path, title, draft_text)
    return post_id, image_path


def regenerate_image(post_id: int):
    """Re-run generation using the stored prompt, send a fresh review message."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT title, draft_text, image_prompt FROM facebook_queue WHERE id=?", (post_id,)
    ).fetchone()
    conn.close()

    if not row:
        return None
    title, draft_text, image_prompt = row

    image_path = generate_image(image_prompt, post_id)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE facebook_queue SET image_path = ? WHERE id = ?", (image_path, post_id))
    conn.commit()
    conn.close()

    send_for_review(post_id, image_path, title, draft_text)
    return image_path


def approve_post(post_id: int):
    """Mark a pending post approved and assign it the next open slot."""
    slot = next_available_slot()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE facebook_queue SET status='approved', scheduled_time=? WHERE id=?",
        (slot.strftime("%Y-%m-%d %H:%M:%S") if slot else None, post_id)
    )
    conn.commit()
    conn.close()
    return slot


def discard_post(post_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE facebook_queue SET status='discarded' WHERE id=?", (post_id,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    pid, img = queue_post(
        title="Test Post",
        summary="Manual test of image_gen.py review flow",
        url="",
        draft_text="This is a test post generated by Watson.",
        image_prompt="a lighthouse on a stormy coast, apologetics theme, warm light",
    )
    print(f"Queued post {pid} as PENDING, sent to Telegram for review, image at {img}")
