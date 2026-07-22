"""jobs/browser/intercept.py — XHR/JSON response interception helper.

Built for the Thesis Tracker's planned Playwright scraper (diff new
countries/institutions against the dashboard's own XHR/JSON calls, rather
than scraping rendered HTML). Not wired into any consumer yet — the actual
Thesis Tracker scraper is separate, later work.
"""
import logging

from jobs.browser.browser_service import DEFAULT_TIMEOUT_MS, get_page, goto_safe

log = logging.getLogger(__name__)


async def capture_json_responses(
    url: str,
    url_pattern: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_ms: int = 3000,
) -> list[dict]:
    """Navigates to `url` and captures every JSON response whose URL
    contains `url_pattern` (plain substring match) fired during page load —
    e.g. a dashboard's own XHR calls for country/institution data, rather
    than anything visible in the rendered DOM.

    `wait_ms` is how long to keep listening after the initial navigation
    settles, for XHR calls that fire on a delay/scroll/interaction rather
    than at load. Malformed/non-JSON responses matching the pattern are
    skipped and logged, not raised. Returns an empty list on total
    navigation failure (goto_safe already logs that to bug_tracker) — this
    function itself never raises."""
    captured: list[dict] = []

    async def _on_response(response):
        if url_pattern not in response.url:
            return
        try:
            captured.append(await response.json())
        except Exception as exc:
            log.warning(
                "Non-JSON response matching %r from %s: %s", url_pattern, response.url, exc
            )

    async with get_page(timeout_ms=timeout_ms) as page:
        page.on("response", _on_response)
        if not await goto_safe(page, url, wait_until="networkidle"):
            return []
        await page.wait_for_timeout(wait_ms)
        return captured
