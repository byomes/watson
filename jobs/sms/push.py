"""jobs/sms/push.py — Web Push sending for Watson SMS (project_backlog
id=39). Degrades gracefully everywhere: no VAPID_PRIVATE_KEY configured, no
subscriptions, or a single bad subscription must never raise into the
caller (bridge.py's poll_inbound()) — one dead browser subscription can't
be allowed to silently stop new-text alerts from ever running again.
"""
import json
import logging
import os

from pywebpush import WebPushException, webpush

from core.database import get_connection

log = logging.getLogger(__name__)

_VAPID_PRIVATE_KEY = lambda: os.getenv("VAPID_PRIVATE_KEY", "")
_VAPID_SUBJECT = lambda: os.getenv("VAPID_SUBJECT", "")


def send_push_to_all(payload: dict) -> None:
    """Sends `payload` (JSON-encoded) as a Web Push notification to every
    stored subscription. Stale (404/410) subscriptions are pruned as they're
    found. Any other per-subscription failure is logged and skipped — never
    raised — so one bad row can't block the rest or crash the caller."""
    private_key = _VAPID_PRIVATE_KEY()
    if not private_key:
        log.info("send_push_to_all: VAPID_PRIVATE_KEY not set, skipping (push not configured yet)")
        return

    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM sms_push_subscriptions").fetchall()
    finally:
        conn.close()

    if not rows:
        return

    data = json.dumps(payload)
    stale_endpoints = []

    for row in rows:
        try:
            webpush(
                subscription_info={
                    "endpoint": row["endpoint"],
                    "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
                },
                data=data,
                vapid_private_key=private_key,
                vapid_claims={"sub": _VAPID_SUBJECT()},
            )
        except WebPushException as exc:
            if exc.status_code in (404, 410):
                stale_endpoints.append(row["endpoint"])
            else:
                log.warning("send_push_to_all: push failed for endpoint=%s: %s", row["endpoint"], exc)
        except Exception as exc:  # noqa: BLE001 — a subscription must never take down the caller
            log.warning("send_push_to_all: unexpected error for endpoint=%s: %s", row["endpoint"], exc)

    if stale_endpoints:
        conn = get_connection()
        try:
            conn.executemany(
                "DELETE FROM sms_push_subscriptions WHERE endpoint = ?",
                [(e,) for e in stale_endpoints],
            )
            conn.commit()
        finally:
            conn.close()
