"""jobs/dev/skills_catalog.py — Regenerate the "Skills & Capabilities Catalog"
section of memory/WATSON_ARCHITECTURE.md from memory/skills.json, then push
the doc to the public byomes/watson-docs mirror (jobs/dev/docs_sync.py) so
Claude.ai's fetch tool always sees a current list of what Watson can do.

Only the section between the "## Skills & Capabilities Catalog" heading and
the next "\n---\n\n## " boundary is replaced -- everything else in the file
is left untouched.

skills.json doesn't cover the direct prefix commands that bypass the skill
router entirely (cdb:, wdb:, kb:, etc. -- hardcoded in jobs/dashboard/app.py's
/api/terminal). Those are parsed straight out of app.py's source instead of
hand-maintained here: every `if cmd_lower.startswith(...)` / `== ...` branch
in terminal(), and every _TERM_COMMANDS dict entry, is expected to carry a
trailing `# doc: <description>` comment; _extract_direct_commands() reads it
via ast + tokenize. A branch missing that comment shows up in the generated
table flagged as undocumented rather than silently omitted -- so the fix for
a stale/missing description is a one-line comment in app.py, right next to
the code it describes, not an edit to this file or the doc.

Called two ways:
  - jobs/skillbuilder/build.py's _post_success(), right after a new skill is
    registered into skills.json, for near-real-time sync.
  - Cron, daily 2am (same block as update_arch.py / file_map.py /
    bugs_backlog_sync.py), as a safety net for any direct/manual edits to
    skills.json that didn't go through the skillbuilder build flow.

Cron: 0 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python
      /home/billyomes/watson/jobs/dev/skills_catalog.py >> /home/billyomes/watson/logs/skills_catalog.log 2>&1
"""
import ast
import io
import json
import logging
import re
import subprocess
import tokenize
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
SKILLS_FILE = REPO / "memory" / "skills.json"
ARCH_FILE = REPO / "memory" / "WATSON_ARCHITECTURE.md"
APP_FILE = REPO / "jobs" / "dashboard" / "app.py"

START_MARKER = "## Skills & Capabilities Catalog\n"

# Category display order; any category present in skills.json but not listed
# here (e.g. a new one introduced by a freshly built skill) is appended after,
# alphabetically, rather than silently dropped.
CATEGORY_ORDER = ["Core", "Research", "Writing", "Documents", "Design", "Utilities", "Watson Dev"]

_UNDOCUMENTED = "*(undocumented — add a `# doc: ...` comment on this line in app.py)*"


def _comment_map(source: str) -> dict[int, str]:
    """1-indexed source line -> trailing comment text (leading '#' stripped)."""
    out: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string.lstrip("#").strip()
    except tokenize.TokenError:
        pass
    return out


def _doc_comment(comments: dict[int, str], lineno: int) -> str:
    text = comments.get(lineno, "")
    if text.lower().startswith("doc:"):
        return text[4:].strip()
    return _UNDOCUMENTED


def _extract_direct_commands() -> list[tuple[str, str]]:
    """Parse jobs/dashboard/app.py's terminal() function (the /api/terminal
    view) for `cmd_lower.startswith("...")` / `cmd_lower == "..."` branches,
    plus the _TERM_COMMANDS dict, pairing each with its `# doc: ...` trailing
    comment. See this module's docstring for the comment convention."""
    if not APP_FILE.exists():
        return []
    source = APP_FILE.read_text(encoding="utf-8")
    comments = _comment_map(source)
    tree = ast.parse(source)

    results: list[tuple[str, str]] = []

    term_fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "terminal"),
        None,
    )
    if term_fn is not None:
        for node in ast.walk(term_fn):
            if not isinstance(node, ast.If):
                continue
            tests = node.test.values if isinstance(node.test, ast.BoolOp) else [node.test]
            prefixes: list[str] = []
            exacts: list[str] = []
            for t in tests:
                if (
                    isinstance(t, ast.Call)
                    and isinstance(t.func, ast.Attribute)
                    and t.func.attr == "startswith"
                    and isinstance(t.func.value, ast.Name)
                    and t.func.value.id == "cmd_lower"
                    and t.args
                    and isinstance(t.args[0], ast.Constant)
                ):
                    prefixes.append(t.args[0].value)
                elif (
                    isinstance(t, ast.Compare)
                    and isinstance(t.left, ast.Name)
                    and t.left.id == "cmd_lower"
                    and len(t.ops) == 1
                    and isinstance(t.ops[0], ast.Eq)
                    and isinstance(t.comparators[0], ast.Constant)
                ):
                    exacts.append(t.comparators[0].value)
            if not prefixes and not exacts:
                continue
            doc = _doc_comment(comments, node.lineno)
            display = " / ".join(f"`{p} <...>`" for p in prefixes) or " / ".join(f"`{e}`" for e in exacts)
            if prefixes and exacts:
                display += " / " + " / ".join(f"`{e}`" for e in exacts)
            results.append((display, doc))

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_TERM_COMMANDS"
            and isinstance(node.value, ast.Dict)
        ):
            for key_node in node.value.keys:
                if isinstance(key_node, ast.Constant):
                    results.append((f"`{key_node.value}`", _doc_comment(comments, key_node.lineno)))
            break

    return results


def _load_skills() -> list:
    if not SKILLS_FILE.exists():
        return []
    try:
        data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        return data.get("skills", data) if isinstance(data, dict) else data
    except Exception as exc:
        log.error("Could not load skills.json: %s", exc)
        return []


