"""jobs/analytics/fast_path_suggestions.py — nightly review of Team Chat
questions Watson had to hand off to an LLM (jobs/analytics/
unanswered_questions.py), looking for repeat shapes that could become a
new LLM-free fast-path phrase instead of costing an API call every time
they're asked again.

Built 2026-09-08 per Bill's ask, right after the deacon-group/birthday
fast-path additions and the "who called Claude" Telegram alert -- this
closes the loop: log what Watson couldn't answer -> review -> propose a
concrete addition -> Bill approves/rejects over Telegram with one tap.

Widened to nightly + broadened 2026-09-09 per Bill's ask, prompted by Jim
Bouchat's Team Chat use for organizing the deacons generating a steady
stream of "Watson called Claude for help" pings: run() now starts by
calling unanswered_questions.sync_claude_answered_questions(), which pulls
in every successfully-answered-but-Claude-billed jobs.analytics.data_chat
question (not just the genuinely-unanswered ones this job originally
reviewed) -- see that function's docstring for why that was most of the
real spend. The goal, per Bill: Watson should get better from every call,
preferably without spending more API calls to do it (this review step
itself still costs one call/run, same as before) -- but instilling a new
learned pattern is worth the occasional API call.

Two kinds of suggestion, both sent to Telegram with Approve/Reject
buttons, handled differently by bot.py's handle_fast_path_suggestion_
callback on Approve:
  - target_id set (matches jobs.analytics.fast_path_patcher's
    CDB_CATEGORY_TARGETS): a genuinely safe one-line addition -- Approve
    auto-applies it (jobs/skills/cdb_query.py edit + git commit + restart
    both services) immediately.
  - target_id None: the question doesn't fit any existing category, so
    answering it without an LLM would need real new logic (like the
    deacon-group/birthday additions did) -- Approve just queues it for a
    real coding session (Bill + Claude Code) rather than attempting a
    risky automated edit; Reject dismisses it.

One Claude/Ollama call per run (job_name="analytics.fast_path_suggestions")
-- a single nightly call analyzing a batch is a rounding error against the
very API spend this whole feature exists to reduce.

Cron (nightly 2:25am, in the existing quiet-hours cluster after
skills_catalog at 2:20am, before backup at 3:00am):
  25 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    -m jobs.analytics.fast_path_suggestions \
    >> /home/billyomes/watson/logs/fast_path_suggestions.log 2>&1
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from core.claude_tier import call_claude
from core.database import get_connection
from core.job_tracker import track_job
from core.vacation import vacation_gate
from jobs.analytics.fast_path_patcher import CDB_CATEGORY_TARGETS, CDB_CATEGORY_LABELS
from jobs.analytics.unanswered_questions import get_open_since, mark_reviewed, sync_claude_answered_questions
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [fast_path_suggestions] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5-coder:7b"  # same model jobs/skills/cdb_query.py's own generation path uses

# Minimum open questions before an analysis run bothers calling the model
# at all -- avoids a weekly Claude/Ollama call (and a possible empty
# Telegram digest) when there's nothing worth looking at.
_MIN_QUESTIONS = 2

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fast_path_suggestions (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id            TEXT,             -- NULL if no existing category fits
                target_label         TEXT NOT NULL,
                new_phrase           TEXT,             -- NULL for "needs new category" suggestions
                example_question     TEXT NOT NULL,
                matched_question_ids TEXT NOT NULL,    -- JSON array of unanswered_questions.id
                reasoning            TEXT,
                status               TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|applied|failed
                applied_detail       TEXT,
                created_at           TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at          TEXT
            )
        """)


_bootstrap()


def _build_prompt(questions: list[dict]) -> tuple[str, str]:
    categories_text = "\n".join(
        f"- {tid}: {CDB_CATEGORY_LABELS[tid]}" for tid in CDB_CATEGORY_TARGETS
    )
    questions_text = "\n".join(
        f"{q['id']}. [{'answered, but only by paying for an LLM call' if q.get('source') == 'claude_call' else 'could NOT be answered -- generic non-answer'}] {q['question']}"
        for q in questions
    )

    system = (
        "You help a church admin assistant (Watson) get better at answering common "
        "questions WITHOUT calling an expensive LLM API, by spotting when several "
        "questions Watson either couldn't answer, or could only answer by paying for an "
        "LLM call, actually share a pattern that fits (or is close to) an EXISTING "
        "recognized question category. Return ONLY a JSON array, no markdown, no "
        "explanation."
    )
    prompt = f"""Here are questions a church staff member asked Watson recently, each tagged \
with whether Watson gave a generic non-answer or actually answered correctly but only by \
paying for an LLM call:

{questions_text}

Watson already recognizes these categories of question (each maps to a real, working \
database query):
{categories_text}

For each question above, decide ONE of:
(a) It's really asking for something one of the categories above already covers, just \
phrased in a way Watson doesn't recognize yet -- propose the exact short trigger phrase \
(lowercase, the way someone would actually type it, e.g. "who's on the prayer list") that \
should be added to that category so Watson recognizes it next time.
(b) It doesn't fit any existing category and would need genuinely new logic to answer \
without an LLM (like a brand-new kind of database query).
(c) It's not really a data question at all (small talk, out of scope, or too vague to ever \
answer from a database) -- skip it entirely, don't include it in your output.

Group questions that share the same underlying pattern into ONE suggestion with multiple \
matched_question_ids, rather than one suggestion per question.

Return a JSON array, each element exactly:
{{"target_id": "<one of the category ids above, or null for case (b)>",
  "new_phrase": "<short lowercase trigger phrase to add, or null for case (b)>",
  "matched_question_ids": [<the question number(s) this suggestion covers>],
  "reasoning": "<one sentence: why this phrase/category, or why it needs new logic>"}}

Only include array elements for case (a) or (b). Never invent a category id that isn't in \
the list above. Return ONLY the JSON array."""
    return system, prompt


