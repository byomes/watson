"""jobs/events/duplicate_review.py — same duplicate-candidate scan
jobs/congregation/duplicate_review.py runs over the members table
(shared email / shared phone / matching-or-similar name, difflib ratio),
applied to event_registrations instead. Built 2026-09-28 after Donna
flagged repeat picnic signups (event_registrations had no duplicate check
at all up to that point — CSV import only dedupes exact-email within the
same import batch, see jobs/events/import_csv.py).

Candidate pairs are only ever compared within a single event_id — the
same person legitimately registering for two different events is not a
duplicate. Backs the wtsn.me/cat/event-duplicates review tool.

scan_for_duplicates() is called both as a one-off (this file's __main__,
used for the retroactive pass over every event already on file) and after
every new registration is inserted (jobs/events/signup_detect.py,
jobs/events/import_csv.py, jobs/events/banquet_rsvp.py all call it with
that event's event_id right after their insert commits) so new signups
get checked against the existing roster going forward, the same way a
connect card checks against the members table at intake instead of only
during a batch review pass.

Mount on the Watson dashboard app:
    from jobs.events.duplicate_review import event_duplicate_review_bp
    app.register_blueprint(event_duplicate_review_bp)
"""
import difflib
import os
import re
import sqlite3
from functools import wraps

from flask import Blueprint, jsonify, request

from config.settings import DB_PATH

event_duplicate_review_bp = Blueprint("event_duplicate_review", __name__)

# Reuses the connect-card/member duplicate tool's admin key rather than
# minting a second secret for what is the same trust boundary (staff-only
# review tool on the same dashboard) -- see jobs/congregation/duplicate_review.py.
_API_KEY = lambda: os.getenv("DUPLICATES_API_KEY", "")

_FUZZY_NAME_THRESHOLD = 0.86  # same bar as the member scanner


def _blank(v) -> bool:
    return not (v or "").strip() or (v or "").strip() == "--"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_email(e):
    return (e or "").strip().lower()


def _norm_phone(p):
    return re.sub(r"\D", "", p or "")


def _norm_name(n):
    return re.sub(r"\s+", " ", (n or "").strip().lower()).rstrip("\\").strip()


def _pair_exists(conn, id_a, id_b) -> bool:
    return conn.execute(
        """SELECT 1 FROM event_duplicate_flags
           WHERE status = 'pending'
           AND ((registration_id_a = ? AND registration_id_b = ?) OR (registration_id_a = ? AND registration_id_b = ?))""",
        (id_a, id_b, id_b, id_a),
    ).fetchone() is not None


def _scan_event(conn, event_id: int, regs: list[dict]) -> int:
    groups: dict[tuple[str, str], list[int]] = {}

    def add(key_kind, key_value, reg_id):
        if not key_value:
            return
        groups.setdefault((key_kind, key_value), []).append(reg_id)

    for r in regs:
        add("email", _norm_email(r["email"]), r["id"])
        phone = _norm_phone(r["phone"])
        if len(phone) >= 10:
            add("phone", phone, r["id"])
        full_name = f"{r['first_name'] or ''} {r['last_name'] or ''}"
        add("name", _norm_name(full_name), r["id"])

    candidate_pairs: set[tuple[int, int]] = set()
    reasons: dict[tuple[int, int], str] = {}

    for (kind, _value), ids in groups.items():
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair = tuple(sorted((ids[i], ids[j])))
                candidate_pairs.add(pair)
                reasons.setdefault(pair, kind if kind != "name" else "name_exact")

    names = [(r["id"], _norm_name(f"{r['first_name'] or ''} {r['last_name'] or ''}")) for r in regs]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            id1, n1 = names[i]
            id2, n2 = names[j]
            if not n1 or not n2 or n1 == n2:
                continue
            pair = tuple(sorted((id1, id2)))
            if pair in candidate_pairs:
                continue
            if difflib.SequenceMatcher(None, n1, n2).ratio() >= _FUZZY_NAME_THRESHOLD:
                candidate_pairs.add(pair)
                reasons[pair] = "name_fuzzy"

    inserted = 0
    for pair in candidate_pairs:
        id_a, id_b = pair
        if _pair_exists(conn, id_a, id_b):
            continue
        already_resolved = conn.execute(
            """SELECT 1 FROM event_duplicate_flags
               WHERE status != 'pending'
               AND ((registration_id_a = ? AND registration_id_b = ?) OR (registration_id_a = ? AND registration_id_b = ?))""",
            (id_a, id_b, id_b, id_a),
        ).fetchone()
        if already_resolved:
            continue
        conn.execute(
            "INSERT INTO event_duplicate_flags (event_id, registration_id_a, registration_id_b, reason, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (event_id, id_a, id_b, reasons[pair]),
        )
        inserted += 1
    return inserted


def scan_for_duplicates(event_id: int | None = None) -> int:
    """Find candidate duplicate registration pairs and insert new
    event_duplicate_flags rows. event_id=None scans every event (the
    retroactive pass); pass a single event_id to re-scan just that event
    (what the intake call sites do right after inserting a new
    registration). Returns count inserted."""
    with _conn() as conn:
        query = "SELECT id, event_id, first_name, last_name, email, phone FROM event_registrations"
        params: tuple = ()
        if event_id is not None:
            query += " WHERE event_id = ?"
            params = (event_id,)
        regs = [dict(r) for r in conn.execute(query, params)]

        by_event: dict[int, list[dict]] = {}
        for r in regs:
            by_event.setdefault(r["event_id"], []).append(r)

        inserted = 0
        for eid, ev_regs in by_event.items():
            inserted += _scan_event(conn, eid, ev_regs)
        conn.commit()
        return inserted


