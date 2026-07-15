"""
batch_update.py — shared engine for batch member field updates
(attendance, member_status, campus_preference, shepherding_exempt).

Never creates new members. Every write requires an explicit confirmation
step: batch_update_members() only resolves/previews, commit_batch_update()
is the only function that writes, and it is only ever called after a
pending resolution has been fully worked through by one of the two
front-ends built on top of this module:

  - jobs/skills/cdb_query.py — dashboard `cdb: mark ...` text-reply flow
    (mark / pick / confirm / cancel typed back into the terminal or chat box)
  - bot/bot.py — Telegram inline-button flow (tg_pending_actions-style,
    but keyed off the batch_update_pending table below since a single
    batch spans several button taps)

Both front-ends share the same pending-state table so either interface can
resolve ambiguous names one at a time and only write once every name is
either matched or explicitly excluded (skipped / not found).
"""

import json
import logging
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

CONG_DB = Path(__file__).resolve().parents[2] / "data" / "congregation.db"
WATSON_DB = Path(__file__).resolve().parents[2] / "data" / "watson.db"

FIELDS = ("attendance", "member_status", "campus_preference", "shepherding_exempt")

FIELD_LABELS = {
    "attendance": "Attendance",
    "member_status": "Status",
    "campus_preference": "Campus",
    "shepherding_exempt": "Shepherding Exempt",
}

MEMBER_STATUS_VALUES = {"active", "deceased", "disconnected", "non_local", "snowbird"}
CAMPUS_VALUES = {"wilmington": "Wilmington", "online": "Online", "hybrid": "Hybrid"}

# Fuzzy-match tiers, per spec: a single candidate scoring >= MATCH_THRESHOLD
# is a clean match; multiple candidates within AMBIGUOUS_MARGIN points of
# the best (and still >= MATCH_THRESHOLD) are ambiguous; anything below
# MATCH_THRESHOLD is not found.
MATCH_THRESHOLD = 90
AMBIGUOUS_MARGIN = 5


def _bootstrap() -> None:
    """Idempotent schema setup — safe to run from either bot.py or app.py's process."""
    try:
        conn = sqlite3.connect(str(CONG_DB))
        try:
            conn.execute("ALTER TABLE attendance ADD COLUMN source TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS member_aliases (
                id         INTEGER PRIMARY KEY,
                member_id  INTEGER NOT NULL REFERENCES members(id),
                alias      TEXT    NOT NULL,
                added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("batch_update congregation.db bootstrap: %s", exc)

    try:
        conn = sqlite3.connect(str(WATSON_DB))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_update_pending (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                field         TEXT    NOT NULL,
                value         TEXT    NOT NULL,
                value_display TEXT    NOT NULL,
                payload       TEXT    NOT NULL,
                interface     TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending',
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("batch_update watson.db bootstrap: %s", exc)


_bootstrap()


def _cong_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CONG_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _watson_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(WATSON_DB))
    conn.row_factory = sqlite3.Row
    return conn


# ── Parsing ──────────────────────────────────────────────────────────────────

def _split_names(text: str) -> list[str]:
    return [n.strip() for n in text.split(",") if n.strip()]


def _parse_date(text: str) -> str | None:
    text = text.strip().lower()
    if text in ("last sunday", "this sunday", "sunday", "this past sunday"):
        from jobs.connect_cards.utils import most_recent_sunday
        return most_recent_sunday().isoformat()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})/(\d{1,2})$", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            return date(date.today().year, month, day).isoformat()
        except ValueError:
            return None
    return None


