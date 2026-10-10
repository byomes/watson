"""jobs/congregation/groups_reminder.py -- Telegram nudge to a small group's leaders to
record attendance on the tracker's Groups tab when nobody has recorded the session yet.
Two runs: Sunday 3:05pm (--include-today) covers the Sunday-morning groups the same
afternoon, alongside the other Sunday reminders; the daily 10am run covers sessions on
earlier days (midweek groups and events) the morning after.

For every group (Subsplash calendar groups and the weekly Sunday groups in
groups_web), look at sessions in the last few days before today. A session with no
attendance recorded (head count for Celebrate Recovery) sends each of its leaders
ONE message with the Groups tab link, once per session per leader. Sessions already
recorded, and leaders already reminded, are skipped, so a group whose leader has
checked people off never gets nagged.

Recipients are only the leaders in LEADERS below, only over Telegram, only if the
leader is onboarded (send_to_person refuses and logs for anyone without a chat id,
and never falls back to another chat). Leaders who are not yet connected are skipped
quietly and start receiving as soon as they are onboarded. No SMS from this job.
Text is a fixed template: no generated wording.

Usage:
  PYTHONPATH=/home/billyomes/watson venv/bin/python -m jobs.congregation.groups_reminder [--include-today] [--dry-run] [--today YYYY-MM-DD]

Cron (both inside Bill's 9am-8pm messaging hours; a leader is reminded once per session
no matter which run gets there first):
  5 15 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.congregation.groups_reminder --include-today >> /home/billyomes/watson/logs/groups_reminder.log 2>&1
  0 10 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.congregation.groups_reminder >> /home/billyomes/watson/logs/groups_reminder.log 2>&1

Leader list: notes/group_leaders.md (Bill, 2026-10-09). To add or change a leader, edit LEADERS.
"""
import argparse
import logging
import sqlite3
from datetime import date, timedelta

from jobs.congregation.groups_web import CONGREGATION_DB, _series_list, _session_dates
from jobs.telegram.send_to_person import send_to_person

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [groups_reminder] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

GROUPS_URL = "https://wtsn.me/cat/tracker?tab=groups"
_LOOKBACK_DAYS = 3   # a session older than this is no longer nagged about

# group title -> watson.db people.id of each leader
LEADERS: dict[str, tuple[int, ...]] = {
    "Celebrate Recovery": (81, 156),                    # Bill Williamson, Deb Noel
    "Men's Fraternity Bible Study": (210,),             # Gerry DiMatteo
    "Men's Fraternity Billiards Outing": (210, 254),    # Gerry DiMatteo, Jim Bouchat
    "Men's Breakfast": (210,),                          # Gerry DiMatteo
    "Remix Youth Group": (470,),                        # Pastor Tyler McCauley
    "Woven Ladies Small Group": (324, 329),             # Letha Palmer, Lisa Bouchat
    "The Names of God": (324,),                         # Letha Palmer
    "5th Sunday Potluck": (2,),                         # Melanie Yomes
    "Hayride and Bonfire": (241,),                      # Jen DiMatteo
    "Trunk or Treat": (7,),                             # Dr. Bill Yomes
    "Jim & Lisa's Group": (254, 329),                   # Jim and Lisa Bouchat
    "Remix Sunday Morning Group": (470,),               # Pastor Tyler McCauley
    "Shift Young Adult Group": (241, 470),              # Jen DiMatteo, Pastor Tyler McCauley
    "9am Elementary Kids Group": (332,),                # Lucie Hale
}


def _bootstrap(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_reminders_sent (
            series TEXT NOT NULL,
            event_date TEXT NOT NULL,
            person_id INTEGER NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (series, event_date, person_id)
        )""")


def _recorded(conn: sqlite3.Connection, series: str, counts_only: bool, event_date: str) -> bool:
    if counts_only:
        row = conn.execute("SELECT 1 FROM group_counts WHERE series=? AND event_date=? AND headcount IS NOT NULL",
                           (series, event_date)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM group_attendance WHERE series=? AND event_date=? LIMIT 1",
                           (series, event_date)).fetchone()
    return row is not None


def _fmt(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    return date(y, m, d).strftime("%A, %B %-d")


def due(today: date, include_today: bool = False) -> dict[int, list[tuple[str, str, str]]]:
    """person_id -> [(series, title, event_date)] sessions still unrecorded and not yet reminded."""
    lo, hi = (today - timedelta(days=_LOOKBACK_DAYS)).isoformat(), (today if include_today else today - timedelta(days=1)).isoformat()
    out: dict[int, list[tuple[str, str, str]]] = {}
    with sqlite3.connect(CONGREGATION_DB) as conn:
        _bootstrap(conn)
        for s in _series_list():
            leaders = LEADERS.get(s["title"])
            if not leaders:
                continue
            for d in _session_dates(s["series"]):
                if not lo <= d <= hi or _recorded(conn, s["series"], s["counts_only"], d):
                    continue
                for pid in leaders:
                    if conn.execute("SELECT 1 FROM group_reminders_sent WHERE series=? AND event_date=? AND person_id=?",
                                    (s["series"], d, pid)).fetchone():
                        continue
                    out.setdefault(pid, []).append((s["series"], s["title"], d))
    return out


def message(items: list[tuple[str, str, str]]) -> str:
    if len(items) == 1:
        _, title, d = items[0]
        return f"Please record attendance for {title} ({_fmt(d)}) on the tracker's Groups tab:\n{GROUPS_URL}"
    lines = "\n".join(f"- {title} ({_fmt(d)})" for _, title, d in sorted(items, key=lambda i: i[2]))
    return f"Please record attendance for these groups on the tracker's Groups tab:\n{lines}\n{GROUPS_URL}"


def _connected(person_id: int) -> bool:
    """True when this person is onboarded to Watson Telegram (has a chat id). Not-yet-connected leaders are skipped quietly."""
    from core.database import get_connection
    with get_connection() as conn:
        row = conn.execute("SELECT telegram_chat_id FROM people WHERE id=?", (person_id,)).fetchone()
    return bool(row and row["telegram_chat_id"])


def run(today: date, dry_run: bool, include_today: bool = False) -> int:
    sent = 0
    for pid, items in sorted(due(today, include_today).items()):
        text = message(items)
        if not _connected(pid):
            if dry_run:
                print(f"[dry-run] person {pid} not on Telegram yet, skipped: {[t for _, t, _ in items]}")
            continue
        if dry_run:
            print(f"[dry-run] person {pid}:\n{text}\n")
            continue
        if not send_to_person(pid, text):
            log.info("person_id=%s not reachable on Telegram (not onboarded or send failed); will retry next run", pid)
            continue
        with sqlite3.connect(CONGREGATION_DB) as conn:
            conn.executemany("INSERT OR IGNORE INTO group_reminders_sent (series, event_date, person_id) VALUES (?,?,?)",
                             [(s, d, pid) for s, _, d in items])
        sent += 1
        log.info("reminded person_id=%s about %d session(s)", pid, len(items))
    return sent


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print who would be reminded, send nothing")
    ap.add_argument("--include-today", action="store_true", help="also remind about sessions held today (Sunday afternoon run)")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD (for testing)")
    a = ap.parse_args()
    run(date.fromisoformat(a.today) if a.today else date.today(), a.dry_run, a.include_today)
