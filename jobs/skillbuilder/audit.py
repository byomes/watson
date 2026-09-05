"""jobs/skillbuilder/audit.py — skill health audit + capability gap audit."""
import argparse
import importlib
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.claude_tier import call_claude
from core.ollama_context import size_num_ctx
from core.ollama_lock import heavy_ollama_call
from core.vacation import vacation_gate
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
DATA_DIR = REPO / "data"
AUDIT_FILE = DATA_DIR / "skill_audit.json"
DB_PATH = Path(os.getenv("WATSON_DB", str(DATA_DIR / "watson.db")))
OLLAMA_URL = "http://localhost:11434/api/generate"
# qwen3:8b, not qwen2.5:7b -- routed 2026-09-03 per model qualification testing.
# PROVISIONAL: based on n=1 real-prompt comparison (post-bug#118-fix, one clean
# run) -- see watson-review/context/2026-09-03-reasoning-comparison-skill-audit.md.
# A second real audit run should be spot-checked against the fabrication-check
# protocol (cross-reference every claimed gap against the actual skills.json)
# before this is considered fully confirmed, not just provisionally routed.
# think:false is a HARD requirement, not a default -- see jobs/memory/reflect.py's
# matching comment; every qwen3:8b call site must set it.
OLLAMA_MODEL = "qwen3:8b"

log = logging.getLogger(__name__)

_PASTORAL_TERMS = {
    "pastoral", "counseling", "counsel", "prayer", "pray",
    "pastor", "contextualization", "spiritual", "chaplain",
    "ministry guidance", "soul care",
}


def _is_pastoral(gap: dict) -> bool:
    text = " ".join([gap.get("gap", ""), gap.get("description", "")]).lower()
    return any(term in text for term in _PASTORAL_TERMS)


def _job_path_exists(job_path: str) -> bool:
    if os.path.exists(os.path.join(REPO, job_path)):
        return True
    slash_path = job_path.replace(".", "/")
    if not slash_path.endswith(".py"):
        slash_path += ".py"
    return os.path.exists(os.path.join(REPO, slash_path))


def _telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.skillbuilder.audit", text):
        return
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return
    if len(text) > 4000:
        text = text[:3950] + "\n…[truncated]"
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
    except Exception as exc:
        log.error("Telegram failed: %s", exc)


