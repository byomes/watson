#!/usr/bin/env python3
"""
smoke_test_triggers.py

Smoke-tests Watson bot keyword triggers and intent routing without
sending Telegram messages or calling Ollama.

Covers two routing layers:
  BOT    — inline pre-checks in bot/bot.py handle_text()
  ROUTER — pattern pre-checks in jobs/skillbuilder/router.py _route()

State-dependent paths (pending intents, riddle state, etc.) and
Ollama-required paths are listed separately and excluded from PASS/FAIL.
"""

import json
import re
import sys
from pathlib import Path

WATSON_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WATSON_ROOT))

# Import router constants — no side effects, all top-level
from jobs.skillbuilder.router import (
    _SKILL_PRE_CHECKS,
    _AUDIT_TRIGGERS,
    _BUILD_TRIGGERS,
    _WRAP_UP_TRIGGERS,
    _LIST_SKILLS_TRIGGERS,
    _RETRY_PHRASES,
    _is_conversational,
    _is_identity_query,
    _is_factual_query,
    _WATSON_PREFIX_RE,
)

# Bot-level constants (from bot/bot.py)
_SKILL_AFFIRM = {"yes", "yes please", "go ahead", "build it", "sure", "do it", "yep", "yeah"}
_SKILL_DENY   = {"no", "never mind", "nope", "cancel", "don't", "no thanks"}
_RIDDLE_ANS   = (
    "what's the answer", "whats the answer", "what is the answer",
    "reveal the answer", "tell me the answer", "give me the answer",
)
_QR_TRIGGERS  = (
    "qr code", "qr-code", "make a qr", "give me a qr",
    "generate a qr", "create a qr", "make qr", "qr for",
)
_SMS_TRIGGERS = (
    "text ", "send a text", "send text", "shoot a text",
    "shoot them a text", "shoot her a text", "shoot him a text",
)


