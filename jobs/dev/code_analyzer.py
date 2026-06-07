"""jobs/dev/code_analyzer.py — Analyze Watson's Python codebase structure."""
import ast
import logging
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO / "jobs"

log = logging.getLogger(__name__)


def analyze_file(path: str) -> dict:
    try:
        source = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as exc:
        return {"error": str(exc), "functions": [], "classes": [], "imports": [], "lines": 0}

    functions = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    lines = len(source.splitlines())
    # Simple cyclomatic complexity: count if/for/while/try branches
    complexity = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler))
    )

    return {
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "lines": lines,
        "complexity": complexity,
    }


def find_functions_without_run(directory: str = "jobs/") -> list:
    target = REPO / directory
    missing = []
    for py_file in sorted(target.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            func_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            if "run" not in func_names:
                missing.append(str(py_file.relative_to(REPO)))
        except Exception:
            pass
    return missing


def analyze_watson_codebase() -> str:
    py_files = list(JOBS_DIR.rglob("*.py"))
    non_init = [f for f in py_files if f.name != "__init__.py"]

    total_lines = 0
    total_functions = 0
    total_classes = 0

    for f in non_init:
        info = analyze_file(str(f))
        total_lines += info.get("lines", 0)
        total_functions += len(info.get("functions", []))
        total_classes += len(info.get("classes", []))

    missing_run = find_functions_without_run()

    lines = [
        "Watson Codebase Report",
        "──────────────────────",
        f"Python files:   {len(non_init)}",
        f"Total lines:    {total_lines:,}",
        f"Functions:      {total_functions}",
        f"Classes:        {total_classes}",
        f"Missing run():  {len(missing_run)}",
    ]
    if missing_run:
        lines.append("\nFiles missing run():")
        for f in missing_run[:15]:
            lines.append(f"  {f}")
        if len(missing_run) > 15:
            lines.append(f"  … and {len(missing_run) - 15} more")

    return "\n".join(lines)


def run(message: str = None) -> str:
    return analyze_watson_codebase()
