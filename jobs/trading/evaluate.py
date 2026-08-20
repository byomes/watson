"""jobs/trading/evaluate.py — Sealed holdout evaluation.

A strategy can be holdout-tested at most ONCE, ever — holdout_tests.strategy_id
is UNIQUE (see schema.py), so repeatedly peeking at the sealed windows to
cherry-pick a passing run is blocked structurally, not just by convention.
Running the holdout test is its own explicit, separately-approved action
(action_type="trading_holdout_test_approve"), never automatic and never
chained onto training-loop approval. A strategy that fails holdout stays
failed — only a genuinely new strategy variant (new strategy_id) gets a new
holdout attempt.

Pass bar: beats SPY buy-and-hold on >= 2 of the 3 sealed windows, AND no
window shows an outright loss.
"""
import json

from config.settings import TELEGRAM_CHAT_ID
from jobs.trading.backtest import run_backtest
from jobs.trading.data import holdout_data
from jobs.trading.db import get_connection
from jobs.trading.holdout import HOLDOUT_WINDOWS
from jobs.trading.strategies.templates import TEMPLATES


def _get_strategy(strategy_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        if not row:
            raise ValueError(f"No strategy with id={strategy_id}")
        return dict(row)
    finally:
        conn.close()


def already_holdout_tested(strategy_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM holdout_tests WHERE strategy_id = ?", (strategy_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def propose_holdout_test(strategy_id: int, chat_id: int | None = None) -> str:
    """Explicit, human-triggered action (dashboard button — see routes.py) —
    never called automatically by the training loop. Saves a Telegram
    approval request and returns the message to send."""
    import jobs.gcal.pending as pending_module

    if already_holdout_tested(strategy_id):
        return f"Strategy #{strategy_id} has already been holdout-tested — the seal only opens once."

    strategy = _get_strategy(strategy_id)
    chat_id = chat_id or TELEGRAM_CHAT_ID
    pending_module.save_pending(
        chat_id, "trading_holdout_test_approve", {"strategy_id": strategy_id}, None,
    )
    label = TEMPLATES[strategy["family"]]["label"]
    return (
        f"Ready to run strategy #{strategy_id} ({label}, params={strategy['params_json']}) "
        f"against the 3 sealed holdout windows. This can only happen once for this strategy — "
        f"the result is permanent either way.\n\nReply YES to run it, or NO to hold off."
    )


def run_holdout_test(strategy_id: int) -> dict:
    """The actual one-shot sealed evaluation. Raises if already tested."""
    if already_holdout_tested(strategy_id):
        raise ValueError(f"Strategy #{strategy_id} already holdout-tested — cannot re-test.")

    strategy = _get_strategy(strategy_id)
    family, params = strategy["family"], json.loads(strategy["params_json"])
    cls = TEMPLATES[family]["cls"]

    window_results = {}
    for window_name in HOLDOUT_WINDOWS:
        metrics = run_backtest(
            cls, params, holdout_data(window_name),
            window_label=window_name, strategy_id=strategy_id,
        )
        window_results[window_name] = metrics

    windows_beaten = sum(
        1 for m in window_results.values() if m["return_pct"] > m["benchmark_return_pct"]
    )
    any_outright_loss = any(m["return_pct"] < 0 for m in window_results.values())
    overall_pass = windows_beaten >= 2 and not any_outright_loss

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO holdout_tests
               (strategy_id, window_results_json, windows_beaten, any_outright_loss, overall_pass)
               VALUES (?, ?, ?, ?, ?)""",
            (strategy_id, json.dumps(window_results), windows_beaten, int(any_outright_loss), int(overall_pass)),
        )
        conn.execute(
            "UPDATE strategies SET status = ? WHERE id = ?",
            ("passed" if overall_pass else "failed", strategy_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "strategy_id": strategy_id,
        "window_results": window_results,
        "windows_beaten": windows_beaten,
        "any_outright_loss": any_outright_loss,
        "overall_pass": overall_pass,
    }


def format_holdout_result(result: dict) -> str:
    lines = [f"Holdout evaluation — strategy #{result['strategy_id']}"]
    for name, m in result["window_results"].items():
        beat = "beat" if m["return_pct"] > m["benchmark_return_pct"] else "trailed"
        lines.append(f"  {name}: {m['return_pct']}% ({beat} SPY {m['benchmark_return_pct']}%)")
    verdict = "PASSED" if result["overall_pass"] else "FAILED"
    lines.append(
        f"{verdict} — beat buy-and-hold on {result['windows_beaten']}/3 windows, "
        f"outright loss: {result['any_outright_loss']}"
    )
    if result["overall_pass"]:
        lines.append("Eligible for live paper-account forward testing (manual next step).")
    return "\n".join(lines)
