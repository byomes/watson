import re


def parse_reminder_time(text):
    """Parse '2pm', '14:30', '2:30pm' → 'HH:MM' or None."""
    text = text.strip().lower()
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)?$', text)
    if m:
        h, mins, mer = int(m.group(1)), int(m.group(2)), m.group(3)
        if mer == 'pm' and h != 12:
            h += 12
        elif mer == 'am' and h == 12:
            h = 0
        return f"{h:02d}:{mins:02d}"
    m = re.match(r'^(\d{1,2})\s*(am|pm)$', text)
    if m:
        h, mer = int(m.group(1)), m.group(2)
        if mer == 'pm' and h != 12:
            h += 12
        elif mer == 'am' and h == 12:
            h = 0
        return f"{h:02d}:00"
    return None


def ensure_reminders_schema(conn) -> None:
    """Create reminders table if missing; migrate to add reminder_time and updated_at."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT    NOT NULL,
            due_datetime  TEXT    NOT NULL DEFAULT '',
            reminder_time TEXT,
            status        TEXT    NOT NULL DEFAULT 'active',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT
        )
    """)
    _cols = {row[1] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
    if "reminder_time" not in _cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN reminder_time TEXT")
    if "updated_at" not in _cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN updated_at TEXT")
