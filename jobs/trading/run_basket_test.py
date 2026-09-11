"""jobs/trading/run_basket_test.py — Run the vetted mean-reversion params
against every symbol in symbol_basket.BASKET's training data (same
HOLDOUT_WINDOWS exclusion as SPY — these are broad market-stress dates,
not SPY-idiosyncratic, so keeping them sealed for these symbols too keeps
a real holdout available later if this basket gets pursued further).

Reuses existing, already-parameterized machinery end to end — no new
data-layer or backtest-engine code needed: data_pull.pull_daily_bars(symbol=...),
data.training_data(symbol=...), backtest.run_backtest(..., symbol=...) all
already accept a symbol argument, just never exercised with anything but
SPY until now.

Usage: PYTHONPATH=<repo> venv/bin/python -m jobs.trading.run_basket_test
"""
import logging

from jobs.trading.backtest import run_backtest
from jobs.trading.data import training_data
from jobs.trading.data_pull import pull_daily_bars
from jobs.trading.strategies.templates import MeanReversionStrategy
from jobs.trading.symbol_basket import BASKET, TEST_PARAMS

log = logging.getLogger(__name__)


def pull_basket(years: int = 10) -> dict:
    """Pull daily bars for every basket symbol. Returns {symbol: rows_written}."""
    results = {}
    for sym in BASKET:
        try:
            n = pull_daily_bars(symbol=sym, years=years)
            results[sym] = n
            log.info("%s: %d bars pulled", sym, n)
        except Exception as exc:
            results[sym] = f"ERROR: {exc}"
            log.error("%s: pull failed: %s", sym, exc)
    return results


def run_basket_test() -> list[dict]:
    """Run TEST_PARAMS mean-reversion against each basket symbol's training
    data. Returns one result dict per symbol, sorted best-to-worst by
    return vs. that symbol's own buy-and-hold."""
    results = []
    for sym in BASKET:
        df = training_data(symbol=sym)
        if len(df) < 100:
            results.append({"symbol": sym, "error": f"insufficient data ({len(df)} rows)"})
            continue
        try:
            metrics = run_backtest(MeanReversionStrategy, TEST_PARAMS, df, symbol=sym, window_label="training")
            results.append({"symbol": sym, **metrics})
        except Exception as exc:
            results.append({"symbol": sym, "error": str(exc)})
    results.sort(key=lambda r: r.get("return_pct", -999) - r.get("benchmark_return_pct", 0), reverse=True)
    return results


def format_report(results: list[dict]) -> str:
    lines = [f"Mean-reversion (period={TEST_PARAMS['period']}, devfactor={TEST_PARAMS['devfactor']}) across {len(results)} symbols:"]
    beat_count = 0
    for r in results:
        if "error" in r:
            lines.append(f"  {r['symbol']}: ERROR — {r['error']}")
            continue
        beat = r["return_pct"] > r["benchmark_return_pct"]
        beat_count += beat
        lines.append(
            f"  {r['symbol']}: {'BEAT' if beat else 'trail'} — return {r['return_pct']}% "
            f"(buy-hold {r['benchmark_return_pct']}%), sharpe {r['sharpe']}, "
            f"trades {r['total_trades']}, win_rate {r['win_rate']}"
        )
    valid = [r for r in results if "error" not in r]
    lines.append(f"\nBeat buy-and-hold on {beat_count}/{len(valid)} symbols with valid data.")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Pulling basket data...")
    pull_results = pull_basket()
    print(pull_results)
    print("\nRunning basket test...")
    test_results = run_basket_test()
    print(format_report(test_results))
