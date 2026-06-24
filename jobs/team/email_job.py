"""jobs/team/email_job.py — Send team emails via Gmail SMTP and log to DB."""
import logging
import os
import smtplib
import sqlite3
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB = BASE_DIR / "data" / "watson.db"

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
FROM_ADDR = "Watson <watson@williamckyomes.com>"
CC_ADDR   = "pastorbill@catalyst302.com"


def send_team_email(member_id: int, subject: str, body: str, meeting_id: int | None = None) -> dict:
    smtp_user = os.getenv("WATSON_GMAIL_ADDRESS", "")
    smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

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

    body_html = body.replace("\n", "<br>")
    msg = MIMEMultipart("alternative")
    msg["From"]    = FROM_ADDR
    msg["To"]      = to_addr
    msg["CC"]      = CC_ADDR
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(f"<html><body>{body_html}</body></html>", "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.sendmail(FROM_ADDR, [to_addr, CC_ADDR], msg.as_string())
    except Exception as exc:
        conn.close()
        log.error("SMTP send failed for member %d: %s", member_id, exc)
        return {"error": str(exc)}

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
