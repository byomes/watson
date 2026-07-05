"""jobs/thesis_tracker/token_health.py — check whether the Digital Commons dashboard
auth token (DC_DASHBOARD_LINK) is still alive. No scraping — just confirms the link
still resolves to the dashboard instead of a login/expired-token page.

Cron: 0 8 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/thesis_tracker/token_health.py
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / "watson" / ".env")

from jobs.thesis_tracker import bootstrap_db, get_db, send_telegram

DASHBOARD_LINK = os.getenv("DC_DASHBOARD_LINK")

LOGIN_MARKERS = (
    "login", "log in", "sign in", "signin", "session has expired",
    "unauthorized", "authentication failed", "expired",
)


def check() -> dict:
    if not DASHBOARD_LINK:
        return {
            "success": 0,
            "status_code": None,
            "final_url": None,
            "note": "DC_DASHBOARD_LINK missing from .env",
        }

    try:
        resp = requests.get(DASHBOARD_LINK, timeout=20, allow_redirects=True)
    except requests.RequestException as exc:
        return {
            "success": 0,
            "status_code": None,
            "final_url": None,
            "note": f"request failed: {exc}",
        }

    final_url = resp.url
    status_code = resp.status_code
    haystack = f"{final_url}\n{resp.text[:5000]}".lower()
    hit = next((m for m in LOGIN_MARKERS if m in haystack), None)

    if status_code == 200 and hit is None:
        return {
            "success": 1,
            "status_code": status_code,
            "final_url": final_url,
            "note": "status 200, no login/expired markers found in final URL or page content",
        }

    if hit:
        note = f"status {status_code}, matched login/expired marker '{hit}' in final URL or page content"
    else:
        note = f"unexpected status {status_code}"

    return {
        "success": 0,
        "status_code": status_code,
        "final_url": final_url,
        "note": note,
    }


def main() -> dict:
    bootstrap_db()
    result = check()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO thesis_token_health (checked_at, success, status_code, final_url, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                result["success"],
                result["status_code"],
                result["final_url"],
                result["note"],
            ),
        )

    if not result["success"]:
        send_telegram(
            "⚠️ Thesis Tracker — Digital Commons dashboard token appears dead.\n\n"
            f"{result['note']}\n"
            f"Final URL: {result['final_url']}"
        )

    return result


if __name__ == "__main__":
    print(main())
