import requests

from jobs.email_job.gmail import send_email

_OLLAMA_URL = "http://localhost:11434/api/generate"
_MODEL = "qwen2.5-coder:7b"
_SYSTEM_PROMPT = (
    "You are Watson's build spec generator. Given a task description, produce a structured build spec with these sections:\n"
    "- Job Name\n"
    "- Purpose (one sentence)\n"
    "- File(s) to create\n"
    "- Dependencies (pip packages if any)\n"
    "- Step-by-step implementation plan\n"
    "- Cron schedule (if applicable)\n"
    "- Notes / edge cases\n"
    "Be concise and specific. Output plain text only."
)
_TO = "bill.yomes@gmail.com"


def _extract_job_name(spec: str) -> str:
    for line in spec.splitlines():
        stripped = line.strip().lstrip("-").lstrip("*").strip()
        if stripped.lower().startswith("job name"):
            parts = stripped.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
    return "Unknown Job"


def run_code_agent(task: str) -> str:
    try:
        resp = requests.post(
            _OLLAMA_URL,
            json={
                "model": _MODEL,
                "system": _SYSTEM_PROMPT,
                "prompt": task,
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        spec = resp.json()["response"]
    except Exception as exc:
        return f"Spec generation failed: {exc}"

    try:
        job_name = _extract_job_name(spec)
        send_email(
            to=_TO,
            subject=f"Watson Build Spec: {job_name}",
            body=spec,
        )
    except Exception as exc:
        return f"Spec generation failed: {exc}"

    return "Spec sent to your email."
