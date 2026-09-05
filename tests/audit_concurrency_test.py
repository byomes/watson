"""
Production-safety concurrency test for jobs/skillbuilder/audit.py
(bug_tracker #118/#121): does the real, now-fixed
audit._call_ollama_batched() (compact + batched + context-chained, wrapped
in core.ollama_lock.heavy_ollama_call()) starve the live Telegram intent
classifier the way qwen2.5:14b did in the original 2026-07-16 incident?

History:
- The first version of this test (before the #121 lock fix) called a
  hand-built duplicate of audit.py's request rather than the real function,
  and reproduced the incident exactly (classify() hit its 55s timeout and
  silently returned the wrong intent while the real ~9.5k-token call ran
  for 734-975s — see bug_tracker #118's update log).
- The second version called the real (then-single-call) _call_ollama()
  directly and confirmed the #121 lock fix: classify() returned the honest
  busy signal instead of guessing.
- This version calls the real _call_ollama_batched() (the #121 Part 2
  restructure — compact skill format + true batching via Ollama context
  chaining, ~53-65% faster in isolation: 252-348s vs. the original
  734-975s), so it's the final pass/fail bar for the complete fix: the
  lock must still hold correctly over the WHOLE batch+synthesis chain
  (not just a single call), classify() must still degrade honestly during
  it, and normal correct classification must resume once it's done.

audit.py's cron (0 7 * * 1, Monday 7am) overlaps confirmed live traffic —
jobs/intent/keep_warm.py pings gemma3:4b every 4 minutes, 24/7, so a
multi-minute audit.py run collides with keep_warm cycles regardless of time
of day, and 7am isn't in the codebase's usual 1-4am quiet-hours cluster for
heavy jobs.

Same protocol as tests/ollama_parallel_test.py (the original qwen2.5:14b
test): fire the heavy call in the background, wait 5s so it's genuinely
mid-generation, then call the real classify() and time it.

Usage: PYTHONPATH=/home/billyomes/watson python3 tests/audit_concurrency_test.py
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "/home/billyomes/watson")
from jobs.intent.classifier import classify  # noqa: E402
import jobs.skillbuilder.audit as _audit  # noqa: E402

TEST_MESSAGE = "remind me to call John tomorrow at 3pm"

_heavy_done = threading.Event()
_heavy_start = None
_heavy_end = None
_heavy_result = {}


def run_heavy_call():
    global _heavy_start, _heavy_end
    repo = Path("/home/billyomes/watson")
    memory = repo / "memory"
    skills_json = (memory / "skills.json").read_text(encoding="utf-8")
    skills_list = _audit._load_skills_list(skills_json)
    index_path = memory / "projects" / "_index.md"
    projects = index_path.read_text(encoding="utf-8")[:2000] if index_path.exists() else "(no projects)"
    relational = memory / "relational.md"
    recent_sessions = relational.read_text(encoding="utf-8")[-2000:] if relational.exists() else "(no session history)"
    research_log = repo / "logs" / "research.log"
    research_excerpt = "\n".join(research_log.read_text(encoding="utf-8").splitlines()[-100:]) if research_log.exists() else "(no research log)"
    print(f"[heavy call] real audit.py: {len(skills_list)} skills, batched+chained, real production code path")
    _heavy_start = time.monotonic()
    try:
        # Calls the actual production function, lock and all -- not a duplicate.
        response = _audit._call_ollama_batched(skills_list, projects, recent_sessions, research_excerpt)
        _heavy_result["response_len"] = len(response)
    except Exception as e:
        print(f"[heavy call] error: {e}")
    finally:
        _heavy_end = time.monotonic()
        _heavy_done.set()


def snapshot(label):
    from core.ollama_lock import ollama_busy
    print(f"\n--- {label} ---")
    print(f"ollama_busy(): {ollama_busy()}")
    out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
    print(f"$ ollama ps\n{out}")


if __name__ == "__main__":
    print("Starting background audit.py real _call_ollama() (real prompt, real production code path)...")
    t = threading.Thread(target=run_heavy_call, daemon=True)
    t.start()

    time.sleep(5)
    snapshot("loaded window (heavy call mid-generation, lock should be held)")
    print("Firing classify() while the real audit.py call is running...")

    classify_start = time.monotonic()
    result = classify(TEST_MESSAGE)
    classify_elapsed = time.monotonic() - classify_start

    print(f"\nclassify() result: {result}")
    print(f"classify() elapsed: {classify_elapsed:.2f}s")
    print(f"(heavy call still running: {not _heavy_done.is_set()})")

    pass_during = result.get("intent") == "busy" and classify_elapsed < 5.0
    print(f"PASS (honest busy signal, not a guess): {pass_during}")

    t.join(timeout=1250)
    heavy_elapsed = (_heavy_end - _heavy_start) if _heavy_end else None
    print(f"heavy call total elapsed: {heavy_elapsed:.2f}s" if heavy_elapsed else "heavy call did not finish in time")
    if _heavy_result:
        print(f"heavy call stats: {_heavy_result}")
    snapshot("after heavy call finished (lock should be cleared)")

    print("Firing classify() again now that the real audit.py call is done...")
    classify_start = time.monotonic()
    result_after = classify(TEST_MESSAGE)
    classify_elapsed_after = time.monotonic() - classify_start
    print(f"classify() result (after): {result_after}")
    print(f"classify() elapsed (after): {classify_elapsed_after:.2f}s")
    pass_after = result_after.get("intent") == "reminder_create"
    print(f"PASS (normal correct classification resumed): {pass_after}")

    print(f"\n=== OVERALL: {'PASS' if (pass_during and pass_after) else 'FAIL'} ===")
