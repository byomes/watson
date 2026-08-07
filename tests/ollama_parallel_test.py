"""
One-off manual test harness for the OLLAMA_NUM_PARALLEL investigation
(WATSON_ARCHITECTURE.md, Hardware -> FMSPC/Ollama). Not wired into any
job or cron. Fires a long-running qwen2.5:14b generate call in the
background, waits briefly for it to actually be mid-generation, then
calls the real jobs/intent/classifier.classify() and times it.

Usage: PYTHONPATH=/home/billyomes/watson python3 tests/ollama_parallel_test.py
"""
import sys
import time
import threading
import requests

sys.path.insert(0, "/home/billyomes/watson")
from jobs.intent.classifier import classify  # noqa: E402

OLLAMA_URL = "http://localhost:11434/api/generate"
HEAVY_MODEL = "qwen2.5:14b"
TEST_MESSAGE = "remind me to call John tomorrow at 3pm"

_heavy_done = threading.Event()
_heavy_start = None
_heavy_end = None


def run_heavy_call():
    global _heavy_start, _heavy_end
    _heavy_start = time.monotonic()
    try:
        requests.post(
            OLLAMA_URL,
            json={
                "model": HEAVY_MODEL,
                "prompt": "Write a detailed 800-word essay on the history and theology of covenant in the Old Testament, covering Noah, Abraham, Moses, and David.",
                "stream": False,
                "options": {"num_predict": 700},
            },
            timeout=300,
        )
    except Exception as e:
        print(f"[heavy call] error: {e}")
    finally:
        _heavy_end = time.monotonic()
        _heavy_done.set()


if __name__ == "__main__":
    print(f"Starting background {HEAVY_MODEL} call...")
    t = threading.Thread(target=run_heavy_call, daemon=True)
    t.start()

    # give it a few seconds to actually start generating, not just queue
    time.sleep(5)
    print("Heavy call should be mid-generation now. Firing classify()...")

    classify_start = time.monotonic()
    result = classify(TEST_MESSAGE)
    classify_elapsed = time.monotonic() - classify_start

    print(f"\nclassify() result: {result}")
    print(f"classify() elapsed: {classify_elapsed:.2f}s")
    print(f"(heavy call still running: {not _heavy_done.is_set()})")

    # wait for heavy call to finish so we can report its total time too
    t.join(timeout=300)
    heavy_elapsed = (_heavy_end - _heavy_start) if _heavy_end else None
    print(f"heavy qwen2.5:14b call total elapsed: {heavy_elapsed:.2f}s" if heavy_elapsed else "heavy call did not finish in time")
