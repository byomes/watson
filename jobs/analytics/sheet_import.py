"""jobs/analytics/sheet_import.py — Parses the "Catalyst Tracking Sheet -
Communications Team" Google Sheet into engagement_sheet_metrics.

Source: https://docs.google.com/spreadsheets/d/1CYm6rqvyO-ELWnHiElwJJjNA-df30ExZvxJZFILw7qw
Tabs: 2025, 2026. Full recon detail (oddities, per-tab structure): see
memory/engagement_data_recon_2026-08-20.md.

Layout, confirmed live via direct API pull (not assumed from recon alone —
this is not a row-per-record sheet, it's a row-per-metric / column-per-month
digest, grouped into labeled sections with blank-row separators):

  Row 1 = header: '' | <month name> ... | TOTAL | YTD AVG. Month columns are
  found by parsing each header cell as a month name (%B), which naturally
  excludes the label column and TOTAL/YTD AVG without hardcoding those
  header strings — robust to either being renamed or reordered.

  Section header row: label (col A) non-blank, every month-column cell blank.
  Blank/artifact row: label blank. This covers both a true blank separator
  row and the known 2026-tab stray near-empty row between "Event Count" and
  "Aquisitions" (which carries a stray "0" only in the YTD AVG column — not
  a month column, so the row still reads as blank-labeled and is skipped
  the same generic way, with no hardcoded row number).
  Data row: label non-blank, at least one month-column cell non-blank.

Connect Cards/Registration is NOT parsed here — replaced by
jobs/analytics/connect_card_rollup.py against congregation.db (source of
truth going forward). Skipped entirely, on purpose.

Sections parsed: Social Media, Catalyt App Engagement (sic — misspelled on
the sheet itself, matched as-is), E Mails/Website, Aquisitions (sic),
Top Page Views. Row labels are matched by text within each section, per tab
independently — 2025 and 2026 do not share row structure (confirmed in
recon), so no cross-tab position/label assumption is made anywhere below.
Any other/unrecognized section header is logged and its rows skipped, not
crashed on.

Number parsing: thousands-separator commas stripped; percentages ("84.00%",
"87%") parsed to 0-1 floats, matching GA4's engagementRate representation.
A non-numeric cell in an otherwise-numeric row (e.g. the literal "TBD")
becomes value_numeric=NULL, value_raw=<original text>, is_flagged=1 — never
crashes, never silently coerced to 0.

Top Page Views cells are freeform "Page Name XX%" strings (inconsistent
separators: "Home Page 29%", "Grapevine- 4.5%", "FBC - 40%", ...); split via
regex into name + percentage. Each ranked entry (1st-5th) becomes its own
row per month, metric_label="Top Page N", value_numeric=percentage (0-1),
value_raw=page name.

Cron: see jobs/analytics/monthly_web_engagement_report.py, which runs this
job as its first step — no separate cron entry for this file alone.

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.sheet_import
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.sheet_import --dry-run
"""

import argparse
import calendar
import logging
import os
import re
import sys
from datetime import date, datetime, timezone

import requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.analytics.schema import create_tables
from core.database import get_connection

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sheet_import] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

KEY_FILE = os.getenv(
    "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE",
    os.path.expanduser("~/watson/config/sheets_service_account.json"),
)
SHEET_ID = "1CYm6rqvyO-ELWnHiElwJJjNA-df30ExZvxJZFILw7qw"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
TABS = ["2025", "2026"]
_RANGE_COLS = "A1:S400"  # widest live tab (2026) uses columns A-O; buffer to S

SKIP_SECTIONS = {"Connect Cards/Registration"}
KNOWN_SECTIONS = {"Social Media", "Catalyt App Engagement", "E Mails/Website", "Aquisitions", "Top Page Views"}
TOP_PAGE_RANK_LABELS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}

_MONTH_BY_NAME = {name: i for i, name in enumerate(calendar.month_name) if name}
_TOP_PAGE_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*$")
_TRAILING_DASH_RE = re.compile(r"[-–]\s*$")


# ── Telegram (fail loud on structure changes only) ──────────────────────────

def _alert(text: str) -> None:
    log.error(text)
    if vacation_gate("system_failure", "jobs.analytics.sheet_import", text):
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


