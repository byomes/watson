import logging
import threading

from flask import Flask, redirect, request, send_file, url_for

from config.settings import DEPLOY_DIR
from core.database import get_connection

log = logging.getLogger(__name__)
app = Flask(__name__)

INDEX_HTML = DEPLOY_DIR / "index.html"


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
