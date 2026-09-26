"""
Pastoral search skill — returns a pastoral summary for a named member.
Triggered via Telegram: "pastoral search [name]"
"""

import datetime
import re
import sqlite3

from core.database import get_connection
from jobs.sms.carrier_lookup import normalize_phone

CONG_DB = "/home/billyomes/watson/data/congregation.db"


def _connect():
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _recent_texts(phone: str | None) -> list[str]:
    """Last 4 weeks of this member's Watson SMS thread (see
    jobs/sms/schema.py's GUARDRAIL note -- this is Bill-only, same as the
    rest of pastoral_search, never exposed through Team Chat/data_chat.py).
    Returns [] if the member has no phone on file or no matching thread."""
    phone_digits = normalize_phone(phone) if phone else None
    if not phone_digits:
        return []

    conn = get_connection()
    try:
        thread = conn.execute("SELECT id FROM sms_threads WHERE phone = ?", (phone_digits,)).fetchone()
        if not thread:
            return []
        rows = conn.execute(
            """SELECT direction, body, created_at FROM sms_messages
               WHERE thread_id = ? AND created_at >= datetime('now', '-28 days')
               ORDER BY created_at DESC""",
            (thread["id"],),
        ).fetchall()
        return [
            f"- {r['created_at']} [{'sent' if r['direction'] == 'out' else 'received'}]: {r['body']}"
            for r in rows
            if r["body"] and r["body"].strip()
        ]
    finally:
        conn.close()


def run(message: str = None) -> str:
    if not message:
        return "Please provide a name to search."

    match = re.search(r'pastoral\s+search\s*:?\s*(?:for\s+)?(.+)', message.strip(), re.IGNORECASE)
    name = match.group(1).strip() if match else message.strip()

    if not name:
        return "Who would you like a pastoral summary for?"

    conn = _connect()
    try:
        # 1. Look up member
        member = conn.execute(
            "SELECT id, name, campus_preference, phone FROM members"
            " WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 1",
            (f"%{name}%",),
        ).fetchone()

        if not member:
            return f"No member found matching '{name}'."

        member_id = member["id"]
        display_name = member["name"]
        # Derive campus from last 6 weeks of attendance
        six_weeks_ago = (datetime.date.today() - datetime.timedelta(weeks=6)).isoformat()
        campus_rows = conn.execute(
            "SELECT campus, COUNT(*) as cnt FROM attendance"
            " WHERE member_id = ? AND service_date >= ? AND campus IS NOT NULL AND campus != ''",
            (member_id, six_weeks_ago),
        ).fetchall()
        campus_counts = {row["campus"]: row["cnt"] for row in campus_rows}
        wilmington = campus_counts.get("Wilmington", 0)
        online = campus_counts.get("Online", 0)
        if wilmington >= 4 and online >= 2:
            campus = "Hybrid (Wilmington + Online)"
        elif online >= 4 and wilmington >= 2:
            campus = "Hybrid (Online + Wilmington)"
        elif wilmington >= 4:
            campus = "Wilmington"
        elif online >= 4:
            campus = "Online"
        elif wilmington > 0 or online > 0:
            campus = f"Insufficient data ({wilmington}W / {online}O last 6 weeks)"
        else:
            campus = "Not on record"

        # 2. Last 4 distinct service dates in attendance table overall
        recent_dates = [
            row["service_date"]
            for row in conn.execute(
                "SELECT DISTINCT service_date FROM attendance"
                " ORDER BY service_date DESC LIMIT 4"
            ).fetchall()
        ]

        # 3. Attendance for this member on those dates, with campus
        attended = {
            row["service_date"]: row["campus"]
            for row in conn.execute(
                "SELECT service_date, campus FROM attendance WHERE member_id = ?",
                (member_id,),
            ).fetchall()
        }
        if recent_dates:
            attendance_lines = [
                f"- {d} — Present ({attended[d]})" if d in attended else f"- {d} — Absent"
                for d in recent_dates
            ]
        else:
            attendance_lines = []

        # 4. Connect cards (last 28 days)
        cards = conn.execute(
            "SELECT service_date, questions_comments FROM connect_cards"
            " WHERE member_id = ? AND service_date >= date('now', '-28 days')"
            " ORDER BY service_date DESC",
            (member_id,),
        ).fetchall()
        card_lines = [
            f"- {r['service_date']}: {r['questions_comments']}"
            for r in cards
            if r["questions_comments"] and r["questions_comments"].strip()
        ]

        # 5. Prayer requests (last 28 days)
        prayers = conn.execute(
            "SELECT date, request_text FROM prayer_requests"
            " WHERE member_id = ? AND date >= date('now', '-28 days')"
            " ORDER BY date DESC",
            (member_id,),
        ).fetchall()
        prayer_lines = [
            f"- {r['date']}: {r['request_text']}"
            for r in prayers
            if r["request_text"] and r["request_text"].strip()
        ]

        # 6. Next steps (last 28 days)
        steps = conn.execute(
            "SELECT date, step FROM next_steps"
            " WHERE member_id = ? AND date >= date('now', '-28 days')"
            " ORDER BY date DESC",
            (member_id,),
        ).fetchall()
        step_lines = [
            f"- {r['date']}: {r['step']}"
            for r in steps
            if r["step"] and r["step"].strip()
        ]

    finally:
        conn.close()

    # 7. Recent texts (last 28 days) -- Watson SMS, Bill-only (see
    # jobs/sms/schema.py's GUARDRAIL note). Own connection to watson.db,
    # separate from the congregation.db block above.
    text_lines = _recent_texts(member["phone"])

    none = "None on record."
    parts = [
        f"*{display_name} — Pastoral Summary*\n",
        f"*Campus:* {campus}\n",
        "*Attendance (last 4 weeks):*",
        "\n".join(attendance_lines) if attendance_lines else none,
        "\n*Connect Card Activity (last 4 weeks):*",
        "\n".join(card_lines) if card_lines else none,
        "\n*Prayer Requests (last 4 weeks):*",
        "\n".join(prayer_lines) if prayer_lines else none,
        "\n*Next Steps (last 4 weeks):*",
        "\n".join(step_lines) if step_lines else none,
        "\n*Recent Texts (last 4 weeks):*",
        "\n".join(text_lines) if text_lines else none,
    ]
    return "\n".join(parts)
