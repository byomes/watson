"""Add deacon_visible_prayer_requests and deacon_visible_connect_cards views.

Per Bill's 2026-09-08 decision to open data_chat.py's Q&A up to the rest of
congregation.db for deacons/leaders, EXCEPT anything a submitter explicitly
flagged as leadership-only (prayer_requests.leadership_only,
connect_cards.prayer_request_public) -- that's a different, member-set
privacy tier from Bill's own private pastoral_notes (a separate table in
watson.db, structurally unreachable from this domain already). These views
let data_chat's SQL-generation whitelist a name that already excludes the
flagged rows/column, rather than trusting every generated query to
remember the WHERE clause itself.
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

VIEWS = {
    "deacon_visible_prayer_requests": """
        CREATE VIEW IF NOT EXISTS deacon_visible_prayer_requests AS
        SELECT id, member_id, card_id, request_text, date, created_at
        FROM prayer_requests
        WHERE leadership_only = 0
    """,
    "deacon_visible_connect_cards": """
        CREATE VIEW IF NOT EXISTS deacon_visible_connect_cards AS
        SELECT
            id, member_id, service_date, campus, questions_comments,
            next_steps, is_first_visit, processed_at,
            CASE WHEN prayer_request_public = 1 THEN prayer_request ELSE NULL END AS prayer_request
        FROM connect_cards
    """,
}

if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    try:
        for name, ddl in VIEWS.items():
            conn.execute(ddl)
            print(f"  [ok] {name}")
        conn.commit()
        print("Done.")
    finally:
        conn.close()
