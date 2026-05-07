import logging
import subprocess

from core.database import get_connection

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE = (
    "You are a research assistant. Summarize the following article in 2-3 sentences "
    "suitable for a daily research briefing. Be clear and informative — no fluff, "
    "no phrases like 'this article discusses'. Just the substance.\n\n"
    "Title: {title}\n"
    "URL: {url}\n\n"
    "{content}"
)


def summarize(item):
    title = item.get("title", "")
    url = item.get("url", "")
    content = item.get("content", "") or ""

    prompt = _PROMPT_TEMPLATE.format(
        title=title,
        url=url,
        content=content[:4000],  # keep prompt reasonable
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"exit code {result.returncode}")
        summary = result.stdout.strip()
        if summary:
            return summary
        raise RuntimeError("empty response")
    except Exception as e:
        log.warning("Summarizer fallback for '%s': %s", title, e)
        fallback = content.strip()[:200]
        return fallback if fallback else title


def summarize_items():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, url, summary FROM items WHERE status = 'new'"
        ).fetchall()

    if not rows:
        log.info("No new items to summarize")
        return 0

    updated = 0
    for row in rows:
        item_id = row["id"]
        title = row["title"]
        # Use existing summary as content if no raw body was stored
        content = row["summary"] or ""

        log.info("Summarizing: %s", title)
        summary = summarize({"title": title, "url": row["url"], "content": content})

        with get_connection() as conn:
            conn.execute(
                "UPDATE items SET summary = ? WHERE id = ?",
                (summary, item_id),
            )
        log.info("  done")
        updated += 1

    log.info("Summarized %d item(s)", updated)
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    count = summarize_items()
    print(f"\nDone. {count} item(s) summarized.")