def decide_route(message: str) -> dict:
    """
    Dry-run of bot.py handle_text() + router.py _route().

    Mirrors the routing decision tree without executing skills,
    running web searches, or calling Ollama.

    Returns:
        dict with keys: layer, action, slug (optional), note (optional)
    """
    def R(action, slug=None, note=None, layer="bot"):
        return {"layer": layer, "action": action, "slug": slug, "note": note}

    # Normalize smart quotes (mirrors bot.py line 521)
    tc = message.replace("‘", "'").replace("’", "'")
    tl = tc.lower().strip()

    # ── Watson prefix strip (bot.py lines 551-555) ────────────────────
    for pfx in ("watson,", "watson"):
        if tl.startswith(pfx):
            tl = tl[len(pfx):].strip()
            tc = tc[len(pfx):].strip()
            break

    # ── BOT INLINE PRE-CHECKS ─────────────────────────────────────────

    # kb: query (bot.py line 558)
    if tl.startswith("kb:"):
        return R("kb_query")

    # Build pipeline — dev build, NOT the skill builder (bot.py line 572)
    if tl.startswith("build ") and not tl.startswith("build:"):
        return R("build_pipeline")

    # Facebook share (bot.py line 585)
    if tc.startswith("\U0001f4d8 TO FACEBOOK"):
        return R("facebook_share")

    # Writing Room commands (bot.py line 591)
    if tl.startswith("room "):
        return R("room_command")

    # Email reply: "go" sends draft (bot.py line 612)
    if tl == "go":
        return R("email_reply_send", note="state-dependent")

    # Email reply: "change: <text>" edits draft (bot.py line 619)
    if tl.startswith("change:"):
        return R("email_reply_change")

    # Build pipeline approval (bot.py line 629)
    if tl == "approve":
        return R("build_approval", note="state-dependent")

    # Pending confirmation: yes/confirm/etc. (bot.py line 638)
    if tl in ("yes", "confirm", "yes do it", "book it", "go ahead") or tl in _SKILL_AFFIRM:
        return R("pending_confirm", note="state-dependent")

    # Pending cancel (bot.py line 680)
    if tl in ("no", "cancel", "don't book", "never mind") or tl in _SKILL_DENY:
        return R("cancel", note="state-dependent")

    # Report menu (bot.py line 708)
    if tl in ("reports", "report menu"):
        return R("report_menu")

    # Run specific report (bot.py line 716)
    m = re.match(r"^report\s+(.+)", tl)
    if m:
        return R("run_report", slug=m.group(1))

    # Riddle answer reveal — only fires when a riddle is pending (bot.py line 736)
    if any(t in tl for t in _RIDDLE_ANS):
        return R("riddle_answer", note="state-dependent")

    # QR code generation (bot.py line 748)
    if any(t in tl for t in _QR_TRIGGERS):
        return R("qr_generate")

    # QR email follow-up — requires last_qr in session (bot.py line 780)
    if re.search(r"(?:email|send)\s+this\s+(?:qr\s+)?to\s+.+", tl):
        return R("qr_email", note="state-dependent (requires last QR in session)")

    # SMS interception (bot.py line 807)
    if any(t in tl for t in _SMS_TRIGGERS):
        return R("sms_send")

    # Time check (bot.py line 854)
    if re.search(r"what.*(time|hour).*is it|what time|current time", tl):
        return R("time_check")

    # Remind me intake (bot.py line 863)
    _timed = re.match(r"^remind me at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+.+", tl)
    _plain = None if _timed else re.match(r"^remind me\s+.+", tl)
    if _timed or _plain:
        return R("remind_me")

    # ── Bot re-checks _SKILL_PRE_CHECKS (bot.py lines 890-914) ────────
    # Uses watson-stripped text (same as tl at this point)
    for slug, triggers in _SKILL_PRE_CHECKS.items():
        if any(t in tl for t in triggers):
            return R("skill", slug=slug)

    # ── Bot factual-query check before router call (bot.py line 916) ──
    if _is_factual_query(tc):
        return R("skill", slug="web_search", note="factual pre-check")

    # ── ROUTER PRE-CHECKS (mirrors router.py _route() without executing) ──

    # run:<slug> — direct dispatch from dashboard Use button
    if tl.startswith("run:"):
        return R("skill", slug="<run-prefix>", layer="router")

    # Retry a failed build (state-dependent)
    if any(p in tl for p in _RETRY_PHRASES):
        return R("build", note="retry (state-dependent)", layer="router")

    # Audit triggers → skill_audit immediately, no LLM
    if any(t in tl for t in _AUDIT_TRIGGERS):
        return R("skill", slug="skill_audit", layer="router")

    # _SKILL_PRE_CHECKS in router (same dict — already caught in bot layer,
    # included here for completeness when testing the router directly)
    for slug, triggers in _SKILL_PRE_CHECKS.items():
        if any(t in tl for t in triggers):
            return R("skill", slug=slug, layer="router")

    # Wrap-up triggers
    if any(t in tl for t in _WRAP_UP_TRIGGERS):
        return R("wrap_up", layer="router")

    # List skills
    if any(t in tl for t in _LIST_SKILLS_TRIGGERS):
        return R("skill", slug="list_skills", layer="router")

    # Build triggers (guaranteed BUILD, no LLM fallthrough)
    if any(t in tl for t in _BUILD_TRIGGERS):
        return R("build", layer="router")

    # Identity query → conversational (then falls to Ollama in bot)
    if _is_identity_query(tc):
        return R("conversational", note="falls to Ollama", layer="router")

    # Factual query → web_search (would execute in real router; decision noted)
    if _is_factual_query(tc):
        return R("skill", slug="web_search", layer="router")

    # Skills.json trigger check — without executing the skill
    try:
        skills_file = WATSON_ROOT / "memory" / "skills.json"
        if skills_file.exists():
            data = json.loads(skills_file.read_text())
            skills = data["skills"] if isinstance(data, dict) and "skills" in data else data
            for s in skills:
                if s.get("status", "ready") != "ready":
                    continue
                if "telegram" not in s.get("interfaces", []):
                    continue
                for trig in s.get("triggers", []):
                    if trig.lower() in tl:
                        return R("skill", slug=s.get("slug", s.get("name", "?")),
                                 note="skills.json trigger", layer="router")
    except Exception:
        pass

    # Watson-addressed question → chat (no LLM routing)
    _unwatson = _WATSON_PREFIX_RE.sub("", tl).strip()
    if _WATSON_PREFIX_RE.match(tl) and re.match(r"^(what|how|why|where|when|who)\b", _unwatson):
        return R("chat", layer="router")

    # Conversational bypass
    if _is_conversational(tc):
        return R("chat", layer="router")

    # Everything else goes to Ollama intent classifier
    return R("ollama_required", note="requires Ollama LLM classification", layer="router")


