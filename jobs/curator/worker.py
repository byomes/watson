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
import threading
import time

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
                # research_gaps stored as a JSON array string; parse to a list so this
                # polling endpoint emits the same shape as api.py's _book_row_to_dict.
                book_dict["research_gaps"] = (
                    json.loads(book_dict["research_gaps"]) if book_dict.get("research_gaps") else []
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
    elif job["input_type"] == "chatgpt_text":
        _process_chatgpt_text(job)
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
# A family member researches a book in the ChatGPT app, copies ChatGPT's response
# text from the phone, and an iOS Shortcut posts that text to
# /api/curator/ingest/chatgpt, which enqueues a 'chatgpt_text' job. Watson already
# has the real research text handed to it — there is no link to fetch or render.
# ChatGPT has already done the equivalent of both Stage A and Stage B
# (identification + spice findings), so this path creates the book directly and
# marks the job 'done' — no research_book_fast(), no Stage B.


def _process_chatgpt_text(job: dict) -> None:
    from jobs.curator.ingest import (
        _add_source, _add_spice_findings, _attach_reading_status_if_missing,
        _create_book, _derive_spice_notes, _extract_chatgpt_research,
        _find_recent_duplicate, title_case,
    )

    payload = json.loads(job["input_raw"] or "{}")
    research_text = payload.get("research_text") or ""

    conn = get_db()
    try:
        try:
            research = _extract_chatgpt_research(research_text)

            # Couldn't confidently identify a book — same outcome as today's
            # "could not identify a book" path (needs_review, no findings).
            if not research["confident"] or not research.get("title"):
                book_id = _create_book(
                    title="Unknown", author="Unknown", status="needs_review",
                    added_by=job["submitted_by"],
                )
                _add_source(book_id, "chatgpt", None, research_text)
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
                 "excerpt": f["excerpt"], "url": None}
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
            _add_source(book_id, "chatgpt", None, research_text)
            _add_spice_findings(book_id, findings)

            conn.execute(
                "UPDATE ingest_jobs SET status='done', book_id=?, "
                "completed_at=datetime('now') WHERE id=?",
                (book_id, job["id"]),
            )
            conn.commit()
        except Exception as exc:
            log.error("chatgpt_text job %s failed: %s", job["id"], exc)
            # Never lose a family member's paste: even when extraction blows up
            # (Ollama down, timeout, malformed response), land the raw text as a
            # needs_review book so it stays visible and salvageable in the app
            # rather than being swallowed into ingest_jobs.input_raw where only a
            # log dive would find it. This mirrors the low-confidence path above,
            # which already preserves the raw text. Fall back to a plain 'failed'
            # only if even this salvage write fails.
            try:
                book_id = _create_book(
                    title="Unknown", author="Unknown", status="needs_review",
                    added_by=job["submitted_by"],
                )
                _add_source(book_id, "chatgpt", None, research_text)
                conn.execute(
                    "UPDATE ingest_jobs SET status='done', book_id=?, error_message=?, "
                    "completed_at=datetime('now') WHERE id=?",
                    (book_id, str(exc), job["id"]),
                )
                conn.commit()
            except Exception as exc2:
                log.error("chatgpt_text job %s salvage also failed: %s", job["id"], exc2)
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
