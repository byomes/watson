"""jobs/privacy/email_ack.py — notifies on an inbound reply from an
opt_out_method='email' broker (currently only BeenVerified) to a removal
Watson already submitted. Deliberately NOT an auto-confirmer: a free-text
reply email has no verifiable success shape the way a wizard's
success_check or confirm.py's confirmation-link-click does, and guessing
"this reply means it worked" risks exactly the false-confidence gap
project_backlog id=37 found in _submit_form()'s old bare-click success path.
So this only ever records the reply (ack_received_at/ack_snippet on
privacy_removals) and pings Bill to read and confirm it himself — status
never auto-upgrades here.

Wired into jobs/email_intake.py's run() loop as an early intercept, same
shape and same reasoning as jobs/privacy/confirm.py's (ordered right after
it — see that module's docstring for why this has to run before generic
non-whitelist triage or email_reply/reader.py's auto-draft would consume
the message first).
"""
import logging

from core.database import get_connection
from jobs.privacy import send_telegram

log = logging.getLogger(__name__)


def _sender_domain(addr: str) -> str:
    return addr.strip().lower().rsplit("@", 1)[-1]


def _email_brokers_by_domain(conn) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT id, name, opt_out_target FROM privacy_brokers WHERE opt_out_method='email'"
    ).fetchall()
    result = {}
    for r in rows:
        if r["opt_out_target"] and "@" in r["opt_out_target"]:
            result[_sender_domain(r["opt_out_target"])] = {"id": r["id"], "name": r["name"]}
    return result


def _match_broker(sender_email: str, brokers_by_domain: dict[str, dict]) -> dict | None:
    domain = _sender_domain(sender_email)
    for known_domain, broker in brokers_by_domain.items():
        if domain == known_domain or domain.endswith("." + known_domain):
            return broker
    return None


def _bug_already_open(conn, title: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM bug_tracker WHERE title=? AND status='open' LIMIT 1", (title,)
    ).fetchone()
    return row is not None


def handle_privacy_ack(uid: str, sender_email: str, subject: str, body: str) -> str | None:
    """Called from jobs/email_intake.py's run() loop, ordered right after
    jobs/privacy/confirm.py's intercept.

    Returns:
      None     — not a recognized email-method broker reply; caller falls
                 through to normal handling.
      "read"   — recorded (or a genuinely unresolvable ambiguous match,
                 logged/alerted once); caller should mark_as_read.
    Never returns "unread" — unlike confirm.py, there's no link to click and
    no retry that would ever resolve differently, so there's nothing to gain
    by leaving it for next run.
    """
    conn = get_connection()
    try:
        brokers_by_domain = _email_brokers_by_domain(conn)
        if not brokers_by_domain:
            return None
        broker = _match_broker(sender_email, brokers_by_domain)
        if not broker:
            return None

        candidates = conn.execute(
            """SELECT r.*, p.name AS person_name
               FROM privacy_removals r JOIN family_profiles p ON p.id = r.person_id
               WHERE r.broker_id=? AND r.status='submitted'
               ORDER BY r.submitted_at DESC""",
            (broker["id"],),
        ).fetchall()
        candidates = [dict(c) for c in candidates]

        removal = None
        if len(candidates) == 1:
            removal = candidates[0]
        elif len(candidates) > 1:
            text = f"{subject}\n{body}"
            narrowed = [c for c in candidates if c["person_name"] and c["person_name"] in text]
            if len(narrowed) == 1:
                removal = narrowed[0]

        if not removal:
            if len(candidates) > 1:
                key = f"Privacy Guard email_ack.py: ambiguous reply match for broker_id={broker['id']}"
                if not _bug_already_open(conn, key):
                    ids = ", ".join(str(c["id"]) for c in candidates)
                    send_telegram(
                        f"⚠️ Privacy Guard: a reply from {broker['name']} matches multiple submitted "
                        f"removals (ids: {ids}) — can't tell which one without guessing. Read it manually."
                    )
                    conn.execute(
                        "INSERT INTO bug_tracker (title, description, repo) VALUES (?, ?, 'watson')",
                        (key, f"Candidate removal ids: {ids}"),
                    )
                    conn.commit()
            # No submitted removal at all for this broker (stray/unexpected
            # reply, or already resolved some other way) -- leave for manual
            # review rather than silently discarding it.
            return "read"

        snippet = (body or "").strip()[:500]
        conn.execute(
            "UPDATE privacy_removals SET ack_received_at=datetime('now'), ack_snippet=? WHERE id=?",
            (snippet, removal["id"]),
        )
        conn.commit()
        send_telegram(
            f"📨 Privacy Guard: {broker['name']} replied to the removal request for "
            f"{removal['person_name']} — read it and confirm manually if it says the listing's gone:\n\n"
            f"{snippet[:300]}"
        )
        return "read"
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Test-drive an email-method broker reply match, standalone.")
    parser.add_argument("--uid", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--subject", default="")
    parser.add_argument("--body", default="")
    args = parser.parse_args()
    print(handle_privacy_ack(args.uid, args.sender, args.subject, args.body))
