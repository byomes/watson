"""jobs/sms/sabbath_digest.py — Saturday 7am email of everything that came
in (texts + calls) during Friday's silenced Sabbath window, so Bill can
re-engage deliberately rather than digging through the phone himself.

Run via cron: 0 7 * * 6 (Saturday 7am, covering the just-elapsed Friday).

Voicemail transcripts are NOT included -- investigated 2026-09-26 and
found infeasible without root: the phone's own visual voicemail app
(com.motorola.visualvoicemail) stores everything in a private, unexported
content provider (VvmProvider), and the standard AOSP VoicemailContract
has no registered source on this device/carrier combination. See
project_sms_gateway_app.md for the full note -- a carrier-side
voicemail-to-email/text feature (via My Verizon account settings) is the
realistic path, not on-device extraction.
"""
import logging
import os
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from core.database import get_connection
from jobs.email_job.brevo_send import send_email
from jobs.sms.carrier_lookup import normalize_phone

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

log = logging.getLogger(__name__)

_TZ = ZoneInfo("America/New_York")
ADB = os.path.expanduser("~/platform-tools/adb")
_DEVICE_CANDIDATES = ["100.87.200.90:5555", "192.168.1.174:5555"]

_CALL_TYPE_LABELS = {1: "Incoming", 2: "Outgoing", 3: "Missed", 4: "Voicemail", 5: "Rejected", 6: "Blocked"}


