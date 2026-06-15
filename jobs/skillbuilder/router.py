"""jobs/skillbuilder/router.py — route messages to skills or fall through to chat."""
import importlib
import io
import json
import logging
import re
import threading
from contextlib import redirect_stdout
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
SKILLS_FILE = REPO / "memory" / "skills.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

log = logging.getLogger(__name__)

# Keyed by interface ("dashboard", "telegram"); stores description of last failed build
_last_failed_build: dict[str, str] = {}

_RETRY_PHRASES = frozenset({
    "retry", "try again", "build it again", "rebuild",
    "try that again", "build again", "retry building",
})

_WRAP_UP_TRIGGERS = (
    "we're done", "wrap this up", "save this session", "end session",
    "that's all for now", "save to memory", "summarize and save",
    "let's wrap up", "we are done", "wrap up this session",
    "wrap up", "save session",
)

_LIST_SKILLS_TRIGGERS = (
    "list your skills", "what can you do", "show skills",
)

# Pre-check map: slug → trigger phrases. Matched before the LLM call.
_SKILL_PRE_CHECKS: dict[str, tuple] = {
    "send_email": (
        "send an email", "send email", "email to",
        "draft an email", "write an email", "compose an email",
    ),
    "tells_many_days": (
        "how many days until christmas", "christmas countdown",
    ),
    "weather_every_morning": (
        "what's the weather", "weather today", "check the weather", "forecast",
    ),
    "log_watch": (
        "check logs", "any errors", "error summary",
    ),
    "skill_audit": (
        "audit skills", "test my skills", "run skill audit", "which skills work", "skill audit",
    ),
    "bible_lookup": (
        "bible", "scripture", "look up verse",
    ),
    "calendar_availability": (
        "my availability", "check calendar", "open slots", "when am i free",
    ),
    "web_search": (
        "search for", "search the web", "look it up", "google that",
    ),
    "read_pdf": (
        "read pdf", "open pdf",
    ),
    "read_word": (
        "read word", "open doc", "open word",
    ),
    "read_excel": (
        "read excel", "open spreadsheet", "open excel",
    ),
    "read_powerpoint": (
        "read powerpoint", "open presentation", "open pptx",
    ),
    "article_reader": (
        "read this article", "fetch this url", "read this page", "summarize this link",
    ),
    "vcf_importer": (
        "import vcf", "import vcard", "load contact card",
    ),
    "qr_generator": (
        "generate qr", "qr code", "qr-code", "create qr", "make a qr",
        "give me a qr", "generate a qr", "create a qr", "make qr", "qr for",
    ),
    "youtube_downloader": (
        "download youtube", "download audio from", "download this video", "get audio from youtube",
    ),
    "text_processor": (
        "summarize this text", "extract keywords", "convert to markdown", "key points from",
    ),
    "system_monitor": (
        "system health", "how is watson doing", "check system", "cpu usage", "memory usage", "disk usage",
    ),
    "git_tools": (
        "show recent commits", "git history", "what changed recently",
    ),
    "test_runner": (
        "run tests", "test watson",
    ),
    "code_analyzer": (
        "analyze codebase", "find missing run", "codebase report",
    ),
    "screenshot": (
        "screenshot", "take a screenshot", "show me this page", "capture this url",
    ),
    "svg_generator": (
        "create quote card", "make a banner", "generate graphic", "quote card",
    ),
    "social_poster": (
        "post to facebook", "share on facebook", "facebook post",
    ),
    "content_calendar": (
        "content calendar", "upcoming posts", "what is scheduled",
    ),
    "site_deployer": (
        "deploy wcky", "deploy watson", "push to vercel",
    ),
    "seo_tools": (
        "seo analysis", "check seo", "analyze this page for seo",
    ),
    "acquire_skill": (
        "find a skill", "acquire a skill", "i need you to be able to",
        "find a library", "learn to", "can you learn",
    ),
    "news_search": (
        "search news", "latest news", "news about", "current events about",
    ),
    "grammar_checker": (
        "check grammar", "fix grammar", "grammar check", "proofread this",
    ),
    "spell_checker": (
        "check spelling", "spell check", "find misspellings", "spelling errors",
    ),
    "semantic_search": (
        "search my memory", "what do i know about", "find related content",
    ),
    "academic_search": (
        "search arxiv", "research papers", "find academic papers", "scholarly search",
    ),
    "skill_tester": (
        "test skill", "test this skill", "run skill test", "does this skill work",
    ),
    "auto_fixer": (
        "auto fix", "fix this skill", "repair this skill",
    ),
    "skill_validator": (
        "validate skill", "validate all", "ready to promote",
    ),
    "performance_profiler": (
        "profile skill", "performance report",
    ),
    "dependency_scanner": (
        "scan dependencies", "missing packages", "check imports",
    ),
    "error_analyzer": (
        "analyze this error", "debug this error", "why is this failing",
    ),
    "style_checker": (
        "check style", "style check", "proselint", "writing style", "check my writing",
    ),
    "document_converter": (
        "convert document", "convert file", "convert to", "doc to", "markdown to html", "docx to",
    ),
    "citation_manager": (
        "add citation", "citation", "cite doi", "cite isbn", "list citations", "bibliography",
    ),
    "manuscript_tracker": (
        "track manuscript", "manuscript", "draft progress", "update manuscript",
    ),
    "epub_generator": (
        "generate epub", "create epub", "make epub", "epub from", "ebook from",
    ),
    "summarizer": (
        "summarize", "tldr", "summary of", "key points", "main topics",
    ),
    "isbn_lookup": (
        "isbn lookup", "lookup book", "book info", "find book", "isbn search",
    ),
    "wordcloud_generator": (
        "word cloud", "wordcloud", "word frequency cloud", "visualize words",
    ),
    "command_executor": (
        "restart watson", "restart the dashboard", "restart dashboard",
        "restart the bot", "restart bot", "restart all",
        "git pull", "pull latest", "pull latest code",
        "check services", "are services running", "service status",
        "check disk space", "disk usage",
        "update packages", "upgrade packages",
        "run command", "execute command", "run sync",
    ),
    "pastoral_notes": (
        "pastoral note", "pastoral notes", "make a note that", "note that",
    ),
    "send_contact_info": (
        "send contact info", "contact info to", "send their contact",
    ),
    "add_task": (
        "add a task", "add task", "new task", "create a task",
        "remind me to", "put on my task list", "add to my tasks",
    ),
    "kb_search": (
        "search kb", "search my notes", "search my sermons",
        "what have i said about", "what did i preach on",
        "find in my notes", "look in my sermons", "kb search",
    ),
    "contacts_lookup": (
        "find ", "look up", "lookup", "who is", "pull up", "contact for",
    ),
    "book_appointment": (
        "book an appointment", "set an appointment", "schedule a meeting",
        "add to my calendar", "create an appointment", "book a meeting",
        "schedule an appointment",
    ),
}

