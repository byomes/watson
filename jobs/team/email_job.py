"""jobs/team/email_job.py — Send team emails via Brevo and log to DB."""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from jobs.email_job.brevo_send import send_email

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB = BASE_DIR / "data" / "watson.db"

log = logging.getLogger(__name__)

CC_ADDR = "pastorbill@catalyst302.com"


def send_team_email(member_id: int, subject: str, body: str, meeting_id: int | None = None) -> dict:
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    member = conn.execute("SELECT * FROM team_members WHERE id=?", (member_id,)).fetchone()
    if not member:
        conn.close()
        return {"error": f"member {member_id} not found"}

    to_addr = member["email"]
    if not to_addr:
        conn.close()
        return {"error": f"member {member['name']} has no email address"}

    body_html = f"<html><body>{body.replace(chr(10), '<br>')}</body></html>"

    for recipient in (to_addr, CC_ADDR):
        result = send_email(
            to_email=recipient,
            to_name=member["name"] if recipient == to_addr else "",
            subject=subject,
            text_body=body,
            html_body=body_html,
        )
        if not result["success"]:
            conn.close()
            log.error("Brevo send failed for member %d: %s", member_id, result["error"])
            return {"error": result["error"]}

    sent_at = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO team_messages (member_id, direction, subject, body, sent_at) VALUES (?,?,?,?,?)",
        (member_id, "out", subject, body, sent_at),
    )
    if meeting_id:
        conn.execute("UPDATE team_meetings SET email_sent=1 WHERE id=?", (meeting_id,))
    conn.commit()
    conn.close()

    log.info("Team email sent to %s (%s)", member["name"], to_addr)
    return {"success": True, "to": to_addr, "sent_at": sent_at}