def _last_friday_window() -> tuple[datetime, datetime]:
    """Returns (start, end) as UTC-naive datetimes bounding the most
    recently elapsed Friday, 12:00am-11:59:59pm America/New_York."""
    now_ny = datetime.now(_TZ)
    days_since_friday = (now_ny.weekday() - 4) % 7
    if days_since_friday == 0 and now_ny.hour < 7:
        # Shouldn't normally happen (cron fires at 7am Saturday), but if run
        # early on Friday itself, go back a full week rather than report a
        # not-yet-finished day.
        days_since_friday = 7
    friday = (now_ny - timedelta(days=days_since_friday)).date()
    start_ny = datetime(friday.year, friday.month, friday.day, 0, 0, 0, tzinfo=_TZ)
    end_ny = start_ny + timedelta(days=1)
    return start_ny.astimezone(ZoneInfo("UTC")).replace(tzinfo=None), end_ny.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _fetch_texts(start_utc: datetime, end_utc: datetime) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT m.body, m.created_at, t.contact_name, t.phone
               FROM sms_messages m JOIN sms_threads t ON t.id = m.thread_id
               WHERE m.direction = 'in' AND m.created_at >= ? AND m.created_at < ?
               ORDER BY m.created_at ASC""",
            (start_utc.strftime("%Y-%m-%d %H:%M:%S"), end_utc.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _thread_name_lookup() -> dict[str, str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT phone, contact_name FROM sms_threads WHERE contact_name IS NOT NULL").fetchall()
        return {r["phone"]: r["contact_name"] for r in rows}
    finally:
        conn.close()


def _connect_device() -> str | None:
    """`adb connect` can hang past its own timeout when the target host is
    up but nothing is listening on 5555 -- see jobs/sms/adb_client.py's
    connect_device() docstring for the uncaught-crash bug this duplicated
    (found 2026-09-29). Guarded the same way here.

    2026-09-30: also mirrors adb_client.py's fix of checking for an
    already-connected device (the phone's permanent USB link to this host)
    before trying the wireless candidates, which don't survive a phone
    reboot -- see that module's docstring."""
    try:
        existing = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        existing = None
    if existing is not None:
        for line in existing.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "device":
                return parts[0]

    for device in _DEVICE_CANDIDATES:
        try:
            subprocess.run([ADB, "connect", device], capture_output=True, text=True, timeout=15)
            check = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            continue
        if device in check.stdout and "device" in check.stdout.split(device, 1)[1].split("\n", 1)[0]:
            return device
    return None


def _fetch_calls(start_utc: datetime, end_utc: datetime) -> list[dict]:
    device = _connect_device()
    if not device:
        log.error("sabbath_digest: phone unreachable via adb, skipping call log")
        return []

    result = subprocess.run(
        [ADB, "-s", device, "shell", "content", "query", "--uri", "content://call_log/calls"],
        capture_output=True, text=True, timeout=20,
    )
    names = _thread_name_lookup()
    start_ms = start_utc.replace(tzinfo=ZoneInfo("UTC")).timestamp() * 1000
    end_ms = end_utc.replace(tzinfo=ZoneInfo("UTC")).timestamp() * 1000

    calls = []
    for line in result.stdout.splitlines():
        if not line.startswith("Row:"):
            continue
        fields = {}
        for part in line.split(", "):
            if "=" in part:
                k, _, v = part.partition("=")
                fields[k.strip()] = v.strip()
        try:
            date_ms = float(fields.get("date", "0"))
        except ValueError:
            continue
        if not (start_ms <= date_ms < end_ms):
            continue
        number = fields.get("number", "")
        digits = normalize_phone(number)
        formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if digits else number
        calls.append({
            "number": formatted,
            "name": names.get(digits, None) if digits else None,
            "type": _CALL_TYPE_LABELS.get(int(fields.get("type", "0")), "Unknown"),
            "duration_sec": int(fields.get("duration", "0")),
            "when_utc": datetime.fromtimestamp(date_ms / 1000, tz=ZoneInfo("UTC")),
        })
    calls.sort(key=lambda c: c["when_utc"])
    return calls


def _format_time(dt_ny: datetime) -> str:
    return dt_ny.strftime("%-I:%M %p")


def build_digest(friday_label: str, texts: list[dict], calls: list[dict]) -> tuple[str, str]:
    """Returns (text_body, html_body)."""
    lines = [f"Sabbath digest for Friday, {friday_label}", ""]
    if not texts and not calls:
        lines.append("Nothing came in during your Sabbath window. Quiet day.")
    else:
        if texts:
            lines.append(f"TEXTS ({len(texts)})")
            for t in texts:
                created = datetime.strptime(t["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("UTC")).astimezone(_TZ)
                # A group thread's phone is the synthetic "group:<id>" key
                # (jobs/sms/schema.py), never a real number to show Bill --
                # contact_name is NULL for those, so this guard keeps it
                # from leaking into the digest email.
                if not t["contact_name"] and str(t["phone"]).startswith("group:"):
                    who = "(group text)"
                else:
                    who = t["contact_name"] or t["phone"]
                lines.append(f"  {_format_time(created)} — {who}: {t['body']}")
            lines.append("")
        if calls:
            lines.append(f"CALLS ({len(calls)})")
            for c in calls:
                when_ny = c["when_utc"].astimezone(_TZ)
                who = c["name"] or c["number"]
                dur = f", {c['duration_sec']}s" if c["duration_sec"] else ""
                lines.append(f"  {_format_time(when_ny)} — {c['type']} — {who}{dur}")
            lines.append("")
    lines.append("Re-engage with these when you're ready.")
    text_body = "\n".join(lines)

    html_rows = "".join(f"<li>{line}</li>" for line in lines[2:] if line.strip())
    html_body = f"<p>{lines[0]}</p><ul>{html_rows}</ul>"
    return text_body, html_body


def run() -> None:
    start_utc, end_utc = _last_friday_window()
    friday_label = start_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(_TZ).strftime("%B %-d, %Y")

    texts = _fetch_texts(start_utc, end_utc)
    calls = _fetch_calls(start_utc, end_utc)
    text_body, html_body = build_digest(friday_label, texts, calls)

    result = send_email(
        to_email="pastorbill@catalyst302.com",
        to_name="Dr. Bill",
        subject=f"Sabbath digest — {friday_label}",
        text_body=text_body,
        html_body=html_body,
    )
    if not result["success"]:
        log.error("sabbath_digest: send failed: %s", result.get("error"))
    else:
        log.info("sabbath_digest: sent (%d texts, %d calls)", len(texts), len(calls))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