def parse_mark_command(text: str) -> dict | None:
    """
    Parse a 'mark ...' cdb: sub-command, e.g.:
      mark attended 7/13: Jane Smith, Bob Wilson
      mark status deceased: John Doe
      mark campus Online: Jane Smith, Bob Wilson
      mark shepherding exempt: Mel Yomes, Kaci Gravatt
      mark shepherding unexempt: Mel Yomes

    Returns None if `text` isn't a mark command at all (caller should fall
    through to other handling). Returns {"error": "..."} for a recognized
    but invalid mark command. Otherwise returns
    {"field", "value", "value_display", "names"}.
    """
    t = text.strip()
    if not t.lower().startswith("mark "):
        return None
    body = t[len("mark "):].strip()

    m = re.match(r"^attended\s+(.+?)\s*:\s*(.+)$", body, re.IGNORECASE)
    if m:
        date_str, names_str = m.group(1).strip(), m.group(2).strip()
        parsed_date = _parse_date(date_str)
        if not parsed_date:
            return {"error": f"Could not parse date: {date_str!r} (try 7/13, 7/13/2026, or 'last sunday')"}
        names = _split_names(names_str)
        if not names:
            return {"error": "No names provided."}
        return {"field": "attendance", "value": parsed_date, "value_display": parsed_date, "names": names}

    m = re.match(r"^status\s+(\S+?)\s*:\s*(.+)$", body, re.IGNORECASE)
    if m:
        status, names_str = m.group(1).strip().lower(), m.group(2).strip()
        if status not in MEMBER_STATUS_VALUES:
            return {"error": f"Invalid status {status!r}. Valid: {', '.join(sorted(MEMBER_STATUS_VALUES))}"}
        names = _split_names(names_str)
        if not names:
            return {"error": "No names provided."}
        return {"field": "member_status", "value": status, "value_display": status, "names": names}

    m = re.match(r"^campus\s+(\S+?)\s*:\s*(.+)$", body, re.IGNORECASE)
    if m:
        campus_raw, names_str = m.group(1).strip().lower(), m.group(2).strip()
        campus = CAMPUS_VALUES.get(campus_raw)
        if not campus:
            return {"error": f"Invalid campus {campus_raw!r}. Valid: Wilmington, Online, Hybrid"}
        names = _split_names(names_str)
        if not names:
            return {"error": "No names provided."}
        return {"field": "campus_preference", "value": campus, "value_display": campus, "names": names}

    m = re.match(r"^shepherding\s+(exempt|unexempt)\s*:\s*(.+)$", body, re.IGNORECASE)
    if m:
        mode, names_str = m.group(1).strip().lower(), m.group(2).strip()
        value = mode == "exempt"
        names = _split_names(names_str)
        if not names:
            return {"error": "No names provided."}
        return {
            "field": "shepherding_exempt",
            "value": value,
            "value_display": "Exempt" if value else "Not Exempt",
            "names": names,
        }

    return {"error": f"Unrecognized mark command: {body!r}"}


def validate_value(field: str, value) -> str | None:
    """Return an error string if value is invalid for field, else None."""
    if field == "attendance":
        try:
            datetime.strptime(str(value), "%Y-%m-%d")
        except ValueError:
            return f"Invalid date: {value!r} (expected YYYY-MM-DD)"
    elif field == "member_status":
        if value not in MEMBER_STATUS_VALUES:
            return f"Invalid member_status: {value!r}. Valid: {', '.join(sorted(MEMBER_STATUS_VALUES))}"
    elif field == "campus_preference":
        if value not in CAMPUS_VALUES.values():
            return f"Invalid campus_preference: {value!r}. Valid: Wilmington, Online, Hybrid"
    elif field == "shepherding_exempt":
        if not isinstance(value, bool):
            return f"Invalid shepherding_exempt: {value!r} (expected true/false)"
    else:
        return f"Unknown field: {field!r}"
    return None


# ── Resolution (read-only preview) ──────────────────────────────────────────

def _current_value(field: str, member: sqlite3.Row, conn: sqlite3.Connection):
    if field == "attendance":
        row = conn.execute(
            "SELECT MAX(service_date) as d FROM attendance WHERE member_id = ?", (member["id"],)
        ).fetchone()
        return row["d"] if row else None
    if field == "shepherding_exempt":
        return bool(member["shepherding_exempt"])
    return member[field]


def resolve_alias(name: str) -> list[int]:
    """
    Case-insensitive exact match against member_aliases.alias — no fuzzy
    matching. Returns the member_ids mapped to this alias (0, 1, or many;
    the same alias string is allowed to point at different members).
    """
    conn = _cong_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT member_id FROM member_aliases WHERE alias = ? COLLATE NOCASE",
            (name.strip(),),
        ).fetchall()
        return [r["member_id"] for r in rows]
    finally:
        conn.close()


