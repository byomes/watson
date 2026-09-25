"""jobs/sms/heartbeat.py — logs gateway vitals and alerts Bill only when
something looks wrong (unreachable, or battery critically low). Not cron'd
yet (no phone hardware as of 2026-09-25) — run directly
(`python -m jobs.sms.heartbeat`) once GATEWAY_MODE=live and the phone is on
Tailscale, then add to crontab like every other scheduled job.
"""
import logging

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.sms import gateway_client

log = logging.getLogger(__name__)

LOW_BATTERY_PCT = 15


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


def check_heartbeat() -> dict:
    vitals = gateway_client.get_vitals()

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sms_gateway_heartbeat (ok, battery_pct, detail) VALUES (?, ?, ?)",
            (1 if vitals["ok"] else 0, vitals.get("battery_pct"), vitals.get("detail")),
        )

    if not vitals["ok"]:
        _send_telegram(
            f"Watson SMS: the gateway phone looks unreachable ({vitals.get('detail')}). "
            "Texts may not be getting through — worth checking on it.\n\n - Watson"
        )
    elif vitals.get("battery_pct") is not None and vitals["battery_pct"] < LOW_BATTERY_PCT:
        _send_telegram(
            f"Watson SMS: the gateway phone's battery is at {vitals['battery_pct']}%. "
            "Might want to plug it in.\n\n - Watson"
        )

    return vitals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = check_heartbeat()
    print(f"check_heartbeat: {result}")
