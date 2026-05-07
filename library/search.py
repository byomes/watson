import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from config.settings import BASE_DIR, DEPLOY_DIR
from core.database import get_connection

log = logging.getLogger(__name__)

TEMPLATE_DIR = BASE_DIR / "briefing" / "templates"
LIBRARY_HTML = DEPLOY_DIR / "library.html"


def search(query, content_type=None, bible_passage=None, date_from=None, date_to=None):
    conditions = []
    params = []

    if query:
        pattern = f"%{query}%"
        conditions.append(
            "(title LIKE ? OR body LIKE ? OR tags LIKE ? OR bible_passage LIKE ?)"
        )
        params.extend([pattern, pattern, pattern, pattern])

    if content_type:
        conditions.append("content_type = ?")
        params.append(content_type)

    if bible_passage:
        conditions.append("bible_passage LIKE ?")
        params.append(f"%{bible_passage}%")

    if date_from:
        conditions.append("date_indexed >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("date_indexed <= ?")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT id, content_type, title, body, tags, bible_passage, date_created, date_indexed
        FROM library
        {where}
        ORDER BY date_indexed DESC
    """

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    results = [dict(row) for row in rows]
    log.info("Search '%s' → %d result(s)", query or "(all)", len(results))
    return results


def search_to_html(results, query=""):
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("library.html")

    html = template.render(
        results=results,
        query=query,
        total=len(results),
        generated_at=datetime.now().strftime("%B %d, %Y"),
    )

    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_HTML.write_text(html, encoding="utf-8")
    log.info("Library page written to %s", LIBRARY_HTML)
    return LIBRARY_HTML
