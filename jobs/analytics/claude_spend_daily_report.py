"""jobs/analytics/claude_spend_daily_report.py -- daily Claude API spend
digest, trailing 24 hours, from core.claude_tier's claude_tier_spend_log.

Built 2026-09-04 per Bill's one-week request right after enabling the
$10/mo Claude API tier across intent/classifier.py, analytics/data_chat.py,
curator/research.py, analytics/monthly_web_engagement_report.py,
email_job/draft_email.py, plus the already-wired memory/reflect.py,
memory/wrap_up.py, connect_cards/state_of_church.py, email_reply/drafter.py
-- a short trial window to watch real spend before deciding whether a
standing daily report is worth keeping.

Deliberately temporary: only sends within [START_DATE, END_DATE] below (7
days). On the END_DATE run, after sending, it removes its own crontab entry
so nothing lingers past the requested week -- see _remove_own_cron_entry().
If Bill wants this to keep going, re-add the cron line (or ask Watson to)
with a new date range.

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.claude_spend_daily_report

Cron (temporary, 2026-09-05 through 2026-09-11, 7:25am):
  25 7 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    -m jobs.analytics.claude_spend_daily_report \
    >> /home/billyomes/watson/logs/claude_spend_daily_report.log 2>&1
"""
import logging
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from core.database import get_connection
from core.job_tracker import track_job
from core.vacation import vacation_gate
from jobs.telegram.send_to_person import send_to_person

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [claude_spend_daily_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
RECIPIENT_NAME = "Bill Yomes"
MONTHLY_BUDGET_USD = float(os.getenv("CLAUDE_MONTHLY_BUDGET_USD", "10.00"))

# One-week trial window -- see module docstring.
START_DATE = date(2026, 9, 5)
END_DATE = date(2026, 9, 11)

_CRON_SCRIPT_MARKER = "claude_spend_daily_report.py"
_CRON_COMMENT_MARKER = "Claude spend daily digest (temporary"


def _person_id(conn, name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def _fetch_last_24h(conn):
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute(
        """SELECT job_name, model, input_tokens, output_tokens, cost_usd
           FROM claude_tier_spend_log
           WHERE created_at >= ?
           ORDER BY cost_usd DESC""",
        (since,),
    ).fetchall()


def _fetch_month_to_date(conn) -> float:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM claude_tier_spend_log "
        "WHERE strftime('%Y-%m', created_at) = ?",
        (month,),
    ).fetchone()
    return float(row["total"])


def build_report() -> str:
    with get_connection() as conn:
        rows = _fetch_last_24h(conn)
        month_total = _fetch_month_to_date(conn)

    by_job: dict[str, dict] = {}
    for r in rows:
        job = by_job.setdefault(r["job_name"], {
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
            "models": set(),
        })
        job["calls"] += 1
        job["input_tokens"] += r["input_tokens"] or 0
        job["output_tokens"] += r["output_tokens"] or 0
        job["cost"] += r["cost_usd"] or 0.0
        job["models"].add(r["model"])

    total_cost = sum(j["cost"] for j in by_job.values())
    total_calls = sum(j["calls"] for j in by_job.values())

    lines = ["\U0001f4b8 Claude API spend -- last 24h", ""]

    if not by_job:
        lines.append("No Claude API calls in the last 24 hours (Ollama handled everything).")
    else:
        lines.append(f"${total_cost:.4f} across {total_calls} call(s):")
        for job_name, j in sorted(by_job.items(), key=lambda kv: -kv[1]["cost"]):
            models = ", ".join(sorted(j["models"]))
            lines.append(
                f"  - {job_name}: ${j['cost']:.4f} ({j['calls']} calls, "
                f"{j['input_tokens']}in/{j['output_tokens']}out, {models})"
            )

    lines.append("")
    lines.append(f"Month-to-date: ${month_total:.2f} / ${MONTHLY_BUDGET_USD:.2f} budget")

    if date.today() >= END_DATE:
        lines.append("")
        lines.append("(This was the last scheduled daily digest -- the one-week trial window has ended.)")

    return "\n".join(lines)


def _remove_own_cron_entry() -> None:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
        old_lines = result.stdout.splitlines()
        new_lines = [
            ln for ln in old_lines
            if _CRON_SCRIPT_MARKER not in ln and _CRON_COMMENT_MARKER not in ln
        ]
        if len(new_lines) != len(old_lines):
            subprocess.run(["crontab", "-"], input="\n".join(new_lines) + "\n", text=True, check=True)
            log.info("Removed claude_spend_daily_report.py's own crontab entry -- trial window complete.")
    except Exception as exc:
        log.warning("Failed to remove own crontab entry: %s", exc)


def send_daily_spend_report() -> bool:
    today = datetime.now(NY).date()
    if today < START_DATE or today > END_DATE:
        log.info("Outside trial window (%s to %s) -- skipping.", START_DATE, END_DATE)
        return False

    text = build_report()

    if vacation_gate("normal", "jobs.analytics.claude_spend_daily_report", text):
        log.info("Vacation mode is on -- daily spend report suppressed (logged).")
        sent = False
    else:
        with get_connection() as conn:
            person_id = _person_id(conn, RECIPIENT_NAME)

        if person_id is None:
            log.error("No people row found for %r -- skipped", RECIPIENT_NAME)
            sent = False
        else:
            sent = send_to_person(person_id, text)
            if sent:
                log.info("Sent daily Claude spend report to %s", RECIPIENT_NAME)
            else:
                log.warning("Failed to send daily Claude spend report to %s", RECIPIENT_NAME)

    if today >= END_DATE:
        _remove_own_cron_entry()

    return sent


def run() -> None:
    with track_job("analytics.claude_spend_daily_report"):
        send_daily_spend_report()


if __name__ == "__main__":
    run()
