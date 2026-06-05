# Run manually or as systemd service — not a cron job

import logging
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.email_intake import WHITELIST, _extract_address
from jobs.email_job.gmail import get_unread, mark_as_read

import requests

log = logging.getLogger(__name__)


def _telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set — skipping: %s", text[:80])
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.error("Telegram message failed: %s", exc)


def _get_pending_job():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id, spec FROM code_agent_jobs WHERE status='awaiting_confirm' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def _update_job(job_id, status, confirmed_at=None, result=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE code_agent_jobs SET status=?, confirmed_at=?, result=? WHERE id=?",
        (status, confirmed_at, result, job_id),
    )
    conn.commit()
    conn.close()


def _expire_stale_jobs():
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id FROM code_agent_jobs WHERE status='awaiting_confirm' AND created_at < ?",
        (cutoff,),
    ).fetchall()
    for (job_id,) in rows:
        log.warning("Job %d expired after 24 hours without confirmation", job_id)
        conn.execute(
            "UPDATE code_agent_jobs SET status='expired' WHERE id=?",
            (job_id,),
        )
    conn.commit()
    conn.close()


def _process_confirm(msg_id, job_id, spec):
    mark_as_read(msg_id)
    _telegram("🔨 Coding — Claude Code is building")

    proc = subprocess.Popen(
        ["claude", "--dangerously-skip-permissions"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/home/billyomes/watson",
    )

    try:
        stdout, stderr = proc.communicate(input=spec.encode(), timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        _update_job(job_id, "failed", result="Timeout after 300 seconds")
        _telegram("❌ Build failed — timed out after 300 seconds")
        return

    if proc.returncode == 0:
        _update_job(job_id, "done", confirmed_at=datetime.utcnow().isoformat())
        _telegram("✅ Done — committed, ready to pull")
    else:
        err_snippet = stderr.decode(errors="replace")[:200]
        _update_job(job_id, "failed", result=err_snippet)
        _telegram("❌ Build failed — " + err_snippet)


def poll_confirms():
    _expire_stale_jobs()

    emails = get_unread(label="WATSON_DIRECTIVE")
    for email in emails:
        msg_id  = email["id"]
        sender  = _extract_address(email["sender"])
        body    = email["body"]

        if sender not in WHITELIST:
            continue

        if "confirm" not in body.lower():
            continue

        job = _get_pending_job()
        if job is None:
            log.warning("CONFIRM received but no awaiting_confirm job found — skipping")
            mark_as_read(msg_id)
            continue

        job_id, spec = job
        _process_confirm(msg_id, job_id, spec)
        return


def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("Code Agent confirm poller started")
    while True:
        try:
            poll_confirms()
        except Exception as exc:
            log.error("poll_confirms error: %s", exc)
        time.sleep(60)


if __name__ == "__main__":
    run()
