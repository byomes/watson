#!/usr/bin/env python3
"""Flag-only style audit: em dashes, double-hyphen dashes, broken frontmatter,
ALL CAPS headers, and lowercase 'scripture'. Reports file/line context. Never
rewrites files.
"""

import argparse
import re
import sys
from pathlib import Path

SCAN_EXTENSIONS = {".md", ".mdx", ".txt", ".tsx", ".ts", ".jsx", ".js"}

EM_DASH_RE = re.compile(r"—")
DOUBLE_HYPHEN_RE = re.compile(r"(?<!-)--(?!-)(?![a-zA-Z])")  # exclude CSS custom properties like --font-jost
MD_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
SCRIPTURE_RE = re.compile(r"\bscripture\b")
FRONTMATTER_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:")


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in SCAN_EXTENSIONS:
            yield path


def check_em_dash(lines):
    findings = []
    for i, line in enumerate(lines, start=1):
        if EM_DASH_RE.search(line):
            findings.append((i, line.strip()))
    return findings


def check_double_hyphen(lines):
    findings = []
    for i, line in enumerate(lines, start=1):
        if DOUBLE_HYPHEN_RE.search(line):
            findings.append((i, line.strip()))
    return findings


def check_frontmatter(lines):
    findings = []
    if not lines or lines[0].strip() != "---":
        return findings

    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break

    if close_idx is None:
        findings.append((1, "frontmatter opened with '---' but never closed"))
        return findings

    for i in range(1, close_idx):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.startswith("-") and not stripped.startswith("- "):
            continue
        if stripped.startswith("- "):
            continue
        if not FRONTMATTER_KEY_RE.match(stripped):
            findings.append((i + 1, f"malformed frontmatter line: {stripped}"))

    return findings


def check_all_caps_headers(lines):
    findings = []
    for i, line in enumerate(lines, start=1):
        m = MD_HEADER_RE.match(line)
        if not m:
            continue
        text = m.group(2)
        letters = re.sub(r"[^A-Za-z]", "", text)
        if len(letters) >= 4 and text == text.upper() and any(c.isalpha() for c in text):
            findings.append((i, line.strip()))
    return findings


def check_lowercase_scripture(lines):
    findings = []
    for i, line in enumerate(lines, start=1):
        for m in SCRIPTURE_RE.finditer(line):
            start = m.start()
            if start == 0 or not line[start - 1].isalpha():
                findings.append((i, line.strip()))
                break
    return findings


CHECKS = [
    ("Em Dashes", check_em_dash),
    ("Double-Hyphen Dashes", check_double_hyphen),
    ("Broken Frontmatter", check_frontmatter),
    ("ALL CAPS Headers", check_all_caps_headers),
    ("Lowercase 'scripture'", check_lowercase_scripture),
]


def audit_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return {}
    lines = text.splitlines()

    results = {}
    for name, fn in CHECKS:
        hits = fn(lines)
        if hits:
            results[name] = hits
    return results


def run_audit(target: Path):
    report = {}
    for path in iter_files(target):
        results = audit_file(path)
        if results:
            report[path] = results
    return report


def render_report(target: Path, report: dict) -> str:
    lines = [f"# Style Audit Report", "", f"Target: `{target}`", ""]

    total_hits = sum(len(hits) for file_results in report.values() for hits in file_results.values())
    total_files = len(report)
    lines.append(f"**{total_hits} flag(s) across {total_files} file(s).**")
    lines.append("")

    if not report:
        lines.append("No issues found.")
        return "\n".join(lines) + "\n"

    by_check = {}
    for path, results in report.items():
        for check_name, hits in results.items():
            by_check.setdefault(check_name, []).append((path, hits))

    for check_name, _ in CHECKS:
        if check_name not in by_check:
            continue
        entries = by_check[check_name]
        count = sum(len(hits) for _, hits in entries)
        lines.append(f"## {check_name} ({count})")
        lines.append("")
        for path, hits in entries:
            lines.append(f"### `{path}`")
            for line_no, context in hits:
                lines.append(f"- line {line_no}: `{context}`")
            lines.append("")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Flag-only style audit (no file rewrites)")
    parser.add_argument("path", type=Path, help="File or directory to scan")
    parser.add_argument("--report", type=Path, required=True, help="Path to write the markdown report")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"Path does not exist: {args.path}", file=sys.stderr)
        sys.exit(1)

    report = run_audit(args.path)
    output = render_report(args.path, report)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(output, encoding="utf-8")

    print(f"Wrote report to {args.report}")


if __name__ == "__main__":
    main()
