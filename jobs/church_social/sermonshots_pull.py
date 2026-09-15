"""jobs/church_social/sermonshots_pull.py — pulls newly-finished clips from
Sermon Shots into a local review queue.

Bill still uploads/trims the sermon on sermonshots.com by hand every week
(that stays manual — an editorial judgment call, not automated). This job
picks up from there: it checks the most recent videos in his Sermon Shots
account, downloads any clip Sermon Shots has finished rendering that Watson
hasn't already pulled, and stores it locally for review. It does NOT post
anything anywhere — that's a separate, not-yet-built step (needs video/
Reels support in jobs/church_social/social.py, itself blocked on the
church's own Facebook/Instagram Meta app setup, unrelated to Sermon Shots).

Cron: every 30 min.
    */30 * * * * PYTHONPATH=/home/billyomes/watson python3 jobs/church_social/sermonshots_pull.py
"""
import os
import secrets
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate
from jobs.church_social.sermonshots import get_clips, is_configured, list_videos, download_clip

load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
CLIP_DIR = Path(os.path.expanduser("~/watson/data/sermonshots_clips"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# How many of the most recent Sermon Shots videos to check each run — wide
# enough to catch a slow week (clips sometimes finish rendering over an hour
# or more after upload, per observed createdAt spread on a real video) plus
# a backlog, without scanning the whole 26+ video history every 30 minutes.
_RECENT_VIDEOS_TO_CHECK = 8


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sermonshots_clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sermonshots_clip_id INTEGER NOT NULL UNIQUE,
            sermonshots_video_id INTEGER NOT NULL,
            video_name TEXT,
            local_path TEXT NOT NULL,
            source_url TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            serve_token TEXT,
            queued_post_id INTEGER,
            pulled_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def _known_clip_ids(conn) -> set[int]:
    return {row[0] for row in conn.execute("SELECT sermonshots_clip_id FROM sermonshots_clips").fetchall()}


def run() -> int:
    """Returns the number of newly-pulled clips."""
    if not is_configured():
        print("Sermon Shots not configured (SERMONSHOTS_API_KEY unset) — skipping.")
        return 0

    init_db()
    CLIP_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    known_ids = _known_clip_ids(conn)
    conn.close()

    pulled = []  # (video_name, clip_id)
    for video in list_videos(limit=_RECENT_VIDEOS_TO_CHECK):
        video_id = video.get("id")
        video_name = video.get("name") or f"Video {video_id}"
        try:
            clips = get_clips(video_id)
        except requests.RequestException as e:
            print(f"Sermon Shots: failed to fetch clips for video {video_id}: {e}")
            continue

        for clip in clips:
            clip_id = clip.get("id")
            if not clip_id or clip_id in known_ids:
                continue

            file_url = (clip.get("file") or {}).get("publicUrl")
            if not file_url:
                print(f"Sermon Shots: clip {clip_id} has no downloadable file yet — skipping this run.")
                continue

            local_path = CLIP_DIR / f"{clip_id}.mp4"
            try:
                download_clip(file_url, str(local_path))
            except requests.RequestException as e:
                print(f"Sermon Shots: failed to download clip {clip_id}: {e}")
                continue

            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                """INSERT INTO sermonshots_clips
                   (sermonshots_clip_id, sermonshots_video_id, video_name, local_path, source_url, serve_token)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (clip_id, video_id, video_name, str(local_path), file_url, secrets.token_urlsafe(16)),
            )
            conn.commit()
            conn.close()

            known_ids.add(clip_id)
            pulled.append((video_name, clip_id))
            print(f"Sermon Shots: pulled clip {clip_id} from '{video_name}'")

    if pulled:
        by_video: dict[str, int] = {}
        for video_name, _ in pulled:
            by_video[video_name] = by_video.get(video_name, 0) + 1
        lines = [f"🎬 {len(pulled)} new Sermon Shots clip(s) pulled in — /churchclips to review:"]
        lines += [f"  • {count} from \"{name}\"" for name, count in by_video.items()]
        msg = "\n".join(lines)
        if not vacation_gate("normal", "jobs.church_social.sermonshots_pull", msg):
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                    timeout=10,
                )

    return len(pulled)


if __name__ == "__main__":
    count = run()
    print(f"Pulled {count} new clip(s).")
