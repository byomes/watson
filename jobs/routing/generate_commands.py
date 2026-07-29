"""jobs/routing/generate_commands.py — sync memory/commands.json's directive
rows with jobs/routing/directive_prefixes.py.

commands.json backs the dashboard's command dropdown and is mostly hand-
curated natural-language examples (calendar, QR codes, System actions, etc.)
that have nothing to do with colon-prefix directives — this script never
touches those. For the subset of rows that *are* directive examples, it:

  - drops any row whose prefix is registered dashboard: False (keeps the
    dashboard menu from advertising Telegram-only commands like task:/
    remind:/sms: that only look like they work from the dropdown)
  - leaves existing rows alone for any dashboard: True prefix that already
    has at least one example row (cdb:/wdb:/kb:/etc. keep their curated,
    often multi-example, entries as-is)
  - appends one generated row, from the registry's description/example/
    category, for any dashboard: True prefix with zero existing rows

Run this after editing DIRECTIVE_PREFIXES (as a deploy/commit-time step —
not wired into dashboard startup, so a restart never silently rewrites a
hand-edited commands.json underneath a maintainer mid-edit).
"""
import json
from pathlib import Path

from jobs.routing.directive_prefixes import DIRECTIVE_PREFIXES

COMMANDS_FILE = Path(__file__).resolve().parents[2] / "memory" / "commands.json"

# Display names for prefixes that don't already have a curated commands.json
# entry to inherit a name from. Only consulted when generating a new row.
_GENERATED_NAMES = {
    "debug:": "Debug loop",
    "bug:": "Log a bug",
    "run:": "Run a skill by slug",
    "xkb:": "Expanded KB search",
    "imagegen:": "Generate an image",
    "shepherding:": "Shepherding report",
}


def _all_prefix_strings() -> dict:
    """Map every prefix string (canonical + aliases) to its canonical entry."""
    out = {}
    for canonical, cfg in DIRECTIVE_PREFIXES.items():
        out[canonical] = canonical
        for alias in cfg.get("aliases", []):
            out[alias] = canonical
    return out


def sync_commands_json() -> dict:
    """Rewrite COMMANDS_FILE's directive rows in place. Returns a summary dict."""
    prefix_map = _all_prefix_strings()
    commands = json.loads(COMMANDS_FILE.read_text(encoding="utf-8")) if COMMANDS_FILE.exists() else []

    kept = []
    dropped = []
    covered_canonical = set()

    for row in commands:
        cmd_text = (row.get("command") or "").strip().lower()
        matched_canonical = None
        for prefix_str, canonical in prefix_map.items():
            if cmd_text.startswith(prefix_str):
                matched_canonical = canonical
                break
        if matched_canonical is None:
            kept.append(row)
            continue
        if DIRECTIVE_PREFIXES[matched_canonical]["dashboard"]:
            kept.append(row)
            covered_canonical.add(matched_canonical)
        else:
            dropped.append(row.get("command"))

    added = []
    for canonical, cfg in DIRECTIVE_PREFIXES.items():
        if not cfg["dashboard"] or canonical in covered_canonical:
            continue
        name = _GENERATED_NAMES.get(canonical, canonical.rstrip(":").replace("_", " ").title())
        row = {
            "name": name,
            "command": cfg["example"],
            "category": cfg["category"],
            "description": cfg["description"],
        }
        if cfg["example"].strip() != canonical:
            row["requires_input"] = True
        kept.append(row)
        added.append(canonical)

    # One compact object per line, 2-space indented, matching the file's
    # existing hand-authored style — json.dumps(indent=2) would reformat
    # every untouched row too and bury the real diff in whitespace noise.
    body = ",\n".join("  " + json.dumps(row, ensure_ascii=False) for row in kept)
    COMMANDS_FILE.write_text("[\n" + body + "\n]\n", encoding="utf-8")
    return {"dropped": dropped, "added": added, "total": len(kept)}


if __name__ == "__main__":
    summary = sync_commands_json()
    print(f"commands.json: {summary['total']} rows")
    if summary["added"]:
        print("Added:", ", ".join(summary["added"]))
    if summary["dropped"]:
        print("Dropped:", ", ".join(summary["dropped"]))
