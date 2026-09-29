"""
Matching and DB writes for the connect card's "Family Birthdays" and
"Anniversaries" fields (added to the wcky/cat connect card form 2026-09-25
for a several-week birthdate/anniversary collection push).

These entries name family members who may not be the card's submitter (a
spouse, a child) and are free text typed into a box, so they are matched
read-only against members -- never member_match.find_or_create_member's
create-on-no-match behavior, which would flood the roster with unverifiable
rows for a name someone mistyped. An entry that can't be matched with
confidence is still recorded (as 'unmatched'), so the data isn't lost by
silently writing nothing -- Dr. Bill/Donna can review via cdb_query
(jobs/skills/cdb_query.py's _TABLES) or a direct query against these tables.

Two matched members sharing one anniversary entry are a married couple by
definition, so record_anniversaries also feeds that into the same
household_id/household_role model jobs/congregation/family_edit.py already
maintains for "who is X's spouse" (Team Chat, Telegram) -- reusing
_mark_spouse_core rather than writing a second, divergent way to mark a
marriage. That function requires the caller to say which of the two is the
husband and which is the wife ("a role assignment is a deliberate human
statement", per its own docstring); a submitted anniversary carries no
gender, so this only auto-applies when both matched members already have
gender on file and it's a clean male/female pair. Anything Watson is unsure
about (gender missing on one/both, a same-gender pair, one of them already
on file as a child) isn't guessed at -- record_anniversaries hands it back
as a review entry, and jobs/connect_cards/intake.py texts Donna Redman one
Telegram message per pairing with buttons (bot.py's sp_c/sp_r
CallbackQueryHandler) so a human decides instead of Watson.
"""

import difflib
import re
import sqlite3

from core.vacation import vacation_gate
from jobs.congregation.family_edit import _mark_spouse_core
from jobs.telegram.send_to_person import send_buttons_to_person, send_to_person

FUZZY_THRESHOLD = 0.82

# people.id for Donna Redman (see jobs/congregation/pin_collection.py's own
# copy of this mapping) -- every family-dates notify below goes to her via
# Telegram (she prefers it over email, see feedback_donna_prefers_telegram
# memory), not a new person on every run.
DONNA_PERSON_ID = 12

_COUPLE_SPLIT_RE = re.compile(r"\s*(?:&|/|\band\b)\s*", re.IGNORECASE)


