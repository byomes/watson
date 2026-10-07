"""jobs/sms/heartbeat.py — logs gateway vitals and alerts Bill only when
something looks wrong (unreachable, battery critically low, or -- added
2026-09-28 alongside the group-text fix -- the phone unreachable via adb).
Cron'd every 5 minutes (added 2026-09-26 phone go-live).

Alerts are debounced via data/sms_gateway_alert_state.json, per-condition:
each condition (down/battery/adb_down) fires once on its own transition,
then at most once per REALERT_INTERVAL_SECONDS while it persists, and its
own "back online"/"restored" message fires on that condition's recovery.
Before this, every 5-minute cron tick sent its own Telegram message with no
backoff — a single overnight outage (2026-09-28, phone dropped off
Tailscale ~5am) produced 71 identical messages. The state file used to be a
single scalar {"alert_type", "last_alert_at"} -- couldn't represent "REST
gateway is fine but adb just died" independently of "REST gateway is down".
That matters more now that jobs/sms/adb_inbound.py (SMS_INBOUND_MODE=adb)
makes adb reachability part of the core inbound pipeline, not just the
best-effort Sabbath call-forwarding/digest features -- an adb outage after
this needs the same kind of alert a gateway outage already gets, not
silence.
"""
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.sms import adb_client, gateway_client

log = logging.getLogger(__name__)

LOW_BATTERY_PCT = 20
# Plug-in nudge goes out in the evening only (Bill plugs the phone in
# overnight; it is not kept on the charger 24/7 to spare the battery).
# Drain measured 2026-10-06 is ~1%/hour, so a 20% alert leaves ~20h of margin.
BATTERY_ALERT_START_HOUR = 19
BATTERY_ALERT_END_HOUR = 22
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


def _maybe_alert(condition: str, active: bool, text: str | None = None, recovery_text: str | None = None) -> None:
    """Per-condition debounce, keyed by `condition` (e.g. "down", "battery",
    "adb_down") -- independent conditions no longer clobber each other's
    state, unlike the old single-scalar version."""
    state = _read_state()
    now = datetime.now(timezone.utc)
    entry = state.get(condition)

    if not active:
        if entry is not None:
            if recovery_text:
                _send_telegram(recovery_text)
            del state[condition]
            _write_state(state)
        return

    if entry is not None:
        last_alert_at = datetime.fromisoformat(entry["last_alert_at"])
        if (now - last_alert_at).total_seconds() < REALERT_INTERVAL_SECONDS:
            return

    _send_telegram(text)
    state[condition] = {"last_alert_at": now.isoformat()}
    _write_state(state)


def check_heartbeat() -> dict:
    vitals = gateway_client.get_vitals()

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sms_gateway_heartbeat (ok, battery_pct, detail) VALUES (?, ?, ?)",
            (1 if vitals["ok"] else 0, vitals.get("battery_pct"), vitals.get("detail")),
        )

    _maybe_alert(
        "down", not vitals["ok"],
        text=f"Watson SMS: the gateway phone looks unreachable ({vitals.get('detail')}). "
             "Texts may not be getting through, worth checking on it.",
        recovery_text="Watson SMS: the gateway phone is back online.",
    )
    needs_plug = (
        vitals.get("battery_pct") is not None
        and vitals["battery_pct"] < LOW_BATTERY_PCT
        and not vitals.get("charging")
    )
    hour = datetime.now(ZoneInfo("America/New_York")).hour
    in_window = BATTERY_ALERT_START_HOUR <= hour < BATTERY_ALERT_END_HOUR
    # Outside the evening window a still-low battery just waits (no alert, no
    # false "recovered" message); it only clears when plugged in or charged.
    if in_window or not needs_plug:
        _maybe_alert(
            "battery", needs_plug,
            text=f"Watson SMS: the gateway phone's battery is at {vitals.get('battery_pct')}%. "
                 "Please plug it in tonight.",
            recovery_text="Watson SMS: the gateway phone is charging or back to a healthy level, thank you.",
        )

    adb_reachable = adb_client.connect_device() is not None
    _maybe_alert(
        "adb_down", not adb_reachable,
        text="Watson SMS: the gateway phone is unreachable over adb. New texts won't be "
             "grouped/ingested until this is fixed -- if it just rebooted, adb tcpip needs "
             "re-running over USB.",
        recovery_text="Watson SMS: adb reachability to the gateway phone is restored.",
    )

    return vitals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = check_heartbeat()
    print(f"check_heartbeat: {result}")
