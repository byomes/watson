"""
loop.py — Watson Dev Loop: autonomous Ollama-powered code generation.

Runs locally on the Beelink. Invoked by trigger.py via subprocess.Popen.

Args:
    --slug            Project slug
    --input-type      'description' or 'spec'
    --input-b64       Base64-encoded task input
    --watson-url      Watson API base URL
    --start-iteration Iteration to start from (default: 1)
    --extend-by       Extra iterations beyond default max (default: 0)
    --feedback-b64    Optional base64-encoded feedback from a prior run
"""
import argparse
import base64
import os
import re
import subprocess
import sys
import tempfile

import requests

from jobs.memory.prompt_builder import build_prompt

def _load_env():
    env_file = os.path.expanduser("~/watson/.env")
    if not os.path.exists(env_file):
        return
    for line in open(env_file):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()
_load_env()

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5-coder:7b"
MAX_ITERATIONS_DEFAULT = 3


def _callback(watson_url: str, payload: dict) -> None:
    api_key = os.getenv("WRITING_ROOM_API_KEY", "")
    try:
        requests.post(
            f"{watson_url}/api/dev-loop/callback",
            json=payload,
            headers={"X-Watson-Key": api_key},
            timeout=30,
        )
    except Exception as e:
        print(f"[loop] Callback failed: {e}", file=sys.stderr)


def _ollama_generate(prompt: str, system: str = "") -> str:
    payload = {"model": MODEL, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _test_code(code: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        tmp = f.name
    try:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", tmp],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return {"ok": False, "errors": [r.stderr.strip() or "Syntax error"]}
        return {"ok": True, "errors": []}
    finally:
        os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--input-type", required=True, dest="input_type")
    parser.add_argument("--input-b64", required=True, dest="input_b64")
    parser.add_argument("--watson-url", required=True, dest="watson_url")
    parser.add_argument("--start-iteration", type=int, default=1, dest="start_iteration")
    parser.add_argument("--extend-by", type=int, default=0, dest="extend_by")
    parser.add_argument("--feedback-b64", default="", dest="feedback_b64")
    args = parser.parse_args()

    input_text = base64.b64decode(args.input_b64).decode("utf-8")
    feedback = base64.b64decode(args.feedback_b64).decode("utf-8") if args.feedback_b64 else ""
    max_iterations = MAX_ITERATIONS_DEFAULT + args.extend_by

    spec = input_text if args.input_type == "spec" else ""
    code = ""
    iteration_history = []
    test_results = {}

    system_prompt = build_prompt(task=input_text, project="dev_loop")

    for iteration in range(args.start_iteration, max_iterations + 1):
        print(f"[loop] Iteration {iteration}/{max_iterations} — slug={args.slug}")

        if args.input_type == "spec":
            prompt_core = f"Implement exactly this specification:\n\n{input_text}"
        else:
            prompt_core = f"Write a Python script that does the following:\n\n{input_text}"

        prior = ""
        if iteration_history:
            last = iteration_history[-1]
            errs = "\n".join(last.get("errors", []))
            prior = f"\n\nPrevious attempt failed with:\n{errs}\n\nFix the issues and rewrite the full script."

        if feedback:
            prior += f"\n\nAdditional feedback:\n{feedback}"

        prompt = (
            f"{prompt_core}{prior}\n\n"
            "Respond with ONLY the complete Python script in a ```python code block. "
            "No explanation. No extra text."
        )

        raw = _ollama_generate(prompt, system=system_prompt)
        code = _extract_code(raw)

        test_results = _test_code(code)
        iteration_history.append({
            "iteration": iteration,
            "ok": test_results["ok"],
            "errors": test_results.get("errors", []),
        })

        if test_results["ok"]:
            print(f"[loop] Tests passed at iteration {iteration}. Delivering.")
            _callback(args.watson_url, {
                "slug": args.slug,
                "status": "delivered",
                "code": code,
                "spec": spec,
                "iteration": iteration,
                "test_results": test_results,
                "iteration_history": iteration_history,
            })
            return

    print(f"[loop] Max iterations ({max_iterations}) reached without passing. Sending paused.")
    _callback(args.watson_url, {
        "slug": args.slug,
        "status": "paused",
        "code": code,
        "spec": spec,
        "iteration": max_iterations,
        "test_results": test_results,
        "iteration_history": iteration_history,
    })


if __name__ == "__main__":
    main()