# Matched against msg_lower BEFORE the LLM call — returns run_audit immediately, no fallthrough
_AUDIT_TRIGGERS = (
    "run audit", "audit", "capability audit", "what am i missing", "gap analysis",
)

# Matched against msg_lower BEFORE the LLM call — guaranteed BUILD, no fallthrough
_BUILD_TRIGGERS = (
    "build a skill", "build me a skill", "build me something that",
    "build something that", "create a job", "create a skill",
    "write a job", "write a skill", "add the ability to",
    "i need you to be able to", "can you build", "build me a",
)

_CATEGORY_KEYWORDS = {
    "monitor": "monitoring", "log": "monitoring", "disk": "monitoring",
    "cpu": "monitoring", "memory": "monitoring", "health": "monitoring",
    "email": "email", "gmail": "email", "mail": "email",
    "calendar": "gcal", "schedule": "gcal", "event": "gcal", "booking": "gcal",
    "bible": "bible", "verse": "bible", "scripture": "bible",
    "weather": "weather", "forecast": "weather",
    "social": "social", "twitter": "social", "facebook": "social",
    "remind": "reminders", "reminder": "reminders",
    "task": "tasks", "todo": "tasks",
    "people": "people", "contact": "people",
    "news": "briefing", "briefing": "briefing",
    "sermon": "sermon", "transcript": "sermon",
    "report": "reporting", "summary": "reporting",
}

