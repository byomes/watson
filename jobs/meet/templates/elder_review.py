"""
Elders Meeting Review email template.

Visual style matches jobs/connect_cards/state_of_church.py — the closest
existing precedent for a professional, Catalyst Community Church-branded
HTML pastoral email (inline styles, 600px wrapper, black/gray palette,
"Watson / Office of Dr. Bill Yomes" header/footer bars). jobs/givebutter/
templates.py was checked too, but it's bare <p> tags with no styling and
FMS-branded, not a good match for this audience.

render_elder_review_email() takes structured content only (title,
date_display, summary_points, action_items grouped by owner, fallback flag)
— it does not call Ollama and does not know anything about how that content
was produced. jobs/meet/fireflies_review.py owns getting Ollama's JSON into
this shape; this module only renders.

build_structured_content_from_review() adapts the dashboard-editable schema
(meeting_reviews + meeting_review_action_items, watson.db — flat per-item
rows with an owner_member_id Bill can reassign) into that same grouped
shape, so the one render function serves both the original Ollama-straight-
through path and the reviewed/edited dashboard path without duplicating any
HTML.
"""

from jobs.meet.fireflies_review import get_member_name


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _html_section_header(title: str) -> str:
    return (
        f'<h2 style="margin:28px 0 0;font-size:12px;font-weight:700;color:#1a1a1a;'
        f'text-transform:uppercase;letter-spacing:0.8px;padding-bottom:8px;'
        f'border-bottom:2px solid #1a1a1a;">{_esc(title)}</h2>'
    )


def _summary_block(summary_points: list[str]) -> str:
    if not summary_points:
        return '<p style="margin:12px 0 0;font-size:14px;color:#888;font-style:italic;">No summary available.</p>'
    items = "".join(
        f'<li style="padding:5px 0;font-size:14px;color:#333;line-height:1.55;">{_esc(pt)}</li>'
        for pt in summary_points
    )
    return f'<ul style="margin:12px 0 0;padding-left:20px;">{items}</ul>'


def _action_items_block(action_items: list[dict]) -> str:
    groups = [g for g in (action_items or []) if (g.get("items") or [])]
    if not groups:
        return '<p style="margin:12px 0 0;font-size:14px;color:#888;font-style:italic;">No action items.</p>'

    blocks = []
    for i, group in enumerate(groups):
        owner = group.get("owner") or "Unassigned"
        items = group.get("items") or []
        border = "" if i == len(groups) - 1 else "border-bottom:1px solid #f0f0f0;"
        item_lis = "".join(
            f'<li style="padding:3px 0;font-size:14px;color:#333;line-height:1.5;">{_esc(it)}</li>'
            for it in items
        )
        blocks.append(f"""
      <div style="padding:12px 0;{border}">
        <span style="font-size:14px;font-weight:700;color:#1a1a1a;">{_esc(owner)}</span>
        <ul style="margin:6px 0 0;padding-left:20px;">{item_lis}</ul>
      </div>""")
    return f'<div style="margin-top:4px;">{"".join(blocks)}</div>'


