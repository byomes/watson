"""jobs/analytics/fast_path_validate.py -- the gate every AUTOMATIC fast-path phrase must pass before it is written to cdb_query.py.

Why this exists (2026-10-06): fast_path_suggestions.py asked a 7B model which attendance category a question belonged to, and the only check before
the phrase was committed and the bot restarted was `ast.parse()` (does the file still compile?). For "How many people are signed up for men's
fraternity tomorrow night" the model answered 'how_many_attended' + phrase 'signed up'. It compiled, so it shipped, and every event-signup question
then answered with last Sunday's attendance (5a96dc4). Compiling says nothing about whether a phrase is RIGHT.

evaluate(target_id, new_phrase, example_question) -> {"ok": bool, "reasons": [...], "changed_others": [...], ...}. A phrase is accepted only if:
  1. it is specific: at least two content words, not just a generic frame ("how many", "who is");
  2. it does not belong to another domain: no signup/registration/RSVP/ticket words, no words that only appear in event names;
  3. the example question is not itself an event/signup question (those have their own hand-maintained fast path, jobs/events/pattern_match.py);
  4. it actually FIXES the example question: after the patch, cdb_query._pattern_match answers it (before the patch it did not);
  5. replaying every logged question (Bill's and the leaders' inbound messages, plus the must-not-change list below) through the patched and the
     unpatched matcher shows no collision: no OTHER question may change its attendance answer, and none may be a question the events/calendar routes
     already claim. A phrase that quietly reroutes unrelated questions is exactly the failure this prevents.
A rejected phrase is not applied; the caller records why and sends it to a human (Bill) instead.
"""
import re
import sqlite3
import types
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "data" / "watson.db"
CDB_QUERY_PATH = REPO / "jobs" / "skills" / "cdb_query.py"

# Questions that must never be affected by an attendance phrase, whatever the logs hold today. Add to this list when an incident teaches a new shape.
MUST_NOT_CHANGE = [
    "How many people are signed up for the men's billiard event", "Who is signed up for the billiards event?",
    "How many people are registered for the church picnic?", "how many are coming to the hayride", "who is coming to the banquet",
    "How many people are signed up for Men's Fraternity tomorrow night", "when is the servant leaders banquet", "who has rsvp'd for the picnic",
    "where can I register for men's breakfast", "how many tickets were sold for trunk or treat", "who is signed up to serve on Sunday",
    "how many volunteers are signed up for nursery", "what events are we tracking", "Is Connor Venuto signed up for the picnic?",
]

_STOP = set("a an the of for to in on at is are was were be been do does did how many much what who whom which when where why up down out "
            "and or but with from by as it its this that these those i we you they he she me my our us people person anyone someone".split())
SIGNUP_WORDS = {"signed", "signup", "signups", "sign", "register", "registered", "registration", "registrations", "registering", "rsvp", "rsvpd",
                "rsvps", "ticket", "tickets", "event", "events", "sold", "enrolled", "enroll"}
