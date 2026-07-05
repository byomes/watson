#!/usr/bin/env python3
"""
Watson content style audit + fixer.

Scans markdown/TSX content for:
  1. Broken frontmatter (missing/malformed closing '---') -- flags only, no auto-fix
     (this is a code/data bug, needs a human look, not a text substitution)
  2. Em dashes (—) -- auto-fixed with a conservative rule, logged for review
  3. Double hyphens used as a dash (--) -- auto-fixed, logged for review
  4. ALL CAPS headers (markdown ## HEADER lines) -- flagged only
  5. Lowercase "scripture" where it likely means the Bible -- flagged only

Usage:
    python3 fix_style.py /path/to/content/blog [--apply] [--report report.md]

Without --apply, this is dry-run: prints/report what WOULD change, changes nothing.
With --apply: writes changes in place, keeps a .bak backup of every modified file.

Em dash / double-hyphen handling -- IMPORTANT DESIGN NOTE:
  Deciding whether an em dash joins two independent clauses (needs a period)
  or a fragment/appositive (needs a comma) is a grammar judgment call, not
  a pattern match. An earlier regex-only version of this script produced a
  comma splice ("...moment, it is a long chain...") on real test content --
  i.e. it silently introduced a new grammar error while "fixing" a style
  rule. That is worse than leaving it alone. So:
    - This script NEVER auto-rewrites em-dash / double-hyphen sentences.
    - It finds every instance and writes each to the report with full line
      context, ready for a human (or a per-line Ollama/Claude pass) to
      rewrite with actual judgment.
    - Frontmatter breakage, ALL CAPS headers, and lowercase "scripture" are
      flag-only for the same reason -- these need a human decision, not a
      silent rewrite.
"""

import argparse
import os
import re
import sys
from pathlib import Path

EM_DASH = "—"
DOUBLE_HYPHEN_RE = re.compile(r"(?<!-)--(?!-)")  # exactly two hyphens, not more
ALLCAPS_HEADER_RE = re.compile(r"^(#{1,6})\s*([A-Z0-9 ,.'!?-]{4,})\s*$")
SCRIPTURE_RE = re.compile(r"\bscripture\b")

TEXT_FILE_EXT = {".md", ".mdx", ".tsx", ".ts"}


def split_frontmatter(text):
    """Return (frontmatter_lines_or_None, body, ok_flag).
    ok_flag False means frontmatter looks malformed (opened but never closed
    cleanly before content starts)."""
    if not text.startswith("---"):
        return None, text, True  # no frontmatter, nothing to check
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None, text, True
    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break
    if closing_idx is None:
        # opened but never closed -- this is the leak bug
        return lines[1:], "\n".join(lines[1:]), False
    fm = lines[1:closing_idx]
    body = "\n".join(lines[closing_idx + 1:])
    return fm, body, True


def flag_dashes(body, filename, report_lines):
    """Flag every em dash / dash-like double-hyphen instance with context.
    Does NOT rewrite the text -- see module docstring for why."""
    for lineno, line in enumerate(body.split("\n"), start=1):
        if EM_DASH in line:
            count = line.count(EM_DASH)
            report_lines.append(f"- FLAG (em dash x{count}) `{filename}` line {lineno}: {line.strip()}")

        for m in DOUBLE_HYPHEN_RE.finditer(line):
            start, end = m.span()
            left_ok = start == 0 or line[start - 1] == " "
            right_ok = end == len(line) or line[end] == " "
            if left_ok and right_ok:
                report_lines.append(f"- FLAG (double hyphen used as dash) `{filename}` line {lineno}: {line.strip()}")
                break  # one flag per line is enough


def scan_flags(body, filename, report_lines):
    for lineno, line in enumerate(body.split("\n"), start=1):
        m = ALLCAPS_HEADER_RE.match(line.strip())
        if m and m.group(2).upper() == m.group(2) and any(c.isalpha() for c in m.group(2)):
            report_lines.append(f"- FLAG (ALL CAPS header) `{filename}` line {lineno}: {line.strip()}")
        if SCRIPTURE_RE.search(line):
            report_lines.append(f"- FLAG (lowercase 'scripture') `{filename}` line {lineno}: {line.strip()}")


def process_file(path: Path, report):
    """Read-only pass: flags every issue with file/line context. Never writes."""
    text = path.read_text(encoding="utf-8")
    fm, body, fm_ok = split_frontmatter(text)

    if not fm_ok:
        report.append(f"- ⚠️ BROKEN FRONTMATTER (needs a code/data fix, not a text edit) `{path}` "
                       f"-- opening '---' found but no closing '---' before content")

    flags = []
    flag_dashes(body, str(path), flags)
    scan_flags(body, str(path), flags)
    if flags:
        report.extend(flags)
    return len(flags) + (0 if fm_ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="File or directory to scan")
    ap.add_argument("--report", default="style_audit_report.md", help="Report output path")
    args = ap.parse_args()

    target = Path(args.path)
    files = []
    if target.is_file():
        files = [target]
    else:
        for ext in TEXT_FILE_EXT:
            files.extend(target.rglob(f"*{ext}"))

    report = ["# Style Audit Report (flag-only, no files modified)", f"Scanned: {target}", ""]
    files_with_flags = 0
    total_flags = 0
    for f in sorted(files):
        n = process_file(f, report)
        if n:
            files_with_flags += 1
            total_flags += n

    report.append("")
    report.append(f"## Summary: {total_flags} issue(s) across {files_with_flags} file(s), {len(files)} file(s) scanned")
    report.append("")
    report.append("Nothing was rewritten. Each em-dash/double-hyphen flag needs a human "
                   "(or a per-line Ollama/Claude rewrite pass) to pick the right fix -- "
                   "period split, comma, colon, or restructure -- since that's a grammar "
                   "judgment call a regex can't make safely.")

    Path(args.report).write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"\nReport written to {args.report}")


if __name__ == "__main__":
    main()