def render_elder_review_email(structured: dict, preview: bool = False) -> tuple[str, str]:
    """Return (subject, html) for an elders meeting review email.

    structured: {
      "title": str,
      "date_display": str,          # already human-formatted — see
                                     # fireflies_review._format_meeting_date()
      "summary_points": [str, ...],
      "action_items": [{"owner": str, "items": [str, ...]}, ...],
      "fallback": bool,             # True if Ollama's structured content
                                     # generation failed and this is the
                                     # basic fallback version
    }
    """
    title        = structured.get("title") or "Elders Meeting"
    date_display = structured.get("date_display") or "Unknown date"
    fallback     = bool(structured.get("fallback"))

    subject_prefix = "PREVIEW: " if preview else ""
    subject = f"{subject_prefix}Elders Meeting Review — {date_display}"

    preview_banner = (
        '<div style="margin:0;padding:10px 32px;background:#fff3cd;border-bottom:1px solid #ffe08a;">'
        '<p style="margin:0;font-size:12px;font-weight:700;color:#8a6d1f;text-transform:uppercase;'
        'letter-spacing:0.5px;">Preview — elders have not received this yet</p>'
        "</div>"
    ) if preview else ""

    fallback_banner = ("""
    <div style="margin:16px 32px 0;padding:10px 14px;background:#fff8e1;border-left:3px solid #f57c00;">
      <p style="margin:0;font-size:12px;color:#8a6d3b;line-height:1.5;">
        Note: automatic formatting of this meeting's transcript failed after a retry —
        this is a basic summary. Consider reviewing the full transcript directly in Fireflies.
      </p>
    </div>""") if fallback else ""

    summary_html = _summary_block(structured.get("summary_points") or [])
    action_items_html = _action_items_block(structured.get("action_items") or [])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:20px 0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:4px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
    {preview_banner}

    <!-- Watson header -->
    <div style="padding:14px 32px;border-bottom:1px solid #ebebeb;">
      <p style="margin:0;font-size:10px;color:#aaa;letter-spacing:0.8px;text-transform:uppercase;">Watson &nbsp;/&nbsp; Office of Dr. Bill Yomes</p>
    </div>

    <!-- Title -->
    <div style="padding:28px 32px 0;">
      <p style="margin:0 0 6px;font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:0.8px;">Catalyst Community Church</p>
      <h1 style="margin:0;font-size:26px;font-weight:700;color:#1a1a1a;line-height:1.25;">Elders Meeting Review</h1>
      <p style="margin:6px 0 0;font-size:15px;color:#666;">{_esc(title)} &nbsp;&middot;&nbsp; {_esc(date_display)}</p>
    </div>
    {fallback_banner}

    <!-- Main content -->
    <div style="padding:0 32px 32px;">

      {_html_section_header("Meeting Overview")}
      {summary_html}

      {_html_section_header("Action Items")}
      {action_items_html}

    </div>

    <!-- Footer -->
    <div style="padding:18px 32px;border-top:1px solid #ebebeb;background:#fafafa;">
      <p style="margin:0;font-size:11px;color:#bbb;text-align:center;">Watson &nbsp;/&nbsp; AI-powered digital assistant &nbsp;/&nbsp; Office of Dr. Bill Yomes</p>
    </div>

  </div>
</body>
</html>"""

    return subject, html


def render_elder_review_plain(structured: dict) -> str:
    """Plain-text alternative part for the same structured content."""
    date_display = structured.get("date_display") or "Unknown date"
    lines = [f"Elders Meeting Review — {date_display}", ""]

    if structured.get("fallback"):
        lines.append("(Note: automatic formatting failed — basic summary shown below.)")
        lines.append("")

    lines.append("Meeting Overview:")
    for pt in structured.get("summary_points") or []:
        lines.append(f"- {pt}")
    lines.append("")

    lines.append("Action Items:")
    groups = [g for g in (structured.get("action_items") or []) if (g.get("items") or [])]
    if not groups:
        lines.append("(none)")
    for group in groups:
        owner = group.get("owner") or "Unassigned"
        lines.append(f"{owner}:")
        for item in group.get("items") or []:
            lines.append(f"  - {item}")

    return "\n".join(lines)


def _resolve_owner_display_name(owner_member_id: int | None, owner_text: str) -> str:
    """Resolve the final owner display name for one action item.
    owner_member_id is checked against the fixed ELDER_REVIEW_OWNERS list
    first (the only pool the dashboard dropdown offers going forward), then
    falls back to a direct congregation.db lookup for backward compatibility
    with any review saved before that restriction shipped, then to the
    stored owner_text (Fireflies' raw guess, or whatever Bill typed for an
    added item), then "Unassigned"."""
    if owner_member_id:
        from jobs.meet.fireflies_review import ELDER_REVIEW_OWNERS
        fixed = next((o for o in ELDER_REVIEW_OWNERS if o["id"] == owner_member_id), None)
        if fixed:
            return fixed["display_name"]
        legacy_name = get_member_name(owner_member_id)
        if legacy_name:
            return legacy_name
    return (owner_text or "").strip() or "Unassigned"


def build_structured_content_from_review(review: dict, items: list[dict]) -> dict:
    """Adapt meeting_reviews + meeting_review_action_items rows into the
    structured content shape render_elder_review_email()/
    render_elder_review_plain() expect. Groups the flat per-item rows by
    resolved owner name — see _resolve_owner_display_name(). Called from
    jobs/dashboard/app.py's /meet/review/<id> preview and send routes —
    review and items come straight from watson.db.
    """
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for item in sorted(items, key=lambda it: it.get("sort_order", 0)):
        item_text = (item.get("item_text") or "").strip()
        if not item_text:
            continue
        owner = _resolve_owner_display_name(item.get("owner_member_id"), item.get("owner_text") or "")
        if owner not in groups:
            groups[owner] = []
            order.append(owner)
        groups[owner].append(item_text)

    summary_text = review.get("summary_text") or ""
    summary_points = [line.strip() for line in summary_text.split("\n") if line.strip()]

    return {
        "title": review.get("title") or "Elders Meeting",
        "date_display": review.get("meeting_date") or "Unknown date",
        "summary_points": summary_points,
        "action_items": [{"owner": o, "items": groups[o]} for o in order],
    }
