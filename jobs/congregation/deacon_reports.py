"""
Deacon Reports — per-deacon and master shepherding-care reports.

Each report is a full roster of the people/families actually assigned
(members.deacon), grouped into family cards by household_id. Every person's
card shows: attendance status (last seen, with an At Risk / Critical flag
once they've missed 3+ / 6+ weeks), any prayer requests from the last 90
days, and any next steps they've taken in the last 90 days. There is no
"First-Time Visitors" section anywhere in this module -- a first-time
visitor is by definition still unassigned (deacon IS NULL/blank), so they
only ever show up in the Unassigned pool, never under a deacon.

Scopes:
  - one deacon's assigned people (members.deacon)                   -> Deacon Report
  - the unassigned pool (deacon IS NULL/blank)                      -> Unassigned Report
  - Jim Bouchat's own list, then every other deacon's list, then
    the unassigned pool (Jim is the elder over shepherding)         -> Master Shepherding Report
  - the 7 deacons' own households (deacon + spouse, via
    household_id) -- Bill's personal oversight of deacon families   -> Pastor Bill's List

Leadership-only prayer requests are held back from individual Deacon
Reports (deacons aren't leadership-tier) but included in the Master
Report, the Unassigned report (-> elders), and Pastor Bill's List.

members.deacon also carries three group/bucket values that are not a single
addressable deacon: "Elders & Deacons", "~ Admin", "P Bill Yomes". These are
excluded entirely from this module (list_deacons(), the master report, and
the unassigned pool all skip them) per Bill's 2026-08-24 decision.

Manual send only — nothing here runs on a cron. Every send goes through a
dashboard preview first (see jobs/dashboard/app.py's /api/deacon-reports/*
routes); this module never sends without an explicit send call.
"""

import os
import re
from datetime import date

from dotenv import load_dotenv

from jobs.connect_cards.reports import _conn, _wrap
from jobs.connect_cards.shepherding_report import (
    _STEP_NAMES,
    _cutoff,
    _display_name,
    _fmt_date,
    _today,
)
from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

BILL_EMAIL = os.getenv("BILL_EMAIL", "bill.yomes@gmail.com")
ELDERS_EMAIL = "elders@catalyst302.com"
MASTER_ELDER_NAME = "Jim Bouchat"
PASTOR_LIST_LABEL = "Pastor Bill's List — Deacons & Families"

# members.deacon values that are group/bucket labels, not one addressable deacon.
# "Inactive" (added 2026-08-31) is the deacons_web.py roster's shepherding-roll
# bucket for members whose campus_preference is also "Inactive" -- deliberately
# excluded here so it never gets its own Master Report section or email, same
# as the other three. Unlike those three, it IS a settable value in
# deacons_web.py's PATCH (a deacon can move someone onto or off of it) --
# see that module's own _BLOCKED_DEACON_VALUES, which excludes "Inactive"
# from the reserved/blocked set this constant would otherwise imply.
EXCLUDED_DEACON_VALUES = {"Elders & Deacons", "~ Admin", "P Bill Yomes", "Inactive"}

# members.deacon value -> canonical members.name, for values that don't match
# the member record's name exactly (see deacon_directory_report_20260824-*.md
# "Likely Fine" section for the 'Tom Smith' / 'Thomas Smith' mismatch)
DEACON_NAME_ALIASES = {"Tom Smith": "Thomas Smith"}

# The source spreadsheet wrote the literal text "None" (not a blank cell) for
# 10 Inactive Partner rows -- treat it the same as a true NULL/blank deacon,
# not as a deacon named "None".
_BLANK_DEACON_VALUES = {"none"}

_DEACON_HEADER_STYLE = (
    "background:#1e3a5f;color:#fff;padding:10px 16px;border-radius:4px;"
    "font-size:1.1em;font-weight:bold;margin:32px 0 12px"
)


# ── Deacon roster ──────────────────────────────────────────────────────────────

def list_deacons() -> list[str]:
    """Real, individually-addressable deacon names — excludes bucket values and blanks."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT deacon FROM members "
            "WHERE deacon IS NOT NULL AND TRIM(deacon) != ''"
        ).fetchall()
    names = {
        r["deacon"] for r in rows
        if r["deacon"] not in EXCLUDED_DEACON_VALUES
        and r["deacon"].strip().lower() not in _BLANK_DEACON_VALUES
    }
    return sorted(names)


def deacon_email(deacon_name: str) -> str | None:
    """Look up a deacon's own email via their member record (deacons are members too)."""
    lookup_name = DEACON_NAME_ALIASES.get(deacon_name, deacon_name)
    with _conn() as conn:
        row = conn.execute(
            "SELECT email FROM members WHERE name = ? AND email IS NOT NULL AND TRIM(email) != ''",
            (lookup_name,),
        ).fetchone()
    return row["email"] if row else None


