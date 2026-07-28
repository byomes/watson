"""
jobs/gsheets/headcount_sync.py — syncs Wilmington Sunday headcounts from the
manual headcount-tracking Google Sheet into congregation.db.

Source sheet: "Catalyst Count Tracking"
  https://docs.google.com/spreadsheets/d/1FFfBwIaHnlgpTcT3UqICnpM17ELIsyN_mLoT5pcWWWw
  One tab per year (2022-2026+). Row 2 is the header row, row 3+ is one row
  per Sunday. Column A = Date. The real Wilmington headcount is whichever
  column is headed "WLM" (2024+) or "NPT" (2023, same position, campus
  renamed at some point) — NOT "T-WLM"/"T-NPT", which is a larger number
  that also includes Connect Groups and other categories. 2022 has no
  Wilmington/Online split at all (single "Total" column, predates the online
  campus) and is permanently out of scope. A "Copy of 2026" tab and a
  "comparison" tab also exist on the sheet — both ignored; per Bill (checked
  2026-07-27), "Copy of 2026" is a stale one-off scratch copy, not kept in
  sync going forward.

  Confirmed with Bill 2026-07-27:
    - WLM/NPT (not T-WLM/T-NPT) is the correct headcount for the gap
      comparison against connect-card-derived attendance.
    - Backfill starts at 2023; 2022 is skipped entirely.
    - Sheet is filled in by Donna, but not consistently same-day — some weeks
      she doesn't enter the count until Tuesday or Wednesday (corrected
      2026-07-27, after an initial "same-day Sunday" assumption turned out
      wrong). Nightly sync picks up a late entry within a day instead of
      waiting up to a full week, and still lands well before Thursday 4pm's
      state_of_church.py send.

Cron (nightly, 1am):
  0 1 * * *  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.gsheets.headcount_sync >> /home/billyomes/watson/logs/headcount_sync.log 2>&1

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.gsheets.headcount_sync
  PYTHONPATH=/home/billyomes/watson python -m jobs.gsheets.headcount_sync --dry-run
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
    format="%(asctime)s [headcount_sync] %(levelname)s %(message)s",
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

EARLIEST_YEAR     = 2023  # 2022 has no Wilmington/Online split — permanently out of scope
HEADCOUNT_HEADERS = {"WLM", "NPT"}  # same column position across years, renamed at some point
DATE_FORMATS      = ("%m/%d/%y", "%m/%d/%Y")


# ── schema ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONG_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wilmington_headcounts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT NOT NULL UNIQUE,
            headcount INTEGER NOT NULL,
            synced_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


# ── Telegram (fail loud on structure changes only) ──────────────────────────

def _alert(text: str) -> None:
    log.error(text)
    if vacation_gate("system_failure", "jobs.gsheets.headcount_sync", text):
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
    """Year tabs >= EARLIEST_YEAR, discovered dynamically so a newly-added
    tab for a future year is picked up automatically."""
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


def _parse_headcount(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _parse_tab(service, tab: str) -> list[tuple[str, int]] | None:
    """Returns [(iso_date, headcount), ...] for one year tab, or None if the
    tab's header doesn't contain a recognizable Date/WLM/NPT column — caller
    treats None as a fail-loud structure-change condition."""
    header_row = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"{tab}!A2:AZ2")
        .execute()
        .get("values", [[]])
    )
    header = header_row[0] if header_row else []

    date_idx = next((i for i, h in enumerate(header) if h.strip().lower() == "date"), None)
    headcount_idx = next(
        (i for i, h in enumerate(header) if h.strip().upper() in HEADCOUNT_HEADERS), None
    )
    if date_idx is None or headcount_idx is None:
        return None

    data = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"{tab}!A3:AZ900")
        .execute()
        .get("values", [])
    )

    rows: list[tuple[str, int]] = []
    for row in data:
        raw_date = row[date_idx] if date_idx < len(row) else ""
        if not raw_date.strip():
            continue
        iso_date = _parse_date(raw_date)
        if iso_date is None:
            log.warning("Tab %s: unparseable date %r, skipping row", tab, raw_date)
            continue
        raw_count = row[headcount_idx] if headcount_idx < len(row) else ""
        headcount = _parse_headcount(raw_count)
        if headcount is None:
            continue  # blank/cancelled Sunday (e.g. snow) — omit, don't zero
        rows.append((iso_date, headcount))
    return rows


# ── Sync ───────────────────────────────────────────────────────────────────

def most_recent_sunday() -> date:
    today = date.today()
    return today - timedelta(days=(today.weekday() + 1) % 7)


def sync(dry_run: bool = False) -> dict:
    service = _sheets_service()
    tabs = _year_tabs(service)
    log.info("Year tabs in scope: %s", tabs)

    all_rows: list[tuple[str, int]] = []
    failed_tabs: list[str] = []
    for tab in tabs:
        parsed = _parse_tab(service, tab)
        if parsed is None:
            failed_tabs.append(tab)
            continue
        all_rows.extend(parsed)

    if failed_tabs:
        _alert(
            "Headcount sheet structure changed — could not find Date/WLM/NPT "
            f"columns in tab(s): {', '.join(failed_tabs)}. Other tabs synced "
            "normally; this/these tab(s) were skipped. Check the sheet layout."
        )

    synced_at = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        conn = _conn()
        try:
            conn.executemany(
                """
                INSERT INTO wilmington_headcounts (date, headcount, synced_at)
                VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    headcount = excluded.headcount,
                    synced_at = excluded.synced_at
                """,
                [(d, c, synced_at) for d, c in all_rows],
            )
            conn.commit()
        finally:
            conn.close()

    # Soft warning (log only, not Telegram) if the most recent Sunday has no row yet
    recent = most_recent_sunday().isoformat()
    if recent not in {d for d, _ in all_rows}:
        log.warning(
            "No headcount row yet for %s — sheet may not be filled in for this Sunday.",
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
    parser = argparse.ArgumentParser(description="Sync Wilmington headcounts from the tracking Google Sheet.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and log without writing to the DB")
    args = parser.parse_args()

    try:
        result = sync(dry_run=args.dry_run)
    except Exception as exc:
        log.error("Sync failed: %s", exc)
        sys.exit(1)

    print(result)
