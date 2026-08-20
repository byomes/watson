"""jobs/trading/backtest.py — backtrader wrapper. Every run models transaction
costs and slippage and logs return/drawdown/Sharpe/win-rate plus the SPY
buy-and-hold return over the identical window, into backtest_runs.

Cost model (documented assumption, not hidden in a config file):
  - Commission: $0/trade, matching Alpaca's actual fee-free US equities.
  - Slippage: 5 bps (0.05%) per fill, applied on both entry and exit, via
    backtrader's built-in percentage slippage model. This is a conservative
    stand-in for real bid/ask spread + market impact on a liquid name like
    SPY — not zero, so "model transaction costs and slippage" is never
    silently skipped, even though commission itself is genuinely zero here.
"""
import backtrader as bt

from jobs.trading.db import get_connection

SLIPPAGE_PCT = 0.0005  # 5 bps
STARTING_CASH = 100_000.0


def _buy_and_hold_return_pct(data_df) -> float:
    if len(data_df) < 2:
        return 0.0
    return float((data_df["close"].iloc[-1] / data_df["close"].iloc[0] - 1) * 100)


def run_backtest(strategy_cls, params: dict, data_df, symbol: str = "SPY",
                  window_label: str = "training", strategy_id: int | None = None,
                  rationale: str | None = None) -> dict:
    """Run `strategy_cls(**params)` against `data_df` (a DataFrame shaped like
    jobs.trading.data.training_data()/holdout_data() output). Logs one row to
    backtest_runs and returns the same metrics dict."""
    if data_df is None or len(data_df) < 2:
        raise ValueError("data_df must have at least 2 rows to backtest")

    # runonce=False (event-driven, not vectorized): default runonce=True
    # pre-sizes internal indicator arrays based on total data length, which
    # throws a raw "array assignment index out of range" IndexError whenever
    # an indicator's period exceeds the window's bar count — a real case
    # once holdout windows as short as 62 bars (crash_2020) meet large
    # lookback periods from the expanded grids. runonce=False just never
    # lets the indicator warm up (strategy correctly takes no positions for
    # that window) instead of crashing. Confirmed identical output for a
    # combo that already ran successfully under the default before this
    # change (mean_reversion period=22/devfactor=2.0 across all 3 windows).
    cerebro = bt.Cerebro(runonce=False)
    cerebro.addstrategy(strategy_cls, **(params or {}))

    feed = bt.feeds.PandasData(dataname=data_df)
    cerebro.adddata(feed)

    cerebro.broker.setcash(STARTING_CASH)
    cerebro.broker.setcommission(commission=0.0)
    cerebro.broker.set_slippage_perc(perc=SLIPPAGE_PCT, slip_open=True, slip_match=True)

    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                         timeframe=bt.TimeFrame.Days, riskfreerate=0.0, annualize=True)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    start_value = cerebro.broker.getvalue()
    results = cerebro.run()
    strat = results[0]
    end_value = cerebro.broker.getvalue()

    dd = strat.analyzers.dd.get_analysis()
    sharpe = strat.analyzers.sharpe.get_analysis()
    trades = strat.analyzers.trades.get_analysis()

    total_trades = trades.get("total", {}).get("total", 0) or 0
    won_trades = trades.get("won", {}).get("total", 0) or 0
    win_rate = (won_trades / total_trades * 100) if total_trades else None

    metrics = {
        "return_pct": round((end_value / start_value - 1) * 100, 4),
        "max_drawdown_pct": round(-(dd.get("max", {}).get("drawdown", 0.0) or 0.0), 4),
        "sharpe": round(sharpe.get("sharperatio") or 0.0, 4) if sharpe.get("sharperatio") is not None else None,
        "win_rate": round(win_rate, 2) if win_rate is not None else None,
        "benchmark_return_pct": round(_buy_and_hold_return_pct(data_df), 4),
    }

    _log_run(
        strategy_id=strategy_id,
        symbol=symbol,
        window_label=window_label,
        start_date=data_df.index.min().date().isoformat(),
        end_date=data_df.index.max().date().isoformat(),
        rationale=rationale,
        **metrics,
    )
    return metrics


def _log_run(strategy_id, symbol, window_label, start_date, end_date,
             return_pct, max_drawdown_pct, sharpe, win_rate, benchmark_return_pct,
             rationale) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO backtest_runs
               (strategy_id, symbol, window_label, start_date, end_date,
                return_pct, max_drawdown_pct, sharpe, win_rate, benchmark_return_pct, rationale)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy_id, symbol, window_label, start_date, end_date,
             return_pct, max_drawdown_pct, sharpe, win_rate, benchmark_return_pct, rationale),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
