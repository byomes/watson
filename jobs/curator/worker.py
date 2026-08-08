"""jobs/curator/worker.py — sequential background processor for ingest_jobs.

Runs as a single daemon thread inside watson-dashboard.service's process (started once
at app boot via start_worker()). Processes exactly one job at a time, deliberately —
Watson's Ollama setup on the Beelink serializes generate requests regardless of
concurrency (see the FMSPC / OLLAMA_NUM_PARALLEL notes in WATSON_ARCHITECTURE.md), so a
second concurrent worker would only add complexity, not speed. bot.py's Telegram
curator: submissions also enqueue through here (not a separate direct-call thread) so
that guarantee holds across every entry point, not just the web app.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from jobs.curator import get_db, resolve_user_contact

log = logging.getLogger(__name__)

_POLL_INTERVAL = 1.5
_started = False


# ── Enqueue ──────────────────────────────────────────────────────────────────

def enqueue_job(
    *, input_type, input_raw=None, image_bytes=None, image_mimetype=None,
    submitted_by=None, batch_id=None,
) -> int:
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO ingest_jobs (input_type, input_raw, image_blob, image_mimetype, "
            "submitted_by, batch_id) VALUES (?, ?, ?, ?, ?, ?)",
            (input_type, input_raw, image_bytes, image_mimetype, submitted_by, batch_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def enqueue_batch(items: list[dict], submitted_by=None) -> dict:
    """items: list of {"title":, "author":, "series":} or {"link":}.
    A single-item batch whose one item is a link (and no title) is a 'reel_link'
    extraction job — a social post that may mention multiple books. Everything else is
    one 'batch_item' job per entry."""
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO ingest_batches (submitted_by, total_jobs) VALUES (?, ?)",
            (submitted_by, len(items)),
        )
        batch_id = cur.lastrowid
        conn.commit()

        is_reel = len(items) == 1 and bool(items[0].get("link")) and not items[0].get("title")
        job_ids = []
        for item in items:
            input_type = "reel_link" if is_reel else "batch_item"
            cur = conn.execute(
                "INSERT INTO ingest_jobs (input_type, input_raw, submitted_by, batch_id) "
                "VALUES (?, ?, ?, ?)",
                (input_type, json.dumps(item), submitted_by, batch_id),
            )
            job_ids.append(cur.lastrowid)
        conn.commit()
        return {"batch_id": batch_id, "job_ids": job_ids}
    finally:
        conn.close()


def get_job_status(job_id: int) -> dict | None:
    conn = get_db()
    try:
        job = conn.execute("SELECT * FROM ingest_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return None
        result = {
            "job_id": job["id"],
            "status": job["status"],
            "error_message": job["error_message"],
            "batch_id": job["batch_id"],
            "book": None,
        }
        if job["book_id"]:
            book = conn.execute("SELECT * FROM books WHERE id = ?", (job["book_id"],)).fetchone()
            if book:
                book_dict = dict(book)
                # Three-state (bug #47, watson.db bug_tracker): NULL = couldn't verify
                # (e.g. Amazon's bot-block page) must stay None, not collapse into False
                # ("confirmed not on KU") via a bare bool() coercion. Mirrors api.py's
                # _book_row_to_dict(), the one other place this same row gets serialized.
                book_dict["kindle_unlimited"] = (
                    None if book_dict["kindle_unlimited"] is None else bool(book_dict["kindle_unlimited"])
                )
                findings = conn.execute(
                    "SELECT * FROM spice_findings WHERE book_id = ? ORDER BY rank ASC",
                    (job["book_id"],),
                ).fetchall()
                book_dict["findings"] = [dict(f) for f in findings]
                result["book"] = book_dict
        return result
    finally:
        conn.close()


# ── Worker loop ──────────────────────────────────────────────────────────────

def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker_loop, daemon=True, name="curator-ingest-worker").start()
    log.info("curator ingest worker started")


def _worker_loop() -> None:
    while True:
        try:
            job = _claim_next_job()
            if job:
                _process_job(job)
            else:
                time.sleep(_POLL_INTERVAL)
        except Exception as exc:
            log.error("curator worker loop error: %s", exc)
            time.sleep(_POLL_INTERVAL)


def _claim_next_job() -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM ingest_jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE ingest_jobs SET status='running' WHERE id=?", (row["id"],))
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def _process_job(job: dict) -> None:
    if job["input_type"] == "reel_link":
        _process_reel_link(job)
    elif job["input_type"] == "chatgpt_link":
        _process_chatgpt_link(job)
    else:
        _process_single(job)


def _process_single(job: dict) -> None:
    """Stage A/B split (curator-spec.md Commit 3), plus the dedup-cache short-circuit
    (Commit 6). Three outcomes for Stage A (ingest_submission):

    - Dedup hit ("duplicate": True in the result, Commit 6): no research ran at all —
      straight to 'done', book_id set, Stage B skipped entirely (nothing to enrich;
      the existing book was already fully enriched on its own original submission).
    - Normal success: job marked 'partial' with book_id already set — the book row is
      fully visible to Mel at this point (same gating rule as always). Stage B
      (enrich_submission_stage_b) then fires immediately, in this same thread, with no
      separate queue entry — see its docstring — and the job is marked 'done' once
      that returns, whether or not it actually found anything.
    - Failure: job marked 'failed', Stage B skipped entirely (no book was created to
      enrich).

    A Stage B failure is logged but never flips a job that reached 'partial' to
    'failed' — Stage A's result already stands as the final one."""
    from jobs.curator.ingest import enrich_submission_stage_b, ingest_submission

    is_batch_item = job["batch_id"] is not None
    payload = json.loads(job["input_raw"] or "{}")

    stage_a_result = None
    is_duplicate = False
    conn = get_db()
    try:
        try:
            stage_a_result = ingest_submission(
                submitted_by=job["submitted_by"],
                title=payload.get("title"),
                author=payload.get("author"),
                series=payload.get("series"),
                link=payload.get("link"),
                image_bytes=job["image_blob"],
                image_mimetype=job["image_mimetype"],
                notify_telegram=not is_batch_item,
                job_id=job["id"],
            )
            is_duplicate = bool(stage_a_result.get("duplicate"))
            if is_duplicate:
                conn.execute(
                    "UPDATE ingest_jobs SET status='done', book_id=?, completed_at=datetime('now') "
                    "WHERE id=?",
                    (stage_a_result.get("book_id"), job["id"]),
                )
            else:
                conn.execute(
                    "UPDATE ingest_jobs SET status='partial', book_id=? WHERE id=?",
                    (stage_a_result.get("book_id"), job["id"]),
                )
            conn.commit()
        except Exception as exc:
            log.error("ingest job %s (Stage A) failed: %s", job["id"], exc)
            conn.execute(
                "UPDATE ingest_jobs SET status='failed', error_message=?, "
                "completed_at=datetime('now') WHERE id=?",
                (str(exc), job["id"]),
            )
            conn.commit()
    finally:
        conn.close()

    if stage_a_result is not None and not is_duplicate:
        try:
            enrich_submission_stage_b(
                stage_a_result.get("book_id"),
                stage_a_result.get("title"),
                stage_a_result.get("author"),
                stage_a_result.get("findings") or [],
                job_id=job["id"],
            )
        except Exception as exc:
            log.error("Stage B enrichment for job %s (book_id=%s) failed: %s",
                       job["id"], stage_a_result.get("book_id"), exc)

        conn = get_db()
        try:
            conn.execute(
                "UPDATE ingest_jobs SET status='done', completed_at=datetime('now') WHERE id=?",
                (job["id"],),
            )
            conn.commit()
        finally:
            conn.close()

    if job["batch_id"]:
        _maybe_complete_batch(job["batch_id"])


