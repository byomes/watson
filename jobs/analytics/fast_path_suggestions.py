"""jobs/analytics/fast_path_suggestions.py — review of Team Chat questions
Watson had to hand off to an LLM (jobs/analytics/unanswered_questions.py),
looking for repeat shapes that could become a new LLM-free fast-path phrase
instead of costing an API call every time they're asked again.

Built 2026-09-08 per Bill's ask, right after the deacon-group/birthday
fast-path additions and the "who called Claude" Telegram alert -- this
closes the loop: log what Watson couldn't answer -> review -> propose a
concrete addition -> apply it.

Widened to nightly + broadened 2026-09-09 per Bill's ask, prompted by Jim
Bouchat's Team Chat use for organizing the deacons generating a steady
stream of "Watson called Claude for help" pings: run() starts by calling
unanswered_questions.sync_claude_answered_questions(), which pulls in every
successfully-answered-but-Claude-billed jobs.analytics.data_chat question
(not just the genuinely-unanswered ones this job originally reviewed) --
see that function's docstring for why that was most of the real spend.

**Re-architected 2026-09-14 per Bill's ask** ("change fast path suggestions
to run every time watson calls claude ... if they are simple fixes just
add them and move on"), after a live incident where a Team Chat gap
(events-domain questions) sat uncaught until Bill noticed the cost/latency
himself -- waiting for the next 2:25am cron meant a same-day fix couldn't
happen automatically. Two changes:
  1. review_single_question(), triggered by core/claude_tier.py's
     call_claude() right after every analytics.data_chat Claude call (see
     that module's _trigger_fast_path_review) -- reviews THAT ONE question
     immediately, launched as a detached subprocess so it adds zero
     latency to the reply already sent to the asker. Ollama-only
     (_call_ollama, never call_claude()) -- reviewing every Claude call by
     making another Claude call would double the exact spend this feature
     exists to cut. run()'s nightly batch below is unchanged and still
     cron'd at 2:25am as a backstop for anything the live path misses
     (e.g. this process getting killed mid-run) -- most nights it will
     just find nothing left open.
  2. Simple fixes (target_id set -- an existing jobs.analytics.
     fast_path_patcher.CDB_CATEGORY_TARGETS category, just a new trigger
     phrase) now auto-apply immediately via _auto_apply() -- no Approve/
     Reject gate, in both review_single_question() and run() below. Bill
     still gets a Telegram message either way: an FYI (not a permission
     ask) on success, a real alert if the safe apply itself failed.

**Re-architected again 2026-09-15**, the night after fixing a whole backlog
of "needs real logic" suggestions by hand (fast_path_suggestions ids
9-25, commit 21f8148) that had been sitting on Telegram Approve/Reject
cards -- some for days -- waiting for Bill to remember to start a coding
session. He asked Watson to "fix everything on this list and then rewire
the loop... so that when help is needed it's just automatically coded and
added, don't wait and stack it up for me to remember." Shown the risk
first (several of that backlog turned out to be misclassified or
structurally impossible fixes, and the one thing that WAS already fully
automated -- a one-line trigger-phrase append -- still shipped a dead
bracket-literal trigger the same day, see commit ee8c4d9/21f8148), Bill
chose full autonomy anyway: write, merge, deploy, no review.

A "needs real logic" suggestion (target_id None) no longer goes to a
Telegram card at all -- _auto_dispatch_fix() immediately dispatches a
real, headless Claude Code job (jobs.devdispatch.api._dispatch_claude_
code_job) with auto_merge=1 set on its claude_code_jobs row. jobs/
devdispatch/poller.py (already cron'd every 2 minutes) merges that job's
PR and deploys it (git pull + restart watson-dashboard/watson-bot) the
instant the PR is ready, with nobody in the loop -- see that function's
_auto_merge_and_deploy() and _merge_claude_code_job's docstring in
jobs/devdispatch/api.py for the scoped exception this is (every OTHER
use of dispatch_claude_code_job still requires Bill's manual merge).
Bill gets a Telegram FYI when the fix starts building and another when
it's live (or if dispatch/merge/deploy failed) -- never a permission
ask, same philosophy as the simple-fix path above.

Cron (nightly 2:25am backstop, in the existing quiet-hours cluster after
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
from jobs.analytics.unanswered_questions import (
    advance_claude_call_watermark, get_open_since, mark_reviewed, sync_claude_answered_questions,
)
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

# review_single_question() gate, added per notes/team_chat_conversational_
# memory_spec.md -- every claude_tier.call_claude() call to
# "analytics.data_chat" was treated as a "question" worth reviewing for a
# fast-path pattern, even ones that were never questions at all (e.g. Kaci
# Gravatt's first-ever message, a plain introduction: "Hi Watson, this is
# Kaci. I handle digital communications and event registrations for
# Catalyst." -- sent to Bill's Telegram 2026-09-15 as suggestion_id=22 asking
# him to judge whether it needed a hand-built SQL pattern). A cheap heuristic
# beats a real classifier call here: this only gates a review of a message
# that already got a full LLM-generated-SQL answer, so the cost of getting it
# wrong is one extra/missing weekly-digest row, not a live user-facing reply.
_QUESTION_MARK_RE = re.compile(r"\?")
_WH_OR_AUX_START_RE = re.compile(
    r"^\s*(who|what|when|where|why|how|which|is|are|was|were|do|does|did|"
    r"can|could|will|would|should|has|have|had)\b", re.IGNORECASE,
)
_REQUEST_VERB_RE = re.compile(
    r"\b(find|look ?up|show|list|pull up|get me|give me|tell me|send me|"
    r"remind|schedule|book|check|search|lookup)\b", re.IGNORECASE,
)
_SELF_INTRO_RE = re.compile(
    r"^\s*(hi|hello|hey)[,!.\s]|(^|\.\s*)(this is|i'?m|my name is|i handle|"
    r"i work|i manage|i'?ve|i just)\b", re.IGNORECASE,
)
# A short social filler with no request buried in it -- "good morning",
# "Thanks!", "sounds good" -- shouldn't fall into the ambiguous-default-True
# bucket below just because it doesn't start with "hi"/"hello"/"I'm" the way
# _SELF_INTRO_RE expects. Whole-message match only (anchored both ends) so a
# real question that happens to open with "thanks, but..." isn't swallowed.
_BARE_SOCIAL_RE = re.compile(
    r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening)|thanks?( you)?( so much)?|"
    r"ok(ay)?|sounds good|got it|great|perfect|sure|no worries|you too|"
    r"bye|goodbye|see you( later)?|talk (later|soon))\s*[!.]*\s*$",
    re.IGNORECASE,
)


def _looks_like_question_or_request(text: str) -> bool:
    """True if `text` plausibly asks for something -- a real "?", a
    who/what/how-shaped opener, or an imperative request verb. False for a
    plain statement, greeting, or self-introduction, which is never worth
    reviewing for a fast-path SQL pattern (there's no data-shaped request in
    it to pattern-match against)."""
    text = (text or "").strip()
    if not text:
        return False
    if _QUESTION_MARK_RE.search(text):
        return True
    if _WH_OR_AUX_START_RE.match(text):
        return True
    if _REQUEST_VERB_RE.search(text):
        return True
    if _BARE_SOCIAL_RE.match(text) or _SELF_INTRO_RE.search(text):
        return False
    # Genuinely ambiguous phrasing with no "?" and no recognized shape --
    # default to reviewing it rather than silently dropping a real question
    # that just happened to be phrased without a question mark or a
    # recognized lead word.
    return True


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


def _call_ollama(system: str, user: str) -> str | None:
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
        log.error("Ollama call failed: %s", exc)
        return None


def _call_model(system: str, user: str) -> str | None:
    result = call_claude(
        system=system, user=user, job_name="analytics.fast_path_suggestions",
        max_tokens=2048, message="(weekly fast-path suggestion review)",
    )
    if result:
        return result
    return _call_ollama(system, user)


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


def _build_dispatch_spec(example_question: str, reasoning: str) -> str:
    """Spec text handed to a real, headless Claude Code job for a 'needs
    real logic' gap -- see _auto_dispatch_fix's docstring for why this
    exists. Points it at the actual conventions to follow and, since this
    ships with zero human review before merge+deploy, tells it explicitly
    to stay conservative and to verify its own fix against the real
    question rather than just eyeballing it."""
    return (
        "A Team Chat leader asked Watson (a church admin assistant) this question, and it had "
        "no fast, free way to answer it -- it fell through to a paid LLM call:\n\n"
        f'  "{example_question}"\n\n'
        f"Why this doesn't fit anything that already exists: {reasoning}\n\n"
        "Add a real, working answer for this shape of question. Read jobs/skills/cdb_query.py's "
        "_pattern_match() first -- it's a big if/elif chain of regex/substring-triggered SQL "
        "generators over data/congregation.db (members, attendance, connect_cards, follow_ups, "
        "prayer_requests, next_steps), used by both jobs/analytics/data_chat.py (Team Chat) and "
        "bot.py's DM-only fast paths. Follow its existing conventions exactly -- SPOUSE LOOKUP "
        "and MEMBER'S OWN DEACON (checked before MEMBER LOOKUP BY NAME, same file) are good "
        "examples of the regex-extract-name-then-build-SQL pattern to copy for a new category. "
        "If the question is really about writing/changing data (not just looking it up), check "
        "jobs/congregation/family_edit.py and bot.py's _extract_mark_spouse/_extract_mark_child "
        "first -- there may already be a write path that just needs a new phrasing recognized, "
        "the way add_child/mark_spouse/mark_child work.\n\n"
        "Test whatever regex/logic you add against the ACTUAL question above before finishing -- "
        "run it through the real function, don't just eyeball it. NEVER insert a literal "
        "template placeholder (like '[name]') as a trigger phrase or pattern -- it can never "
        "match real text; extract the real name with a regex capture group instead, the way "
        "every existing pattern in that file does (a past auto-applied fix got this wrong and "
        "shipped a dead trigger -- see commit 21f8148 for the cleanup). This fix ships with NO "
        "human review before it goes live, so be conservative: prefer extending an existing, "
        "working pattern over inventing new schema or new write paths, and if the question is "
        "genuinely ambiguous or risky to answer automatically (e.g. it could write incorrect "
        "data about a real person), it's fine to leave it unanswered/falling through to the LLM "
        "path rather than guess."
    )


def _auto_dispatch_fix(suggestion_id: int, example_question: str, reasoning: str) -> None:
    """Per Bill's 2026-09-15 explicit direction ('fully autonomous -- write,
    merge, deploy, no review') for this one trigger: a 'needs real logic'
    gap no longer sits on a Telegram Approve/Reject card waiting for him to
    remember to start a coding session. It's dispatched to a real Claude
    Code job immediately (jobs.devdispatch.api._dispatch_claude_code_job),
    flagged auto_merge=1 on the claude_code_jobs row so jobs/devdispatch/
    poller.py merges + deploys it the moment the PR is ready, with nobody
    in the loop. Bill gets a Telegram FYI at dispatch time and another once
    it's actually live (or if something failed) -- never a permission ask,
    matching this file's existing 'FYI not a permission ask' philosophy for
    the simple-fix auto-apply path. Replaces the old _send_suggestion
    Approve/Reject-card flow for this branch entirely -- bot.py's
    handle_fast_path_suggestion_callback still exists for its target_id-set
    branch's sake (harmless either way, and a defensive fallback if this
    dispatch itself fails below), but nothing here creates a 'pending' row
    that needs a Telegram tap anymore."""
    from jobs.devdispatch.api import _dispatch_claude_code_job

    spec = _build_dispatch_spec(example_question, reasoning)
    result = _dispatch_claude_code_job(spec, repo="watson", branch_name=f"fastpath/{suggestion_id}")
    job_id = result.get("job_id")
    if result.get("status") != "running" or not job_id:
        err = result.get("error", "unknown error")
        with get_connection() as conn:
            conn.execute(
                "UPDATE fast_path_suggestions SET status='failed', applied_detail=?, resolved_at=datetime('now') WHERE id=?",
                (f"could not dispatch a coding job: {err}", suggestion_id),
            )
        _send_telegram(
            f"⚠️ Tried to auto-fix a Team Chat gap but couldn't even start the coding job: {err}\n\n"
            f'Question: "{example_question}"\n\n- Watson'
        )
        return

    with get_connection() as conn:
        conn.execute(
            "UPDATE claude_code_jobs SET auto_merge=1, source_suggestion_id=? WHERE id=?",
            (suggestion_id, job_id),
        )
        conn.execute(
            "UPDATE fast_path_suggestions SET status='dispatched', applied_detail=? WHERE id=?",
            (f"Auto-dispatched as devdispatch job {job_id} (branch fastpath/{suggestion_id}).", suggestion_id),
        )
        conn.commit()

    _send_telegram(
        f"\U0001f6e0️ Found a Team Chat gap and I'm building a fix automatically -- no action needed.\n\n"
        f'Question: "{example_question}"\n{reasoning}\n\n'
        f"I'll let you know once it's live (devdispatch job {job_id}).\n\n- Watson"
    )


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
        # Per Bill's 2026-09-14 "simple fixes just add them and move on" --
        # applies here too, not just the per-call path (review_single_
        # question) below, so this nightly backstop run behaves the same
        # way if it ever catches something the live path missed. Per his
        # 2026-09-15 "fully autonomous" follow-up, the else branch below no
        # longer waits on a Telegram Approve/Reject tap either -- see
        # _auto_dispatch_fix's docstring.
        if target_id and new_phrase:
            _auto_apply(suggestion_id, target_id, target_label, new_phrase, example_question, reasoning)
        else:
            _auto_dispatch_fix(suggestion_id, example_question, reasoning)
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


def _auto_apply(suggestion_id: int, target_id: str, target_label: str, new_phrase: str,
                 example_question: str, reasoning: str) -> None:
    """Immediately applies a simple, safe fast-path addition — per Bill's
    2026-09-14 "if they are simple fixes just add them and move on," no
    Approve/Reject gate for this kind of suggestion anymore. Still sends a
    Telegram message either way: an FYI (not a permission ask) on success,
    so there's a visible trail and an easy `git revert` if the phrase turns
    out to misfire later; a real alert on failure, since that genuinely does
    need Bill — the safe automated path couldn't be taken."""
    from jobs.analytics.fast_path_patcher import apply_and_deploy
    ok, detail = apply_and_deploy(target_id, new_phrase, actor="Watson (automatic, per-call review)")

    with get_connection() as conn:
        conn.execute(
            "UPDATE fast_path_suggestions SET status=?, applied_detail=?, resolved_at=datetime('now') WHERE id=?",
            ("applied" if ok else "failed", detail, suggestion_id),
        )

    if ok:
        text = (
            f"✅ Auto-added fast-path phrase\n\n"
            f'Someone asked: "{example_question}"\n\n'
            f'Added trigger phrase "{new_phrase}" to {target_label} ({reasoning}).\n'
            f"Commit {detail}. No API call needed for this kind of question going forward.\n\n"
            "No action needed — just letting you know.\n\n- Watson"
        )
    else:
        text = (
            f"⚠️ Couldn't auto-apply a fast-path fix\n\n"
            f'Someone asked: "{example_question}"\n\n'
            f'Tried to add "{new_phrase}" to {target_label} but it failed: {detail}\n\n'
            "Flagging for a coding session instead.\n\n- Watson"
        )
    _send_telegram(text)


