"""
Watson daily pipeline: fetch → build → publish.
"""
import logging
import sys

log = logging.getLogger(__name__)


def run():
    from core.database import init_db
    from core.fetcher import fetch_all
    from briefing.builder import build_briefing
    from briefing.publisher import publish

    init_db()

    count = fetch_all()
    log.info("Fetched %d new item(s)", count)

    build_briefing()
    log.info("Static briefing built")

    published = publish()
    log.info("Published: %s", published)

    return {"fetched": count, "published": published}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    result = run()
    print(f"\nDone — fetched={result['fetched']} published={result['published']}")
    sys.exit(0)
