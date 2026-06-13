import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

WATSON_ROOT = Path.home() / "watson"
load_dotenv(WATSON_ROOT / ".env")

log = logging.getLogger(__name__)

REVIEWS_DIR = WATSON_ROOT / "logs" / "build-reviews"
ERRORS_LOG = REVIEWS_DIR / "errors.log"

_SYSTEM_PROMPT = (
    "You are a code deployment reviewer for Watson, an AI assistant system running on a Beelink mini PC. "
    "Analyze the code diff, test results, and spec. "
    "Check for: breaking changes to existing Watson jobs, conflicts with existing services, "
    "test coverage gaps, security issues, logic errors. "
    "Return structured JSON only."
)


def run(
    spec: str,
    code_diff: str,
    test_output: str,
    systems_affected: list[str],
    build_metadata: dict | None = None,
) -> dict:
    import anthropic

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)

    systems_str = ", ".join(systems_affected) if systems_affected else "none"
    meta_str = json.dumps(build_metadata or {}, indent=2)

    user_message = (
        f"=== SPEC ===\n{spec}\n\n"
        f"=== CODE DIFF ===\n{code_diff}\n\n"
        f"=== TEST OUTPUT ===\n{test_output}\n\n"
        f"=== SYSTEMS AFFECTED ===\n{systems_str}\n\n"
        f"=== BUILD METADATA ===\n{meta_str}\n\n"
        "Return a JSON object with these exact keys:\n"
        "  recommendation (string: \"deploy\" or \"refine\"),\n"
        "  confidence (float 0.0–1.0),\n"
        "  assessment (string: full review text),\n"
        "  risks (array of strings),\n"
        "  strengths (array of strings),\n"
        "  required_changes (null or array of strings),\n"
        "  deployment_safety (string: \"high\", \"medium\", or \"low\")"
    )

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0].strip()

        result = json.loads(raw)

    except Exception as exc:
        log.error("Claude API final review failed: %s", exc)
        ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERRORS_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} ERROR: {exc}\n")
        return {"recommendation": "error", "assessment": "Claude API unavailable"}

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = REVIEWS_DIR / f"{timestamp}-review.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    log.info("Build review saved: %s  recommendation=%s", out_path, result.get("recommendation"))

    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    sample = run(
        spec="Add a health-check endpoint at /health that returns {status: ok}.",
        code_diff=(
            "--- a/jobs/dashboard/app.py\n"
            "+++ b/jobs/dashboard/app.py\n"
            "@@ -10,0 +11 @@\n"
            "+@app.route('/health')\n"
            "+def health(): return {'status': 'ok'}\n"
        ),
        test_output="test_health_check PASSED",
        systems_affected=["dashboard"],
        build_metadata={"build_id": "test-001", "branch": "main"},
    )

    print(json.dumps(sample, indent=2))
