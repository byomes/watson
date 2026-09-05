"""
Candidate-model concurrency + eviction/thrash qualification harness
(2026-09-03 model benchmark, memory/model_benchmark_20260903.md). Same
protocol as tests/ollama_parallel_test.py (WATSON_ARCHITECTURE.md,
Hardware -> FMSPC/Ollama, qwen2.5:14b incident) but parameterized by
model, so candidates other than qwen2.5:14b can be run through the
identical test without touching the original harness. Not wired into
any job or cron.

Two modes:

1. Single-candidate concurrency stress test (original mode). Fires a
   long-running background generate call on the candidate model, waits
   5s for it to be genuinely mid-generation, then calls the real
   jobs/intent/classifier.classify() (gemma3:4b, production code path)
   and times it. Captures the full classify() JSON so a wrong-but-fast
   answer is caught, not just a slow one. Also snapshots `ollama ps` and
   `free -h` during the loaded window.

   Usage:
     PYTHONPATH=/home/billyomes/watson python3 tests/ollama_parallel_candidate_test.py <model> [--think=false] [--baseline]

     <model>        model tag for the heavy background call (e.g. qwen3:8b, phi4:14b)
     --think=false  set think:false in the heavy call payload (qwen3-family
                     hybrid-reasoning models default to thinking mode ON)
     --baseline     skip the heavy background call; time classify() solo only,
                     for an unloaded gemma3:4b reference baseline

2. Mixed-traffic eviction/thrash test (different failure mode from #1 —
   isolated CPU contention during one generate call doesn't prove a
   candidate is safe to add to the resident-model rotation under
   OLLAMA_MAX_LOADED_MODELS=3; a model that forces frequent evict/reload
   cycling against the existing rotation can still hurt even if it never
   wins the isolated stress test). Fires realistic, production-shaped
   calls (real classify() calls against gemma3:4b, a real dashboard-style
   llama3.2:3b chat-summarize call, a real jobs.ask KB-search call against
   qwen2.5-coder:7b, and a real jobs.memory.reflect-shaped background call
   against qwen2.5:7b) on a fixed schedule over a window, with the
   candidate model's own calls either present or absent so the same
   schedule can be run twice (--with-candidate vs without) for an
   apples-to-apples before/after comparison. Samples `ollama ps` every
   20s throughout and flags any call whose `load_duration` implies an
   evict-and-reload (not just the model's first appearance in the run).

   Usage:
     PYTHONPATH=/home/billyomes/watson python3 tests/ollama_parallel_candidate_test.py --mixed-traffic [--with-candidate] [--candidate=qwen3:8b] [--duration=1200]
"""
import argparse
import subprocess
import sys
import threading
import time

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


# ─── Mixed-traffic eviction/thrash mode ─────────────────────────────────────

_results_lock = threading.Lock()
_call_log = []  # list of dicts: {t, kind, model, elapsed_s, load_duration_s, ok, note}
_ps_log = []    # list of (t, resident_model_names)


def _post(model, prompt, think=None, num_predict=200, timeout=120):
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": num_predict}}
    if think is not None:
        payload["think"] = think
    t0 = time.monotonic()
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.monotonic() - t0
        return elapsed, (data.get("load_duration") or 0) / 1e9, True, None
    except Exception as e:
        return time.monotonic() - t0, None, False, str(e)


def _fire(kind, model, fn, t_offset):
    elapsed, load_dur, ok, err = fn()
    with _results_lock:
        _call_log.append({
            "t": t_offset, "kind": kind, "model": model,
            "elapsed_s": round(elapsed, 2),
            "load_duration_s": round(load_dur, 2) if load_dur is not None else None,
            "ok": ok, "note": err,
        })
    tag = "OK" if ok else f"ERROR: {err}"
    print(f"[t+{t_offset}s] {kind} ({model}) done in {elapsed:.2f}s, load_duration={load_dur}, {tag}")


def _call_classify():
    t0 = time.monotonic()
    try:
        result = classify(TEST_MESSAGE)
        return time.monotonic() - t0, 0.0, "intent" in result, None
    except Exception as e:
        return time.monotonic() - t0, None, False, str(e)


def _call_chat_summarize():
    # Same prompt template as jobs/dashboard/app.py's /api/chat/summarize (llama3.2:3b).
    convo = (
        "User: What was the attendance in the toddler class last Sunday\n"
        "Assistant: Toddlers on 2026-08-30: 1 kid and 2 workers.\n"
        "User: When was the last time Emily Yomes came to church\n"
        "Assistant: Emily Yomes was last seen on 2026-08-30.\n"
        "User: How many Instagram followers do we have?\n"
        "Assistant: Instagram Followers for 2026-06: 370"
    )
    prompt = (
        "Summarize this conversation in 3-5 sentences. Focus on topics discussed, decisions made, "
        "tasks mentioned, and anything Dr. Bill said about himself, his ministry, or his plans. "
        "Be specific and factual. No preamble. "
        "Important: The person in this conversation is Dr. William C.K. Yomes. Use his full name accurately. Do not substitute or confuse him with any other person.\n\n" + convo
    )
    return _post("llama3.2:3b", prompt, num_predict=150)