def _process_reel_link(job: dict) -> None:
    from jobs.curator.ingest import extract_multiple_books_from_text, fetch_og_metadata

    payload = json.loads(job["input_raw"] or "{}")
    link = payload.get("link")

    conn = get_db()
    try:
        try:
            meta = fetch_og_metadata(link)
            extraction = extract_multiple_books_from_text(meta["raw_text"])
            confident = extraction["confident_titles"]
            uncertain_note = extraction["uncertain_note"]

            new_job_ids = []
            for item in confident:
                cur = conn.execute(
                    "INSERT INTO ingest_jobs (input_type, input_raw, submitted_by, batch_id) "
                    "VALUES ('batch_item', ?, ?, ?)",
                    (
                        json.dumps({"title": item["title"], "author": item.get("author")}),
                        job["submitted_by"], job["batch_id"],
                    ),
                )
                new_job_ids.append(cur.lastrowid)
            if new_job_ids:
                conn.execute(
                    "UPDATE ingest_batches SET total_jobs = total_jobs + ? WHERE id = ?",
                    (len(new_job_ids), job["batch_id"]),
                )
            conn.commit()

            if uncertain_note or not confident:
                _send_uncertain_reel_email(
                    job["submitted_by"], link, meta["raw_text"], confident, uncertain_note
                )

            conn.execute(
                "UPDATE ingest_jobs SET status='done', completed_at=datetime('now') WHERE id=?",
                (job["id"],),
            )
            conn.commit()
        except Exception as exc:
            log.error("reel_link job %s failed: %s", job["id"], exc)
            conn.execute(
                "UPDATE ingest_jobs SET status='failed', error_message=?, "
                "completed_at=datetime('now') WHERE id=?",
                (str(exc), job["id"]),
            )
            conn.commit()
    finally:
        conn.close()

    _maybe_complete_batch(job["batch_id"])