def review_single_question(spend_log_id: int, asker_name: str, question: str) -> None:
    """Per Bill's 2026-09-14 ask: review THIS one question right after the
    analytics.data_chat Claude call it came from — launched as a detached
    subprocess by core/claude_tier.py's call_claude(), see that module —
    instead of waiting for the nightly batch (run(), still cron'd at
    2:25am as a backstop for anything this misses, e.g. if this process
    itself gets killed mid-run).

    Simple fixes (an existing cdb_query.py category, just a new trigger
    phrase) get applied immediately via _auto_apply, no approval gate.
    Anything else — a genuinely new category needing real logic, or an
    apply that failed — goes to Bill over Telegram with the same Approve/
    Reject card run() already used, since THAT is a real decision, not a
    mechanical one.

    Ollama-only for the analysis call (_call_ollama, not _call_model) —
    deliberately never call_claude() here: reviewing every single Claude
    call by making another Claude call would double the exact API spend
    this whole feature exists to cut down. run()'s own nightly batch still
    tries Claude first since that's one call a night either way, a
    rounding error against the spend it's analyzing."""
    question = (question or "").strip()
    if not question:
        return
    if not _looks_like_question_or_request(question):
        log.info("review_single_question: id skipped -- not a question/request, asker=%r q=%r", asker_name, question)
        return

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO unanswered_questions (asker_name, question, reply, source) "
            "VALUES (?, ?, NULL, 'claude_call')",
            (asker_name, question),
        )
        row_id = cur.lastrowid

    log.info("review_single_question: reviewing id=%d asker=%r q=%r", row_id, asker_name, question)
    try:
        system, prompt = _build_prompt([{"id": row_id, "question": question, "source": "claude_call"}])
        raw = _call_ollama(system, prompt)
        suggestions = _extract_json_array(raw) if raw else None
        if not raw:
            log.error("review_single_question: Ollama analysis failed for q=%r", question)
        elif suggestions is None:
            log.error("review_single_question: could not parse JSON array: %r", raw[:300])
        elif not suggestions:
            log.info("review_single_question: id=%d -- nothing actionable.", row_id)

        for s in (suggestions or []):
            if not isinstance(s, dict):
                continue
            if row_id not in (s.get("matched_question_ids") or []):
                continue
            target_id = s.get("target_id")
            if target_id is not None and target_id not in CDB_CATEGORY_TARGETS:
                log.warning("review_single_question: unrecognized target_id %r -- treating as 'needs new pattern'.", target_id)
                target_id = None
            new_phrase = (s.get("new_phrase") or "").strip().lower() or None
            if target_id is None:
                new_phrase = None
            reasoning = (s.get("reasoning") or "").strip()

            suggestion_id = _store_suggestion(target_id, new_phrase, question, [row_id], reasoning)
            target_label = CDB_CATEGORY_LABELS.get(target_id, "a new kind of question")

            if target_id and new_phrase:
                log.info("review_single_question: id=%d auto-applying %r to %s", row_id, new_phrase, target_id)
                _auto_apply(suggestion_id, target_id, target_label, new_phrase, question, reasoning)
            else:
                log.info("review_single_question: id=%d needs real logic -- auto-dispatching (suggestion_id=%d).", row_id, suggestion_id)
                _auto_dispatch_fix(suggestion_id, question, reasoning)
    finally:
        mark_reviewed([row_id])
        advance_claude_call_watermark(spend_log_id)


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) >= 4 and _sys.argv[1] == "--single":
        with track_job("analytics.fast_path_suggestions"):
            review_single_question(int(_sys.argv[2]), _sys.argv[3], _sys.argv[4] if len(_sys.argv) > 4 else "")
    else:
        with track_job("analytics.fast_path_suggestions"):
            run()
