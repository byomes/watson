"""jobs/congregation/kids_attendance_web.py -- Flask Blueprint backing the
wtsn.me/cat/kidsatt staff tool (kids-class attendance, present/absent
toggles + move-between-classes), same shape as jobs/congregation/
attendance_web.py's adult attendance tool.

Auth: same shared-secret pattern as attendance_web.py -- every route
requires header X-Watson-Key matching KIDS_ATTENDANCE_API_KEY, a dedicated
key for this consumer, not ATTENDANCE_API_KEY or any other (this
codebase's one-key-per-external-consumer convention).

Mount on the Watson dashboard app:
    from jobs.congregation.kids_attendance_web import kids_attendance_web_bp
    app.register_blueprint(kids_attendance_web_bp)

Data model note: like `attendance`, `kids_checkin` is the only signal for
"present" -- there's no separate absent record, and (kid_id, event_date)
has no unique constraint (see migrate_kids_checkin_tables.py), so toggle()
below checks existence before insert/delete the same way attendance_web's
toggle() does rather than relying on the schema to prevent a double row.

A leader-created/moved row gets a synthetic subsplash_checkin_id
(`leader_manual:<kid_id>:<event_date>`, stable across a class move on the
same date so toggling off/on again or moving doesn't spawn a second row)
since that column is NOT NULL and real Subsplash rows carry a real one.
checkin_source='leader_manual' distinguishes it from the Subsplash-sourced
rows kids_checkin_import.py writes, though toggle-absent removes a row
regardless of its source -- same "leader can correct anything" philosophy
attendance_web.py's toggle has for adults.

CLASS_NAMES is the fixed, ordered (youngest to oldest) set of real
classrooms seen in Subsplash data as of 2026-09-29 (Nursery, Pre-K,
Elementary Kids Church -- no Toddlers room currently in use, despite that
column existing on the older, separate `classroom_attendance` aggregate-
headcount table). "Move up/down" in the frontend just means picking a
different entry in this list.
"""
import base64
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

# Landing spot for raw Subsplash "catalyst-kids-check-ins" CSV exports
# dropped in via the /cat/kidsatt batch-import dialog -- upload only for
# now (just gets a file safely onto disk with a collision-proof name).
# Parsing/ingesting these into kids_checkin is a separate follow-up once a
# real sample has been uploaded through the dialog to confirm the export's
# exact column layout.
IMPORT_DIR = os.path.expanduser("~/watson/data/imports/kids_att_csv")
_MAX_IMPORT_BYTES = 5 * 1024 * 1024

kids_attendance_web_bp = Blueprint("kids_attendance_web", __name__)

_API_KEY = lambda: os.getenv("KIDS_ATTENDANCE_API_KEY", "")

_RECENT_SUNDAYS_COUNT = 10

CLASS_NAMES = ["Nursery", "Pre-K", "Elementary Kids Church"]


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _most_recent_sunday() -> date:
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def _recent_sundays(count: int) -> list[str]:
    latest = _most_recent_sunday()
    return [(latest - timedelta(weeks=i)).isoformat() for i in range(count)]


def _last_name_key(first: str, last: str) -> str:
    return (last or first or "").lower()


def _synthetic_checkin_id(kid_id: int, service_date: str) -> str:
    return f"leader_manual:{kid_id}:{service_date}"


