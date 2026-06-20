#!/usr/bin/env python3
"""
sync.py — Sync Givebutter transactions → ~/watson/data/donors.db → Kit subscribers.

Cron: 0 6 * * * cd /home/billyomes/watson && \
  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
  -m jobs.givebutter.sync >> /home/billyomes/watson/logs/givebutter_sync.log 2>&1
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "donors.db"
LOG_PATH = BASE_DIR / "logs" / "givebutter_sync.log"

GB_API_KEY = os.getenv("GIVEBUTTER_API_KEY", "")
KIT_API_KEY = os.getenv("KIT_API_KEY", "")
KIT_API_SECRET = os.getenv("KIT_API_SECRET", "")
KIT_SENDER_EMAIL = os.getenv("KIT_SENDER_EMAIL", "")
KIT_SENDER_NAME = os.getenv("KIT_SENDER_NAME", "")

_TAG_CACHE: dict[str, int] = {}


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )


log = logging.getLogger(__name__)


# ── DB ────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS donors (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            givebutter_id        TEXT    UNIQUE,
            name                 TEXT,
            email                TEXT    UNIQUE,
            phone                TEXT,
            first_gift_date      TEXT,
            last_gift_date       TEXT,
            total_given          REAL    DEFAULT 0,
            gift_count           INTEGER DEFAULT 0,
            segment              TEXT,
            created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            donor_id                    INTEGER NOT NULL,
            givebutter_transaction_id   TEXT    UNIQUE,
            amount                      REAL,
            fund                        TEXT,
            campaign                    TEXT,
            given_at                    TEXT,
            thanked                     INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (donor_id) REFERENCES donors(id)
        );
    """)
    conn.commit()
    conn.close()
    log.info("DB ready: %s", DB_PATH)


# ── Givebutter ────────────────────────────────────────────────────────────────

def fetch_transactions() -> list[dict]:
    url: str | None = "https://api.givebutter.com/v1/transactions"
    headers = {"Authorization": f"Bearer {GB_API_KEY}", "Accept": "application/json"}
    params: dict = {"per_page": 100}
    all_txns: list[dict] = []

    while url:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        batch = data.get("data", [])
        all_txns.extend(batch)
        url = data.get("links", {}).get("next")
        params = {}
        log.info("Fetched %d transactions (running total: %d)", len(batch), len(all_txns))

    return all_txns


# ── DB upsert helpers ─────────────────────────────────────────────────────────

def _upsert_donor(conn: sqlite3.Connection, txn: dict) -> int:
    givebutter_id = str(txn.get("contact_id", ""))
    name = f"{txn.get('first_name', '')} {txn.get('last_name', '')}".strip()
    email = (txn.get("email") or "").lower().strip()
    phone = txn.get("phone", "") or ""

    row = conn.execute(
        "SELECT id FROM donors WHERE givebutter_id = ? OR (email != '' AND email = ?)",
        (givebutter_id, email),
    ).fetchone()

    if row:
        conn.execute(
            """UPDATE donors SET givebutter_id=?, name=?, phone=?, updated_at=datetime('now')
               WHERE id=?""",
            (givebutter_id, name, phone, row[0]),
        )
        return row[0]

    cursor = conn.execute(
        "INSERT INTO donors (givebutter_id, name, email, phone) VALUES (?, ?, ?, ?)",
        (givebutter_id, name, email, phone),
    )
    return cursor.lastrowid


