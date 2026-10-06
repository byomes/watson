"""jobs/church_calendar/share_event.py -- draft an invite for a church event
(from the unified calendar cache) and, only when asked, post it into an
approved Catalyst302 app (Subsplash) group via jobs/sms/catalyst_app_send.py.

Safety rules (Bill, 2026-10-06):
  * Default is DRY RUN: prints the draft, posts nothing. Posting needs --post.
  * Only groups in ALLOWED_GROUPS can be posted to. Add a group here only after
    Bill (or the group's leader, for Men's Fraternity: Jim Bouchat) has said yes.
  * Invites are templated from the event's own published text, never authored
    relational language. Posts go out as the church: no Watson sign-off.
  * No posting between 8pm and 8am (standing no-messages-after-8pm rule).
  * Plain ASCII, no em dashes.

Usage:
  python -m jobs.church_calendar.share_event "billiards"                    # draft only
  python -m jobs.church_calendar.share_event "billiards" --group "Teaching Team" --post
  python -m jobs.church_calendar.share_event --text "exact approved text" --group "Teaching Team" --post
"""
import argparse
import re
import sys
from datetime import datetime

from core.database import get_connection
from jobs.church_calendar.lookup import next_by_series

# Groups Watson may post event invites into. Men's Fraternity is NOT here until
# Jim Bouchat approves (asked via Telegram 2026-10-06).
ALLOWED_GROUPS = {"Teaching Team"}
QUIET_START, QUIET_END = 20, 8


def _ascii(text: str) -> str:
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("—", ", "), ("–", "-"), (" ", " ")):
        text = text.replace(a, b)
    return text.encode("ascii", "ignore").decode()


def _description_body(desc: str | None) -> str:
    """Paragraphs of the event description, cut before the address block."""
    out = []
    for line in (desc or "").splitlines():
        line = line.strip()
        if not line or re.match(r"^\d+\s+\w", line) or line == "Get directions" or "@" in line:
            break
        if not line.lower().startswith("when:"):  # date/time already stated up front
            out.append(line)
    return " ".join(out)


def build_invite(ev: dict) -> str:
    d = datetime.strptime(ev["start_date"], "%Y-%m-%d")
    when = f"{d.strftime('%A, %B')} {d.day}"
    if ev.get("time_text"):
        when += f", {ev['time_text']}"
    parts = [f"You are invited: {ev['title']}, {when}."]
    body = _description_body(ev.get("description"))
    if body:
        parts.append(body)
    if ev.get("register_url"):
        parts.append(f"Register here: {ev['register_url']}")
    else:
        parts.append(f"Details: {ev['event_url']}")
    return _ascii(" ".join(parts))


def _log_share(group: str, text: str, subsplash_id: str | None, ok: bool) -> None:
    with get_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS church_calendar_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subsplash_id TEXT, grp TEXT NOT NULL,
            text TEXT NOT NULL, ok INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("INSERT INTO church_calendar_shares (subsplash_id, grp, text, ok) VALUES (?,?,?,?)",
                     (subsplash_id, group, text, int(ok)))


def post(group: str, text: str, subsplash_id: str | None = None) -> bool:
    if group not in ALLOWED_GROUPS:
        raise PermissionError(f"{group!r} is not an approved group (allowed: {sorted(ALLOWED_GROUPS)})")
    hour = datetime.now().hour
    if hour >= QUIET_START or hour < QUIET_END:
        raise RuntimeError("quiet hours (8pm-8am): not posting")
    from jobs.sms.catalyst_app_send import send_group_message
    ok = send_group_message(group, _ascii(text))
    _log_share(group, text, subsplash_id, ok)
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--group")
    ap.add_argument("--text", help="post this exact text instead of a generated draft")
    ap.add_argument("--post", action="store_true")
    a = ap.parse_args()
    ev = None
    if a.text:
        text = a.text
    else:
        if not a.query:
            sys.exit("need a query or --text")
        matches = next_by_series(a.query)
        if len(matches) != 1:
            sys.exit("matches: " + (", ".join(m["title"] for m in matches.values()) or "none")
                     + " -- need exactly one event; refine the query")
        ev = next(iter(matches.values()))
        text = build_invite(ev)
    print(text)
    if a.post:
        if not a.group:
            sys.exit("--post needs --group")
        ok = post(a.group, text, ev["subsplash_id"] if ev else None)
        print("POSTED" if ok else "FAILED")
        sys.exit(0 if ok else 1)