# ── ChatGPT-research import ───────────────────────────────────────────────────
#
# A family member researches a book in the ChatGPT app, shares the conversation,
# and an iOS Shortcut posts the share link to /api/curator/ingest/chatgpt, which
# enqueues a 'chatgpt_link' job. ChatGPT has already done the equivalent of both
# Stage A and Stage B (identification + spice findings), so this path creates the
# book directly and marks the job 'done' — no research_book_fast(), no Stage B.

_CHATGPT_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
# Below this, the plain fetch is assumed to have gotten a JS shell rather than
# the real conversation, and we fall back to a rendered fetch.
_MIN_CONVERSATION_CHARS = 400
_REPO_ROOT = Path(__file__).resolve().parents[2]
# Wall-clock guard on the render child. The render itself uses a 30s page
# timeout (jobs/browser/render_page.py); this leaves headroom for Chromium's
# cold launch on top of that before we give up on a wedged child.
_RENDER_SUBPROCESS_TIMEOUT_S = 90

# In-process tally of how the ChatGPT fetch resolved, so the logs answer "how
# often does the Playwright fallback actually fire vs. the plain requests.get()
# succeeding?" — the real numbers Bill wants before deciding whether the
# subprocess cost is worth carrying long-term. Resets on service restart (fine:
# it's a rolling since-boot rate, and each per-job line also carries path= for
# exact grep-able counts across restarts).
_FETCH_STATS = {"requests": 0, "browser": 0, "browser_empty": 0}


def _html_to_text(html: str) -> str:
    """Crude tag-strip for the plain-fetch path: drop <script>/<style> bodies,
    remove remaining tags, collapse whitespace. Enough to (a) feed the extractor
    and (b) decide whether the plain fetch actually returned conversation content
    or just an empty JS shell."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _record_fetch(path: str) -> None:
    """Increment the since-boot fetch tally for `path` and log the running rate."""
    key = path.replace("-", "_")
    if key in _FETCH_STATS:
        _FETCH_STATS[key] += 1
    total = sum(_FETCH_STATS.values())
    fallback = _FETCH_STATS["browser"] + _FETCH_STATS["browser_empty"]
    log.info(
        "curator chatgpt fetch stats (since boot): total=%d plain_ok=%d "
        "fallback_fired=%d (browser_ok=%d browser_empty=%d) fallback_rate=%.0f%%",
        total, _FETCH_STATS["requests"], fallback,
        _FETCH_STATS["browser"], _FETCH_STATS["browser_empty"],
        (100.0 * fallback / total) if total else 0.0,
    )


def _render_chatgpt_share_text(share_url: str) -> str:
    """Out-of-process Playwright render. Shells out to
    jobs/browser/render_page.py as a child process rather than importing
    get_page() here, so Chromium never loads into watson-dashboard.service's own
    process — a Chromium crash is contained to the short-lived child, per
    browser_service.py's guardrail (browser jobs run as one-off subprocess
    invocations). Uses the same interpreter (sys.executable = the service's
    venv, where Playwright is installed). Returns the rendered body text, or ''
    on any failure (blocked/timeout/nonzero exit)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "jobs.browser.render_page", share_url],
            cwd=str(_REPO_ROOT),
            env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
            timeout=_RENDER_SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log.warning("chatgpt share render subprocess timed out for %s", share_url)
        return ""
    except Exception as exc:
        log.warning("chatgpt share render subprocess failed to launch for %s: %s", share_url, exc)
        return ""

    if proc.returncode != 0:
        log.warning(
            "chatgpt share render subprocess exit=%s for %s: %s",
            proc.returncode, share_url, (proc.stderr or "").strip()[:300],
        )
        return ""
    return (proc.stdout or "").strip()