def _get_range(service, rng: str) -> list[list[str]]:
    return (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=rng)
        .execute()
        .get("values", [])
    )


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _is_blank(cell: str) -> bool:
    return not (cell or "").strip()


def _month_columns(header_row: list[str], year: int) -> dict[int, str]:
    """{column index: iso date of the 1st of that month} for every header
    cell that parses as a month name. Also excludes the label column and
    TOTAL/YTD AVG columns without needing to hardcode either string."""
    cols = {}
    for idx, raw in enumerate(header_row):
        month_num = _MONTH_BY_NAME.get((raw or "").strip())
        if month_num:
            cols[idx] = date(year, month_num, 1).isoformat()
    return cols


def _parse_number(raw: str) -> float | None:
    text = raw.strip()
    if not text:
        return None
    is_pct = text.endswith("%")
    if is_pct:
        text = text[:-1].strip()
    text = text.replace(",", "").replace(" ", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return value / 100 if is_pct else value


def _parse_top_page(raw: str) -> tuple[str, float] | None:
    """Splits a freeform 'Page Name XX%' cell into (name, 0-1 fraction), or
    None if no trailing percentage is found at all."""
    match = _TOP_PAGE_PCT_RE.search(raw)
    if not match:
        return None
    pct = float(match.group(1)) / 100
    name = raw[: match.start()].strip()
    name = _TRAILING_DASH_RE.sub("", name).strip()
    return name, pct


def _row(tab: str, section: str, metric_label: str, month: str,
         value_numeric: float | None, value_raw: str, is_flagged: bool) -> dict:
    return {
        "tab": tab, "section": section, "metric_label": metric_label, "month": month,
        "value_numeric": value_numeric, "value_raw": value_raw, "is_flagged": is_flagged,
    }


def _parse_tab(service, tab: str) -> tuple[list[dict], list[str]]:
    """Returns (rows, warnings) for one tab. Raises ValueError if the header
    row has no recognizable month columns at all — caller treats that as a
    fail-loud structure-change condition for the whole tab."""
    data = _get_range(service, f"{tab}!{_RANGE_COLS}")
    if not data:
        raise ValueError("tab is empty")

    header_row = data[0]
    year = int(tab)
    month_cols = _month_columns(header_row, year)
    if not month_cols:
        raise ValueError("no month columns found in header row")

    rows_out: list[dict] = []
    warnings: list[str] = []
    current_section: str | None = None
    skip_section = False

    for raw_row in data[1:]:
        label = (raw_row[0] if raw_row else "").strip()
        if not label:
            continue  # blank separator OR the known stray artifact row — same generic skip

        month_values = {idx: (raw_row[idx] if idx < len(raw_row) else "") for idx in month_cols}
        has_month_data = any(not _is_blank(v) for v in month_values.values())

        if not has_month_data:
            current_section = label
            skip_section = label in SKIP_SECTIONS
            if label not in KNOWN_SECTIONS and label not in SKIP_SECTIONS:
                warnings.append(f"tab {tab}: unrecognized section {label!r} — its rows will be skipped")
                skip_section = True
            continue

        if current_section is None or skip_section:
            continue

        if current_section == "Top Page Views":
            rank = TOP_PAGE_RANK_LABELS.get(label)
            if rank is None:
                warnings.append(f"tab {tab}: Top Page Views row with unexpected label {label!r} — skipped")
                continue
            metric_label = f"Top Page {rank}"
            for idx, month_iso in month_cols.items():
                raw_val = raw_row[idx] if idx < len(raw_row) else ""
                if _is_blank(raw_val):
                    continue
                parsed = _parse_top_page(raw_val)
                if parsed is None:
                    warnings.append(f"tab {tab}: unparseable Top Page Views cell {raw_val!r} ({metric_label}, {month_iso})")
                    rows_out.append(_row(tab, current_section, metric_label, month_iso, None, raw_val, True))
                    continue
                name, pct = parsed
                rows_out.append(_row(tab, current_section, metric_label, month_iso, pct, name, False))
            continue

        # Normal metric row (Social Media / Catalyt App Engagement / E Mails/Website / Aquisitions)
        metric_label = label
        for idx, month_iso in month_cols.items():
            raw_val = raw_row[idx] if idx < len(raw_row) else ""
            if _is_blank(raw_val):
                continue
            num = _parse_number(raw_val)
            rows_out.append(_row(tab, current_section, metric_label, month_iso, num, raw_val, num is None))

    return rows_out, warnings


# ── Sync ───────────────────────────────────────────────────────────────────

def sync(dry_run: bool = False) -> dict:
    service = _sheets_service()

    all_rows: list[dict] = []
    all_warnings: list[str] = []
    failed_tabs: list[str] = []

    for tab in TABS:
        try:
            rows, warnings = _parse_tab(service, tab)
        except ValueError as exc:
            failed_tabs.append(tab)
            log.error("tab %s failed structure check: %s", tab, exc)
            continue
        all_rows.extend(rows)
        all_warnings.extend(warnings)

    if failed_tabs:
        _alert(
            "Catalyst Tracking Sheet structure changed — could not find month "
            f"columns in tab(s): {', '.join(failed_tabs)}. Other tabs synced "
            "normally; this/these tab(s) were skipped. Check the sheet layout."
        )

    for w in all_warnings:
        log.warning(w)

    synced_at = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        conn = get_connection()
        create_tables(conn)
        try:
            conn.executemany(
                """
                INSERT INTO engagement_sheet_metrics
                    (tab, section, metric_label, month, value_numeric, value_raw, is_flagged, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tab, section, metric_label, month) DO UPDATE SET
                    value_numeric = excluded.value_numeric,
                    value_raw     = excluded.value_raw,
                    is_flagged    = excluded.is_flagged,
                    synced_at     = excluded.synced_at
                """,
                [
                    (r["tab"], r["section"], r["metric_label"], r["month"],
                     r["value_numeric"], r["value_raw"], int(r["is_flagged"]), synced_at)
                    for r in all_rows
                ],
            )
            conn.commit()
        finally:
            conn.close()

    result = {
        "rows": len(all_rows),
        "flagged": sum(1 for r in all_rows if r["is_flagged"]),
        "warnings": len(all_warnings),
        "tabs_ok": len(TABS) - len(failed_tabs),
        "tabs_failed": failed_tabs,
    }
    log.info("Synced %d row(s), %d flagged, %d warning(s), %d/%d tab(s) OK.",
              result["rows"], result["flagged"], result["warnings"], result["tabs_ok"], len(TABS))
    return result


# ── Query helpers (bot.py's team-chat web-diagnostics lookup) ───────────────

def latest_metric(section: str, metric_label: str) -> dict | None:
    """Most recent non-flagged month's value for one section/metric_label
    pair, or None if nothing's synced. Skips is_flagged rows (e.g. a "TBD"
    cell in the sheet) so a lookup doesn't answer with a non-answer when an
    earlier month has real data -- doesn't filter by `tab`, since month
    strings (YYYY-MM-01) already sort correctly across years."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT month, value_raw, value_numeric FROM engagement_sheet_metrics "
            "WHERE section = ? AND metric_label = ? AND is_flagged = 0 "
            "ORDER BY month DESC LIMIT 1",
            (section, metric_label),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def latest_top_pages() -> list[dict]:
    """Top Page 1-5 for the most recent month that has any Top Page Views
    data, ordered by rank (Top Page 1 first)."""
    conn = get_connection()
    try:
        latest = conn.execute(
            "SELECT MAX(month) AS m FROM engagement_sheet_metrics WHERE section = 'Top Page Views'"
        ).fetchone()
        if not latest or not latest["m"]:
            return []
        rows = conn.execute(
            "SELECT metric_label, month, value_raw, value_numeric FROM engagement_sheet_metrics "
            "WHERE section = 'Top Page Views' AND month = ? ORDER BY metric_label",
            (latest["m"],),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync the Catalyst Tracking Sheet into engagement_sheet_metrics.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and log without writing to the DB")
    args = parser.parse_args()

    try:
        out = sync(dry_run=args.dry_run)
    except Exception as exc:
        log.error("Sync failed: %s", exc)
        sys.exit(1)

    print(out)
