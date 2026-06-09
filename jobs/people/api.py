"""
People Registry API — callable module for Watson jobs.

Usage:
    from jobs.people.api import people_list, congregation_search, ...

PYTHONPATH=/home/billyomes/watson required on Beelink.
Run migrate.py once before first use.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.expanduser("~/watson/data/watson.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row(row):
    return dict(row) if row else None


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ── Personal contacts ──────────────────────────────────────────

def people_list():
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM people ORDER BY name COLLATE NOCASE"
            ).fetchall()
            return [_row(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


def people_get(id):
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM people WHERE id = ?", (id,)
            ).fetchone()
            return _row(row) or {"error": "Not found"}
    except Exception as e:
        return {"error": str(e)}


def people_create(data):
    try:
        with _conn() as conn:
            cur = conn.execute(
                """INSERT INTO people (name, email, phone, relationship, notes, carrier)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (data.get("name"), data.get("email"), data.get("phone"),
                 data.get("relationship"), data.get("notes"), data.get("carrier") or None),
            )
            row = conn.execute(
                "SELECT * FROM people WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return _row(row) or {"error": "Not found"}
    except Exception as e:
        return {"error": str(e)}


def people_update(id, data):
    allowed = {"name", "email", "phone", "relationship", "notes", "carrier"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return {"error": "No valid fields to update"}
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    try:
        with _conn() as conn:
            conn.execute(
                f"UPDATE people SET {set_clause} WHERE id = ?",
                (*fields.values(), id),
            )
        return people_get(id)
    except Exception as e:
        return {"error": str(e)}


def people_delete(id):
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM people WHERE id = ?", (id,))
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


# ── Congregation ───────────────────────────────────────────────

def congregation_list():
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM congregation ORDER BY name COLLATE NOCASE"
            ).fetchall()
            return [_row(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


def congregation_get(id):
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM congregation WHERE id = ?", (id,)
            ).fetchone()
            return _row(row) or {"error": "Not found"}
    except Exception as e:
        return {"error": str(e)}


def congregation_create(data):
    try:
        with _conn() as conn:
            cur = conn.execute(
                """INSERT INTO congregation
                   (name, email, phone, status, campus, notes,
                    prayer_requests, follow_up, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data.get("name"), data.get("email"), data.get("phone"),
                 data.get("status"), data.get("campus"), data.get("notes"),
                 data.get("prayer_requests"), data.get("follow_up"),
                 data.get("first_seen"), data.get("last_seen")),
            )
            row = conn.execute(
                "SELECT * FROM congregation WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return _row(row) or {"error": "Not found"}
    except Exception as e:
        return {"error": str(e)}


def congregation_update(id, data):
    allowed = {"name", "email", "phone", "status", "campus", "notes",
               "prayer_requests", "follow_up", "first_seen", "last_seen"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return {"error": "No valid fields to update"}
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    try:
        with _conn() as conn:
            conn.execute(
                f"UPDATE congregation SET {set_clause} WHERE id = ?",
                (*fields.values(), id),
            )
        return congregation_get(id)
    except Exception as e:
        return {"error": str(e)}


def congregation_delete(id):
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM congregation WHERE id = ?", (id,))
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def congregation_search(name):
    """Fuzzy name search — used for connect card matching."""
    try:
        with _conn() as conn:
            rows = conn.execute(
                """SELECT * FROM congregation
                   WHERE name LIKE ? COLLATE NOCASE
                   ORDER BY name COLLATE NOCASE""",
                (f"%{name}%",),
            ).fetchall()
            return [_row(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}
