"""jobs/thesis_tracker/scrape.py — Pull a fresh snapshot from the Digital Commons
author dashboard and insert it into watson.db.

The dashboard link's session is only established via a real browser JS handshake
(a plain requests.Session() GET does not receive the sessionJwt cookie — confirmed
during discovery). Playwright loads the link headless once, then hits the JSON API
endpoints directly through the authenticated context.request — no DOM scraping.

Cron: 10 8 * * 6 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/thesis_tracker/scrape.py
"""
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from jobs.thesis_tracker import send_telegram
from jobs.thesis_tracker.db import (
    init_db,
    insert_snapshot,
    get_known_countries,
    get_known_institutions,
)

API_BASE = "https://dashboard.digital-commons.com/api"
PERIOD = "allTime"
PAGE_SIZE = 200


class ScrapeError(Exception):
    pass


def _fetch_json(context, final_url: str, path: str, extra_params: dict | None = None):
    params = {"dashboardType": "author", "period": PERIOD, "includeCollected": "true"}
    if extra_params:
        params.update(extra_params)
    resp = context.request.get(f"{API_BASE}{path}", params=params, headers={"Referer": final_url})
    if resp.status != 200:
        raise ScrapeError(f"{path} returned status {resp.status}")
    try:
        return resp.json()
    except Exception as exc:
        raise ScrapeError(f"{path} did not return valid JSON: {exc}")


def _pull(dashboard_link: str) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(dashboard_link, wait_until="networkidle", timeout=45000)
        except Exception as exc:
            browser.close()
            raise ScrapeError(f"could not load dashboard link: {exc}")

        final_url = page.url
        if "dashboard.digital-commons.com" not in final_url:
            browser.close()
            raise ScrapeError(
                f"link did not resolve to the Digital Commons dashboard (landed on {final_url}) — token likely expired"
            )

        total_downloads = _fetch_json(context, final_url, "/downloads/summary")
        total_views = _fetch_json(context, final_url, "/metadata/summary")
        total_countries = _fetch_json(context, final_url, "/countries/summary")
        countries = _fetch_json(context, final_url, "/countries", {"size": PAGE_SIZE})
        institutions = _fetch_json(context, final_url, "/institutions", {"size": PAGE_SIZE})
        referrers = _fetch_json(context, final_url, "/referrers", {"size": PAGE_SIZE})
        downloads = _fetch_json(context, final_url, "/downloads", {"size": PAGE_SIZE})

        browser.close()

    if not isinstance(total_downloads, int) or not isinstance(total_views, int) or not isinstance(total_countries, int):
        raise ScrapeError("summary endpoints returned unexpected (non-integer) data — dashboard shape may have changed")

    window_start = (countries.get("period") or {}).get("from")

    return {
        "final_url": final_url,
        "total_downloads": total_downloads,
        "total_views": total_views,
        "total_countries": total_countries,
        "countries": countries.get("results", []),
        "institutions": institutions.get("results", []),
        "referrers": referrers.get("results", []),
        "titles": downloads.get("results", []),
        "window_start": window_start,
        "raw": {
            "countries": countries,
            "institutions": institutions,
            "referrers": referrers,
            "downloads": downloads,
        },
    }


def _fmt_range(window_start: str | None, window_end: datetime) -> str:
    end_str = window_end.strftime("%b %d %Y")
    if not window_start:
        return end_str
    try:
        start_dt = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
    except ValueError:
        return end_str
    return f"{start_dt.strftime('%b %d')}–{end_str}"


def scrape(dashboard_link: str) -> dict:
    """Pull a fresh snapshot, insert it, and send a Telegram summary.

    Returns {success, downloads, views, countries, new_countries, new_institutions,
    snapshot_id} on success, or {success: False, error} on failure. Never raises.
    """
    init_db()
    known_countries_before = get_known_countries()
    known_institutions_before = get_known_institutions()

    try:
        result = _pull(dashboard_link)
    except Exception as exc:
        send_telegram(
            "⚠️ Thesis snapshot failed — link may have expired.\n"
            "Update DC_DASHBOARD_LINK in .env with a fresh link from the latest Liberty/Digital Commons email."
        )
        return {"success": False, "error": str(exc)}

    country_rows = [
        {"country": row.get("name"), "downloads": row.get("doc_count")}
        for row in result["countries"]
    ]
    institution_rows = [
        {"institution": row.get("isp"), "downloads": row.get("doc_count")}
        for row in result["institutions"]
    ]
    referrer_rows = [
        {"referrer": row.get("key"), "downloads": row.get("numHits")}
        for row in result["referrers"]
    ]
    title_rows = [
        {"title": row.get("title"), "downloads": row.get("num_hits")}
        for row in result["titles"]
    ]

    new_countries = sorted(
        {row["country"] for row in country_rows if row["country"]} - known_countries_before
    )
    new_institutions = sorted(
        {row["institution"] for row in institution_rows if row["institution"]} - known_institutions_before
    )

    now = datetime.now(timezone.utc)
    pulled_at = now.isoformat()

    try:
        snapshot_id = insert_snapshot(
            pulled_at=pulled_at,
            window_start=result["window_start"],
            window_end=pulled_at,
            total_downloads=result["total_downloads"],
            total_views=result["total_views"],
            total_countries=result["total_countries"],
            source_link=dashboard_link,
            raw_json=str(result["raw"]),
            titles=title_rows,
            countries=country_rows,
            institutions=institution_rows,
            referrers=referrer_rows,
            window_type="all_time",
        )
    except Exception as exc:
        send_telegram(
            "⚠️ Thesis snapshot failed — link may have expired.\n"
            "Update DC_DASHBOARD_LINK in .env with a fresh link from the latest Liberty/Digital Commons email."
        )
        return {"success": False, "error": f"DB insert failed: {exc}"}

    date_range = _fmt_range(result["window_start"], now)
    header = (
        f"📊 Thesis snapshot ({date_range})\n"
        f"{result['total_downloads']} downloads / {result['total_views']} views / {result['total_countries']} countries"
    )

    if new_countries or new_institutions:
        lines = [header, ""]
        if new_countries:
            lines.append(f"🌍 New countries: {', '.join(new_countries)}")
        if new_institutions:
            lines.append(f"🏫 New institutions: {', '.join(new_institutions)}")
        send_telegram("\n".join(lines))
    else:
        send_telegram(f"{header}\nNo new countries or institutions this pull.")

    return {
        "success": True,
        "downloads": result["total_downloads"],
        "views": result["total_views"],
        "countries": result["total_countries"],
        "new_countries": new_countries,
        "new_institutions": new_institutions,
        "snapshot_id": snapshot_id,
    }


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from dotenv import load_dotenv

    load_dotenv(Path.home() / "watson" / ".env")
    link = os.getenv("DC_DASHBOARD_LINK")
    print(scrape(link))