def _fuzzy_resolve_name(name: str, members: list[sqlite3.Row]):
    """
    Score `name` against every member's name via rapidfuzz. Returns
    (tier, data):
      "matched"    -> data is the single matching sqlite3.Row
      "ambiguous"  -> data is a list of candidate sqlite3.Row
      "not_found"  -> data is None
    """
    scored = sorted(
        ((fuzz.token_sort_ratio(name, m["name"] or ""), m) for m in members),
        key=lambda t: t[0],
        reverse=True,
    )
    if not scored or scored[0][0] < MATCH_THRESHOLD:
        return "not_found", None
    best_score = scored[0][0]
    top = [t for t in scored if t[0] >= best_score - AMBIGUOUS_MARGIN and t[0] >= MATCH_THRESHOLD]
    if len(top) == 1:
        return "matched", top[0][1]
    return "ambiguous", [m for _, m in top]


def _matched_entry(name: str, member: sqlite3.Row, field: str, conn: sqlite3.Connection) -> dict:
    return {
        "name": name,
        "member_id": member["id"],
        "member_name": member["name"],
        "current_value": _current_value(field, member, conn),
    }


def _ambiguous_entry(name: str, candidates: list[sqlite3.Row]) -> dict:
    return {
        "name": name,
        "candidates": [
            {
                "member_id": m["id"],
                "name": m["name"],
                "campus": m["campus_preference"],
                "status": m["member_status"],
            }
            for m in candidates
        ],
    }


def batch_update_members(field: str, value, names: list[str]) -> dict:
    """
    Resolve/preview only — does NOT write anything.

    Each name checks member_aliases first (exact, case-insensitive): a
    single alias hit matches outright and skips fuzzy scoring entirely; a
    multi-member alias hit is ambiguous (aliased candidates, not fuzzy
    scores); no alias hit falls through to the existing rapidfuzz matching.

    Returns {matched: [...], ambiguous: [...], not_found: [...]}.
    """
    if field not in FIELDS:
        raise ValueError(f"Unknown field: {field!r}")

    matched, ambiguous, not_found = [], [], []
    conn = _cong_conn()
    try:
        members = conn.execute(
            "SELECT id, name, campus_preference, member_status, shepherding_exempt FROM members"
        ).fetchall()
        members_by_id = {m["id"]: m for m in members}

        for raw_name in names:
            name = raw_name.strip()
            if not name:
                continue

            alias_ids = resolve_alias(name)
            alias_members = [members_by_id[i] for i in alias_ids if i in members_by_id]
            if len(alias_members) == 1:
                matched.append(_matched_entry(name, alias_members[0], field, conn))
                continue
            if len(alias_members) > 1:
                ambiguous.append(_ambiguous_entry(name, alias_members))
                continue

            tier, data = _fuzzy_resolve_name(name, members)
            if tier == "not_found":
                not_found.append(name)
            elif tier == "matched":
                matched.append(_matched_entry(name, data, field, conn))
            else:
                ambiguous.append(_ambiguous_entry(name, data))
    finally:
        conn.close()

    return {"matched": matched, "ambiguous": ambiguous, "not_found": not_found}


# ── Commit (the only function that writes) ──────────────────────────────────

