import logging
from datetime import datetime, timezone

import yaml

from config.settings import BASE_DIR
from core.database import _migrate, get_connection

log = logging.getLogger(__name__)

SOURCES_PATH = BASE_DIR / "config" / "sources.yaml"

_PRIORITY_BASE = {1: 100, 2: 60, 3: 30}
_TYPE_BONUS = {
    "podcast":     20,
    "publication": 15,
    "journal":     10,
    "article":      5,
}


def _load_priority_map():
    with open(SOURCES_PATH) as f:
        data = yaml.safe_load(f) or {}
    pmap = {}
    for category in ("authors", "organizations", "journals"):
        for entry in data.get(category, []) or []:
            pmap[entry["name"]] = int(entry.get("priority", 3))
    return pmap


def _recency_bonus(published_date_str):
    if not published_date_str:
        return 0
    try:
        dt = datetime.fromisoformat(published_date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        if age_days <= 1:
            return 50
        if age_days <= 7:
            return 25
        if age_days <= 30:
            return 10
        return 0
    except (ValueError, TypeError):
        return 0


def score_items():
    _migrate()
    priority_map = _load_priority_map()

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, source_name, source_type, published_date FROM items WHERE status = 'new'"
        ).fetchall()

        updates = []
        for row in rows:
            priority = priority_map.get(row["source_name"], 3)
            score = (
                _PRIORITY_BASE[priority]
                + _recency_bonus(row["published_date"])
                + _TYPE_BONUS.get(row["source_type"], 5)
            )
            updates.append((score, row["id"]))

        conn.executemany("UPDATE items SET score = ? WHERE id = ?", updates)

    log.info("Scored %d item(s)", len(updates))
    return len(updates)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    count = score_items()
    print(f"\nScored {count} item(s).")
