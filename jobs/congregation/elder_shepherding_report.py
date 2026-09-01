"""
Elder Shepherding Report — weekly per-deacon-group attendance rollup for Bill.

Counts-only summary (no names): for each deacon and the Unassigned pool, how
many of their people fall into each absence bucket. This sits above
deacon_reports.py's full-roster Master Shepherding Report -- it's for an
elder to scan group health at a glance, not to replace it. Once proven out,
the plan is a per-deacon version of this sent to each deacon individually
(most deacons aren't Telegram-onboarded yet, so that's not buildable today).

Buckets (days since last connect card or attendance record):
  2 wks    14-20 days ago
  3-5 wks  21-41 days ago   (same cutoff as shepherding_report.py's "at risk")
  6+ wks   42+ days ago, with 3+ total visits on file
           (same cutoff/gate as shepherding_report.py's "critical" -- the
           visit-count gate keeps a single old visitor record from reading
           as "critical"; a first-time visitor is unassigned by definition,
           see deacon_reports.py's module docstring, so this mostly matters
           for the Unassigned row)

Same base filters as shepherding_report.py's at-risk/critical sections:
members.status != 'inactive', not shepherding_exempt, member_status not in
(deceased/disconnected/non_local/snowbird), and at least one connect_cards
or attendance row on file. Same deacon-bucket exclusions as deacon_reports.py
(EXCLUDED_DEACON_VALUES) -- "Elders & Deacons" / "~ Admin" / "P Bill Yomes" /
"Inactive" are group labels, not addressable deacons, and are skipped here
too, per the same 2026-08-24 decision that scopes deacon_reports.py.

Telegram-only for now, delivered via jobs/telegram/send_to_person.py, sent
to Bill Yomes only. No email counterpart -- this is deliberately Telegram
as the primary channel.

Cron (Wednesday 6:15am, right after shepherding_report.py's 6:00am run --
matching its live schedule, which corrects the docstring-stated Monday and
actually runs Wednesday, after missed_report.py's Tuesday 7am corrected
attendance pass):
  15 6 * * 3 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.elder_shepherding_report \
    >> /home/billyomes/watson/logs/elder_shepherding_report.log 2>&1

Usage:
  python3 -m jobs.congregation.elder_shepherding_report
"""

import logging
import os
from datetime import date

from dotenv import load_dotenv

