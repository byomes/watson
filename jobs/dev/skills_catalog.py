"""jobs/dev/skills_catalog.py — Regenerate the "Skills & Capabilities Catalog"
section of memory/WATSON_ARCHITECTURE.md from memory/skills.json, then push
the doc to the public byomes/watson-docs mirror (jobs/dev/docs_sync.py) so
Claude.ai's fetch tool always sees a current list of what Watson can do.

Only the section between the "## Skills & Capabilities Catalog" heading and
the next "\n---\n\n## " boundary is replaced -- everything else in the file
is left untouched.

skills.json doesn't cover the direct prefix commands that bypass the skill
router entirely (cdb:, wdb:, kb:, etc. -- hardcoded in jobs/dashboard/app.py's
/api/terminal and chat_stream()). _DIRECT_COMMANDS below is a hand-maintained
mirror of those; there's no registry to generate it from, so if a prefix
command is added/changed/removed in app.py, this list needs a matching edit.

Called two ways:
  - jobs/skillbuilder/build.py's _post_success(), right after a new skill is
    registered into skills.json, for near-real-time sync.
  - Cron, daily 2am (same block as update_arch.py / file_map.py /
    bugs_backlog_sync.py), as a safety net for any direct/manual edits to
    skills.json that didn't go through the skillbuilder build flow.

Cron: 0 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python
      /home/billyomes/watson/jobs/dev/skills_catalog.py >> /home/billyomes/watson/logs/skills_catalog.log 2>&1
"""
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
SKILLS_FILE = REPO / "memory" / "skills.json"
ARCH_FILE = REPO / "memory" / "WATSON_ARCHITECTURE.md"

START_MARKER = "## Skills & Capabilities Catalog\n"

# Category display order; any category present in skills.json but not listed
# here (e.g. a new one introduced by a freshly built skill) is appended after,
# alphabetically, rather than silently dropped.
CATEGORY_ORDER = ["Core", "Research", "Writing", "Documents", "Design", "Utilities", "Watson Dev"]

# Hand-maintained -- see module docstring. (command shown, description)
_DIRECT_COMMANDS = [
    ("`cdb: <question>`", "Query the congregation database in plain English (attendance, membership, campus, engagement trends) — e.g. `cdb: who missed this Sunday`."),
    ("`wdb: <question>`", "Query the leadership/team database (task status, stalled work, follow-ups, meeting notes) — e.g. `wdb: stalled tasks`."),
    ("`web: <query>`", "Web search (duplicate entry point to the web_search skill, prefix form)."),
    ("`bible: <reference>`", "Bible lookup (duplicate entry point to bible_lookup, prefix form) — e.g. `bible: John 3:16 NIV`."),
    ("`kb: <query>` / `search the kb: <query>`", "Search the sermon-transcript ChromaDB knowledge base."),
    ("`xkb: <query>`", "Search the sermons KB with expanded/deeper matching."),
    ("`gutenberg: <query>`", "Search Project Gutenberg; reply with a number (in chat, not terminal) to download and ingest a text into the `gutenberg` KB collection."),
    ("`classics: <query>`", "Search the `gutenberg` KB collection (ingested public-domain texts), kept separate from sermons."),
    ("`imagegen: <prompt>` / `imgen: <prompt>`", "Generate an AI image from a text prompt."),
    ("`polish this: <text>` / `polish: <text>`", "Polish text in Dr. Yomes's pastoral-scholarly voice (duplicate entry point to the polish skill)."),
    ("`bug: <title>`", "Log a bug directly to the `bug_tracker` table."),
    ("`backlog: <title> | <summary>`", "Log an item to the project backlog."),
    ("`build: <description>` / `devloop: <description>`", "Trigger a new Dev Loop autonomous coding project (`devloop:` is the Telegram spelling, `build:` the dashboard spelling — both work in both places)."),
    ("`debug: <problem>`", "Run Claude-assisted diagnostics on a Watson problem."),
    ("`run: <slug> <args>`", "Explicitly dispatch a registered skill by its skills.json slug, bypassing trigger-phrase matching."),
    ("`shepherding:`", "Pastoral shepherding report — critical care, at-risk, first-time visitors, no-next-step members."),
    ("`state of church report`", "Generate and email the full State of the Church HTML report (async — runs in the background, delivered by email)."),
    ("`remind me at <time> <text>` / `remind me <text>`", "Create a timed or plain reminder."),
    ("what's on my calendar / my schedule / today's schedule / etc.", "Also reachable via the calendar_query skill above; these phrasings work identically."),
    ("`system status`", "CPU, memory, disk, and service health."),
    ("`check logs`", "Tail the last 50 lines of the watson-bot / watson-dashboard systemd journal (dashboard only, via /api/terminal)."),
    ("`count congregation members` / `count tasks` / `count connect cards`", "Quick row counts from the relevant database."),
    ("`conflict_check`", "Run the member-conflict report in the background (results arrive via Telegram)."),
    ("`watson audit skills`", "Run the full skill_audit self-test and report pass/fail (dashboard only, via /api/terminal)."),
    ("`git pull`", "Pull latest changes into ~/watson (dashboard only, via /api/terminal)."),
    ("`restart watson bot` / `restart dashboard`", "Restart the named systemd service (dashboard only, via /api/terminal; passwordless sudo scoped to exactly these two commands)."),
]


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
        "`interfaces`, `status`) plus a hand-maintained list of direct prefix commands. "
        "Do not hand-edit this section -- edit skills.json (or _DIRECT_COMMANDS in "
        "skills_catalog.py for prefix commands) and re-run the job instead; a manual "
        "edit here will be overwritten on the next regeneration.",
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
        "These are handled by hardcoded prefix/exact-match checks in "
        "`jobs/dashboard/app.py` (`/api/terminal` and the chat endpoint) rather than "
        "through `skills.json` — they are not in the machine-readable catalog above, "
        "but they're real, working capabilities and the prefix must be typed exactly "
        "as shown. (Hand-maintained list — see this file's module docstring.)"
    )
    lines.append("")
    lines.append("| Command | What it does |")
    lines.append("|---|---|")
    for cmd, desc in _DIRECT_COMMANDS:
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
