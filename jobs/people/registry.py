import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.expanduser("~/watson/data/watson.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row):
    return dict(row) if row else None


def add_person(name, email=None, phone=None, info=None, carrier=None):
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO people (name, email, phone, info, carrier) VALUES (?, ?, ?, ?, ?)",
            (name, email, phone, info, carrier),
        )
        return cur.lastrowid


def get_person_by_id(person_id):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        return _row_to_dict(row)


def get_person_by_name(name):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM people WHERE name LIKE ? COLLATE NOCASE",
            (f"%{name}%",),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_person_by_email(email):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM people WHERE email = ?", (email,)
        ).fetchone()
        return _row_to_dict(row)


def update_person(person_id, **kwargs):
    allowed = {"name", "email", "phone", "info", "carrier"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [person_id]
    with _conn() as conn:
        conn.execute(
            f"UPDATE people SET {set_clause} WHERE id = ?", values
        )


def list_people():
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM people ORDER BY name").fetchall()
        return [_row_to_dict(r) for r in rows]


def delete_person(person_id):
    with _conn() as conn:
        conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
