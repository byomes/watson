import logging
import threading

from flask import Flask, redirect, request, send_file, url_for

from config.settings import DEPLOY_DIR
from core.database import get_connection

log = logging.getLogger(__name__)
app = Flask(__name__)

INDEX_HTML = DEPLOY_DIR / "index.html"
THOUGHT_LIBRARY_HTML = DEPLOY_DIR / "thought-library.html"
RESEARCH_LIBRARY_HTML = DEPLOY_DIR / "research-library.html"
READING_LIST_HTML = DEPLOY_DIR / "reading-list.html"


# ── Pages ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if not INDEX_HTML.exists():
        return (
            "<p>No briefing built yet. "
            "<a href='/run'>Run the pipeline now.</a></p>",
            404,
        )
    return send_file(INDEX_HTML)


# ── Item actions ───────────────────────────────────────────────────────────

def _update_status(status):
    item_id = request.form.get("item_id")
    if not item_id:
        return redirect(url_for("index"))
    with get_connection() as conn:
        conn.execute(
            "UPDATE items SET status = ? WHERE id = ?",
            (status, int(item_id)),
        )
    log.info("Item %s → %s", item_id, status)
    return redirect(url_for("index"))


@app.route("/approve", methods=["POST"])
def approve():
    return _update_status("sent_to_broadcaster")


@app.route("/archive", methods=["POST"])
def archive():
    return _update_status("archived")


@app.route("/dismiss", methods=["POST"])
def dismiss():
    return _update_status("dismissed")


# ── Reading list ───────────────────────────────────────────────────────────

def _render_reading_list():
    from jinja2 import Environment, FileSystemLoader
    from config.settings import BASE_DIR
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, url, source_name, source_type, summary, date_added, status "
            "FROM reading_list WHERE status != 'finished' ORDER BY date_added DESC"
        ).fetchall()
    items = [dict(r) for r in rows]
    env = Environment(
        loader=FileSystemLoader(str(BASE_DIR / "briefing" / "templates")),
        autoescape=True,
    )
    html = env.get_template("reading-list.html").render(items=items, total=len(items))
    READING_LIST_HTML.parent.mkdir(parents=True, exist_ok=True)
    READING_LIST_HTML.write_text(html, encoding="utf-8")


@app.route("/reading-list/add", methods=["POST"])
def reading_list_add():
    item_id = request.form.get("item_id")
    if not item_id:
        return redirect(url_for("index"))
    with get_connection() as conn:
        row = conn.execute(
            "SELECT title, url, source_name, source_type, summary FROM items WHERE id = ?",
            (int(item_id),),
        ).fetchone()
        if row:
            conn.execute(
                "INSERT INTO reading_list (title, url, source_name, source_type, summary) "
                "VALUES (?, ?, ?, ?, ?)",
                (row["title"], row["url"], row["source_name"], row["source_type"], row["summary"]),
            )
            conn.execute(
                "UPDATE items SET status = 'dismissed' WHERE id = ?",
                (int(item_id),),
            )
            log.info("Item %s added to reading list", item_id)
    return redirect(url_for("index"))


@app.route("/reading-list")
def reading_list():
    _render_reading_list()
    return send_file(READING_LIST_HTML)


@app.route("/reading-list/update", methods=["POST"])
def reading_list_update():
    entry_id = request.form.get("entry_id")
    status = request.form.get("status")
    valid_statuses = ("unread", "reading", "finished")
    if not entry_id or status not in valid_statuses:
        return redirect(url_for("reading_list"))
    with get_connection() as conn:
        conn.execute(
            "UPDATE reading_list SET status = ? WHERE id = ?",
            (status, int(entry_id)),
        )
    log.info("Reading list entry %s → %s", entry_id, status)
    return redirect(url_for("reading_list"))


# ── Library ────────────────────────────────────────────────────────────────

@app.route("/library")
def library():
    return redirect(url_for("research_library"))


@app.route("/research-library")
def research_library():
    from library.search import search_research_library, search_to_html
    if not RESEARCH_LIBRARY_HTML.exists():
        search_to_html(search_research_library(), library_type="research")
    return send_file(RESEARCH_LIBRARY_HTML)


@app.route("/thought-library")
def thought_library():
    from library.search import search_thought_library, search_to_html
    if not THOUGHT_LIBRARY_HTML.exists():
        search_to_html(search_thought_library(), library_type="thought")
    return send_file(THOUGHT_LIBRARY_HTML)


@app.route("/search", methods=["POST"])
def search_library():
    from library.search import (
        search_research_library, search_thought_library, search_to_html,
    )
    query = request.form.get("query", "").strip()
    library_type = request.form.get("type", "research")

    if library_type == "thought":
        results = search_thought_library(query)
        search_to_html(results, query=query, library_type="thought")
        return redirect(url_for("thought_library"))
    else:
        results = search_research_library(query)
        search_to_html(results, query=query, library_type="research")
        return redirect(url_for("research_library"))


# ── Manual pipeline trigger ────────────────────────────────────────────────

_pipeline_lock = threading.Lock()


def _run_pipeline():
    from core.fetcher import fetch_all
    from core.summarizer import summarize_items
    from briefing.publisher import publish_briefing

    log.info("Pipeline started")
    new_items = fetch_all()
    log.info("Fetched %d new item(s)", new_items)
    summarized = summarize_items()
    log.info("Summarized %d item(s)", summarized)
    publish_briefing()
    log.info("Pipeline complete")


@app.route("/run")
def run_pipeline():
    if not _pipeline_lock.acquire(blocking=False):
        return "<p>Pipeline already running. Refresh in a moment.</p>", 409

    def run_and_release():
        try:
            _run_pipeline()
        finally:
            _pipeline_lock.release()

    thread = threading.Thread(target=run_and_release, daemon=True)
    thread.start()

    return (
        "<p>Pipeline started. "
        "<a href='/'>Refresh the briefing</a> in 30–60 seconds.</p>"
    )


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app.run(host="127.0.0.1", port=5000, debug=False)