def _fetch_chatgpt_share_text(share_url: str) -> tuple[str, str]:
    """Fetch the shared conversation's page text. Plain requests.get() first;
    fall back to an out-of-process Playwright render only if the plain text looks
    empty / lacks conversation content. Returns (text, path) where path is one of
    'requests', 'browser', or 'browser-empty' — logged by the caller (per job)
    and tallied in _record_fetch (since-boot rate) so the logs show whether the
    plain fetch is sufficient or the Playwright fallback is firing."""
    text = ""
    try:
        resp = requests.get(share_url, headers=_CHATGPT_UA, timeout=15)
        resp.raise_for_status()
        text = _html_to_text(resp.text)
    except Exception as exc:
        log.warning("chatgpt share plain fetch failed for %s: %s", share_url, exc)

    if len(text) >= _MIN_CONVERSATION_CHARS:
        result, path = text, "requests"
    else:
        rendered = _render_chatgpt_share_text(share_url)
        if len(rendered) >= _MIN_CONVERSATION_CHARS:
            result, path = rendered, "browser"
        else:
            # Neither cleared the bar — hand back whichever is longer so the
            # extractor still gets a shot (and can decide it's not confident).
            result, path = (rendered if len(rendered) > len(text) else text), "browser-empty"

    _record_fetch(path)
    return result, path


def _process_chatgpt_link(job: dict) -> None:
    from jobs.curator.ingest import (
        _add_source, _add_spice_findings, _attach_reading_status_if_missing,
        _create_book, _derive_spice_notes, _extract_chatgpt_research,
        _find_recent_duplicate, title_case,
    )

    payload = json.loads(job["input_raw"] or "{}")
    share_url = payload.get("share_url")

    conn = get_db()
    try:
        try:
            page_text, fetch_path = _fetch_chatgpt_share_text(share_url)
            log.info(
                "curator chatgpt fetch job_id=%s path=%s chars=%d url=%s",
                job["id"], fetch_path, len(page_text or ""), share_url,
            )

            research = _extract_chatgpt_research(page_text)

            # Couldn't confidently identify a book — same outcome as today's
            # "could not identify a book" path (needs_review, no findings).
            if not research["confident"] or not research.get("title"):
                book_id = _create_book(
                    title="Unknown", author="Unknown", status="needs_review",
                    added_by=job["submitted_by"],
                )
                _add_source(book_id, "chatgpt", share_url, page_text)
                conn.execute(
                    "UPDATE ingest_jobs SET status='done', book_id=?, "
                    "completed_at=datetime('now') WHERE id=?",
                    (book_id, job["id"]),
                )
                conn.commit()
                return

            title = title_case(research["title"])
            author = research.get("author") or "Unknown"
            series = research.get("series")

            duplicate = _find_recent_duplicate(title, author)
            if duplicate is not None:
                _attach_reading_status_if_missing(duplicate["id"], job["submitted_by"])
                conn.execute(
                    "UPDATE ingest_jobs SET status='done', book_id=?, "
                    "completed_at=datetime('now') WHERE id=?",
                    (duplicate["id"], job["id"]),
                )
                conn.commit()
                return

            findings = [
                {"source_name": "ChatGPT", "source_type": "chatgpt", "rank": f["rank"],
                 "excerpt": f["excerpt"], "url": share_url}
                for f in research["spice_findings"]
            ]
            # Same visibility gate as ingest_submission(): real findings -> pending;
            # a confident book with no spice sentences -> needs_review. spice_rating
            # stays NULL (2026-07-22 decision — Mel reads the excerpt herself).
            status = "pending" if findings else "needs_review"

            book_id = _create_book(
                title=title, author=author, series=series, status=status,
                added_by=job["submitted_by"],
                spice_notes=_derive_spice_notes(findings),
                kindle_unlimited=research.get("kindle_unlimited"),
            )
            _add_source(book_id, "chatgpt", share_url, page_text)
            _add_spice_findings(book_id, findings)

            conn.execute(
                "UPDATE ingest_jobs SET status='done', book_id=?, "
                "completed_at=datetime('now') WHERE id=?",
                (book_id, job["id"]),
            )
            conn.commit()
        except Exception as exc:
            log.error("chatgpt_link job %s failed: %s", job["id"], exc)
            conn.execute(
                "UPDATE ingest_jobs SET status='failed', error_message=?, "
                "completed_at=datetime('now') WHERE id=?",
                (str(exc), job["id"]),
            )
            conn.commit()
    finally:
        conn.close()