# ── Test cases ────────────────────────────────────────────────────────────────
# Format: (message, expected_layer, expected_action, expected_slug, description)
# expected_slug=None means "don't check slug"

TEST_CASES = [
    # ── BOT: kb: query ────────────────────────────────────────────────
    ("kb: what is grace",                       "bot", "kb_query",         None,               "kb: prefix"),
    ("kb:sermon notes on John 3",               "bot", "kb_query",         None,               "kb: no space"),

    # ── BOT: build pipeline (dev build, not skill builder) ────────────
    ("build a weather dashboard",               "bot", "build_pipeline",   None,               "build pipeline"),
    ("build me a daily habit tracker",          "bot", "build_pipeline",   None,               "build me a …"),

    # ── BOT: facebook share ───────────────────────────────────────────
    ("\U0001f4d8 TO FACEBOOK",                  "bot", "facebook_share",   None,               "facebook share trigger"),

    # ── BOT: room command ────────────────────────────────────────────
    ("room partners",                           "bot", "room_command",     None,               "room partners"),
    ("room pending",                            "bot", "room_command",     None,               "room pending"),
    ("room revoke bill@example.com",            "bot", "room_command",     None,               "room revoke"),

    # ── BOT: email reply ─────────────────────────────────────────────
    ("change: please use a friendlier tone",    "bot", "email_reply_change", None,             "change: email reply"),

    # ── BOT: report menu ─────────────────────────────────────────────
    ("reports",                                 "bot", "report_menu",      None,               "reports keyword"),
    ("report menu",                             "bot", "report_menu",      None,               "report menu"),
    ("report pastoral quarterly",               "bot", "run_report",       "pastoral quarterly", "run named report"),
    ("report connect cards summary",            "bot", "run_report",       "connect cards summary", "run report with spaces"),

    # ── BOT: QR code ─────────────────────────────────────────────────
    ("qr code for https://example.com",         "bot", "qr_generate",      None,               "qr code trigger"),
    ("qr-code for my church site",              "bot", "qr_generate",      None,               "qr-code hyphen"),
    ("make a qr for my email list",             "bot", "qr_generate",      None,               "make a qr"),
    ("give me a qr for this url",               "bot", "qr_generate",      None,               "give me a qr"),
    ("generate a qr for the sign-up form",      "bot", "qr_generate",      None,               "generate a qr"),
    ("create a qr for the event",               "bot", "qr_generate",      None,               "create a qr"),
    ("make qr for the landing page",            "bot", "qr_generate",      None,               "make qr"),
    ("qr for https://catalyst302.com",          "bot", "qr_generate",      None,               "qr for url"),

    # ── BOT: SMS ─────────────────────────────────────────────────────
    ("text John saying call me back",           "bot", "sms_send",         None,               "text <name>"),
    ("send a text to Mary",                     "bot", "sms_send",         None,               "send a text to"),
    ("send text to Bill",                       "bot", "sms_send",         None,               "send text to"),
    ("shoot a text to the team",                "bot", "sms_send",         None,               "shoot a text"),
    ("shoot them a text saying we're starting", "bot", "sms_send",         None,               "shoot them a text"),
    ("shoot her a text",                        "bot", "sms_send",         None,               "shoot her a text"),
    ("shoot him a text saying thanks",          "bot", "sms_send",         None,               "shoot him a text"),

    # ── BOT: time check ──────────────────────────────────────────────
    ("what time is it",                         "bot", "time_check",       None,               "what time is it"),
    ("what is the current time",                "bot", "time_check",       None,               "current time"),
    ("what hour is it",                         "bot", "time_check",       None,               "what hour is it"),

    # ── BOT: remind me ───────────────────────────────────────────────
    ("remind me to call John",                  "bot", "remind_me",        None,               "remind me plain"),
    ("remind me to prepare for Sunday",         "bot", "remind_me",        None,               "remind me plain 2"),
    ("remind me at 3pm to take medication",     "bot", "remind_me",        None,               "remind me at time"),
    ("remind me at 9:30am to read email",       "bot", "remind_me",        None,               "remind me at HH:MM"),

    # ── BOT: _SKILL_PRE_CHECKS re-check ──────────────────────────────

    # add_task
    ("add task review sermon notes",            "bot", "skill", "add_task",         "add task trigger"),
    ("new task prepare Sunday message",         "bot", "skill", "add_task",         "new task trigger"),
    ("create task write thank you notes",       "bot", "skill", "add_task",         "create task trigger"),

    # bible_lookup
    ("bible verse John 3:16",                   "bot", "skill", "bible_lookup",     "bible verse trigger"),
    ("look up verse Psalm 23:1",                "bot", "skill", "bible_lookup",     "look up verse trigger"),

    # command_executor
    ("run command ls -la",                      "bot", "skill", "command_executor", "run command trigger"),
    ("shell command pwd",                       "bot", "skill", "command_executor", "shell command trigger"),
    ("bash df -h",                              "bot", "skill", "command_executor", "bash trigger"),

    # claude_debug
    ("run diagnostics",                         "bot", "skill", "claude_debug",     "run diagnostics trigger"),
    ("debug: check the error logs",             "bot", "skill", "claude_debug",     "debug: prefix trigger"),

    # contacts_lookup
    ("show all contacts",                       "bot", "skill", "contacts_lookup",  "show all contacts trigger"),
    ("list contacts",                           "bot", "skill", "contacts_lookup",  "list contacts trigger"),
    ("contact search Johnson",                  "bot", "skill", "contacts_lookup",  "contact search trigger"),

    # pastoral_search
    ("pastoral search Smith family",            "bot", "skill", "pastoral_search",  "pastoral search trigger"),

    # book_appointment
    ("book an appointment with John on Friday", "bot", "skill", "book_appointment", "book an appointment"),
    ("schedule an appointment with Dr. Smith",  "bot", "skill", "book_appointment", "schedule an appointment"),
    ("book a meeting with the elders",          "bot", "skill", "book_appointment", "book a meeting"),
    ("schedule a meeting for next week",        "bot", "skill", "book_appointment", "schedule a meeting"),

    # kb_export
    ("kb export: theology notes",               "bot", "skill", "kb_export",        "kb export trigger"),
    ("kb export: all sermon summaries",         "bot", "skill", "kb_export",        "kb export long"),

    # web_search
    ("search the web for apologetics resources","bot", "skill", "web_search",       "search the web trigger"),
    ("web search for sermon illustrations",     "bot", "skill", "web_search",       "web search trigger"),

    # image_search
    ("find image of a sunset",                  "bot", "skill", "image_search",     "find image trigger"),
    ("find a photo of mountains",               "bot", "skill", "image_search",     "find a photo trigger"),
    ("find a picture of the cross",             "bot", "skill", "image_search",     "find a picture trigger"),
    ("search image of a dove",                  "bot", "skill", "image_search",     "search image trigger"),
    ("image of a lion",                         "bot", "skill", "image_search",     "image of trigger"),

    # wrap_up removed from _SKILL_PRE_CHECKS — now correctly caught by _WRAP_UP_TRIGGERS in router
    ("wrap up",                                 "router", "wrap_up", None,          "wrap up → router wrap_up action"),
    ("end session",                             "router", "wrap_up", None,          "end session → router wrap_up action"),

    # ── ROUTER: audit triggers ────────────────────────────────────────
    ("run audit",                               "router", "skill", "skill_audit",   "run audit trigger"),
    ("audit",                                   "router", "skill", "skill_audit",   "bare audit"),
    ("capability audit",                        "router", "skill", "skill_audit",   "capability audit"),
    ("what am i missing",                       "router", "skill", "skill_audit",   "what am i missing"),
    ("gap analysis",                            "router", "skill", "skill_audit",   "gap analysis"),

    # ── ROUTER: wrap-up triggers (not in _SKILL_PRE_CHECKS) ──────────
    ("we're done",                              "router", "wrap_up", None,          "we're done"),
    ("wrap this up",                            "router", "wrap_up", None,          "wrap this up"),
    ("save this session",                       "router", "wrap_up", None,          "save this session"),
    ("that's all for now",                      "router", "wrap_up", None,          "that's all for now"),
    ("save to memory",                          "router", "wrap_up", None,          "save to memory"),
    ("summarize and save",                      "router", "wrap_up", None,          "summarize and save"),
    ("let's wrap up",                           "router", "wrap_up", None,          "let's wrap up"),
    ("we are done",                             "router", "wrap_up", None,          "we are done"),
    ("wrap up this session",                    "router", "wrap_up", None,          "wrap up this session"),
    ("save session",                            "router", "wrap_up", None,          "save session"),

    # ── ROUTER: list skills ───────────────────────────────────────────
    ("list your skills",                        "router", "skill", "list_skills",   "list your skills"),
    ("show skills",                             "router", "skill", "list_skills",   "show skills"),
    ("what can you do",                         "router", "skill", "list_skills",   "what can you do"),

    # ── ROUTER: build triggers (non-"build " prefix — those go to build_pipeline) ──
    ("create a job that sends weather reports", "router", "build", None,            "create a job trigger"),
    ("create a skill for bible lookup",         "router", "build", None,            "create a skill trigger"),
    ("write a job that monitors disk space",    "router", "build", None,            "write a job trigger"),
    ("write a skill for email drafting",        "router", "build", None,            "write a skill trigger"),
    ("add the ability to track donations",      "router", "build", None,            "add the ability to trigger"),
    ("i need you to be able to check calendar", "router", "build", None,            "i need you to be able to"),
    ("can you build a habit tracker for me",    "router", "build", None,            "can you build trigger"),
]

