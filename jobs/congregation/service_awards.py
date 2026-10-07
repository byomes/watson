"""jobs/congregation/service_awards.py -- length-of-service award tracking for
the annual servant banquet (Bill, 2026-10-07). One row per person per
milestone in congregation.db `service_awards`; `received=1` once the pin has
been handed over (`needed=1` = flagged to receive at the banquet). Milestones: 6
months, 2 years, then every 5 years from 5 to 70. Legacy pins outside that list (e.g. a 55yr) are kept as received
history. Marking a pin received also appends it to members.service_pin_notes
so every existing profile surface shows it. See
[[project_banquet_service_length_report]]."""
import re
import sqlite3
from datetime import date

from jobs.congregation.serving_edit import _resolve_one_member
from jobs.people.lookup import CONG_DB

MILESTONES = [6, 24] + [y * 12 for y in range(5, 75, 5)]  # months (5yr steps to 70yr, per Bill/Donna's 2026-10-07 sheet)


def label(months: int) -> str:
    return f"{months}mo" if months < 12 else f"{months // 12}yr"


def _conn():
    conn = sqlite3.connect(CONG_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS service_awards (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id        INTEGER NOT NULL REFERENCES members(id),
            milestone_months INTEGER NOT NULL,
            label            TEXT NOT NULL,
            received         INTEGER NOT NULL DEFAULT 0,
            received_date    TEXT,
            source           TEXT,
            notes            TEXT,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(member_id, milestone_months)
        )"""
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(service_awards)")}
    if "needed" not in cols:
        conn.execute("ALTER TABLE service_awards ADD COLUMN needed INTEGER NOT NULL DEFAULT 0")


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


_AWARD = re.compile(r"(\d+)\s*(mo|month|yr|year)", re.I)
_WHEN = re.compile(r"(\d{1,2})/(\d{4}|\d{2})\b")


def parse_pin_notes(text: str) -> tuple[list[tuple[int, str | None]], list[str]]:
    """Returns ([(milestone_months, 'YYYY-MM' or None)], unparsed segments)."""
    found, leftover = [], []
    for seg in (text or "").split("|"):
        seg = seg.strip()
        if not seg:
            continue
        a = _AWARD.search(seg)
        if not a:
            leftover.append(seg)
            continue
        n = int(a.group(1))
        months = n if a.group(2).lower().startswith("mo") else n * 12
        w = _WHEN.search(seg)
        when = None
        if w:
            yr = int(w.group(2))
            yr += 2000 if yr < 100 else 0
            when = f"{yr:04d}-{int(w.group(1)):02d}"
        found.append((months, when))
    return found, leftover


def backfill_from_notes(conn) -> dict:
    ensure_table(conn)
    stats = {"rows": 0, "undated": 0, "unparsed": []}
    for r in conn.execute("SELECT id, name, service_pin_notes FROM members WHERE coalesce(service_pin_notes,'')<>''").fetchall():
        found, leftover = parse_pin_notes(r["service_pin_notes"])
        for months, when in found:
            cur = conn.execute(
                "INSERT OR IGNORE INTO service_awards (member_id, milestone_months, label, received, received_date, source, notes)"
                " VALUES (?,?,?,1,?, 'backfill', ?)",
                (r["id"], months, label(months), when, r["service_pin_notes"]),
            )
            stats["rows"] += cur.rowcount
            stats["undated"] += 1 if (cur.rowcount and not when) else 0
        if leftover:
            stats["unparsed"].append((r["name"], leftover))
    return stats


def status(as_of: date | None = None) -> list[dict]:
    """Per active servant: highest official milestone reached by as_of, whether
    it's received, and lower official milestones reached with no record."""
    as_of = as_of or date.today()
    out = []
    with _conn() as conn:
        ensure_table(conn)
        people = conn.execute(
            """SELECT m.id, m.name, m.started_serving_date sd, group_concat(tm.team_name, ', ') teams
               FROM members m JOIN team_memberships tm ON tm.member_id = m.id
               WHERE tm.active = 1 AND coalesce(m.started_serving_date,'') <> '' GROUP BY m.id ORDER BY m.name"""
        ).fetchall()
        got = {}
        for a in conn.execute("SELECT member_id, milestone_months FROM service_awards WHERE received = 1"):
            got.setdefault(a["member_id"], set()).add(a["milestone_months"])
        for p in people:
            sd = date.fromisoformat(p["sd"][:10])
            reached = [m for m in MILESTONES if _add_months(sd, m) <= as_of]
            have = got.get(p["id"], set())
            top = reached[-1] if reached else None
            due = top if top and top not in have else None
            if due and any(h > due for h in have):  # already got a bigger pin (e.g. legacy 55yr)
                due = None
            out.append({
                "member_id": p["id"], "name": p["name"], "started": p["sd"], "teams": p["teams"],
                "due": due, "due_label": label(due) if due else None,
                "due_date": _add_months(sd, due).isoformat() if due else None,
                "earlier_no_record": [label(m) for m in reached if m != top and m not in have],
                "received": sorted(label(m) for m in have),
                "next": next(((label(m), _add_months(sd, m).isoformat()) for m in MILESTONES if _add_months(sd, m) > as_of), None),
            })
    return out


def mark_received(name_query: str, milestone_text: str, received_on: str | None, sender_name: str) -> str:
    """e.g. mark_received("Tyler McCauley", "5yr", None, "Donna Redman")."""
    a = _AWARD.search(milestone_text or "")
    if not a:
        return 'I need the award, e.g. "5yr" or "6mo".'
    n = int(a.group(1))
    months = n if a.group(2).lower().startswith("mo") else n * 12
    if months not in MILESTONES:
        return f"{label(months)} isn't one of the award milestones ({', '.join(label(m) for m in MILESTONES)})."
    when = received_on or date.today().strftime("%Y-%m")
    with _conn() as conn:
        ensure_table(conn)
        member = _resolve_one_member(conn, name_query, "id, name, service_pin_notes")
        if isinstance(member, str):
            return member
        conn.execute(
            "INSERT INTO service_awards (member_id, milestone_months, label, received, received_date, source)"
            " VALUES (?,?,?,1,?, ?) ON CONFLICT(member_id, milestone_months) DO UPDATE SET received=1, needed=0, received_date=excluded.received_date, source=excluded.source",
            (member["id"], months, label(months), when, f"manual:{sender_name}"),
        )
        sync_pin_notes(conn, member["id"])
    return f"Done: {member['name']} marked as having received the {label(months)} pin ({when}). Logged by {sender_name}."


def ingest_sheet_csv(path: str) -> dict:
    """Load Bill/Donna's edited tracking sheet: columns member_id, Name, ...,
    then one column per award ('6mo','2yr',...). AR = already received,
    NTR = needs to receive. Blank cells are left untouched (never deletes).
    Existing received dates are kept when a cell is AR."""
    import csv
    rows = list(csv.reader(open(path, encoding="utf-8")))
    head = rows[0]
    awards = [(i, h) for i, h in enumerate(head) if _AWARD.fullmatch(h.strip())]
    stats = {"ar_new": 0, "ar_kept": 0, "ntr": 0, "skipped_unknown_member": 0}
    with _conn() as conn:
        ensure_table(conn)
        for r in rows[1:]:
            if not r or not r[0].strip().isdigit():
                continue
            mid = int(r[0])
            if not conn.execute("SELECT 1 FROM members WHERE id=?", (mid,)).fetchone():
                stats["skipped_unknown_member"] += 1
                continue
            for i, h in awards:
                v = (r[i] if i < len(r) else "").strip().upper()
                if v not in ("AR", "NTR"):
                    continue
                a = _AWARD.search(h)
                n = int(a.group(1))
                months = n if a.group(2).lower().startswith("mo") else n * 12
                ex = conn.execute("SELECT received FROM service_awards WHERE member_id=? AND milestone_months=?", (mid, months)).fetchone()
                if v == "AR":
                    if ex and ex["received"]:
                        conn.execute("UPDATE service_awards SET needed=0 WHERE member_id=? AND milestone_months=?", (mid, months))
                        stats["ar_kept"] += 1
                    else:
                        conn.execute(
                            "INSERT INTO service_awards (member_id, milestone_months, label, received, needed, source)"
                            " VALUES (?,?,?,1,0,'sheet 2026-10-07') ON CONFLICT(member_id, milestone_months)"
                            " DO UPDATE SET received=1, needed=0, source='sheet 2026-10-07'",
                            (mid, months, label(months)))
                        stats["ar_new"] += 1
                else:
                    conn.execute(
                        "INSERT INTO service_awards (member_id, milestone_months, label, received, needed, source)"
                        " VALUES (?,?,?,0,1,'sheet 2026-10-07') ON CONFLICT(member_id, milestone_months)"
                        " DO UPDATE SET needed=1, received=0, source='sheet 2026-10-07'",
                        (mid, months, label(months)))
                    stats["ntr"] += 1
    return stats


def _fmt_when(when: str | None) -> str:
    return f" in {when[5:7]}/{when[:4]}" if when and re.fullmatch(r"\d{4}-\d{2}", when) else ""


def sync_pin_notes(conn, member_id: int) -> str:
    """Regenerate members.service_pin_notes from service_awards so every
    existing profile/grid/chat surface shows award status without
    per-surface wiring. Received awards first (oldest milestone first),
    then 'NEEDS: ...' for awards still to be handed out."""
    rows = conn.execute(
        "SELECT milestone_months, label, received, received_date, needed FROM service_awards"
        " WHERE member_id=? ORDER BY milestone_months", (member_id,)).fetchall()
    parts = [f"{r['label']} Pin{_fmt_when(r['received_date'])}" for r in rows if r["received"]]
    need = [f"{r['label']} Pin" for r in rows if r["needed"] and not r["received"]]
    if need:
        parts.append("NEEDS: " + ", ".join(need))
    text = " | ".join(parts)
    conn.execute("UPDATE members SET service_pin_notes = ? WHERE id = ?", (text, member_id))
    return text


def sync_all() -> int:
    with _conn() as conn:
        ensure_table(conn)
        ids = [r[0] for r in conn.execute("SELECT DISTINCT member_id FROM service_awards")]
        for i in ids:
            sync_pin_notes(conn, i)
    return len(ids)


def awards_for_member(member_id: int) -> list[dict]:
    """Every milestone with its status for one member: received / needed / none."""
    with _conn() as conn:
        ensure_table(conn)
        have = {r["milestone_months"]: r for r in conn.execute("SELECT * FROM service_awards WHERE member_id=?", (member_id,))}
        sd = conn.execute("SELECT started_serving_date FROM members WHERE id=?", (member_id,)).fetchone()
    start = date.fromisoformat(sd[0][:10]) if sd and sd[0] else None
    out = []
    for m in sorted(set(MILESTONES) | set(have)):
        r = have.get(m)
        out.append({
            "label": label(m), "milestone_months": m,
            "status": ("received" if r and r["received"] else "needed" if r and r["needed"] else "none"),
            "received_date": r["received_date"] if r else None,
            "eligible_on": _add_months(start, m).isoformat() if start else None,
        })
    return out


def set_status(member_id: int, label_text: str, status: str, received_date: str | None = None) -> dict:
    """status: 'received' | 'needed' | 'none' (clears the row)."""
    a = _AWARD.search(label_text or "")
    if not a or status not in ("received", "needed", "none"):
        raise ValueError("bad label or status")
    n = int(a.group(1))
    months = n if a.group(2).lower().startswith("mo") else n * 12
    if months not in MILESTONES and status != "none":
        raise ValueError(f"{label(months)} is not an award milestone")
    with _conn() as conn:
        ensure_table(conn)
        if status == "none":
            conn.execute("DELETE FROM service_awards WHERE member_id=? AND milestone_months=?", (member_id, months))
        else:
            rec = 1 if status == "received" else 0
            when = received_date or (date.today().strftime("%Y-%m") if rec else None)
            conn.execute(
                "INSERT INTO service_awards (member_id, milestone_months, label, received, needed, received_date, source)"
                " VALUES (?,?,?,?,?,?, 'profile') ON CONFLICT(member_id, milestone_months) DO UPDATE SET"
                " received=excluded.received, needed=excluded.needed,"
                " received_date=CASE WHEN excluded.received=1 THEN coalesce(service_awards.received_date, excluded.received_date) ELSE NULL END,"
                " source='profile'",
                (member_id, months, label(months), rec, 0 if rec else 1, when))
        sync_pin_notes(conn, member_id)
    return {"awards": awards_for_member(member_id)}
