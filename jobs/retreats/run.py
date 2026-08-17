"""jobs/retreats/run.py — orchestrates discover -> extract -> fit -> push.

Entry point for both cron (`python -m jobs.retreats.run`) and the Telegram
"find more retreats" trigger (`bot.py` imports `run()` directly and runs it in
a background thread).
"""
import logging
import sys

from jobs.retreats import SOURCES, send_telegram
from jobs.retreats.discover import discover_candidates, load_seen, save_seen
from jobs.retreats.extract import fetch_page_text, extract_listing
from jobs.retreats.fit import compute_fit, distance_and_drive_time, within_range
from jobs.retreats.push import push_listings

log = logging.getLogger(__name__)

MAX_NEW_PER_SOURCE = 10


def run(source_names: list[str] | None = None, notify: bool = True) -> dict:
    sources = [s for s in SOURCES if not source_names or s["name"] in source_names]
    seen = load_seen()

    listings = []
    stats = {"candidates_checked": 0, "not_confident": 0, "out_of_range": 0, "sources": {}}

    for source in sources:
        candidates = discover_candidates(source, seen)[:MAX_NEW_PER_SOURCE]
        source_found = 0
        log.info("%s: %d new candidate page(s)", source["name"], len(candidates))

        for url in candidates:
            stats["candidates_checked"] += 1
            seen.add(url)  # mark seen regardless of outcome — don't re-try a bad page every run

            title, text = fetch_page_text(url)
            extracted = extract_listing(url, title, text)
            if not extracted:
                stats["not_confident"] += 1
                continue

            miles, drive_time, duration_s = distance_and_drive_time(extracted.get("location") or "")
            if not within_range(duration_s):
                stats["out_of_range"] += 1
                continue

            fit_rating, fit_label = compute_fit(extracted.get("kitchen_status"), extracted.get("capacity"))

            listings.append({
                "name": extracted["name"],
                "location": extracted.get("location"),
                "distance_miles": miles,
                "drive_time": drive_time,
                "price": extracted.get("price"),
                "capacity": extracted.get("capacity"),
                "beds": extracted.get("beds"),
                "baths": extracted.get("baths"),
                "amenities": extracted.get("amenities") or [],
                "kitchen_status": extracted.get("kitchen_status"),
                "kitchen_detail": extracted.get("kitchen_detail"),
                "fit_rating": fit_rating,
                "fit_label": fit_label,
                "phone": extracted.get("phone"),
                "website": extracted.get("website"),
                "email": extracted.get("email"),
                "source_url": url,
                "free_or_paid": extracted.get("free_or_paid"),
            })
            source_found += 1

        stats["sources"][source["name"]] = source_found

    save_seen(seen)

    push_result = push_listings(listings)
    inserted = push_result.get("inserted", 0)
    skipped = push_result.get("skipped_duplicates", 0)

    summary = {
        "checked": stats["candidates_checked"],
        "not_confident": stats["not_confident"],
        "out_of_range": stats["out_of_range"],
        "found": len(listings),
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "good_fit": sum(1 for l in listings if l["fit_rating"] == "good"),
        "error": push_result.get("error"),
    }

    if notify:
        if summary["error"]:
            send_telegram(f"Retreat search hit an error pushing results: {summary['error']}")
        elif inserted == 0:
            send_telegram(
                f"Retreat search: checked {stats['candidates_checked']} page(s), nothing new to add."
            )
        else:
            send_telegram(
                f"Retreat search: found {inserted} new candidate(s) "
                f"({summary['good_fit']} good fit), {skipped} already known. "
                f"Checked {stats['candidates_checked']} page(s) across {len(sources)} source(s)."
            )

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    result = run()
    print(f"\nDone — {result}")
    sys.exit(0)
