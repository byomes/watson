"""jobs/privacy/confirm.py — matches an inbound broker confirmation email to
a pending Privacy Guard removal, clicks the confirmation link, and verifies
a real completion signal before ever upgrading status='unconfirmed' to
'submitted'. See project_backlog id=37.

Wired into jobs/email_intake.py's run() loop as an early intercept — same
pattern as its SMS-reply / Connect Card Bcc / missed-report-reply guards —
NOT a second independent IMAP poller. email_intake.py polls every minute;
jobs/email_reply/reader.py polls the same WATSON_GMAIL_ADDRESS inbox every
15 minutes and would otherwise auto-draft a reply to (or skip-and-mark-seen)
a broker confirmation email before this ever saw it, since both jobs only
ever search UNSEEN. Checking here first, inside the faster-cadence poller,
avoids that race by construction instead of coordinating two pollers.

form_selectors on privacy_brokers gets one more optional key (no schema
migration needed for this — same JSON blob the wizard "steps" key already
lives in):
    "confirmation": {
        "sender_domain": "spokeo.com",          # captured live, never guessed
        "link_pattern": "https://...",           # regex, captured live
        "success_check": {"type": "...", "value": "..."}  # jobs/privacy/verify.py's shape
    }
No broker ships with this key populated yet — Spokeo needs one real,
authorized live submission first to observe its actual confirmation email
before this can be filled in (see the KNOWN GAP comment in remove.py and
Spokeo's notes column in schema.py).
"""
import asyncio
import email as email_lib
import imaplib
import json
import logging
import os
import re

from core.database import get_connection
from jobs.browser.browser_service import get_page, goto_safe, log_browser_failure
from jobs.privacy import send_telegram
from jobs.privacy.remove import _mark_submitted
from jobs.privacy.verify import check_success

log = logging.getLogger(__name__)

MAX_CONFIRM_ATTEMPTS = 3


def _sender_domain(addr: str) -> str:
    return addr.strip().lower().rsplit("@", 1)[-1]


def _brokers_with_confirmation(conn) -> list[dict]:
    rows = conn.execute("SELECT id, name, form_selectors FROM privacy_brokers").fetchall()
    result = []
    for r in rows:
        try:
            sel = json.loads(r["form_selectors"] or "{}")
        except (TypeError, ValueError):
            continue
        conf = sel.get("confirmation")
        if conf and conf.get("sender_domain") and conf.get("link_pattern"):
            result.append({"id": r["id"], "name": r["name"], "confirmation": conf})
    return result


def _match_broker(sender_email: str, brokers: list[dict]) -> dict | None:
    domain = _sender_domain(sender_email)
    for b in brokers:
        sd = b["confirmation"]["sender_domain"].lower()
        if domain == sd or domain.endswith("." + sd):
            return b
    return None


def _match_removal(conn, broker_id: int, subject: str, body: str) -> tuple[dict | None, list[dict]]:
    """A broker's confirmation email carries no removal_id of its own, so a
    match has to be inferred. Single pending removal for this broker -> use
    it (the common case with 5 family_profiles and manually-approved
    submissions). Multiple -> only resolve if the email body names one
    candidate's person or matched_url; otherwise this refuses to guess (see
    the ambiguous-match Telegram alert in handle_privacy_confirmation)."""
    rows = conn.execute(
        """SELECT r.*, p.name AS person_name
           FROM privacy_removals r JOIN family_profiles p ON p.id = r.person_id
           WHERE r.broker_id=? AND r.status='unconfirmed'
           ORDER BY r.submitted_at DESC""",
        (broker_id,),
    ).fetchall()
    candidates = [dict(r) for r in rows]
    if len(candidates) == 1:
        return candidates[0], candidates
    if len(candidates) > 1:
        text = f"{subject}\n{body}"
        narrowed = [
            c for c in candidates
            if (c["person_name"] and c["person_name"] in text)
            or (c["matched_url"] and c["matched_url"] in text)
        ]
        if len(narrowed) == 1:
            return narrowed[0], candidates
    return None, candidates


def _fetch_raw_parts(uid: str) -> tuple[str, str]:
    """Independent short IMAP session to re-fetch one message's raw plain
    and html parts by UID. email_intake.py's get_unread() already strips
    HTML to plain text via BeautifulSoup(...).get_text(), which discards
    <a href> URLs whose visible anchor text doesn't contain the link itself
    — exactly the shape a "click here to confirm" email takes. Re-fetching
    the raw parts here avoids trusting the already-lossy pre-stripped body
    for link extraction."""
    address = os.environ["WATSON_GMAIL_ADDRESS"]
    password = os.environ["WATSON_GMAIL_APP_PASSWORD"]
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(address, password)
    mail.select("inbox")
    uid_bytes = uid.encode() if isinstance(uid, str) else uid
    _, msg_data = mail.fetch(uid_bytes, "(BODY.PEEK[])")
    mail.logout()
    if not msg_data or msg_data[0] is None:
        return "", ""
    msg = email_lib.message_from_bytes(msg_data[0][1])
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    plain = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            elif ct == "text/html" and html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace") if payload else ""
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text
    return plain or "", html or ""


