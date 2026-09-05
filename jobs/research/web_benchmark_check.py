"""
jobs/research/web_benchmark_check.py — Weekly scan for new digital/church-
communications benchmark research (Barna Digital, church social media
engagement, nonprofit email open rates, church website analytics).

Mirrors jobs/research/benchmark_check.py exactly in structure, scoped to a
different domain — digital engagement, not attendance. Runs 4 Serper.dev
searches restricted to the last 14 days, skips anything already logged in
web_benchmark_sources (a separate table from benchmark_sources — zero blast
radius on the attendance benchmark flow already in production), and
cheap-filters the rest down to results that reference an actual engagement
metric with real data behind it (not generic "grow your church's social
media" tips content). New relevant candidates get inserted into
web_benchmark_sources (status='pending'), summarized via local Ollama
(llama3.2:3b), and sent to Bill on Telegram with "Update doc" / "Ignore"
buttons — same self-contained callback_data pattern as benchmark_check.py,
but with distinct callback prefixes (webbench_update: / webbench_ignore:)
so it can't collide with handle_benchmark_callback.

The actual doc update / status flip on button tap happens in bot.py's
handle_web_benchmark_callback, which calls apply_update() / ignore_source()
below.

Cron (Thu 6:15am — offset from benchmark_check.py's Thu 6am so the two
weekly scans don't compete for the same Serper/Ollama window):
  15 6 * * 4  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/research/web_benchmark_check.py \
    >> /home/billyomes/watson/logs/web_benchmark_check.log 2>&1

Usage:
  python3 jobs/research/web_benchmark_check.py
"""
import asyncio
import logging
import os
import sqlite3
import time
from datetime import date, timedelta
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [web_benchmark_check] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.vacation import vacation_gate
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
WEB_BENCHMARKS_DOC = os.path.expanduser("~/watson/memory/projects/web_engagement_benchmarks.md")

SERPER_URL = "https://google.serper.dev/search"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_TIMEOUT = 60

SEARCH_QUERIES = [
    "Barna Digital church",
    "church social media engagement benchmark",
    "nonprofit email open rate benchmark",
    "church website analytics benchmark",
]

LOOKBACK_DAYS = 14

# Cheap keyword relevance filter — must reference an actual engagement metric
# with real data behind it, not generic "grow your church's social media"
# tips content. No LLM call for this step; the Ollama call below is only for
# summarizing candidates that already pass.
_ENGAGEMENT_TERMS = (
    "engagement rate", "open rate", "click rate", "bounce rate",
    "followers", "reach",
)
_DATA_TERMS = (
    "%", "percent", "trend", "average", "benchmark", "benchmarks",
    "report", "study",
)
_TIP_LISTICLE_TERMS = (
    "tips", "how to grow", "strategies", "ways to", "checklist", "guide to",
)


def _is_relevant(title: str, snippet: str) -> bool:
    text = f"{title} {snippet}".lower()
    has_engagement = any(t in text for t in _ENGAGEMENT_TERMS)
    has_data = any(t in text for t in _DATA_TERMS)
    looks_like_tips = any(t in text for t in _TIP_LISTICLE_TERMS) and not has_data
    return has_engagement and has_data and not looks_like_tips


def _serper_search(query: str, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        log.error("SERPER_API_KEY not set")
        return []

    end = date.today()
    start = end - timedelta(days=lookback_days)
    tbs = f"cdr:1,cd_min:{start.strftime('%m/%d/%Y')},cd_max:{end.strftime('%m/%d/%Y')}"

    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 10, "tbs": tbs},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Serper request failed for %r: %s", query, exc)
        return []

    return [
        {
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": item.get("link", ""),
        }
        for item in data.get("organic", [])
    ]


def _source_name_from_url(url: str) -> str:
    netloc = urlparse(url).netloc
    return netloc[4:] if netloc.startswith("www.") else netloc


def _already_seen(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM web_benchmark_sources WHERE url = ?", (url,)
    ).fetchone()
    return row is not None


