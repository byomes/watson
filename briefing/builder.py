import html
import logging
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from config.settings import BASE_DIR, DEPLOY_DIR
from core.database import get_connection

_HTML_TAGS = re.compile(r"<[^>]+>")


def _strip_html(text):
    if not text:
        return ""
    stripped = _HTML_TAGS.sub(" ", text)
    unescaped = html.unescape(stripped)
    return " ".join(_HTML_TAGS.sub(" ", unescaped).split())

log = logging.getLogger(__name__)


def _format_date(dt):
    # strftime %-d / %-I are Linux-only; build the string manually for portability
    day = dt.strftime("%A, %B") + f" {dt.day}, {dt.year}"
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{day} at {hour}:{dt.minute:02d} {ampm}"

TEMPLATE_DIR = BASE_DIR / "briefing" / "templates"
OUTPUT_PATH = DEPLOY_DIR / "index.html"

SECTION_ORDER = [
    ("article",     "Articles"),
    ("podcast",     "Podcasts"),
    ("publication", "Publications"),
    ("journal",     "Journal Articles"),
]


def _fetch_top_items():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, source_name, source_type, title, url, summary, published_date, score
            FROM items
            WHERE status = 'new' AND featured_date IS NULL
            ORDER BY score DESC NULLS LAST, published_date DESC
            LIMIT 20
            """
        ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        item["summary"] = _strip_html(item.get("summary") or "")
    return items


def _mark_featured(item_ids):
    today = datetime.now().date().isoformat()
    with get_connection() as conn:
        conn.executemany(
            "UPDATE items SET featured_date = ? WHERE id = ?",
            [(today, id_) for id_ in item_ids],
        )


def _group_by_type(items):
    groups = {key: [] for key, _ in SECTION_ORDER}
    for item in items:
        stype = item.get("source_type", "article")
        if stype in groups:
            groups[stype].append(item)
    return groups


def build():
    items = _fetch_top_items()
    groups = _group_by_type(items)

    total = sum(len(v) for v in groups.values())
    log.info("Building briefing — %d item(s) across %d section(s)",
             total, sum(1 for v in groups.values() if v))

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("briefing.html")

    sections = [
        {"type": stype, "label": label, "cards": groups[stype]}
        for stype, label in SECTION_ORDER
        if groups[stype]
    ]

    html = template.render(
        sections=sections,
        generated_at=_format_date(datetime.now()),
        total=total,
    )

    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    log.info("Briefing written to %s", OUTPUT_PATH)

    if items:
        _mark_featured([item["id"] for item in items])
        log.info("Marked %d item(s) as featured", len(items))

    return OUTPUT_PATH


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = build()
    print(f"\nBriefing saved to {path}")
