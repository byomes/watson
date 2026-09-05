"""jobs/tools/registry.py — register/query/gate public_tools rows for
wtsn.me.

Tools are addressed by (category, slug) — e.g. category='cat', slug='connect'
resolves at wtsn.me/cat/connect. Categories group tools by area of life
(cat/ for Catalyst, writing/, fms/, adelphos/, etc.), decided by Bill per
tool, not inferred.

Slug status is the single source of truth for whether a tool is reachable:
'draft' rows exist but are never returned by get_live_tool() (the function
jobs/tools/api.py's public /api/tools/resolve/<category>/<slug> route
calls), so a tool can be built and pushed to the watson-tools repo well
before it's actually live — going live is a separate, explicit step gated
by a Telegram Go Live/Cancel button confirm (request_first_deploy /
flip_live), matching the button-based pattern jobs/adelphos/security_monitor.py
already established (see bot.py's handle_tool_deploy_callback) — not the
pending_actions/typed-YES-NO mechanism the six classifier-stage gated
writes use. Bill's explicit preference (2026-08-28): buttons over typing a
reply for this kind of confirm.

'custom' tool_type rows track status/gating for a tool that has its own
dedicated Next.js route (e.g. src/app/cat/connect/page.tsx) rather than
being rendered by the generic [category]/[slug]/page.tsx catch-all —
Next.js's router matches a static route ahead of a dynamic segment, so a
'custom' row's target_url/body_text are never actually read by that
catch-all; the row exists purely so the same go-live gate applies to it
too.
"""
import re

from core.database import get_connection

_CATEGORY_RE = re.compile(r"^[a-z0-9-]+$")
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_VALID_TYPES = ("redirect", "page", "custom")


def register_tool(category: str, slug: str, title: str, tool_type: str,
                   target_url: str | None = None, body_text: str | None = None) -> dict:
    category = (category or "").strip().lower()
    slug = (slug or "").strip().lower()
    title = (title or "").strip()
    if not category or not _CATEGORY_RE.match(category):
        raise ValueError("category must be URL-safe (lowercase letters, numbers, hyphens)")
    if not slug or not _SLUG_RE.match(slug):
        raise ValueError("slug must be URL-safe (lowercase letters, numbers, hyphens)")
    if not title:
        raise ValueError("title is required")
    if tool_type not in _VALID_TYPES:
        raise ValueError(f"tool_type must be one of: {', '.join(_VALID_TYPES)}")
    if tool_type == "redirect" and not (target_url or "").strip():
        raise ValueError("target_url is required for tool_type='redirect'")
    if tool_type == "page" and not (body_text or "").strip():
        raise ValueError("body_text is required for tool_type='page'")

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT slug FROM public_tools WHERE category = ? AND slug = ?", (category, slug)
        ).fetchone()
        if existing:
            raise ValueError(f"'{category}/{slug}' already exists")
        conn.execute(
            """INSERT INTO public_tools (category, slug, title, tool_type, target_url, body_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (category, slug, title, tool_type, target_url, body_text),
        )
        row = conn.execute(
            "SELECT * FROM public_tools WHERE category = ? AND slug = ?", (category, slug)
        ).fetchone()
    return dict(row)


def get_tool(category: str, slug: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM public_tools WHERE category = ? AND slug = ?", (category, slug)
        ).fetchone()
    return dict(row) if row else None


def get_live_tool(category: str, slug: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM public_tools WHERE category = ? AND slug = ? AND status = 'live'",
            (category, slug),
        ).fetchone()
    return dict(row) if row else None


def request_first_deploy(category: str, slug: str) -> dict:
    """Kick off the first-deploy Telegram confirm gate for a draft tool.

    Proactive send — this isn't a reply to an incoming Telegram message,
    it's called by whatever build step just finished pushing the tool's
    first deploy. Sends a message with inline Go Live / Cancel buttons
    (callback_data carries category:slug directly, so no separate
    pending-state row is needed — bot.py's handle_tool_deploy_callback acts
    directly on the tap). No dedup guard against a second call sending a
    second prompt: the callback is idempotent (checks current status before
    flipping), so a duplicate prompt is just mildly noisy, never unsafe.
    """
    import requests
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    from core.vacation import vacation_gate

    tool = get_tool(category, slug)
    if not tool:
        raise ValueError(f"no tool registered for '{category}/{slug}'")
    if tool["status"] != "draft":
        raise ValueError(f"tool '{category}/{slug}' is not in draft status (status={tool['status']!r})")

    display = f"{tool['title']} — https://wtsn.me/{category}/{slug}"
    text = f"\U0001f195 First deploy of a new public tool — go live?\n\n{display}"

    # "normal" priority (not "system_failure") — a new tool going live isn't
    # urgent the way a security alert is; it can wait out a vacation-mode
    # suppression window like the other five gated write intents.
    if not vacation_gate("normal", "jobs.tools.registry", text):
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "reply_markup": {
                        "inline_keyboard": [[
                            {"text": "✅ Go Live", "callback_data": f"tool_deploy_yes:{category}:{slug}"},
                            {"text": "❌ Cancel", "callback_data": f"tool_deploy_no:{category}:{slug}"},
                        ]],
                    },
                },
                timeout=10,
            )
    return {"category": category, "slug": slug}


def flip_live(category: str, slug: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE public_tools
               SET status = 'live', updated_at = datetime('now'), first_deploy_confirmed_at = datetime('now')
               WHERE category = ? AND slug = ?""",
            (category, slug),
        )
