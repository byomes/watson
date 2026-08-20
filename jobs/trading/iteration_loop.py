"""jobs/trading/iteration_loop.py — Watson proposes strategy variants,
backtests them against training data only, and logs each result with a
rationale. Two modes, both gated by an explicit Telegram YES:

  - Single-variant (propose_and_run_next): one approval per variant. Slower,
    maximally supervised.
  - Batch (run_batch / propose_batch / run_batch_and_report): one approval
    covers a whole batch of up to N variants, run back-to-back with no
    per-variant approval in between; results are filtered to those clearing
    WIN_RATE_THRESHOLD_PCT and reported back as a summary. Added because
    single-variant mode's per-item approval chaining (a fresh pending row
    created after every variant) turned out to have an unresolved bug in
    bot.py's overlapping routing layers — a single YES could cascade
    through an entire grid instead of stopping after one (see git history
    on jobs/gcal/pending.py's confirm_pending() for the partial fix that
    didn't fully explain it). Batch mode structurally avoids the failure
    mode: it never creates a new pending row mid-flight, so there's nothing
    for a stray routing-layer re-entry to latch onto.

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
import logging

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.trading.backtest import run_backtest
from jobs.trading.data import training_data
from jobs.trading.db import get_connection
from jobs.trading.strategies.templates import TEMPLATES

log = logging.getLogger(__name__)


def _send_telegram(text: str, chat_id: int | None = None) -> None:
    """Only needed to kick off a family's very first variant — every
    subsequent message is a reply sent by bot.py itself after a YES. Same
    plain requests.post pattern used elsewhere in Watson (e.g.
    jobs/kb/sync_and_index.py's _send_telegram)."""
    if not TELEGRAM_BOT_TOKEN or not (chat_id or TELEGRAM_CHAT_ID):
        log.warning("Telegram not configured — cannot send: %s", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id or TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


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


WIN_RATE_THRESHOLD_PCT = 80.0


def run_batch(n: int, families: list[str] | None = None) -> list[dict]:
    """Run up to n variants back-to-back across `families` (default: all
    three templates), round-robin so one family's grid doesn't consume the
    whole budget before the others get a turn. No per-variant approval —
    this whole batch is triggered by a single upstream approval (see
    propose_batch/run_batch_and_report). Stops early if every family's grid
    is exhausted before reaching n. Returns one dict per variant run,
    combining the proposed variant and its backtest metrics."""
    families = list(families or TEMPLATES)
    results = []
    exhausted = set()
    idx = 0
    while len(results) < n and len(exhausted) < len(families):
        family = families[idx % len(families)]
        idx += 1
        if family in exhausted:
            continue
        variant = propose_next_variant(family)
        if variant is None:
            exhausted.add(family)
            continue
        metrics = run_variant(variant["strategy_id"])
        results.append({**variant, **metrics})
    return results


def _format_batch_report(results: list[dict]) -> str:
    winners = [r for r in results if (r.get("win_rate") or 0) > WIN_RATE_THRESHOLD_PCT]
    lines = [f"Batch complete: {len(results)} variants run across all families."]
    if winners:
        winners.sort(key=lambda r: -r["win_rate"])
        lines.append(f"{len(winners)} cleared >{WIN_RATE_THRESHOLD_PCT:.0f}% win rate:")
        for w in winners[:15]:
            label = TEMPLATES[w["family"]]["label"]
            lines.append(
                f"  #{w['strategy_id']} {label} {w['params']} — win rate {w['win_rate']}%, "
                f"return {w['return_pct']}% (SPY {w['benchmark_return_pct']}%)"
            )
        if len(winners) > 15:
            lines.append(f"  ...and {len(winners) - 15} more — see the Trading dashboard page for the full list.")
    else:
        lines.append(f"None cleared the >{WIN_RATE_THRESHOLD_PCT:.0f}% win-rate bar.")
    return "\n".join(lines)


def run_batch_and_report(n: int) -> str:
    """The actual work run inside the trading_batch_approve YES handler
    (bot.py) — a single call, no per-item pending_actions cycling, which is
    exactly why this mode structurally can't hit the duplicate-advance bug
    single-variant mode did (that bug depended on a fresh pending row being
    created mid-flight for a stray routing layer to latch onto; batch mode
    never creates one)."""
    results = run_batch(n)
    return _format_batch_report(results)


def propose_batch(n: int, chat_id: int | None = None) -> str:
    """Save a single pending approval covering the whole batch and return
    the message to send. Mirrors propose_and_run_next()'s single-pending-
    per-chat guard."""
    import jobs.gcal.pending as pending_module

    chat_id = chat_id or TELEGRAM_CHAT_ID

    existing = pending_module.get_pending(chat_id)
    if existing:
        return (
            f"A previous action (#{existing['id']}, {existing['action_type']}) is still "
            f"awaiting your YES/NO — reply to that first."
        )

    pending_module.save_pending(chat_id, "trading_batch_approve", {"n": n}, None)
    return (
        f"Ready to run {n} strategy variants across all templates (no per-variant "
        f"approval — this one YES covers the whole batch). Results filtered to "
        f">{WIN_RATE_THRESHOLD_PCT:.0f}% win rate will be reported back.\n\n"
        f"Reply YES to run it, or NO to cancel."
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--family", choices=list(TEMPLATES))
    group.add_argument("--batch", type=int, metavar="N")
    args = parser.parse_args()

    if args.family:
        message = propose_and_run_next(args.family)
    else:
        message = propose_batch(args.batch)
    print(message)
    _send_telegram(message)
