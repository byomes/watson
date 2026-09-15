"""Wire sermonshots_clips into church_social_queue for the /cat/social
"schedule a pulled clip" flow (jobs/church_social/social_web.py,
watson-tools SocialDashboard.tsx's Sermon Clips section).

sermonshots_clips gets:
  - serve_token: random public-facing id (secrets.token_urlsafe(16), same
    convention as social.save_queued_image()'s image filenames) backing
    GET /church_social/clip/<token> in jobs/church_social/api.py -- kept
    separate from the internal autoincrement id so the dashboard/Meta's
    fetch URL doesn't expose a guessable sequential clip id.
  - queued_post_id: set once a clip has been scheduled, pointing at the
    church_social_queue row it became. NULL means "still awaiting review."

church_social_queue gets:
  - video_path: mutually exclusive with the existing image_path -- set
    when the row came from a scheduled clip rather than the compose form.
  - fb_video_id / ig_creation_id: Reels container/session ids, carried
    across cron ticks by run_due_posts() while Instagram's async video
    processing is still in flight (status='processing').

No CHECK constraint exists on church_social_queue.status today, so the
new 'processing' status needs no schema change -- just these columns.

Usage:
  python3 jobs/church_social/migrate_clip_scheduling.py
"""
import os
import secrets
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

conn = sqlite3.connect(DB_PATH)
try:
    clip_cols = {row[1] for row in conn.execute("PRAGMA table_info(sermonshots_clips)").fetchall()}
    if "serve_token" not in clip_cols:
        conn.execute("ALTER TABLE sermonshots_clips ADD COLUMN serve_token TEXT")
        print("  [migrated] sermonshots_clips.serve_token")
    else:
        print("  [exists]   sermonshots_clips.serve_token")

    if "queued_post_id" not in clip_cols:
        conn.execute("ALTER TABLE sermonshots_clips ADD COLUMN queued_post_id INTEGER")
        print("  [migrated] sermonshots_clips.queued_post_id")
    else:
        print("  [exists]   sermonshots_clips.queued_post_id")

    backfilled = 0
    for row in conn.execute("SELECT id FROM sermonshots_clips WHERE serve_token IS NULL").fetchall():
        conn.execute(
            "UPDATE sermonshots_clips SET serve_token=? WHERE id=?",
            (secrets.token_urlsafe(16), row[0]),
        )
        backfilled += 1
    if backfilled:
        print(f"  [migrated] backfilled serve_token on {backfilled} existing clip row(s)")

    clip_indexes = {row[1] for row in conn.execute("PRAGMA index_list(sermonshots_clips)").fetchall()}
    if "idx_sermonshots_clips_serve_token" not in clip_indexes:
        conn.execute(
            "CREATE UNIQUE INDEX idx_sermonshots_clips_serve_token "
            "ON sermonshots_clips(serve_token)"
        )
        print("  [migrated] idx_sermonshots_clips_serve_token")
    else:
        print("  [exists]   idx_sermonshots_clips_serve_token")

    queue_cols = {row[1] for row in conn.execute("PRAGMA table_info(church_social_queue)").fetchall()}
    for col, coltype in (("video_path", "TEXT"), ("fb_video_id", "TEXT"), ("ig_creation_id", "TEXT")):
        if col not in queue_cols:
            conn.execute(f"ALTER TABLE church_social_queue ADD COLUMN {col} {coltype}")
            print(f"  [migrated] church_social_queue.{col}")
        else:
            print(f"  [exists]   church_social_queue.{col}")

    conn.commit()
    print("Done: clip scheduling columns ready.")
finally:
    conn.close()
