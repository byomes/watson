"""jobs/telegram/prayer_notify_weekly.py -- sends every eligible prayer
request from the most recent Sunday to a deacon's Telegram, with the same
accountability buttons (Logged/Done, Remind me later, Escalate) a manually
triggered jobs.telegram.prayer_notify.send_notification() call already
produces.

Test phase (Bill, 2026-09-28): every request goes to a single deacon
(DEACON_NAME below) regardless of any per-deacon assignment -- splitting
this into real deacon-specific lists is a later, separate step, not done
here.

Cron: Monday 11am
  0 11 * * 1  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.telegram.prayer_notify_weekly >> /home/billyomes/watson/logs/prayer_notify_weekly.log 2>&1

Idempotent per (prayer_request_id, deacon): skips a request already sent to
this run's deacon (checks prayer_contact_log for an existing non-escalation
row) so a re-run doesn't double-notify. leadership_only requests are
silently skipped too -- prayer_notify.send_notification() already refuses
to send those (see its format_message()), same rule this reuses rather
than re-implementing.
"""
import logging
import os
import sqlite3

from jobs.connect_cards.utils import most_recent_sunday
from jobs.telegram import prayer_notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONG_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB_PATH = os.path.expanduser("~/watson/data/watson.db")

DEACON_NAME = "Jim Bouchat"  # test-phase: hardcoded single recipient, see module docstring


def _deacon_contact(full_name: str) -> tuple[int, str] | None:
    """Looks up (people.id, telegram_chat_id) in watson.db, mirroring
    prayer_notify.bill_contact()'s pattern for a non-Bill name. None if the
    deacon hasn't claimed a Telegram chat yet."""
    conn = sqlite3.connect(WATSON_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id, telegram_chat_id FROM people WHERE name = ?", (full_name,)).fetchone()
    finally:
        conn.close()
    if not row or not row["telegram_chat_id"]:
        return None
    return row["id"], row["telegram_chat_id"]


def _requests_for_sunday(service_date: str) -> list[int]:
    conn = sqlite3.connect(CONG_DB_PATH)
    try:
        rows = conn.execute(
            """SELECT pr.id
               FROM prayer_requests pr
               JOIN connect_cards cc ON cc.id = pr.card_id
               WHERE cc.service_date = ?
               ORDER BY pr.id""",
            (service_date,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _already_sent(prayer_request_id: int, deacon_person_id: int) -> bool:
    conn = sqlite3.connect(CONG_DB_PATH)
    try:
        # header IS NULL excludes an escalation row for this same
        # request+person pair (escalations use `header`, not `deacon_name`,
        # and go to a different person anyway -- Bill, not the deacon) --
        # this only matches a normal weekly-style send.
        row = conn.execute(
            "SELECT 1 FROM prayer_contact_log WHERE prayer_request_id = ? AND deacon_person_id = ? AND header IS NULL",
            (prayer_request_id, deacon_person_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def run(service_date: str | None = None) -> int:
    """Sends every eligible prayer request for `service_date` (defaults to
    the most recent Sunday) to DEACON_NAME. Returns the count actually
    sent -- leadership_only requests and already-sent ones are skipped,
    not counted."""
    d = service_date or most_recent_sunday().isoformat()

    contact = _deacon_contact(DEACON_NAME)
    if not contact:
        log.error("prayer_notify_weekly: %s has no telegram_chat_id on file -- not onboarded, nothing sent", DEACON_NAME)
        return 0
    deacon_person_id, deacon_chat_id = contact
    first_name = DEACON_NAME.split()[0]

    request_ids = _requests_for_sunday(d)
    if not request_ids:
        log.info("prayer_notify_weekly: no prayer requests for %s", d)
        return 0

    sent = 0
    for prayer_request_id in request_ids:
        if _already_sent(prayer_request_id, deacon_person_id):
            log.info("prayer_notify_weekly: request id=%s already sent to %s, skipping", prayer_request_id, DEACON_NAME)
            continue
        log_id = prayer_notify.send_notification(
            prayer_request_id, deacon_person_id, deacon_chat_id, deacon_name=first_name
        )
        if log_id is None:
            log.info("prayer_notify_weekly: request id=%s not eligible (leadership_only or missing), skipped", prayer_request_id)
            continue
        sent += 1
        log.info("prayer_notify_weekly: sent request id=%s to %s (log_id=%s)", prayer_request_id, DEACON_NAME, log_id)

    log.info("prayer_notify_weekly: done, sent %d of %d request(s) for %s", sent, len(request_ids), d)
    return sent


if __name__ == "__main__":
    run()
