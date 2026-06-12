"""
Watson daily pipeline: fetch → filter → score → store → build → publish.
"""
import logging
import sys
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _write_briefing_items(items: list[dict]):
    from core.database import get_connection
    with get_connection() as conn:
        for item in items:
            conn.execute(
                """
                INSERT OR IGNORE INTO briefing_items
                    (title, url, summary, source_name, source_type,
                     priority, score, published_at, date_unknown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["title"],
                    item["url"],
                    item.get("summary"),
                    item["source_name"],
                    item["source_type"],
                    item["priority"],
                    item.get("score", 0),
                    item.get("published_at"),
                    1 if item.get("date_unknown") else 0,
                ),
            )


def _archive_total() -> int:
    from core.database import get_connection
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM research_archive").fetchone()[0]


def _log_summary(fetch: dict, filter_stats: dict, score_stats: dict,
                 briefing_count: int, archived_total: int):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pool_size    = len(fetch["pool"])
    rejected_age = filter_stats["rejected_age"]
    rej_q        = (filter_stats["rejected_listicle"] +
                    filter_stats["rejected_clickbait"] +
                    filter_stats["rejected_thin"])
    passed       = pool_size - rejected_age - rej_q

    from config.settings import FRESHNESS_DAYS
    guaranteed_names = ", ".join(score_stats["guaranteed_names"]) or "none"

    sep = "=" * 66
    log.info(sep)
    log.info("  WATSON BRIEFING SUMMARY — %s", today)
    log.info(sep)
    log.info(
        "  Sources:   %d active | %d inactive (skipped)",
        fetch["sources_active"], fetch["sources_inactive"],
    )
    log.info(
        "  Fetched:   %d candidates | %d already archived (skipped)",
        pool_size, fetch["skipped_seen"],
    )
    log.info(
        "  Archived:  %d new items → research_archive (total: %d)",
        pool_size, archived_total,
    )
    log.info("")
    log.info("  Freshness filter (FRESHNESS_DAYS=%d):", FRESHNESS_DAYS)
    log.info("    Passed:          %d", pool_size - rejected_age)
    log.info("    Rejected (age):  %d", rejected_age)
    log.info("")
    log.info("  Quality filter:")
    log.info("    Passed:          %d", passed)
    log.info(
        "    Rejected:        %d  "
        "(listicle: %d | clickbait: %d | thin summary: %d)",
        rej_q,
        filter_stats["rejected_listicle"],
        filter_stats["rejected_clickbait"],
        filter_stats["rejected_thin"],
    )
    log.info(
        "    Rejected (unknown date): %d",
        filter_stats["rejected_unknown_date"],
    )
    log.info("")
    log.info("  Relevance gate:")
    log.info(
        "    Rejected (irrelevant): %d",
        score_stats["rejected_irrelevant"],
    )
    log.info("")
    log.info("  Scoring:")
    log.info("    Candidates:       %d", passed)
    log.info(
        "    Guaranteed slots: %d  (%s)",
        score_stats["guaranteed_slots"], guaranteed_names,
    )
    log.info(
        "    Top score: %d | Bottom score: %d",
        score_stats["top_score"], score_stats["bottom_score"],
    )
    log.info("    Briefing items:   %d", briefing_count)
    log.info(sep)


def run():
    import requests as _requests
    from core.database import init_db
    from core.fetcher import fetch_all
    from core.scorer import filter_pool, score_and_select
    from briefing.builder import build_briefing
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

    init_db()

    fetch        = fetch_all()
    pool         = fetch["pool"]
    log.info("Fetched %d candidate(s) across all sources", len(pool))

    passed, filter_stats = filter_pool(pool)
    log.info("Filter passed %d/%d items", len(passed), len(pool))

    try:
        from jobs.briefing.gemini_relevance import score_items
        passed = score_items(passed)
        log.info("Gemini relevance scoring complete")
    except Exception as exc:
        log.warning("Gemini relevance scoring skipped: %s", exc)

    top, score_stats = score_and_select(passed)
    log.info("Scorer selected %d item(s) for briefing", len(top))

    narrative = ""
    try:
        from jobs.briefing.gemini_narrative import generate_narrative
        narrative = generate_narrative(top)
        log.info("Gemini narrative generated")
    except Exception as exc:
        log.warning("Gemini narrative skipped: %s", exc)

    _write_briefing_items(top)

    archived_total = _archive_total()

    _log_summary(fetch, filter_stats, score_stats, len(top), archived_total)

    build_briefing(narrative=narrative)
    log.info("Static briefing built")

    try:
        _requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": "📋 Your briefing is ready at williamckyomes.com/dashboard"},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)

    return {
        "fetched":    len(pool),
        "filtered":   len(passed),
        "selected":   len(top),
        "archived":   archived_total,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    result = run()
    print(
        f"\nDone — fetched={result['fetched']} "
        f"filtered={result['filtered']} "
        f"selected={result['selected']} "
        f"archive_total={result['archived']}"
    )
    sys.exit(0)