_BUILD_FRAME_PATTERNS = [
    r'^watson[,\s]+',
    r'^build\s+me\s+a\s+skill\s+that\s+',
    r'^build\s+a\s+skill\s+that\s+',
    r'^build\s+a\s+skill\s+to\s+',
    r'^create\s+a\s+job\s+that\s+',
    r'^create\s+a\s+skill\s+that\s+',
    r'^write\s+a\s+job\s+that\s+',
    r'^add\s+the\s+ability\s+to\s+',
    r'^i\s+need\s+you\s+to\s+be\s+able\s+to\s+',
    r'^build\s+something\s+that\s+',
    r'^build\s+me\s+something\s+that\s+',
    r'^can\s+you\s+build\s+',
    r'^build\s+me\s+a\s+',
    r'^build\s+a\s+',
    r'^create\s+a\s+',
    r'^write\s+a\s+',
]

_CONVERSATIONAL_STARTERS = (
    "hi", "hey", "hello", "good", "thanks", "thank", "ok", "okay",
    "sounds", "got it", "what", "how", "why", "who", "when", "where",
    "is", "are", "can", "do", "does", "tell me", "explain",
)

_ACTION_KEYWORDS = frozenset({
    "build", "create", "add", "send", "email", "search", "find",
    "look up", "schedule", "set", "list", "show", "remind",
    "draft", "write", "publish",
    "compare", "comparing", "vs", "versus", "difference", "better", "recommend",
    "who", "what year", "when did", "how much", "price", "cost",
    "current", "latest", "recent", "news",
})

_FACTUAL_KEYWORDS = frozenset({
    "compare", "comparing", "vs", "versus", "difference",
    "who", "current", "latest", "recent", "news", "price", "cost",
})

_FACTUAL_PHRASES = (
    "which is", "what year", "when did", "how much",
)

_GREETING_ONLY = frozenset({
    "hi", "hey", "hello", "good", "thanks", "thank", "ok", "okay", "sounds", "got it",
})

_WATSON_PREFIX_RE = re.compile(r'^watson[,\s]+', re.IGNORECASE)


def _is_conversational(message: str) -> bool:
    """Return True if the message is conversational and should skip the LLM router."""
    msg_lower = message.lower().strip()
    words = msg_lower.split()
    if len(words) < 8 and not any(kw in msg_lower for kw in _ACTION_KEYWORDS):
        return True
    for starter in _CONVERSATIONAL_STARTERS:
        if msg_lower == starter or msg_lower.startswith(starter + " ") or msg_lower.startswith(starter + ","):
            return True
    return False


_IDENTITY_PHRASES = (
    "who are you", "what are you", "who is watson", "what is watson",
    "introduce yourself", "tell me about yourself", "how many skills",
    "what can you do", "what do you do",
)


def _is_identity_query(message: str) -> bool:
    msg_lower = message.lower().strip()
    return any(phrase in msg_lower for phrase in _IDENTITY_PHRASES)


def _is_factual_query(message: str) -> bool:
    """Return True if message is a factual/lookup query that should route to web search."""
    msg_lower = message.lower().strip()
    first_word = msg_lower.split()[0] if msg_lower.split() else ""
    if first_word in _GREETING_ONLY:
        return False
    return (
        any(kw in msg_lower for kw in _FACTUAL_KEYWORDS)
        or any(phrase in msg_lower for phrase in _FACTUAL_PHRASES)
    )