def deacon_counts() -> list[dict]:
    """[{kind, name, label, count, email}, ...] for the dashboard's deacon picker:
    one row per real deacon (kind='deacon'), one Unassigned row (kind='unassigned',
    sent to elders@catalyst302.com), and one Pastor Bill's List row
    (kind='pastor_list', sent to Bill himself)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT deacon, COUNT(*) AS c FROM members "
            "WHERE deacon IS NOT NULL AND TRIM(deacon) != '' "
            "GROUP BY deacon"
        ).fetchall()
        unassigned = conn.execute(
            "SELECT COUNT(*) AS c FROM members "
            "WHERE deacon IS NULL OR TRIM(deacon) = '' OR LOWER(TRIM(deacon)) = 'none'"
        ).fetchone()["c"]

    out = [
        {"kind": "deacon", "name": r["deacon"], "label": r["deacon"], "count": r["c"], "email": deacon_email(r["deacon"])}
        for r in rows
        if r["deacon"] not in EXCLUDED_DEACON_VALUES
        and r["deacon"].strip().lower() not in _BLANK_DEACON_VALUES
    ]
    out.sort(key=lambda d: d["name"])

    household_ids = _deacon_household_ids()
    if household_ids:
        with _conn() as conn:
            placeholders = ",".join("?" for _ in household_ids)
            pastor_list_count = conn.execute(
                f"SELECT COUNT(*) AS c FROM members WHERE household_id IN ({placeholders})",
                household_ids,
            ).fetchone()["c"]
    else:
        pastor_list_count = 0
    out.insert(0, {
        "kind": "pastor_list", "name": None, "label": PASTOR_LIST_LABEL,
        "count": pastor_list_count, "email": BILL_EMAIL,
    })

    out.append({"kind": "unassigned", "name": None, "label": "Unassigned", "count": unassigned, "email": ELDERS_EMAIL})
    return out


def _deacon_clause(deacon: str | None) -> tuple[str, tuple]:
    """SQL fragment + params scoping m.deacon to one deacon, or the unassigned pool
    (true NULL/blank, and the literal text "None" some source rows carry)."""
    if deacon is None:
        return "(m.deacon IS NULL OR TRIM(m.deacon) = '' OR LOWER(TRIM(m.deacon)) = 'none')", ()
    return "m.deacon = ?", (deacon,)


def _deacon_household_ids() -> list[str]:
    """household_id values for each real deacon's own household — used to build
    Pastor Bill's list (deacons, their wives, and families). Deliberately does
    NOT reuse the "Elders & Deacons" bucket: that bucket also holds Mike and
    Samantha Latham, whom the deacon-directory import flagged as an open
    question ("Should he/she be assigned to a deacon?") rather than confirmed
    deacon-family members, so pulling from the bucket would silently include
    them. household_id is derived straight from the 7 confirmed deacons instead."""
    deacons = list_deacons()
    if not deacons:
        return []
    lookup_names = [DEACON_NAME_ALIASES.get(d, d) for d in deacons]
    placeholders = ",".join("?" for _ in lookup_names)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT household_id FROM members "
            f"WHERE name IN ({placeholders}) AND household_id IS NOT NULL AND TRIM(household_id) != ''",
            lookup_names,
        ).fetchall()
    return [r["household_id"] for r in rows]


def _household_clause(household_ids: list[str]) -> tuple[str, tuple]:
    """SQL fragment + params scoping m.household_id to a specific set of households."""
    if not household_ids:
        return "1=0", ()
    placeholders = ",".join("?" for _ in household_ids)
    return f"m.household_id IN ({placeholders})", tuple(household_ids)


# ── Roster ───────────────────────────────────────────────────────────────────
#
# A deacon report is the full list of people/families actually assigned to
# that deacon (via members.deacon) -- not a slice by risk category. A
# first-time visitor is, by definition, still unassigned (falls in the
# Unassigned pool), so this roster never surfaces one under a deacon: no
# separate "First-Time Visitors" section exists here at all.

_LAST_SEEN_NEVER = "1900-01-01"
_PRAYER_WINDOW_DAYS = 90
_STEPS_WINDOW_DAYS = 90

_STATUS_LABELS = {
    "disconnected": "Disconnected",
    "non_local": "Non-local",
    "snowbird": "Snowbird",
    "deceased": "Deceased",
}


def _surname(r) -> str:
    """Last whitespace-separated token of the member's name, lowercased, for
    alphabetizing families by last name."""
    parts = (r["name"] or "").strip().split()
    return parts[-1].lower() if parts else ""


def _attendance_line(last_seen: str) -> str:
    if not last_seen or last_seen == _LAST_SEEN_NEVER:
        return "<span style='color:#999'>No attendance on file</span>"
    weeks = (date.today() - date.fromisoformat(last_seen)).days // 7
    date_txt = f"Last seen: {_fmt_date(last_seen)} ({weeks} wks ago)"
    if weeks >= 6:
        return f"<span style='color:#ff6b6b;font-weight:bold'>&#128308; Critical — {date_txt}</span>"
    if weeks >= 3:
        return f"<span style='color:#f0c040;font-weight:bold'>&#9888;&#65039; At Risk — {date_txt}</span>"
    return f"<span style='color:#7eb8f7'>{date_txt}</span>"


def _person_card(r, prayers: list, steps: list) -> str:
    name = _display_name(r["name"]) or "(no name)"
    contact = " · ".join(x for x in (r["email"], r["phone"]) if x) or "—"

    badges = ""
    if r["deacon_status"]:
        badges += f"<span class='badge campus' style='margin-left:6px'>{r['deacon_status']}</span>"
    status_label = _STATUS_LABELS.get(r["member_status"] or "")
    if status_label:
        badges += f"<span class='badge private' style='margin-left:6px'>{status_label}</span>"

    prayer_html = ""
    if prayers:
        items = "".join(
            f"<li>{p['request_text']} <span style='color:#999;font-size:11px'>({_fmt_date(p['date'])})</span></li>"
            for p in prayers
        )
        prayer_html = (
            f"<div style='margin-top:6px;font-size:12px'><strong>Prayer requests:</strong>"
            f"<ul style='margin:2px 0 0 18px;padding:0'>{items}</ul></div>"
        )

    steps_html = ""
    if steps:
        items = "".join(
            f"<li>{_STEP_NAMES.get(s['step'], s['step'])} "
            f"<span style='color:#999;font-size:11px'>({_fmt_date(s['date'])})</span></li>"
            for s in steps
        )
        steps_html = (
            f"<div style='margin-top:6px;font-size:12px'><strong>Next steps taken:</strong>"
            f"<ul style='margin:2px 0 0 18px;padding:0'>{items}</ul></div>"
        )

    return (
        f"<div style='padding:10px 0;border-bottom:1px solid #eee'>"
        f"<div><strong>{name}</strong>{badges}</div>"
        f"<div style='font-size:12px;color:#888;margin-top:2px'>{contact}</div>"
        f"<div style='font-size:12px;margin-top:4px'>{_attendance_line(r['last_seen'])}</div>"
        f"{prayer_html}{steps_html}"
        f"</div>"
    )


def _family_card(group: list, prayers_by_member: dict, steps_by_member: dict) -> str:
    header = ""
    if len(group) > 1:
        surnames = []
        for r in group:
            parts = (r["name"] or "").split()
            if parts and parts[-1] not in surnames:
                surnames.append(parts[-1])
        fam_label = f"{'/'.join(surnames)} Family" if surnames else "Household"
        header = f"<div style='font-weight:600;font-size:13px;color:#555;margin-top:16px'>{fam_label}</div>"
    people = "".join(
        _person_card(r, prayers_by_member.get(r["id"], []), steps_by_member.get(r["id"], []))
        for r in group
    )
    return f"<div style='margin-bottom:4px'>{header}{people}</div>"


def _build_roster(clause: str, clause_params: tuple, include_leadership_only: bool) -> tuple[str, int, int, int]:
    """Every person in scope, grouped into family cards by household_id.
    Returns (html, total_people, critical_count, at_risk_count)."""
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id, m.name, m.email, m.phone, m.deacon_status, m.member_status,
                   m.household_id,
                   MAX(
                     COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '{_LAST_SEEN_NEVER}'),
                     COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '{_LAST_SEEN_NEVER}')
                   ) AS last_seen
            FROM members m
            WHERE {clause}
              AND (m.member_status IS NULL OR m.member_status != 'deceased')
            GROUP BY m.id
            ORDER BY m.name
            """,
            clause_params,
        ).fetchall()

        prayers_by_member: dict = {}
        steps_by_member: dict = {}
        member_ids = [r["id"] for r in rows]
        if member_ids:
            placeholders = ",".join("?" for _ in member_ids)

            prayer_cutoff = _cutoff(_PRAYER_WINDOW_DAYS)
            for pr in conn.execute(
                f"SELECT member_id, request_text, leadership_only, date FROM prayer_requests "
                f"WHERE member_id IN ({placeholders}) AND date >= ? ORDER BY date DESC",
                member_ids + [prayer_cutoff],
            ):
                if pr["leadership_only"] and not include_leadership_only:
                    continue
                prayers_by_member.setdefault(pr["member_id"], []).append(pr)

            steps_cutoff = _cutoff(_STEPS_WINDOW_DAYS)
            for ns in conn.execute(
                f"SELECT member_id, step, date FROM next_steps "
                f"WHERE member_id IN ({placeholders}) AND date >= ? ORDER BY date DESC",
                member_ids + [steps_cutoff],
            ):
                steps_by_member.setdefault(ns["member_id"], []).append(ns)

    if not rows:
        return "<p class='empty'>No one assigned.</p>", 0, 0, 0

    # Group by household_id via a dict (not adjacency in `rows`) so families
    # collect together regardless of fetch order.
    groups_by_key: dict = {}
    for r in rows:
        key = r["household_id"] or f"_solo_{r['id']}"
        groups_by_key.setdefault(key, []).append(r)
    groups = list(groups_by_key.values())

    # Alphabetize by last name so families stay grouped together under their
    # shared surname (a blended household sorts under its earliest surname).
    groups.sort(key=lambda g: min((_surname(r) for r in g), default=""))

    # Inactive Partner families sort last -- a family with even one still-active
    # member stays in normal order; only fully-inactive families are pushed down.
    # (Stable sort: alphabetical order from above is preserved within each bucket.)
    groups.sort(key=lambda g: all(r["deacon_status"] == "Inactive Partner" for r in g))

    critical_count = 0
    at_risk_count = 0
    for r in rows:
        last_seen = r["last_seen"]
        if not last_seen or last_seen == _LAST_SEEN_NEVER:
            continue
        weeks = (date.today() - date.fromisoformat(last_seen)).days // 7
        if weeks >= 6:
            critical_count += 1
        elif weeks >= 3:
            at_risk_count += 1

    html = "".join(_family_card(g, prayers_by_member, steps_by_member) for g in groups)
    return html, len(rows), critical_count, at_risk_count


