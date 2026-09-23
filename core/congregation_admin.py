"""core/congregation_admin.py -- standing admin-directive executor for
trusted congregation.db senders. Per Bill's 2026-09-23 directive: Donna
Redman (church secretary) has full authority to direct congregation.db
changes by email or Telegram, and Watson should just do them -- EXCEPT when
the change looks risky (size or phrasing), in which case Bill gets a private
Telegram heads-up BEFORE it's made, not after.

Built after Watson's Ollama triage failed on three real emails from Donna
(a 17-title leader list, an 8-team Sunday-Serve exclusion request, and a
servant-names confirmation) -- see [[project_email_triage_escalate_button]].
The escalate-to-Claude button re-triages but only ever logs/drafts; this
module is the actual execution path for congregation.db changes.

Design choice: Claude never writes raw SQL here. It only picks from a small
typed action vocabulary (mirroring jobs/congregation/serving_edit.py's and
family_edit.py's existing hand-written functions) and fills in free-text
fields (a name query, a team query, a title, a date). Python still does all
entity resolution and the actual write, through the exact same vetted
functions the Telegram allowlist branches already call -- so a bad Claude
read produces a clear "couldn't find/ambiguous" string, never an arbitrary
database mutation. This is a narrower, less flexible surface than letting
an LLM emit SQL, and that's deliberate.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from core.claude_tier import call_claude
from jobs.people.lookup import CONG_DB

log = logging.getLogger(__name__)

# Bulk-size floor: anything touching more than this many people/teams is
# ALWAYS treated as risky regardless of what Claude itself says -- defense
# in depth against a planner that under-calls its own risk. Donna's actual
# 17-title email would trip this (deliberately, on first use) so Bill sees
# one real example of the confirm flow before trusting the auto-execute path.
_BULK_RISK_THRESHOLD = 5

_ACTION_VOCABULARY = """\
Available actions (choose ANY number of these, zero or more per request):

