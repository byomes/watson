"""jobs/congregation/bday_web.py — Flask Blueprint backing the standalone
wtsn.me/cat/bday public form: a Family Birthdays / Anniversaries-only card,
separate from the full connect card, for the congregation-wide birthdate/
anniversary collection push (see jobs/congregation/family_dates.py's own
docstring). Built 2026-09-29.

Unlike the connect card's form -> Brevo email -> IMAP poll
(jobs/connect_cards/intake.py) round-trip, this ingests straight into
congregation.db in real time, the same way jobs/congregation/papercards_web.py
does for staff-keyed paper cards -- the difference here is this endpoint is
reached by a genuinely public, unauthenticated congregation member (via the
watson-tools Next.js API route at src/app/api/cat/bday/route.ts, which holds
the shared secret server-side and does its own honeypot/fill-time bot
checks before ever calling here, same pattern as src/app/api/cat/connect/
route.ts). The X-Watson-Key header this route still requires isn't a
membership gate -- it's what keeps this URL from being directly POSTed by
anyone who isn't the one Vercel app that's supposed to call it.

A connect_cards row is still created per submission (service_date = the day
it came in, campus = 'N/A' -- see below) purely so connect_card_birthdays/
connect_card_anniversaries have a card_id to hang off of, matching every
other producer of those two tables. campus = 'N/A' keeps these out of the
Wilmington/Online buckets in campus breakdowns (monthly_engagement_report.py
etc.), but these rows DO still count toward plain "connect cards submitted"
COUNT(*) totals in state_of_church.py / monthly_state_report.py / reports.py
-- those weren't changed as part of this build (out of scope, and each is a
live pastoral report), so a raw connect-card-volume number will read a
little high while this form is being promoted. Flagged to Bill in the build
that introduced this file; worth a `source` column + query updates later if
that stat needs to stay clean.

"Your Name" is required (2026-09-29: Bill made it mandatory rather than
optional -- without it, entries only match congregation-wide, which risks
landing on the wrong same-named person instead of the submitter's own
household). Matching that name against members is still read-only
(family_dates.match_submitter), never member_match.find_or_create_member's
create-on-no-match behavior -- it exists only to bias Family Birthdays /
Anniversaries entries toward the right household (family_dates._match_name),
not to add a new person to the roster on its own. A name that doesn't match
anyone on file still works fine (submitter_member_id stays None, entries
fall back to congregation-wide matching) -- required means "typed
something," not "matched an existing member."

Mount on the Watson dashboard app:
    from jobs.congregation.bday_web import bday_web_bp
    app.register_blueprint(bday_web_bp)
"""
import os
import sqlite3
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.congregation.family_dates import (
    match_submitter,
    notify_donna_family_date_conflicts,
    notify_donna_spouse_reviews,
    notify_donna_unmatched_family_dates,
    record_anniversaries,
    record_birthdays,
)

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

bday_web_bp = Blueprint("bday_web", __name__)

# Dedicated key for this consumer (the watson-tools wtsn.me app's
# /api/cat/bday route), per this codebase's one-key-per-external-consumer
# convention -- see papercards_web.py / event_duplicate_review's own copies
# of this same pattern.
_API_KEY = lambda: os.getenv("BDAY_API_KEY", "")

# A submission carrying neither a birthday nor an anniversary entry is not
# a real card -- reject it outright rather than writing an empty
# connect_cards row for nothing.
_MAX_ENTRIES = 20  # generous headroom for a large family; well past bot-flood territory


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _clean_entries(raw, *fields: str) -> list[dict]:
    """Validate/trim a birthdays or anniversaries list from the request
    body. Mirrors the trimming watson-tools' route.ts already does before
    this is ever called, but that's a different process (a Vercel
    function) -- this endpoint doesn't trust it as the only line of
    defense."""
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw[:_MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        cleaned = {}
        for field in fields:
            v = entry.get(field)
            cleaned[field] = v.strip()[:200] if isinstance(v, str) else ""
        if any(cleaned.values()):
            out.append(cleaned)
    return out


@bday_web_bp.route("/api/cat/bday/submit", methods=["POST"])
@_require_key
def submit():
    data = request.get_json(force=True) or {}

    submitted_by_name = (data.get("submittedByName") or "").strip()[:200]
    birthdays = _clean_entries(data.get("birthdays"), "name", "date")
    anniversaries = _clean_entries(data.get("anniversaries"), "names", "date")

    # Required -- not just a matching nicety, the page's own copy now asks
    # for it directly, so this endpoint enforces it too rather than trusting
    # watson-tools' route.ts (or the form's `required` attribute) alone.
    if not submitted_by_name:
        return jsonify({"error": "Your name is required."}), 400
    if not birthdays and not anniversaries:
        return jsonify({"error": "At least one birthday or anniversary is required."}), 400

    # family_dates.record_birthdays/record_anniversaries expect {"name"/
    # "names": str, "date": str} with the date already in YYYY-MM-DD --
    # that's exactly what the form's <input type="date"> gives, same shape
    # ConnectCardForm.tsx sends.
    today = date.today().isoformat()

    with _conn() as conn:
        submitter_member_id = match_submitter(conn, submitted_by_name)

        conn.execute(
            """
            INSERT INTO connect_cards
              (member_id, service_date, campus, raw_text, questions_comments)
            VALUES (?, ?, 'N/A', NULL, ?)
            """,
            (
                submitter_member_id,
                today,
                f"Birthdays & Anniversaries card (wtsn.me/cat/bday) -- submitted by {submitted_by_name}",
            ),
        )
        card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        unmatched_bdays, bday_conflicts = record_birthdays(conn, card_id, submitter_member_id, birthdays)
        unmatched_annivs, spouse_reviews, anniv_conflicts = record_anniversaries(
            conn, card_id, submitter_member_id, anniversaries
        )

        conn.commit()

    for entry in unmatched_bdays:
        entry["submitted_by"] = submitted_by_name
    for entry in bday_conflicts:
        entry["submitted_by"] = submitted_by_name
    for entry in unmatched_annivs:
        entry["submitted_by"] = submitted_by_name
    for entry in anniv_conflicts:
        entry["submitted_by"] = submitted_by_name
    for entry in spouse_reviews:
        entry["submitted_by"] = submitted_by_name

    # Real-time, one submission at a time -- unlike intake.py's cron run
    # (which batches every card from a 30-minute window into one summary),
    # each /cat/bday submit fires its own notify call, right away.
    if unmatched_bdays or unmatched_annivs:
        notify_donna_unmatched_family_dates(unmatched_bdays, unmatched_annivs)
    if bday_conflicts or anniv_conflicts:
        notify_donna_family_date_conflicts(bday_conflicts, anniv_conflicts)
    if spouse_reviews:
        notify_donna_spouse_reviews(spouse_reviews)

    return jsonify({"ok": True, "card_id": card_id}), 200