# ── Composition ────────────────────────────────────────────────────────────────

def _scope_section_html(clause: str, clause_params: tuple, label: str, include_leadership_only: bool = True) -> tuple[str, int]:
    """One scope's (a deacon, the unassigned pool, or a household set) full
    roster, under a banner heading summarizing attendance risk."""
    roster_html, total, critical, at_risk = _build_roster(clause, clause_params, include_leadership_only)
    stat_bits = [f"{total} {'person' if total == 1 else 'people'}"]
    if critical:
        stat_bits.append(f"{critical} critical")
    if at_risk:
        stat_bits.append(f"{at_risk} at risk")
    banner = f"<div style='{_DEACON_HEADER_STYLE}'>{label} — {', '.join(stat_bits)}</div>"
    return banner + roster_html, total


# ── Public API: generate (preview-safe, never sends) ───────────────────────────

def generate_deacon_report(deacon_name: str) -> tuple[str, str]:
    """(subject, html) — the full roster of everyone assigned to this deacon.
    Leadership-only prayer requests are held back; deacons aren't leadership-tier."""
    if deacon_name not in list_deacons():
        raise ValueError(f"{deacon_name!r} is not a recognized deacon.")
    today = _today()
    subject = f"Deacon Report — {deacon_name} — {today}"
    clause, params = _deacon_clause(deacon_name)
    body, _ = _scope_section_html(clause, params, deacon_name, include_leadership_only=False)
    return subject, _wrap(f"Deacon Report — {deacon_name}", today, body)


