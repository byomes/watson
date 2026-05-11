import logging
import re as _re
import threading
from datetime import datetime

import yaml
from flask import Flask, redirect, request, send_file, url_for
from jinja2 import Environment, FileSystemLoader

from config.settings import BASE_DIR, DEPLOY_DIR
from core.database import get_connection
from core.scorer import _BOOST

SOURCES_PATH = BASE_DIR / "config" / "sources.yaml"
TEMPLATE_DIR = BASE_DIR / "briefing" / "templates"

log = logging.getLogger(__name__)
app = Flask(__name__)

THOUGHT_LIBRARY_HTML = DEPLOY_DIR / "thought-library.html"
RESEARCH_LIBRARY_HTML = DEPLOY_DIR / "research-library.html"
READING_LIST_HTML = DEPLOY_DIR / "reading-list.html"


# ── Briefing dashboard ─────────────────────────────────────────────────────

@app.route("/")
def index():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, title, url, summary, source_name, source_type
            FROM briefing_items
            WHERE dismissed = 0
            ORDER BY score DESC, fetched_at DESC
            LIMIT 50
            """
        ).fetchall()
    items = [dict(r) for r in rows]

    dt = datetime.now()
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    generated_at = f"{dt.strftime('%B')} {dt.day}, {dt.year} at {hour}:{dt.minute:02d} {ampm}"

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("dashboard.html")
    html = template.render(items=items, total=len(items), generated_at=generated_at)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


# ── Jenny action (forward to Telegram) ────────────────────────────────────

@app.route("/action", methods=["POST"])
def action():
    item_id = request.form.get("item_id")
    dest = request.form.get("dest")  # "email", "facebook", or "dismiss"

    if not item_id:
        return redirect(url_for("index"))

    item_id = int(item_id)
    item = None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT title, url, summary FROM briefing_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row:
            item = dict(row)

    if item and dest in ("email", "facebook"):
        try:
            from telegram.jenny import send_to_email, send_to_facebook
            if dest == "email":
                send_to_email(item["title"], item["summary"] or "", item["url"] or "")
            else:
                send_to_facebook(item["title"], item["summary"] or "", item["url"] or "")
            log.info("Forwarded item %d to %s via Jenny", item_id, dest)
        except Exception as e:
            log.error("Jenny send failed: %s", e)

    with get_connection() as conn:
        conn.execute(
            "UPDATE briefing_items SET dismissed = 1 WHERE id = ?", (item_id,)
        )

    return redirect(url_for("index"))


@app.route("/reject", methods=["POST"])
def reject_item():
    item_id       = request.form.get("item_id")
    reject_reason = request.form.get("reject_reason", "").strip()

    if not item_id or not reject_reason:
        return redirect(url_for("index"))

    item_id = int(item_id)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT title, summary, source_name FROM briefing_items WHERE id = ?",
            (item_id,),
        ).fetchone()

        if row:
            conn.execute(
                "UPDATE briefing_items SET dismissed = 1, reject_reason = ? WHERE id = ?",
                (reject_reason, item_id),
            )
            text     = f"{row['title']} {row['summary'] or ''}"
            keywords = {m.lower() for m in _BOOST.findall(text)}
            for kw in keywords:
                existing = conn.execute(
                    "SELECT id FROM rejection_patterns "
                    "WHERE source_name = ? AND keyword = ? AND reason = ?",
                    (row["source_name"], kw, reject_reason),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE rejection_patterns SET count = count + 1, "
                        "last_seen = datetime('now') WHERE id = ?",
                        (existing["id"],),
                    )
                else:
                    conn.execute(
                        "INSERT INTO rejection_patterns (source_name, keyword, reason) "
                        "VALUES (?, ?, ?)",
                        (row["source_name"], kw, reject_reason),
                    )
            log.info("Rejected item %d (%s): %s", item_id, reject_reason, row["title"][:60])

    return redirect(url_for("index"))


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
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, url, source_name, source_type, summary, date_added, status "
            "FROM reading_list WHERE status != 'finished' ORDER BY date_added DESC"
        ).fetchall()
    items = [dict(r) for r in rows]
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
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


# ── Sources management ─────────────────────────────────────────────────────

def _update_source_in_yaml(source_name, priority, active):
    """Update priority and active for one source in sources.yaml, preserving comments."""
    lines = SOURCES_PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    # Locate the block start: line matching "  - name: "SOURCE_NAME""
    start = None
    for i, line in enumerate(lines):
        m = _re.match(r'^\s+-\s+name:\s+"?(.+?)"?\s*$', line)
        if m and m.group(1) == source_name:
            start = i
            break
    if start is None:
        log.warning("Source '%s' not found in sources.yaml", source_name)
        return

    # Locate block end: next entry at same indent level, or section header
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _re.match(r'\s+-\s+name:', lines[i]):
            end = i
            break
        if _re.match(r'^[a-z]', lines[i]) and ':' in lines[i]:
            end = i
            break

    block = list(lines[start:end])

    # Update or insert priority field
    priority_set = False
    for j, bline in enumerate(block):
        if _re.match(r'\s+priority:', bline):
            block[j] = _re.sub(r'(\s+priority:\s*)\d+', rf'\g<1>{priority}', bline)
            priority_set = True
            break
    if not priority_set:
        for j, bline in enumerate(block):
            if _re.match(r'\s+source_type:', bline):
                indent = _re.match(r'^(\s+)', bline).group(1)
                block.insert(j + 1, f'{indent}priority: {priority}\n')
                break

    # Update or insert/remove active field
    active_idx = None
    for j, bline in enumerate(block):
        if _re.match(r'\s+active:', bline):
            active_idx = j
            break

    if not active:
        if active_idx is not None:
            block[active_idx] = _re.sub(r'(\s+active:\s*)\w+', r'\g<1>false', block[active_idx])
        else:
            # Insert before trailing comment lines
            insert_at = len(block)
            for j in range(len(block) - 1, 0, -1):
                stripped = block[j].strip()
                if stripped and not stripped.startswith('#'):
                    insert_at = j + 1
                    break
            indent = '    '
            block.insert(insert_at, f'{indent}active: false\n')
    else:
        # active=True is the default; remove the explicit line
        if active_idx is not None:
            del block[active_idx]

    lines[start:end] = block
    SOURCES_PATH.write_text(''.join(lines), encoding="utf-8")
    log.info("Updated source '%s': priority=%s active=%s", source_name, priority, active)


@app.route("/sources")
def sources():
    with open(SOURCES_PATH) as f:
        data = yaml.safe_load(f) or {}

    category_meta = [
        ("authors",       "author",       "Authors"),
        ("organizations", "organization", "Organizations"),
        ("journals",      "journal",      "Journals"),
    ]
    sections = []
    for key, cat, label in category_meta:
        rows = []
        for entry in data.get(key, []) or []:
            rows.append({
                "name":     entry["name"],
                "category": cat,
                "priority": int(entry.get("priority", 3)),
                "active":   entry.get("active", True) is not False,
            })
        sections.append({"label": label, "category": cat, "sources": rows})

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("sources.html").render(sections=sections)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/sources/update", methods=["POST"])
def sources_update():
    name     = request.form.get("name", "").strip()
    priority = request.form.get("priority", "3")
    active   = request.form.get("active") == "1"

    if not name or priority not in ("1", "2", "3"):
        return redirect(url_for("sources"))

    _update_source_in_yaml(name, int(priority), active)
    return redirect(url_for("sources"))


# ── Manual pipeline trigger ────────────────────────────────────────────────

_pipeline_lock = threading.Lock()


def _run_pipeline():
    from core.pipeline import run as pipeline_run

    log.info("Pipeline started")
    count = pipeline_run()
    log.info("Pipeline complete — %d new item(s)", count)


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