def _insert_candidate(conn: sqlite3.Connection, result: dict, source_name: str, summary: str) -> int:
    cur = conn.execute(
        """INSERT INTO web_benchmark_sources
           (url, title, source_name, date_found, summary, status)
           VALUES (?, ?, ?, ?, ?, 'pending')""",
        (result["url"], result["title"], source_name, date.today().isoformat(), summary),
    )
    conn.commit()
    return cur.lastrowid


async def _summarize(title: str, snippet: str) -> str:
    prompt = (
        "Summarize this church/nonprofit digital-engagement research finding in 1-2 "
        "plain sentences for a pastor. Be factual and concise, based only on the "
        "text given — do not add information not present in it.\n\n"
        f"Title: {title}\n"
        f"Snippet: {snippet}\n\n"
        "Return only the summary. No preamble."
    )

    def _call():
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    try:
        return await asyncio.to_thread(_call)
    except Exception as exc:
        log.warning("Ollama summary failed for %r: %s", title, exc)
        return snippet[:200]


def _send_candidate(source_id: int, source_name: str, title: str, summary: str) -> None:
    text = (
        f"📈 New web engagement benchmark research found\n\n"
        f"Source: {source_name}\n"
        f"{title}\n\n"
        f"{summary}"
    )
    if vacation_gate("normal", "jobs.research.web_benchmark_check", text):
        return
    payload = {
        "chat_id": WATSON_CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "Update doc", "callback_data": f"webbench_update:{source_id}"},
                {"text": "Ignore",     "callback_data": f"webbench_ignore:{source_id}"},
            ]]
        },
    }
    resp = requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()


# ── bot.py-facing helpers (button resolution) ──────────────────────────────

def get_source(source_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM web_benchmark_sources WHERE id = ?", (source_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _entry_line(source_name: str, summary: str) -> str:
    today = date.today().isoformat()
    label = source_name or "Unknown source"
    return f"- **{today}** — {label}: {summary}"


def append_update_log_entry(source_name: str, summary: str) -> None:
    """Insert a new dated line at the top of the Update Log section, per the
    doc's own 'Entry format for future updates' line."""
    with open(WEB_BENCHMARKS_DOC, "r", encoding="utf-8") as f:
        content = f.read()

    marker = (
        "*Newest entries on top. Each entry is additive — old entries are "
        "never deleted, only superseded in practice.*\n"
    )
    idx = content.find(marker)
    if idx == -1:
        raise RuntimeError("Update Log marker not found in web_engagement_benchmarks.md")
    insert_at = idx + len(marker)
    new_line = _entry_line(source_name, summary) + "\n"
    updated = content[:insert_at] + new_line + content[insert_at:]

    with open(WEB_BENCHMARKS_DOC, "w", encoding="utf-8") as f:
        f.write(updated)


def apply_update(source_id: int) -> dict:
    """'Update doc' button: append log entry, mark row approved."""
    row = get_source(source_id)
    if not row:
        return {"ok": False, "msg": "Source not found."}
    append_update_log_entry(row["source_name"], row["summary"] or "")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE web_benchmark_sources SET status='approved' WHERE id=?", (source_id,)
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "msg": f"Added to web_engagement_benchmarks.md Update Log: {row['source_name']}"}


def ignore_source(source_id: int) -> dict:
    """'Ignore' button: mark row ignored, no file change."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE web_benchmark_sources SET status='ignored' WHERE id=?", (source_id,)
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "msg": "Ignored."}


# ── Main scan ───────────────────────────────────────────────────────────────

async def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        found_any = False
        for query in SEARCH_QUERIES:
            results = _serper_search(query)
            for result in results:
                if not result["url"] or _already_seen(conn, result["url"]):
                    continue
                if not _is_relevant(result["title"], result["snippet"]):
                    continue

                source_name = _source_name_from_url(result["url"]) or query
                summary = await _summarize(result["title"], result["snippet"])
                source_id = _insert_candidate(conn, result, source_name, summary)
                _send_candidate(source_id, source_name, result["title"], summary)
                found_any = True
                log.info("New candidate id=%d: %s", source_id, result["title"])
                time.sleep(2)

        if not found_any:
            log.info("No relevant new candidates this run.")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
