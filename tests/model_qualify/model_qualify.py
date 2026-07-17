#!/usr/bin/env python3
"""
Watson — Ollama Model Qualification Harness
Run directly on the Beelink (needs local Ollama at localhost:11434).

Usage:
    python3 model_qualify.py --test-set test_set.json --out results.json

Requires: requests (pip install requests --break-system-packages if not present)
"""

import argparse
import json
import time
from datetime import datetime

import requests

MODELS = ["llama3.2:3b", "qwen2.5-coder:7b", "phi3:mini", "gemma3:1b"]

OLLAMA_URL = "http://localhost:11434/api/generate"

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a Telegram bot. Given a message, output ONLY one of these labels, nothing else:
reminder, kb_search, congregation_query, watson_db_query, polish, calendar_query, clear_day,
qr_generate, contacts_lookup, bible_lookup, web_search, vacation_gate, gutenberg, classics,
imagegen, general_chat, AMBIGUOUS_multi_intent

Message: {prompt}
Label:"""


def call_ollama(model: str, prompt: str) -> tuple[str, float, dict]:
    start = time.time()
    resp = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    elapsed = time.time() - start
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "").strip(), elapsed, data


def run_battery_a(model: str, cases: list) -> dict:
    results = []
    correct = 0
    total_latency = 0.0
    for case in cases:
        prompt = INTENT_SYSTEM_PROMPT.format(prompt=case["prompt"])
        response, elapsed, raw = call_ollama(model, prompt)
        is_correct = case["expected"].lower() in response.lower()
        if is_correct:
            correct += 1
        total_latency += elapsed
        eval_count = raw.get("eval_count", 0)
        eval_duration = raw.get("eval_duration", 1) / 1e9  # ns -> s
        tokens_per_sec = eval_count / eval_duration if eval_duration > 0 else None
        results.append({
            "id": case["id"],
            "prompt": case["prompt"],
            "expected": case["expected"],
            "actual": response,
            "correct": is_correct,
            "latency_sec": round(elapsed, 3),
            "tokens_per_sec": round(tokens_per_sec, 1) if tokens_per_sec else None,
        })
    return {
        "accuracy": round(correct / len(cases), 3) if cases else None,
        "avg_latency_sec": round(total_latency / len(cases), 3) if cases else None,
        "cases": results,
    }


def run_battery_b(model: str, cases: list) -> dict:
    results = []
    for case in cases:
        response, elapsed, raw = call_ollama(model, case["prompt"])
        results.append({
            "id": case["id"],
            "type": case["type"],
            "prompt": case["prompt"],
            "notes": case.get("notes", ""),
            "response": response,
            "latency_sec": round(elapsed, 3),
            "_grade_correctness": None,
            "_grade_completeness": None,
            "_grade_format": None,
            "_grade_hallucination_pass": None,
        })
    return {"cases": results, "grading": "MANUAL — fill in _grade_* fields, or paste into Claude for scoring"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-set", default="test_set.json")
    parser.add_argument("--out", default=None)
    parser.add_argument("--models", nargs="+", default=MODELS)
    args = parser.parse_args()

    with open(args.test_set) as f:
        test_set = json.load(f)

    out_path = args.out or f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    all_results = {}

    for model in args.models:
        print(f"\n=== Testing {model} ===")
        print("Battery A (intent classification)...")
        battery_a = run_battery_a(model, test_set["battery_a_intent"])
        print(f"  Accuracy: {battery_a['accuracy']}  Avg latency: {battery_a['avg_latency_sec']}s")

        print("Battery B (reasoning)...")
        battery_b = run_battery_b(model, test_set["battery_b_reasoning"])
        print(f"  {len(battery_b['cases'])} cases logged for manual grading")

        all_results[model] = {
            "battery_a_intent": battery_a,
            "battery_b_reasoning": battery_b,
        }

    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\nResults written to {out_path}")
    print("Next: review Battery B responses manually or paste results_*.json into Claude for grading.")


if __name__ == "__main__":
    main()
