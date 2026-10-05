"""jobs/llm/candidate_sweep.py — one-off driver that loops
jobs/llm/compare_reasoning.py across multiple candidate models and the two
hardest real job prompt types (skill_audit, state_of_church — per
memory/project_ollama_model_routing.md 2026-09-03 notes, memory_consolidation
is too easy to differentiate candidates on, both prior models passed it
clean). Local Ollama calls only (http://localhost:11434) — no Claude/Anthropic
API calls anywhere in this path.

Not wired into any cron — run once, by hand, on the Beelink. Writes a plain
summary to stdout and to memory/reasoning_comparisons/_sweep_summary.txt;
the full per-run comparison docs (with fabrication-check reviewer
placeholders, still needing a human/Claude read) land in
memory/reasoning_comparisons/ exactly as compare_reasoning.py always does.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 -m jobs.llm.candidate_sweep
"""
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/home/billyomes/watson")
OUT_DIR = REPO / "memory" / "reasoning_comparisons"
SUMMARY_PATH = OUT_DIR / "_sweep_summary.txt"

CANDIDATES = ["qwen3.5:4b", "granite4.2:3b", "granite4.2:8b"]
JOBS = ["skill_audit", "state_of_church"]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"Candidate sweep started {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]

    for candidate in CANDIDATES:
        for job in JOBS:
            cmd = [
                sys.executable, "-m", "jobs.llm.compare_reasoning",
                "--job", job,
                "--candidate", candidate,
                "--candidate-think", "false",
            ]
            print(f"\n=== {candidate} vs baseline on {job} ===", flush=True)
            t0 = time.monotonic()
            try:
                result = subprocess.run(
                    cmd, cwd=str(REPO), capture_output=True, text=True, timeout=2700
                )
                elapsed = round(time.monotonic() - t0, 1)
                out_path = None
                for line in result.stdout.splitlines():
                    if line.startswith("Written to "):
                        out_path = line[len("Written to "):].strip()
                status = "ok" if result.returncode == 0 and out_path else "FAILED"
                detail = out_path or result.stderr[-300:]
            except subprocess.TimeoutExpired:
                elapsed = round(time.monotonic() - t0, 1)
                status = "TIMEOUT"
                detail = "exceeded 2700s wrapper timeout"
            summary_line = f"{candidate:16s} {job:16s} {status:8s} {elapsed:7.1f}s  {detail}"
            print(summary_line, flush=True)
            lines.append(summary_line)

    lines.append("")
    lines.append(f"Sweep finished {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("No automated grading. Each output file above still needs a human/Claude")
    lines.append("fabrication-check read (see compare_reasoning.py docstring) before any")
    lines.append("routing decision is made.")
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSummary written to {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
