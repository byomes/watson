"""leader_tool_usage_report.py -- print every onboarded (telegram_chat_id
set) team member / deacon, with their total uses and last-used timestamp
for Telegram-based leader tools (currently just team_chat). Leaders who've
never used anything still show up, with 0 uses / "never" -- that's the
point, to see who isn't using what Bill built for them.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 jobs/telegram/leader_tool_usage_report.py
"""
import os
import sqlite3

from jobs.telegram.leader_tool_usage import ensure_schema

DB_PATH = os.path.expanduser("~/watson/data/watson.db")


def build_report() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)

        team_members = {
            r["name"] for r in conn.execute(
                "SELECT tm.name FROM team_members tm "
                "JOIN people p ON p.name = tm.name COLLATE NOCASE "
                "WHERE p.telegram_chat_id IS NOT NULL AND tm.active = 1"
            )
        }

        from jobs.congregation.deacon_reports import list_deacons
        deacon_names = set(list_deacons())
        onboarded_deacons = {
            r["name"] for r in conn.execute(
                "SELECT name FROM people WHERE telegram_chat_id IS NOT NULL"
            ) if r["name"] in deacon_names
        }

        names = sorted(team_members | onboarded_deacons)

        report = []
        for name in names:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, MAX(used_at) AS last_used "
                "FROM leader_tool_usage WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            report.append({"name": name, "uses": row["cnt"], "last_used": row["last_used"]})

        report.sort(key=lambda r: r["last_used"] or "", reverse=True)
        return report
    finally:
        conn.close()


if __name__ == "__main__":
    for r in build_report():
        print(f"{r['name']}: {r['uses']} uses, last used {r['last_used'] or 'never'}")