def _best_match(name: str, candidates: list[tuple]) -> tuple[int | None, float]:
    """candidates: (id, name) rows. Returns (id, ratio) if ratio clears
    FUZZY_THRESHOLD, else (None, best ratio seen)."""
    name_l = name.lower().strip()
    best_ratio, best_id = 0.0, None
    for mid, mname in candidates:
        ratio = difflib.SequenceMatcher(None, name_l, (mname or "").lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_id = ratio, mid
    if best_id is not None and best_ratio >= FUZZY_THRESHOLD:
        return best_id, best_ratio
    return None, best_ratio


def _household_id(conn: sqlite3.Connection, member_id: int | None) -> str | None:
    if member_id is None:
        return None
    row = conn.execute("SELECT household_id FROM members WHERE id = ?", (member_id,)).fetchone()
    return row["household_id"] if row and row["household_id"] else None


def _match_name(conn: sqlite3.Connection, name: str, submitter_member_id: int | None) -> int | None:
    """Lookup-only fuzzy match. Prefers the submitter's own household (the
    common case -- a birthday typed into a family member's field belongs to
    someone sharing the submitter's household_id) before falling back to a
    congregation-wide match."""
    if not name:
        return None
    household_id = _household_id(conn, submitter_member_id)
    if household_id:
        household_rows = conn.execute(
            "SELECT id, name FROM members WHERE household_id = ?", (household_id,)
        ).fetchall()
        member_id, _ = _best_match(name, household_rows)
        if member_id:
            return member_id
    all_rows = conn.execute("SELECT id, name FROM members").fetchall()
    member_id, _ = _best_match(name, all_rows)
    return member_id


def match_submitter(conn: sqlite3.Connection, name: str) -> int | None:
    """Lookup-only fuzzy match for a submitter's own typed name against all
    members -- no household bias, since there's no known submitter yet to
    bias from (that's the whole point of this call: establishing one).
    Public wrapper around _match_name for callers outside this module (e.g.
    jobs/congregation/bday_web.py) that want to resolve "whose family is
    this" from a free-text name field without creating a new member the
    way member_match.find_or_create_member would on no match."""
    return _match_name(conn, name, None)


def _split_couple_names(raw: str) -> list[str]:
    """'John & Jane Smith' -> ['John Smith', 'Jane Smith']. A bare first
    name on one side borrows the other side's trailing surname, since a
    couple sharing a last name (the form's own placeholder example) is the
    common case for this field."""
    parts = [p.strip() for p in _COUPLE_SPLIT_RE.split(raw) if p.strip()]
    if len(parts) != 2:
        return parts
    first, second = parts
    if " " not in second and " " in first:
        second = f"{second} {first.rsplit(' ', 1)[1]}"
    elif " " not in first and " " in second:
        first = f"{first} {second.rsplit(' ', 1)[1]}"
    return [first, second]


def _apply_date(conn: sqlite3.Connection, member_id: int, column: str, value: str) -> tuple[str, str]:
    """Set members.<column> only if currently empty -- never overwrite data
    already on file. Returns (status, existing_value): status is 'applied',
    'no_change' (already correct), or 'conflict' (existing value disagrees --
    needs a human); existing_value is '' except on 'conflict', where callers
    need it to tell a human what's on file vs what was submitted."""
    row = conn.execute(f"SELECT {column} FROM members WHERE id = ?", (member_id,)).fetchone()
    existing = (row[column] or "").strip() if row else ""
    if not existing:
        conn.execute(
            f"UPDATE members SET {column} = ?, updated_at = datetime('now') WHERE id = ?",
            (value, member_id),
        )
        return "applied", ""
    if existing == value:
        return "no_change", ""
    return "conflict", existing


def _member_name(conn: sqlite3.Connection, member_id: int) -> str | None:
    row = conn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
    return row["name"] if row else None


def record_birthdays(
    conn: sqlite3.Connection, card_id: int, submitter_member_id: int | None, entries: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Returns (unmatched, conflicts).

    unmatched holds entries (submitted_name, birth_date) that never matched
    a member at all. conflicts holds entries that DID match a member but
    disagreed with the birthdate already on file -- previously these sat
    silently in connect_card_birthdays with status='conflict' until someone
    thought to query it (caught by hand 2026-09-28: Sharon/Jim Hurst).
    Either way the caller is responsible for notifying someone; this
    function only stages them."""
    unmatched: list[dict] = []
    conflicts: list[dict] = []
    for entry in entries:
        name = (entry.get("name") or "").strip()
        birth_date = entry.get("date")
        if not name and not birth_date:
            continue
        matched_id = _match_name(conn, name, submitter_member_id) if name else None
        if matched_id and birth_date:
            status, existing = _apply_date(conn, matched_id, "birthdate", birth_date)
            if status == "conflict":
                conflicts.append({
                    "submitted_name": name,
                    "submitted_date": birth_date,
                    "matched_member_id": matched_id,
                    "matched_member_name": _member_name(conn, matched_id),
                    "existing_date": existing,
                })
        elif matched_id:
            status = "matched"
        else:
            status = "unmatched"
            unmatched.append({"submitted_name": name, "birth_date": birth_date})
        conn.execute(
            """
            INSERT INTO connect_card_birthdays
              (card_id, submitted_name, birth_date, matched_member_id, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (card_id, name or None, birth_date, matched_id, status),
        )
    return unmatched, conflicts


def _spouse_pairing_options(a_gender: str | None, b_gender: str | None) -> list[tuple[str, str]]:
    """Which (a_role, b_role) pairs are worth offering a human, given
    whatever gender is already on file. A clean complementary pair
    (male+female) never reaches here -- _try_mark_spouses auto-applies
    that case. Both known and NOT complementary (e.g. two males) means no
    role assignment fits, so there's nothing to suggest; unknown-on-one-side
    forces the pairing from the side that IS known; unknown on both sides
    means either ordering is equally plausible, so offer both."""
    if a_gender and b_gender:
        return []
    if a_gender == "male" or b_gender == "female":
        return [("husband", "wife")]
    if a_gender == "female" or b_gender == "male":
        return [("wife", "husband")]
    return [("husband", "wife"), ("wife", "husband")]


def _try_mark_spouses(conn: sqlite3.Connection, member_ids: list[int]) -> tuple[str, dict | None, dict | None]:
    """Best-effort: mark two matched members as spouses of each other via
    _mark_spouse_core, using whatever gender is already on file to decide
    who's husband/wife. Returns (status, a, b) -- a/b are the member rows
    (id, name, household_id, household_role, gender) fetched along the way,
    None if member_ids wasn't a resolvable pair. status is one of:
    'already_married', 'married', 'skipped_unknown_gender',
    'skipped_not_a_pair', 'skipped_not_found', or
    'skipped_<reason from _mark_spouse_core>' (e.g. a child on file)."""
    if len(member_ids) != 2:
        return "skipped_not_a_pair", None, None

    rows = {}
    for mid in member_ids:
        row = conn.execute(
            "SELECT id, name, household_id, household_role, gender FROM members WHERE id = ?", (mid,)
        ).fetchone()
        if not row:
            return "skipped_not_found", None, None
        rows[mid] = dict(row)
    a, b = (rows[member_ids[0]], rows[member_ids[1]])

    if (
        a["household_id"]
        and a["household_id"] == b["household_id"]
        and a["household_role"] in ("husband", "wife")
        and b["household_role"] in ("husband", "wife")
    ):
        return "already_married", a, b

    if a["gender"] == "male" and b["gender"] == "female":
        a_role, b_role = "husband", "wife"
    elif a["gender"] == "female" and b["gender"] == "male":
        a_role, b_role = "wife", "husband"
    else:
        return "skipped_unknown_gender", a, b

    ok, message = _mark_spouse_core(conn, a, b, "Connect card intake", a_role, b_role)
    if ok:
        return "married", a, b
    return ("skipped_child" if "on file as a child" in message else "skipped_other"), a, b


def record_anniversaries(
    conn: sqlite3.Connection, card_id: int, submitter_member_id: int | None, entries: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (unmatched, needs_review, conflicts).

    unmatched mirrors record_birthdays' return (submitted_names,
    anniversary_date) -- nobody on the submission matched a member at all.

    needs_review is for entries where two real members WERE matched but
    Watson won't guess who's husband/wife on its own: each entry carries
    the connect_card_anniversaries row id, both members' id/name, and the
    role-pair options a human could confirm (may be empty -- e.g. a
    same-gender pair -- in which case only rejecting is offered).

    conflicts mirrors record_birthdays' conflicts return -- a matched
    member whose anniversary on file disagrees with what was submitted.
    Previously sat silently in connect_card_anniversaries with
    status='conflict' until someone thought to query it.

    The caller (jobs/connect_cards/intake.py) is responsible for actually
    texting someone about needs_review/conflicts; this function only
    stages them."""
    unmatched: list[dict] = []
    needs_review: list[dict] = []
    conflicts: list[dict] = []
    for entry in entries:
        names = (entry.get("name") or "").strip()
        anniv_date = entry.get("date")
        if not names and not anniv_date:
            continue
        candidates = _split_couple_names(names) if names else []
        matched_ids = [mid for mid in (_match_name(conn, n, submitter_member_id) for n in candidates) if mid]
        spouse_status = None
        spouse_a = spouse_b = None
        if matched_ids and anniv_date:
            results = [_apply_date(conn, mid, "anniversary", anniv_date) for mid in matched_ids]
            statuses = [r[0] for r in results]
            if "conflict" in statuses:
                status = "conflict"
                conflicts.append({
                    "submitted_names": names,
                    "submitted_date": anniv_date,
                    "conflicting_members": [
                        {"member_id": mid, "name": _member_name(conn, mid), "existing_date": existing}
                        for mid, (st, existing) in zip(matched_ids, results) if st == "conflict"
                    ],
                })
            elif len(matched_ids) < len(candidates):
                status = "partial_match"
            else:
                status = "applied" if "applied" in statuses else "no_change"
            spouse_reason = None
            if status in ("applied", "no_change") and len(matched_ids) == 2:
                spouse_reason, spouse_a, spouse_b = _try_mark_spouses(conn, matched_ids)
                spouse_status = spouse_reason if spouse_reason in ("married", "already_married") else "pending_review"
        elif matched_ids:
            status = "matched"
        else:
            status = "unmatched"
            unmatched.append({"submitted_names": names, "anniversary_date": anniv_date})
        cur = conn.execute(
            """
            INSERT INTO connect_card_anniversaries
              (card_id, submitted_names, anniversary_date, matched_member_ids, status, spouse_link_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (card_id, names or None, anniv_date, ",".join(str(i) for i in matched_ids) or None, status, spouse_status),
        )
        if spouse_status == "pending_review":
            needs_review.append({
                "anniv_row_id": cur.lastrowid,
                "anniversary_date": anniv_date,
                "reason": spouse_reason,
                "member_ids": (spouse_a["id"], spouse_b["id"]),
                "member_names": (spouse_a["name"], spouse_b["name"]),
                "options": _spouse_pairing_options(spouse_a["gender"], spouse_b["gender"]),
            })
    return unmatched, needs_review, conflicts


# ── Donna notifications ─────────────────────────────────────────────────────
#
# Moved here from jobs/connect_cards/intake.py 2026-09-29 when
# jobs/congregation/bday_web.py (the standalone wtsn.me/cat/bday form,
# ingesting straight into congregation.db in real time rather than via the
# connect card's email/IMAP round-trip) became a second caller of
# record_birthdays/record_anniversaries that also needs to notify Donna the
# same way. intake.py still calls these (imported under its old private
# names) so its own behavior/log lines are unchanged.

def notify_donna_unmatched_family_dates(
    unmatched_birthdays: list[dict], unmatched_anniversaries: list[dict]
) -> None:
    """Texts Donna (Telegram) one summary of unmatched Family Birthdays /
    Anniversaries entries -- names typed in that couldn't be confidently
    matched to an existing member (a new child, a typo, someone not yet in
    the system). They're still saved in connect_card_birthdays/
    connect_card_anniversaries either way; this just means she doesn't have
    to think to go query it. One message covering both sections rather than
    two separate texts."""
    lines: list[str] = []

    if unmatched_birthdays:
        n = len(unmatched_birthdays)
        lines.append(f"🎂 {n} birthday submission{'s' if n != 1 else ''} didn't match anyone on file:")
        for e in unmatched_birthdays:
            date_str = e["birth_date"] or "(no date given)"
            who = e["submitted_name"] or "(no name given)"
            lines.append(f"• {who} — {date_str} (submitted by {e['submitted_by']})")

    if unmatched_anniversaries:
        if lines:
            lines.append("")
        n = len(unmatched_anniversaries)
        lines.append(f"💍 {n} anniversary submission{'s' if n != 1 else ''} didn't match anyone on file:")
        for e in unmatched_anniversaries:
            date_str = e["anniversary_date"] or "(no date given)"
            who = e["submitted_names"] or "(no names given)"
            lines.append(f"• {who} — {date_str} (submitted by {e['submitted_by']})")

    lines += ["", "Take a look and add them if they're new, or let me know who they match."]
    text = "\n".join(lines)

    if vacation_gate("normal", "jobs.congregation.family_dates.unmatched_family_dates", text):
        return
    send_to_person(DONNA_PERSON_ID, text)


def notify_donna_family_date_conflicts(
    birthday_conflicts: list[dict], anniversary_conflicts: list[dict]
) -> None:
    """Texts Donna (Telegram) one summary of birthday/anniversary
    submissions that matched an existing member but disagreed with the date
    already on file. These used to sit silently in connect_card_birthdays /
    connect_card_anniversaries with status='conflict' until someone thought
    to query it -- caught by hand 2026-09-28 (Sharon/Jim Hurst), added this
    notification so it can't happen silently again."""
    lines: list[str] = []

    if birthday_conflicts:
        n = len(birthday_conflicts)
        lines.append(f"🎂 {n} birthday submission{'s' if n != 1 else ''} disagree with what's on file:")
        for e in birthday_conflicts:
            who = e["matched_member_name"] or e["submitted_name"]
            lines.append(
                f"• {who} — submitted {e['submitted_date']}, on file {e['existing_date']} "
                f"(submitted by {e['submitted_by']})"
            )

    if anniversary_conflicts:
        if lines:
            lines.append("")
        n = len(anniversary_conflicts)
        lines.append(f"💍 {n} anniversary submission{'s' if n != 1 else ''} disagree with what's on file:")
        for e in anniversary_conflicts:
            for cm in e["conflicting_members"]:
                lines.append(
                    f"• {cm['name']} — submitted {e['submitted_date']}, on file {cm['existing_date']} "
                    f"(submitted by {e['submitted_by']})"
                )

    lines += ["", "Can you check which is right and update whichever's wrong?"]
    text = "\n".join(lines)

    if vacation_gate("normal", "jobs.congregation.family_dates.family_date_conflicts", text):
        return
    send_to_person(DONNA_PERSON_ID, text)


SPOUSE_REVIEW_REASON_TEXT = {
    "skipped_child": "one of them is on file as a child in their household, so I didn't want to override that on my own",
    "skipped_other": "I couldn't link their households automatically (they may already be in two different populated ones)",
}


def spouse_review_keyboard(entry: dict) -> list[list[dict]]:
    """Telegram inline_keyboard for one spouse-pairing review -- see
    bot.py's CallbackQueryHandler(pattern=r"^sp_(c|r):") for the tap side."""
    row_id = entry["anniv_row_id"]
    a_name, b_name = entry["member_names"]
    rows = []
    for a_role, b_role in entry["options"]:
        a_word = "Husband" if a_role == "husband" else "Wife"
        b_word = "Husband" if b_role == "husband" else "Wife"
        rows.append([{
            "text": f"✅ {a_name} = {a_word}, {b_name} = {b_word}",
            "callback_data": f"sp_c:{row_id}:{a_role}",
        }])
    rows.append([{"text": "🙅 Not married / skip", "callback_data": f"sp_r:{row_id}"}])
    return rows


def notify_donna_spouse_reviews(entries: list[dict]) -> None:
    """Texts Donna (Telegram) one message per uncertain spouse pairing, each
    with its own confirm/reject buttons -- one at a time, same pattern as
    jobs/congregation/notify_subsplash_fuzzy_review.py's duplicate-member
    review texts. A confident pairing (matching gender on file) is applied
    automatically by record_anniversaries above and never reaches here;
    this is only for the ones Watson itself is unsure about."""
    if vacation_gate("normal", "jobs.congregation.family_dates.spouse_review", f"{len(entries)} spouse pairing(s)"):
        return
    for e in entries:
        a_name, b_name = e["member_names"]
        reason_text = SPOUSE_REVIEW_REASON_TEXT.get(
            e["reason"], "I don't have gender on file for one or both of them"
        )
        text = (
            f"💍 {a_name} and {b_name} gave the same anniversary date "
            f"({e['anniversary_date']}, submitted by {e['submitted_by']}) -- looks like they might be "
            f"married, but {reason_text}. Can you confirm?"
        )
        send_buttons_to_person(DONNA_PERSON_ID, text, spouse_review_keyboard(e))
