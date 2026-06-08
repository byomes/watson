"""Pastoral query reports against congregation.db."""
from collections import defaultdict
from datetime import date, timedelta

from jobs.connect_cards.reports import _CSS, _wrap, _conn, _subject

_STEP_NAMES = {
    "follow_jesus":     "Follow Jesus",
    "baptism":          "Baptism",
    "grow_faith":       "Grow in Faith",
    "catalyst_partner": "Catalyst Partner",
    "small_group":      "Small Group",
    "ministry_team":    "Ministry Team",
}


def _cutoff(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _today() -> str:
    return date.today().isoformat()


# ── 1. Next Steps ─────────────────────────────────────────────────────────────

def next_steps_report(weeks: int = 12) -> tuple[str, str]:
    cutoff = _cutoff(weeks * 7)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT ns.step, m.name, cc.campus, ns.date
            FROM next_steps ns
            JOIN members m ON m.id = ns.member_id
            JOIN connect_cards cc ON cc.id = ns.card_id
            WHERE ns.date >= ?
            ORDER BY ns.step, m.name
            """,
            (cutoff,),
        ).fetchall()

    title = f"Next Steps — Last {weeks} Weeks"
    subject = _subject(title, _today(), False)

    if not rows:
        body = f"<p class='empty'>No next steps recorded in the last {weeks} weeks.</p>"
        return subject, _wrap(title, _today(), body)

    by_step: dict = defaultdict(list)
    for r in rows:
        by_step[r["step"]].append(r)

    body = f"<p style='color:#888;font-size:.9em'>{len(rows)} next steps recorded in last {weeks} weeks</p>"
    for step_key, step_name in _STEP_NAMES.items():
        step_rows = by_step.get(step_key, [])
        if not step_rows:
            continue
        body += f"<h2>{step_name} ({len(step_rows)})</h2>"
        body += "<table><thead><tr><th>Name</th><th>Campus</th><th>Date</th></tr></thead><tbody>"
        for r in step_rows:
            body += (
                f"<tr><td>{r['name'] or '(no name)'}</td>"
                f"<td>{r['campus'] or '—'}</td>"
                f"<td>{r['date']}</td></tr>"
            )
        body += "</tbody></table>"

    return subject, _wrap(title, _today(), body)


# ── 2. Missed Weeks ───────────────────────────────────────────────────────────

def missed_weeks_report(weeks: int = 3) -> tuple[str, str]:
    cutoff = _cutoff(weeks * 7)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.name, m.campus_preference,
                   MAX(cc.service_date) AS last_seen,
                   CAST((julianday('now') - julianday(MAX(cc.service_date))) / 7 AS INTEGER)
                       AS weeks_absent
            FROM members m
            JOIN connect_cards cc ON cc.member_id = m.id
            WHERE m.status != 'inactive'
            GROUP BY m.id
            HAVING MAX(cc.service_date) < ?
            ORDER BY weeks_absent DESC
            """,
            (cutoff,),
        ).fetchall()

    title = f"Absent {weeks}+ Weeks"
    subject = _subject(title, _today(), False)

    if not rows:
        body = f"<p class='empty'>No members absent {weeks}+ weeks.</p>"
        return subject, _wrap(title, _today(), body)

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{len(rows)}</div>"
        f"<div class='stat-label'>Members</div></div>"
        f"</div>"
    )
    table_rows = "".join(
        f"<tr><td>{r['name'] or '(no name)'}</td>"
        f"<td>{r['campus_preference'] or '—'}</td>"
        f"<td>{r['last_seen'] or '—'}</td>"
        f"<td>{r['weeks_absent']} wks</td></tr>"
        for r in rows
    )
    body = (
        stats
        + "<table><thead><tr><th>Name</th><th>Campus</th><th>Last Seen</th><th>Absent</th></tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return subject, _wrap(title, _today(), body)


# ── 3. First-Time Visitors ────────────────────────────────────────────────────

def first_time_visitors_report(weeks: int = 4) -> tuple[str, str]:
    cutoff = _cutoff(weeks * 7)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.name, m.email, m.phone, cc.campus, cc.service_date AS visit_date
            FROM follow_ups fu
            JOIN members m ON m.id = fu.member_id
            JOIN connect_cards cc ON cc.id = fu.card_id
            WHERE fu.note = 'First-time visitor'
              AND cc.service_date >= ?
            ORDER BY cc.service_date DESC
            """,
            (cutoff,),
        ).fetchall()

    title = f"First-Time Visitors — Last {weeks} Weeks"
    subject = _subject(title, _today(), False)

    if not rows:
        body = f"<p class='empty'>No first-time visitors in the last {weeks} weeks.</p>"
        return subject, _wrap(title, _today(), body)

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{len(rows)}</div>"
        f"<div class='stat-label'>First-Time Visitors</div></div>"
        f"</div>"
    )
    table_rows = "".join(
        f"<tr><td>{r['name'] or '(no name)'}</td>"
        f"<td><small>{r['email'] or r['phone'] or '—'}</small></td>"
        f"<td>{r['campus'] or '—'}</td>"
        f"<td>{r['visit_date']}</td></tr>"
        for r in rows
    )
    body = (
        stats
        + "<table><thead><tr><th>Name</th><th>Contact</th><th>Campus</th><th>Visit Date</th></tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return subject, _wrap(title, _today(), body)


# ── 4. Lapsed Visitors ────────────────────────────────────────────────────────

def lapsed_visitors_report(min_weeks: int = 3, max_weeks: int = 8) -> tuple[str, str]:
    min_cutoff = _cutoff(min_weeks * 7)
    max_cutoff = _cutoff(max_weeks * 7)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.name, m.email, m.phone,
                   MAX(cc.service_date) AS last_visit,
                   CAST((julianday('now') - julianday(MAX(cc.service_date))) / 7 AS INTEGER)
                       AS weeks_ago
            FROM members m
            JOIN connect_cards cc ON cc.member_id = m.id
            WHERE m.id IN (
                SELECT DISTINCT member_id FROM follow_ups WHERE note = 'First-time visitor'
            )
            GROUP BY m.id
            HAVING COUNT(DISTINCT cc.id) = 1
               AND MAX(cc.service_date) < ?
               AND MAX(cc.service_date) >= ?
            ORDER BY weeks_ago ASC
            """,
            (min_cutoff, max_cutoff),
        ).fetchall()

    title = f"Visitors Not Seen Since ({min_weeks}–{max_weeks} weeks ago)"
    subject = _subject("First-Time Visitors Not Seen Since", _today(), False)

    if not rows:
        body = f"<p class='empty'>No lapsed first-time visitors in the {min_weeks}–{max_weeks} week window.</p>"
        return subject, _wrap(title, _today(), body)

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{len(rows)}</div>"
        f"<div class='stat-label'>Need Follow-Up</div></div>"
        f"</div>"
    )
    table_rows = "".join(
        f"<tr><td>{r['name'] or '(no name)'}</td>"
        f"<td><small>{r['email'] or r['phone'] or '—'}</small></td>"
        f"<td>{r['last_visit']}</td>"
        f"<td>{r['weeks_ago']} wks</td></tr>"
        for r in rows
    )
    body = (
        stats
        + "<table><thead><tr><th>Name</th><th>Contact</th><th>Visited</th><th>Weeks Ago</th></tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return subject, _wrap(title, _today(), body)


# ── 5. Next Steps Follow-Up Action List ──────────────────────────────────────

def next_steps_followup_report() -> tuple[str, str]:
    cutoff = _cutoff(90)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT ns.step, m.name, m.email, m.phone, cc.campus, ns.date
            FROM next_steps ns
            JOIN members m ON m.id = ns.member_id
            JOIN connect_cards cc ON cc.id = ns.card_id
            WHERE ns.date >= ?
            ORDER BY ns.step, m.name
            """,
            (cutoff,),
        ).fetchall()

    title = "Next Steps — Needs Follow-Up"
    subject = _subject(title, _today(), False)

    if not rows:
        body = "<p class='empty'>No next steps in the last 90 days.</p>"
        return subject, _wrap(title, _today(), body)

    by_step: dict = defaultdict(list)
    for r in rows:
        by_step[r["step"]].append(r)

    body = f"<p style='color:#888;font-size:.9em'>{len(rows)} open next steps (last 90 days)</p>"
    for step_key, step_name in _STEP_NAMES.items():
        step_rows = by_step.get(step_key, [])
        if not step_rows:
            continue
        body += f"<h2>{step_name} ({len(step_rows)})</h2>"
        body += "<table><thead><tr><th>Name</th><th>Contact</th><th>Campus</th><th>Date</th></tr></thead><tbody>"
        for r in step_rows:
            contact = r["email"] or r["phone"] or "—"
            body += (
                f"<tr><td>{r['name'] or '(no name)'}</td>"
                f"<td><small>{contact}</small></td>"
                f"<td>{r['campus'] or '—'}</td>"
                f"<td>{r['date']}</td></tr>"
            )
        body += "</tbody></table>"

    return subject, _wrap(title, _today(), body)


# ── 6. New Faces ──────────────────────────────────────────────────────────────

def new_faces_report(weeks: int = 4) -> tuple[str, str]:
    cutoff = _cutoff(weeks * 7)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT name, email, phone, campus_preference, first_visit_date
            FROM members
            WHERE first_visit_date >= ?
            ORDER BY first_visit_date DESC
            """,
            (cutoff,),
        ).fetchall()

    title = f"New Faces — Last {weeks} Weeks"
    subject = _subject(title, _today(), False)

    if not rows:
        body = f"<p class='empty'>No new members in the last {weeks} weeks.</p>"
        return subject, _wrap(title, _today(), body)

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{len(rows)}</div>"
        f"<div class='stat-label'>New Faces</div></div>"
        f"</div>"
    )
    table_rows = "".join(
        f"<tr><td>{r['name'] or '(no name)'}</td>"
        f"<td><small>{r['email'] or r['phone'] or '—'}</small></td>"
        f"<td>{r['campus_preference'] or '—'}</td>"
        f"<td>{r['first_visit_date'] or '—'}</td></tr>"
        for r in rows
    )
    body = (
        stats
        + "<table><thead><tr><th>Name</th><th>Contact</th><th>Campus</th><th>First Visit</th></tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return subject, _wrap(title, _today(), body)


# ── 7. Attendance Trends ──────────────────────────────────────────────────────

def attendance_trends_report(weeks: int = 8) -> tuple[str, str]:
    cutoff = _cutoff(weeks * 7)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT service_date,
                   COUNT(*) AS total,
                   SUM(CASE WHEN campus = 'Wilmington' THEN 1 ELSE 0 END) AS wilmington,
                   SUM(CASE WHEN campus = 'Online' THEN 1 ELSE 0 END) AS online
            FROM connect_cards
            WHERE service_date >= ?
            GROUP BY service_date
            ORDER BY service_date DESC
            """,
            (cutoff,),
        ).fetchall()

    title = f"Attendance Trends — Last {weeks} Weeks"
    subject = _subject(title, _today(), False)

    if not rows:
        body = f"<p class='empty'>No attendance data in the last {weeks} weeks.</p>"
        return subject, _wrap(title, _today(), body)

    totals = [r["total"] for r in rows]
    avg = sum(totals) // len(totals)

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{avg}</div><div class='stat-label'>Avg / Week</div></div>"
        f"<div class='stat-box'><div class='stat'>{max(totals)}</div><div class='stat-label'>Highest Week</div></div>"
        f"<div class='stat-box'><div class='stat'>{min(totals)}</div><div class='stat-label'>Lowest Week</div></div>"
        f"</div>"
    )
    table_rows = "".join(
        f"<tr><td>{r['service_date']}</td><td>{r['total']}</td>"
        f"<td>{r['wilmington']}</td><td>{r['online']}</td></tr>"
        for r in rows
    )
    body = (
        stats
        + "<table><thead><tr><th>Date</th><th>Total</th><th>Wilmington</th><th>Online</th></tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return subject, _wrap(title, _today(), body)


# ── 8. Congregation Overview ──────────────────────────────────────────────────

def congregation_overview_report() -> tuple[str, str]:
    with _conn() as conn:
        total_members = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        active_members = conn.execute(
            "SELECT COUNT(DISTINCT member_id) FROM connect_cards WHERE service_date >= ?",
            (_cutoff(60),),
        ).fetchone()[0]
        total_cards = conn.execute("SELECT COUNT(*) FROM connect_cards").fetchone()[0]
        total_prayer = conn.execute("SELECT COUNT(*) FROM prayer_requests").fetchone()[0]
        total_next_steps_count = conn.execute("SELECT COUNT(*) FROM next_steps").fetchone()[0]

        top_step_row = conn.execute(
            "SELECT step, COUNT(*) AS cnt FROM next_steps GROUP BY step ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        top_step = (
            _STEP_NAMES.get(top_step_row["step"], top_step_row["step"])
            if top_step_row else "—"
        )

        wilm_regulars = conn.execute(
            """
            SELECT m.name, COUNT(cc.id) AS visits
            FROM members m
            JOIN connect_cards cc ON cc.member_id = m.id
            WHERE cc.campus = 'Wilmington' AND cc.service_date >= ?
            GROUP BY m.id
            HAVING visits >= 4
            ORDER BY visits DESC
            """,
            (_cutoff(56),),
        ).fetchall()

        online_regulars = conn.execute(
            """
            SELECT m.name, COUNT(cc.id) AS visits
            FROM members m
            JOIN connect_cards cc ON cc.member_id = m.id
            WHERE cc.campus = 'Online' AND cc.service_date >= ?
            GROUP BY m.id
            HAVING visits >= 4
            ORDER BY visits DESC
            """,
            (_cutoff(56),),
        ).fetchall()

    title = "Congregation Overview"
    subject = _subject(title, _today(), False)

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{total_members}</div><div class='stat-label'>Total Members</div></div>"
        f"<div class='stat-box'><div class='stat'>{active_members}</div><div class='stat-label'>Active (60 days)</div></div>"
        f"<div class='stat-box'><div class='stat'>{total_cards}</div><div class='stat-label'>Cards (all time)</div></div>"
        f"<div class='stat-box'><div class='stat'>{total_prayer}</div><div class='stat-label'>Prayer Requests</div></div>"
        f"<div class='stat-box'><div class='stat'>{total_next_steps_count}</div><div class='stat-label'>Next Steps</div></div>"
        f"</div>"
        f"<p style='color:#888;font-size:.9em'>Most common next step: <strong>{top_step}</strong></p>"
    )

    def _regulars_table(regulars):
        if not regulars:
            return "<tr><td colspan='2' class='empty'>No regulars found.</td></tr>"
        return "".join(
            f"<tr><td>{r['name']}</td><td>{r['visits']}</td></tr>"
            for r in regulars
        )

    body = (
        stats
        + "<h2>Wilmington Regulars (4+ visits / 8 weeks)</h2>"
        + "<table><thead><tr><th>Name</th><th>Visits</th></tr></thead>"
        + f"<tbody>{_regulars_table(wilm_regulars)}</tbody></table>"
        + "<h2>Online Regulars (4+ visits / 8 weeks)</h2>"
        + "<table><thead><tr><th>Name</th><th>Visits</th></tr></thead>"
        + f"<tbody>{_regulars_table(online_regulars)}</tbody></table>"
    )
    return subject, _wrap(title, _today(), body)