def _upsert_transaction(conn: sqlite3.Connection, donor_id: int, txn: dict) -> None:
    txn_id = str(txn.get("id", ""))
    amount = float(txn.get("amount", 0) or 0)
    fund = txn.get("fund_code", "") or ""
    campaign = txn.get("campaign_title", "") or ""
    given_at = txn.get("transacted_at", "") or txn.get("created_at", "") or ""

    conn.execute(
        """INSERT OR IGNORE INTO transactions
           (donor_id, givebutter_transaction_id, amount, fund, campaign, given_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (donor_id, txn_id, amount, fund, campaign, given_at),
    )


def _recompute_aggregates(conn: sqlite3.Connection) -> None:
    conn.execute("""
        UPDATE donors SET
            first_gift_date = (
                SELECT MIN(t.given_at) FROM transactions t WHERE t.donor_id = donors.id
            ),
            last_gift_date = (
                SELECT MAX(t.given_at) FROM transactions t WHERE t.donor_id = donors.id
            ),
            total_given = COALESCE((
                SELECT SUM(t.amount) FROM transactions t WHERE t.donor_id = donors.id
            ), 0),
            gift_count = (
                SELECT COUNT(*) FROM transactions t WHERE t.donor_id = donors.id
            ),
            updated_at = datetime('now')
    """)


# ── Segment logic ─────────────────────────────────────────────────────────────

def _compute_segment(total_given: float, gift_count: int, last_gift_date: str | None) -> str:
    if gift_count == 1:
        return "first-time-donor"
    if total_given >= 500:
        return "major-donor"
    if last_gift_date:
        try:
            last = datetime.fromisoformat(last_gift_date.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - last).days > 90:
                return "lapsed-donor"
        except ValueError:
            pass
    return "recurring-donor"


def _update_segments(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, total_given, gift_count, last_gift_date FROM donors"
    ).fetchall()
    for donor_id, total_given, gift_count, last_gift_date in rows:
        segment = _compute_segment(total_given or 0, gift_count or 0, last_gift_date)
        conn.execute(
            "UPDATE donors SET segment=?, updated_at=datetime('now') WHERE id=?",
            (segment, donor_id),
        )


# ── Kit subscriber sync ───────────────────────────────────────────────────────

def _get_or_create_kit_tag(name: str) -> int:
    global _TAG_CACHE
    if not _TAG_CACHE:
        r = requests.get(
            "https://api.convertkit.com/v3/tags",
            params={"api_key": KIT_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        for t in r.json().get("tags", []):
            _TAG_CACHE[t["name"]] = t["id"]

    if name in _TAG_CACHE:
        return _TAG_CACHE[name]

    r = requests.post(
        "https://api.convertkit.com/v3/tags",
        json={"api_secret": KIT_API_SECRET, "tag": {"name": name}},
        timeout=10,
    )
    r.raise_for_status()
    tag = r.json()
    tag_id = tag.get("id") or (tag.get("tags") or [{}])[0].get("id")
    _TAG_CACHE[name] = tag_id
    return tag_id


def _kit_tag_subscriber(email: str, first_name: str, tag_id: int) -> None:
    r = requests.post(
        f"https://api.convertkit.com/v3/tags/{tag_id}/subscribe",
        json={"api_secret": KIT_API_SECRET, "first_name": first_name, "email": email},
        timeout=10,
    )
    if not r.ok:
        log.warning("Kit tag %d subscribe failed for %s: %s", tag_id, email, r.text[:200])


def sync_donor_to_kit(name: str, email: str, segment: str) -> None:
    first_name = name.split()[0] if name else ""
    for tag_name in ("donor", segment):
        try:
            tag_id = _get_or_create_kit_tag(tag_name)
            _kit_tag_subscriber(email, first_name, tag_id)
            log.info("Kit: tagged %s as '%s'", email, tag_name)
        except Exception as exc:
            log.warning("Kit tag '%s' failed for %s: %s", tag_name, email, exc)


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    init_db()

    log.info("Fetching Givebutter transactions…")
    try:
        txns = fetch_transactions()
    except Exception as exc:
        log.error("Givebutter fetch failed: %s", exc)
        return

    log.info("Processing %d transactions…", len(txns))

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    for txn in txns:
        if not txn.get("email"):
            log.debug("Skipping txn %s — no email.", txn.get("id"))
            continue
        try:
            donor_id = _upsert_donor(conn, txn)
            _upsert_transaction(conn, donor_id, txn)
        except Exception as exc:
            log.warning("Error processing txn %s: %s", txn.get("id"), exc)

    _recompute_aggregates(conn)
    _update_segments(conn)
    conn.commit()

    donors = conn.execute(
        "SELECT name, email, segment FROM donors WHERE email IS NOT NULL AND email != '' AND segment IS NOT NULL"
    ).fetchall()
    conn.close()

    log.info("Syncing %d donors to Kit…", len(donors))
    for name, email, segment in donors:
        try:
            sync_donor_to_kit(name, email, segment)
        except Exception as exc:
            log.warning("Kit sync failed for %s: %s", email, exc)

    log.info("Givebutter sync complete.")


if __name__ == "__main__":
    _setup_logging()
    run()