# words that appear in event titles but are ordinary in attendance talk: not evidence of an events question by themselves
_COMMON_EVENT_WORDS = {"catalyst", "names", "name", "men", "mens", "women", "womens", "church", "service", "services", "sunday", "kids", "kid", "group", "groups", "bible", "study",
                       "small", "youth", "ladies", "worship", "campus", "the", "and", "of"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def content_words(phrase: str) -> list[str]:
    return [w for w in _words(phrase) if w not in _STOP and len(w) >= 3]


def event_words(rows_titles: list[str] | None = None) -> set[str]:
    """Words that only make sense as part of an event name (tracked events + every church-calendar title)."""
    titles = rows_titles
    if titles is None:
        titles = []
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
            titles += [r[0] for r in conn.execute("SELECT event_name FROM church_events")]
            titles += [r[0] for r in conn.execute("SELECT DISTINCT title FROM church_calendar_events WHERE title IS NOT NULL")]
            conn.close()
        except Exception:
            pass
    out = set()
    for t in titles:
        out |= {w.replace("'s", "").strip("'") for w in _words(t)}
    return {w for w in out if len(w) >= 3} - _COMMON_EVENT_WORDS - _STOP


def phrase_problems(phrase: str, titles: list[str] | None = None) -> list[str]:
    """Pure checks on the phrase alone (1 and 2 above)."""
    out = []
    cw = content_words(phrase)
    # Specific enough = two content words ("attendance count"), or a natural phrase of 4+ words that still has a content word of its own
    # ("how many were in the building"). Bare frames ("signed up", "how many", "who is") fire on far too much.
    if len(cw) < 2 and not (len(_words(phrase)) >= 4 and len(cw) >= 1):
        out.append(f"too generic: '{phrase}' has {len(cw)} content word(s); a trigger needs to be specific so it cannot fire on unrelated questions")
    bad = [w for w in _words(phrase) if w in SIGNUP_WORDS]
    if bad:
        out.append(f"belongs to the events/signup domain (contains {', '.join(sorted(set(bad)))}), not attendance")
    ev = sorted({w.replace("'s", "").strip("'") for w in _words(phrase)} & event_words(titles))
    if ev:
        out.append(f"names an event ({', '.join(ev)}): event questions have their own fast path")
    return out


def _stem(w: str) -> str:
    w = w.replace("'s", "").strip("'")
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def phrase_in_question(phrase: str, question: str) -> bool:
    """A suggested trigger must be lifted from the question that prompted it. Placeholders like '[name]' (member-lookup phrasings) match any word."""
    norm = lambda t: " ".join(re.findall(r"[a-z0-9']+", t.lower().replace("’", "'")))
    q = norm(question)
    parts = [norm(p) for p in re.split(r"\[[^\]]*\]", phrase.lower()) if norm(p)]
    pos = 0
    for part in parts:
        i = q.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    return bool(parts)


def looks_like_event_question(question: str, titles: list[str] | None = None) -> bool:
    """True for signup/registration/RSVP/ticket wording, or a question containing a word that only appears in event names
    ("when is the hayride", "who is coming to the banquet"). Errs toward True: the cost of a false positive is one skipped suggestion."""
    q = question.lower().replace("’", "'")
    if re.search(r"\b(signed[- ]?up|sign[- ]?ups?|registered|registrations?|rsvp'?d?|rsvps|tickets?)\b", q):
        return True
    ev = {_stem(w) for w in event_words(titles)}
    return any(_stem(w) in ev for w in _words(q))


def corpus(extra: list[str] | None = None) -> list[str]:
    """Every distinct inbound question we have: Bill's own Telegram log, the leaders' team chat, the unanswered-questions table, plus the must-not-change list."""
    qs: list[str] = list(MUST_NOT_CHANGE) + list(extra or [])
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        for tbl in ("bill_telegram_log", "telegram_log"):
            try:
                qs += [r[0] for r in conn.execute(f"SELECT DISTINCT message FROM {tbl} WHERE direction='in' AND length(message) BETWEEN 8 AND 240")]
            except sqlite3.Error:
                pass
        try:
            qs += [r[0] for r in conn.execute("SELECT DISTINCT question FROM unanswered_questions WHERE length(question) BETWEEN 8 AND 240")]
        except sqlite3.Error:
            pass
        conn.close()
    except Exception:
        pass
    return sorted({q.strip() for q in qs if q and q.strip()})


def _load(source: str, name: str):
    mod = types.ModuleType(name)
    mod.__file__ = str(CDB_QUERY_PATH)
    mod.__package__ = "jobs.skills"
    exec(compile(source, str(CDB_QUERY_PATH), "exec"), mod.__dict__)
    return mod


def _run_pm(mod, q: str):
    weeks = [(date.today() - timedelta(weeks=i)).strftime("%Y-%m-%d") for i in range(1, 13)]
    try:
        return mod._pattern_match(q, mod._last_sunday(), weeks)
    except Exception as e:
        return f"!error {type(e).__name__}"


def claimed_elsewhere(question: str, titles: list[str] | None = None) -> bool:
    """True if a route other than the attendance matcher already answers this question (events fast path, church calendar, signup guard)."""
    try:
        from jobs.events.pattern_match import pattern_match as events_pm
        if events_pm(question):
            return True
    except Exception:
        pass
    try:
        from jobs.church_calendar.chat import answer as cal_answer
        if cal_answer(question):
            return True
    except Exception:
        pass
    return looks_like_event_question(question, titles)


def evaluate(target_id: str, new_phrase: str, example_question: str, *, corpus_questions: list[str] | None = None,
             original_source: str | None = None, titles: list[str] | None = None, max_other_changes: int = 3) -> dict:
    from jobs.analytics import fast_path_patcher as fp
    reasons: list[str] = []
    phrase = (new_phrase or "").strip().lower()
    result = {"ok": False, "reasons": reasons, "changed_others": [], "example_before": None, "example_after": None}

    reasons += phrase_problems(phrase, titles)
    if not phrase_in_question(phrase, example_question):
        reasons.append("the phrase does not appear in the question that prompted it (the model made it up): a trigger must come from a real question")
    if looks_like_event_question(example_question, titles):
        reasons.append("the question that prompted this is an event/signup question, not an attendance one (events have their own fast path)")

    ok, msg, new_text, old_text = fp.build_patched_text(target_id, phrase, source=original_source)
    if not ok:
        reasons.append(msg)
        return result
    try:
        before_mod, after_mod = _load(old_text, "cdb_before"), _load(new_text, "cdb_after")
    except Exception as e:
        reasons.append(f"patched file did not load: {type(e).__name__}: {e}")
        return result

    ex_before, ex_after = _run_pm(before_mod, example_question), _run_pm(after_mod, example_question)
    result["example_before"], result["example_after"] = ex_before, ex_after
    if ex_after is None:
        reasons.append("the phrase does not make the example question match: it fixes nothing")
    elif ex_before == ex_after:
        reasons.append("the example question was already answered the same way: nothing to fix")

    changed = []
    for q in corpus_questions if corpus_questions is not None else corpus():
        if q.strip().lower() == example_question.strip().lower():
            continue
        b, a = _run_pm(before_mod, q), _run_pm(after_mod, q)
        if b != a:
            changed.append({"question": q, "before": bool(b), "after": bool(a), "claimed_elsewhere": claimed_elsewhere(q, titles)})
    result["changed_others"] = changed
    hijacked = [c for c in changed if c["claimed_elsewhere"]]
    if hijacked:
        reasons.append(f"it would hijack {len(hijacked)} question(s) other routes already answer, e.g. {hijacked[0]['question']!r}")
    rerouted = [c for c in changed if c["before"] and c["after"]]
    if rerouted:
        reasons.append(f"it would change the answer to {len(rerouted)} question(s) the attendance matcher already answered, e.g. {rerouted[0]['question']!r}")
    if len(changed) > max_other_changes:
        reasons.append(f"it changes {len(changed)} other logged questions (limit {max_other_changes}): too broad to apply without a human looking")

    result["ok"] = not reasons
    return result


def format_reasons(result: dict) -> str:
    return "; ".join(result["reasons"]) if result["reasons"] else "ok"
