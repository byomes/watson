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