# State-dependent cases — not tested for PASS/FAIL
STATE_DEPENDENT_CASES = [
    ("go",            "bot", "email_reply_send", None, "email reply send — needs pending draft"),
    ("approve",       "bot", "build_approval",   None, "build approval — needs pending build"),
    ("yes",           "bot", "pending_confirm",  None, "confirm — needs pending intent/skill/gap"),
    ("sure",          "bot", "pending_confirm",  None, "confirm (affirm) — needs pending intent"),
    ("no",            "bot", "cancel",           None, "cancel — needs pending action"),
    ("never mind",    "bot", "cancel",           None, "cancel — needs pending action"),
    ("what's the answer", "bot", "riddle_answer", None, "riddle answer — needs pending riddle"),
    ("email this to John", "bot", "qr_email",    None, "QR email — needs last_qr in session"),
    ("retry",         "router", "build",         None, "retry build — needs prior failed build"),
]

# Ollama-required cases — noted but not tested
OLLAMA_REQUIRED_CASES = [
    ("who are you",                     "Identity query → intent classifier"),
    ("introduce yourself",              "Identity query → intent classifier"),
    ("hello Watson",                    "Greeting → chat → intent classifier"),
    ("what do you think about prayer",  "General question → intent classifier"),
    ("look up John Smith's address",    "Contact lookup phrasing → intent classifier"),
    ("what's on my calendar today",     "Calendar query phrasing → intent classifier"),
    ("block 90 minutes for sermon prep","Block time → intent classifier"),
    ("am I free on Friday afternoon",   "Calendar availability → intent classifier"),
]


