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


def get_token_status() -> dict:
    """Non-alerting variant of check_token_expiry() for the dashboard's
    GET /api/cat/social/status — same debug_token call, but returns a dict
    instead of sending Telegram messages."""
    if not is_configured():
        return {"configured": False}
    try:
        resp = requests.get(
            "https://graph.facebook.com/debug_token",
            params={"input_token": FB_ACCESS_TOKEN, "access_token": FB_ACCESS_TOKEN},
            timeout=10,
        )
        data = resp.json().get("data", {})
        expires_at = data.get("expires_at", 0)
        if not data.get("is_valid", False):
            return {"configured": True, "valid": False, "ig_configured": bool(IG_USER_ID)}
        days_remaining = None if expires_at == 0 else round((expires_at - time.time()) / 86400)
        return {
            "configured": True,
            "valid": True,
            "days_remaining": days_remaining,
            "ig_configured": bool(IG_USER_ID),
        }
    except Exception as e:
        return {"configured": True, "valid": False, "error": str(e), "ig_configured": bool(IG_USER_ID)}


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
            video_path TEXT,
            status TEXT NOT NULL DEFAULT 'approved',
            scheduled_time DATETIME NOT NULL,
            posted_time DATETIME,
            fb_error TEXT,
            ig_error TEXT,
            fb_video_id TEXT,
            ig_creation_id TEXT,
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


def add_to_queue(
    text: str,
    scheduled_time: datetime,
    platform: str = "facebook",
    image_path: str | None = None,
    video_path: str | None = None,
) -> int:
    """image_path and video_path are mutually exclusive — a queued post is
    either a photo/text post or a scheduled Sermon Shots clip, never both."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO church_social_queue (platform, text, image_path, video_path, scheduled_time)
           VALUES (?, ?, ?, ?, ?)""",
        (platform, text, image_path, video_path, scheduled_time.strftime("%Y-%m-%d %H:%M:%S")),
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


def post_to_facebook_reel(text: str, video_url: str) -> dict:
    """Facebook Reels publishing flow — distinct from post_to_facebook()'s
    /photos endpoint, since a Sermon Shots clip is a video, not an image.

    UNVERIFIED against a real Meta account (same caveat as
    jobs/church_social/sermonshots.py's documented spec-vs-reality gaps —
    this cannot be live-tested until Phase 0 tokens exist). Per Meta's
    Reels Publishing API docs as of this writing: a two-call
    start/finish flow around /{page_id}/video_reels, with the upload
    itself done by pointing the returned upload_url at video_url (a
    hosted-file upload rather than streaming raw bytes) via a `file_url`
    header — confirm this against live docs and a real test post before
    trusting it once Phase 0 unblocks testing.
    """
    start = requests.post(
        f"{GRAPH_API}/{FB_PAGE_ID}/video_reels",
        data={"upload_phase": "start", "access_token": FB_ACCESS_TOKEN},
        timeout=30,
    ).json()
    video_id = start.get("video_id")
    upload_url = start.get("upload_url")
    if not video_id or not upload_url:
        return {"error": {"message": "video_reels start phase failed"}, "raw": start}

    upload = requests.post(
        upload_url,
        headers={
            "Authorization": f"OAuth {FB_ACCESS_TOKEN}",
            "file_url": video_url,
        },
        timeout=60,
    )
    if not upload.ok:
        return {"error": {"message": f"upload phase failed: {upload.status_code}"}, "raw": upload.text}

    return requests.post(
        f"{GRAPH_API}/{FB_PAGE_ID}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": text,
            "access_token": FB_ACCESS_TOKEN,
        },
        timeout=30,
    ).json()


def start_instagram_reel(text: str, video_url: str) -> dict:
    """Creates the IG Reels media container. Instagram's video processing
    is asynchronous (unlike the image container→publish flow in
    post_to_instagram(), which completes in one round trip) — the caller
    stores the returned creation_id and polls check_instagram_reel_status()
    on later cron ticks rather than publishing immediately.

    UNVERIFIED — see post_to_facebook_reel()'s docstring."""
    if not IG_USER_ID:
        return {"error": {"message": "CHURCH_IG_USER_ID is not configured."}}

    return requests.post(
        f"{GRAPH_API}/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": text,
            "access_token": FB_ACCESS_TOKEN,
        },
        timeout=30,
    ).json()


def check_instagram_reel_status(creation_id: str) -> str:
    """Returns the container's status_code: IN_PROGRESS, FINISHED, or
    ERROR (per Meta's documented container states)."""
    resp = requests.get(
        f"{GRAPH_API}/{creation_id}",
        params={"fields": "status_code", "access_token": FB_ACCESS_TOKEN},
        timeout=15,
    ).json()
    return resp.get("status_code", "ERROR")


def publish_instagram_reel(creation_id: str) -> dict:
    return requests.post(
        f"{GRAPH_API}/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": FB_ACCESS_TOKEN},
        timeout=30,
    ).json()


