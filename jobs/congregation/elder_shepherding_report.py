"""
Elder Shepherding Report — weekly per-deacon-group attendance rollup for Bill.

Displayed to users as "Catalyst Shepherding Report" (Telegram message
header, wtsn.me/cat/shepherdingreport page title/heading) as of 2026-09-01
-- module/file/function names and this docstring keep the original
internal name deliberately, to avoid a disruptive rename touching cron
entries, log filenames, and imports for a purely cosmetic label change.

Counts-only summary (no names): for each deacon and the Unassigned pool, how
many of their people fall into each absence bucket. This sits above
deacon_reports.py's full-roster Master Shepherding Report -- it's for an
elder to scan group health at a glance, not to replace it. Once proven out,
the plan is a per-deacon version of this sent to each deacon individually
(most deacons aren't Telegram-onboarded yet, so that's not buildable today).

Buckets (days since last connect card or attendance record; Bill's ruling
2026-09-15 -- deliberately its own scale now, no longer pinned to
shepherding_report.py's 3-5/6+ week "at risk"/"critical" cutoffs, since
missing one Sunday isn't a pastoral concern but missing two is):
  current   0-13 days ago    (missed 0-1 Sunday)
  at_risk   14-27 days ago   (missed 2-3 Sundays)
  critical  28+ days ago
            (no visit-count gate as of 2026-09-16 -- every listed member
            gets one of these three, so the totals always sum to the full
            roster; a first-time visitor is unassigned by definition, see
            deacon_reports.py's module docstring, so this mostly matters
            for the Unassigned row)

Same base filters as shepherding_report.py's at-risk/critical sections:
members.active not in (disconnected, deceased), residency = local, and
at least one connect_cards
or attendance row on file. Same deacon-bucket exclusions as deacon_reports.py
(EXCLUDED_DEACON_VALUES) -- "Elders & Deacons" / "~ Admin" / "P Bill Yomes" /
"Inactive" are group labels, not addressable deacons, and are skipped here
too, per the same 2026-08-24 decision that scopes deacon_reports.py.

Telegram-only, delivered via jobs/telegram/send_to_person.py. Sent to Bill
Yomes, Jim Bouchat, and Bill Crook (2026-09-01 -- expanded from Bill Yomes
only, once Bill approved the wtsn.me/cat/shepherdingreport page; Jim and
Bill Crook are, as of this date, the only two deacons onboarded to
Telegram of the 7 in list_deacons(), so this is everyone reachable today,
not a deliberate subset). No email counterpart -- this is deliberately
Telegram as the primary channel.

Named, per-group breakdown (2026-09-01): the Telegram message stays
counts-only to stay well under Telegram's character limit -- full names,
grouped by deacon and sorted worst-bucket-first, live instead at
wtsn.me/cat/shepherdingreport (build_deacon_group_names() below, served by
jobs/congregation/elder_shepherding_report_web.py). The Telegram message
links to it. That page is a 'custom' public_tools row gated the same way
every other wtsn.me tool is -- draft until Bill taps Go Live on the
first-deploy Telegram prompt, then reachable by anyone with the URL (no
further per-viewer auth, same as /cat/attendance and /cat/deacons).

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

RECIPIENT_NAMES = ("Bill Yomes", "Jim Bouchat", "Bill Crook")
REPORT_URL = "https://wtsn.me/cat/shepherdingreport"

_BLANK_DEACON_VALUES = {"none", "--"}

# Bill's ruling 2026-09-15: missing one Sunday isn't a concern (Current),
# missing two starts to matter (At Risk), four+ is Critical. Days are
# inclusive Sunday-to-Sunday windows (7 days/wk), so "missed 1 wk" covers
# same-day through just under 2 wks out, etc.
_CURRENT_DAYS_MIN, _CURRENT_DAYS_MAX = 0, 13
_AT_RISK_DAYS_MIN, _AT_RISK_DAYS_MAX = 14, 27
_CRITICAL_DAYS_MIN = 28


def _bucket(days_since: int) -> str:
    """Always returns one of the three buckets -- no more visit-count gate
    (dropped 2026-09-16, Bill's call: every listed member should land in a
    visible box so Current+At Risk+Critical always sums to the full
    roster, rather than a rare old first-timer silently falling through
    all three)."""
    if _CURRENT_DAYS_MIN <= days_since <= _CURRENT_DAYS_MAX:
        return "current"
    if _AT_RISK_DAYS_MIN <= days_since <= _AT_RISK_DAYS_MAX:
        return "at_risk"
    return "critical"


def _raw_rows() -> list:
    """Every non-excluded member with at least one attendance record, plus
    their name, raw members.deacon value, last_seen, and total visit count.

    2026-09-16 bug fix: this used to gate on `m.status != 'inactive'`, but
    `status` is a membership-type label (only ever 'member'/'visitor' in
    live data) -- not an activity flag, so that condition was always true
    and did nothing. The real inactive flag is `m.active` (set to 0 via
    wtsn.me/cat/attendance's Member Management panel), which is what
    _member_engagement_tiers() below already correctly checks -- meaning
    someone marked Inactive would silently vanish from Consistency but
    keep showing up here. Switched to `m.active = 1` so both sections of
    the report agree on who counts as active.

    2026-09-24: `m.active = 1` further replaced with
    `m.active NOT IN ('disconnected', 'deceased')` (the new 4-value
    column, see ~/.claude/plans/zesty-cuddling-robin.md) plus a separate
    `m.residency = 'local'` check -- previously this also filtered on
    member_status excluding non_local/snowbird, which residency now
    covers on its own."""
    with _conn() as conn:
        return conn.execute(
            """
            SELECT m.id, m.name, m.deacon, m.email, m.phone,
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
            WHERE m.active NOT IN ('disconnected', 'deceased')
              AND (m.residency IS NULL OR m.residency = 'local')
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
    """[{name, total, current, at_risk, critical}, ...] -- one row per real
    deacon (alphabetical, seeded at zero so every deacon appears even with no
    risk), plus a trailing Unassigned row."""
    deacons = list_deacons()
    counts = {d: {"name": d, "total": 0, "current": 0, "at_risk": 0, "critical": 0} for d in deacons}
    unassigned = {"name": "Unassigned", "total": 0, "current": 0, "at_risk": 0, "critical": 0}

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
        bucket = _bucket(days_since)
        if bucket == "current":
            target["current"] += 1
        elif bucket == "at_risk":
            target["at_risk"] += 1
        elif bucket == "critical":
            target["critical"] += 1

    rows = [counts[d] for d in deacons]
    rows.append(unassigned)
    return rows


def _last_name_key(name: str) -> str:
    """Sort key by last name -- same last-whitespace-token heuristic
    jobs/congregation/attendance_web.py uses (members.name is one free-text
    field, no separate first/last columns)."""
    parts = (name or "").strip().split()
    return parts[-1].lower() if parts else ""


_BUCKET_ORDER = {"critical": 0, "at_risk": 1, "current": 2}


def _member_engagement_tiers(conn) -> dict:
    """{member_id: 'consistent'|'active'|'occasional'|'lapsed'} -- last-8-
    service-date visit-count thresholds, loosely based on
    jobs/connect_cards/state_of_church.py's _engagement_tiers() but
    diverged 2026-09-16 (Bill's call, deacon app only -- state_of_church.py
    is untouched):
      - counts connect_cards as a visit alongside attendance (a UNION of
        both, same as _raw_rows()'s last_seen/visit_count above), since the
        Last Sunday bucket already credits a connect card as being seen --
        Consistency previously ignored connect_cards entirely, so someone
        with connect-card-only history could show as "Current" up top but
        be invisible down here.
      - always returns one of the four tiers (no more None/"outside all
        tiers" case, which used to require a last-24-service-date check to
        rule out) -- last8 == 0 is now just Lapsed, so Consistent+Active+
        Occasional+Lapsed always sums to the full roster, matching the
        Last Sunday buckets' behavior above."""
    rows = conn.execute(
        """
        WITH visits AS (
            SELECT member_id, service_date FROM attendance
            UNION
            SELECT member_id, service_date FROM connect_cards
        ),
        last8 AS (
            SELECT DISTINCT service_date FROM visits ORDER BY service_date DESC LIMIT 8
        )
        SELECT
            m.id,
            SUM(CASE WHEN v.service_date IN (SELECT service_date FROM last8)  THEN 1 ELSE 0 END) AS last8_count
        FROM members m
        LEFT JOIN visits v ON v.member_id = m.id
        WHERE m.active NOT IN ('disconnected', 'deceased')
        GROUP BY m.id
        """
    ).fetchall()

    tiers = {}
    for r in rows:
        last8 = r["last8_count"] or 0
        if last8 >= 6:
            tiers[r["id"]] = "consistent"
        elif 3 <= last8 <= 5:
            tiers[r["id"]] = "active"
        elif 1 <= last8 <= 2:
            tiers[r["id"]] = "occasional"
        else:
            tiers[r["id"]] = "lapsed"
    return tiers


def build_deacon_group_names() -> list[dict]:
    """[{name, members: [{id, name, bucket, days_since, last_seen, email,
    phone, engagement}, ...]}, ...] -- one row per real deacon (same
    list_deacons() order as build_deacon_group_counts()), plus a trailing
    Unassigned row. Every non-excluded member with attendance history
    appears exactly once, always under one of the three current/at_risk/
    critical `bucket` values (see _bucket()) -- no None case since
    2026-09-16. `id` and `last_seen` (raw ISO date) power the
    "update last seen" date-picker on wtsn.me/cat/shepherdingreport (see
    elder_shepherding_report_web.py's set_last_seen route); `days_since` is
    the exact day count the coarse `bucket` is derived from, shown as a
    precise week count in that same UI instead of the bucket's range label.
    `email`/`phone` are raw members.* values (None if blank) -- power the
    call/text/email contact icons. `engagement` is the consistent/active/
    occasional/lapsed 8-week-window classification computed per-member by
    _member_engagement_tiers() (diverged from state_of_church.py's version
    2026-09-16, see that function's docstring). None of these five are
    used in the Telegram message. Each group's members are pre-sorted
    worst-bucket-first, then
    by last name, so the page renders top to bottom with no client-side
    sort. Powers wtsn.me/cat/shepherdingreport -- kept separate from
    build_deacon_group_counts() because Telegram's character limit is the
    reason that one stays counts-only."""
    deacons = list_deacons()
    groups = {d: {"name": d, "members": []} for d in deacons}
    unassigned = {"name": "Unassigned", "members": []}

    today = date.today()
    with _conn() as conn:
        engagement = _member_engagement_tiers(conn)

    for r in _raw_rows():
        key = _group_key(r["deacon"])
        if key == "_excluded_":
            continue
        target = unassigned if key is None else groups.get(key)
        if target is None:
            continue

        days_since = (today - date.fromisoformat(r["last_seen"])).days
        bucket = _bucket(days_since)
        target["members"].append({
            "id": r["id"],
            "name": r["name"],
            "bucket": bucket,
            "days_since": days_since,
            "last_seen": r["last_seen"],
            "email": r["email"] or None,
            "phone": r["phone"] or None,
            "engagement": engagement.get(r["id"]),
        })

    rows = [groups[d] for d in deacons]
    rows.append(unassigned)
    for row in rows:
        row["members"].sort(key=lambda m: (_BUCKET_ORDER[m["bucket"]], _last_name_key(m["name"])))
    return rows


def build_report_text() -> str:
    today = _today()
    rows = build_deacon_group_counts()

    lines = [f"\U0001f4ca Catalyst Shepherding Report: {today}", ""]
    tot_current = tot_at_risk = tot_critical = 0
    for r in rows:
        tot_current += r["current"]
        tot_at_risk += r["at_risk"]
        tot_critical += r["critical"]
        at_risk_flag = " ⚠️" if r["at_risk"] else ""
        critical_flag = " \U0001f534" if r["critical"] else ""
        lines.append(
            f"{r['name']}: {r['total']}, "
            f"current {r['current']}, at-risk {r['at_risk']}{at_risk_flag}, critical {r['critical']}{critical_flag}"
        )

    lines.append("")
    tot_flag = " \U0001f534" if tot_critical else ""
    lines.append(f"Totals: current {tot_current} | at-risk {tot_at_risk} | critical {tot_critical}{tot_flag}")
    lines.append("")
    lines.append(f"Names by group: {REPORT_URL}")
    return "\n".join(lines)


def _person_id(conn, name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def send_elder_shepherding_report() -> bool:
    """Generate and send the report to everyone in RECIPIENT_NAMES.
    Returns True if at least one send succeeded."""
    text = build_report_text()

    if vacation_gate("normal", "jobs.congregation.elder_shepherding_report", text):
        log.info("Vacation mode is on — Elder Shepherding Report suppressed (logged).")
        return False

    with get_connection() as conn:
        ids = {name: _person_id(conn, name) for name in RECIPIENT_NAMES}

    sent_any = False
    for name, person_id in ids.items():
        if person_id is None:
            log.error("No people row found for %r — skipped", name)
            continue
        if send_to_person(person_id, text):
            log.info("Sent Elder Shepherding Report to %s", name)
            sent_any = True
        else:
            log.warning("Failed to send Elder Shepherding Report to %s (not onboarded?)", name)

    return sent_any


if __name__ == "__main__":
    print("Generating and sending Elder Shepherding Report...")
    print(build_report_text())
    send_elder_shepherding_report()
