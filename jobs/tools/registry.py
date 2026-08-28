"""jobs/tools/registry.py — register/query/gate public_tools rows for
wtsn.me.

Slug status is the single source of truth for whether a tool is reachable:
'draft' rows exist but are never returned by get_live_tool() (the function
jobs/tools/api.py's public /api/tools/resolve/<slug> route calls), so a
tool can be built and pushed to the watson-tools repo well before it's
actually live — going live is a separate, explicit step gated by Telegram
confirm (request_first_deploy / flip_live), the same pending_actions
mechanism the six classifier-stage gated writes in bot.py already use (see
Confirmation Gate in WATSON_ARCHITECTURE.md).

'custom' tool_type rows track status/gating for a tool that has its own
dedicated Next.js route (e.g. src/app/connect-card/page.tsx) rather than
being rendered by the generic [slug]/page.tsx catch-all — Next.js's router
matches a static route ahead of a dynamic segment, so a 'custom' row's
target_url/body_text are never actually read by that catch-all; the row
exists purely so the same go-live gate applies to it too.
"""
import re

from core.database import get_connection

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_VALID_TYPES = ("redirect", "page", "custom")


def register_tool(slug: str, title: str, tool_type: str, target_url: str | None = None,
                   body_text: str | None = None) -> dict:
    slug = (slug or "").strip().lower()
    title = (title or "").strip()
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
        existing = conn.execute("SELECT slug FROM public_tools WHERE slug = ?", (slug,)).fetchone()
        if existing:
            raise ValueError(f"slug '{slug}' already exists")
        conn.execute(
            """INSERT INTO public_tools (slug, title, tool_type, target_url, body_text)
               VALUES (?, ?, ?, ?, ?)""",
            (slug, title, tool_type, target_url, body_text),
        )
        row = conn.execute("SELECT * FROM public_tools WHERE slug = ?", (slug,)).fetchone()
    return dict(row)


def get_tool(slug: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM public_tools WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def get_live_tool(slug: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM public_tools WHERE slug = ? AND status = 'live'", (slug,)
        ).fetchone()
    return dict(row) if row else None


def _has_pending_first_deploy(slug: str) -> bool:
    from config.settings import TELEGRAM_CHAT_ID
    from jobs.gcal import pending as pending_module

    p = pending_module.get_pending(TELEGRAM_CHAT_ID)
    return bool(p and p["action_type"] == "tool_first_deploy" and p["params"].get("slug") == slug)


def request_first_deploy(slug: str) -> dict:
    """Kick off the first-deploy Telegram confirm gate for a draft tool.

    Proactive send — this isn't a reply to an incoming Telegram message, it's
    called by whatever build step just finished pushing the tool's first
    deploy. Uses the same pending_actions/save_pending mechanism the six
    classifier-stage gated writes rely on, so bot.py's existing YES/NO
    handling in _execute_pending picks it up with just one new action_type
    branch there — no new plumbing.
    """
    import requests
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    from core.vacation import vacation_gate
    from jobs.gcal import pending as pending_module

    tool = get_tool(slug)
    if not tool:
        raise ValueError(f"no tool registered for slug '{slug}'")
    if tool["status"] != "draft":
        raise ValueError(f"tool '{slug}' is not in draft status (status={tool['status']!r})")
    if _has_pending_first_deploy(slug):
        return {"already_pending": True, "slug": slug}

    display = f"{tool['title']} — https://wtsn.me/{slug}"
    pending_module.save_pending(
        TELEGRAM_CHAT_ID, "tool_first_deploy", {"slug": slug}, {"display": display},
    )

    text = (
        f"\U0001f195 First deploy of a new public tool — go live?\n\n{display}\n\n"
        "Reply YES to confirm or NO to cancel."
    )
    # "normal" priority (not "system_failure") — a new tool going live isn't
    # urgent the way a security alert is; it can wait out a vacation-mode
    # suppression window like the other five gated write intents.
    if not vacation_gate("normal", "jobs.tools.registry", text):
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=10,
            )
    return {"already_pending": False, "slug": slug}


def flip_live(slug: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE public_tools
               SET status = 'live', updated_at = datetime('now'), first_deploy_confirmed_at = datetime('now')
               WHERE slug = ?""",
            (slug,),
        )
