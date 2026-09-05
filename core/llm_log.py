"""core/llm_log.py — lightweight, always-on logging of every LLM call made
through Watson's shared `requests` client, tagged by which job/script made
the call.

Background (2026-09-04): Watson has no single Ollama call site the way the
task that created this file assumed. Roughly 40 job files each carry their
own small `_call_ollama()` / `call_ollama()` helper that does
`requests.post(OLLAMA_URL, json=payload, timeout=...)` — copy-pasted, not
shared. `jobs/skillbuilder/router.py`'s "router.py" is the skill-matching
router, not an LLM client, and core/ollama_lock.py is a busy-signal file
lock (see its own docstring), not a call site either. There is no existing
choke point to wrap.

Rather than reimplementing logging in ~40 places (or rewriting ~70+
individual call sites, several of which call `requests.post` through a
locally aliased import — see jobs/dashboard/app.py's `_sreq`/`_mreq`/`_req`/
`_siri_req`), this module patches `requests.post` itself, once per process,
to time and log only calls whose URL is Watson's local Ollama endpoint —
every other requests.post call (Telegram, Gmail, Kit, Serper, etc.) passes
through completely unchanged, with no behavior change (same args, same
timeout, same exceptions). Patching the shared `requests` module object
also means it doesn't matter whether a call site does `import requests` or
`import requests as _foo` — both look up the same `.post` attribute.

Any file that can independently start a process and call Ollama (a cron
job's own script, bot.py, dashboard/app.py) imports this module once near
its own top-level imports. Python's module cache means the patch installs
at most once per process no matter how many such files get loaded.

This is purely additive observability: it never changes what gets sent to
Ollama, never touches routing/model-selection, and never raises into a
caller — a failure to log is swallowed, same philosophy as
core/claude_tier.py, since a job's real work must never break because
logging did.

Purpose: after ~1 week of real production data, compare call volume and
weight per job to decide which jobs should get a small, budget-capped
Claude API tier (core/claude_tier.py) for deeper-reasoning calls. See
jobs/analytics/llm_usage_report.py for the report.
"""
import logging
import sys
import time
from pathlib import Path

import requests

from core.database import get_connection

log = logging.getLogger(__name__)

_OLLAMA_PREFIX = "http://localhost:11434"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_call_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name          TEXT NOT NULL,
                provider          TEXT NOT NULL DEFAULT 'ollama',
                model             TEXT,
                endpoint          TEXT,
                success           INTEGER NOT NULL,
                latency_ms        INTEGER NOT NULL,
                prompt_tokens     INTEGER,
                completion_tokens INTEGER,
                error             TEXT,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


_bootstrap()


def _caller_job_name() -> str:
    """Best-effort identifier for whichever job/script made the call: the
    repo-relative file path of the nearest stack frame outside this module.

    Deliberately keyed on __file__, not __name__ — a cron-invoked job runs
    as __main__ (every job would collapse to the same "__main__" label),
    but __file__ is the same real source path whether the module is run
    directly or imported (e.g. as a dashboard/bot skill handler)."""
    frame = sys._getframe(1)
    while frame is not None:
        mod_name = frame.f_globals.get("__name__")
        mod_file = frame.f_globals.get("__file__")
        if mod_name != __name__ and mod_file:
            try:
                return Path(mod_file).resolve().relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                return Path(mod_file).name
        frame = frame.f_back
    return "unknown"


def _log_call(*, job_name, model, endpoint, success, latency_ms,
              prompt_tokens=None, completion_tokens=None, error=None) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO llm_call_log "
                "(job_name, model, endpoint, success, latency_ms, "
                "prompt_tokens, completion_tokens, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_name, model, endpoint, int(success), latency_ms,
                 prompt_tokens, completion_tokens, error),
            )
    except Exception:
        log.debug("llm_log: failed to record call log entry", exc_info=True)


_original_post = requests.post


def _logged_post(url, *args, **kwargs):
    if not isinstance(url, str) or not url.startswith(_OLLAMA_PREFIX):
        return _original_post(url, *args, **kwargs)

    payload = kwargs.get("json")
    payload = payload if isinstance(payload, dict) else {}
    model = payload.get("model")
    is_stream = bool(payload.get("stream"))
    job_name = _caller_job_name()

    start = time.monotonic()
    try:
        resp = _original_post(url, *args, **kwargs)
    except Exception as exc:
        _log_call(
            job_name=job_name, model=model, endpoint=url, success=False,
            latency_ms=int((time.monotonic() - start) * 1000), error=str(exc)[:500],
        )
        raise

    latency_ms = int((time.monotonic() - start) * 1000)
    success = resp.ok
    error = None if success else f"HTTP {resp.status_code}"
    prompt_tokens = completion_tokens = None

    if success and not is_stream:
        try:
            data = resp.json()
            prompt_tokens = data.get("prompt_eval_count")
            completion_tokens = data.get("eval_count")
        except Exception:
            pass

    _log_call(
        job_name=job_name, model=model, endpoint=url, success=success,
        latency_ms=latency_ms, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, error=error,
    )
    return resp


if not getattr(requests.post, "_watson_llm_logged", False):
    _logged_post._watson_llm_logged = True
    requests.post = _logged_post