def _extract_link(plain: str, html: str, link_pattern: str) -> str | None:
    rx = re.compile(link_pattern)
    m = rx.search(plain)
    if m:
        return m.group(0)
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        if rx.search(href):
            return href
    return None


def _bug_already_open(conn, title: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM bug_tracker WHERE title=? AND status='open' LIMIT 1", (title,)
    ).fetchone()
    return row is not None


def _log_bug_once(conn, title: str, description: str) -> None:
    """Dedup on title+status='open' — without this, an email that keeps
    failing the same way (e.g. a broker's template changed and link_pattern
    no longer matches) would re-alert every poll cycle instead of once."""
    if _bug_already_open(conn, title):
        return
    conn.execute(
        "INSERT INTO bug_tracker (title, description, repo) VALUES (?, ?, 'watson')",
        (title, description),
    )
    conn.commit()


async def _click_and_verify(link: str, success_check: dict | None) -> tuple[bool, str | None]:
    async with get_page() as page:
        ok = await goto_safe(page, link, wait_until="networkidle")
        if not ok:
            return False, "could not load confirmation link (robots.txt disallow or navigation failure)"
        return await check_success(page, success_check)


def handle_privacy_confirmation(uid: str, sender_email: str, subject: str, body: str) -> str | None:
    """Called from jobs/email_intake.py's run() loop, before generic
    non-whitelist triage.

    Returns:
      None     — not a recognized broker confirmation email; caller falls
                 through to normal handling.
      "read"   — resolved this run (confirmed, or a terminal failure that's
                 been logged/alerted and won't get better on retry); caller
                 should mark_as_read and continue.
      "unread" — recognized but not resolvable yet (ambiguous match, or a
                 success check that hasn't passed within the retry cap);
                 caller should leave it unread and continue — next run
                 (next minute) retries.
    """
    conn = get_connection()
    try:
        brokers = _brokers_with_confirmation(conn)
        if not brokers:
            return None
        broker = _match_broker(sender_email, brokers)
        if not broker:
            return None

        removal, candidates = _match_removal(conn, broker["id"], subject, body)
        if not removal:
            if len(candidates) > 1:
                key = f"Privacy Guard confirm.py: ambiguous match for broker_id={broker['id']}"
                if not _bug_already_open(conn, key):
                    ids = ", ".join(str(c["id"]) for c in candidates)
                    send_telegram(
                        f"⚠️ Privacy Guard: confirmation email from {broker['name']} matches "
                        f"multiple pending removals (ids: {ids}) — can't tell which one without "
                        "guessing. Resolve manually."
                    )
                    _log_bug_once(conn, key, f"Candidate removal ids: {ids}")
            # No unconfirmed removal at all for this broker (already resolved
            # some other way, or a stray/unexpected email) — leave for
            # manual review rather than silently discarding it.
            return "unread"

        plain, html = _fetch_raw_parts(uid)
        link = _extract_link(plain, html, broker["confirmation"]["link_pattern"])
        if not link:
            title = (
                f"Privacy Guard confirm.py: no confirmation link found "
                f"(broker={broker['name']}, removal_id={removal['id']})"
            )
            _log_bug_once(
                conn, title,
                "link_pattern did not match this email's body or any href in its html part — "
                "broker's confirmation template may have changed.",
            )
            send_telegram(
                f"⚠️ Privacy Guard: got a confirmation email from {broker['name']} for "
                f"{removal['person_name']} but couldn't find the confirmation link in it. Check manually."
            )
            return "read"  # body won't change on retry — logged and alerted once, stop looping

        success, reason = asyncio.run(
            _click_and_verify(link, broker["confirmation"].get("success_check"))
        )

        if success:
            _mark_submitted(removal["id"])
            send_telegram(
                f"✅ Privacy Guard: removal CONFIRMED via email link — "
                f"{removal['person_name']} on {broker['name']}."
            )
            return "read"

        attempts = removal["confirm_attempts"] + 1
        conn.execute(
            "UPDATE privacy_removals SET confirm_attempts=? WHERE id=?",
            (attempts, removal["id"]),
        )
        conn.commit()
        log_browser_failure(
            f"privacy.confirm link click ({broker['name']})", link,
            Exception(reason or "success check failed"),
        )

        if attempts >= MAX_CONFIRM_ATTEMPTS:
            send_telegram(
                f"⚠️ Privacy Guard: clicked {broker['name']}'s confirmation link for "
                f"{removal['person_name']} {attempts}x but never saw a verified success signal "
                f"({reason}). Status stays 'unconfirmed' — check manually."
            )
            return "read"
        return "unread"
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Test-drive a broker confirmation match/click, standalone.")
    parser.add_argument("--uid", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--subject", default="")
    parser.add_argument("--body", default="")
    args = parser.parse_args()
    print(handle_privacy_confirmation(args.uid, args.sender, args.subject, args.body))
