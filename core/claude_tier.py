"""Budget-capped Claude API tier — opt-in helper for jobs whose highest-value,
lowest-frequency generation tasks currently run on local Ollama (qwen2.5:7b)
only because it's the best *local* option, not because local is the right
tool.

Callers try call_claude() first; a None return (budget exhausted, no key, or
any SDK error) means "fall back to your existing Ollama call, unchanged."
This module never raises into a caller and never blocks a job from running.

Uses WATSON_CLAUDE_BUDGET_KEY, NOT ANTHROPIC_API_KEY — several other dormant
Watson features (jobs/dev/command_executor.py, jobs/dev/claude_debug.py,
jobs/dev/build_pipeline.py, jobs/dev/claude_api_final_review.py,
jobs/code_agent/agent.py, jobs/dashboard/app.py, jobs/skillbuilder/build.py)
already read ANTHROPIC_API_KEY and would silently reactivate — with zero
budget awareness — the moment that var is set. Setting WATSON_CLAUDE_BUDGET_KEY
instead activates ONLY this tier.
"""
import logging
import os
from datetime import datetime, timezone

from core.database import get_connection
from core.vacation import vacation_gate

log = logging.getLogger(__name__)

_ENV_KEY = "WATSON_CLAUDE_BUDGET_KEY"
_ENV_BUDGET = "CLAUDE_MONTHLY_BUDGET_USD"
_DEFAULT_BUDGET_USD = 10.00
_DEFAULT_MODEL = "claude-sonnet-5"

# $ per 1M tokens (input, output) — only the models this tier is expected to use.
_PRICING = {
    "claude-opus-5":    (5.00, 25.00),
    "claude-sonnet-5":  (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claude_tier_spend_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name       TEXT NOT NULL,
                model          TEXT NOT NULL,
                input_tokens   INTEGER NOT NULL,
                output_tokens  INTEGER NOT NULL,
                cost_usd       REAL NOT NULL,
                created_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claude_tier_budget_alerts (
                month        TEXT PRIMARY KEY,   -- 'YYYY-MM'
                alerted_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


_bootstrap()


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _month_spend_usd(month: str) -> float:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM claude_tier_spend_log "
            "WHERE strftime('%Y-%m', created_at) = ?",
            (month,),
        ).fetchone()
    return float(row["total"])


def _budget_usd() -> float:
    try:
        return float(os.getenv(_ENV_BUDGET, str(_DEFAULT_BUDGET_USD)))
    except ValueError:
        return _DEFAULT_BUDGET_USD


def _log_spend(job_name: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO claude_tier_spend_log "
            "(job_name, model, input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?, ?)",
            (job_name, model, input_tokens, output_tokens, cost_usd),
        )


def _maybe_send_exhausted_alert(month: str, spend: float, budget: float) -> None:
    """Sends the 'falling back to Ollama for the rest of the month' Telegram
    alert exactly once per calendar month, on the call that first crosses the
    budget — not on every subsequent call."""
    with get_connection() as conn:
        already = conn.execute(
            "SELECT 1 FROM claude_tier_budget_alerts WHERE month = ?", (month,)
        ).fetchone()
        if already:
            return
        conn.execute(
            "INSERT INTO claude_tier_budget_alerts (month) VALUES (?)", (month,)
        )
    text = (
        f"⚠️ Claude API tier: ${spend:.2f} spent this month "
        f"(budget ${budget:.2f}) — falling back to local Ollama for all "
        f"jobs until next month."
    )
    if vacation_gate("normal", "core.claude_tier", text):
        return
    import requests as _rq
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def call_claude(
    system: str,
    user: str,
    job_name: str,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 2048,
) -> str | None:
    """Try a budget-tracked Claude API call. Returns the response text on
    success, or None if the tier is inactive/exhausted/erroring — callers
    treat None as "fall back to your existing local Ollama call, unchanged."

    Never raises. `system` may be "" for jobs whose existing Ollama call
    flattens system+user into one prompt string with no native split.
    """
    api_key = os.getenv(_ENV_KEY)
    if not api_key:
        return None

    month = _current_month()
    budget = _budget_usd()
    spend = _month_spend_usd(month)
    if spend >= budget:
        _maybe_send_exhausted_alert(month, spend, budget)
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        kwargs = {}
        if system:
            kwargs["system"] = system
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
            **kwargs,
        )
    except Exception as exc:
        log.warning("claude_tier: call failed for job=%s model=%s: %s", job_name, model, exc)
        return None

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        return None

    price_in, price_out = _PRICING.get(model, _PRICING[_DEFAULT_MODEL])
    in_tok = response.usage.input_tokens
    out_tok = response.usage.output_tokens
    cost = (in_tok * price_in + out_tok * price_out) / 1_000_000

    try:
        _log_spend(job_name, model, in_tok, out_tok, cost)
    except Exception as exc:
        log.warning("claude_tier: failed to log spend for job=%s: %s", job_name, exc)

    new_total = spend + cost
    if new_total >= budget:
        _maybe_send_exhausted_alert(month, new_total, budget)

    return text