from core.database import get_connection
from core.vacation import vacation_gate
from jobs.congregation.deacon_reports import EXCLUDED_DEACON_VALUES, list_deacons
from jobs.connect_cards.reports import _conn
from jobs.connect_cards.shepherding_report import _today
from jobs.telegram.send_to_person import send_to_person

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [elder_shepherding_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RECIPIENT_NAME = "Bill Yomes"

_BLANK_DEACON_VALUES = {"none"}

_WK2_DAYS_MIN, _WK2_DAYS_MAX = 14, 20
_WK35_DAYS_MIN, _WK35_DAYS_MAX = 21, 41
_WK6PLUS_DAYS_MIN = 42
_WK6PLUS_VISIT_MIN = 3


def _bucket(days_since: int, visit_count: int) -> str | None:
    if _WK2_DAYS_MIN <= days_since <= _WK2_DAYS_MAX:
        return "2wk"
    if _WK35_DAYS_MIN <= days_since <= _WK35_DAYS_MAX:
        return "3-5wk"
    if days_since >= _WK6PLUS_DAYS_MIN and visit_count >= _WK6PLUS_VISIT_MIN:
        return "6wk"
    return None


def _raw_rows() -> list:
    """Every non-excluded member with at least one attendance record, plus
    their raw members.deacon value, last_seen, and total visit count."""
    with _conn() as conn:
        return conn.execute(
            """
            SELECT m.id, m.deacon,
                   MAX(
                     COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '1900-01-01'),
                     COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '1900-01-01')
                   ) AS last_seen,
                   (
                     SELECT COUNT(*) FROM (
                       SELECT service_date FROM connect_cards WHERE member_id = m.id
                       UNION
                       SELECT service_date FROM attendance WHERE member_id = m.id
                     )
                   ) AS visit_count
            FROM members m
            WHERE m.status != 'inactive'
              AND (m.shepherding_exempt IS NULL OR m.shepherding_exempt = 0)
              AND (m.member_status IS NULL OR m.member_status NOT IN ('deceased', 'disconnected', 'non_local', 'snowbird'))
              AND (
                EXISTS (SELECT 1 FROM connect_cards WHERE member_id = m.id)
                OR EXISTS (SELECT 1 FROM attendance WHERE member_id = m.id)
              )
            """
        ).fetchall()


def _group_key(raw_deacon: str | None) -> str | None:
    """Normalize a raw members.deacon value to a canonical deacon name, or
    None for the Unassigned pool. Returns '_excluded_' for group/bucket
    values that get no row at all (mirrors deacon_reports.py)."""
    if raw_deacon is None or raw_deacon.strip() == "" or raw_deacon.strip().lower() in _BLANK_DEACON_VALUES:
        return None
    if raw_deacon in EXCLUDED_DEACON_VALUES:
        return "_excluded_"
    return raw_deacon


def build_deacon_group_counts() -> list[dict]:
    """[{name, total, wk2, wk35, wk6plus}, ...] -- one row per real deacon
    (alphabetical, seeded at zero so every deacon appears even with no risk),
    plus a trailing Unassigned row."""
    deacons = list_deacons()
    counts = {d: {"name": d, "total": 0, "wk2": 0, "wk35": 0, "wk6plus": 0} for d in deacons}
    unassigned = {"name": "Unassigned", "total": 0, "wk2": 0, "wk35": 0, "wk6plus": 0}

    today = date.today()
    for r in _raw_rows():
        key = _group_key(r["deacon"])
        if key == "_excluded_":
            continue
        target = unassigned if key is None else counts.get(key)
        if target is None:
            continue  # deacon value not in list_deacons() (shouldn't happen)

        target["total"] += 1
        days_since = (today - date.fromisoformat(r["last_seen"])).days
        bucket = _bucket(days_since, r["visit_count"])
        if bucket == "2wk":
            target["wk2"] += 1
        elif bucket == "3-5wk":
            target["wk35"] += 1
        elif bucket == "6wk":
            target["wk6plus"] += 1

    rows = [counts[d] for d in deacons]
    rows.append(unassigned)
    return rows


def build_report_text() -> str:
    today = _today()
    rows = build_deacon_group_counts()

    lines = [f"\U0001f4ca Elder Shepherding Report — {today}", ""]
    tot2 = tot35 = tot6 = 0
    for r in rows:
        tot2 += r["wk2"]
        tot35 += r["wk35"]
        tot6 += r["wk6plus"]
        wk35_flag = " ⚠️" if r["wk35"] else ""
        wk6_flag = " \U0001f534" if r["wk6plus"] else ""
        lines.append(
            f"{r['name']}: {r['total']} — "
            f"2wk {r['wk2']}, 3-5wk {r['wk35']}{wk35_flag}, 6+wk {r['wk6plus']}{wk6_flag}"
        )

    lines.append("")
    tot_flag = " \U0001f534" if tot6 else ""
    lines.append(f"Totals — 2wk {tot2} | 3-5wk {tot35} | 6+wk {tot6}{tot_flag}")
    return "\n".join(lines)


def _person_id(conn, name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def send_elder_shepherding_report() -> bool:
    """Generate and send the report to Bill Yomes. Returns True if sent."""
    text = build_report_text()

    if vacation_gate("normal", "jobs.congregation.elder_shepherding_report", text):
        log.info("Vacation mode is on — Elder Shepherding Report suppressed (logged).")
        return False

    with get_connection() as conn:
        person_id = _person_id(conn, RECIPIENT_NAME)

    if person_id is None:
        log.error("No people row found for %r — cannot send.", RECIPIENT_NAME)
        return False

    if send_to_person(person_id, text):
        log.info("Sent Elder Shepherding Report to %s", RECIPIENT_NAME)
        return True

    log.warning("Failed to send Elder Shepherding Report to %s (not onboarded?)", RECIPIENT_NAME)
    return False


if __name__ == "__main__":
    print("Generating and sending Elder Shepherding Report...")
    print(build_report_text())
    send_elder_shepherding_report()