def commit_batch_update(field: str, value, resolved_member_ids: list[int], actor: str = "Bill") -> dict:
    """
    Single transaction, all-or-nothing. Only ever operates on member_ids
    that already exist — never creates a new member.
    """
    err = validate_value(field, value)
    if err:
        return {"applied": [], "errors": [err]}
    if not resolved_member_ids:
        return {"applied": [], "errors": ["No member ids to apply."]}

    conn = _cong_conn()
    try:
        placeholders = ",".join("?" * len(resolved_member_ids))
        select_col = "campus_preference" if field == "attendance" else field
        rows = conn.execute(
            f"SELECT id, name, {select_col} FROM members WHERE id IN ({placeholders})",
            resolved_member_ids,
        ).fetchall()
        rows_by_id = {r["id"]: r for r in rows}

        missing = [mid for mid in resolved_member_ids if mid not in rows_by_id]
        if missing:
            return {"applied": [], "errors": [f"member_id(s) not found: {missing}"]}

        applied = []
        conn.execute("BEGIN")
        for member_id in resolved_member_ids:
            row = rows_by_id[member_id]
            if field == "attendance":
                old_value = conn.execute(
                    "SELECT MAX(service_date) as d FROM attendance WHERE member_id = ?", (member_id,)
                ).fetchone()["d"]
                campus = row["campus_preference"] or "Wilmington"
                conn.execute(
                    "INSERT INTO attendance (member_id, service_date, campus, card_id, source) "
                    "VALUES (?, ?, ?, NULL, 'manual_batch')",
                    (member_id, value, campus),
                )
                new_value = value
            elif field == "shepherding_exempt":
                old_value = bool(row["shepherding_exempt"])
                conn.execute(
                    "UPDATE members SET shepherding_exempt = ? WHERE id = ?",
                    (1 if value else 0, member_id),
                )
                new_value = bool(value)
            else:
                old_value = row[field]
                conn.execute(f"UPDATE members SET {field} = ? WHERE id = ?", (value, member_id))
                new_value = value

            applied.append({
                "member_id": member_id, "name": row["name"],
                "old_value": old_value, "new_value": new_value,
            })
            log.info(
                "batch_update write: who=%s field=%s member_id=%s name=%r old=%r new=%r",
                actor, field, member_id, row["name"], old_value, new_value,
            )
        conn.commit()
        return {"applied": applied, "errors": []}
    except Exception as exc:
        conn.rollback()
        log.error("batch_update commit failed, rolled back: %s", exc)
        return {"applied": [], "errors": [str(exc)]}
    finally:
        conn.close()


# ── Pending-state store (shared by dashboard text flow + Telegram buttons) ──

def create_pending(field: str, value, value_display: str, resolution: dict, interface: str) -> int:
    conn = _watson_conn()
    payload = json.dumps({
        "matched": resolution["matched"],
        "ambiguous": resolution["ambiguous"],
        "not_found": resolution["not_found"],
    })
    cur = conn.execute(
        "INSERT INTO batch_update_pending (field, value, value_display, payload, interface) "
        "VALUES (?, ?, ?, ?, ?)",
        (field, json.dumps(value), value_display, payload, interface),
    )
    conn.commit()
    pending_id = cur.lastrowid
    conn.close()
    return pending_id