- add_to_team: {"member": "<name>", "team": "<team name>", "position": "<optional>"}
- remove_from_team: {"member": "<name>", "team": "<team name>"}
- add_leadership_role: {"member": "<name>", "title": "<specific title, e.g. 'Worship Leader'>"}
- exclude_team_from_serving: {"team": "<team name>"}   -- removes a team from the Sunday check-off page (doesn't serve every Sunday)
- include_team_in_serving: {"team": "<team name>"}     -- reverse of the above
- update_serving_date: {"member": "<name>", "date": "<date text>"}
- update_pin_notes: {"member": "<name>", "pin_text": "<text>"}
- no_action_needed: {"reason": "<why nothing needs to change in the database>"}
- unclear: {"reason": "<what's ambiguous or missing>"}
"""

_PLANNER_SYSTEM = (
    "You are Watson's congregation-database admin planner, for Dr. Bill Yomes's "
    "church (Catalyst). Donna Redman, the church secretary, has standing "
    "authority from Dr. Bill to direct congregation.db changes -- your job is "
    "to read her request and turn it into a precise action list.\n\n"
    + _ACTION_VOCABULARY +
    "\nRules:\n"
    "- One request can produce MANY actions (e.g. a list of 15 people each get "
    "their own add_leadership_role action). List every single one -- don't "
    "summarize or skip any.\n"
    "- Never invent a member name or team name that isn't clearly in the "
    "request; use exactly the name/team text she wrote (correcting obvious "
    "typos is fine, e.g. 'Gravett' -> 'Gravatt' if the rest of the context "
    "makes it unambiguous).\n"
    "- If the request is a pure confirmation/FYI with nothing to change "
    "(e.g. confirming some names should NOT be tracked, when they clearly "
    "already aren't), use no_action_needed, not a made-up action.\n"
    "- If you cannot confidently tell what she wants, use unclear rather than "
    "guessing.\n"
    "- Separately from any individual action, set \"risky\": true if this "
    "request as a whole feels risky to execute unattended -- a large or "
    "bulk change, wording that suggests real uncertainty, or anything "
    "deleting/deactivating rather than adding. Set \"risk_reason\" when true.\n"
    "- Set \"is_directive\": false if this message isn't asking for any "
    "database change at all (a greeting, a general question, small talk, "
    "something unrelated to congregation records) -- in that case actions "
    "should be an empty list and everything else can be blank. Set it true "
    "for anything database-related, including pure confirmations that "
    "resolve to no_action_needed.\n\n"
    "Reply ONLY with JSON:\n"
    "{\n"
    '  "is_directive": true/false,\n'
    '  "summary": "<plain-English one-liner of what she is asking for>",\n'
    '  "actions": [{"type": "<one of the action names above>", "args": {...}}, ...],\n'
    '  "risky": true/false,\n'
    '  "risk_reason": "<empty string if not risky>"\n'
    "}"
)

# type -> (callable, arg-order). Every callable already exists in
# serving_edit.py/family_edit.py and is called with sender_name last, same
# as every existing Telegram allowlist branch does.
def _dispatch_table():
    from jobs.congregation.serving_edit import (
        add_to_team, remove_from_team, add_leadership_role,
        exclude_team_from_serving, include_team_in_serving,
        update_serving_date, update_pin_notes,
    )
    return {
        "add_to_team":               lambda a, s: add_to_team(a.get("member", ""), a.get("team", ""), s, a.get("position")),
        "remove_from_team":          lambda a, s: remove_from_team(a.get("member", ""), a.get("team", ""), s),
        "add_leadership_role":       lambda a, s: add_leadership_role(a.get("member", ""), a.get("title", ""), s),
        "exclude_team_from_serving": lambda a, s: exclude_team_from_serving(a.get("team", ""), s),
        "include_team_in_serving":   lambda a, s: include_team_in_serving(a.get("team", ""), s),
        "update_serving_date":       lambda a, s: update_serving_date(a.get("member", ""), a.get("date", ""), s),
        "update_pin_notes":          lambda a, s: update_pin_notes(a.get("member", ""), a.get("pin_text", ""), s),
    }


def _bootstrap() -> None:
    conn = sqlite3.connect(CONG_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS congregation_admin_actions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            requester     TEXT NOT NULL,
            channel       TEXT NOT NULL,
            request_text  TEXT NOT NULL,
            summary       TEXT,
            action_type   TEXT,
            action_args   TEXT,
            result        TEXT,
            status        TEXT NOT NULL,
            source_uid    TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    try:
        conn.execute("ALTER TABLE congregation_admin_actions ADD COLUMN source_uid TEXT")
    except Exception:
        pass  # column already exists
    conn.commit()
    conn.close()


_bootstrap()


def _already_handled(source_uid: str) -> str | None:
    """True (returns its status) if a still-open row already exists for this
    email uid -- prevents the 1-minute email-intake cron from re-planning
    (and re-notifying Bill) on the same still-unread message every cycle
    while a risky directive awaits his approval. Telegram callers never
    pass source_uid, so this is a no-op for that channel."""
    if not source_uid:
        return None
    conn = sqlite3.connect(CONG_DB)
    row = conn.execute(
        "SELECT status FROM congregation_admin_actions WHERE source_uid = ? "
        "ORDER BY id DESC LIMIT 1",
        (source_uid,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _log_action(requester, channel, request_text, summary, action_type, args, result, status, source_uid=None) -> int | None:
    try:
        conn = sqlite3.connect(CONG_DB)
        cur = conn.execute(
            """INSERT INTO congregation_admin_actions
               (requester, channel, request_text, summary, action_type, action_args, result, status, source_uid)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (requester, channel, request_text[:2000], summary, action_type,
             json.dumps(args or {}), result, status, source_uid),
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id
    except Exception as exc:
        log.error("congregation_admin: failed to log action: %s", exc)
        return None


def _notify_bill(text: str) -> None:
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    import requests as _rq
    try:
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("congregation_admin: Bill notify failed: %s", exc)


def _plan(requester_name: str, request_text: str) -> dict | None:
    raw = call_claude(
        system=_PLANNER_SYSTEM,
        user=f"Request from {requester_name}:\n\n{request_text}",
        job_name="congregation.admin_directive",
        person=requester_name,
        message=request_text[:400],
        max_tokens=4096,
    )
    if raw is None:
        return None
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception as exc:
        log.error("congregation_admin: unparseable plan JSON: %s -- %r", exc, raw[:500])
        return {"summary": "", "actions": [], "risky": True,
                "risk_reason": f"Claude's plan wasn't valid JSON: {exc}"}


def _execute_actions(requester_name: str, actions: list[dict]) -> str:
    dispatch = _dispatch_table()
    results = []
    for a in actions:
        atype = a.get("type")
        args = a.get("args") or {}
        if atype in ("no_action_needed", "unclear"):
            results.append(f"{atype}: {args.get('reason', '')}")
            continue
        fn = dispatch.get(atype)
        if not fn:
            results.append(f"⚠️ unknown action type '{atype}', skipped")
            continue
        try:
            result = fn(args, requester_name)
        except Exception as exc:
            result = f"⚠️ error: {exc}"
        results.append(result)
    return "\n".join(results)


def _send_approval_prompt(pending_id: int, requester_name: str, channel: str, summary: str, risk_reason: str, request_text: str, actions: list[dict]) -> None:
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    lines = [
        f"⚠️ {requester_name} ({channel}) sent a congregation.db request "
        f"Watson flagged as risky -- NOT executed yet.",
        "",
        f"Request: {request_text[:400]}",
        "",
        f"Plan: {summary}",
        f"Why flagged: {risk_reason}",
        "",
        "Proposed actions:",
    ]
    for a in actions:
        lines.append(f"  - {a.get('type')}: {a.get('args')}")
    text = "\n".join(lines)[:4000]
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve and execute", "callback_data": f"adx_yes:{pending_id}"},
            {"text": "🚫 Reject", "callback_data": f"adx_no:{pending_id}"},
        ]]
    }
    import requests as _rq
    try:
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
            timeout=15,
        )
    except Exception as exc:
        log.warning("congregation_admin: approval prompt send failed: %s", exc)


