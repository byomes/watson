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
from jobs.telegram.donna_notify import send_buttons_to_donna, send_to_donna

FUZZY_THRESHOLD = 0.82

# Lower bar used only for the submitter's own household (_match_name below)
# -- a small, already-scoped candidate pool (typically 2-6 people) where a
# near-miss is far more likely a nickname/typo than a different person.
# Caught 2026-09-29 alongside the match_first_name gap: "Kenneth Silva"
# submitted against the on-file "Ken Silva" scores 0.818 -- a hair under
# FUZZY_THRESHOLD despite being obviously the same person. The
# congregation-wide fallback keeps the stricter FUZZY_THRESHOLD, where a
# common-name collision is a real risk.
HOUSEHOLD_FUZZY_THRESHOLD = 0.75

# people.id for Donna Redman (see jobs/congregation/pin_collection.py's own
# copy of this mapping) -- every family-dates notify below goes to her via
# Telegram (she prefers it over email, see feedback_donna_prefers_telegram
# memory), not a new person on every run. The actual send goes through
# jobs.telegram.donna_notify (send_to_donna/send_buttons_to_donna), which
# holds anything outside 9am-8pm rather than sending it here directly --
# see that module's docstring for why.
DONNA_PERSON_ID = 12

_COUPLE_SPLIT_RE = re.compile(r"\s*(?:&|/|\band\b)\s*", re.IGNORECASE)