def merge_registrations(
    conn: sqlite3.Connection,
    keep_id: int,
    merge_id: int,
    final_num_tickets: int | None = None,
) -> dict:
    """Fills blank fields on keep_id from merge_id, optionally overrides
    num_tickets with the reviewer's reconciled headcount (never auto-summed
    -- two registrations of the same person is exactly as likely to mean
    "they registered a second time for the same 2 tickets" as "2 + 3 people",
    so the reviewer states the real number rather than code guessing it,
    same reasoning as duplicate_review.py's final_name field for members),
    then deletes the merge_id row."""
    if keep_id == merge_id:
        raise ValueError("keep_id and merge_id must differ")

    keep = conn.execute("SELECT * FROM event_registrations WHERE id = ?", (keep_id,)).fetchone()
    merge = conn.execute("SELECT * FROM event_registrations WHERE id = ?", (merge_id,)).fetchone()
    if not keep or not merge:
        raise ValueError("both registrations must exist")
    if keep["event_id"] != merge["event_id"]:
        raise ValueError("registrations must belong to the same event")

    conn.execute(
        "UPDATE event_duplicate_flags SET registration_id_a = ? WHERE registration_id_a = ?",
        (keep_id, merge_id),
    )
    conn.execute(
        "UPDATE event_duplicate_flags SET registration_id_b = ? WHERE registration_id_b = ?",
        (keep_id, merge_id),
    )

    fills = {}
    for field in ("first_name", "last_name", "email", "phone", "ticket_type", "ticket_price", "member_id"):
        if _blank(str(keep[field]) if keep[field] is not None else "") and not _blank(str(merge[field]) if merge[field] is not None else ""):
            fills[field] = merge[field]

    if final_num_tickets is not None:
        fills["num_tickets"] = final_num_tickets

    if fills:
        set_clause = ", ".join(f"{k} = ?" for k in fills)
        conn.execute(
            f"UPDATE event_registrations SET {set_clause} WHERE id = ?",
            (*fills.values(), keep_id),
        )

    conn.execute("DELETE FROM event_registrations WHERE id = ?", (merge_id,))
    conn.commit()

    result = dict(conn.execute("SELECT * FROM event_registrations WHERE id = ?", (keep_id,)).fetchone())
    return result


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _registration_summary(conn, reg_id: int) -> dict:
    r = conn.execute("SELECT * FROM event_registrations WHERE id = ?", (reg_id,)).fetchone()
    if not r:
        return {"id": reg_id, "deleted": True}
    return {
        "id": r["id"],
        "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or None,
        "email": r["email"],
        "phone": r["phone"],
        "ticket_type": r["ticket_type"],
        "num_tickets": r["num_tickets"],
        "source": r["source"],
        "submitted_at": r["submitted_at"],
    }


@event_duplicate_review_bp.route("/api/cat/events/duplicates/list", methods=["GET"])
@_require_key
def list_duplicates():
    with _conn() as conn:
        flags = conn.execute(
            """SELECT f.id, f.event_id, f.registration_id_a, f.registration_id_b, f.reason, f.created_at,
                      e.event_name
               FROM event_duplicate_flags f
               JOIN church_events e ON e.id = f.event_id
               WHERE f.status = 'pending' AND f.registration_id_a != f.registration_id_b
               ORDER BY f.created_at DESC"""
        ).fetchall()
        pairs = []
        for f in flags:
            a = _registration_summary(conn, f["registration_id_a"])
            b = _registration_summary(conn, f["registration_id_b"])
            if a.get("deleted") or b.get("deleted"):
                conn.execute("UPDATE event_duplicate_flags SET status = 'auto_resolved' WHERE id = ?", (f["id"],))
                continue
            pairs.append({
                "flag_id": f["id"],
                "event_id": f["event_id"],
                "event_name": f["event_name"],
                "reason": f["reason"],
                "created_at": f["created_at"],
                "registration_a": a,
                "registration_b": b,
            })
        conn.commit()
    return jsonify({"pairs": pairs}), 200


@event_duplicate_review_bp.route("/api/cat/events/duplicates/rescan", methods=["POST"])
@_require_key
def rescan():
    inserted = scan_for_duplicates()
    return jsonify({"new_candidates": inserted}), 200


@event_duplicate_review_bp.route("/api/cat/events/duplicates/merge", methods=["POST"])
@_require_key
def merge_route():
    data = request.get_json(force=True) or {}
    flag_id = data.get("flag_id")
    keep_id = data.get("keep_id")
    merge_id = data.get("merge_id")
    final_num_tickets = data.get("num_tickets")

    if not all(isinstance(v, int) for v in (flag_id, keep_id, merge_id)):
        return jsonify({"error": "flag_id, keep_id, merge_id (ints) are required"}), 400
    if final_num_tickets is not None and not isinstance(final_num_tickets, int):
        return jsonify({"error": "num_tickets must be an int if provided"}), 400

    with _conn() as conn:
        try:
            result = merge_registrations(conn, keep_id, merge_id, final_num_tickets)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        conn.execute("UPDATE event_duplicate_flags SET status = 'merged' WHERE id = ?", (flag_id,))
        conn.commit()

    return jsonify({"kept": result}), 200


@event_duplicate_review_bp.route("/api/cat/events/duplicates/dismiss", methods=["POST"])
@_require_key
def dismiss_route():
    data = request.get_json(force=True) or {}
    flag_id = data.get("flag_id")
    if not isinstance(flag_id, int):
        return jsonify({"error": "flag_id (int) is required"}), 400

    with _conn() as conn:
        conn.execute("UPDATE event_duplicate_flags SET status = 'dismissed' WHERE id = ?", (flag_id,))
        conn.commit()

    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    from jobs.events.schema import create_tables
    create_tables()
    n = scan_for_duplicates()
    print(f"{n} new candidate duplicate pair(s) flagged across all events.")
