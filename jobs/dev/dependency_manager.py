"""jobs/dev/dependency_manager.py — Manage pip packages and detect missing imports."""
import ast
import logging
import subprocess
import sys

log = logging.getLogger(__name__)


def check_outdated() -> list:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
            capture_output=True, text=True,
        )
        import json
        packages = json.loads(result.stdout or "[]")
        return [{"name": p["name"], "current": p["version"], "latest": p["latest_version"]}
                for p in packages]
    except Exception as exc:
        log.error("check_outdated failed: %s", exc)
        return []


def install_package(package: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--break-system-packages", "-q"],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception as exc:
        log.error("install_package failed: %s", exc)
        return False


def check_installed(package: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_imports(file_path: str) -> list:
    try:
        tree = ast.parse(open(file_path).read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
        return sorted(set(imports))
    except Exception as exc:
        log.error("get_imports failed: %s", exc)
        return []


def find_missing_imports(file_path: str) -> list:
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
    imports = get_imports(file_path)
    missing = []
    for pkg in imports:
        if pkg in stdlib:
            continue
        if not check_installed(pkg):
            missing.append(pkg)
    return missing


def run(message: str = None) -> str:
    outdated = check_outdated()
    if not outdated:
        return "All packages up to date."
    lines = [f"Outdated packages ({len(outdated)}):"]
    for p in outdated[:15]:
        lines.append(f"  {p['name']}: {p['current']} → {p['latest']}")
    if len(outdated) > 15:
        lines.append(f"  … and {len(outdated) - 15} more")
    return "\n".join(lines)
