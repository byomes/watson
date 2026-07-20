"""jobs/curator/refresh_ku.py — weekly Kindle Unlimited status refresh.

Cron: Sunday 5am. Re-checks the Amazon listing for every confirmed-KU book;
flips kindle_unlimited off silently (no Telegram alert — low stakes) if the
badge is gone. Never alerts, never guesses a rating.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.curator import get_db
from jobs.curator.research import fetch_page_details, find_amazon_listing

log = logging.getLogger(__name__)


def _amazon_url_for(conn, book_id: int) -> str | None:
    row = conn.execute(
        "SELECT url FROM book_sources WHERE book_id = ? AND type = 'amazon' "
        "ORDER BY created_at DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    return row["url"] if row else None


def run() -> dict:
    conn = get_db()
    checked = flipped = skipped = 0
    try:
        books = conn.execute(
            "SELECT id, title, author FROM books WHERE kindle_unlimited = 1 AND status != 'rejected'"
        ).fetchall()

        for book in books:
            url = _amazon_url_for(conn, book["id"])
            if not url:
                url = find_amazon_listing(book["title"], book["author"])
                if url:
                    conn.execute(
                        "INSERT INTO book_sources (book_id, type, url) VALUES (?, 'amazon', ?)",
                        (book["id"], url),
                    )

            if not url:
                skipped += 1
                log.warning("refresh_ku: no Amazon URL for book %s (%s)", book["id"], book["title"])
                continue

            details = fetch_page_details(url)
            if not details["fetched"]:
                skipped += 1
                continue

            checked += 1
            conn.execute(
                "UPDATE books SET kindle_unlimited_checked_at = datetime('now') WHERE id = ?",
                (book["id"],),
            )
            if not details["kindle_unlimited"]:
                conn.execute("UPDATE books SET kindle_unlimited = 0 WHERE id = ?", (book["id"],))
                flipped += 1

        conn.commit()
    finally:
        conn.close()

    result = {"checked": checked, "flipped_off": flipped, "skipped": skipped}
    log.info("refresh_ku complete: %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