def _best_match(
    name: str, candidates: list[tuple], match_first_name: bool = False, threshold: float = FUZZY_THRESHOLD
) -> tuple[int | None, float]:
    """candidates: (id, name) rows. Returns (id, ratio) if ratio clears
    threshold (FUZZY_THRESHOLD by default), else (None, best ratio seen).

    match_first_name additionally scores a single-token submitted name (e.g.
    "Jesse", typed into a Family Birthdays box that only had one name field)
    against each candidate's own first name. A plain SequenceMatcher ratio
    of "jesse" against "Jesse Franco" is ~0.59 -- well under
    FUZZY_THRESHOLD despite being an exact match -- because the whole
    surname counts against it. Caught 2026-09-29: every self-entry across a
    batch of wtsn.me/cat/bday submissions (Jesse/Megan/Gabriel/Tara/Dino/
    Bettina/...) came back 'unmatched' even though each person was already
    on file, once the household was traced by hand. Only pass this for the
    submitter's own household (_match_name below) -- a handful of
    candidates, so the false-positive risk a bare first name would carry
    matched congregation-wide isn't present here."""
    name_l = name.lower().strip()
    best_ratio, best_id = 0.0, None
    for mid, mname in candidates:
        mname_l = (mname or "").lower()
        ratio = difflib.SequenceMatcher(None, name_l, mname_l).ratio()
        if match_first_name and " " not in name_l and mname_l:
            first_ratio = difflib.SequenceMatcher(None, name_l, mname_l.split(" ", 1)[0]).ratio()
            ratio = max(ratio, first_ratio)
        if ratio > best_ratio:
            best_ratio, best_id = ratio, mid
    if best_id is not None and best_ratio >= threshold:
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
    congregation-wide match. Excludes deactivated members (active
    disconnected/deceased) from candidates either way -- members.active
    already gates every other congregation.db view (see catalystdb_web.py's
    docstring), and a submitted birthday/anniversary/name shouldn't be the
    one path that can still silently attach itself to someone deactivated."""
    if not name:
        return None
    household_id = _household_id(conn, submitter_member_id)
    if household_id:
        household_rows = conn.execute(
            "SELECT id, name FROM members WHERE household_id = ? "
            "AND active NOT IN ('disconnected', 'deceased')",
            (household_id,),
        ).fetchall()
        member_id, _ = _best_match(
            name, household_rows, match_first_name=True, threshold=HOUSEHOLD_FUZZY_THRESHOLD
        )
        if member_id:
            return member_id
    all_rows = conn.execute(
        "SELECT id, name FROM members WHERE active NOT IN ('disconnected', 'deceased')"
    ).fetchall()
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


def _submitter_spouse_pair(conn: sqlite3.Connection, submitter_member_id: int | None) -> list[int]:
    """If submitter_member_id is one half of an on-file husband/wife pair
    (same household_id, both household_role in husband/wife), return
    [submitter_id, spouse_id]; else []. Used when an anniversary entry
    comes in with a date but no names typed -- caught 2026-09-29: several
    /cat/bday submitters left the "Couple's Names" box blank on their own
    anniversary entry (apparently assuming it was obvious whose it was),
    which _split_couple_names can't do anything with since there's no text
    to split. Rather than stage those as a bare unmatched date forever,
    infer the pair from the submitter's own household when it's already a
    clean couple on file -- the same complementary-pair case
    _try_mark_spouses below would auto-apply if the names HAD matched."""
    if not submitter_member_id:
        return []
    row = conn.execute(
        "SELECT household_id, household_role FROM members WHERE id = ?", (submitter_member_id,)
    ).fetchone()
    if not row or not row["household_id"] or row["household_role"] not in ("husband", "wife"):
        return []
    pair = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND household_role IN ('husband', 'wife') "
        "AND active NOT IN ('disconnected', 'deceased')",
        (row["household_id"],),
    ).fetchall()
    if len(pair) != 2:
        return []
    spouse_ids = [r["id"] for r in pair if r["id"] != submitter_member_id]
    if len(spouse_ids) != 1:
        return []
    return [submitter_member_id, spouse_ids[0]]


def record_birthdays(
    conn: sqlite3.Connection, card_id: int, submitter_member_id: int | None, entries: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Returns (unmatched, conflicts).

    unmatched holds entries (submitted_name, birth_date, card_row_id,
    parent_member_id) that never matched a member at all -- the common
    case being a child who isn't in congregation.db yet, since a typo
    against an existing member usually still clears FUZZY_THRESHOLD.
    parent_member_id is submitter_member_id (None if the submitter's own
    name didn't match anyone either, in which case there's no household to
    offer adding the child to). conflicts holds entries that DID match a
    member but disagreed with the birthdate already on file -- previously
    these sat silently in connect_card_birthdays with status='conflict'
    until someone thought to query it (caught by hand 2026-09-28: Sharon/
    Jim Hurst). Either way the caller is responsible for notifying someone;
    this function only stages them."""
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
        cursor = conn.execute(
            """
            INSERT INTO connect_card_birthdays
              (card_id, submitted_name, birth_date, matched_member_id, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (card_id, name or None, birth_date, matched_id, status),
        )
        if status == "unmatched":
            unmatched.append({
                "submitted_name": name,
                "birth_date": birth_date,
                "card_row_id": cursor.lastrowid,
                "parent_member_id": submitter_member_id,
            })
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
        stored_names = names
        if not names and anniv_date:
            # No couple names typed at all -- just a date. Several
            # wtsn.me/cat/bday submitters did this on their own anniversary
            # entry (2026-09-29), presumably assuming it was obvious whose
            # it was. If the submitter is already one half of a clean
            # husband/wife pair on file, that IS obvious -- infer it rather
            # than staging a permanently unmatched bare date. Record what
            # was inferred (not the literal blank submission) so anyone
            # reviewing connect_card_anniversaries later can see why.
            inferred = _submitter_spouse_pair(conn, submitter_member_id)
            if inferred:
                matched_ids = inferred
                candidates = inferred
                stored_names = " & ".join(
                    n for n in (_member_name(conn, mid) for mid in inferred) if n
                ) + " (inferred -- no names submitted)"
        spouse_status = None
        spouse_a = spouse_b = None
        if matched_ids and anniv_date:
            results = [_apply_date(conn, mid, "anniversary", anniv_date) for mid in matched_ids]
            statuses = [r[0] for r in results]
            if "conflict" in statuses:
                status = "conflict"
                conflicts.append({
                    "submitted_names": stored_names,
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
            (card_id, stored_names or None, anniv_date, ",".join(str(i) for i in matched_ids) or None, status, spouse_status),
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
    matched to an existing member (a typo, someone not yet in the system).
    They're still saved in connect_card_birthdays/connect_card_anniversaries
    either way; this just means she doesn't have to think to go query it.
    One message covering both sections rather than two separate texts.

    unmatched_birthdays here should only be entries whose parent_member_id
    came back None (the submitter's own name didn't match anyone, so there's
    no household to offer adding a child to) -- the caller routes anything
    WITH a known parent to notify_donna_child_additions below instead, since
    that's the common case (a child not yet on file) and worth a specific
    ask rather than a passive "let me know" line."""
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
    send_to_donna(text)


def child_addition_keyboard(entry: dict) -> list[list[dict]]:
    """Telegram inline_keyboard for one possible new-child ask -- see
    bot.py's CallbackQueryHandler(pattern=r"^fc_(add|skip):") for the tap
    side, which calls family_edit.add_child_by_id on confirm."""
    row_id = entry["card_row_id"]
    return [
        [{"text": "✅ Add as new child", "callback_data": f"fc_add:{row_id}"}],
        [{"text": "🚫 Not a match / skip", "callback_data": f"fc_skip:{row_id}"}],
    ]


def notify_donna_child_additions(entries: list[dict]) -> None:
    """Texts Donna (Telegram) one message per unmatched Family Birthday
    entry that DOES have a known parent -- the submitter's own name matched
    an active member, so there's a specific household to offer adding the
    child to -- with confirm/skip buttons, same one-at-a-time pattern as
    notify_donna_spouse_reviews. This is the common shape of an unmatched
    birthday submission: someone's child who isn't in congregation.db yet,
    not a typo against an existing member (a typo usually still clears
    FUZZY_THRESHOLD). Entries with no known parent still go through
    notify_donna_unmatched_family_dates above instead, since there's no
    household to name in the ask."""
    if vacation_gate(
        "normal", "jobs.congregation.family_dates.child_additions", f"{len(entries)} possible new child(ren)"
    ):
        return
    for e in entries:
        date_str = e["birth_date"] or "(no date given)"
        who = e["submitted_name"] or "(no name given)"
        text = (
            f"🎂 {e['submitted_by']} submitted a Family Birthday for {who} ({date_str}) that didn't "
            f"match anyone on file. Add {who} as a new child in {e['submitted_by']}'s household?"
        )
        send_buttons_to_donna(text, child_addition_keyboard(e))


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
    send_to_donna(text)


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
        send_buttons_to_donna(text, spouse_review_keyboard(e))
