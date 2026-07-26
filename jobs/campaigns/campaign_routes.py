"""jobs/campaigns/campaign_routes.py — Flask Blueprint: the weekly edit/approve
page for book-launch campaigns.

Mount on the Watson dashboard app:
    from jobs.campaigns.campaign_routes import campaigns_bp
    app.register_blueprint(campaigns_bp)

Auth: gated by jobs.dashboard.app._admin_required() — the same session check
used by /admin/* and the meet-review pages (see the "Gated by _admin_required()"
comment above meet_reviews_list() in app.py: the admin session is this app's
established precedent for "protected, sensitive dashboard content", and these
routes can trigger real sends to donor/general/ARC lists. Imported lazily
inside each route (not at module level) — app.py imports this module before
_admin_required() is defined further down in its own source, so a top-level
import here would be circular.
"""
import logging

from flask import Blueprint, jsonify, render_template_string, request

from core.database import get_connection
from jobs.campaigns.dispatch import approve_week

log = logging.getLogger(__name__)

campaigns_bp = Blueprint("campaigns", __name__)

_PAGE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ campaign.book_title }} — Week {{ week_number }}</title>
  <style>
    body { font-family: -apple-system, Arial, sans-serif; max-width: 800px; margin: 24px auto; padding: 0 16px; color: #222; }
    h1 { font-size: 20px; }
    .meta { color: #777; font-size: 13px; margin-bottom: 20px; }
    .row { border: 1px solid #ddd; border-radius: 8px; padding: 14px; margin-bottom: 14px; }
    .row-head { display: flex; justify-content: space-between; font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 8px; }
    .status { font-weight: 600; }
    .status-sent { color: #2e7d32; }
    .status-approved { color: #1565c0; }
    .status-edited { color: #ef6c00; }
    input[type=text] { width: 100%; padding: 6px 8px; margin-bottom: 8px; font-size: 14px; box-sizing: border-box; }
    textarea { width: 100%; min-height: 120px; padding: 8px; font-size: 14px; box-sizing: border-box; font-family: inherit; }
    .img-type { color: #888; font-size: 12px; margin-top: 6px; }
    button { padding: 8px 14px; border-radius: 6px; border: 1px solid #ccc; background: #f5f5f5; cursor: pointer; font-size: 13px; }
    .approve-all { background: #1565c0; color: white; border: none; padding: 10px 18px; font-size: 14px; margin-bottom: 20px; }
    .save-btn { margin-top: 8px; }
    .save-status { font-size: 12px; color: #2e7d32; margin-left: 8px; }
  </style>
</head>
<body>
  <h1>{{ campaign.book_title }} — Week {{ week_number }}</h1>
  <div class="meta">Launch {{ campaign.launch_date }} · campaign_id={{ campaign.campaign_id }}</div>

  <button class="approve-all" onclick="approveAll()">Approve All (Week {{ week_number }})</button>
  <div id="approve-result"></div>

  {% for row in rows %}
  <div class="row" data-id="{{ row.id }}">
    <div class="row-head">
      <span>{{ row.platform }} / {{ row.segment }} · {{ row.send_date }}</span>
      <span class="status status-{{ row.status }}">{{ row.status }}</span>
    </div>
    {% if row.subject is not none %}
    <input type="text" class="subject" value="{{ row.subject }}">
    {% endif %}
    <textarea class="body">{{ row.body_text }}</textarea>
    {% if row.image_template_type %}
    <div class="img-type">Image template (read-only): {{ row.image_template_type }}</div>
    {% endif %}
    <div>
      <button class="save-btn" onclick="saveRow({{ row.id }})">Save</button>
      <span class="save-status" id="save-status-{{ row.id }}"></span>
    </div>
  </div>
  {% endfor %}

  <script>
    async function saveRow(id) {
      const rowEl = document.querySelector('.row[data-id="' + id + '"]');
      const subjectEl = rowEl.querySelector('.subject');
      const body = rowEl.querySelector('.body').value;
      const subject = subjectEl ? subjectEl.value : null;
      const resp = await fetch('/api/campaigns/{{ campaign.campaign_id }}/week/{{ week_number }}/sends/' + id, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({subject: subject, body_text: body}),
      });
      const data = await resp.json();
      const statusEl = document.getElementById('save-status-' + id);
      statusEl.textContent = data.success ? ('Saved' + (data.status === 'edited' ? ' (marked edited)' : '')) : ('Error: ' + data.error);
    }
    async function approveAll() {
      const resp = await fetch('/api/campaigns/{{ campaign.campaign_id }}/week/{{ week_number }}/approve', {method: 'POST'});
      const data = await resp.json();
      document.getElementById('approve-result').textContent = data.success
        ? ('Approved ' + data.approved + ' — Facebook queued: ' + data.facebook_queued + ', Brevo sent now: ' + data.brevo_sent_now)
        : ('Error: ' + data.error);
      location.reload();
    }
  </script>
</body>
</html>
"""


@campaigns_bp.route("/campaigns/<campaign_id>/week/<int:week_number>")
def campaign_week_page(campaign_id, week_number):
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return redir

    conn = get_connection()
    try:
        campaign = conn.execute(
            "SELECT * FROM book_launch_campaigns WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        if not campaign:
            return f"Campaign {campaign_id!r} not found", 404

        rows = conn.execute(
            "SELECT * FROM book_launch_sends WHERE campaign_id=? AND week_number=? ORDER BY platform, id",
            (campaign_id, week_number),
        ).fetchall()

        return render_template_string(
            _PAGE_TEMPLATE,
            campaign=dict(campaign),
            week_number=week_number,
            rows=[dict(r) for r in rows],
        )
    finally:
        conn.close()


@campaigns_bp.route("/api/campaigns/<campaign_id>/week/<int:week_number>/sends/<int:send_id>", methods=["POST"])
def campaign_send_save(campaign_id, week_number, send_id):
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401

    data = request.get_json(force=True) or {}
    new_subject = data.get("subject")
    new_body = data.get("body_text", "")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM book_launch_sends WHERE id=? AND campaign_id=? AND week_number=?",
            (send_id, campaign_id, week_number),
        ).fetchone()
        if not row:
            return jsonify({"success": False, "error": "not found"}), 404

        changed = (new_body != (row["body_text"] or "")) or (new_subject != row["subject"])
        new_status = "edited" if changed else row["status"]

        conn.execute(
            "UPDATE book_launch_sends SET subject=?, body_text=?, status=? WHERE id=?",
            (new_subject, new_body, new_status, send_id),
        )
        conn.commit()
        return jsonify({"success": True, "status": new_status})
    except Exception as exc:
        log.error("campaign_send_save failed for id=%s: %s", send_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500
    finally:
        conn.close()


@campaigns_bp.route("/api/campaigns/<campaign_id>/week/<int:week_number>/approve", methods=["POST"])
def campaign_week_approve(campaign_id, week_number):
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401

    try:
        # This is the real, intentional-approval path (Bill clicked the
        # button) — dry_run=False is explicit and must stay explicit; don't
        # make it conditional or drop it back to the bare default here.
        result = approve_week(campaign_id, week_number, dry_run=False)
        return jsonify({"success": True, **result})
    except Exception as exc:
        log.error("campaign_week_approve failed for %s/%s: %s", campaign_id, week_number, exc)
        return jsonify({"success": False, "error": str(exc)}), 500
