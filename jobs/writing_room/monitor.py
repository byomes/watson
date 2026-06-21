"""jobs/writing_room/monitor.py — poll Writing Room tables, fire Telegram alerts.

Cron: */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/writing_room/monitor.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import bootstrap_db, get_db, send_telegram

log = logging.getLogger(__name__)

_ROOM_URL     = "https://williamckyomes.com/room"
_BETA_URL     = "https://williamckyomes.com/room/beta"
_PRAYER_URL   = "https://williamckyomes.com/room/prayer"

WILLIAM_PARTNER_ID = 0  # reserved ID for posts authored by William


def _partner_name(conn, partner_id: int) -> str:
    row = conn.execute(
        "SELECT name FROM writing_room_partners WHERE id = ?", (partner_id,)
    ).fetchone()
    return row["name"] if row else "Unknown"


def _section_label(section: str) -> str:
    return {"board": "Board", "beta": "Beta", "prayer": "Prayer"}.get(section, section.title())


def check_new_partners(conn) -> None:
    """Alert William when a new active partner has not been welcomed yet (onboard.py handles welcome, monitor tracks it)."""
    rows = conn.execute(
        "SELECT * FROM writing_room_partners WHERE status = 'active' AND welcome_sent = 0"
    ).fetchall()
    # onboard.py handles these — monitor just ensures we don't miss any
    for row in rows:
        from jobs.writing_room.onboard import process_approval
        try:
            process_approval(row["id"])
        except Exception as exc:
            log.error("onboard failed for partner %d: %s", row["id"], exc)


def check_posts(conn) -> None:
    rows = conn.execute(
        "SELECT p.*, pr.name as partner_name, pr.joined_at "
        "FROM writing_room_posts p "
        "LEFT JOIN writing_room_partners pr ON p.partner_id = pr.id "
        "WHERE p.watson_alerted = 0"
    ).fetchall()

    for row in rows:
        conn.execute(
            "UPDATE writing_room_posts SET watson_alerted = 1 WHERE id = ?", (row["id"],)
        )

        section    = row["section"]
        name       = row["partner_name"] or "Unknown"
        content    = row["content"]
        preview    = content[:120] + ("…" if len(content) > 120 else "")
        parent_id  = row["parent_id"]
        partner_id = row["partner_id"]
        flagged    = row["flagged"]

        if section == "prayer" and parent_id is not None:
            msg = (
                f"🙏 Writing Room — Prayer\n\n"
                f"{name} responded to a prayer request.\n\n"
                f"\"{preview}\"\n\n"
                f"View → {_PRAYER_URL}"
            )
            send_telegram(msg)
            continue

        if flagged:
            msg = (
                f"✍️ Writing Room — {_section_label(section)}\n\n"
                f"⚠️ Flagged content from {name}\n\n"
                f"\"{preview}\"\n\n"
                f"View in Room → {_ROOM_URL}"
            )
            send_telegram(msg)
            continue

        if parent_id is not None:
            # reply to a post — check if original post was William's
            parent = conn.execute(
                "SELECT partner_id FROM writing_room_posts WHERE id = ?", (parent_id,)
            ).fetchone()
            if parent and parent["partner_id"] == WILLIAM_PARTNER_ID:
                kind = "reply to William"
            else:
                kind = "reply"
        else:
            # top-level post
            from datetime import datetime, timedelta
            joined_at = row["joined_at"]
            is_new_member = False
            if joined_at:
                try:
                    joined_dt = datetime.fromisoformat(joined_at)
                    is_new_member = (datetime.utcnow() - joined_dt) < timedelta(days=7)
                except ValueError:
                    pass
            kind = "new member first post" if is_new_member else "post"

        has_question = "?" in content
        if has_question and kind == "post":
            kind = "question"

        msg = (
            f"✍️ Writing Room — {_section_label(section)}\n\n"
            f"New {kind} from {name}\n\n"
            f"\"{preview}\"\n\n"
            f"View in Room → {_ROOM_URL}"
        )
        send_telegram(msg)


def check_beta_feedback(conn) -> None:
    rows = conn.execute(
        "SELECT f.*, p.name as partner_name "
        "FROM writing_room_beta_feedback f "
        "LEFT JOIN writing_room_partners p ON f.partner_id = p.id "
        "WHERE f.watson_alerted = 0"
    ).fetchall()

    for row in rows:
        conn.execute(
            "UPDATE writing_room_beta_feedback SET watson_alerted = 1 WHERE id = ?", (row["id"],)
        )
        name       = row["partner_name"] or "Unknown"
        slug       = row["target_slug"]
        reaction   = row["reaction"] or "(none)"
        comment    = row["comment"] or "(none)"

        msg = (
            f"📖 Writing Room — Beta Feedback\n\n"
            f"{name} reacted to {slug}\n"
            f"Reaction: {reaction}\n"
            f"Comment: \"{comment}\"\n\n"
            f"View Feedback → {_BETA_URL}"
        )
        send_telegram(msg)


def check_messages(conn) -> None:
    rows = conn.execute(
        "SELECT * FROM writing_room_messages WHERE watson_alerted = 0"
    ).fetchall()

    for row in rows:
        conn.execute(
            "UPDATE writing_room_messages SET watson_alerted = 1 WHERE id = ?", (row["id"],)
        )
        msg = (
            f"✉️ Writing Room — Message for William\n\n"
            f"From: {row['name']} ({row['email']})\n\n"
            f"{row['message']}"
        )
        send_telegram(msg)


def main() -> None:
    bootstrap_db()
    conn = get_db()
    try:
        check_posts(conn)
        check_beta_feedback(conn)
        check_messages(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
