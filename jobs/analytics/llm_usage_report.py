"""jobs/analytics/llm_usage_report.py — per-job LLM call volume/weight report
from the llm_call_log table (core/llm_log.py), trailing N days by default.

Purpose: after ~1 week of real production logging, this is what Bill reads
to decide which jobs are worth a small ($10/mo) budget-capped Claude API
tier (core/claude_tier.py) for deeper-reasoning calls, vs. which stay on
local Ollama — based on actual call volume and token weight per job, not
guesswork.

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.llm_usage_report
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.llm_usage_report --days 14
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.llm_usage_report --json
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from core.database import get_connection

DEFAULT_DAYS = 7


def collect(since_iso: str) -> list[dict]:
    """One row per job_name, most calls first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT job_name, provider, model, success, prompt_tokens, completion_tokens "
            "FROM llm_call_log WHERE created_at >= ?",
            (since_iso,),
        ).fetchall()

    by_job: dict[str, dict] = {}
    for r in rows:
        job = by_job.setdefault(r["job_name"], {
            "job_name": r["job_name"],
            "total_calls": 0,
            "error_count": 0,
            "calls_by_model": {},
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "_tokened_calls": 0,
        })
        job["total_calls"] += 1
        if not r["success"]:
            job["error_count"] += 1
        model = r["model"] or "unknown"
        job["calls_by_model"][model] = job["calls_by_model"].get(model, 0) + 1
        if r["prompt_tokens"] is not None or r["completion_tokens"] is not None:
            job["total_prompt_tokens"] += r["prompt_tokens"] or 0
            job["total_completion_tokens"] += r["completion_tokens"] or 0
            job["_tokened_calls"] += 1

    results = []
    for job in by_job.values():
        tokened = job.pop("_tokened_calls")
        total_tokens = job["total_prompt_tokens"] + job["total_completion_tokens"]
        job["total_tokens"] = total_tokens
        job["avg_tokens_per_call"] = round(total_tokens / tokened, 1) if tokened else None
        results.append(job)

    results.sort(key=lambda j: j["total_calls"], reverse=True)
    return results


def render_text(results: list[dict], since_iso: str, until_label: str) -> str:
    if not results:
        return f"No LLM calls logged between {since_iso} and {until_label}."

    lines = [f"LLM usage report: {since_iso} to {until_label}", ""]
    for job in results:
        models = ", ".join(f"{m}×{c}" for m, c in sorted(job["calls_by_model"].items(), key=lambda kv: -kv[1]))
        avg = job["avg_tokens_per_call"]
        avg_str = f"{avg:.1f}" if avg is not None else "n/a"
        lines.append(
            f"{job['job_name']}: {job['total_calls']} calls, {job['error_count']} errors, "
            f"{job['total_tokens']} total tokens (avg {avg_str}/call) — {models}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"Trailing days to report on (default {DEFAULT_DAYS}).")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of plain text.")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    since_iso = since.strftime("%Y-%m-%d %H:%M:%S")
    results = collect(since_iso)

    if args.json:
        print(json.dumps({"since": since_iso, "days": args.days, "jobs": results}, indent=2))
    else:
        print(render_text(results, since_iso, "now"))


if __name__ == "__main__":
    sys.exit(main())