def _extract_build_description(message: str) -> str:
    """Strip build-request framing, return the functional description."""
    msg = message.strip()
    for pattern in _BUILD_FRAME_PATTERNS:
        match = re.match(pattern, msg, re.IGNORECASE)
        if match:
            msg = msg[match.end():]
            break
    return msg.strip() or message.strip()


_SLUG_FILLER = frozenset({
    "build", "skill", "that", "checks", "the", "a", "an", "and", "or",
    "for", "to", "with", "i", "me", "my", "job", "create", "add",
    "ability", "write", "something", "watson", "can", "you", "make",
    "new", "some", "it", "its", "is", "are", "be", "been", "has",
    "have", "will", "would", "should", "could", "get", "gets", "give",
    "gives", "send", "sends", "this", "then", "when", "how", "what",
    "which", "also", "just", "let", "use", "using",
})

_CATEGORY_MAP = {
    "weather": "monitoring", "forecast": "monitoring",
    "monitor": "monitoring", "log": "monitoring", "disk": "monitoring",
    "cpu": "monitoring", "memory": "monitoring", "health": "monitoring",
    "email": "email", "gmail": "email", "mail": "email",
    "calendar": "gcal", "schedule": "gcal", "event": "gcal", "booking": "gcal",
    "bible": "bible", "verse": "bible", "scripture": "bible",
    "social": "social", "twitter": "social", "facebook": "social",
    "remind": "misc", "reminder": "misc",
    "task": "misc", "todo": "misc",
    "people": "people", "contact": "people",
    "news": "misc", "briefing": "misc",
    "sermon": "sermon", "transcript": "sermon",
    "report": "misc", "summary": "misc",
}


def _generate_job_path(description: str) -> str:
    """Derive jobs/<category>/<slug>.py from a plain-English description.

    Strips filler words, picks the first 3 meaningful keywords for the slug,
    and maps the first recognised domain keyword to a category folder.
    Example: "checks the weather and sends it to me" → jobs/monitoring/weather_sends.py
    """
    words = re.sub(r'[^a-z0-9\s]', '', description.lower()).split()
    meaningful = [w for w in words if w not in _SLUG_FILLER and len(w) > 1]

    category = "misc"
    for word in meaningful:
        if word in _CATEGORY_MAP:
            category = _CATEGORY_MAP[word]
            break

    slug_words = meaningful[:3]
    slug = "_".join(slug_words) or "skill"
    return f"jobs/{category}/{slug}.py"


def _load_skills(interface: str) -> list:
    if not SKILLS_FILE.exists():
        return []
    try:
        data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        raw = data["skills"] if isinstance(data, dict) and "skills" in data else data
    except Exception:
        return []
    result = []
    for s in raw:
        if interface not in s.get("interfaces", []) or s.get("status", "ready") != "ready":
            continue
        # Normalize field names so the rest of the router can use slug/job_module uniformly.
        if "slug" not in s:
            s = dict(s, slug=s.get("name", ""))
        if "job_module" not in s and "module" in s:
            s = dict(s, job_module=s["module"])
        result.append(s)
    return result