def _ensure_gaps_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capability_gaps (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            gap_name    TEXT NOT NULL,
            reason      TEXT,
            job_path    TEXT,
            description TEXT,
            status      TEXT NOT NULL DEFAULT 'proposed',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


# bug_tracker #118/#121: sending all ~43 skills (in full, verbose JSON) in one
# ~9-10k token call took 734-975s real wall time, long enough to starve the
# live intent classifier even with the busy lock honestly degrading it (the
# lock fixes the WRONG-ANSWER risk; it doesn't shrink the actual blocking
# window). Two changes here shrink that window directly:
#   1. A compact "slug: description" skill format instead of the full
#      verbose JSON (triggers/module/function/interfaces/status) — the bulk
#      of the original prompt's size, most of it not needed for gap analysis.
#   2. True batching across several small Ollama calls, chained via Ollama's
#      `context` field so each later call only pays prefill for its own NEW
#      content instead of re-processing everything seen so far (verified
#      empirically 2026-09-03: a chained call with 38 new tokens took 4s vs.
#      3043 fresh tokens taking 202s) — the final synthesis call ends up with
#      full awareness of every skill across all batches without re-sending
#      the skill list a second time.
_BATCH_SIZE = 15


def _compact_skill_line(skill: dict) -> str:
    return f"- {skill.get('slug', '?')}: {skill.get('description', '')}"


def _call_ollama_step(prompt: str, context: list | None, num_predict: int, num_ctx: int) -> tuple[str, list]:
    """One step in a context-chained Ollama conversation. Returns (response_text, new_context)."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,  # hard requirement for qwen3:8b -- see OLLAMA_MODEL comment
        "options": {"num_predict": num_predict, "num_ctx": num_ctx},
    }
    if context is not None:
        payload["context"] = context
    resp = requests.post(OLLAMA_URL, json=payload, timeout=600)
    resp.raise_for_status()
    data = resp.json()
    return (data.get("response") or "").strip(), data.get("context")


def _run_batched_capability_audit(
    skills: list, projects: str, recent_sessions: str, research_excerpt: str
) -> str:
    batches = [skills[i:i + _BATCH_SIZE] for i in range(0, len(skills), _BATCH_SIZE)]

    # num_ctx must cover the CUMULATIVE chain by its last call, not just one
    # batch — sized off the compact skill index appearing TWICE (once across
    # the batches, once again explicitly in the synthesis prompt — see that
    # prompt's comment for why) + recent-activity content + per-call
    # instruction overhead. Must stay the same value across every call in the
    # chain (a differing num_ctx would force a model reload mid-chain,
    # discarding the cached context).
    full_compact_chars = sum(len(_compact_skill_line(s)) for s in skills)
    est_total_chars = (full_compact_chars * 2) + len(projects) + len(recent_sessions) + len(research_excerpt) + 4000
    chain_num_ctx = size_num_ctx("x" * est_total_chars)

    context = None
    for i, batch in enumerate(batches, 1):
        batch_text = "\n".join(_compact_skill_line(s) for s in batch)
        prompt = (
            f"Batch {i} of {len(batches)} of Watson's current skills (a personal AI assistant "
            "for a church pastor). Just read these — you'll see the rest, then be asked one "
            "question at the end. Reply with just \"ok\".\n\n"
            f"{batch_text}"
        )
        _, context = _call_ollama_step(prompt, context, num_predict=10, num_ctx=chain_num_ctx)

    # Re-including the full compact index explicitly here (not just relying on
    # the chained batch context) matters: a spot check found that recall
    # through several turns of a chained conversation was NOT reliable enough
    # on its own — the model proposed a "PDF read/write/manage" gap even
    # though a `pdf` skill ("Read, extract, or manipulate PDF files") had
    # already been shown in an earlier batch. A clean, freshly-presented list
    # right before the decision is a real, well-justified check, not
    # redundant repetition.
    full_index = "\n".join(_compact_skill_line(s) for s in skills)
    synthesis_prompt = (
        "You've now seen Watson's full current skill list, batch by batch, above. Here it is again "
        "in full, for reference:\n\n"
        f"{full_index}\n\n"
        "Here is real recent activity to ground your analysis:\n\n"
        f"Active projects:\n{projects}\n\n"
        f"Recent sessions:\n{recent_sessions}\n\n"
        f"Recent research queries:\n{research_excerpt}\n\n"
        "You are Watson's capability auditor. Using the full skill list above and this recent "
        "activity, identify the top 3 capability gaps — things Bill has needed that Watson cannot "
        "do, or things that would significantly improve Watson's usefulness. For each gap, provide: "
        "gap name, why it matters, suggested job path, brief description of what to build.\n\n"
        "Format response as a JSON array:\n"
        '[{"gap": "name", "reason": "why it matters", "job_path": "jobs/category/skill_name.py", '
        '"description": "what to build"}]\n\n'
        "The job_path must be a NEW file that does not yet exist. "
        "Use format jobs/category/descriptive_name.py. "
        "Valid categories: monitoring, email, content, research, calendar, documents, ministry, misc. "
        "Example valid paths: jobs/research/argument_mapper.py, jobs/content/sermon_outline.py, "
        "jobs/ministry/theology_tester.py\n"
        "Do NOT propose a gap that duplicates or is already covered by any skill in the full list "
        "above — check carefully, a skill's stated capability may already cover it even if worded "
        "differently. Do NOT reference existing Watson modules. Do NOT use dot-separated names.\n\n"
        "Output ONLY the JSON array. No preamble, no explanation."
    )
    final_response, _ = _call_ollama_step(synthesis_prompt, context, num_predict=600, num_ctx=chain_num_ctx)
    return final_response


def _call_ollama_batched(skills: list, projects: str, recent_sessions: str, research_excerpt: str) -> str:
    # bug_tracker #118/#121: whole chain wrapped in ONE lock — Ollama is
    # occupied for the full batch+synthesis sequence, not just the last call.
    with heavy_ollama_call("skillbuilder.audit"):
        return _run_batched_capability_audit(skills, projects, recent_sessions, research_excerpt)


# ── Capability gap audit (existing) ──────────────────────────────────────────

def _load_skills_list(skills_json: str) -> list:
    try:
        data = json.loads(skills_json)
        return data.get("skills", data) if isinstance(data, dict) else data
    except Exception:
        return []


def run_audit() -> str:
    skills_file = MEMORY / "skills.json"
    skills_json = skills_file.read_text(encoding="utf-8") if skills_file.exists() else "[]"
    skills_list = _load_skills_list(skills_json)

    index_path = MEMORY / "projects" / "_index.md"
    projects = index_path.read_text(encoding="utf-8")[:2000] if index_path.exists() else "(no projects)"

    relational = MEMORY / "relational.md"
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

    try:
        raw = call_claude(system=system, user=user, job_name="skillbuilder.audit")
        if not raw:
            raw = _call_ollama_batched(skills_list, projects, recent_sessions, research_excerpt)
    except Exception as exc:
        log.error("Ollama audit call failed: %s", exc)
        _telegram(f"⚠️ Weekly audit failed: {exc}")
        return f"Audit failed: {exc}"

    gaps = []
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            gaps = json.loads(raw[start:end])
    except Exception as exc:
        log.error("Audit JSON parse failed: %s — raw: %s", exc, raw[:200])
        _telegram(f"⚠️ Audit parse error: {exc}\n\nRaw: {raw[:300]}")
        return f"Audit parse failed: {exc}"

    if not gaps:
        _telegram("📊 Weekly audit complete — no capability gaps identified.")
        return "No gaps identified."

    filtered = []
    for gap in gaps:
        job_path = gap.get("job_path", "")
        if _job_path_exists(job_path):
            log.info("Audit rejected existing-path gap: %s (%s)", gap.get("gap"), job_path)
            continue
        filtered.append(gap)
    gaps = filtered

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_gaps_table(conn)

    summary_lines = ["📊 Weekly Capability Audit\n"]
    for n, gap in enumerate(gaps[:3], 1):
        gap_name = gap.get("gap", "Unknown gap")
        reason = gap.get("reason", "")
        job_path = gap.get("job_path", "jobs/misc/new_skill.py")
        description = gap.get("description", "")

        if _is_pastoral(gap):
            log.info("Audit rejected pastoral gap: %s", gap_name)
            summary_lines.append(f"#{n} {gap_name} — SKIPPED (pastoral content)")
            continue

        conn.execute(
            "INSERT INTO capability_gaps (gap_name, reason, job_path, description, status) "
            "VALUES (?, ?, ?, ?, 'proposed')",
            (gap_name, reason, job_path, description),
        )
        conn.commit()

        _telegram(
            f"📊 Weekly Audit — Capability Gap #{n}\n\n"
            f"{gap_name}\n\n"
            f"Why: {reason}\n\n"
            f"Shall I build this? Reply YES to build {job_path}"
        )
        summary_lines.append(f"#{n} {gap_name} — {job_path}")

    conn.close()
    summary = "\n".join(summary_lines)
    log.info("Audit complete: %d gaps proposed", len(gaps[:3]))
    return summary


# ── Skill health audit ────────────────────────────────────────────────────────

_ENV_PATTERN = re.compile(
    r'os\.(?:getenv|environ\.get)\(\s*["\']([^"\']+)["\']'
    r'|os\.environ\[\s*["\']([^"\']+)["\']'
)

_FALLBACK_PATTERN = re.compile(
    r'os\.(?:getenv|environ\.get)\(\s*["\']([^"\']+)["\'][^)]*\)'
    r'\s*or\s*'
    r'os\.(?:getenv|environ\.get)\(\s*["\']([^"\']+)["\']'
)


def _find_missing_env(text: str) -> list:
    satisfied = set()
    for m in _FALLBACK_PATTERN.finditer(text):
        a, b = m.group(1), m.group(2)
        if os.environ.get(a) or os.environ.get(b):
            satisfied.add(a)
            satisfied.add(b)
    all_refs = set()
    for m in _ENV_PATTERN.finditer(text):
        all_refs.add(m.group(1) or m.group(2))
    return sorted(v for v in all_refs if v not in satisfied and not os.environ.get(v))


def _classify_skill(skill: dict) -> tuple:
    """Return (status, detail) for a single skill entry."""
    slug = skill.get("slug", "")
    job_module = skill.get("job_module", "")

    # Locate job file
    job_file = None
    if job_module:
        job_file = REPO / (job_module.replace(".", "/") + ".py")
    else:
        matches = list(REPO.glob(f"jobs/**/{slug}.py"))
        if matches:
            job_file = matches[0]

    if job_file is None or not job_file.exists():
        return "prompt_only", "no job file"

    # Attempt import
    if job_module:
        try:
            importlib.import_module(job_module)
        except (ImportError, ModuleNotFoundError) as exc:
            return "missing_deps", str(exc)
        except Exception as exc:
            return "broken", str(exc)

    # Scan for missing env vars
    try:
        text = job_file.read_text(encoding='utf-8')
        missing = _find_missing_env(text)
        if missing:
            return 'missing_creds', f'missing: {', '.join(missing)}'
    except Exception as exc:
        log.warning('Env scan failed for %s: %s', slug, exc)

    return "functional", ""


def run_skill_audit(dry_run: bool = False) -> dict:
    """Check every skill in skills.json for health and write ~/watson/data/skill_audit.json."""
    skills_file = MEMORY / "skills.json"
    if not skills_file.exists():
        raise FileNotFoundError("memory/skills.json not found")

    skills = json.loads(skills_file.read_text(encoding="utf-8"))
    summary = {"functional": 0, "prompt_only": 0, "broken": 0, "missing_deps": 0, "missing_creds": 0}
    skill_results = []

    for skill in skills:
        slug = skill.get("slug", "unknown")
        status, detail = _classify_skill(skill)
        summary[status] = summary.get(status, 0) + 1
        skill_results.append({"slug": slug, "status": status, "detail": detail})
        log.info("%-40s %s  %s", slug, status, detail)

    report = {
        "run_at": datetime.utcnow().isoformat(),
        "summary": summary,
        "skills": skill_results,
    }

    if dry_run:
        print("Skill Audit — dry run")
        print(f"  ✅ Functional:     {summary['functional']}")
        print(f"  📝 Prompt-only:    {summary['prompt_only']}")
        print(f"  ❌ Broken:         {summary['broken']}")
        print(f"  📦 Missing deps:   {summary['missing_deps']}")
        print(f"  🔑 Missing creds:  {summary['missing_creds']}")
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        AUDIT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("Skill audit written to %s", AUDIT_FILE)
        _telegram(
            "Skill audit complete.\n"
            f"✅ Functional: {summary['functional']}\n"
            f"📝 Prompt-only: {summary['prompt_only']}\n"
            f"❌ Broken: {summary['broken']}\n"
            f"📦 Missing deps: {summary['missing_deps']}\n"
            f"🔑 Missing creds: {summary['missing_creds']}\n"
            "Say 'show skill audit' for the full report."
        )

    return report


def run() -> str:
    """Capability gap audit — called by the skill router."""
    return run_audit()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Watson skill health audit")
    parser.add_argument("--dry-run", action="store_true", help="Print summary instead of writing file and notifying Telegram")
    args = parser.parse_args()
    try:
        run_skill_audit(dry_run=args.dry_run)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
