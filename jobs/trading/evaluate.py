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
import logging

from config.settings import TELEGRAM_CHAT_ID
from jobs.trading.backtest import run_backtest
from jobs.trading.data import holdout_data
from jobs.trading.db import get_connection
from jobs.trading.holdout import HOLDOUT_WINDOWS
from jobs.trading.strategies.templates import TEMPLATES

log = logging.getLogger(__name__)


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


def strategies_above_win_rate(threshold_pct: float = 80.0) -> list[int]:
    """Strategy IDs whose training-data win_rate cleared threshold_pct,
    ranked best-first by win_rate. Used to select candidates for
    run_holdout_batch(). Structurally biased toward low-activity/mean-
    reversion-style strategies — a trend-following strategy that loses
    small and often but wins big rarely can be genuinely profitable with a
    win_rate well under 50%. See strategies_above_sharpe() for a selection
    criterion that doesn't have this bias."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT s.id FROM strategies s JOIN backtest_runs b ON b.strategy_id = s.id
               WHERE b.window_label = 'training' AND b.win_rate > ?
               ORDER BY b.win_rate DESC""",
            (threshold_pct,),
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def strategies_above_sharpe(threshold: float = 0.5, family: str | None = None) -> list[int]:
    """Strategy IDs whose training-data Sharpe ratio cleared threshold,
    ranked best-first by Sharpe. Unlike strategies_above_win_rate(), this
    doesn't structurally favor low-activity strategies — appropriate for
    trend-following templates (e.g. time_series_momentum) where a real,
    profitable strategy can have a win_rate well under 50%. Optionally
    restrict to one family."""
    conn = get_connection()
    try:
        query = """SELECT s.id FROM strategies s JOIN backtest_runs b ON b.strategy_id = s.id
                   WHERE b.window_label = 'training' AND b.sharpe > ?"""
        params = [threshold]
        if family:
            query += " AND s.family = ?"
            params.append(family)
        query += " ORDER BY b.sharpe DESC"
        rows = conn.execute(query, params).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def run_holdout_batch(strategy_ids: list[int]) -> list[dict]:
    """Run the one-shot sealed holdout test for each id in strategy_ids.
    Already-tested ids are skipped (not an error — keeps this safely
    re-runnable over a list that might include a mix of new and
    previously-tested strategies). Each individual strategy is still only
    ever holdout-tested once, same guarantee as run_holdout_test().

    Each strategy's test is independently wrapped — one bad combo (e.g. a
    backtest engine error) is recorded and skipped rather than aborting the
    rest of the batch and losing every result computed so far. Confirmed
    live this matters: a large-period ma_crossover combo against the
    62-bar crash_2020 window crashed backtrader mid-batch before this
    (fixed separately, backtest.py's runonce=False) and the whole batch's
    report was lost even though 42 of 62 strategies had already been
    correctly, permanently sealed."""
    results = []
    for sid in strategy_ids:
        if already_holdout_tested(sid):
            continue
        try:
            results.append(run_holdout_test(sid))
        except Exception as exc:
            log.error("Holdout test failed for strategy #%d, skipping: %s", sid, exc)
    return results


def format_holdout_batch_report(results: list[dict]) -> str:
    """Rank strategies by holdout performance: passed first, then by how
    many windows beaten, then by average return across the 3 windows as a
    tiebreak. NOTE: ranking many candidates by holdout result and reporting
    the "best" is weaker evidence of real edge than a single pre-registered
    candidate passing would be — running N candidates through a fixed
    holdout set and picking the top is a mild form of the same multiple-
    comparisons/data-snooping bias the ingested KB material warns about for
    training-data overfitting, just applied to the holdout set instead.
    Each strategy is still only tested once (the seal itself isn't
    violated), but "best of N" should be read with that caveat in mind."""
    def _avg_return(r):
        return sum(m["return_pct"] for m in r["window_results"].values()) / len(r["window_results"])

    ranked = sorted(
        results,
        key=lambda r: (r["overall_pass"], r["windows_beaten"], _avg_return(r)),
        reverse=True,
    )

    passed = [r for r in ranked if r["overall_pass"]]
    lines = [
        f"Holdout batch complete: {len(results)} strategies tested, {len(passed)} PASSED.",
        "",
        f"Caveat: this ranks {len(results)} candidates against the same sealed data — the "
        f"top result is weaker evidence of real edge than a single pre-registered test "
        f"would be (multiple-comparisons bias, same class QuantStart's backtesting-"
        f"pitfalls KB article warns about for training data).",
        "",
        "Ranked (best first):",
    ]
    for r in ranked[:20]:
        strategy = _get_strategy(r["strategy_id"])
        label = TEMPLATES[strategy["family"]]["label"]
        verdict = "PASS" if r["overall_pass"] else "fail"
        lines.append(
            f"  #{r['strategy_id']} [{verdict}] {label} {strategy['params_json']} — "
            f"beat {r['windows_beaten']}/3, avg return {_avg_return(r):.3f}%, "
            f"outright loss: {r['any_outright_loss']}"
        )
    if len(ranked) > 20:
        lines.append(f"  ...and {len(ranked) - 20} more — see trading.db for the full list.")
    return "\n".join(lines)


def propose_holdout_batch(strategy_ids: list[int], chat_id: int | None = None) -> str:
    """Single approval covering the whole holdout batch — mirrors
    iteration_loop.propose_batch()'s one-approval-per-batch design, for the
    same reason (no per-item pending row to cascade off)."""
    import jobs.gcal.pending as pending_module

    untested = [sid for sid in strategy_ids if not already_holdout_tested(sid)]
    if not untested:
        return "All of those strategies have already been holdout-tested."

    chat_id = chat_id or TELEGRAM_CHAT_ID
    existing = pending_module.get_pending(chat_id)
    if existing:
        return (
            f"A previous action (#{existing['id']}, {existing['action_type']}) is still "
            f"awaiting your YES/NO — reply to that first."
        )

    pending_module.save_pending(
        chat_id, "trading_holdout_batch_approve", {"strategy_ids": untested}, None,
    )
    return (
        f"Ready to run the sealed holdout test against {len(untested)} strategies "
        f"(one shot each, permanent either way) and produce one ranked report.\n\n"
        f"Reply YES to run it, or NO to hold off."
    )


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