def _ask_router(message: str, skills: list) -> str:
    skills_json = json.dumps(skills, indent=2)
    prompt = (
        "SYSTEM: You are Watson's skill router. Given a user message and a list of "
        "available skills, determine the best action. Reply with exactly one of: "
        "SKILL:<slug>, LIST_SKILLS, BUILD, PROPOSE, WRAP_UP, or CHAT. Nothing else.\n\n"
        "SKILL:<slug> — the message clearly maps to a known skill by intent and meaning. "
        "Match on what the user wants to accomplish, not exact wording. "
        "The triggers array is a hint only.\n"
        "LIST_SKILLS — the user wants to know what Watson can do or see his capabilities. "
        "This includes any natural phrasing like 'what can you do', 'show me your skills', "
        "'what do you know how to do', 'what are you capable of'.\n"
        "BUILD — the user is explicitly asking Watson to build, create, or add a new skill "
        "or job. Phrases like 'build a skill', 'create a job', 'add the ability to', "
        "'I need you to be able to', 'write a job that', 'build me something that'.\n"
        "PROPOSE — the message describes a task Watson should be able to do but currently "
        "cannot — but is NOT explicitly asking Watson to build it right now.\n"
        "WRAP_UP — Bill is explicitly closing a working session and wants Watson to summarize "
        "and save it to memory. Phrases: 'we're done', 'wrap this up', 'save this session', "
        "'end session', 'that's all for now', 'save to memory', 'summarize and save', "
        "'let's wrap up'.\n"
        "CHAT — general conversation, question, or something Watson should respond to normally.\n\n"
        f"Available skills:\n{skills_json}\n\n"
        f"User message: {message}"
    )
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _run_skill(skill: dict, message: str = None) -> str:
    import inspect
    mod = importlib.import_module(skill["job_module"])
    fn = getattr(mod, skill["function"])
    buf = io.StringIO()
    result = None
    with redirect_stdout(buf):
        sig = inspect.signature(fn)
        if message is not None and "message" in sig.parameters:
            result = fn(message=message)
        else:
            result = fn()
    if isinstance(result, dict):
        return result
    output = buf.getvalue().strip()
    if result is not None:
        return str(result)
    return output or "(no output)"


def _list_skills_result(interface: str) -> str:
    skills = _load_skills(interface)
    if not skills:
        return "I don't have any skills registered yet."
    lines = "\n".join(
        f"• {s['slug'].replace('_', ' ').title()}: {s['description']}"
        for s in skills
    )
    return "Here are my current skills:\n\n" + lines


def _build_in_background(description: str, job_path: str, interface: str) -> None:
    """Background thread target: run build_skill and track result in _last_failed_build."""
    from jobs.skillbuilder.build import build_skill
    success = build_skill(description, job_path)
    if success:
        _last_failed_build.pop(interface, None)
    else:
        _last_failed_build[interface] = description


def route(message: str, interface: str) -> dict:
    """Route a message to a skill, trigger a build, propose a new skill, or fall through.

    Returns one of:
      {"action": "skill", "slug": str, "result": str}
      {"action": "build", "description": str, "job_path": str}
      {"action": "propose", "message": str}
      {"action": "chat"}
    """
    try:
        return _route(message, interface)
    except Exception as exc:
        log.error("Router failed: %s", exc)
        return {"action": "chat"}