def _synthetic_profile_id() -> str:
    # kids.subsplash_profile_id is UNIQUE NOT NULL -- every real row carries
    # an actual Subsplash profile id (kids_checkin_import.py), so a
    # leader-created kid needs a synthetic value in the same "leader_manual:"
    # namespace _synthetic_checkin_id already established, with a random
    # suffix (no natural per-kid key exists before the row is inserted).
    return f"leader_manual:{uuid.uuid4().hex}"


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@kids_attendance_web_bp.route("/api/cat/kidsatt/state", methods=["GET"])
@_require_key
def get_state():
    requested = request.args.get("date", "").strip()
    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    service_date = requested if requested in valid_dates else _most_recent_sunday().isoformat()

    with _conn() as conn:
        kids = conn.execute("SELECT id, first_name, last_name, current_class FROM kids").fetchall()

        today_rows = {
            row["kid_id"]: row["class_name"]
            for row in conn.execute(
                "SELECT kid_id, class_name FROM kids_checkin WHERE event_date = ?", (service_date,)
            )
        }

    buckets: dict[str, list[dict]] = {name: [] for name in CLASS_NAMES}
    for k in kids:
        present = k["id"] in today_rows
        # today's actual checkin (if any) always wins; otherwise a kid's
        # persistent current_class places them, and a kid with NO
        # current_class (removed via the "X" button/remove()) and no
        # checkin today simply doesn't appear anywhere -- that's the point
        # of current_class existing as its own field rather than being
        # derived from "most recent checkin" forever.
        class_name = today_rows.get(k["id"]) or k["current_class"]
        if not class_name:
            continue
        if class_name not in buckets:
            buckets[class_name] = []  # a class name outside CLASS_NAMES (shouldn't normally happen)
        name = f"{k['first_name']} {k['last_name'] or ''}".strip()
        buckets[class_name].append({"id": k["id"], "name": name, "present": present})

    classes = []
    for class_name in list(CLASS_NAMES) + sorted(c for c in buckets if c not in CLASS_NAMES):
        kids_list = sorted(buckets[class_name], key=lambda e: e["name"].split()[-1].lower() if e["name"] else "")
        classes.append({
            "class_name": class_name,
            "present_count": sum(1 for e in kids_list if e["present"]),
            "kids": kids_list,
        })

    return jsonify({
        "service_date": service_date,
        "recent_sundays": _recent_sundays(_RECENT_SUNDAYS_COUNT),
        "class_names": CLASS_NAMES,
        "classes": classes,
    }), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/toggle", methods=["POST"])
