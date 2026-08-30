"""jobs/congregation/duplicate_review.py — batch duplicate-member scanner +
merge, backing the wtsn.me/cat/duplicates staff review tool.

member_match.py already flags *individual* records for review the moment a
fuzzy name match fires during live intake (member_id_a == member_id_b in
duplicate_flags -- a "double check this auto-match" signal, not a pair).
This module is the batch counterpart: scan_for_duplicates() walks the whole
members table looking for actual candidate PAIRS (shared email, shared
phone, matching name) and writes real member_id_a != member_id_b rows so a
human can pick which one to keep. Reuses member_match's FUZZY_THRESHOLD/
difflib approach for the name-similarity leg rather than inventing a second
threshold.

Deliberately over-inclusive: two members sharing an email/phone are just as
often a married couple or parent+child as they are one person entered
twice (see the Cox/Myers/Valentine households found in the first manual
pass, 2026-08-30) -- this module surfaces every candidate and lets the
reviewer dismiss the ones that are actually two different people, rather
than guessing that judgment call in code.

Mount on the Watson dashboard app:
    from jobs.congregation.duplicate_review import duplicate_review_bp
    app.register_blueprint(duplicate_review_bp)
"""
import difflib
import os
import re
import sqlite3
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.congregation.member_match import FUZZY_THRESHOLD

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

duplicate_review_bp = Blueprint("duplicate_review", __name__)

_API_KEY = lambda: os.getenv("DUPLICATES_API_KEY", "")

_FUZZY_NAME_THRESHOLD = 0.86  # stricter than FUZZY_THRESHOLD (0.82) -- this
# runs over the WHOLE table (n^2), not one new record against the rest, so
# a slightly higher bar keeps the review queue from filling with noise.

_LINKED_TABLES = ("attendance", "connect_cards", "follow_ups", "prayer_requests", "next_steps")

# Filled on the kept record only if it's currently null/blank.
_FILL_IF_BLANK_FIELDS = (
    "email", "phone", "campus_preference", "notes", "carrier", "address",
    "household_id", "deacon", "deacon_status", "status_reason", "status_note",
    "snowbird_return",
)


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
        """SELECT 1 FROM duplicate_flags
           WHERE status = 'pending'
           AND ((member_id_a = ? AND member_id_b = ?) OR (member_id_a = ? AND member_id_b = ?))""",
        (id_a, id_b, id_b, id_a),
    ).fetchone() is not None


def scan_for_duplicates() -> int:
    """Find candidate duplicate pairs and insert new duplicate_flags rows
    (status='pending', member_id_a != member_id_b). Returns count inserted.
    Skips a pair already pending from a prior scan."""
    with _conn() as conn:
        members = [dict(r) for r in conn.execute("SELECT id, name, email, phone FROM members")]

        groups: dict[tuple[str, str], list[int]] = {}

        def add(key_kind, key_value, member_id):
            if not key_value:
                return
            groups.setdefault((key_kind, key_value), []).append(member_id)

        for m in members:
            add("email", _norm_email(m["email"]), m["id"])
            phone = _norm_phone(m["phone"])
            if len(phone) >= 10:
                add("phone", phone, m["id"])
            add("name", _norm_name(m["name"]), m["id"])

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

        # Fuzzy name pass, catching near-misses exact matching won't (typos,
        # nicknames) -- only added if not already caught above.
        names = [(m["id"], _norm_name(m["name"])) for m in members]
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
                """SELECT 1 FROM duplicate_flags
                   WHERE status != 'pending'
                   AND ((member_id_a = ? AND member_id_b = ?) OR (member_id_a = ? AND member_id_b = ?))""",
                (id_a, id_b, id_b, id_a),
            ).fetchone()
            if already_resolved:
                continue
            conn.execute(
                "INSERT INTO duplicate_flags (member_id_a, member_id_b, reason, status) VALUES (?, ?, ?, 'pending')",
                (id_a, id_b, reasons[pair]),
            )
            inserted += 1
        conn.commit()
        return inserted


