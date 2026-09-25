"""prayer_notify.py -- deacon prayer-request notifications with accountability buttons.

Sends a formatted prayer-request alert to a deacon's Telegram chat with
inline buttons: "Logged / Done" (marks contact made), "Remind me later"
(offers a 12/18/24/36-hour snooze), and "Escalate to Pastor" (notifies Bill
with the same accountability buttons, minus a further escalate option).
Every send is tracked in prayer_contact_log so bot.py's callback handlers
(pr_done/pr_later/pr_back/pr_remind/pr_escalate) can verify the
button-presser is the same chat the notification went to, and so
prayer_reminder_fire.py's cron can re-fire snoozed ones. Not gated behind
bot.py's _is_authorized (Bill-only) -- deacons other than Bill are the
intended audience once onboarded; wtsn.me/cat/shepcheck (shepcheck_web.py)
reads this table read-only for the elder-level accountability report.
"""
import os
import sqlite3

import requests

from config.settings import TELEGRAM_BOT_TOKEN

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB_PATH = os.path.expanduser("~/watson/data/watson.db")

REMIND_HOURS = (12, 18, 24, 36)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prayer_contact_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            prayer_request_id   INTEGER NOT NULL REFERENCES prayer_requests(id),
            deacon_name         TEXT,
            header              TEXT,
            deacon_person_id    INTEGER NOT NULL,
            telegram_chat_id    TEXT NOT NULL,
            telegram_message_id INTEGER,
            status              TEXT NOT NULL DEFAULT 'pending',
            can_escalate        INTEGER NOT NULL DEFAULT 1,
            parent_log_id       INTEGER REFERENCES prayer_contact_log(id),
            escalated_to_log_id INTEGER REFERENCES prayer_contact_log(id),
            sent_at             TEXT NOT NULL DEFAULT (datetime('now')),
            contacted_at        TEXT,
            remind_at           TEXT,
            snooze_hours        INTEGER,
            escalated_at        TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Migration path for the table as it existed before escalation support
    # (2026-09-24) -- ALTER TABLE ADD COLUMN for anyone who already has rows.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(prayer_contact_log)").fetchall()}
    for col, ddl in (
        ("header", "ALTER TABLE prayer_contact_log ADD COLUMN header TEXT"),
        ("can_escalate", "ALTER TABLE prayer_contact_log ADD COLUMN can_escalate INTEGER NOT NULL DEFAULT 1"),
        ("parent_log_id", "ALTER TABLE prayer_contact_log ADD COLUMN parent_log_id INTEGER"),
        ("escalated_to_log_id", "ALTER TABLE prayer_contact_log ADD COLUMN escalated_to_log_id INTEGER"),
        ("escalated_at", "ALTER TABLE prayer_contact_log ADD COLUMN escalated_at TEXT"),
        ("escalation_note", "ALTER TABLE prayer_contact_log ADD COLUMN escalation_note TEXT"),
    ):
        if col not in existing:
            conn.execute(ddl)

    # deacon_name was NOT NULL in the table's original (pre-escalation)
    # definition. An escalation row (header set, deacon_name left NULL)
    # violates that and crashes the INSERT with an IntegrityError -- hit
    # live 2026-09-24 when Jim's escalation never reached Bill. SQLite can't
    # drop a NOT NULL constraint via ALTER TABLE, so rebuild the table.
    deacon_name_notnull = next(
        (row[3] for row in conn.execute("PRAGMA table_info(prayer_contact_log)").fetchall() if row[1] == "deacon_name"),
        0,
    )
    if deacon_name_notnull:
        conn.executescript("""
            CREATE TABLE prayer_contact_log_new (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                prayer_request_id   INTEGER NOT NULL REFERENCES prayer_requests(id),
                deacon_name         TEXT,
                header              TEXT,
                deacon_person_id    INTEGER NOT NULL,
                telegram_chat_id    TEXT NOT NULL,
                telegram_message_id INTEGER,
                status              TEXT NOT NULL DEFAULT 'pending',
                can_escalate        INTEGER NOT NULL DEFAULT 1,
                parent_log_id       INTEGER REFERENCES prayer_contact_log(id),
                escalated_to_log_id INTEGER REFERENCES prayer_contact_log(id),
                escalation_note     TEXT,
                sent_at             TEXT NOT NULL DEFAULT (datetime('now')),
                contacted_at        TEXT,
                remind_at           TEXT,
                snooze_hours        INTEGER,
                escalated_at        TEXT,
                created_at          TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO prayer_contact_log_new (
                id, prayer_request_id, deacon_name, header, deacon_person_id, telegram_chat_id,
                telegram_message_id, status, can_escalate, parent_log_id, escalated_to_log_id,
                escalation_note, sent_at, contacted_at, remind_at, snooze_hours, escalated_at, created_at
            )
            SELECT
                id, prayer_request_id, deacon_name, header, deacon_person_id, telegram_chat_id,
                telegram_message_id, status, can_escalate, parent_log_id, escalated_to_log_id,
                escalation_note, sent_at, contacted_at, remind_at, snooze_hours, escalated_at, created_at
            FROM prayer_contact_log;
            DROP TABLE prayer_contact_log;
            ALTER TABLE prayer_contact_log_new RENAME TO prayer_contact_log;
        """)


