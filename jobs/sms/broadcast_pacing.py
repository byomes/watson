"""jobs/sms/broadcast_pacing.py -- spreads a broadcast's individual sends
out over time instead of firing them in a tight loop.

A single Android phone sending the same text to dozens/hundreds of numbers
back-to-back is exactly the pattern carrier anti-spam filters key on: it's
the FAN-OUT VELOCITY (one number originating many new conversations in a
short window), more than exact-duplicate content, that reads as bulk
messaging rather than a person texting people. stagger_send_times() gives
each recipient their own randomized send time -- computed once, up front,
when the broadcast is confirmed (jobs/sms/api.py's create_broadcast) -- and
jobs/sms/broadcast_sender.py just sends whoever's own time has come on each
cron tick, the same way it would if Bill were individually texting people
throughout the day himself.

Two modes, chosen per broadcast (`spread_hours` in the /broadcasts POST
body):
  - spread_hours is None: the original tight mode -- a fixed random gap
    per recipient (SMS_BROADCAST_MIN/MAX_GAP_SECONDS, default 8-45s, ~26s
    average). Fine for a handful of people who need it out quickly.
  - spread_hours is a number: the recipient list is spread evenly (with
    jitter) across that many hours, e.g. spread_hours=10 for "across the
    whole day" -- the closer this gets to how Bill would actually text
    people one at a time over a day, the less any single-number fan-out
    signal stands out. Quiet hours (QUIET_HOURS_START/END, default
    9pm-8am America/New_York) are enforced regardless of mode: a computed
    time that would land in quiet hours is pushed to the next allowed
    window rather than firing overnight, which both avoids waking anyone
    and avoids the odd-hours pattern that's its own tell.

SMS_BROADCAST_TICK_DISPATCH_DELAY_MIN/MAX_SECONDS -- small extra real-time
sleep broadcast_sender.py adds between actual sends that land in the same
cron tick (default 2-5s), so a same-minute cluster still doesn't fire in a
single instant.
"""
import os
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/New_York")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def min_gap_seconds() -> float:
    return _env_float("SMS_BROADCAST_MIN_GAP_SECONDS", 8.0)


def max_gap_seconds() -> float:
    return _env_float("SMS_BROADCAST_MAX_GAP_SECONDS", 45.0)


def quiet_hours_start() -> int:
    """Local hour (0-23) quiet hours begin -- default 21 (9pm)."""
    return int(_env_float("SMS_BROADCAST_QUIET_HOURS_START", 21))


def quiet_hours_end() -> int:
    """Local hour (0-23) quiet hours end -- default 8 (8am)."""
    return int(_env_float("SMS_BROADCAST_QUIET_HOURS_END", 8))


def tick_dispatch_delay_range() -> tuple[float, float]:
    return (
        _env_float("SMS_BROADCAST_TICK_DISPATCH_DELAY_MIN_SECONDS", 2.0),
        _env_float("SMS_BROADCAST_TICK_DISPATCH_DELAY_MAX_SECONDS", 5.0),
    )


def _push_out_of_quiet_hours(t: datetime) -> datetime:
    """t is UTC. If its America/New_York local time falls in quiet hours,
    push forward to quiet_hours_end() local time (same day if t was before
    the window opened, next day if it was at/after the window closed)."""
    start, end = quiet_hours_start(), quiet_hours_end()
    local = t.astimezone(LOCAL_TZ)
    hour = local.hour

    in_quiet = (hour >= start) or (hour < end) if start > end else (start <= hour < end)
    if not in_quiet:
        return t

    day_offset = 0 if hour < end else (1 if start > end else 0)
    # start > end means quiet hours wrap midnight (e.g. 21 -> 8): a time at
    # or after `start` needs to roll to the *next* day's `end`; a time
    # before `end` (still within the overnight window, e.g. 3am) resolves
    # to today's `end`. If quiet hours don't wrap (unusual, but handle it),
    # any in-quiet hour just moves to today's `end`.
    target_date = (local + timedelta(days=day_offset)).date()
    pushed_local = datetime(target_date.year, target_date.month, target_date.day, end, 0, 0, tzinfo=LOCAL_TZ)
    return pushed_local.astimezone(timezone.utc)


def stagger_send_times(base_send_at: str, count: int, spread_hours: float | None = None) -> list[str]:
    """Returns `count` UTC 'YYYY-MM-DD HH:MM:SS' timestamps, the first
    equal to base_send_at (pushed later if it lands in quiet hours), each
    following one after a randomized gap. Order here should already be a
    shuffled recipient order (see create_broadcast) -- this only handles
    timing, not who goes first.

    spread_hours=None uses the tight fixed-range gap (min_gap_seconds..
    max_gap_seconds). A number targets spreading the whole list evenly
    across that many hours, with each gap jittered +/-35% around the
    even-split average (never below min_gap_seconds, so a huge spread
    request over a tiny list doesn't produce a silly multi-hour idle gap
    followed by nothing -- it's still bounded sensibly either direction)."""
    base = datetime.strptime(base_send_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    lo = min_gap_seconds()

    if spread_hours:
        avg_gap = (spread_hours * 3600) / max(count - 1, 1)
        gap_lo, gap_hi = max(avg_gap * 0.65, lo), avg_gap * 1.35
    else:
        gap_lo, gap_hi = lo, max_gap_seconds()

    times = []
    t = _push_out_of_quiet_hours(base)
    for i in range(count):
        if i > 0:
            t = _push_out_of_quiet_hours(t + timedelta(seconds=random.uniform(gap_lo, gap_hi)))
        times.append(t.strftime("%Y-%m-%d %H:%M:%S"))
    return times
