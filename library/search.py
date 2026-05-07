import logging
from datetime import datetime

from jinja2 import Environment, FileSystemLoader

from config.settings import BASE_DIR, DEPLOY_DIR
from core.database import get_connection

log = logging.getLogger(__name__)

TEMPLATE_DIR = BASE_DIR / "briefing" / "templates"


def _build_query(table, text_fields, filters):
    query, content_type, bible_passage, date_from, date_to = (
        filters.get("query", ""),
        filters.get("content_type"),
        filters.get("bible_passage"),
        filters.get("date_from"),
        filters.get("date_to"),
    )

    conditions, params = [], []

    if query:
        pattern = f"%{query}%"
        like_clause = " OR ".join(f"{f} LIKE ?" for f in text_fields)
        conditions.append(f"({like_clause})")
        params.extend([pattern] * len(text_fields))

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
    return f"SELECT * FROM {table} {where} ORDER BY date_indexed DESC", params


def search_thought_library(query="", **filters):
    filters["query"] = query
    sql, params = _build_query(
        "thought_library",
        ["title", "body", "tags", "bible_passage"],
        filters,
    )
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    results = [dict(r) for r in rows]
    log.info("Thought Library search '%s' → %d result(s)", query or "(all)", len(results))
    return results


def search_research_library(query="", **filters):
    filters["query"] = query
    sql, params = _build_query(
        "research_library",
        ["title", "summary", "author", "source_name", "tags"],
        filters,
    )
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    results = [dict(r) for r in rows]
    log.info("Research Library search '%s' → %d result(s)", query or "(all)", len(results))
    return results


def search_to_html(results, query="", library_type="research"):
    if library_type == "thought":
        template_name = "thought_library.html"
        output_path = DEPLOY_DIR / "thought-library.html"
    else:
        template_name = "research_library.html"
        output_path = DEPLOY_DIR / "research-library.html"

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template(template_name)

    html = template.render(
        results=results,
        query=query,
        total=len(results),
        generated_at=datetime.now().strftime("%B %d, %Y"),
    )

    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("%s written to %s", template_name, output_path)
    return output_path
