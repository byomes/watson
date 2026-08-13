"""Shared subprocess retry helper for Watson's backup legs (and any other job
that shells out to a flaky external command).

Both backup legs — jobs/backup.py (OneDrive/rclone) and jobs/backup_local.py
(restic) — run subprocess commands that can fail transiently: sqlite3 .backup
snapshots (lock contention), rclone copy/copyto (network), restic
backup/forget --prune (network + disk I/O). Historically each failed op was
marked failed on the *first* try (bug_tracker #60 was one symptom of this —
the sqlite3 snapshot giving up instantly with no busy-timeout).

`run_with_retry` re-runs a subprocess command with exponential backoff until it
succeeds or a real-elapsed-time budget is exhausted, logging every attempt. It
returns the final `subprocess.CompletedProcess` (success OR the last failure),
so callers keep their existing `if result.returncode != 0: errors.append(...)`
error-tracking and Telegram-summary structure unchanged — only the retry
semantics move. A failure now means "still failing after the full retry
budget," not "failed once."

This lives in ONE place on purpose. The bug this pattern guards against was
itself caused by two near-identical backup files drifting apart when a fix
landed in only one of them; a single shared helper keeps both legs honest.
"""
import subprocess
import time
from typing import Callable, Optional, Sequence


def run_with_retry(
    cmd: Sequence[str],
    *,
    budget_seconds: float = 600,
    initial_backoff: float = 5,
    max_backoff: float = 60,
    description: str = "",
    log: Optional[Callable[[str], None]] = None,
    **run_kwargs,
) -> subprocess.CompletedProcess:
    """Run `cmd` via subprocess.run, retrying with exponential backoff until it
    exits 0 or `budget_seconds` of real elapsed time is used up.

    Args:
        cmd: argv list passed to subprocess.run.
        budget_seconds: stop starting new attempts once this much wall-clock
            time has elapsed since the first attempt (default ~10 min).
        initial_backoff / max_backoff: exponential backoff bounds, in seconds.
        description: human label for log lines (e.g. "rclone copyto watson.db").
        log: optional callable used for per-attempt logging; falls back to
            print. Pass the caller's own file logger so attempts land in the
            same backup log.
        **run_kwargs: forwarded to subprocess.run (e.g. env=...). capture_output
            and text default to True so callers can read stderr as before.

    Returns:
        The final subprocess.CompletedProcess — the first success, or the last
        failure once the budget is exhausted. Never raises on a non-zero exit;
        the caller inspects `.returncode` exactly as with a bare subprocess.run.
    """
    _log = log or print
    label = description or (cmd[0] if cmd else "command")

    run_kwargs.setdefault("capture_output", True)
    run_kwargs.setdefault("text", True)

    start = time.monotonic()
    backoff = initial_backoff
    attempt = 0

    while True:
        attempt += 1
        result = subprocess.run(cmd, **run_kwargs)

        if result.returncode == 0:
            if attempt > 1:
                _log(f"OK after retry: {label} (succeeded on attempt {attempt})")
            return result

        elapsed = time.monotonic() - start
        err = (result.stderr or "").strip()
        remaining = budget_seconds - elapsed

        # Budget spent (or no time left to wait out a backoff) — give up and
        # hand the failed result back for normal error tracking.
        if remaining <= 0:
            _log(
                f"GAVE UP: {label} still failing after {attempt} attempt(s) / "
                f"{elapsed:.0f}s (budget {budget_seconds:.0f}s): {err}"
            )
            return result

        sleep_for = min(backoff, max_backoff, remaining)
        _log(
            f"RETRY: {label} failed on attempt {attempt} "
            f"({elapsed:.0f}s elapsed, retrying in {sleep_for:.0f}s): {err}"
        )
        time.sleep(sleep_for)
        backoff = min(backoff * 2, max_backoff)