def _maybe_complete_batch(batch_id) -> None:
    if not batch_id:
        return
    conn = get_db()
    try:
        conn.execute(
            "UPDATE ingest_batches SET completed_jobs = completed_jobs + 1 WHERE id = ?",
            (batch_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT total_jobs, completed_jobs, submitted_by, status FROM ingest_batches "
            "WHERE id = ?",
            (batch_id,),
        ).fetchone()
        if row and row["status"] != "done" and row["completed_jobs"] >= row["total_jobs"]:
            conn.execute(
                "UPDATE ingest_batches SET status='done', completed_at=datetime('now') WHERE id=?",
                (batch_id,),
            )
            conn.commit()
            book_count = conn.execute(
                "SELECT COUNT(*) as c FROM ingest_jobs WHERE batch_id=? AND book_id IS NOT NULL",
                (batch_id,),
            ).fetchone()["c"]
            if book_count > 0:
                _send_batch_sms(row["submitted_by"], book_count)
    finally:
        conn.close()


# ── Notifications ────────────────────────────────────────────────────────────

def _send_batch_sms(user_id, count: int) -> None:
    if not user_id:
        return
    conn = get_db()
    try:
        user = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not user:
        return

    contact = resolve_user_contact(user["name"])
    if not contact or not contact.get("phone"):
        log.warning("Could not resolve SMS contact for curator user %r", user["name"])
        return

    from jobs.sms.sms_send import send_sms
    message = (
        f"Curator: {count} book{'s' if count != 1 else ''} ready for review "
        "— open the app when you get a chance."
    )
    result = send_sms(contact["name"], contact["phone"], "", message)
    if not result.get("success"):
        log.error("Batch-completion SMS failed: %s", result.get("error"))


def _send_uncertain_reel_email(user_id, link, raw_text, confident_titles, uncertain_note) -> None:
    if not user_id:
        return
    conn = get_db()
    try:
        user = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not user:
        return

    contact = resolve_user_contact(user["name"])
    if not contact or not contact.get("email"):
        log.warning("Could not resolve email contact for curator user %r", user["name"])
        return

    lines = ["Watson looked at this link but couldn't confidently sort out every book:", "", link, ""]
    if confident_titles:
        lines.append("Confidently found (already queued for research):")
        for t in confident_titles:
            lines.append(f"  - {t['title']}" + (f" by {t['author']}" if t.get("author") else ""))
        lines.append("")
    lines.append("What I saw but couldn't confidently identify:")
    lines.append(uncertain_note or (raw_text or "")[:800] or "(no readable text found on the page)")
    lines.append("")
    lines.append("Could you take a look and search for those manually in Curator?")
    body = "\n".join(lines)

    from jobs.email_job.brevo_send import send_email
    try:
        result = send_email(
            to_email=contact["email"], to_name=contact.get("name") or "",
            subject="Curator — a few books I couldn't identify", text_body=body,
        )
        if not result["success"]:
            raise RuntimeError(result["error"])
    except Exception as exc:
        log.error("Uncertain-reel email failed: %s", exc)