def _route(message: str, interface: str) -> dict:
    # Direct slug dispatch — from dashboard Use button (run:<slug>)
    if message.lower().startswith("run:"):
        run_body = message[4:].strip()
        slug = run_body.split()[0] if run_body else ""
        skills = _load_skills(interface)
        skill = next((s for s in skills if s["slug"] == slug), None)
        if skill:
            try:
                skill_message = run_body
                result = _run_skill(skill, message=skill_message)
            except Exception as exc:
                result = f"Skill error: {exc}"
            return {"action": "skill", "slug": slug, "result": result}

    # Retry check: if Bill is retrying after a failed build, skip the LLM
    msg_lower = message.lower().strip()
    if any(phrase in msg_lower for phrase in _RETRY_PHRASES) and interface in _last_failed_build:
        description = _last_failed_build[interface]
        job_path = _generate_job_path(description)
        return {"action": "build", "description": description, "job_path": job_path}

    # Keyword pre-check: audit phrases fire run_audit immediately, no LLM call.
    if any(trigger in msg_lower for trigger in _AUDIT_TRIGGERS):
        return {"action": "skill", "slug": "skill_audit"}

    # Keyword pre-checks: common skills — no LLM call for known patterns.
    for slug, triggers in _SKILL_PRE_CHECKS.items():
        if any(trigger in msg_lower for trigger in triggers):
            return {"action": "skill", "slug": slug, "message": message}

    # Keyword pre-check: wrap-up phrases.
    if any(trigger in msg_lower for trigger in _WRAP_UP_TRIGGERS):
        return {"action": "wrap_up"}

    # Keyword pre-check: list skills.
    if any(trigger in msg_lower for trigger in _LIST_SKILLS_TRIGGERS):
        return {"action": "skill", "slug": "list_skills", "result": _list_skills_result(interface)}

    # Keyword pre-check: guaranteed BUILD before the LLM sees the message.
    # Prevents build requests from ever falling through to Ollama.
    if any(trigger in msg_lower for trigger in _BUILD_TRIGGERS):
        description = _extract_build_description(message)
        job_path = _generate_job_path(description)
        return {"action": "build", "description": description, "job_path": job_path}

    # Identity pre-check: route to conversational before factual or LLM.
    if _is_identity_query(message):
        return {"action": "conversational"}

    # Factual query pre-check: route to web_search before LLM or conversational bypass.
    if _is_factual_query(message):
        from jobs.research.web_search import run as web_search_run
        result = web_search_run(message)
        return {"action": "skill", "slug": "web_search", "result": result}

    # Skill trigger pre-check: if any ready skill's trigger is an exact substring of
    # the message, execute it immediately — no LLM call needed. Runs before the
    # conversational bypass so short trigger phrases like "tell me a joke" aren't lost.
    skills = _load_skills(interface)
    for skill in skills:
        for trigger in skill.get("triggers", []):
            if trigger.lower() in msg_lower:
                slug = skill["slug"]
                try:
                    result = _run_skill(skill, message=message)
                except Exception as exc:
                    result = f"Skill failed: {exc}"
                return {"action": "skill", "slug": slug, "result": result}

    # Watson-addressed question messages that matched no skill trigger route to chat,
    # not the LLM router. Prevents misrouting of "Watson, what's/how/why/..." questions
    # by an unreliable small model when no skill is actually relevant.
    if _WATSON_PREFIX_RE.match(msg_lower):
        _unwatson = _WATSON_PREFIX_RE.sub('', msg_lower).strip()
        if re.match(r'^(what|how|why|where|when|who)\b', _unwatson):
            return {"action": "chat"}

    # Conversational pre-check: skip LLM router entirely for short/greeting messages.
    if _is_conversational(message):
        return {"action": "chat"}

    if not skills:
        return {"action": "chat"}

    try:
        decision = _ask_router(message, skills)
    except Exception as exc:
        log.warning("Skill router LLM call failed: %s", exc)
        return {"action": "chat"}

    # If the LLM mistakenly returned CHAT for a build-phrased message, force BUILD.
    if decision == "CHAT" and any(
        kw in msg_lower for kw in ("build", "create a job", "write a job", "add ability")
    ):
        description = _extract_build_description(message)
        job_path = _generate_job_path(description)
        return {"action": "build", "description": description, "job_path": job_path}

    if decision.startswith("SKILL:"):
        slug = decision[len("SKILL:"):].strip()
        skill = next((s for s in skills if s["slug"] == slug), None)
        if not skill:
            return {"action": "chat"}
        try:
            result = _run_skill(skill)
        except Exception as exc:
            result = f"Skill failed to execute: {exc}"
        return {"action": "skill", "slug": slug, "result": result}

    if decision == "LIST_SKILLS":
        return {
            "action": "skill",
            "slug": "list_skills",
            "result": _list_skills_result(interface),
        }

    if decision == "BUILD":
        description = _extract_build_description(message)
        job_path = _generate_job_path(description)
        return {"action": "build", "description": description, "job_path": job_path}

    if decision == "PROPOSE":
        return {
            "action": "propose",
            "message": "I don't have a skill for that yet. Want me to build one?",
        }

    if decision == "WRAP_UP":
        return {"action": "wrap_up"}

    return {"action": "chat"}