def generate_unassigned_report() -> tuple[str, str]:
    """(subject, html) for the unassigned pool — for elders to assign deacons."""
    today = _today()
    subject = f"Unassigned Shepherding Report — {today}"
    clause, params = _deacon_clause(None)
    body, _ = _scope_section_html(clause, params, "Unassigned — Needs Deacon Assignment")
    return subject, _wrap("Unassigned Shepherding Report", today, body)


def generate_master_shepherding_report() -> tuple[str, str]:
    """(subject, html) — Jim Bouchat's own list first, then every other deacon's
    list, then the unassigned pool. Jim is the elder over shepherding."""
    today = _today()
    subject = f"Master Shepherding Report — {today}"

    deacons = list_deacons()
    ordered = [MASTER_ELDER_NAME] + [d for d in deacons if d != MASTER_ELDER_NAME]

    parts = []
    for d in ordered:
        clause, params = _deacon_clause(d)
        html, _ = _scope_section_html(clause, params, d)
        parts.append(html)

    clause, params = _deacon_clause(None)
    unassigned_html, _ = _scope_section_html(clause, params, "Unassigned — Needs Deacon Assignment")
    parts.append(unassigned_html)

    return subject, _wrap("Master Shepherding Report", today, "".join(parts))


def generate_pastor_list_report() -> tuple[str, str]:
    """(subject, html) for Pastor Bill's own list — the 7 deacons plus their
    wives/families (grouped by household_id off the confirmed deacon roster).
    This is Bill's personal oversight of the deacon households, separate from
    the Master Shepherding Report (which is Jim's, as elder over shepherding)."""
    today = _today()
    subject = f"Pastor Bill's List — {today}"
    household_ids = _deacon_household_ids()
    clause, params = _household_clause(household_ids)
    body, _ = _scope_section_html(clause, params, PASTOR_LIST_LABEL)
    return subject, _wrap(PASTOR_LIST_LABEL, today, body)


