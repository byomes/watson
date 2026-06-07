"""jobs/dev/code_editor.py — Read and edit Watson files directly with git commits."""
import ast
import logging
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

log = logging.getLogger(__name__)


def _git_commit(path: str, message: str) -> bool:
    try:
        subprocess.run(["git", "add", path], cwd=str(REPO), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(REPO), check=True, capture_output=True)
        return True
    except Exception as exc:
        log.error("git commit failed: %s", exc)
        return False


def _syntax_ok(path: str) -> bool:
    try:
        ast.parse(Path(path).read_text(encoding="utf-8"))
        return True
    except SyntaxError as exc:
        log.warning("syntax check failed: %s", exc)
        return False


def read_file(path: str) -> str:
    full = REPO / path
    try:
        return full.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading {path}: {exc}"


def write_file(path: str, content: str) -> bool:
    full = REPO / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    if path.endswith(".py") and not _syntax_ok(str(full)):
        log.warning("write_file: syntax error in %s — file written but not committed", path)
        return False
    return _git_commit(str(full), f"edit: {path}")


def find_and_replace(path: str, old: str, new: str) -> bool:
    full = REPO / path
    try:
        content = full.read_text(encoding="utf-8")
        if old not in content:
            log.warning("find_and_replace: string not found in %s", path)
            return False
        full.write_text(content.replace(old, new, 1), encoding="utf-8")
        if path.endswith(".py") and not _syntax_ok(str(full)):
            full.write_text(content, encoding="utf-8")  # rollback
            return False
        return _git_commit(str(full), f"edit: {path}")
    except Exception as exc:
        log.error("find_and_replace failed: %s", exc)
        return False


def append_to_file(path: str, content: str) -> bool:
    full = REPO / path
    try:
        existing = full.read_text(encoding="utf-8") if full.exists() else ""
        full.write_text(existing.rstrip() + "\n\n" + content + "\n", encoding="utf-8")
        if path.endswith(".py") and not _syntax_ok(str(full)):
            full.write_text(existing, encoding="utf-8")  # rollback
            return False
        return _git_commit(str(full), f"edit: {path}")
    except Exception as exc:
        log.error("append_to_file failed: %s", exc)
        return False


def insert_after_line(path: str, line_number: int, content: str) -> bool:
    full = REPO / path
    try:
        lines = full.read_text(encoding="utf-8").splitlines(keepends=True)
        lines.insert(line_number, content + "\n")
        full.write_text("".join(lines), encoding="utf-8")
        if path.endswith(".py") and not _syntax_ok(str(full)):
            full.write_text("".join(lines[:line_number] + lines[line_number + 1:]), encoding="utf-8")
            return False
        return _git_commit(str(full), f"edit: {path}")
    except Exception as exc:
        log.error("insert_after_line failed: %s", exc)
        return False


def add_function(path: str, function_code: str) -> bool:
    return append_to_file(path, function_code)


def run(message: str = None) -> str:
    return "Code editor ready. I can read and edit Watson files directly."