def merge_members(conn: sqlite3.Connection, keep_id: int, merge_id: int, final_name: str | None = None) -> dict:
    """Reassigns merge_id's history onto keep_id, fills blank contact fields
    on keep_id from merge_id, then deletes the merge_id member row. Does not
    touch status/member_status/partnership_status -- those are a judgment
    call the reviewer makes separately, not inferred here."""
    if keep_id == merge_id:
        raise ValueError("keep_id and merge_id must differ")

    keep = conn.execute("SELECT * FROM members WHERE id = ?", (keep_id,)).fetchone()
    merge = conn.execute("SELECT * FROM members WHERE id = ?", (merge_id,)).fetchone()
    if not keep or not merge:
        raise ValueError("both members must exist")

    for table in _LINKED_TABLES:
        conn.execute(f"UPDATE {table} SET member_id = ? WHERE member_id = ?", (keep_id, merge_id))
    conn.execute("UPDATE duplicate_flags SET member_id_a = ? WHERE member_id_a = ?", (keep_id, merge_id))
    conn.execute("UPDATE duplicate_flags SET member_id_b = ? WHERE member_id_b = ?", (keep_id, merge_id))

    fills = {}
    for field in _FILL_IF_BLANK_FIELDS:
        if not (keep[field] or "").strip() and (merge[field] or "").strip():
            fills[field] = merge[field]

    keep_visit = keep["first_visit_date"]
    merge_visit = merge["first_visit_date"]
    if keep_visit and merge_visit:
        fills["first_visit_date"] = min(keep_visit, merge_visit)
    elif merge_visit and not keep_visit:
        fills["first_visit_date"] = merge_visit

    if final_name and final_name.strip():
        fills["name"] = final_name.strip()

    if fills:
        set_clause = ", ".join(f"{k} = ?" for k in fills)
        conn.execute(
            f"UPDATE members SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fills.values(), keep_id),
        )

    conn.execute("DELETE FROM members WHERE id = ?", (merge_id,))
    conn.commit()

    result = dict(conn.execute("SELECT * FROM members WHERE id = ?", (keep_id,)).fetchone())
    return result


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _member_summary(conn, member_id: int) -> dict:
    m = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not m:
        return {"id": member_id, "deleted": True}
    history = sum(
        conn.execute(f"SELECT COUNT(*) FROM {t} WHERE member_id = ?", (member_id,)).fetchone()[0]
        for t in _LINKED_TABLES
    )
    return {
        "id": m["id"],
        "name": m["name"],
        "email": m["email"],
        "phone": m["phone"],
        "campus_preference": m["campus_preference"],
        "status": m["status"],
        "member_status": m["member_status"],
        "first_visit_date": m["first_visit_date"],
        "history_count": history,
    }


@duplicate_review_bp.route("/api/cat/duplicates/list", methods=["GET"])
@_require_key
def list_duplicates():
    with _conn() as conn:
        flags = conn.execute(
            "SELECT id, member_id_a, member_id_b, reason, created_at FROM duplicate_flags "
            "WHERE status = 'pending' AND member_id_a != member_id_b ORDER BY created_at DESC"
        ).fetchall()
        pairs = []
        for f in flags:
            a = _member_summary(conn, f["member_id_a"])
            b = _member_summary(conn, f["member_id_b"])
            if a.get("deleted") or b.get("deleted"):
                conn.execute("UPDATE duplicate_flags SET status = 'auto_resolved' WHERE id = ?", (f["id"],))
                continue
            pairs.append({
                "flag_id": f["id"],
                "reason": f["reason"],
                "created_at": f["created_at"],
                "member_a": a,
                "member_b": b,
            })
        conn.commit()
    return jsonify({"pairs": pairs}), 200


@duplicate_review_bp.route("/api/cat/duplicates/rescan", methods=["POST"])
@_require_key
def rescan():
    inserted = scan_for_duplicates()
    return jsonify({"new_candidates": inserted}), 200


@duplicate_review_bp.route("/api/cat/duplicates/merge", methods=["POST"])
@_require_key
def merge_route():
    data = request.get_json(force=True) or {}
    flag_id = data.get("flag_id")
    keep_id = data.get("keep_id")
    merge_id = data.get("merge_id")
    final_name = data.get("name")

    if not all(isinstance(v, int) for v in (flag_id, keep_id, merge_id)):
        return jsonify({"error": "flag_id, keep_id, merge_id (ints) are required"}), 400

    with _conn() as conn:
        try:
            result = merge_members(conn, keep_id, merge_id, final_name)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        conn.execute("UPDATE duplicate_flags SET status = 'merged' WHERE id = ?", (flag_id,))
        conn.commit()

    return jsonify({"kept": result}), 200


@duplicate_review_bp.route("/api/cat/duplicates/dismiss", methods=["POST"])
@_require_key
def dismiss_route():
    data = request.get_json(force=True) or {}
    flag_id = data.get("flag_id")
    if not isinstance(flag_id, int):
        return jsonify({"error": "flag_id (int) is required"}), 400

    with _conn() as conn:
        conn.execute("UPDATE duplicate_flags SET status = 'dismissed' WHERE id = ?", (flag_id,))
        conn.commit()

    return jsonify({"ok": True}), 200
