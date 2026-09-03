"""jobs/llm/compare_reasoning.py — side-by-side output-quality comparison
harness for a reasoning-model candidate against Watson's current production
model on a real, recent Watson job prompt.

This does NOT grade anything automatically. It pulls a real prompt from one
of Watson's actual reason-heavy jobs (memory_consolidation / skill_audit /
state_of_church), runs it through the current production model and a
candidate model, and writes both full outputs side by side to
memory/reasoning_comparisons/ for a human to read and judge directly.

The written doc's judging protocol puts a fabrication check FIRST for each
candidate, before any quality/completeness commentary — every factual claim
must be cross-referenced against the source data, and any claim not directly
grounded there disqualifies that output outright, per
WATSON_ARCHITECTURE.md's "No hallucination" principle. The script itself
only scaffolds this structure (reviewer placeholders); it does not attempt
to auto-detect fabrication — that still requires a human (or Claude) reading
both outputs against the real source data, same "no automated grading" rule
as before, just structurally enforced as a gate instead of left implicit.

Not wired into any job or cron — a one-off qualification tool, run manually.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 -m jobs.llm.compare_reasoning \
      --job memory_consolidation --candidate qwen3:8b --candidate-think=false

  --job              which job's real prompt to pull: memory_consolidation
                      (default), skill_audit, or state_of_church
  --session-id       for memory_consolidation only: session id to pull the
                      real transcript from (default: most recent session
                      with >=3 messages)
  --baseline-model   current production model for this job (default: the
                      job's own OLLAMA_MODEL, e.g. qwen2.5:7b)
  --candidate        candidate model to compare against (default: qwen3:8b)
  --candidate-think  think mode for the candidate: "false" (default, required
                      for qwen3-family models per the 2026-09-03 concurrency
                      test) or "true"
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, "/home/billyomes/watson")

REPO = Path("/home/billyomes/watson")
DB_PATH = REPO / "data" / "watson.db"
OUT_DIR = REPO / "memory" / "reasoning_comparisons"
OLLAMA_URL = "http://localhost:11434/api/generate"


def _most_recent_session_with_messages(min_messages=3):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT session_id, COUNT(*) as n, MAX(created_at) as last "
        "FROM chat_messages GROUP BY session_id HAVING n >= ? ORDER BY last DESC LIMIT 1",
        (min_messages,),
    ).fetchall()
    conn.close()
    return rows[0]["session_id"] if rows else None


def build_memory_consolidation_prompt(session_id=None):
    """Real jobs/memory/reflect.py prompt shape — actual production system
    prompt + a real transcript pulled from watson.db (not synthetic).

    Loads the transcript ordered by `id` rather than calling reflect.py's own
    _load_messages() (which orders by `created_at` alone). That function has
    a live bug (bug_tracker #117, logged 2026-09-03, open, not fixed —
    out of scope for this comparison, which changes no job's behavior):
    same-second user/assistant timestamp ties leave SQLite's ORDER BY
    unspecified, so pairs can come back scrambled. Confirmed on this exact
    session's data. `id` is a reliable insertion-order key so this local
    fetch avoids feeding the comparison a broken transcript, without
    touching reflect.py itself. Uses reflect.py's real _format_transcript()
    unchanged — only the message fetch/ordering is done locally."""
    from jobs.memory.reflect import _format_transcript

    if session_id is None:
        session_id = _most_recent_session_with_messages()
    if session_id is None:
        raise RuntimeError("No session with >=3 messages found in watson.db")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT 20",
        (session_id,),
    ).fetchall()
    conn.close()
    messages = [dict(r) for r in reversed(rows)]
    transcript = _format_transcript(messages)
    system = (
        "You are Watson's memory system. Summarize the conversation below concisely. Extract:\n"
        "1. What was discussed or worked on\n"
        "2. Any decisions made\n"
        "3. Any next steps identified\n"
        "4. Anything worth remembering long-term\n\n"
        "Be brief. 3-5 sentences maximum. Write in past tense. "
        "Do not invent dates, context, or content not present in the transcript."
    )
    label = f"memory_consolidation (jobs/memory/reflect.py), session {session_id}"
    return system, transcript, "qwen2.5:7b", label


def build_skill_audit_prompt():
    """Real jobs/skillbuilder/audit.py run_audit() capability-gap prompt —
    identical system prompt and real current inputs (memory/skills.json,
    memory/projects/_index.md, memory/relational.md, logs/research.log)."""
    memory = REPO / "memory"
    skills_file = memory / "skills.json"
    skills_json = skills_file.read_text(encoding="utf-8") if skills_file.exists() else "[]"

    index_path = memory / "projects" / "_index.md"
    projects = index_path.read_text(encoding="utf-8")[:2000] if index_path.exists() else "(no projects)"

    relational = memory / "relational.md"
    recent_sessions = relational.read_text(encoding="utf-8")[-2000:] if relational.exists() else "(no session history)"

    research_log = REPO / "logs" / "research.log"
    if research_log.exists():
        lines = research_log.read_text(encoding="utf-8").splitlines()
        research_excerpt = "\n".join(lines[-100:])
    else:
        research_excerpt = "(no research log)"

    system = (
        "You are Watson's capability auditor. Analyze Watson's current skills, active projects, "
        "and recent activity. Identify the top 3 capability gaps — things Bill has needed that "
        "Watson cannot do, or things that would significantly improve Watson's usefulness. "
        "For each gap, provide: gap name, why it matters, suggested job path, brief description "
        "of what to build.\n\n"
        "Format response as a JSON array:\n"
        '[{"gap": "name", "reason": "why it matters", "job_path": "jobs/category/skill_name.py", '
        '"description": "what to build"}]\n\n'
        "The job_path must be a NEW file that does not yet exist. "
        "Use format jobs/category/descriptive_name.py. "
        "Valid categories: monitoring, email, content, research, calendar, documents, ministry, misc. "
        "Example valid paths: jobs/research/argument_mapper.py, jobs/content/sermon_outline.py, "
        "jobs/ministry/theology_tester.py\n"
        "Do NOT reference existing Watson modules. Do NOT use dot-separated names.\n\n"
        "Output ONLY the JSON array. No preamble, no explanation."
    )
    user = (
        f"Current skills:\n{skills_json}\n\n"
        f"Active projects:\n{projects}\n\n"
        f"Recent sessions:\n{recent_sessions}\n\n"
        f"Recent research queries:\n{research_excerpt}"
    )
    return system, user, "qwen2.5:7b", "skill_audit (jobs/skillbuilder/audit.py run_audit()), real memory/ + logs/ inputs"


def build_state_of_church_prompt():
    """Real jobs/connect_cards/state_of_church.py prompt — reuses build_report()
    itself (real congregation.db queries, real trend/engagement computation)
    by temporarily capturing its (condensed, benchmarks_context) inputs to
    _ollama_synthesis instead of letting it call Ollama, so the exact real
    prompt-building pipeline runs unmodified. No changes to the job's own
    file; the monkeypatch is local to this process and is restored after."""
    import jobs.connect_cards.state_of_church as soc

    captured = {}
    original = soc._ollama_synthesis

    def _capture(condensed, benchmarks_context):
        captured["condensed"] = condensed
        captured["benchmarks_context"] = benchmarks_context
        return "(captured, not sent to Ollama)"

    soc._ollama_synthesis = _capture
    try:
        soc.build_report()
    finally:
        soc._ollama_synthesis = original

    if "condensed" not in captured:
        raise RuntimeError("state_of_church.build_report() did not reach _ollama_synthesis")

    # Same prompt template as soc._ollama_synthesis, built from the real
    # captured condensed/benchmarks_context.
    system = ""
    user = (
        "You are Watson, AI assistant to Dr. Bill Yomes, Senior Pastor of Catalyst Community Church "
        "in Wilmington, DE, with both a Wilmington campus and an Online campus.\n\n"
        "Reference context — church attendance benchmarks (use this only to judge whether this week's "
        "numbers reflect normal variance or an actual trend; do not quote or repeat it verbatim):\n"
        f"{captured['benchmarks_context']}\n\n"
        "Based on this week's church data below, write exactly one cohesive 2-3 paragraph pastoral "
        "synthesis for Dr. Bill, following these rules:\n"
        "a. The normal range and in/out-of-range verdict given below (COMBINED TOTAL VS NORMAL RANGE) "
        "apply ONLY to the combined total across both campuses. Never state or imply that a single "
        "campus's average is 'within' or 'outside' that combined range — campus-level averages are for "
        "descriptive context only, not range comparison. Judge overall attendance health using the "
        "combined verdict, not campus-level arithmetic.\n"
        "b. Only use language like 'trend' or 'decline' if CONSECUTIVE WEEKS OUTSIDE NORMAL RANGE below "
        "is 3 or more. A number inside the normal range, or a one-week dip, is not a trend — say so "
        "plainly if that's the case.\n"
        "c. If SEASONAL CAVEAT below is not 'none', lead with it before commenting on any dip — never "
        "call a summer or holiday-week dip a decline.\n"
        "d. Do not imply continuous week-over-week growth is the expected baseline — a plateau is "
        "healthy, not a symptom.\n"
        "e. Report the numbers plainly. Only add interpretive or diagnostic language when it's grounded "
        "in the benchmarks context above or a real sustained deviation; otherwise describe, don't diagnose.\n\n"
        "Also comment on engagement health (what the Consistent/Active/Occasional/Lapsed distribution "
        "reveals), areas of concern, and who may need attention. Do not include a summary paragraph at "
        "the end. Do not repeat yourself. "
        "Do not include a 'Watson\\'s Read:' label or any other label inside the text.\n\n"
        f"{captured['condensed']}\n\n"
        "You must respond in English only. Do not use any other language. Begin writing now:"
    )
    return system, user, "qwen2.5:7b", "state_of_church (jobs/connect_cards/state_of_church.py build_report()), real congregation.db data"


_JOB_BUILDERS = {
    "memory_consolidation": build_memory_consolidation_prompt,
    "skill_audit": build_skill_audit_prompt,
    "state_of_church": build_state_of_church_prompt,
}


def _call_ollama(model, system, user, think=None, num_predict=400, timeout=180, num_ctx=None):
    prompt = f"{system}\n\n{user}"
    options = {"num_predict": num_predict}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    payload = {"model": model, "prompt": prompt, "stream": False, "options": options}
    if think is not None:
        payload["think"] = think
    t0 = time.monotonic()
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    elapsed = time.monotonic() - t0
    return {
        "model": model,
        "response": (data.get("response") or "").strip(),
        "elapsed_s": round(elapsed, 2),
        "eval_count": data.get("eval_count"),
        "eval_duration_s": round((data.get("eval_duration") or 0) / 1e9, 2),
        "load_duration_s": round((data.get("load_duration") or 0) / 1e9, 2),
        "total_duration_s": round((data.get("total_duration") or 0) / 1e9, 2),
    }


def run(job, session_id, baseline_model, candidate_model, candidate_think):
    if job not in _JOB_BUILDERS:
        raise ValueError(f"unknown job {job!r}, must be one of {list(_JOB_BUILDERS)}")

    if job == "memory_consolidation":
        system, user, default_model, label = _JOB_BUILDERS[job](session_id=session_id)
    else:
        system, user, default_model, label = _JOB_BUILDERS[job]()

    baseline_model = baseline_model or default_model

    print(f"Prompt source: {label}")
    print(f"Baseline model: {baseline_model}")
    print(f"Candidate model: {candidate_model} (think={candidate_think})")

    call_timeout = max(300, len(system + user) // 20)  # scale with prompt size; skill_audit's ~8k-token prompt needs headroom

    # Real jobs (e.g. jobs/skillbuilder/audit.py) send this prompt with no num_ctx
    # override, so a prompt this size can exceed the model's default runtime context
    # (4096, confirmed via `ollama ps` — the model's trained max of 32768 is not what
    # Ollama allocates by default) and silently drop the system prompt's task
    # instructions (bug_tracker #118, logged 2026-09-03). That's a truncation
    # artifact, not a reasoning signal, so this harness sets num_ctx explicitly with
    # headroom to actually test reasoning quality rather than reproduce the bug.
    approx_tokens = len(system + user) // 4
    call_num_ctx = max(4096, approx_tokens + 1500)

    print(f"\nRunning {baseline_model}... (num_ctx={call_num_ctx})")
    baseline_result = _call_ollama(baseline_model, system, user, timeout=call_timeout, num_ctx=call_num_ctx, num_predict=600)
    print(f"  done in {baseline_result['elapsed_s']}s")

    print(f"Running {candidate_model} (think={candidate_think})... (num_ctx={call_num_ctx})")
    candidate_result = _call_ollama(candidate_model, system, user, think=candidate_think, timeout=call_timeout, num_ctx=call_num_ctx, num_predict=600)
    print(f"  done in {candidate_result['elapsed_s']}s")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = OUT_DIR / f"{ts}_{job}.md"

    def _candidate_section(heading, result, extra_label):
        return [
            f"## {heading} — `{result['model']}`{extra_label}",
            "",
            f"*{result['elapsed_s']}s total, {result['eval_count']} tokens, "
            f"{result['eval_duration_s']}s eval, {result['load_duration_s']}s load*",
            "",
            "### Fabrication check (evaluated FIRST — a fabricated claim disqualifies the output outright, "
            "regardless of completeness or style, per WATSON_ARCHITECTURE.md's 'No hallucination' principle)",
            "",
            "**Verdict:** _[REVIEWER: PASS — no claim below traces outside the source data, or FAIL — "
            "list every claim not directly grounded in the system/user prompt above]_",
            "",
            "_[REVIEWER: list each unsupported claim found, quoting the exact phrase, or write "
            "\"None found — every claim traces to the source data.\" if clean]_",
            "",
            "### Full output",
            "",
            "```",
            result["response"],
            "```",
            "",
            "### Quality/completeness notes",
            "",
            "_[REVIEWER: only evaluate this if the fabrication check above is PASS. If FAIL, write "
            "\"N/A — disqualified by fabrication check\" and stop; do not weigh other qualities against it.]_",
            "",
        ]

    md = [
        f"# Reasoning Comparison — {job}",
        "",
        f"**Prompt source:** {label}",
        f"**Run at:** {datetime.now().isoformat(timespec='seconds')}",
        "",
        "**Judging protocol:** fabrication check runs first, per candidate, before any quality/completeness "
        "commentary. Every factual claim in each output is cross-referenced against the source data below; "
        "any claim not directly grounded there is listed. A fabricated claim disqualifies that output "
        "outright — it is not weighed against completeness, style, or usefulness. This is not a new rule: "
        "it enforces WATSON_ARCHITECTURE.md's existing 'No hallucination. If Watson does not know, Watson "
        "says so and stops' principle as a hard first-pass gate rather than leaving it as one quality factor "
        "among several. No automated grading beyond this structural gate — a human reads and completes the "
        "fabrication check and quality notes below.",
        "",
        "---",
        "",
        "## System prompt",
        "```",
        system.strip(),
        "```",
        "",
        "## User prompt (source data to cross-reference every claim against)",
        "```",
        user.strip(),
        "```",
        "",
        "---",
        "",
        *_candidate_section("Baseline", baseline_result, " (current production model)"),
        "---",
        "",
        *_candidate_section("Candidate", candidate_result, f" (think={candidate_think})"),
    ]
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWritten to {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", default="memory_consolidation", choices=list(_JOB_BUILDERS))
    parser.add_argument("--session-id", type=int, default=None)
    parser.add_argument("--baseline-model", default=None)
    parser.add_argument("--candidate", default="qwen3:8b")
    parser.add_argument("--candidate-think", default="false", choices=["true", "false"])
    args = parser.parse_args()

    run(
        job=args.job,
        session_id=args.session_id,
        baseline_model=args.baseline_model,
        candidate_model=args.candidate,
        candidate_think=(args.candidate_think == "true"),
    )
