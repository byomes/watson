"""jobs/browser/render_page.py — one-off, out-of-process page renderer.

Renders a single URL in a fresh headless Chromium (via browser_service) and
prints the page's visible body text to stdout. Exists so a caller can get a
Playwright render WITHOUT importing Chromium into its own long-lived process:
per browser_service.py's guardrail, browser jobs run as one-off subprocess
invocations, so a Chromium crash is contained to this short-lived child rather
than taking down watson-dashboard.service. First consumer is
jobs/curator/worker.py's _render_chatgpt_share_text (ChatGPT-research import).

robots.txt is still enforced (goto_safe), and no failure raises — a blocked or
failed navigation is reported via exit code, not a traceback the parent has to
parse.

Usage:
    python -m jobs.browser.render_page <url>

Exit codes:
    0  rendered OK — body text on stdout
    2  navigation blocked or failed (robots disallow, timeout, nav error)
    3  bad usage, or an unexpected error (message on stderr)
"""
import asyncio
import sys

from jobs.browser.browser_service import get_page, goto_safe

_TIMEOUT_MS = 30000


async def _render(url: str) -> str | None:
    async with get_page(timeout_ms=_TIMEOUT_MS) as page:
        if not await goto_safe(page, url, wait_until="networkidle"):
            return None
        return (await page.inner_text("body")).strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m jobs.browser.render_page <url>", file=sys.stderr)
        return 3
    try:
        text = asyncio.run(_render(argv[1]))
    except Exception as exc:
        print(f"render failed: {exc}", file=sys.stderr)
        return 3
    if text is None:
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
