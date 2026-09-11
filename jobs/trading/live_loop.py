"""jobs/trading/live_loop.py — Daily paper-trading live-forward loop.

The first job in this pipeline that places a REAL order (paper account
only — alpaca_client.py's hard paper-only guards still apply, unchanged)
based on a strategy's live signal, rather than a historical backtest.
Everything before this (backtest.py, evaluate.py, iteration_loop.py) only
ever simulates; this is what proves the mechanics actually work: can
Watson pull fresh data, evaluate a signal, size an order off real account
equity, respect the risk halts, and place/close a real (paper) position —
reliably, day after day, unattended.

Explicitly NOT trying to prove the strategy has edge here — donchian_20_10
(the classic Turtle default, chosen specifically because it's the least
grid-tuned, most principled candidate available, not because it's the
best backtested performer) has NOT cleared evaluate.py's "beat buy-and-
hold outright" bar, same as everything else tested this session. This
loop's job is to prove the PIPE works; whether the strategy is any good
is a separate, still-open question.

Risk wiring: this is the first caller of jobs/trading/risk.py's functions
anywhere in the codebase (previously read-only, for the dashboard display
— confirmed via grep 2026-09-11, nothing ever called check_drawdown/
check_daily_loss/update_equity). update_equity() runs every call, before
any trading decision — if it sets risk_state.status to daily_halt or
drawdown_stop, this loop still evaluates EXIT signals (closing an existing
position is risk-reducing, never suppressed) but refuses any new ENTRY.
drawdown_stop only clears via the dashboard's manual resume, same as
already designed.

Sizing: 95% of current account equity, matching backtest.py's convention
— NOT risk.py's MAX_POSITION_PCT (2%), which is designed for a future
multi-symbol portfolio where positions are diversified across many names.
Applied to this single-symbol test, a 2% cap would leave ~98% of the
account in cash at all times, making the test almost meaningless as a
check of whether the signal's timing does anything. Flagged explicitly,
not silently decided — revisit if/when this pipeline ever trades more
than one symbol at once.

Usage: PYTHONPATH=<repo> venv/bin/python -m jobs.trading.live_loop
"""
import json
import logging

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.trading.alpaca_client import get_trading_client
from jobs.trading.data_pull import pull_daily_bars
from jobs.trading.db import get_connection
from jobs.trading.schema import create_tables
from jobs.trading import risk

log = logging.getLogger(__name__)

SYMBOL = "SPY"
FAMILY = "donchian_breakout"
PARAMS = {"entry_period": 20, "exit_period": 10}
POSITION_PCT = 0.95


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — cannot send: %s", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


def _donchian_signal(entry_period: int, exit_period: int) -> dict:
    """Same rule as strategies/templates.py's DonchianBreakoutStrategy,
    evaluated imperatively against the latest daily_bars row instead of
    replayed through backtrader — this is a live, single-decision-per-day
    evaluation, not a historical batch. upper/lower are computed over the
    N days BEFORE the latest bar (excluded), matching the backtest's
    bt.ind.Highest(self.data.high(-1), ...) shift."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT date, high, low, close FROM daily_bars WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (SYMBOL, max(entry_period, exit_period) + 1),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < max(entry_period, exit_period) + 1:
        raise RuntimeError(f"Not enough daily_bars history for {SYMBOL} — pull more data first.")

    rows = list(reversed(rows))  # ascending
    latest = rows[-1]
    prior = rows[:-1]
    upper = max(r["high"] for r in prior[-entry_period:])
    lower = min(r["low"] for r in prior[-exit_period:])
    return {
        "bar_date": latest["date"],
        "close": latest["close"],
        "upper": upper,
        "lower": lower,
        "breakout_long": latest["close"] > upper,
        "breakdown": latest["close"] < lower,
    }


def _log_decision(bar_date, signal, action_taken, reason, equity_before, equity_after,
                   order_id, risk_status, error=None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO live_decisions
               (symbol, family, params_json, bar_date, signal, action_taken, reason,
                equity_before, equity_after, order_id, risk_status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (SYMBOL, FAMILY, json.dumps(PARAMS), bar_date, signal, action_taken, reason,
             equity_before, equity_after, order_id, risk_status, error),
        )
        conn.commit()
    finally:
        conn.close()


def run_once() -> dict:
    """One live-loop cycle: refresh data, evaluate the signal, respect
    risk state, place/close a paper order if warranted, log and notify.
    Safe to call repeatedly (idempotent w.r.t. position state — checks the
    REAL current position before deciding, not a locally-cached guess)."""
    create_tables()
    pull_daily_bars(symbol=SYMBOL, years=1)  # cheap incremental refresh, not the full 10yr history

    client = get_trading_client()
    account = client.get_account()
    equity = float(account.equity)

    risk.start_new_day(equity)
    risk_state = risk.update_equity(equity)
    risk_status = risk_state["status"]

    try:
        sig = _donchian_signal(PARAMS["entry_period"], PARAMS["exit_period"])
    except Exception as exc:
        log.error("Signal evaluation failed: %s", exc)
        _log_decision(None, "error", "none", str(exc), equity, equity, None, risk_status, error=str(exc))
        _send_telegram(f"Live loop error evaluating signal: {exc} - Watson")
        raise

    try:
        current_position_qty = float(client.get_open_position(SYMBOL).qty)
    except Exception:
        current_position_qty = 0.0
    has_position = current_position_qty > 0

    if has_position and sig["breakdown"]:
        signal, action = "exit", "sell"
    elif not has_position and sig["breakout_long"] and risk_status == "active":
        signal, action = "enter", "buy"
    elif not has_position and sig["breakout_long"] and risk_status != "active":
        signal, action = "enter", "none"
    else:
        signal, action = "hold", "none"

    order_id, error, reason = None, None, None
    if action == "buy":
        notional = round(equity * POSITION_PCT, 2)
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            order = client.submit_order(MarketOrderRequest(
                symbol=SYMBOL, notional=notional, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            ))
            order_id = str(order.id)
            reason = f"breakout above {sig['upper']:.2f} (close {sig['close']:.2f}), notional=${notional}"
        except Exception as exc:
            error = str(exc)
            log.error("Buy order failed: %s", exc)
    elif action == "sell":
        try:
            order = client.close_position(SYMBOL)
            order_id = str(order.id)
            reason = f"breakdown below {sig['lower']:.2f} (close {sig['close']:.2f})"
        except Exception as exc:
            error = str(exc)
            log.error("Close order failed: %s", exc)
    elif signal == "enter" and action == "none":
        reason = f"signal fired (breakout above {sig['upper']:.2f}) but risk_status={risk_status} — entry suppressed"
    else:
        reason = "no signal change"

    equity_after = float(client.get_account().equity)
    _log_decision(sig["bar_date"], signal, action, reason, equity, equity_after, order_id, risk_status, error)

    msg = (
        f"Live paper loop — {sig['bar_date']} close ${sig['close']:.2f}\n"
        f"Signal: {signal} | Action: {action} | Risk: {risk_status}\n"
        f"{reason}\n"
        f"Equity: ${equity:.2f} -> ${equity_after:.2f}"
    )
    if error:
        msg += f"\nERROR: {error}"
    _send_telegram(msg + "\n- Watson")

    return {
        "bar_date": sig["bar_date"], "signal": signal, "action": action, "reason": reason,
        "equity_before": equity, "equity_after": equity_after, "order_id": order_id,
        "risk_status": risk_status, "error": error,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_once()
    print(result)