def _call_kb_search():
    # Same shape as jobs/ask.py's search()+synthesize() (qwen2.5-coder:7b), done as a
    # raw request here so load_duration is captured (jobs.ask.synthesize returns text only).
    from jobs.ask import search as kb_search
    chunks = kb_search("What does Pastor Bill teach about covenant?", k=3)
    context = "".join(f"--- From: {c['title']} ---\n{c['text']}\n\n" for c in chunks)
    prompt = (
        "You are a helpful assistant with access to sermon transcripts from Pastor Bill Yomes. "
        "Answer the following question using only the provided sermon excerpts. Be specific and "
        "reference which sermons your answer draws from.\n\nQuestion: What does Pastor Bill teach "
        "about covenant?\n\nSermon excerpts:\n" + context + "\n\nAnswer:"
    )
    return _post("qwen2.5-coder:7b", prompt, num_predict=250, timeout=240)


def _reflect_prompt():
    # Same system+transcript shape as jobs/memory/reflect.py's reflect() (session 195, real).
    from jobs.memory.reflect import _load_messages, _format_transcript
    messages = _load_messages(195, limit=20)
    transcript = _format_transcript(messages) if messages else (
        "Bill: What was the attendance in the toddler class last Sunday\n"
        "Watson: Toddlers on 2026-08-30: 1 kid and 2 workers."
    )
    system = (
        "You are Watson's memory system. Summarize the conversation below concisely. Extract:\n"
        "1. What was discussed or worked on\n"
        "2. Any decisions made\n"
        "3. Any next steps identified\n"
        "4. Anything worth remembering long-term\n\n"
        "Be brief. 3-5 sentences maximum. Write in past tense. "
        "Do not invent dates, context, or content not present in the transcript."
    )
    return f"{system}\n\n{transcript}"


def _call_background_qwen7b():
    return _post("qwen2.5:7b", _reflect_prompt(), num_predict=200, timeout=120)


def _call_candidate(model):
    def _fn():
        return _post(model, _reflect_prompt(), think=False, num_predict=200, timeout=120)
    return _fn


def _monitor_ps(stop_event, start_time, interval=20):
    while not stop_event.is_set():
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
        names = [line.split()[0] for line in out.strip().splitlines()[1:] if line.strip()]
        with _results_lock:
            _ps_log.append((round(time.monotonic() - start_time), names))
        stop_event.wait(interval)


def run_mixed_traffic(duration, with_candidate, candidate_model):
    print(f"Mixed-traffic test: duration={duration}s, with_candidate={with_candidate} ({candidate_model if with_candidate else 'n/a'})")
    start = time.monotonic()

    stop_event = threading.Event()
    mon = threading.Thread(target=_monitor_ps, args=(stop_event, start), daemon=True)
    mon.start()

    schedule = []  # (t_offset, kind, model, fn)
    for t in range(0, duration, 90):
        schedule.append((t, "classify", "gemma3:4b", _call_classify))
    for t in range(60, duration, 240):
        schedule.append((t, "chat_summarize", "llama3.2:3b", _call_chat_summarize))
    for t in range(150, duration, 300):
        schedule.append((t, "kb_search", "qwen2.5-coder:7b", _call_kb_search))
    for t in range(200, duration, 400):
        schedule.append((t, "background_qwen7b", "qwen2.5:7b", _call_background_qwen7b))
    if with_candidate:
        for t in range(100, duration, 300):
            schedule.append((t, "candidate", candidate_model, _call_candidate(candidate_model)))

    schedule.sort(key=lambda e: e[0])
    print(f"Schedule: {len(schedule)} calls over {duration}s")

    threads = []
    for t_offset, kind, model, fn in schedule:
        now = time.monotonic() - start
        wait = t_offset - now
        if wait > 0:
            time.sleep(wait)
        th = threading.Thread(target=_fire, args=(kind, model, fn, t_offset), daemon=True)
        th.start()
        threads.append(th)

    for th in threads:
        th.join(timeout=180)

    stop_event.set()
    mon.join(timeout=5)

    print("\n=== Call log ===")
    for c in _call_log:
        print(c)

    print("\n=== ollama ps timeline ===")
    for t_offset, names in _ps_log:
        print(f"t+{t_offset}s: {names}")

    print("\n=== Reload analysis (per model, calls after the first with load_duration > 1.0s) ===")
    seen = {}
    for c in _call_log:
        m = c["model"]
        is_first = m not in seen
        seen[m] = True
        if not is_first and c["load_duration_s"] is not None and c["load_duration_s"] > 1.0:
            print(f"RELOAD at t+{c['t']}s: {m} ({c['kind']}) load_duration={c['load_duration_s']}s")

    return _call_log, _ps_log


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("model", nargs="?", default=None)
    parser.add_argument("--think=false", dest="think_false", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--mixed-traffic", action="store_true")
    parser.add_argument("--with-candidate", action="store_true")
    parser.add_argument("--candidate", default="qwen3:8b")
    parser.add_argument("--duration", type=int, default=1200)
    ns = parser.parse_args()

    if ns.mixed_traffic:
        run_mixed_traffic(ns.duration, ns.with_candidate, ns.candidate)
        sys.exit(0)

    baseline = ns.baseline
    think_false = ns.think_false
    model = ns.model

    if not baseline and not model:
        print("usage: ollama_parallel_candidate_test.py <model> [--think=false] [--baseline] | --mixed-traffic [--with-candidate] [--candidate=MODEL] [--duration=SECONDS]")
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
