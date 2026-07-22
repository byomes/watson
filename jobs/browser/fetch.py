"""jobs/browser/fetch.py — generic fetch-and-render helper built on the
shared jobs.browser.browser_service.get_page() context manager.

Returns rendered text/HTML/screenshot for a single URL — no site-specific
logic lives here. Not wired into any consumer yet; Curator's fetch logic is
separate, later work.
"""
import logging

from jobs.browser.browser_service import DEFAULT_TIMEOUT_MS, get_page, goto_safe

log = logging.getLogger(__name__)


async def fetch_rendered_html(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
    """Returns the fully JS-rendered HTML of `url`, or None on any failure
    (robots disallow, timeout, navigation error — all logged to bug_tracker
    by goto_safe(), never raised here)."""
    async with get_page(timeout_ms=timeout_ms) as page:
        if not await goto_safe(page, url, wait_until="networkidle"):
            return None
        return await page.content()


async def fetch_rendered_text(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
    """Returns the rendered page's visible body text, or None on failure."""
    async with get_page(timeout_ms=timeout_ms) as page:
        if not await goto_safe(page, url, wait_until="networkidle"):
            return None
        return await page.inner_text("body")


async def fetch_screenshot(
    url: str,
    output_path: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    full_page: bool = True,
) -> str | None:
    """Renders `url` and saves a screenshot to `output_path`. Returns
    output_path on success, None on failure."""
    async with get_page(timeout_ms=timeout_ms) as page:
        if not await goto_safe(page, url, wait_until="networkidle"):
            return None
        await page.screenshot(path=output_path, full_page=full_page)
        return output_path
