#!/usr/bin/env python3
"""jobs/arc/commitment_validator.py — Flag suspicious bulk-submission patterns.

Runs every 5 minutes. Checks commitments submitted in the last 5 minutes.
If all 5 rows for a single reader were submitted within 30 seconds of each other,
marks all 5 as flagged_as_suspicious=1. Admin-dashboard-only review — no Telegram.

Cron:
*/5 * * * * PYTHONPATH=/home/billyomes/watson \
  /home/billyomes/watson/venv/bin/python -m jobs.arc.commitment_validator \
  >> /home/billyomes/watson/logs/arc_validator.log 2>&1
"""
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import get_db

log = logging.getLogger(__name__)


def run() -> None:
    cutoff = (datetime.utcnow() - timedelta(minutes=5)).isoformat()

    conn = get_db()
    try:
        # Find arc_reader_ids that have at least one commitment submitted in the last 5 min
        recent = conn.execute(
            "SELECT DISTINCT arc_reader_id FROM arc_reader_commitments "
            "WHERE submitted_at >= ? AND flagged_as_suspicious = 0",
            (cutoff,),
        ).fetchall()

        if not recent:
            log.info("No recent commitment submissions — nothing to check.")
            return

        flagged_count = 0
        for row in recent:
            reader_id = row["arc_reader_id"]

            all_commitments = conn.execute(
                "SELECT id, commitment_number, submitted_at FROM arc_reader_commitments "
                "WHERE arc_reader_id = ? AND submitted_at IS NOT NULL "
                "ORDER BY commitment_number",
                (reader_id,),
            ).fetchall()

            if len(all_commitments) < 5:
                continue

            timestamps = [c["submitted_at"] for c in all_commitments]
            try:
                parsed = [datetime.fromisoformat(t) for t in timestamps]
            except Exception:
                continue

            span = (max(parsed) - min(parsed)).total_seconds()
            if span <= 30:
                log.info(
                    "Reader %d: all 5 commitments submitted within %.1fs — flagging.",
                    reader_id, span,
                )
                conn.execute(
                    "UPDATE arc_reader_commitments SET flagged_as_suspicious = 1 "
                    "WHERE arc_reader_id = ?",
                    (reader_id,),
                )
                flagged_count += 1

        if flagged_count:
            conn.commit()
            log.info("Flagged %d reader(s) for suspicious bulk submission.", flagged_count)
        else:
            log.info("No suspicious patterns detected.")
    finally:
        conn.close()


if __name__ == "__main__":
    LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "arc_validator.log"
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )
    run()
