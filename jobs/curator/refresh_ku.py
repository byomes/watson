"""jobs/curator/refresh_ku.py — weekly Kindle Unlimited status refresh.

Cron: Sunday 5am. Re-checks the Amazon listing for every confirmed-KU book;
flips kindle_unlimited off silently (no Telegram alert — low stakes) if the
badge is gone. Never alerts, never guesses a rating.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.curator import amazon_url_for, get_db
from jobs.curator.research import fetch_amazon_ku_status, find_amazon_listing

log = logging.getLogger(__name__)


def run() -> dict:
    conn = get_db()
    checked = flipped = skipped = 0
    try:
        books = conn.execute(
            "SELECT id, title, author FROM books WHERE kindle_unlimited = 1 AND status != 'rejected'"
        ).fetchall()

        for book in books:
            url = amazon_url_for(conn, book["id"])
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

            # fetch_amazon_ku_status() (2026-07-23): routes through FlareSolverr and
            # checks Amazon's own "Kindle Unlimited Eligible" search filter for this
            # ASIN, rather than fetch_page_details()'s direct requests.get — that got
            # bot-blocked ~75% of the time, and even when it got through, its bare
            # "kindle unlimited" text search was a false positive on every real page
            # tested (the phrase is in Amazon's site nav regardless of enrollment).
            details = fetch_amazon_ku_status(url, book["title"], book["author"])
            if not details["fetched"]:
                skipped += 1
                continue

            checked += 1
            conn.execute(
                "UPDATE books SET kindle_unlimited_checked_at = datetime('now') WHERE id = ?",
                (book["id"],),
            )
            # Explicit `is False`, not `not details[...]` — fetch_amazon_ku_status()
            # returns None (not False) when it couldn't verify at all, same
            # three-state contract fetch_page_details() had. `not None` is True in
            # Python, so the old falsy check would have incorrectly flipped a book
            # off KU every time this job merely couldn't verify, instead of only
            # when it confirmed the book is genuinely no longer KU-eligible.
            if details["kindle_unlimited"] is False:
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
