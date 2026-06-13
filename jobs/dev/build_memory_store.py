import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

WATSON_ROOT = Path.home() / "watson"

log = logging.getLogger(__name__)

BUILDS_DIR = WATSON_ROOT / "memory" / "builds"
BUILD_INDEX = BUILDS_DIR / "BUILD_INDEX.md"
MEMORY_LOG = WATSON_ROOT / "logs" / "build-memory.log"
ERRORS_LOG = WATSON_ROOT / "logs" / "build-memory-errors.log"


def _log_error(build_id: str, message: str) -> None:
    ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ERRORS_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} [{build_id}] {message}\n")
    log.error("[%s] %s", build_id, message)


def run(
    build_name: str,
    spec_text: str,
    code_diff: str,
    test_output: str,
    claude_review_json: dict,
    human_approval: str,
    services_restarted: list[str],
    files_changed: list[str],
) -> dict:
    now = datetime.now(timezone.utc)
    build_id = now.strftime("%Y%m%d-%H%M%S") + f"-{build_name}"
    build_dir = BUILDS_DIR / build_id

    try:
        build_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _log_error(build_id, f"Failed to create build directory {build_dir}: {exc}")
        raise

    ts = now.isoformat()
    claude_recommendation = claude_review_json.get("recommendation", "unknown")

    files_to_write = {
        "spec.md": spec_text,
        "code-diff.patch": code_diff,
        "test-output.log": test_output,
        "claude-review.json": json.dumps(claude_review_json, indent=2),
        "human-approval.txt": f"Approved: {ts}\n\n{human_approval}\n",
        "deployment-log.txt": (
            f"Deployed: {ts}\n\n"
            f"Services restarted:\n"
            + "\n".join(f"  - {s}" for s in services_restarted)
            + "\n"
        ),
        "metadata.json": json.dumps(
            {
                "build_id": build_id,
                "build_name": build_name,
                "timestamp": ts,
                "files_changed": files_changed,
                "services_restarted": services_restarted,
                "claude_recommendation": claude_recommendation,
            },
            indent=2,
        ),
    }

    failed: list[str] = []
    for filename, content in files_to_write.items():
        try:
            (build_dir / filename).write_text(content, encoding="utf-8")
        except Exception as exc:
            msg = f"Failed to write {filename}: {exc}\n{traceback.format_exc()}"
            _log_error(build_id, msg)
            failed.append(filename)

    if failed:
        raise RuntimeError(
            f"Build memory store incomplete — {len(failed)} file(s) failed: {failed}"
        )

    services_str = ", ".join(services_restarted) if services_restarted else "none"
    index_line = f"{ts} | {build_name} | {services_str} | {claude_recommendation}\n"
    try:
        BUILD_INDEX.parent.mkdir(parents=True, exist_ok=True)
        with BUILD_INDEX.open("a", encoding="utf-8") as f:
            f.write(index_line)
    except Exception as exc:
        _log_error(build_id, f"Failed to append to BUILD_INDEX.md: {exc}")

    MEMORY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_LOG.open("a", encoding="utf-8") as f:
        f.write(
            f"{ts} SUCCESS build_id={build_id} "
            f"files={len(files_to_write)} "
            f"recommendation={claude_recommendation}\n"
        )
    log.info(
        "Build memory stored: %s  recommendation=%s  services=%s",
        build_dir,
        claude_recommendation,
        services_str,
    )

    return {
        "build_id": build_id,
        "build_dir": str(build_dir),
        "files_written": list(files_to_write.keys()),
        "claude_recommendation": claude_recommendation,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    result = run(
        build_name="health-check-endpoint",
        spec_text="Add a /health endpoint to the dashboard that returns {status: ok}.",
        code_diff=(
            "--- a/jobs/dashboard/app.py\n"
            "+++ b/jobs/dashboard/app.py\n"
            "@@ -10,0 +11 @@\n"
            "+@app.route('/health')\n"
            "+def health(): return {'status': 'ok'}\n"
        ),
        test_output="test_health_check PASSED\n1 passed in 0.12s",
        claude_review_json={
            "recommendation": "deploy",
            "confidence": 0.95,
            "assessment": "Minimal, well-scoped change with full test coverage.",
            "risks": [],
            "strengths": ["Single-responsibility", "Covered by test"],
            "required_changes": None,
            "deployment_safety": "high",
        },
        human_approval="LGTM — deploy to production.",
        services_restarted=["watson-dashboard"],
        files_changed=["jobs/dashboard/app.py"],
    )

    print(json.dumps(result, indent=2))
