"""jobs/dev/historical_activity_backfill.py -- modeled pre-sampler savings estimate.

jobs/dev/resource_sampler.py has only measured real CPU/RAM/disk since
2026-09-05. There is no OS-level historical monitoring either (sysstat
was installed but never enabled before that date) -- so there is no way
to *measure* Beelink usage before the sampler started.

What does survive further back is job activity: `logs/*.log*` (current +
rotated, back to ~2026-05-16 for the oldest surviving file) carries
timestamped lines from every cron job run, and the repo's first commit
is 2026-05-07. That's real evidence of *what ran and when* -- not of
CPU/RAM at that moment.

This module turns that into a MODELED (not measured) daily $ estimate:
  1. Count timestamped log lines per calendar day across every log file
     (current and rotated/.gz), as a rough proxy for "how much job
     activity happened that day".
  2. Calibrate a $-per-log-line rate against the real, measured
     estimated_vps_daily_usd figures from vps_cost_estimate.py for the
     days where both exist (the sampler's window).
  3. Apply that rate to every day strictly before the sampler's window
     to produce a modeled estimated_usd.

This is intentionally a rough proxy, calibrated off a handful of
measured days -- log line volume doesn't actually track CPU/RAM the way
resource_samples does. Every day this produces is tagged
source="modeled" everywhere it's surfaced; it must never be presented
as measured. Re-run this script whenever you want the backfill cache
refreshed (e.g. after the sampler has accumulated more measured days to
calibrate against) -- it is not on a cron, and vps_cost_estimate.py only
reads its cached output.

Output cached to data/dev/historical_activity_backfill.json.
"""
import gzip
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

WATSON_DIR = Path(__file__).resolve().parents[2]
LOGS_DIR = WATSON_DIR / "logs"
BACKFILL_CACHE_PATH = WATSON_DIR / "data" / "dev" / "historical_activity_backfill.json"

# Matches "2026-05-16 22:22:04,342 INFO ...", "2026-09-06 04:16:02 [x] INFO ...",
# and "[2026-06-26 22:35:48] === ..." -- the leading "[" is optional.
_TIMESTAMP_RE = re.compile(r"^\[?(\d{4}-\d{2}-\d{2})[ T]\d{2}:\d{2}:\d{2}")


def _iter_log_files():
    if not LOGS_DIR.exists():
        return
    for path in LOGS_DIR.rglob("*.log*"):
        if path.is_file():
            yield path


def _count_lines_per_day(path: Path, counts: dict) -> None:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = _TIMESTAMP_RE.match(line)
                if m:
                    counts[m.group(1)] += 1
    except OSError as exc:
        log.warning("could not read %s: %s", path, exc)


def _count_activity_by_day() -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for path in _iter_log_files():
        _count_lines_per_day(path, counts)
    return dict(counts)


def build_backfill() -> dict:
    from jobs.dev.vps_cost_estimate import build_estimate

    activity = _count_activity_by_day()
    if not activity:
        return {"available": False, "reason": "No timestamped log lines found under logs/."}

    measured = build_estimate()
    measured_days = {
        d["day"]: d["estimated_vps_daily_usd"]
        for d in (measured.get("daily") or [])
        if d.get("estimated_vps_daily_usd") is not None
    }
    if not measured_days:
        return {"available": False, "reason": "No measured days yet to calibrate against -- run this again once resource_sampler.py has data."}

    earliest_measured_day = min(measured_days)

    calib_usd = 0.0
    calib_lines = 0
    for day, usd in measured_days.items():
        lines = activity.get(day, 0)
        if lines:
            calib_usd += usd
            calib_lines += lines
    if calib_lines == 0:
        return {"available": False, "reason": "No overlapping activity+measured days to calibrate a rate from."}

    rate_usd_per_line = calib_usd / calib_lines

    historical_days = {}
    for day, lines in sorted(activity.items()):
        if day >= earliest_measured_day:
            continue  # already real/measured, not this module's job
        historical_days[day] = {
            "activity_count": lines,
            "estimated_usd": round(lines * rate_usd_per_line, 2),
        }

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calibration": {
            "rate_usd_per_line": rate_usd_per_line,
            "calibration_days": len([d for d in measured_days if activity.get(d)]),
            "calibration_usd": round(calib_usd, 2),
            "calibration_lines": calib_lines,
        },
        "earliest_measured_day": earliest_measured_day,
        "days": historical_days,
    }


def save_backfill() -> dict:
    result = build_backfill()
    BACKFILL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKFILL_CACHE_PATH, "w") as f:
        json.dump(result, f, indent=2)
    return result


def load_backfill() -> dict:
    try:
        with open(BACKFILL_CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"available": False, "reason": "Backfill cache not generated yet -- run jobs/dev/historical_activity_backfill.py."}


if __name__ == "__main__":
    result = save_backfill()
    if result.get("available"):
        total = round(sum(d["estimated_usd"] for d in result["days"].values()), 2)
        print(f"Backfilled {len(result['days'])} days from {min(result['days']) if result['days'] else '?'} "
              f"to before {result['earliest_measured_day']}.")
        print(f"Calibration: ${result['calibration']['calibration_usd']} over "
              f"{result['calibration']['calibration_lines']} lines across "
              f"{result['calibration']['calibration_days']} measured days "
              f"-> ${result['calibration']['rate_usd_per_line']:.6f}/line")
        print(f"Modeled total (pre-sampler): ${total}")
    else:
        print("Backfill unavailable:", result.get("reason"))
