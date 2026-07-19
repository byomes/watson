"""jobs/routing/directive_prefixes.py — canonical colon-prefix directive registry.

Single source of truth for which colon-prefixed chat directives exist and which
channels support them. bot.py and jobs/dashboard/app.py both derive their
prefix-recognition sets from this table instead of maintaining separate,
drifting lists (2026-07-17 routing audit found bot.py's `_DIRECTIVE_PREFIXES`
and the dashboard's early `/api/chat/stream` intercepts had quietly diverged).

Per-channel dispatch — what each prefix actually calls, and how the result is
delivered (Telegram reply_text vs dashboard SSE) — stays in bot.py / app.py,
since that legitimately differs by channel and protocol.

`aliases` lists additional spellings one channel accepts for the same
underlying action (e.g. dashboard historically said "build:", Telegram said
"devloop:" — both trigger the same Dev Loop pipeline).
"""

DIRECTIVE_PREFIXES: dict = {
    "cdb:":       {"telegram": True, "dashboard": True},
    "wdb:":       {"telegram": True, "dashboard": True},
    "web:":       {"telegram": True, "dashboard": True},
    "bible:":     {"telegram": True, "dashboard": True},
    "debug:":     {"telegram": True, "dashboard": True},
    "bug:":       {"telegram": True, "dashboard": True},
    "gutenberg:": {"telegram": True, "dashboard": True},
    "classics:":  {"telegram": True, "dashboard": True},
    "polish:":    {"telegram": True, "dashboard": True},
    "run:":       {"telegram": True, "dashboard": True},
    "kb:":        {"telegram": True, "dashboard": True},
    "devloop:":   {"telegram": True, "dashboard": True, "aliases": ["build:"]},
    # Telegram-only — verified 2026-07-17, not just unported:
    "task:":      {"telegram": True, "dashboard": False},   # dashboard has its own natural-language task-add path
    "note:":      {"telegram": True, "dashboard": False},   # pastoral note intake, interactive multi-step flow
    "remind:":    {"telegram": True, "dashboard": False},   # dashboard has its own "remind me ..." regex intake
    "sms:":       {"telegram": True, "dashboard": False},   # carrier-confirm inline-keyboard flow, Telegram UX only
    "fireflies:": {"telegram": True, "dashboard": False},   # backgrounded meeting pipeline, notify-on-done via Telegram
    "curator:":   {"telegram": True, "dashboard": False},   # book submission (title/author or link) -> Curator ingest pipeline
}


def telegram_prefixes() -> tuple:
    """Flat tuple of every prefix string (canonical + aliases) Telegram matches on."""
    out = []
    for canonical, cfg in DIRECTIVE_PREFIXES.items():
        if cfg["telegram"]:
            out.append(canonical)
            out.extend(cfg.get("aliases", []))
    return tuple(out)


def dashboard_prefixes() -> tuple:
    """Flat tuple of every prefix string (canonical + aliases) dashboard matches on."""
    out = []
    for canonical, cfg in DIRECTIVE_PREFIXES.items():
        if cfg["dashboard"]:
            out.append(canonical)
            out.extend(cfg.get("aliases", []))
    return tuple(out)


def canonicalize(prefix: str) -> str:
    """Map an alias prefix back to its canonical name; canonical names map to themselves."""
    if prefix in DIRECTIVE_PREFIXES:
        return prefix
    for canonical, cfg in DIRECTIVE_PREFIXES.items():
        if prefix in cfg.get("aliases", []):
            return canonical
    return prefix
