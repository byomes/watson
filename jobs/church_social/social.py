"""jobs/church_social/social.py — scheduled posting engine for the church's
own Facebook Page and Instagram Business account.

Deliberately separate from jobs/facebook/facebook_post.py, which posts to
the Faith Makes Sense ministry page (a different Meta asset, different
token) — this never reads or writes facebook_queue, and uses its own
CHURCH_FB_*/CHURCH_IG_* env vars so the two pipelines can't collide.

Instagram's Content Publishing API cannot accept a direct file upload the
way Facebook's /photos endpoint does — it requires a publicly reachable
image_url that Meta's servers fetch at publish time. jobs/church_social/api.py
serves queued images at a token-named path on the dashboard's Flask app
(port 5200), which Tailscale Funnel already proxies to the public internet
in full (same trust model as jobs/exports/export_link.py) — see that
module's api.py for the serving route.

Cron: every 15 min, mirroring facebook_post.py's cadence:
    */15 * * * * PYTHONPATH=/home/billyomes/watson python3 jobs/church_social/social.py
"""
import os
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
IMAGE_DIR = Path(os.path.expanduser("~/watson/data/church_social_images"))

FB_PAGE_ID = os.getenv("CHURCH_FB_PAGE_ID")
FB_ACCESS_TOKEN = os.getenv("CHURCH_FB_ACCESS_TOKEN")
IG_USER_ID = os.getenv("CHURCH_IG_USER_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GRAPH_API = "https://graph.facebook.com/v25.0"
PUBLIC_BASE_URL = "https://watson.tail0243ff.ts.net"


def is_configured() -> bool:
    """False until Phase 0 (Meta app/token setup) is done for the church's own
    accounts — callers should skip quietly rather than alert on missing creds
    that are expected to be missing for a while."""
    return bool(FB_PAGE_ID and FB_ACCESS_TOKEN)


def check_token_expiry():
    if not is_configured():
        return
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
            print("Church social token check: OK (non-expiring token)")
            return

        days_remaining = round((expires_at - time.time()) / 86400)
        expiry_date = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d")

        if days_remaining <= 0:
            raise ValueError(f"token expired {abs(days_remaining)} days ago")

        if days_remaining <= 7:
            msg = (
                f"⚠️ Church Facebook/Instagram token expires in {days_remaining} days. "
                "Renew at developers.facebook.com/tools/explorer"
            )
            print(f"Church social token check: {days_remaining} days remaining — Telegram warning sent")
            if not vacation_gate("system_failure", "jobs.church_social.social.check_token_expiry", msg):
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                    timeout=10,
                )
        else:
            print(f"Church social token check: OK ({days_remaining} days remaining, expires {expiry_date})")

    except Exception as e:
        print(f"Church social token check failed: {e}")
        try:
            invalid_msg = "🚨 Church Facebook/Instagram token is expired or invalid. Posts will not go out."
            if not vacation_gate("system_failure", "jobs.church_social.social.check_token_expiry", invalid_msg):
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": invalid_msg},
                    timeout=10,
                )
        except Exception:
            pass


def init_db():
    """Create church_social_queue table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS church_social_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL CHECK(platform IN ('facebook', 'instagram', 'both')),
            text TEXT NOT NULL,
            image_path TEXT,
            status TEXT NOT NULL DEFAULT 'approved',
            scheduled_time DATETIME NOT NULL,
            posted_time DATETIME,
            fb_error TEXT,
            ig_error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_queued_image(image_bytes: bytes) -> str:
    """Save uploaded image bytes under a random token filename and return the
    local path. The filename doubles as the public token api.py serves it at."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(16)
    path = IMAGE_DIR / f"{token}.jpg"
    path.write_bytes(image_bytes)
    return str(path)


def add_to_queue(text: str, scheduled_time: datetime, platform: str = "facebook", image_path: str | None = None) -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO church_social_queue (platform, text, image_path, scheduled_time)
           VALUES (?, ?, ?, ?)""",
        (platform, text, image_path, scheduled_time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    post_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return post_id


def post_to_facebook(text: str, image_path: str | None = None) -> dict:
    """Posts as a photo with caption if image_path exists, else a plain text post."""
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as img_file:
            response = requests.post(
                f"{GRAPH_API}/{FB_PAGE_ID}/photos",
                data={"caption": text, "access_token": FB_ACCESS_TOKEN},
                files={"source": img_file},
                timeout=30,
            )
        return response.json()

    response = requests.post(
        f"{GRAPH_API}/{FB_PAGE_ID}/feed",
        data={"message": text, "access_token": FB_ACCESS_TOKEN},
        timeout=30,
    )
    return response.json()


def post_to_instagram(text: str, image_path: str | None = None) -> dict:
    """Instagram has no text-only post — an image is mandatory. Uses the
    two-step Graph API container -> publish flow, pointing image_url at the
    public serving route in jobs/church_social/api.py rather than uploading
    the file directly (the IG API doesn't accept multipart uploads)."""
    if not IG_USER_ID:
        return {"error": {"message": "CHURCH_IG_USER_ID is not configured."}}
    if not image_path or not os.path.exists(image_path):
        return {"error": {"message": "Instagram requires an image; none provided."}}

    image_url = f"{PUBLIC_BASE_URL}/church_social/img/{Path(image_path).name}"
    container = requests.post(
        f"{GRAPH_API}/{IG_USER_ID}/media",
        data={"image_url": image_url, "caption": text, "access_token": FB_ACCESS_TOKEN},
        timeout=30,
    ).json()

    creation_id = container.get("id")
    if not creation_id:
        return container

    return requests.post(
        f"{GRAPH_API}/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": FB_ACCESS_TOKEN},
        timeout=30,
    ).json()


def run_due_posts():
    """Check for due posts and fire them to every platform the row targets."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    due = conn.execute(
        "SELECT id, platform, text, image_path FROM church_social_queue "
        "WHERE status='approved' AND scheduled_time <= ?",
        (now,),
    ).fetchall()
    conn.close()

    for post_id, platform, text, image_path in due:
        fb_result = ig_result = None
        fb_ok = ig_ok = True

        if platform in ("facebook", "both"):
            fb_result = post_to_facebook(text, image_path)
            fb_ok = "id" in fb_result or "post_id" in fb_result

        if platform in ("instagram", "both"):
            ig_result = post_to_instagram(text, image_path)
            ig_ok = "id" in ig_result

        conn = sqlite3.connect(DB_PATH)
        if fb_ok and ig_ok:
            conn.execute(
                "UPDATE church_social_queue SET status='posted', posted_time=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), post_id),
            )
            print(f"Posted church social #{post_id} ({platform})")
        else:
            conn.execute(
                "UPDATE church_social_queue SET status='failed', fb_error=?, ig_error=? WHERE id=?",
                (
                    None if fb_ok else str(fb_result),
                    None if ig_ok else str(ig_result),
                    post_id,
                ),
            )
            print(f"Failed church social #{post_id}: fb={fb_result} ig={ig_result}")
        conn.commit()
        conn.close()


if __name__ == "__main__":
    init_db()
    if not is_configured():
        print("Church social not configured yet (CHURCH_FB_PAGE_ID/CHURCH_FB_ACCESS_TOKEN unset) — skipping.")
    else:
        check_token_expiry()
        run_due_posts()