@_require_key
def toggle():
    data = request.get_json(force=True) or {}
    kid_id = data.get("kid_id")
    service_date = (data.get("service_date") or "").strip()
    present = bool(data.get("present"))
    class_name = (data.get("class_name") or "").strip()

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(kid_id, int):
        return jsonify({"error": "kid_id (int) is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400
    if present and class_name not in CLASS_NAMES:
        return jsonify({"error": f"class_name must be one of {CLASS_NAMES} when marking present"}), 400

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM kids WHERE id = ?", (kid_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        already_present = conn.execute(
            "SELECT 1 FROM kids_checkin WHERE kid_id = ? AND event_date = ?",
            (kid_id, service_date),
        ).fetchone() is not None

        if present and not already_present:
            # event_id is NOT NULL (migrate_kids_checkin_tables.py) -- a
            # leader-created row has no real Subsplash event either, so it
            # reuses the same synthetic id as subsplash_checkin_id rather
            # than passing NULL (caught 2026-09-30 while building add(),
            # which does the identical insert: this exact call had never
            # actually been exercised against the real NOT NULL schema
            # before, since kidsatt only went live 2026-09-29).
            checkin_id = _synthetic_checkin_id(kid_id, service_date)
            conn.execute(
                "INSERT INTO kids_checkin (kid_id, subsplash_checkin_id, event_id, class_name, event_date, "
                " checked_in_at, checkin_source) VALUES (?, ?, ?, ?, ?, datetime('now'), 'leader_manual')",
                (kid_id, checkin_id, checkin_id, class_name, service_date),
            )
        elif not present and already_present:
            conn.execute(
                "DELETE FROM kids_checkin WHERE kid_id = ? AND event_date = ?",
                (kid_id, service_date),
            )
        conn.commit()

    return jsonify({"kid_id": kid_id, "service_date": service_date, "present": present}), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/move", methods=["POST"])
@_require_key
def move():
    data = request.get_json(force=True) or {}
    kid_id = data.get("kid_id")
    service_date = (data.get("service_date") or "").strip()
    class_name = (data.get("class_name") or "").strip()

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(kid_id, int):
        return jsonify({"error": "kid_id (int) is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400
    if class_name not in CLASS_NAMES:
        return jsonify({"error": f"class_name must be one of {CLASS_NAMES}"}), 400

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM kids WHERE id = ?", (kid_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        # Reclassifying a kid (current_class) is independent of logging
        # attendance -- a leader can move someone between rooms without
        # that also marking them present. Only touch kids_checkin if a row
        # for this date already exists (i.e. they ARE marked present),
        # keeping that record's class in sync; never create one here.
        row = conn.execute(
            "SELECT id FROM kids_checkin WHERE kid_id = ? AND event_date = ?", (kid_id, service_date)
        ).fetchone()
        if row:
            conn.execute("UPDATE kids_checkin SET class_name = ? WHERE id = ?", (class_name, row["id"]))
        conn.execute(
            "UPDATE kids SET current_class = ?, updated_at = datetime('now') WHERE id = ?", (class_name, kid_id)
        )
        conn.commit()

    return jsonify({"kid_id": kid_id, "service_date": service_date, "class_name": class_name}), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/remove", methods=["POST"])
@_require_key
def remove():
    """Takes a kid out of the tool's view entirely: clears their
    persistent current_class and, if they happen to have a checkin row for
    the currently selected date, removes that too, so the "X" button
    always fully removes them from what's on screen right now regardless
    of whether they were showing as present or absent. Does NOT delete the
    kid or their attendance history -- a genuine future Subsplash checkin
    (kids_checkin_import.py) or another move() re-populates current_class."""
    data = request.get_json(force=True) or {}
    kid_id = data.get("kid_id")
    service_date = (data.get("service_date") or "").strip()

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(kid_id, int):
        return jsonify({"error": "kid_id (int) is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM kids WHERE id = ?", (kid_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "DELETE FROM kids_checkin WHERE kid_id = ? AND event_date = ?", (kid_id, service_date)
        )
        conn.execute(
            "UPDATE kids SET current_class = NULL, updated_at = datetime('now') WHERE id = ?", (kid_id,)
        )
        conn.commit()

    return jsonify({"kid_id": kid_id, "removed": True}), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/search", methods=["GET"])
@_require_key
def search():
    """Name search across every kid in the database (not scoped to today's
    roster or any one class) -- backs the "search for a kid already in the
    db" half of the add-a-kid flow, so a leader checks here before falling
    through to create_new and risking a duplicate record. Requires 2+ chars
    to avoid an unfiltered full-table scan on every keystroke of a 1-char
    query."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"candidates": []}), 200
    like = f"%{q}%"
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, first_name, last_name, current_class FROM kids "
            "WHERE first_name LIKE ? OR last_name LIKE ? "
            "OR (first_name || ' ' || COALESCE(last_name, '')) LIKE ? "
            "ORDER BY last_name, first_name LIMIT 20",
            (like, like, like),
        ).fetchall()
    candidates = [
        {
            "id": r["id"],
            "name": f"{r['first_name']} {r['last_name'] or ''}".strip(),
            "current_class": r["current_class"],
        }
        for r in rows
    ]
    return jsonify({"candidates": candidates}), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/add", methods=["POST"])
@_require_key
def add():
    """Adds a kid to a class for a given service date. Two modes, both
    ending in the same place (kids.current_class set, a kids_checkin row
    for today's date exists):

    - kid_id given: an existing kids row, already found via search() --
      does what move() + toggle(present=true) do together in one call.
    - create_new=true (with at least first_name): inserts a brand-new kids
      row first (synthetic subsplash_profile_id, created_via='leader_manual',
      see _synthetic_profile_id's docstring), then the same checkin logic.

    Optional guardian_name/guardian_phone/guardian_email are stored on the
    new checkin row only (kids_checkin already carries these per Subsplash
    rows; kids itself has no guardian columns) -- useful context for a
    brand-new kid a leader is checking in without a Subsplash profile yet.
    """
    data = request.get_json(force=True) or {}
    service_date = (data.get("service_date") or "").strip()
    class_name = (data.get("class_name") or "").strip()
    create_new = bool(data.get("create_new"))
    kid_id = data.get("kid_id")

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400
    if class_name not in CLASS_NAMES:
        return jsonify({"error": f"class_name must be one of {CLASS_NAMES}"}), 400
    if not create_new and not isinstance(kid_id, int):
        return jsonify({"error": "kid_id (int) or create_new (with first_name) is required"}), 400

    with _conn() as conn:
        if create_new:
            first_name = (data.get("first_name") or "").strip()
            last_name = (data.get("last_name") or "").strip() or None
            gender = (data.get("gender") or "").strip() or None
            if not first_name:
                return jsonify({"error": "first_name is required to add a new kid"}), 400
            cur = conn.execute(
                "INSERT INTO kids (subsplash_profile_id, first_name, last_name, gender, "
                " created_via, current_class) VALUES (?, ?, ?, ?, 'leader_manual', ?)",
                (_synthetic_profile_id(), first_name, last_name, gender, class_name),
            )
            kid_id = cur.lastrowid
        else:
            existing = conn.execute("SELECT id FROM kids WHERE id = ?", (kid_id,)).fetchone()
            if not existing:
                return jsonify({"error": "not found"}), 404
            conn.execute(
                "UPDATE kids SET current_class = ?, updated_at = datetime('now') WHERE id = ?",
                (class_name, kid_id),
            )

        guardian_name = (data.get("guardian_name") or "").strip() or None
        guardian_phone = (data.get("guardian_phone") or "").strip() or None
        guardian_email = (data.get("guardian_email") or "").strip() or None

        already_present = conn.execute(
            "SELECT id FROM kids_checkin WHERE kid_id = ? AND event_date = ?",
            (kid_id, service_date),
        ).fetchone()
        if already_present:
            conn.execute("UPDATE kids_checkin SET class_name = ? WHERE id = ?", (class_name, already_present["id"]))
        else:
            checkin_id = _synthetic_checkin_id(kid_id, service_date)
            conn.execute(
                "INSERT INTO kids_checkin (kid_id, subsplash_checkin_id, event_id, class_name, event_date, "
                " checked_in_at, guardian_name, guardian_phone, guardian_email, checkin_source) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, 'leader_manual')",
                (
                    kid_id, checkin_id, checkin_id, class_name, service_date,
                    guardian_name, guardian_phone, guardian_email,
                ),
            )
        conn.commit()

    return jsonify({"kid_id": kid_id, "service_date": service_date, "class_name": class_name}), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/import", methods=["POST"])
@_require_key
def import_csv():
    """Receives one CSV file from the /cat/kidsatt batch-import dialog and
    writes it to IMPORT_DIR -- upload only, no parsing. The frontend can't
    send this box a real multipart upload, so the file arrives
    base64-encoded inside a JSON body instead (watsonFetch's shared
    transport is JSON-only, see src/lib/watson.ts on watson-tools)."""
    data = request.get_json(force=True) or {}
    filename = (data.get("filename") or "").strip()
    content_b64 = data.get("content_base64") or ""

    if not filename.lower().endswith(".csv"):
        return jsonify({"error": "file must be a .csv"}), 400

    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception:
        return jsonify({"error": "invalid file content"}), 400

    if not content:
        return jsonify({"error": "file is empty"}), 400
    if len(content) > _MAX_IMPORT_BYTES:
        return jsonify({"error": "file is too large (5MB max)"}), 400

    # Strip to a bare basename (no path components from the client) and
    # drop anything but a conservative safe-filename charset, so a crafted
    # filename can't escape IMPORT_DIR or collide with shell-unsafe chars.
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename)) or "upload.csv"
    stamped_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe_name}"

    os.makedirs(IMPORT_DIR, exist_ok=True)
    dest_path = os.path.join(IMPORT_DIR, stamped_name)
    with open(dest_path, "wb") as f:
        f.write(content)

    return jsonify({"filename": stamped_name, "size": len(content), "saved": True}), 200
