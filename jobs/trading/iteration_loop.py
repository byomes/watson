"""jobs/trading/iteration_loop.py — Watson proposes a strategy variant,
backtests it against training data only, logs the result with a rationale,
and requires an explicit Telegram YES before the next variant runs.

"Proposes a variant" is deliberately NOT an LLM generating strategy code —
Watson's automated jobs never call an LLM to produce code that then gets
executed (that's a real code-execution safety surface against a brokerage
account, paper or not, and Watson's automated inference is Ollama-only by
standing architecture rule anyway). Instead this is a deterministic walk
across a fixed parameter grid on one of the three vetted templates in
jobs/trading/strategies/templates.py — only that pre-written, reviewed
logic ever executes, just with different parameters each time.

Reuses the exact same Telegram pending-action approval mechanism used
elsewhere in Watson (jobs/gcal/pending.py — save_pending/get_pending/
confirm_pending/cancel_pending), action_type="trading_variant_approve".
The dispatch/execution side lives in bot.py's _execute_pending(), mirroring
the existing block_time/task_done handlers.
"""
import json

from config.settings import TELEGRAM_CHAT_ID
from jobs.trading.backtest import run_backtest
from jobs.trading.data import training_data
from jobs.trading.db import get_connection
from jobs.trading.strategies.templates import TEMPLATES


def _variant_count(family: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM strategies WHERE family = ?", (family,)).fetchone()
        return row["n"]
    finally:
        conn.close()


def _build_rationale(family: str, params: dict, prior_params: dict | None) -> str:
    label = TEMPLATES[family]["label"]
    if prior_params is None:
        return f"{label}: first variant, params={params}."
    diffs = [f"{k} {prior_params.get(k)}→{v}" for k, v in params.items() if prior_params.get(k) != v]
    diff_str = ", ".join(diffs) if diffs else "same params re-tested"
    return f"{label}: {diff_str}."


def propose_next_variant(family: str) -> dict | None:
    """Deterministically pick the next untried grid point for `family` and
    insert a new strategies row for it. Returns None once the grid is
    exhausted for that family."""
    if family not in TEMPLATES:
        raise ValueError(f"Unknown strategy family: {family!r}. Valid: {list(TEMPLATES)}")

    grid = TEMPLATES[family]["grid"]
    count = _variant_count(family)
    if count >= len(grid):
        return None

    params = grid[count]
    prior_params = grid[count - 1] if count > 0 else None
    rationale = _build_rationale(family, params, prior_params)

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO strategies (family, params_json, rationale, status) VALUES (?, ?, ?, 'proposed')",
            (family, json.dumps(params), rationale),
        )
        conn.commit()
        strategy_id = cur.lastrowid
    finally:
        conn.close()

    return {"strategy_id": strategy_id, "family": family, "params": params, "rationale": rationale}


def run_variant(strategy_id: int) -> dict:
    """Backtest a proposed strategy variant against training_data() only —
    never holdout_data(). Updates the strategy's status and returns metrics."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        if not row:
            raise ValueError(f"No strategy with id={strategy_id}")
        family, params, rationale = row["family"], json.loads(row["params_json"]), row["rationale"]
    finally:
        conn.close()

    metrics = run_backtest(
        TEMPLATES[family]["cls"], params, training_data(),
        window_label="training", strategy_id=strategy_id, rationale=rationale,
    )

    conn = get_connection()
    try:
        conn.execute("UPDATE strategies SET status = 'training_tested' WHERE id = ?", (strategy_id,))
        conn.commit()
    finally:
        conn.close()

    return metrics


def _format_result_message(variant: dict, metrics: dict) -> str:
    beat = "beat" if metrics["return_pct"] > metrics["benchmark_return_pct"] else "trailed"
    return (
        f"Trading — {TEMPLATES[variant['family']]['label']} variant #{variant['strategy_id']}\n"
        f"Rationale: {variant['rationale']}\n"
        f"Training-data return: {metrics['return_pct']}% ({beat} SPY buy-and-hold "
        f"{metrics['benchmark_return_pct']}%)\n"
        f"Max drawdown: {metrics['max_drawdown_pct']}% | Sharpe: {metrics['sharpe']} | "
        f"Win rate: {metrics['win_rate']}%"
    )


def propose_and_run_next(family: str, chat_id: int | None = None) -> str:
    """Propose the next untried variant for `family`, backtest it against
    training data, log it, and save a new Telegram approval request for it.
    This is the single entry point for both starting a family's iteration
    and continuing it after a YES — deterministic grid indexing means both
    cases are identical calls."""
    import jobs.gcal.pending as pending_module  # local import: avoids a
    # hard dependency on the bot's pending module for callers that only
    # want propose_next_variant()/run_variant() (e.g. tests, dashboard).
    # This is the same pending_module bot.py aliases (jobs/gcal/pending.py,
    # the pending_actions table) — not jobs/telegram/pending.py, a
    # different, reply-threading-only module despite the similar name.

    chat_id = chat_id or TELEGRAM_CHAT_ID

    existing = pending_module.get_pending(chat_id)
    if existing:
        return (
            f"A previous action (#{existing['id']}, {existing['action_type']}) is still "
            f"awaiting your YES/NO — reply to that first before the next variant runs."
        )

    variant = propose_next_variant(family)
    if variant is None:
        n = _variant_count(family)
        return (
            f"{TEMPLATES[family]['label']}: grid exhausted, {n} variants tested. "
            f"Review results on the Trading dashboard page and propose a holdout test "
            f"for whichever variant looks strongest."
        )

    metrics = run_variant(variant["strategy_id"])
    message = _format_result_message(variant, metrics)

    pending_module.save_pending(
        chat_id, "trading_variant_approve",
        {"strategy_id": variant["strategy_id"], "family": family}, None,
    )
    return message + "\n\nReply YES to run the next variant, or NO to stop this family here."


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--family", required=True, choices=list(TEMPLATES))
    args = parser.parse_args()
    print(propose_and_run_next(args.family))
