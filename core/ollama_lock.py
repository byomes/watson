"""core/ollama_lock.py — cross-process "a long Ollama call is in progress"
signal, sibling to core/ollama_context.py.

Background (bug_tracker #118/#121, 2026-09-03): fixing context truncation
for the long, data-heavy Ollama jobs (audit.py, build.py, team/extractor.py,
the two monthly report jobs) makes them actually take as long as they really
need (up to ~12 minutes) instead of quietly returning a truncated response
fast. A concurrency test (tests/audit_concurrency_test.py) confirmed that
while one of these runs, Ollama's single-request-at-a-time queue
(OLLAMA_NUM_PARALLEL=1) starves the live Telegram intent classifier
(jobs/intent/classifier.classify()) — it hits its own 55s timeout and
silently returns the WRONG intent instead of admitting it doesn't know.
That's a hallucination caused by latency, not a benign slowdown, and it
violates WATSON_ARCHITECTURE.md's "No hallucination. If Watson does not
know, Watson says so and stops" principle.

The fix here isn't a bigger timeout or faster inference — it's honesty:
while a long job holds this lock, classify() and the skill router's LLM
fallback (jobs/skillbuilder/router.py's _ask_router()) skip the Ollama
round trip entirely and return a clear "I'm busy" signal instead of racing
a contended queue and guessing on timeout.

File-based, not a DB row: a plain marker file's existence is trivially
checkable from any process (a cron job, the Flask dashboard, bot.py) with
no schema/connection overhead — this is a "busy" signal to READ, not a
mutex to acquire, so flock() semantics (see jobs/kb/sync_and_index.py's
LOCK_PATH) aren't needed. Matches the existing lock-file convention in this
codebase (data/.kb_sync.lock, data/.devdispatch_poller.lock).
"""
import contextlib
import json
import time
from pathlib import Path

LOCK_PATH = Path("/home/billyomes/watson/data/.ollama_busy.lock")

# A lock older than this is treated as stale (e.g. a crashed job that skipped
# cleanup) rather than trusted indefinitely — generous over every current
# heavy job's real observed wall time (audit.py measured 734-975s) so it
# never expires while a real call is still legitimately running.
_STALE_AFTER_SECONDS = 1800

BUSY_MESSAGE = (
    "Watson's running a background task right now — try again in a few minutes."
)


@contextlib.contextmanager
def heavy_ollama_call(job_name: str):
    """Wrap a long Ollama call: `with heavy_ollama_call("skillbuilder.audit"): ...`.
    Sets the busy signal before the block, clears it after — including on
    exception, via try/finally, so a crash never leaves a stale lock past
    _STALE_AFTER_SECONDS."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(
        json.dumps({"job": job_name, "started_at": time.time()}), encoding="utf-8"
    )
    try:
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def ollama_busy() -> dict | None:
    """Return {"job": str, "started_at": float} if a heavy job's lock is
    currently held and not stale, else None."""
    if not LOCK_PATH.exists():
        return None
    try:
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - data.get("started_at", 0) > _STALE_AFTER_SECONDS:
        return None
    return data
