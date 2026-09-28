"""jobs/sms/heartbeat.py — logs gateway vitals and alerts Bill only when
something looks wrong (unreachable, or battery critically low). Cron'd every
5 minutes (added 2026-09-26 phone go-live).

Alerts are debounced via data/sms_gateway_alert_state.json: a condition
(down/low-battery) fires once on transition, then at most once per
REALERT_INTERVAL_SECONDS while it persists, and a "back online" message
fires on recovery. Before this, every 5-minute cron tick sent its own
Telegram message with no backoff — a single overnight outage (2026-09-28,
phone dropped off Tailscale ~5am) produced 71 identical messages.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.sms import gateway_client

log = logging.getLogger(__name__)

LOW_BATTERY_PCT = 15
REALERT_INTERVAL_SECONDS = 3600

_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "sms_gateway_alert_state.json"


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.error("heartbeat: telegram send failed: %s", exc)


def _read_state() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state))


def _maybe_alert(alert_type: str | None, text: str | None) -> None:
    state = _read_state()
    now = datetime.now(timezone.utc)
    prev_type = state.get("alert_type")

    if alert_type is None:
        if prev_type is not None:
            _send_telegram("Watson SMS: the gateway phone is back online.\n\n - Watson")
        _write_state({})
        return

    if prev_type == alert_type:
        last_alert_at = datetime.fromisoformat(state["last_alert_at"])
        if (now - last_alert_at).total_seconds() < REALERT_INTERVAL_SECONDS:
            return

    _send_telegram(text)
    _write_state({"alert_type": alert_type, "last_alert_at": now.isoformat()})


def check_heartbeat() -> dict:
    vitals = gateway_client.get_vitals()

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sms_gateway_heartbeat (ok, battery_pct, detail) VALUES (?, ?, ?)",
            (1 if vitals["ok"] else 0, vitals.get("battery_pct"), vitals.get("detail")),
        )

    if not vitals["ok"]:
        _maybe_alert(
            "down",
            f"Watson SMS: the gateway phone looks unreachable ({vitals.get('detail')}). "
            "Texts may not be getting through, worth checking on it.\n\n - Watson",
        )
    elif vitals.get("battery_pct") is not None and vitals["battery_pct"] < LOW_BATTERY_PCT:
        _maybe_alert(
            "battery",
            f"Watson SMS: the gateway phone's battery is at {vitals['battery_pct']}%. "
            "Might want to plug it in.\n\n - Watson",
        )
    else:
        _maybe_alert(None, None)

    return vitals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = check_heartbeat()
    print(f"check_heartbeat: {result}")
