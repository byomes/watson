"""jobs/dev/test_runner.py — Run Watson's test suite and generate skill tests."""
import logging
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO / "tests"

log = logging.getLogger(__name__)


def run_tests(path: str = None) -> str:
    target = str(Path(path) if path else TESTS_DIR)
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", target, "-v", "--tb=short", "--no-header", "-q"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        lines = [l for l in output.splitlines() if l.strip()]
        return "\n".join(lines[-30:]) if lines else "No test output."
    except FileNotFoundError:
        return "pytest not installed or no test files found."
    except subprocess.TimeoutExpired:
        return "Test run timed out (120s)."
    except Exception as exc:
        log.error("run_tests failed: %s", exc)
        return f"Error: {exc}"


def write_test(skill_module: str, function_name: str, test_cases: list) -> str:
    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = skill_module.replace(".", "_")
    test_path = TESTS_DIR / f"test_{slug}.py"
    lines = [
        f"\"\"\"Auto-generated tests for {skill_module}.{function_name}\"\"\"",
        f"from {skill_module} import {function_name}",
        "",
    ]
    for i, tc in enumerate(test_cases):
        args = tc.get("args", {})
        expected = tc.get("expected")
        arg_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
        lines.append(f"def test_{function_name}_{i}():")
        if expected is not None:
            lines.append(f"    result = {function_name}({arg_str})")
            lines.append(f"    assert result == {repr(expected)}")
        else:
            lines.append(f"    result = {function_name}({arg_str})")
            lines.append(f"    assert result is not None")
        lines.append("")
    test_path.write_text("\n".join(lines), encoding="utf-8")
    return str(test_path)


def run(message: str = None) -> str:
    if not TESTS_DIR.exists() or not list(TESTS_DIR.glob("test_*.py")):
        return "No tests found in ~/watson/tests/. Use write_test() to generate tests for a skill."
    return run_tests()
