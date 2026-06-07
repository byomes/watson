"""jobs/dev/code_quality.py — Format, lint, and security-scan Python code."""
import ast
import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def format_code(code: str) -> str:
    import black
    try:
        return black.format_str(code, mode=black.Mode())
    except Exception as exc:
        log.warning("black format failed: %s", exc)
        return code


def lint_code(code: str) -> list:
    tmp = Path(tempfile.gettempdir()) / "watson_lint_check.py"
    tmp.write_text(code, encoding="utf-8")
    try:
        result = subprocess.run(
            ["python3", "-m", "pylint", str(tmp), "--output-format=text",
             "--disable=C,R,W0611,W0614", "--score=no"],
            capture_output=True, text=True,
        )
        issues = [
            line for line in result.stdout.splitlines()
            if line.strip() and not line.startswith("*") and not line.startswith("-")
        ]
        return issues
    except Exception as exc:
        log.error("pylint failed: %s", exc)
        return []
    finally:
        tmp.unlink(missing_ok=True)


def security_scan(code: str) -> list:
    tmp = Path(tempfile.gettempdir()) / "watson_bandit_check.py"
    tmp.write_text(code, encoding="utf-8")
    try:
        result = subprocess.run(
            ["python3", "-m", "bandit", "-q", str(tmp)],
            capture_output=True, text=True,
        )
        issues = [line for line in result.stdout.splitlines() if line.strip()]
        return issues
    except Exception as exc:
        log.error("bandit failed: %s", exc)
        return []
    finally:
        tmp.unlink(missing_ok=True)


def type_check(file_path: str) -> list:
    try:
        result = subprocess.run(
            ["python3", "-m", "mypy", file_path, "--ignore-missing-imports"],
            capture_output=True, text=True,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]
    except Exception as exc:
        log.error("mypy failed: %s", exc)
        return []


def full_check(code: str) -> dict:
    formatted = format_code(code)
    lint_issues = lint_code(formatted)
    security_issues = security_scan(formatted)
    return {
        "formatted": formatted,
        "lint_issues": lint_issues,
        "security_issues": security_issues,
    }


def run(message: str = None) -> str:
    if not message:
        return "Code quality tools ready."
    result = full_check(message)
    lines = ["Code Quality Report", "─" * 20]
    if result["lint_issues"]:
        lines.append(f"Lint ({len(result['lint_issues'])} issues):")
        lines.extend(f"  {i}" for i in result["lint_issues"][:10])
    else:
        lines.append("Lint: ✓ No issues")
    if result["security_issues"]:
        lines.append(f"Security ({len(result['security_issues'])} issues):")
        lines.extend(f"  {i}" for i in result["security_issues"][:5])
    else:
        lines.append("Security: ✓ No issues")
    return "\n".join(lines)
