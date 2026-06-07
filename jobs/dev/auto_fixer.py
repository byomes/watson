"""jobs/dev/auto_fixer.py — automatically fix common skill errors."""
import ast
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]

_IMPORT_FIXES = {
    "python_dotenv": ("python-dotenv", None),
    "PIL": ("pillow", None),
    "cv2": ("opencv-python-headless", None),
    "sklearn": ("scikit-learn", None),
}


def _git(cmd: list) -> bool:
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    return r.returncode == 0


def fix_missing_run(file_path: str) -> bool:
    p = Path(file_path)
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8")
    if re.search(r'^\s*def run\(', content, re.MULTILINE):
        return False
    stub = f'\n\ndef run(message: str = None) -> str:\n    return "{p.stem} skill ready."\n'
    p.write_text(content.rstrip() + stub, encoding="utf-8")
    _git(["git", "add", str(p)])
    _git(["git", "commit", "-m", f"fix: add missing run() to {file_path}"])
    log.info("fix_missing_run: added run() to %s", file_path)
    return True


def fix_bad_imports(file_path: str) -> bool:
    p = Path(file_path)
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False

    fixed = False
    for node in ast.walk(tree):
        top = None
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
        if top and top in _IMPORT_FIXES:
            pkg, _ = _IMPORT_FIXES[top]
            subprocess.run(
                ["pip", "install", pkg, "--break-system-packages"],
                capture_output=True, text=True,
            )
            fixed = True
    return fixed


def fix_tilde_paths(file_path: str) -> bool:
    p = Path(file_path)
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8")
    # Match "~/" strings not already inside expanduser(
    pattern = r'(?<!expanduser\()"(~\/[^"]+)"'
    if not re.search(pattern, content):
        return False
    fixed = re.sub(pattern, lambda m: f'os.path.expanduser("{m.group(1)}")', content)
    if fixed == content:
        return False
    p.write_text(fixed, encoding="utf-8")
    _git(["git", "add", str(p)])
    _git(["git", "commit", "-m", f"fix: expand tilde paths in {file_path}"])
    log.info("fix_tilde_paths: fixed paths in %s", file_path)
    return True


def auto_fix_skill(slug: str) -> str:
    skills_file = REPO / "memory" / "skills.json"
    try:
        skills = json.loads(skills_file.read_text(encoding="utf-8"))
        skill = next((s for s in skills if s["slug"] == slug), None)
    except Exception as exc:
        return f"Could not load skills.json: {exc}"

    if not skill:
        return f"Skill '{slug}' not found."

    full_path = str(REPO / (skill["job_module"].replace(".", "/") + ".py"))

    fixes = []
    if fix_missing_run(full_path):
        fixes.append("added run()")
    if fix_bad_imports(full_path):
        fixes.append("fixed imports")
    if fix_tilde_paths(full_path):
        fixes.append("fixed tilde paths")

    from jobs.dev.skill_tester import test_skill
    result = test_skill(slug)
    if result["success"]:
        applied = ", ".join(fixes) if fixes else "no changes needed"
        return f"✓ Auto-fixed and validated: {slug} ({applied})"

    from jobs.dev.error_analyzer import analyze_skill_error
    analysis = analyze_skill_error(slug)
    return f"✗ {slug} still failing after fixes.\n\n{analysis}"


def run(message: str = None) -> str:
    if not message:
        return "Auto-fixer ready. Give me a skill slug to fix."
    match = re.search(r'[\w]+', message.replace("-", "_"))
    slug = match.group() if match else message.strip()
    return auto_fix_skill(slug)
