"""jobs/trading/intraday_backtest.py — Day-trading backtest engine.

Runs one independent backtrader cerebro per trading SESSION — never spans
multiple days in one cerebro run. This is what actually guarantees "day
trading" (zero overnight position carry): it's a structural property of
how the data is fed (one session's bars, nothing else), not a "check the
clock and hope the strategy closes in time" rule. A strategy that still
leaves a position open at a session's last bar is a strategy bug, and
run_intraday_backtest() raises rather than silently accepting it — same
fail-loud principle as backtest.py's rejected_orders tracking.

Starting cash for each session is the previous session's ENDING equity, so
a multi-session run still compounds into one realistic equity curve even
though each session is an independent cerebro run.

Cost model: same starting assumption as backtest.py (commission $0, 5bps
slippage) — not re-validated for intraday's much higher trade frequency,
where real bid-ask spread and market-impact cost matter more per round
trip. Worth revisiting once a specific intraday strategy looks promising
enough to matter.

No training/holdout split, no sealed evaluation, no strategies-table
tracking exists yet for this engine (unlike backtest.py/evaluate.py's
daily-pipeline equivalent) — this is infrastructure-validation scope, not
the full pipeline. That's a deliberate, separate decision for later.
"""
import logging

import backtrader as bt
import pandas as pd

from jobs.trading.intraday_data import session_bars

log = logging.getLogger(__name__)

SLIPPAGE_PCT = 0.0005
STARTING_CASH = 100_000.0


def run_intraday_backtest(strategy_cls, params: dict, session_dates: list[str],
                           symbol: str = "SPY", starting_cash: float = STARTING_CASH) -> dict:
    """Run strategy_cls across each date in session_dates, one cerebro per
    session, cash compounding session-to-session. Returns per-session
    results plus an aggregate summary."""
    equity = starting_cash
    session_results = []

    for d in session_dates:
        df = session_bars(d, symbol)
        if len(df) < 10:
            log.warning("Skipping %s: only %d bars (early close / data gap)", d, len(df))
            continue

        # A flatten-by-close call() submitted while processing a session's
        # LITERAL LAST bar never gets a chance to execute: backtrader
        # matches/fills a pending order during the broker-processing cycle
        # BETWEEN next() calls, and there is no such cycle after the final
        # bar. This bit on 2022-11-25 (the Thanksgiving-Friday early close
        # — real trading thins out well before the fixed 16:00 ET session
        # boundary this pipeline uses, see intraday_data.py's documented
        # simplification, so the flatten trigger fired on what turned out
        # to be the last bar actually fed). Fix: append one synthetic,
        # zero-volume, flat-price trailing bar so there's always one more
        # processing cycle for a same-day flatten order to fill against —
        # at that bar's OPEN, under backtrader's normal (non-cheating)
        # next-bar-open fill semantics, same convention backtest.py's
        # daily pipeline uses. Confirmed live 2026-09-11: an earlier
        # version of this fix used cerebro.broker.set_coc(True)
        # (cheat-on-close) instead, which "worked" but silently made
        # EVERY fill in this engine happen at the signal bar's own close
        # rather than the next bar's open — a broker-level setting, not
        # something scopable to just the flatten order — quietly more
        # optimistic than the daily pipeline's convention for every entry
        # and exit, not just the end-of-day flatten it was meant to fix.
        # The trailing bar alone (verified via a minimal repro) fully
        # solves the original problem without that side effect.
        last_ts = df.index[-1]
        last_close = df["close"].iloc[-1]
        trailing = df.iloc[[-1]].copy()
        trailing.index = [last_ts + pd.Timedelta(minutes=1)]
        trailing[["open", "high", "low", "close"]] = last_close
        trailing["volume"] = 0
        df = pd.concat([df, trailing])

        cerebro = bt.Cerebro(runonce=False)
        cerebro.addstrategy(strategy_cls, **(params or {}))
        cerebro.adddata(bt.feeds.PandasData(dataname=df))
        cerebro.broker.setcash(equity)
        cerebro.broker.setcommission(commission=0.0)
        cerebro.broker.set_slippage_perc(perc=SLIPPAGE_PCT, slip_open=True, slip_match=True)
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

        start_value = cerebro.broker.getvalue()
        results = cerebro.run()
        strat = results[0]
        end_value = cerebro.broker.getvalue()

        if strat.position:
            raise RuntimeError(
                f"{strategy_cls.__name__} left an open position at the end of session {d} "
                f"(size={strat.position.size}) — day-trading strategies MUST flatten by "
                f"close. This is a strategy bug (missing/broken flatten-by-close logic), "
                f"not something this engine should silently paper over."
            )

        trades = strat.analyzers.trades.get_analysis()
        total_trades = trades.get("total", {}).get("total", 0) or 0
        rejected_orders = getattr(strat, "rejected_orders", 0)

        session_results.append({
            "date": d,
            "return_pct": round((end_value / start_value - 1) * 100, 4),
            "start_equity": round(start_value, 2),
            "end_equity": round(end_value, 2),
            "total_trades": total_trades,
            "rejected_orders": rejected_orders,
        })
        equity = end_value

    total_return_pct = round((equity / starting_cash - 1) * 100, 4) if session_results else 0.0
    return {
        "session_results": session_results,
        "starting_cash": starting_cash,
        "ending_equity": round(equity, 2),
        "total_return_pct": total_return_pct,
        "total_trades": sum(s["total_trades"] for s in session_results),
        "total_rejected_orders": sum(s["rejected_orders"] for s in session_results),
        "num_sessions": len(session_results),
    }
