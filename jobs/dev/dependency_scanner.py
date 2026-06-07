"""jobs/dev/dependency_scanner.py — scan skill files for missing or conflicting imports."""
import ast
import importlib.util
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]

_STDLIB = frozenset({
    "os", "sys", "re", "json", "time", "datetime", "pathlib", "logging",
    "subprocess", "threading", "ast", "importlib", "io", "tempfile",
    "sqlite3", "hashlib", "base64", "collections", "functools", "itertools",
    "contextlib", "typing", "dataclasses", "enum", "abc", "copy", "math",
    "random", "string", "textwrap", "traceback", "inspect", "types",
    "shutil", "glob", "fnmatch", "zipfile", "tarfile", "csv", "configparser",
    "argparse", "signal", "socket", "urllib", "http", "email", "html",
    "xml", "struct", "array", "queue", "concurrent", "multiprocessing",
    "gc", "weakref", "operator", "heapq", "bisect", "pprint", "warnings",
    "unittest", "py_compile", "dis", "tokenize", "keyword", "tracemalloc",
    "zoneinfo", "calendar", "locale", "codecs", "unicodedata", "decimal",
    "fractions", "statistics", "cmath", "numbers", "binascii",
})


def scan_skill_imports(file_path: str) -> dict:
    p = Path(file_path)
    if not p.exists():
        return {"file": file_path, "imports": [], "missing": [], "conflicts": [], "all_ok": False}

    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content)
    except Exception as exc:
        return {"file": str(p), "imports": [], "missing": [], "conflicts": [str(exc)], "all_ok": False}

    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])

    third_party = sorted(modules - _STDLIB - {"__future__", ""})
    missing = [m for m in third_party if importlib.util.find_spec(m) is None]

    rel = str(p.relative_to(REPO)) if REPO in p.parents else str(p)
    return {
        "file": rel,
        "imports": third_party,
        "missing": missing,
        "conflicts": [],
        "all_ok": len(missing) == 0,
    }


def scan_all_skills() -> str:
    job_files = [
        f for f in (REPO / "jobs").rglob("*.py")
        if "__pycache__" not in str(f)
    ]

    problem_files = {}
    for f in sorted(job_files):
        result = scan_skill_imports(str(f))
        if result["missing"]:
            problem_files[result["file"]] = result["missing"]

    lines = [f"Dependency scan: {len(job_files)} files, {len(problem_files)} with missing imports\n"]
    for path, missing in sorted(problem_files.items()):
        lines.append(f"✗ {path}")
        lines.append(f"  Missing: {', '.join(missing)}")
    if not problem_files:
        lines.append("All imports resolved.")
    return "\n".join(lines)


def run(message: str = None) -> str:
    if message:
        match = re.search(r'[\w/._\-]+\.py', message)
        if match:
            result = scan_skill_imports(match.group())
            if result["all_ok"]:
                return f"✓ {result['file']}: all imports OK"
            return f"✗ {result['file']}: missing {', '.join(result['missing'])}"
    return scan_all_skills()
