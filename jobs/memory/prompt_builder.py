"""
prompt_builder.py — Watson memory injection system.

Every Ollama call in Watson uses build_prompt() to assemble a system prompt
with four layers: core identity, active memory, project memory, and archive.
"""
import os
import sqlite3
from datetime import datetime, timedelta

DB = os.path.expanduser("~/watson/data/watson.db")
PROJECTS_DIR = os.path.expanduser("~/watson/memory/projects")

_WORDS_PER_TOKEN = 0.75  # rough conversion for trimming
_MAX_TOTAL_TOKENS = 1500
_SESSION_TOKEN_LIMIT = 150
_PROJECT_TOKEN_LIMIT_FULL = None  # no limit unless trimming
_PROJECT_TOKEN_LIMIT_TRIM = 500


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) / _WORDS_PER_TOKEN)


def _trim_to_tokens(text: str, max_tokens: int) -> str:
    words = text.split()
    target = int(max_tokens * _WORDS_PER_TOKEN)
    if len(words) <= target:
        return text
    return " ".join(words[:target])


def _build_layer1(current_date: str) -> str:
    return (
        f"You are Watson, Dr. Bill Yomes's AI-powered digital assistant.\n"
        f"You act on Dr. Bill's behalf under his supervision.\n"
        f"You are terse, efficient, and direct. You never guess. You never pastor, counsel, or speak theologically without permission.\n"
        f"If you do not know something, say so and stop.\n"
        f"Today is {current_date}. Dr. Bill's schedule: desk days Wed/Thu. Deep work 9am–2pm. People always beat tasks."
    )


def _build_layer2(task: str, max_entries: int = 3) -> str:
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT summary FROM memory_sessions WHERE created_at >= ? ORDER BY created_at DESC LIMIT 50",
            (cutoff,),
        ).fetchall()
        conn.close()
    except Exception:
        return ""

    task_words = set(task.lower().split())

    scored = []
    for row in rows:
        summary = row["summary"] or ""
        score = sum(1 for w in summary.lower().split() if w in task_words)
        if score > 0:
            scored.append((score, summary))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_entries]

    if not top:
        return ""

    lines = ["Recent context:"]
    for _, summary in top:
        trimmed = _trim_to_tokens(summary, _SESSION_TOKEN_LIMIT)
        lines.append(f"- {trimmed}")
    return "\n".join(lines)


def _detect_project(task: str) -> str | None:
    if not os.path.isdir(PROJECTS_DIR):
        return None
    slugs = [
        f[:-3] for f in os.listdir(PROJECTS_DIR)
        if f.endswith(".md") and not f.startswith("_")
    ]
    matches = [slug for slug in slugs if slug in task]
    if len(matches) == 1:
        return matches[0]
    return None


def _build_layer3(project: str | None, task: str) -> str:
    resolved = project if project is not None else _detect_project(task)
    if not resolved:
        return ""

    path = os.path.join(PROJECTS_DIR, f"{resolved}.md")
    if not os.path.isfile(path):
        return ""

    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


# Layer 4: archive retrieved via jobs/kb/search.py on explicit request only


def build_prompt(task: str, project: str = None) -> str:
    """Assemble a Watson system prompt with up to four layers.

    Layer 1: core identity (always present)
    Layer 2: recent memory sessions scored against task
    Layer 3: project-specific memory file
    Layer 4: archive (not injected here — on-demand only)
    """
    current_date = datetime.now().strftime("%B %d, %Y")

    layer1 = _build_layer1(current_date)
    layer2 = _build_layer2(task, max_entries=3)
    layer3 = _build_layer3(project, task)

    base_tokens = _approx_tokens(layer1)
    l2_tokens = _approx_tokens(layer2) if layer2 else 0
    l3_tokens = _approx_tokens(layer3) if layer3 else 0

    if base_tokens + l2_tokens + l3_tokens > _MAX_TOTAL_TOKENS:
        # Trim layer 2 to 1 entry first
        layer2 = _build_layer2(task, max_entries=1)
        l2_tokens = _approx_tokens(layer2) if layer2 else 0

        # Trim layer 3 if still over
        if base_tokens + l2_tokens + l3_tokens > _MAX_TOTAL_TOKENS:
            layer3 = _trim_to_tokens(layer3, _PROJECT_TOKEN_LIMIT_TRIM)

    parts = [layer1]
    if layer2:
        parts.append(layer2)
    if layer3:
        parts.append(layer3)

    return "\n\n".join(parts)
