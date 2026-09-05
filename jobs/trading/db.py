"""jobs/trading/db.py — Connection to trading.db, kept fully separate from
watson.db/congregation.db/donors.db/curator.db (see WATSON_ARCHITECTURE.md
Two-database-architecture convention — this is a third, trading-only DB)."""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TRADING_DB_PATH = BASE_DIR / "data" / "trading.db"


def get_connection() -> sqlite3.Connection:
    TRADING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(TRADING_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
