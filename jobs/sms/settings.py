"""jobs/sms/settings.py — vacation mode + Friday Sabbath silence, the two
app-wide toggles added 2026-09-26 (project_backlog id=39). Single settings
row (id=1) in sms_settings; see jobs/sms/schema.py for the table.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from core.database import get_connection

_TZ = ZoneInfo("America/New_York")


_COLUMNS = (
    "vacation_mode", "sabbath_silence",
    "sabbath_autoresponder_on", "sabbath_autoresponder_body",
    "vacation_autoresponder_on", "vacation_autoresponder_body",
    "vacation_started_at",
)


def _get_row() -> dict:
    conn = get_connection()
    try:
        row = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM sms_settings WHERE id = 1").fetchone()
        return {
            "vacation_mode": bool(row["vacation_mode"]),
            "sabbath_silence": bool(row["sabbath_silence"]),
            "sabbath_autoresponder_on": bool(row["sabbath_autoresponder_on"]),
            "sabbath_autoresponder_body": row["sabbath_autoresponder_body"],
            "vacation_autoresponder_on": bool(row["vacation_autoresponder_on"]),
            "vacation_autoresponder_body": row["vacation_autoresponder_body"],
            "vacation_started_at": row["vacation_started_at"],
        }
    finally:
        conn.close()


def get_vacation_mode() -> bool:
    return _get_row()["vacation_mode"]


def get_sabbath_silence_enabled() -> bool:
    return _get_row()["sabbath_silence"]


def set_setting(
    vacation_mode: bool | None = None,
    sabbath_silence: bool | None = None,
    sabbath_autoresponder_on: bool | None = None,
    sabbath_autoresponder_body: str | None = None,
    vacation_autoresponder_on: bool | None = None,
    vacation_autoresponder_body: str | None = None,
) -> dict:
    conn = get_connection()
    try:
        if vacation_mode is not None:
            was_on = bool(conn.execute("SELECT vacation_mode FROM sms_settings WHERE id = 1").fetchone()[0])
            conn.execute("UPDATE sms_settings SET vacation_mode = ?, updated_at = datetime('now') WHERE id = 1", (1 if vacation_mode else 0,))
            # Stamped only on the 0->1 transition -- this is the
            # autoresponder's window key for "this vacation period"
            # (see get_active_autoresponder below), so toggling off and
            # back on later starts a fresh window instead of reusing one
            # a prior vacation already exhausted.
            if vacation_mode and not was_on:
                conn.execute("UPDATE sms_settings SET vacation_started_at = datetime('now') WHERE id = 1")
        if sabbath_silence is not None:
            conn.execute("UPDATE sms_settings SET sabbath_silence = ?, updated_at = datetime('now') WHERE id = 1", (1 if sabbath_silence else 0,))
        if sabbath_autoresponder_on is not None:
            conn.execute("UPDATE sms_settings SET sabbath_autoresponder_on = ?, updated_at = datetime('now') WHERE id = 1", (1 if sabbath_autoresponder_on else 0,))
        if sabbath_autoresponder_body is not None:
            conn.execute("UPDATE sms_settings SET sabbath_autoresponder_body = ?, updated_at = datetime('now') WHERE id = 1", (sabbath_autoresponder_body,))
        if vacation_autoresponder_on is not None:
            conn.execute("UPDATE sms_settings SET vacation_autoresponder_on = ?, updated_at = datetime('now') WHERE id = 1", (1 if vacation_autoresponder_on else 0,))
        if vacation_autoresponder_body is not None:
            conn.execute("UPDATE sms_settings SET vacation_autoresponder_body = ?, updated_at = datetime('now') WHERE id = 1", (vacation_autoresponder_body,))
        conn.commit()
    finally:
        conn.close()
    return _get_row()


def is_sabbath_now() -> bool:
    """Friday, 12:00am-11:59pm, America/New_York (Bill's family Sabbath).
    Always False while vacation mode is on -- see call_forwarding_toggle.py's
    docstring for why the two aren't compounded."""
    if get_vacation_mode():
        return False
    if not get_sabbath_silence_enabled():
        return False
    return datetime.now(_TZ).weekday() == 4  # Monday=0 ... Friday=4


def should_silence_notifications() -> bool:
    """Texts: vacation mode OR Friday Sabbath both mean no push/badge --
    messages still arrive and get stored normally, just silently."""
    if get_vacation_mode():
        return True
    return is_sabbath_now()


def get_active_autoresponder() -> tuple[str, str] | None:
    """Returns (window_key, body) for the autoresponder that should fire
    right now, or None if neither is active/enabled/filled in. Vacation
    takes priority over Sabbath, same precedence should_silence_notifications
    already uses (vacation covers Fridays too, so is_sabbath_now() is False
    while it's on) -- mirrored here rather than called, since this also
    needs vacation_started_at for the window key."""
    row = _get_row()
    if row["vacation_mode"]:
        if row["vacation_autoresponder_on"] and row["vacation_autoresponder_body"]:
            return f"vacation:{row['vacation_started_at']}", row["vacation_autoresponder_body"]
        return None
    if is_sabbath_now() and row["sabbath_autoresponder_on"] and row["sabbath_autoresponder_body"]:
        today = datetime.now(_TZ).strftime("%Y-%m-%d")
        return f"sabbath:{today}", row["sabbath_autoresponder_body"]
    return None
