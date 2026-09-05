"""
Verification test for the core/ollama_lock.py fix (bug_tracker #118/#121).

Re-runs the same real-world scenario as tests/audit_concurrency_test.py
(a real audit.py-shaped ~9.5k-token qwen2.5:7b call, running for real,
overlapping a live classify() call) but this time the heavy call is wrapped
in `heavy_ollama_call()`, same as the committed jobs/skillbuilder/audit.py
fix. Pass bar: classify() must return the honest busy signal (not a wrong
intent, not a silent timeout) while the lock is held, and normal correct
classify() behavior must resume immediately once the lock clears.

Usage: PYTHONPATH=/home/billyomes/watson python3 tests/ollama_lock_concurrency_test.py
"""
import subprocess
import sys
import threading
import time

import requests

sys.path.insert(0, "/home/billyomes/watson")
from jobs.intent.classifier import classify  # noqa: E402
from jobs.llm.compare_reasoning import build_skill_audit_prompt  # noqa: E402
from core.ollama_context import size_num_ctx  # noqa: E402
from core.ollama_lock import heavy_ollama_call, ollama_busy, LOCK_PATH  # noqa: E402

OLLAMA_URL = "http://localhost:11434/api/generate"
TEST_MESSAGE = "remind me to call John tomorrow at 3pm"

_heavy_done = threading.Event()
_heavy_start = None
_heavy_end = None
_heavy_result = {}


def run_heavy_call():
    global _heavy_start, _heavy_end
    system, user, model, label = build_skill_audit_prompt()
    prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}"
    num_ctx = size_num_ctx(prompt)
    print(f"[heavy call] real audit.py prompt: {len(prompt)} chars, num_ctx={num_ctx}, model={model}")
    _heavy_start = time.monotonic()
    with heavy_ollama_call("skillbuilder.audit"):
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": model, "prompt": prompt, "stream": False, "options": {"num_ctx": num_ctx}},
                timeout=1200,
            )
            resp.raise_for_status()
            data = resp.json()
            _heavy_result["prompt_eval_count"] = data.get("prompt_eval_count")
        except Exception as e:
            print(f"[heavy call] error: {e}")
        finally:
            _heavy_end = time.monotonic()
            _heavy_done.set()


def snapshot(label):
    print(f"\n--- {label} ---")
    print(f"LOCK_PATH exists: {LOCK_PATH.exists()}, ollama_busy(): {ollama_busy()}")
    out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
    print(f"$ ollama ps\n{out}")


if __name__ == "__main__":
    print("Starting background audit.py-shaped qwen2.5:7b call, wrapped in heavy_ollama_call()...")
    t = threading.Thread(target=run_heavy_call, daemon=True)
    t.start()

    time.sleep(5)
    snapshot("loaded window (heavy call mid-generation, lock should be held)")
    print("Firing classify() while lock is held — expect the honest busy signal, not a guess...")

    classify_start = time.monotonic()
    result_during = classify(TEST_MESSAGE)
    classify_elapsed_during = time.monotonic() - classify_start
    print(f"classify() result (during lock): {result_during}")
    print(f"classify() elapsed (during lock): {classify_elapsed_during:.2f}s")

    pass_during = (
        result_during.get("intent") == "busy"
        and classify_elapsed_during < 5.0  # should short-circuit almost instantly, no Ollama round trip
    )
    print(f"PASS (busy signal, fast, no guess): {pass_during}")

    print("\nWaiting for heavy call to finish so we can confirm the lock clears...")
    t.join(timeout=1250)
    heavy_elapsed = (_heavy_end - _heavy_start) if _heavy_end else None
    print(f"heavy call total elapsed: {heavy_elapsed:.2f}s" if heavy_elapsed else "heavy call did not finish in time")
    if _heavy_result:
        print(f"heavy call stats: {_heavy_result}")

    snapshot("after heavy call finished (lock should be cleared)")

    print("Firing classify() again now that the lock should be clear — expect normal correct behavior...")
    classify_start = time.monotonic()
    result_after = classify(TEST_MESSAGE)
    classify_elapsed_after = time.monotonic() - classify_start
    print(f"classify() result (after lock cleared): {result_after}")
    print(f"classify() elapsed (after lock cleared): {classify_elapsed_after:.2f}s")

    pass_after = result_after.get("intent") == "reminder_create"
    print(f"PASS (normal correct classification resumed): {pass_after}")

    print(f"\n=== OVERALL: {'PASS' if (pass_during and pass_after) else 'FAIL'} ===")
