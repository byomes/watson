"""jobs/trip/schema.py — DB schema for the Romantic 3-Day Trip Finder.

One tenant table: trip_proposals. Watson proposes candidate getaways here;
Bill approves or rejects via Telegram. No booking table — Watson never
books, so 'approved' is a terminal status, not a trigger for anything
downstream.
"""
from core.database import get_connection

CREATE_TRIP_PROPOSALS = """
CREATE TABLE IF NOT EXISTS trip_proposals (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    destination_city       TEXT NOT NULL,
    destination_airport    TEXT NOT NULL,
    origin_airport         TEXT NOT NULL,
    depart_date            TEXT NOT NULL,
    return_date            TEXT NOT NULL,
    flight_price           REAL,
    flight_currency        TEXT,
    hotel_name             TEXT,
    hotel_chain_code       TEXT,
    hotel_rating           REAL,
    hotel_price_per_night  REAL,
    hotel_currency         TEXT,
    blurb                  TEXT,
    status                 TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','approved','rejected')),
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at             TEXT
);
"""

ALL_TABLES = [CREATE_TRIP_PROPOSALS]


def create_tables(conn=None) -> None:
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        for stmt in ALL_TABLES:
            conn.execute(stmt)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    create_tables()
    print("trip_proposals ready.")