# A stuck 'processing' row (IG container never reaches FINISHED/ERROR) is
# failed out after this long rather than polling forever.
_PROCESSING_TIMEOUT_HOURS = 2


def _clip_public_url(video_path: str) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT serve_token FROM sermonshots_clips WHERE local_path=?", (video_path,)
        ).fetchone()
    if not row:
        return None
    return f"{PUBLIC_BASE_URL}/church_social/clip/{row[0]}"


def _advance_processing_posts():
    """Poll every 'processing' row's IG container (video posts only —
    Facebook's Reels flow completes within run_due_posts' single pass) and
    finalize or time it out."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, ig_creation_id, created_at FROM church_social_queue WHERE status='processing'"
    ).fetchall()
    conn.close()

    for row in rows:
        post_id, creation_id, created_at = row["id"], row["ig_creation_id"], row["created_at"]
        if not creation_id:
            continue

        age_hours = (datetime.now() - datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        status_code = check_instagram_reel_status(creation_id)

        conn = sqlite3.connect(DB_PATH)
        if status_code == "FINISHED":
            result = publish_instagram_reel(creation_id)
            if "id" in result:
                conn.execute(
                    "UPDATE church_social_queue SET status='posted', posted_time=? WHERE id=?",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), post_id),
                )
                print(f"Posted church social clip #{post_id} (Instagram Reel)")
            else:
                conn.execute(
                    "UPDATE church_social_queue SET status='failed', ig_error=? WHERE id=?",
                    (str(result), post_id),
                )
                print(f"Failed to publish church social clip #{post_id}: {result}")
        elif status_code == "ERROR":
            conn.execute(
                "UPDATE church_social_queue SET status='failed', ig_error=? WHERE id=?",
                ("Instagram container returned ERROR", post_id),
            )
            print(f"Failed church social clip #{post_id}: Instagram container errored")
        elif age_hours >= _PROCESSING_TIMEOUT_HOURS:
            conn.execute(
                "UPDATE church_social_queue SET status='failed', ig_error=? WHERE id=?",
                (f"Instagram processing timed out after {_PROCESSING_TIMEOUT_HOURS}h", post_id),
            )
            print(f"Failed church social clip #{post_id}: Instagram processing timed out")
        conn.commit()
        conn.close()


def _run_due_video_post(post_id: int, platform: str, text: str, video_path: str) -> None:
    """Video rows take the Reels path instead of post_to_facebook()/
    post_to_instagram(). Facebook's flow is treated as completing within
    this one call; Instagram's is asynchronous, so a 'both' post can land
    in 'processing' with Facebook already posted and Instagram still
    pending — ig_creation_id carries it to _advance_processing_posts()."""
    video_url = _clip_public_url(video_path)
    if not video_url:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE church_social_queue SET status='failed', fb_error=? WHERE id=?",
                ("No serve_token found for this clip's video_path", post_id),
            )
        print(f"Failed church social clip #{post_id}: no serve_token for {video_path}")
        return

    fb_result = ig_start = None
    fb_ok = True
    ig_pending = False

    if platform in ("facebook", "both"):
        fb_result = post_to_facebook_reel(text, video_url)
        fb_ok = "video_id" in fb_result or "id" in fb_result

    if platform in ("instagram", "both"):
        ig_start = start_instagram_reel(text, video_url)
        creation_id = ig_start.get("id")
        ig_pending = bool(creation_id)

    with sqlite3.connect(DB_PATH) as conn:
        if ig_pending:
            conn.execute(
                "UPDATE church_social_queue SET status='processing', ig_creation_id=?, fb_error=? WHERE id=?",
                (ig_start.get("id"), None if fb_ok else str(fb_result), post_id),
            )
            print(f"Church social clip #{post_id}: Instagram processing, Facebook {'ok' if fb_ok else 'failed'}")
        elif fb_ok and platform == "facebook":
            conn.execute(
                "UPDATE church_social_queue SET status='posted', posted_time=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), post_id),
            )
            print(f"Posted church social clip #{post_id} (Facebook Reel)")
        else:
            conn.execute(
                "UPDATE church_social_queue SET status='failed', fb_error=?, ig_error=? WHERE id=?",
                (
                    None if fb_ok else str(fb_result),
                    str(ig_start) if platform in ("instagram", "both") else None,
                    post_id,
                ),
            )
            print(f"Failed church social clip #{post_id}: fb={fb_result} ig={ig_start}")


def run_due_posts():
    """Check for due posts and fire them to every platform the row targets."""
    _advance_processing_posts()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    due = conn.execute(
        "SELECT id, platform, text, image_path, video_path FROM church_social_queue "
        "WHERE status='approved' AND scheduled_time <= ?",
        (now,),
    ).fetchall()
    conn.close()

    for post_id, platform, text, image_path, video_path in due:
        if video_path:
            _run_due_video_post(post_id, platform, text, video_path)
            continue

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
