"""jobs/telegram/leader_tool_usage.py -- usage log for Telegram-based tools
built for onboarded leaders (team_members / deacons), so Bill can see who's
actually using them and who isn't.

Scoped deliberately to leaders identified via people.telegram_chat_id -- the
same claim-code identity used everywhere else in Watson (see
jobs/telegram/seed_claim_codes.py). No IP-based tracking exists here or
anywhere else in Watson; usage is attributed to a real onboarded person or
not logged at all.

Currently the only Telegram-reachable tool for onboarded leaders is the
team/deacon chat Q&A (bot.py::_handle_team_chat, tool="team_chat") -- any
future leader-facing Telegram feature should call log_usage() with its own
tool name so it shows up in leader_tool_usage_report.py alongside it.
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/watson.db")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS leader_tool_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            used_at TEXT DEFAULT (datetime('now'))
        )"""
    )


def log_usage(conn: sqlite3.Connection, name: str, chat_id: str, tool: str) -> None:
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO leader_tool_usage (name, chat_id, tool) VALUES (?, ?, ?)",
        (name, chat_id, tool),
    )
    conn.commit()


def build_report() -> list[dict]:
    """Every onboarded (telegram_chat_id set) team member / deacon, with
    total uses and last-used timestamp. Leaders who've never used anything
    still show up, with 0 uses / last_used=None -- that's the point, to
    surface who isn't using what's been built for them."""
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
