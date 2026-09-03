"""
Candidate-model concurrency qualification harness (2026-09-03 model
benchmark, memory/model_benchmark_20260903.md). Same protocol as
tests/ollama_parallel_test.py (WATSON_ARCHITECTURE.md, Hardware ->
FMSPC/Ollama, qwen2.5:14b incident) but parameterized by model, so
candidates other than qwen2.5:14b can be run through the identical test
without touching the original harness. Not wired into any job or cron.

Fires a long-running background generate call on the candidate model,
waits 5s for it to be genuinely mid-generation, then calls the real
jobs/intent/classifier.classify() (gemma3:4b, production code path) and
times it. Captures the full classify() JSON so a wrong-but-fast answer
is caught, not just a slow one. Also snapshots `ollama ps` and `free -h`
during the loaded window.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 tests/ollama_parallel_candidate_test.py <model> [--think=false] [--baseline]

  <model>        model tag for the heavy background call (e.g. qwen3:8b, phi4:14b)
  --think=false  set think:false in the heavy call payload (qwen3-family
                  hybrid-reasoning models default to thinking mode ON)
  --baseline     skip the heavy background call; time classify() solo only,
                  for an unloaded gemma3:4b reference baseline
"""
import subprocess
import sys
import time
import threading
import requests

sys.path.insert(0, "/home/billyomes/watson")
from jobs.intent.classifier import classify  # noqa: E402

OLLAMA_URL = "http://localhost:11434/api/generate"
TEST_MESSAGE = "remind me to call John tomorrow at 3pm"

_heavy_done = threading.Event()
_heavy_start = None
_heavy_end = None
_heavy_payload = None
_heavy_eval = None


def run_heavy_call(model, think_false):
    global _heavy_start, _heavy_end, _heavy_payload, _heavy_eval
    payload = {
        "model": model,
        "prompt": "Write a detailed 800-word essay on the history and theology of covenant in the Old Testament, covering Noah, Abraham, Moses, and David.",
        "stream": False,
        "options": {"num_predict": 700},
    }
    if think_false:
        payload["think"] = False
    _heavy_payload = payload
    _heavy_start = time.monotonic()
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        _heavy_eval = {
            "eval_count": data.get("eval_count"),
            "eval_duration_s": (data.get("eval_duration") or 0) / 1e9,
            "total_duration_s": (data.get("total_duration") or 0) / 1e9,
            "load_duration_s": (data.get("load_duration") or 0) / 1e9,
        }
    except Exception as e:
        print(f"[heavy call] error: {e}")
    finally:
        _heavy_end = time.monotonic()
        _heavy_done.set()


def snapshot(label):
    print(f"\n--- {label} ---")
    for cmd in (["ollama", "ps"], ["free", "-h"]):
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        print(f"$ {' '.join(cmd)}\n{out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    baseline = "--baseline" in args
    think_false = "--think=false" in args
    positional = [a for a in args if not a.startswith("--")]
    model = positional[0] if positional else None

    if not baseline and not model:
        print("usage: ollama_parallel_candidate_test.py <model> [--think=false] [--baseline]")
        sys.exit(1)

    if not baseline:
        print(f"Starting background {model} call (think=false: {think_false})...")
        t = threading.Thread(target=run_heavy_call, args=(model, think_false), daemon=True)
        t.start()
        time.sleep(5)
        snapshot(f"loaded window ({model} mid-generation)")
        print("Heavy call should be mid-generation now. Firing classify()...")
    else:
        print("Baseline mode: no background call. Firing classify() solo...")

    classify_start = time.monotonic()
    result = classify(TEST_MESSAGE)
    classify_elapsed = time.monotonic() - classify_start

    print(f"\nclassify() result: {result}")
    print(f"classify() elapsed: {classify_elapsed:.2f}s")

    if not baseline:
        print(f"(heavy call still running: {not _heavy_done.is_set()})")
        t.join(timeout=300)
        heavy_elapsed = (_heavy_end - _heavy_start) if _heavy_end else None
        print(f"heavy {model} call total elapsed: {heavy_elapsed:.2f}s" if heavy_elapsed else "heavy call did not finish in time")
        if _heavy_eval:
            print(f"heavy call eval stats: {_heavy_eval}")
        snapshot("after heavy call finished")
