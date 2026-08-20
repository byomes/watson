"""jobs/trading/routes.py — Flask Blueprint: the Trading dashboard page.

Mount on the Watson dashboard app:
    from jobs.trading.routes import trading_bp
    app.register_blueprint(trading_bp)

Auth: gated by jobs.dashboard.app._admin_required() — same session check as
/admin/*, the meet-review pages, and jobs/campaigns/campaign_routes.py.
Imported lazily inside each route (not at module level) — app.py imports
this module before _admin_required() is defined further down in its own
source, so a top-level import here would be circular (same reason
campaign_routes.py does it this way).

This page is "the why," not a competing source of truth — the real Alpaca
paper account/app remains ground truth for the account itself; a note on
the page says so explicitly.
"""
import json

from flask import Blueprint, jsonify, render_template_string

from jobs.trading.db import get_connection
from jobs.trading.risk import get_risk_state, resume_from_drawdown_stop

trading_bp = Blueprint("trading", __name__)

_PAGE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Trading — Watson</title>
  <style>
    body { font-family: -apple-system, Arial, sans-serif; max-width: 900px; margin: 24px auto; padding: 0 16px; color: #222; }
    h1 { font-size: 20px; }
    h2 { font-size: 15px; margin-top: 28px; color: #444; }
    .note { color: #777; font-size: 13px; margin-bottom: 20px; }
    .risk-active { color: #1a7a1a; font-weight: 600; }
    .risk-halt { color: #b8860b; font-weight: 600; }
    .risk-stop { color: #c0392b; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; }
    th { color: #666; font-weight: 600; text-transform: uppercase; font-size: 11px; }
    .pass { color: #1a7a1a; font-weight: 600; }
    .fail { color: #c0392b; font-weight: 600; }
    .rationale { color: #666; font-size: 12px; }
    button { font-size: 12px; padding: 4px 10px; border-radius: 6px; border: 1px solid #ccc; background: #f7f7f7; cursor: pointer; }
    .empty { color: #999; font-style: italic; padding: 12px 0; }
  </style>
</head>
<body>
  <h1>Trading</h1>
  <p class="note">Paper-trading strategy development pipeline (Alpaca paper account only —
    the Alpaca app/login remains ground truth for the account itself; this page shows
    the "why" behind it.)</p>

  <h2>Risk state</h2>
  {% if risk.status == 'active' %}
    <p class="risk-active">Active — no limits currently tripped.</p>
  {% elif risk.status == 'daily_halt' %}
    <p class="risk-halt">Daily loss halt — trading paused for today, clears automatically next day.</p>
  {% else %}
    <p class="risk-stop">Drawdown stop — {{ (risk.halted_at or '') }}. Requires manual resume below.</p>
    <button onclick="resumeDrawdown()">Resume from drawdown stop</button>
  {% endif %}
  <p class="note">Peak equity: {{ risk.peak_equity or 'n/a' }} | Day-start equity: {{ risk.day_start_equity or 'n/a' }}</p>

  <h2>Strategies &amp; holdout status</h2>
  {% if strategies %}
  <table>
    <tr><th>#</th><th>Family</th><th>Params</th><th>Status</th><th>Holdout</th><th></th></tr>
    {% for s in strategies %}
    <tr>
      <td>{{ s.id }}</td>
      <td>{{ s.family }}</td>
      <td>{{ s.params_json }}</td>
      <td>{{ s.status }}</td>
      <td>
        {% if s.holdout_overall_pass is not none %}
          <span class="{{ 'pass' if s.holdout_overall_pass else 'fail' }}">
            {{ 'PASSED' if s.holdout_overall_pass else 'FAILED' }} ({{ s.holdout_windows_beaten }}/3)
          </span>
        {% else %}
          not tested
        {% endif %}
      </td>
      <td>
        {% if s.status == 'training_tested' and s.holdout_overall_pass is none %}
          <button onclick="proposeHoldout({{ s.id }})">Propose holdout test</button>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p class="empty">No strategies proposed yet.</p>
  {% endif %}

  <h2>Backtest / trade log</h2>
  {% if runs %}
  <table>
    <tr><th>When</th><th>Strategy</th><th>Window</th><th>Return</th><th>vs SPY</th><th>Max DD</th><th>Sharpe</th><th>Win %</th></tr>
    {% for r in runs %}
    <tr>
      <td>{{ r.created_at }}</td>
      <td>{{ r.strategy_id or '(ad-hoc)' }}</td>
      <td>{{ r.window_label }}</td>
      <td>{{ r.return_pct }}%</td>
      <td>{{ r.benchmark_return_pct }}%</td>
      <td>{{ r.max_drawdown_pct }}%</td>
      <td>{{ r.sharpe }}</td>
      <td>{{ r.win_rate }}</td>
    </tr>
    {% if r.rationale %}
    <tr><td></td><td colspan="7" class="rationale">{{ r.rationale }}</td></tr>
    {% endif %}
    {% endfor %}
  </table>
  {% else %}
  <p class="empty">No backtest runs logged yet.</p>
  {% endif %}

  <script>
    async function resumeDrawdown() {
      const r = await fetch('/trading/api/resume-drawdown', { method: 'POST' });
      if (r.ok) location.reload(); else alert('Failed to resume.');
    }
    async function proposeHoldout(strategyId) {
      const r = await fetch('/trading/api/propose-holdout/' + strategyId, { method: 'POST' });
      const data = await r.json();
      alert(data.message || 'Holdout test proposed — check Telegram to approve.');
    }
  </script>
</body>
</html>
"""


@trading_bp.route("/trading")
def trading_page():
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return redir

    risk = get_risk_state()

    conn = get_connection()
    try:
        strategies = [dict(r) for r in conn.execute(
            """SELECT s.*, h.overall_pass AS holdout_overall_pass, h.windows_beaten AS holdout_windows_beaten
               FROM strategies s
               LEFT JOIN holdout_tests h ON h.strategy_id = s.id
               ORDER BY s.id DESC"""
        ).fetchall()]
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 50"
        ).fetchall()]
    finally:
        conn.close()

    return render_template_string(_PAGE_TEMPLATE, risk=risk, strategies=strategies, runs=runs)


@trading_bp.route("/trading/api/resume-drawdown", methods=["POST"])
def api_resume_drawdown():
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return jsonify({"error": "unauthorized"}), 401

    resume_from_drawdown_stop()
    return jsonify({"status": "active"})


@trading_bp.route("/trading/api/propose-holdout/<int:strategy_id>", methods=["POST"])
def api_propose_holdout(strategy_id):
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return jsonify({"error": "unauthorized"}), 401

    from jobs.trading.evaluate import propose_holdout_test
    message = propose_holdout_test(strategy_id)
    return jsonify({"message": message})