# ── Public API: send (approval happens in the dashboard before these are ever
#    called — nothing in this module sends on its own) ─────────────────────────

def _send(to_email: str, to_name: str, subject: str, html: str) -> None:
    text_fallback = re.sub(r"<[^>]+>", "", html)
    result = send_email(
        to_email=to_email, to_name=to_name, subject=subject,
        text_body=text_fallback, html_body=html, include_signature=False,
    )
    if not result["success"]:
        raise RuntimeError(f"Brevo send failed for {to_email}: {result['error']}")


def send_deacon_report(deacon_name: str) -> str:
    """Generate + send one deacon's report to their own email. Returns the email sent to."""
    email = deacon_email(deacon_name)
    if not email:
        raise RuntimeError(f"No email on file for deacon {deacon_name!r}.")
    subject, html = generate_deacon_report(deacon_name)
    _send(email, deacon_name, subject, html)
    print(f"Sent: {subject!r} → {email}")
    return email


def send_pastor_list_report() -> str:
    """Generate + send Pastor Bill's list to Bill's own email. Returns the email sent to."""
    subject, html = generate_pastor_list_report()
    _send(BILL_EMAIL, "", subject, html)
    print(f"Sent: {subject!r} → {BILL_EMAIL}")
    return BILL_EMAIL


def send_unassigned_report() -> str:
    """Generate + send the unassigned pool to elders@catalyst302.com. Returns the email sent to."""
    subject, html = generate_unassigned_report()
    _send(ELDERS_EMAIL, "Elders", subject, html)
    print(f"Sent: {subject!r} → {ELDERS_EMAIL}")
    return ELDERS_EMAIL


def send_master_shepherding_report(also_to_bill: bool = False, also_to_elders: bool = False) -> list[str]:
    """Send the master report to Jim Bouchat, plus optionally Bill and/or elders@catalyst302.com.
    Returns the list of email addresses actually sent to."""
    jim_email = deacon_email(MASTER_ELDER_NAME)
    if not jim_email:
        raise RuntimeError(f"No email on file for {MASTER_ELDER_NAME!r}.")

    subject, html = generate_master_shepherding_report()

    recipients = [(jim_email, MASTER_ELDER_NAME)]
    if also_to_bill and BILL_EMAIL:
        recipients.append((BILL_EMAIL, ""))
    if also_to_elders:
        recipients.append((ELDERS_EMAIL, "Elders"))

    sent_to = []
    for to_email, to_name in recipients:
        _send(to_email, to_name, subject, html)
        sent_to.append(to_email)
    print(f"Sent: {subject!r} → {', '.join(sent_to)}")
    return sent_to