def run_tests():
    passed   = 0
    failed   = 0
    failures = []

    SEP = "─" * 70

    print(SEP)
    print("  Watson Bot Smoke Test — Trigger & Routing")
    print(SEP)
    print()

    for msg, exp_layer, exp_action, exp_slug, desc in TEST_CASES:
        route = decide_route(msg)
        layer  = route["layer"]
        action = route["action"]
        slug   = route.get("slug")
        note   = route.get("note") or ""

        slug_ok  = (exp_slug is None) or (slug == exp_slug)
        layer_ok = (layer == exp_layer)
        action_ok= (action == exp_action)
        ok = slug_ok and layer_ok and action_ok

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
            print(f"  [PASS]  {desc}")
            print(f"          msg    : {msg!r}")
            print(f"          route  : {layer}:{action}" + (f":{slug}" if slug else ""))
        else:
            failed += 1
            got = f"{layer}:{action}" + (f":{slug}" if slug else "")
            exp = f"{exp_layer}:{exp_action}" + (f":{exp_slug}" if exp_slug else "")
            print(f"  [FAIL]  {desc}")
            print(f"          msg      : {msg!r}")
            print(f"          expected : {exp}")
            print(f"          got      : {got}" + (f"  ({note})" if note else ""))
            failures.append((desc, msg, exp, got))
        print()

    # State-dependent
    print(SEP)
    print("  STATE-DEPENDENT (skipped — require runtime state)")
    print(SEP)
    for msg, exp_layer, exp_action, exp_slug, desc in STATE_DEPENDENT_CASES:
        route  = decide_route(msg)
        layer  = route["layer"]
        action = route["action"]
        slug   = route.get("slug")
        print(f"  [SKIP]  {desc}")
        print(f"          msg   : {msg!r}")
        print(f"          route : {layer}:{action}" + (f":{slug}" if slug else ""))
        print()

    # Ollama-required
    print(SEP)
    print("  OLLAMA-REQUIRED (skipped — LLM classification)")
    print(SEP)
    for msg, reason in OLLAMA_REQUIRED_CASES:
        route  = decide_route(msg)
        layer  = route["layer"]
        action = route["action"]
        slug   = route.get("slug")
        print(f"  [SKIP]  {reason}")
        print(f"          msg   : {msg!r}")
        print(f"          route : {layer}:{action}" + (f":{slug}" if slug else ""))
        print()

    # Summary
    total = passed + failed
    print(SEP)
    print("  SUMMARY")
    print(SEP)
    print(f"  Tested           : {total}")
    print(f"  Passed           : {passed}")
    print(f"  Failed           : {failed}")
    print(f"  State-dependent  : {len(STATE_DEPENDENT_CASES)} (skipped)")
    print(f"  Ollama-required  : {len(OLLAMA_REQUIRED_CASES)} (skipped)")
    print()

    if failures:
        print("  FAILED TRIGGERS:")
        for desc, msg, exp, got in failures:
            print(f"    • {desc}")
            print(f"        msg      : {msg!r}")
            print(f"        expected : {exp}")
            print(f"        got      : {got}")
        print()

    if failed == 0:
        print("  All deterministic triggers route correctly.")
    else:
        print(f"  {failed} trigger(s) mis-routed — see above.")
    print(SEP)

    return failed


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