def get_pending(pending_id: int) -> dict | None:
    conn = _watson_conn()
    row = conn.execute("SELECT * FROM batch_update_pending WHERE id = ?", (pending_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["value"] = json.loads(d["value"])
    d["payload"] = json.loads(d["payload"])
    return d


def _save_payload(pending_id: int, payload: dict) -> None:
    conn = _watson_conn()
    conn.execute(
        "UPDATE batch_update_pending SET payload = ? WHERE id = ?",
        (json.dumps(payload), pending_id),
    )
    conn.commit()
    conn.close()


def current_ambiguous(pending: dict) -> dict | None:
    amb = pending["payload"]["ambiguous"]
    return amb[0] if amb else None


def resolve_current_ambiguous(pending_id: int, choice) -> dict:
    """
    choice: 1-based candidate index (int or numeric str) or 'skip'.
    Resolves the FIRST unresolved ambiguous name. Returns the updated
    pending dict, or {"error": "..."}.
    """
    pending = get_pending(pending_id)
    if not pending or pending["status"] != "pending":
        return {"error": "This batch update has expired or was already resolved."}

    payload = pending["payload"]
    amb = payload["ambiguous"]
    if not amb:
        return {"error": "No ambiguous names left to resolve."}

    entry = amb.pop(0)
    choice_str = str(choice).strip().lower()
    if choice_str == "skip":
        payload["not_found"].append(entry["name"])
    else:
        try:
            idx = int(choice_str) - 1
        except ValueError:
            amb.insert(0, entry)
            return {"error": f"Invalid pick {choice!r}. Choose 1-{len(entry['candidates'])} or 'skip'."}
        candidates = entry["candidates"]
        if idx < 0 or idx >= len(candidates):
            amb.insert(0, entry)
            return {"error": f"Invalid pick. Choose 1-{len(candidates)} or 'skip'."}
        cand = candidates[idx]
        conn = _cong_conn()
        try:
            member_row = conn.execute(
                "SELECT id, name, campus_preference, member_status, shepherding_exempt "
                "FROM members WHERE id = ?", (cand["member_id"],),
            ).fetchone()
            current_value = _current_value(pending["field"], member_row, conn) if member_row else None
        finally:
            conn.close()
        payload["matched"].append({
            "name": entry["name"],
            "member_id": cand["member_id"],
            "member_name": cand["name"],
            "current_value": current_value,
        })

    _save_payload(pending_id, payload)
    return get_pending(pending_id)


def cancel_pending(pending_id: int) -> None:
    conn = _watson_conn()
    conn.execute("UPDATE batch_update_pending SET status = 'cancelled' WHERE id = ?", (pending_id,))
    conn.commit()
    conn.close()


def finalize_pending(pending_id: int, actor: str = "Bill") -> dict:
    pending = get_pending(pending_id)
    if not pending or pending["status"] != "pending":
        return {"applied": [], "errors": ["This batch update has expired or was already resolved."]}
    if pending["payload"]["ambiguous"]:
        return {"applied": [], "errors": ["Resolve all ambiguous names first."]}

    member_ids = [m["member_id"] for m in pending["payload"]["matched"]]
    result = commit_batch_update(pending["field"], pending["value"], member_ids, actor=actor)

    conn = _watson_conn()
    conn.execute(
        "UPDATE batch_update_pending SET status = ? WHERE id = ?",
        ("done" if not result["errors"] else "pending", pending_id),
    )
    conn.commit()
    conn.close()
    return result


# ── Text rendering (dashboard flow, and reused for Telegram message bodies) ─

def format_ambiguous_prompt(entry: dict) -> str:
    lines = [f'Ambiguous: "{entry["name"]}"']
    for i, c in enumerate(entry["candidates"], 1):
        extra = " / ".join(x for x in [c.get("campus"), c.get("status")] if x)
        lines.append(f"  {i}) {c['name']}" + (f" ({extra})" if extra else ""))
    return "\n".join(lines)


def format_preview(pending: dict) -> str:
    field, value_display = pending["field"], pending["value_display"]
    payload = pending["payload"]
    lines = [f"Batch update #{pending['id']} — {FIELD_LABELS.get(field, field)} → {value_display}", ""]
    if payload["matched"]:
        lines.append("Will update:")
        for m in payload["matched"]:
            cur = m["current_value"] if m["current_value"] not in (None, "") else "(none)"
            lines.append(f"  {m['member_name']}: {cur} → {value_display}")
    else:
        lines.append("Nothing to update — no names resolved.")
    if payload["not_found"]:
        lines.append("")
        lines.append("Not found (excluded):")
        for n in payload["not_found"]:
            lines.append(f"  {n}")
    if field == "campus_preference":
        lines.append("")
        lines.append(
            "Note: campus_classifier.py (Mon 5:45am) may revert this based on "
            "future connect-card history."
        )
    return "\n".join(lines)


# ── Dashboard text-command handlers (mark / pick / confirm / cancel) ───────

def _render_state(pending_id: int) -> str:
    pending = get_pending(pending_id)
    if not pending:
        return "Batch update not found."
    entry = current_ambiguous(pending)
    if entry:
        return (
            format_ambiguous_prompt(entry)
            + f"\n\nReply: cdb: pick {pending_id} <#>   or   cdb: pick {pending_id} skip"
        )
    preview = format_preview(pending)
    return preview + f"\n\nReply: cdb: confirm {pending_id}   or   cdb: cancel {pending_id}"


def handle_mark_command(text: str, interface: str = "dashboard") -> str:
    parsed = parse_mark_command(text)
    if parsed is None:
        return "Not a recognized mark command."
    if "error" in parsed:
        return parsed["error"]
    err = validate_value(parsed["field"], parsed["value"])
    if err:
        return err
    resolution = batch_update_members(parsed["field"], parsed["value"], parsed["names"])
    pending_id = create_pending(
        parsed["field"], parsed["value"], parsed["value_display"], resolution, interface
    )
    return _render_state(pending_id)


def handle_pick_command(pending_id: int, choice, interface: str = "dashboard") -> str:
    result = resolve_current_ambiguous(pending_id, choice)
    if "error" in result:
        return result["error"]
    return _render_state(pending_id)


def handle_confirm_command(pending_id: int, actor: str = "Bill") -> str:
    pending = get_pending(pending_id)
    if not pending or pending["status"] != "pending":
        return "This batch update has expired or was already resolved."
    if pending["payload"]["ambiguous"]:
        return "Resolve all ambiguous names first.\n\n" + _render_state(pending_id)
    result = finalize_pending(pending_id, actor=actor)
    if result["errors"]:
        return "Batch update failed:\n" + "\n".join(result["errors"])
    lines = [f"Applied {len(result['applied'])} update(s):"]
    for a in result["applied"]:
        old = a["old_value"] if a["old_value"] not in (None, "") else "(none)"
        lines.append(f"  {a['name']}: {old} → {a['new_value']}")
    return "\n".join(lines)


def handle_cancel_command(pending_id: int) -> str:
    cancel_pending(pending_id)
    return f"Cancelled batch update #{pending_id}."


# ── Alias management (cdb: alias <name> = <alias>) ──────────────────────────

def parse_alias_command(text: str) -> dict | None:
    """
    Parse an 'alias <full name> = <alias>' cdb: sub-command, e.g.:
      alias Melanie Yomes = Mel
      alias William Yomes = Bill

    Returns None if `text` isn't an alias command at all. Returns
    {"error": "..."} for a malformed one. Otherwise {"name", "alias"}.
    """
    t = text.strip()
    if not t.lower().startswith("alias "):
        return None
    body = t[len("alias "):]
    if "=" not in body:
        return {"error": "Format: alias <full name> = <alias>"}
    name_part, alias_part = body.split("=", 1)
    name, alias = name_part.strip(), alias_part.strip()
    if not name or not alias:
        return {"error": "Format: alias <full name> = <alias>"}
    return {"name": name, "alias": alias}


def add_alias(member_id: int, alias: str, actor: str = "Bill") -> None:
    """Insert an alias for member_id. Never creates or modifies a member record."""
    conn = _cong_conn()
    try:
        existing = conn.execute(
            "SELECT 1 FROM member_aliases WHERE member_id = ? AND alias = ? COLLATE NOCASE",
            (member_id, alias),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO member_aliases (member_id, alias) VALUES (?, ?)",
            (member_id, alias),
        )
        conn.commit()
    finally:
        conn.close()
    log.info("alias add: who=%s member_id=%s alias=%r", actor, member_id, alias)


def handle_alias_command(text: str, actor: str = "Bill") -> str:
    """
    Handle a 'cdb: alias <name> = <alias>' directive. Admin action, not a
    batch operation — no pending/confirm flow. The left-hand name must
    resolve to exactly one member via the same rapidfuzz matching used
    elsewhere in the engine, or the alias is rejected outright.
    """
    parsed = parse_alias_command(text)
    if parsed is None:
        return "Not a recognized alias command."
    if "error" in parsed:
        return parsed["error"]

    conn = _cong_conn()
    try:
        members = conn.execute(
            "SELECT id, name, campus_preference, member_status, shepherding_exempt FROM members"
        ).fetchall()
    finally:
        conn.close()

    tier, data = _fuzzy_resolve_name(parsed["name"], members)
    if tier == "not_found":
        return f"No member found matching {parsed['name']!r}. Alias not created."
    if tier == "ambiguous":
        options = ", ".join(m["name"] for m in data)
        return f"{parsed['name']!r} is ambiguous ({options}). Be more specific — alias not created."

    member = data
    add_alias(member["id"], parsed["alias"], actor=actor)
    return f"Alias added: {parsed['alias']!r} → {member['name']} (member_id {member['id']})"