def _extract_json_array(raw: str) -> list[dict] | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, list) else None
    except Exception:
        return None


def _call_model(system: str, user: str) -> str | None:
    result = call_claude(
        system=system, user=user, job_name="analytics.fast_path_suggestions",
        max_tokens=2048, message="(weekly fast-path suggestion review)",
    )
    if result:
        return result
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except Exception as exc:
        log.error("Ollama fallback failed: %s", exc)
        return None


def _send_telegram(text: str, reply_markup: dict | None = None) -> None:
    if vacation_gate("normal", "jobs.analytics.fast_path_suggestions", text):
        log.info("Vacation mode is on -- suggestion suppressed (logged).")
        return
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


def _store_suggestion(target_id: str | None, new_phrase: str | None, example_question: str,
                       matched_ids: list[int], reasoning: str) -> int:
    target_label = CDB_CATEGORY_LABELS.get(target_id, "a new kind of question")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO fast_path_suggestions "
            "(target_id, target_label, new_phrase, example_question, matched_question_ids, reasoning) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (target_id, target_label, new_phrase, example_question, json.dumps(matched_ids), reasoning),
        )
        return cur.lastrowid


def _send_suggestion(suggestion_id: int, target_id: str | None, target_label: str,
                      new_phrase: str | None, example_question: str, reasoning: str) -> None:
    if target_id and new_phrase:
        text = (
            f"\U0001f4a1 Fast-path suggestion\n\n"
            f'Someone asked: "{example_question}"\n\n'
            f"Add trigger phrase \"{new_phrase}\" to the existing {target_label} category?\n"
            f"{reasoning}\n\n"
            "Approving applies it immediately (no API call needed for this kind of "
            "question going forward) and restarts Watson to pick it up.\n\n"
            "- Watson"
        )
    else:
        text = (
            f"\U0001f4dd New pattern needed\n\n"
            f'Someone asked: "{example_question}"\n\n'
            f"This doesn't fit anything Watson already recognizes -- {reasoning}\n\n"
            "Approving just flags it for our next coding session (no automatic code "
            "change for something this open-ended).\n\n"
            "- Watson"
        )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"fps_approve:{suggestion_id}"},
            {"text": "❌ Reject", "callback_data": f"fps_reject:{suggestion_id}"},
        ]]
    }
    _send_telegram(text, reply_markup)


def run() -> int:
    """Returns the number of suggestions sent (0 is a normal, quiet night)."""
    synced = sync_claude_answered_questions()
    if synced:
        log.info("Synced %d successfully-answered-but-Claude-billed question(s) from claude_tier_spend_log.", synced)

    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    questions = get_open_since(since)
    if len(questions) < _MIN_QUESTIONS:
        log.info("Only %d open question(s) tonight -- skipping analysis.", len(questions))
        return 0

    system, prompt = _build_prompt(questions)
    raw = _call_model(system, prompt)
    if not raw:
        log.error("Both Claude and Ollama failed to analyze this week's unanswered questions.")
        return 0

    suggestions = _extract_json_array(raw)
    if suggestions is None:
        log.error("Could not parse a JSON array from the model's response: %r", raw[:300])
        return 0

    questions_by_id = {q["id"]: q for q in questions}
    sent = 0
    all_matched_ids: set[int] = set()

    for s in suggestions:
        if not isinstance(s, dict):
            continue
        matched_ids = [i for i in (s.get("matched_question_ids") or []) if i in questions_by_id]
        if not matched_ids:
            continue
        target_id = s.get("target_id")
        if target_id is not None and target_id not in CDB_CATEGORY_TARGETS:
            log.warning("Model proposed an unrecognized target_id %r -- treating as 'needs new pattern'.", target_id)
            target_id = None
        new_phrase = (s.get("new_phrase") or "").strip().lower() or None
        if target_id is None:
            new_phrase = None  # never auto-apply without both a valid target AND a phrase

        example_question = questions_by_id[matched_ids[0]]["question"]
        reasoning = (s.get("reasoning") or "").strip()

        suggestion_id = _store_suggestion(target_id, new_phrase, example_question, matched_ids, reasoning)
        target_label = CDB_CATEGORY_LABELS.get(target_id, "a new kind of question")
        _send_suggestion(suggestion_id, target_id, target_label, new_phrase, example_question, reasoning)
        sent += 1
        all_matched_ids.update(matched_ids)

    # Every open question this run looked at is now reviewed, whether or
    # not it produced a suggestion (a skipped small-talk question doesn't
    # need to be re-analyzed forever) or the model just didn't address it.
    mark_reviewed(list(questions_by_id.keys()))

    if sent == 0:
        log.info("Analyzed %d question(s), no actionable suggestions this week.", len(questions))
    else:
        log.info("Sent %d suggestion(s) covering %d/%d question(s).", sent, len(all_matched_ids), len(questions))
    return sent


if __name__ == "__main__":
    with track_job("analytics.fast_path_suggestions"):
        run()