def _pronoun(gender: str | None) -> str:
    g = (gender or "").strip().lower()
    if g == "male":
        return "his"
    if g == "female":
        return "her"
    return "their"


def format_message(conn, prayer_request_id: int, deacon_name: str | None = None, header: str | None = None) -> str | None:
    """Returns None if the request doesn't exist, has no linked member, or
    is leadership_only (those never go to deacons -- see
    deacon_visible_prayer_requests, which applies the same filter).

    Pass deacon_name for the normal "Jim, so-and-so asked for prayer..."
    greeting, or header for an escalation-style lead-in (e.g. "🚨 Escalated
    by Jim -- ") -- exactly one of the two is expected."""
    row = conn.execute("""
        SELECT pr.request_text, pr.leadership_only, m.name, m.email, m.phone, m.gender
        FROM prayer_requests pr
        JOIN members m ON m.id = pr.member_id
        WHERE pr.id = ?
    """, (prayer_request_id,)).fetchone()
    if not row or row["leadership_only"]:
        return None

    possessive = _pronoun(row["gender"])
    contact_bits = [b for b in (row["email"], row["phone"]) if b]
    contact = ", ".join(contact_bits) if contact_bits else "no contact info on file"

    intro = header if header else f"{deacon_name}, "

    return (
        f'{intro}{row["name"]} asked for prayer this week: '
        f'"{row["request_text"]}"\n\n'
        f"Here is {possessive} info: {contact}."
    )


def _keyboard(log_id: int, can_escalate: bool = True) -> dict:
    rows = [[
        {"text": "✅ Logged / Done", "callback_data": f"pr_done:{log_id}"},
        {"text": "⏰ Remind me later", "callback_data": f"pr_later:{log_id}"},
    ]]
    if can_escalate:
        rows.append([{"text": "🚨 Escalate to Pastor", "callback_data": f"pr_escalate:{log_id}"}])
    return {"inline_keyboard": rows}


def _send_telegram(chat_id, text: str, keyboard: dict) -> int | None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["result"]["message_id"]


def bill_contact() -> tuple[int, str] | None:
    """Looks up (people.id, telegram_chat_id) for Bill Yomes in watson.db's
    people table, for the pr_escalate handler to send to. None if Bill
    hasn't claimed a Telegram chat (shouldn't happen in practice, but
    callers must check rather than silently no-op sending nowhere)."""
    conn = sqlite3.connect(WATSON_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, telegram_chat_id FROM people WHERE name = 'Bill Yomes'"
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["telegram_chat_id"]:
        return None
    return row["id"], row["telegram_chat_id"]


def send_notification(
    prayer_request_id: int,
    deacon_person_id: int,
    deacon_chat_id: str,
    deacon_name: str | None = None,
    header: str | None = None,
    can_escalate: bool = True,
    parent_log_id: int | None = None,
) -> int | None:
    """Send a fresh prayer-contact notification. Returns the new
    prayer_contact_log id, or None if the request isn't eligible to send."""
    with _conn() as conn:
        ensure_schema(conn)
        text = format_message(conn, prayer_request_id, deacon_name, header)
        if text is None:
            return None

        cur = conn.execute(
            "INSERT INTO prayer_contact_log "
            "(prayer_request_id, deacon_name, header, deacon_person_id, telegram_chat_id, "
            "status, can_escalate, parent_log_id) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (prayer_request_id, deacon_name, header, deacon_person_id, str(deacon_chat_id),
             int(can_escalate), parent_log_id),
        )
        log_id = cur.lastrowid

        message_id = _send_telegram(deacon_chat_id, text, _keyboard(log_id, can_escalate))
        conn.execute(
            "UPDATE prayer_contact_log SET telegram_message_id = ? WHERE id = ?",
            (message_id, log_id),
        )
    return log_id


def resend_notification(log_id: int) -> bool:
    """Re-send a snoozed notification's message with fresh buttons (used by
    prayer_reminder_fire.py once remind_at is due)."""
    with _conn() as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM prayer_contact_log WHERE id = ?", (log_id,)).fetchone()
        if not row:
            return False
        text = format_message(conn, row["prayer_request_id"], row["deacon_name"], row["header"])
        if text is None:
            return False
        can_escalate = bool(row["can_escalate"])
        message_id = _send_telegram(row["telegram_chat_id"], text, _keyboard(log_id, can_escalate))
        conn.execute(
            "UPDATE prayer_contact_log SET telegram_message_id = ?, status = 'pending', "
            "sent_at = datetime('now'), remind_at = NULL, snooze_hours = NULL WHERE id = ?",
            (message_id, log_id),
        )
    return True