def handle_directive(requester_name: str, channel: str, request_text: str, source_uid: str | None = None) -> str | None:
    """Main entry point. channel is 'email' or 'telegram', for the audit log
    only. source_uid (email UID) dedups the email channel against repeated
    1-minute cron re-planning while a risky directive awaits approval --
    Telegram callers should leave it None. Returns a short human-readable
    status, safe to relay back over whichever channel called this -- OR
    None if the planner decided this message isn't a database directive at
    all, meaning the caller should fall back to its own normal handling
    (generic email triage, or plain team-chat Q&A) instead of showing
    anything from this module."""
    prior_status = _already_handled(source_uid)
    if prior_status == "awaiting_approval":
        # Distinct "⚠️" prefix, not "ℹ️" -- callers (e.g. email_intake.py)
        # use the prefix to decide whether it's now safe to mark the
        # source email as read, and it is NOT while still awaiting Bill's
        # approval tap.
        return "⚠️ Already awaiting Bill's approval for this message -- not executed yet."
    if prior_status in ("executed", "no_action"):
        return f"ℹ️ Already {prior_status} for this message -- no action taken."

    plan = _plan(requester_name, request_text)
    if plan is None:
        return (
            "⚠️ Admin-directive planning unavailable right now "
            "(Claude tier off, no key, or budget exhausted) -- handle manually."
        )

    if not plan.get("is_directive", True):
        return None

    actions = plan.get("actions") or []
    summary = plan.get("summary", "")
    risky = bool(plan.get("risky")) or len(actions) > _BULK_RISK_THRESHOLD
    risk_reason = plan.get("risk_reason") or (
        f"{len(actions)} actions in one request (over the {_BULK_RISK_THRESHOLD}-action auto-execute limit)"
        if len(actions) > _BULK_RISK_THRESHOLD else ""
    )

    if not actions:
        _log_action(requester_name, channel, request_text, summary, None, None, summary, "no_action", source_uid)
        return f"ℹ️ {summary or 'No database action needed.'}"

    if risky:
        pending_id = _log_action(
            requester_name, channel, request_text, summary,
            "MULTI" if len(actions) > 1 else actions[0].get("type"),
            {"actions": actions}, risk_reason, "awaiting_approval", source_uid,
        )
        if pending_id:
            _send_approval_prompt(pending_id, requester_name, channel, summary, risk_reason, request_text, actions)
        return "⚠️ Flagged as risky -- Bill notified privately with an Approve/Reject button, not executed."

    result_text = _execute_actions(requester_name, actions)
    _log_action(requester_name, channel, request_text, summary,
                "MULTI" if len(actions) > 1 else (actions[0].get("type") if actions else None),
                {"actions": actions}, result_text, "executed", source_uid)

    _notify_bill(
        f"✅ Watson made a congregation.db change for {requester_name} ({channel}).\n\n"
        f"Request: {request_text[:300]}\n\nWhat changed:\n{result_text}"
    )
    return f"✅ {summary}\n\n{result_text}"


def resolve_approval(pending_id: int, approved: bool) -> str:
    """Called from bot.py's adx_yes/adx_no callback. Fetches the stored plan
    by congregation_admin_actions.id, executes it (or marks rejected), and
    returns a status string for the Telegram button-tap edit."""
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM congregation_admin_actions WHERE id = ? AND status = 'awaiting_approval'",
        (pending_id,),
    ).fetchone()
    conn.close()
    if not row:
        return "⚠️ Already resolved or not found."

    requester_name = row["requester"]
    channel = row["channel"]
    request_text = row["request_text"]
    summary = row["summary"]
    source_uid = row["source_uid"]
    actions = (json.loads(row["action_args"]) or {}).get("actions", [])

    def _mark_source_read() -> None:
        # The source email was left unread while awaiting Bill's tap (see
        # handle_directive's risky branch) -- now that he's resolved it one
        # way or the other, stop leaving it sitting unread forever.
        if channel == "email" and source_uid:
            try:
                from jobs.email_intake import mark_as_read
                mark_as_read(source_uid)
            except Exception as exc:
                log.error("congregation_admin: mark_as_read after approval failed: %s", exc)

    if not approved:
        conn = sqlite3.connect(CONG_DB)
        conn.execute("UPDATE congregation_admin_actions SET status = 'rejected' WHERE id = ?", (pending_id,))
        conn.commit()
        conn.close()
        _mark_source_read()
        return f"🚫 Rejected — {requester_name}'s request was not executed."

    result_text = _execute_actions(requester_name, actions)
    conn = sqlite3.connect(CONG_DB)
    conn.execute(
        "UPDATE congregation_admin_actions SET status = 'executed', result = ? WHERE id = ?",
        (result_text, pending_id),
    )
    conn.commit()
    conn.close()
    _notify_bill(
        f"✅ Approved and executed — {requester_name} ({channel}).\n\n"
        f"Request: {request_text[:300]}\n\nWhat changed:\n{result_text}"
    )
    _mark_source_read()
    return f"✅ Approved and executed.\n\n{summary}\n\n{result_text}"
