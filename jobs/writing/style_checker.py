"""jobs/writing/style_checker.py — prose style analysis using proselint."""
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]


def check_style(text: str) -> dict:
    """Run proselint on text, return structured results."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp_path = f.name
    try:
        r = subprocess.run(
            ["proselint", "check", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
        issues = []
        for line in lines:
            # format: filename:row:col: rule: message
            m = re.match(r'^.+?:(\d+):(\d+):\s*([\w.]+):\s*(.+)$', line)
            if m:
                issues.append({
                    "line": int(m.group(1)),
                    "col": int(m.group(2)),
                    "rule": m.group(3),
                    "message": m.group(4),
                })
            else:
                issues.append({"line": 0, "col": 0, "rule": "unknown", "message": line})
        return {"issue_count": len(issues), "issues": issues, "error": None}
    except subprocess.TimeoutExpired:
        return {"issue_count": 0, "issues": [], "error": "proselint timed out"}
    except FileNotFoundError:
        return {"issue_count": 0, "issues": [], "error": "proselint not installed"}
    finally:
        os.unlink(tmp_path)


def check_file(file_path: str) -> dict:
    p = Path(file_path).expanduser()
    if not p.exists():
        return {"issue_count": 0, "issues": [], "error": f"File not found: {file_path}"}
    return check_style(p.read_text(encoding="utf-8", errors="ignore"))


def run(message: str = None) -> str:
    if not message:
        return "Usage: check style <text or file path>"

    # Treat as file path if it looks like one
    if re.search(r'\.(txt|md|rst|doc)$', message.strip(), re.IGNORECASE):
        result = check_file(message.strip())
    else:
        result = check_style(message)

    if result["error"]:
        return f"Style check error: {result['error']}"
    if not result["issues"]:
        return "No style issues found."

    lines = [f"Style issues ({result['issue_count']} found):"]
    for issue in result["issues"][:20]:
        lines.append(f"  L{issue['line']}:{issue['col']} [{issue['rule']}] {issue['message']}")
    if result["issue_count"] > 20:
        lines.append(f"  ...and {result['issue_count'] - 20} more")
    return "\n".join(lines)
