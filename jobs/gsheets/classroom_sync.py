"""
jobs/gsheets/classroom_sync.py — syncs per-classroom kids/adults counts from
the same "Catalyst Count Tracking" Google Sheet headcount_sync.py already
reads (see that file for sheet/tab layout background) into congregation.db.

Columns pulled (confirmed with Bill 2026-09-01):
  Kids N / Adults N   — Nursery
  Kids T / Adults T   — Toddlers
  Kids PK / Adults PK — PreK
  Kids E / Adults E   — Elementary

A separate table/job from headcount_sync.py on purpose: that one only ever
pulls the single WLM/NPT headcount column (deliberately narrow), and this
adds a second, independent narrow pull rather than widening it to cover
unrelated columns neither job needs.

Cron (nightly, 1:05am -- same cadence as headcount_sync.py, offset 5 min so
they don't both hit the Sheets API in the same instant):
  5 1 * * *  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.gsheets.classroom_sync >> /home/billyomes/watson/logs/classroom_sync.log 2>&1

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.gsheets.classroom_sync
  PYTHONPATH=/home/billyomes/watson python -m jobs.gsheets.classroom_sync --dry-run
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [classroom_sync] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CONG_DB  = os.path.expanduser("~/watson/data/congregation.db")
KEY_FILE = os.getenv(
    "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE",
    os.path.expanduser("~/watson/config/sheets_service_account.json"),
)
SHEET_ID = os.getenv("GOOGLE_SHEETS_HEADCOUNT_ID", "1FFfBwIaHnlgpTcT3UqICnpM17ELIsyN_mLoT5pcWWWw")
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

EARLIEST_YEAR = 2023  # matches headcount_sync.py -- earlier tabs predate this column layout
DATE_FORMATS  = ("%m/%d/%y", "%m/%d/%Y")

# Sheet header text -> (db column, room name). Matched case-insensitively,
# stripped of surrounding whitespace.
_ROOM_COLUMNS = {
    "kids n":    ("kids_nursery",     "Nursery"),
    "adults n":  ("adults_nursery",   "Nursery"),
    "kids t":    ("kids_toddlers",    "Toddlers"),
    "adults t":  ("adults_toddlers",  "Toddlers"),
    "kids pk":   ("kids_prek",        "PreK"),
    "adults pk": ("adults_prek",      "PreK"),
    "kids e":    ("kids_elementary",  "Elementary"),
    "adults e":  ("adults_elementary","Elementary"),
}
_DB_COLUMNS = [col for col, _ in _ROOM_COLUMNS.values()]

# Room name -> (kids column, adults column), for the Telegram lookup side.
ROOMS = {
    "nursery":    ("kids_nursery", "adults_nursery"),
    "toddlers":   ("kids_toddlers", "adults_toddlers"),
    "prek":       ("kids_prek", "adults_prek"),
    "elementary": ("kids_elementary", "adults_elementary"),
}


def latest_room_count(room: str) -> dict | None:
    """Most recently synced Sunday's kids/adults counts for `room` (a key of
    ROOMS), or None if nothing's synced yet. Used by bot.py's team-chat
    classroom lookup -- doesn't attempt to resolve a specific date out of
    the question, always answers for the latest row."""
    if room not in ROOMS:
        raise ValueError(f"unknown room {room!r}, expected one of {list(ROOMS)}")
    kids_col, adults_col = ROOMS[room]
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT date, {kids_col} AS kids, {adults_col} AS adults "
            f"FROM classroom_attendance ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ── schema ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONG_DB)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS classroom_attendance (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT NOT NULL UNIQUE,
            {", ".join(f"{col} INTEGER" for col in _DB_COLUMNS)},
            synced_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


# ── Telegram (fail loud on structure changes only) ──────────────────────────

def _alert(text: str) -> None:
    log.error(text)
    if vacation_gate("system_failure", "jobs.gsheets.classroom_sync", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram alert failed to send: %s", exc)


# ── Sheets access ────────────────────────────────────────────────────────────

def _sheets_service():
    creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _year_tabs(service) -> list[str]:
    meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    titles = []
    for s in meta["sheets"]:
        title = s["properties"]["title"]
        if re.fullmatch(r"\d{4}", title) and int(title) >= EARLIEST_YEAR:
            titles.append(title)
    return sorted(titles)


def _parse_date(raw: str) -> str | None:
    raw = (raw or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_count(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _parse_tab(service, tab: str) -> list[dict] | None:
    """Returns [{"date": iso_date, "kids_nursery": n, ...}, ...] for one year
    tab, or None if the header doesn't contain Date + all 8 room columns --
    caller treats None as a fail-loud structure-change condition."""
    header_row = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"{tab}!A2:AZ2")
        .execute()
        .get("values", [[]])
    )
    header = header_row[0] if header_row else []

    date_idx = next((i for i, h in enumerate(header) if h.strip().lower() == "date"), None)
    col_idx: dict[str, int] = {}
    for i, h in enumerate(header):
        key = h.strip().lower()
        if key in _ROOM_COLUMNS:
            db_col, _ = _ROOM_COLUMNS[key]
            col_idx[db_col] = i

    if date_idx is None or len(col_idx) != len(_ROOM_COLUMNS):
        return None

    data = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"{tab}!A3:AZ900")
        .execute()
        .get("values", [])
    )

    rows: list[dict] = []
    for row in data:
        raw_date = row[date_idx] if date_idx < len(row) else ""
        if not raw_date.strip():
            continue
        iso_date = _parse_date(raw_date)
        if iso_date is None:
            log.warning("Tab %s: unparseable date %r, skipping row", tab, raw_date)
            continue
        record = {"date": iso_date}
        for db_col, idx in col_idx.items():
            raw_val = row[idx] if idx < len(row) else ""
            record[db_col] = _parse_count(raw_val)
        # Blank/cancelled Sunday (e.g. snow) if every room is blank -- omit
        # the row entirely rather than writing all-NULL, matching
        # headcount_sync.py's "don't zero a blank week" convention.
        if all(record[c] is None for c in _DB_COLUMNS):
            continue
        rows.append(record)
    return rows


# ── Sync ───────────────────────────────────────────────────────────────────

def most_recent_sunday() -> date:
    today = date.today()
    return today - timedelta(days=(today.weekday() + 1) % 7)


def sync(dry_run: bool = False) -> dict:
    service = _sheets_service()
    tabs = _year_tabs(service)
    log.info("Year tabs in scope: %s", tabs)

    all_rows: list[dict] = []
    failed_tabs: list[str] = []
    for tab in tabs:
        parsed = _parse_tab(service, tab)
        if parsed is None:
            failed_tabs.append(tab)
            continue
        all_rows.extend(parsed)

    if failed_tabs:
        _alert(
            "Classroom attendance sheet structure changed — could not find "
            f"Date + all 8 room columns in tab(s): {', '.join(failed_tabs)}. "
            "Other tabs synced normally; this/these tab(s) were skipped. "
            "Check the sheet layout."
        )

    synced_at = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        conn = _conn()
        try:
            cols = ["date"] + _DB_COLUMNS + ["synced_at"]
            placeholders = ", ".join("?" for _ in cols)
            update_clause = ", ".join(f"{c} = excluded.{c}" for c in _DB_COLUMNS + ["synced_at"])
            conn.executemany(
                f"""
                INSERT INTO classroom_attendance ({", ".join(cols)})
                VALUES ({placeholders})
                ON CONFLICT(date) DO UPDATE SET {update_clause}
                """,
                [tuple(r.get(c) for c in cols[:-1]) + (synced_at,) for r in all_rows],
            )
            conn.commit()
        finally:
            conn.close()

    recent = most_recent_sunday().isoformat()
    if recent not in {r["date"] for r in all_rows}:
        log.warning(
            "No classroom attendance row yet for %s — sheet may not be filled in for this Sunday.",
            recent,
        )

    log.info(
        "Synced %d row(s) across %d tab(s), %d tab(s) failed structure check.",
        len(all_rows), len(tabs) - len(failed_tabs), len(failed_tabs),
    )
    return {
        "rows": len(all_rows),
        "tabs_ok": len(tabs) - len(failed_tabs),
        "tabs_failed": failed_tabs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync per-classroom attendance from the tracking Google Sheet.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and log without writing to the DB")
    args = parser.parse_args()

    try:
        result = sync(dry_run=args.dry_run)
    except Exception as exc:
        log.error("Sync failed: %s", exc)
        sys.exit(1)

    print(result)
