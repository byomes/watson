"""jobs/sms/autoresponder.py — Sabbath/vacation auto-reply (added 2026-10-02,
project_backlog id=39 follow-on).

Bill authors the Sabbath and vacation reply bodies himself in Settings
(jobs/sms/settings.py's sabbath_autoresponder_body / vacation_autoresponder_body)
-- Watson only sends that fixed text verbatim, same as the broadcast and
send-to-self paths, never rewriting or originating phrasing (see
feedback_ai_never_originates_relational_language).

This is a deliberate, narrow exception to "Bill always taps send": Bill
chose true auto-send (no tap) for these two cases specifically (2026-10-02),
scoped to exactly this one fixed, pre-approved body of text per mode, with a
once-per-sender-per-window dedup (sms_autoresponder_log) so a chatty sender
-- or a sender whose own phone also autoresponds -- doesn't get looped or
spammed. It does not widen the general send-authority guardrail anywhere
else in the app.

Called from both inbound paths (jobs/sms/bridge.py's poll_inbound and
jobs/sms/adb_inbound.py's poll_inbound_adb) right where they already decide
whether to suppress the push notification -- same gating condition
(sms_settings.should_silence_notifications()), just also firing a reply the
first time a given sender shows up in the window.
"""
import logging

from jobs.sms import send_core, settings as sms_settings

log = logging.getLogger(__name__)


def maybe_autorespond(conn, thread_id: int, phone: str | None, is_group: bool) -> None:
    """Best-effort -- a failure here must never break inbound ingestion.
    Skips group threads outright: the dedup/window model below is keyed to
    one sender's phone, and auto-replying into a group thread would put
    Watson's fixed text in front of everyone in it, not just the sender."""
    if is_group or not phone:
        return

    try:
        active = sms_settings.get_active_autoresponder()
        if not active:
            return
        window_key, body = active

        cur = conn.execute(
            "INSERT OR IGNORE INTO sms_autoresponder_log (phone, window_key, thread_id) VALUES (?, ?, ?)",
            (phone, window_key, thread_id),
        )
        if cur.rowcount == 0:
            # Already auto-replied to this phone in this window.
            return

        error, status, _ = send_core.send_and_record(conn, thread_id, body, None, None)
        if error:
            log.warning("maybe_autorespond: send failed for thread_id=%s window=%s: %s", thread_id, window_key, error)
    except Exception:  # noqa: BLE001 — never break inbound ingestion over an autoresponder hiccup
        log.exception("maybe_autorespond: unexpected failure for thread_id=%s", thread_id)
