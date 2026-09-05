# Cron: 45 1 * * * (nightly, ahead of KB sync at 2am and local backup at 2:30am)
"""jobs/session_archives/claude_export_import.py — nightly ingest of a Claude.ai
account-data export dropped at ~/watson/incoming/claude_export/.

Bill periodically exports his Claude.ai account (Settings -> Export data),
downloads the resulting zips, and scp's them to DROP_DIR. This job:
  1. Looks for conversations-*.zip (required) and projects-*.zip (optional,
     used for classification only) in DROP_DIR.
  2. Extracts, skips any conversation whose uuid is already archived
     (storage.known_source_uuids()) — repeat exports overlap heavily with
     what's already in, since each export is a full account snapshot, not a
     delta.
  3. Classifies each new conversation against Bill's named Claude.ai projects
     (jobs.session_archives.classify) and archives it straight into that
     project, or FALLBACK_PROJECT if nothing clears the confidence threshold.
  4. Deletes the consumed zips + scratch extraction dir — the content is now
     durably in data/session_archives/, covered by both backup legs, so the
     raw export has no reason to linger.
  5. Telegram summary every run, even a no-op one (matches jobs/kb/
     sync_and_index.py's "nothing new" ping) — a silent night should look
     different from a broken cron job, not identical to it.

Does NOT reclassify anything already sitting in FALLBACK_PROJECT from a prior
run — that's jobs/session_archives/backfill_reclassify.py, a manual tool, on
purpose (moving already-archived content around every night on a shifting
classification boundary would be more churn than value).
"""
import json
import shutil
import time
import zipfile
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.session_archives import classify
from jobs.session_archives import claude_export_render as render
from jobs.session_archives import storage
from jobs.session_archives.schema import create_tables

WATSON_DIR = Path(__file__).resolve().parents[2]
DROP_DIR = WATSON_DIR / "incoming" / "claude_export"
WORK_DIR = DROP_DIR / "_work"
FALLBACK_PROJECT = "claude-account-import"
LOG = WATSON_DIR / "logs" / "claude_export_import.log"


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        _log(f"Telegram send failed: {exc}")


def _extract_all(zip_paths: list, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for zp in zip_paths:
        with zipfile.ZipFile(zp) as zf:
            zf.extractall(dest)


def _load_conversations(work_dir: Path) -> list:
    matches = list(work_dir.glob("conversations.json")) or list(work_dir.rglob("conversations.json"))
    if not matches:
        return []
    with open(matches[0]) as f:
        return json.load(f)


def _load_projects(work_dir: Path) -> list:
    proj_dir = None
    for candidate in work_dir.rglob("projects"):
        if candidate.is_dir():
            proj_dir = candidate
            break
    if not proj_dir:
        return []
    projects = []
    for fp in proj_dir.glob("*.json"):
        try:
            with open(fp) as f:
                projects.append(json.load(f))
        except Exception:
            continue
    return projects


def run() -> dict:
    create_tables()

    conv_zips = sorted(DROP_DIR.glob("conversations-*.zip")) if DROP_DIR.is_dir() else []
    if not conv_zips:
        _log("No conversations-*.zip in drop folder — nothing to do.")
        _send_telegram("📥 Claude export sync: nothing new.")
        return {"status": "nothing_to_do"}

    proj_zips = sorted(DROP_DIR.glob("projects-*.zip"))
    all_zips = conv_zips + proj_zips + sorted(DROP_DIR.glob("light_metadata-*.zip")) + sorted(DROP_DIR.glob("memories-*.zip"))

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    try:
        _extract_all(all_zips, WORK_DIR)
        conversations = _load_conversations(WORK_DIR)
        projects = _load_projects(WORK_DIR)
    except Exception as exc:
        _log(f"ERROR extracting/parsing export: {exc}")
        _send_telegram(f"❌ Claude export sync failed to read the export files: {exc}")
        return {"status": "error", "error": str(exc)}

    if not conversations:
        _log("conversations.json had zero entries — treating as nothing to do.")
        _send_telegram("📥 Claude export sync: export unpacked but contained no conversations.")
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        for zp in all_zips:
            zp.unlink(missing_ok=True)
        return {"status": "empty_export"}

    known_uuids = storage.known_source_uuids()
    new_conversations = [c for c in conversations if c.get("uuid") not in known_uuids]
    skipped_duplicate = len(conversations) - len(new_conversations)

    project_refs = classify.build_project_refs(projects)
    classifications = classify.classify(
        [{"name": c.get("name"), "summary": c.get("summary")} for c in new_conversations],
        project_refs,
    )

    per_project_counts = {}
    secrets_flagged = 0
    files_attached = 0
    errors = []

    for c, (project_slug, score) in zip(new_conversations, classifications):
        target_project = project_slug or FALLBACK_PROJECT
        title = render.build_title(c)
        summary = render.build_summary(c)
        transcript = render.render_transcript(c)
        files = render.extract_files(c)

        result = storage.archive_session(
            transcript=transcript,
            files=files,
            project=target_project,
            title=title,
            summary=summary,
            source_conversation_uuid=c.get("uuid"),
        )
        if "error" in result:
            errors.append({"uuid": c.get("uuid"), "name": c.get("name"), "error": result["error"]})
            continue

        actual_project = result["project"]
        per_project_counts[actual_project] = per_project_counts.get(actual_project, 0) + 1
        if result.get("secrets_flagged"):
            secrets_flagged += 1
        files_attached += result.get("file_count", 0)

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    for zp in all_zips:
        zp.unlink(missing_ok=True)

    imported = len(new_conversations) - len(errors)
    _log(
        f"Export processed: {len(conversations)} in export, {skipped_duplicate} already known, "
        f"{imported} newly archived, {len(errors)} errors, {secrets_flagged} secrets-flagged, "
        f"{files_attached} files attached. Per-project: {per_project_counts}"
    )

    if imported == 0 and not errors:
        _send_telegram(f"📥 Claude export sync: {skipped_duplicate} conversation(s) in export, all already archived.")
    else:
        lines = [f"📥 Claude export sync: {imported} new conversation(s) archived ({skipped_duplicate} already known)."]
        for proj, count in sorted(per_project_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  • {proj}: {count}")
        if secrets_flagged:
            lines.append(f"⚠ {secrets_flagged} flagged for possible secrets — review with search_archives.")
        if errors:
            lines.append(f"❌ {len(errors)} failed — see {LOG.name}")
        _send_telegram("\n".join(lines))

    return {
        "status": "ok",
        "total_in_export": len(conversations),
        "skipped_duplicate": skipped_duplicate,
        "imported": imported,
        "errors": errors,
        "secrets_flagged": secrets_flagged,
        "files_attached": files_attached,
        "per_project_counts": per_project_counts,
    }


if __name__ == "__main__":
    run()
