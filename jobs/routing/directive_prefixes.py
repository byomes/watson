"""jobs/routing/directive_prefixes.py — canonical colon-prefix directive registry.

Single source of truth for which colon-prefixed chat directives exist, which
channels support them, and how they're described in every menu surface.
bot.py and jobs/dashboard/app.py both derive their prefix-recognition sets
from this table instead of maintaining separate, drifting lists (2026-07-17
routing audit found bot.py's `_DIRECTIVE_PREFIXES` and the dashboard's early
`/api/chat/stream` intercepts had quietly diverged). A follow-up 2026-07-29
audit found the same drift had spread to the *menu* surfaces that describe
these directives to users (memory/commands.json, bot.py's /help text) —
`description`, `example`, and `category` were added per entry so those menus
can be generated from here too, instead of hand-maintained lists that fall
out of sync with what's actually wired up.

Per-channel dispatch — what each prefix actually calls, and how the result is
delivered (Telegram reply_text vs dashboard SSE) — stays in bot.py / app.py,
since that legitimately differs by channel and protocol.

`aliases` lists additional spellings one channel accepts for the same
underlying action (e.g. dashboard historically said "build:", Telegram said
"devloop:" — both trigger the same Dev Loop pipeline).
"""

DIRECTIVE_PREFIXES: dict = {
    "cdb:": {
        "telegram": True, "dashboard": True, "category": "Congregation",
        "description": "Query the congregation database — attendance, membership trends, member lookups",
        "example": "cdb: who attended this Sunday",
    },
    "wdb:": {
        "telegram": True, "dashboard": True, "category": "Leadership",
        "description": "Query the team/task database — completion rates, stalled tasks, leader activity",
        "example": "wdb: team overview",
    },
    "web:": {
        "telegram": True, "dashboard": True, "category": "Research",
        "description": "Search the web and return a synthesized answer",
        "example": "web: Scot McKnight on the Sermon on the Mount",
    },
    "backlog:": {
        "telegram": True, "dashboard": False, "category": "Dev",
        # dashboard reachable via /api/terminal only, not this registry — see app.py terminal()
        "description": "Log an item to the dev backlog",
        "example": "backlog: add dark mode | requested by Mel",
    },
    "kb:": {
        "telegram": True, "dashboard": True, "category": "Knowledge Base",
        "description": "Search sermon & Q&A transcripts via ChromaDB (bible study notes/handouts only surface via xkb:)",
        "example": "kb: what does the sermon say about forgiveness",
    },
    "xkb:": {
        "telegram": True, "dashboard": True, "category": "Knowledge Base",
        "description": "Same as kb:, but immediately includes bible study notes and handouts — no follow-up needed",
        "example": "xkb: what does the sermon say about forgiveness",
    },
    # Telegram-only — verified 2026-07-17, not just unported:
    "task:": {
        "telegram": True, "dashboard": False, "category": "Tasks & Reminders",
        # dashboard has its own natural-language task-add path
        "description": "Add a task to the Catalyst task list",
        "example": "task: Prepare elder meeting agenda for Thursday",
    },
    "note:": {
        "telegram": True, "dashboard": False, "category": "Congregation",
        # pastoral note intake, interactive multi-step flow
        "description": "Save a pastoral note directly, no post-meeting prompt",
        "example": "note: Spoke with James after service — struggling with job loss",
    },
    "remind:": {
        "telegram": True, "dashboard": False, "category": "Tasks & Reminders",
        # dashboard has its own "remind me ..." regex intake
        "description": "Save a reminder to the active reminders list",
        "example": "remind: Call the Johnsons about the baptism date",
    },
    "sms:": {
        "telegram": True, "dashboard": False, "category": "Communication",
        # carrier-confirm inline-keyboard flow, Telegram UX only
        "description": "Send an SMS to a contact",
        "example": "sms: John Smith: Let me know when you're available this week.",
    },
    "fireflies:": {
        "telegram": True, "dashboard": False, "category": "Meetings",
        # backgrounded meeting pipeline, notify-on-done via Telegram
        "description": "Process a Fireflies meeting recording in the background, notify when done",
        "example": "fireflies: 01JABC23XYZ",
    },
    "curator:": {
        "telegram": True, "dashboard": False, "category": "Books",
        # book submission (title/author or link) -> Curator ingest pipeline
        "description": "Submit a book (title/author or link) to the Curator ingest pipeline",
        "example": "curator: City of Bones by Cassandra Clare",
    },
    # Added 2026-07-29 menu-drift audit — both already had working dispatch in
    # app.py's /api/terminal (and image_gen/shepherding_report skills), just
    # weren't tracked here, so they were invisible to any generated menu.
    "imagegen:": {
        "telegram": True, "dashboard": True, "category": "Utilities", "aliases": ["imgen:"],
        "description": "Generate an image from a text prompt",
        "example": "imagegen: a stained glass window depicting the Good Shepherd",
    },
    "shepherding:": {
        "telegram": True, "dashboard": True, "category": "Congregation",
        "description": "Members overdue for pastoral contact based on attendance history",
        "example": "shepherding:",
    },
    # Added 2026-09-02: dashboard-only debug bypass so Bill can exercise the
    # leader-facing Telegram team-chat path (bot.py's compute_team_chat_reply /
    # data_chat.py) without a second Telegram account. No Telegram equivalent —
    # Bill's own Telegram already gets full owner access, not the team-chat path.
    "teamtest:": {
        "telegram": False, "dashboard": True, "category": "Dev",
        "description": "Ask a question as the synthetic 'Test Person' team member — mirrors exactly what an onboarded leader's Telegram team-chat gets back",
        "example": "teamtest: how many people came Sunday?",
    },
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


def telegram_help_text() -> str:
    """Render the directive-prefix portion of Telegram's /help message.

    One line per telegram:True entry: "<example> — <description>", in
    registry order. Callers append/prepend the non-directive parts of /help
    (book commands, #blog, etc.) themselves.
    """
    lines = []
    for prefix, cfg in DIRECTIVE_PREFIXES.items():
        if cfg["telegram"]:
            lines.append(f"{cfg['example']} — {cfg['description']}")
    return "\n".join(lines)