def build_section() -> str:
    skills = _load_skills()
    cats: dict[str, list] = {}
    for s in skills:
        cats.setdefault(s.get("category") or "Utilities", []).append(s)

    ordered_cats = [c for c in CATEGORY_ORDER if c in cats]
    ordered_cats += sorted(c for c in cats if c not in CATEGORY_ORDER)

    lines = [
        "## Skills & Capabilities Catalog",
        "",
        "> Auto-generated by `jobs/dev/skills_catalog.py` from `memory/skills.json` "
        "(each entry there has `slug`, `module`/`job_module`, `function`, `triggers`, "
        "`interfaces`, `status`) plus the `# doc:` comments on jobs/dashboard/app.py's "
        "`/api/terminal` prefix commands. Do not hand-edit this section -- edit "
        "skills.json or the relevant `# doc:` comment in app.py and re-run the job "
        "instead; a manual edit here will be overwritten on the next regeneration.",
        "",
        "**How to trigger a skill.** Talk to Watson via the Telegram bot "
        "(`@wckyWatsonbot`) or the dashboard chat tab "
        "(`https://watson.tail0243ff.ts.net`). Two ways to invoke:",
        "",
        "1. **Natural language** — say what you want in plain English close to the "
        "skill's description; `jobs/skillbuilder/router.py` matches known trigger "
        "phrases first (no LLM call, instant) and falls back to an LLM intent "
        "classifier (`gemma3:4b`) for anything else, so exact trigger wording usually "
        "isn't required.",
        "2. **Exact trigger phrase** — the phrases listed below always work and skip "
        "the classifier. Several skills use a colon-prefix form (`kb:`, `bible:`, "
        "`polish this:`) — the prefix must be exact and the text after it is passed "
        "straight through as the argument.",
        "",
        "A skill marked **disabled** below is registered but intentionally turned "
        "off — it will not fire even if its trigger phrase is used.",
        "",
    ]

    for cat in ordered_cats:
        lines.append(f"### {cat}")
        lines.append("")
        lines.append("| Skill | Status | Interfaces | Trigger phrases | What it does |")
        lines.append("|---|---|---|---|---|")
        for s in sorted(cats[cat], key=lambda x: x.get("slug", "")):
            slug = s.get("slug", "")
            status = s.get("status", "ready")
            ifaces = ", ".join(s.get("interfaces", []))
            trigs = ", ".join(f"`{t}`" for t in s.get("triggers", []))
            desc = (s.get("description") or "").replace("|", "\\|")
            name = s.get("name", slug)
            lines.append(f"| **{name}** (`{slug}`) | {status} | {ifaces} | {trigs} | {desc} |")
        lines.append("")

    lines.append(
        "### Direct commands (bypass the skill router)\n\n"
        "Parsed directly out of `jobs/dashboard/app.py`'s `/api/terminal` view "
        "(`terminal()`'s prefix/exact-match checks and its `_TERM_COMMANDS` dict) — "
        "they're not in `skills.json`, so this table can't come from that file the way "
        "the ones above do; each row's description instead comes from a `# doc: ...` "
        "comment on that line in app.py. A row that says *undocumented* means that "
        "comment is missing — add it in app.py, not here.\n\n"
        "Every prefix here also works typed directly in dashboard/Telegram chat, not "
        "just in the terminal, plus a few things too free-form for a prefix table: "
        "natural-language reminders (`remind me ...` / `remind me at <time> ...`) and "
        "calendar phrasing (see the calendar_query skill above)."
    )
    lines.append("")
    lines.append("| Command | What it does |")
    lines.append("|---|---|")
    for cmd, desc in _extract_direct_commands():
        lines.append(f"| {cmd} | {desc} |")
    lines.append("")

    return "\n".join(lines)


def update_architecture_doc(section_md: str) -> str:
    content = ARCH_FILE.read_text(encoding="utf-8")
    start = content.index(START_MARKER)
    m = re.search(r"\n---\n\n## ", content[start:])
    if not m:
        raise RuntimeError(
            "Could not find the '\\n---\\n\\n## ' boundary after "
            "'## Skills & Capabilities Catalog' in WATSON_ARCHITECTURE.md -- "
            "doc structure changed, needs a manual look before this job can "
            "safely regenerate the section."
        )
    tail_start = start + m.end() - len("## ")
    return content[:start] + section_md.rstrip() + "\n\n---\n\n" + content[tail_start:]


def _git_commit_push() -> None:
    commands = [
        ["git", "-C", str(REPO), "add", "memory/WATSON_ARCHITECTURE.md"],
        ["git", "-C", str(REPO), "commit", "-m", "docs: regenerate Skills & Capabilities Catalog"],
        ["git", "-C", str(REPO), "push", "origin", "main"],
    ]
    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            log.warning("git warning: %s", result.stderr.strip() or result.stdout.strip())


def sync_skills_catalog() -> bool:
    """Regenerate the catalog section, write it, commit+push it, and mirror to
    byomes/watson-docs. Returns True on full success, False if any step failed
    (never raises -- callers like build.py's _post_success() treat this as
    non-fatal)."""
    try:
        section = build_section()
        updated = update_architecture_doc(section)
        if updated == ARCH_FILE.read_text(encoding="utf-8"):
            log.info("Skills catalog unchanged, nothing to sync")
            return True
        ARCH_FILE.write_text(updated, encoding="utf-8")
        _git_commit_push()
        from jobs.dev import docs_sync
        return docs_sync.push_file("WATSON_ARCHITECTURE.md", updated)
    except Exception as exc:
        log.error("Skills catalog sync failed: %s", exc)
        return False


def run() -> str:
    ok = sync_skills_catalog()
    return "Skills catalog synced." if ok else "Skills catalog sync failed -- see logs."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    print(run())
