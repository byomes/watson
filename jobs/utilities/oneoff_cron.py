"""jobs/utilities/oneoff_cron.py -- shared self-delete helper for one-off
scheduled scripts (jobs/*/_oneoff_*.py).

Fixes a bug present in every one-off script written before 2026-09-25:
each script's own inline _self_delete_cron() matched crontab lines on
os.path.basename(__file__) (e.g. "_oneoff_foo.py"), but the crontab line
actually invokes the script as a dotted module path via
"-m jobs.<pkg>._oneoff_foo" with no ".py" suffix. The substring never
matched, so the crontab line was never removed even though the job logged
"Removed crontab entry" as if it had. Found 2026-09-25 when
_oneoff_bill_followup_system_scope.py's crontab line was still present
after a successful send + file self-delete; the same stale-line pattern
was independently confirmed for two 2026-09-23 one-offs whose .py files
had correctly self-deleted but whose crontab lines had not.

Future one-off scripts should import self_delete() from here instead of
copy-pasting the old inline pattern:

    from jobs.utilities.oneoff_cron import self_delete
    ...
    def main():
        ...
        self_delete(__file__)
"""
import logging
import os
import subprocess

log = logging.getLogger(__name__)


def _module_path(script_path: str) -> str:
    """Convert an absolute script path under the watson repo into the dotted
    "-m" module path used in its own crontab invocation, e.g.
    ".../watson/jobs/congregation/_oneoff_foo.py" -> "jobs.congregation._oneoff_foo".
    """
    watson_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    rel = os.path.relpath(os.path.abspath(script_path), watson_root)
    without_ext = rel[:-3] if rel.endswith(".py") else rel
    return without_ext.replace(os.sep, ".")


def _filter_crontab_lines(lines: list, match_strings: list) -> tuple:
    """Drop any line containing one of match_strings, plus an immediately
    preceding comment-only line (the "# One-off: ..." description each of
    these scripts' cron entries is written with). Pure function, no I/O,
    so this is unit-testable without touching the real crontab.

    Returns (remaining_lines, removed: bool).
    """
    out = []
    removed = False
    i = 0
    while i < len(lines):
        if any(m in lines[i] for m in match_strings):
            if out and out[-1].strip().startswith("#"):
                out.pop()
            removed = True
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return out, removed


def remove_cron_entry(script_path: str) -> bool:
    """Remove the crontab line for this script, matching on both the dotted
    "-m" module path (how these are actually scheduled) and the bare
    filename (defensive, in case a future one-off is scheduled differently).
    Returns True if a line was actually removed.
    """
    match_strings = [_module_path(script_path), os.path.basename(script_path)]

    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
    lines = result.stdout.split("\n")
    out, removed = _filter_crontab_lines(lines, match_strings)

    if removed:
        subprocess.run(["crontab", "-"], input="\n".join(out), text=True, check=True)
    return removed


def self_delete(script_path: str) -> None:
    """Remove this one-off's crontab entry, then delete the script file
    itself. Never raises -- a failure on either half just leaves a stale
    line/file for manual cleanup, not a crash on an otherwise-successful
    send.
    """
    try:
        if remove_cron_entry(script_path):
            log.info("Removed crontab entry for %s", os.path.basename(script_path))
        else:
            log.warning(
                "No matching crontab entry found for %s -- may already be "
                "removed, or scheduled differently than expected",
                os.path.basename(script_path),
            )
    except Exception as exc:
        log.warning("Could not remove crontab entry: %s", exc)

    try:
        os.remove(os.path.abspath(script_path))
        log.info("Removed one-off script file: %s", script_path)
    except Exception as exc:
        log.warning("Could not remove one-off script file: %s", exc)
