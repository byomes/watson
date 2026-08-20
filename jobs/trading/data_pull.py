"""jobs/trading/data_pull.py — Pull SPY daily bars from Alpaca into trading.db.

Usage: PYTHONPATH=<repo> venv/bin/python -m jobs.trading.data_pull
"""
import logging
from datetime import date, timedelta

from jobs.trading.alpaca_client import get_data_client
from jobs.trading.db import get_connection
from jobs.trading.schema import create_tables

log = logging.getLogger(__name__)


def pull_daily_bars(symbol: str = "SPY", years: int = 10) -> int:
    """Pull `years` of daily bars for `symbol` and upsert into daily_bars.
    Returns the number of rows written."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    end = date.today()
    start = end - timedelta(days=365 * years + 10)

    client = get_data_client()
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    bars = client.get_stock_bars(req).data[symbol]

    create_tables()
    conn = get_connection()
    try:
        rows = [
            (symbol, b.timestamp.date().isoformat(), b.open, b.high, b.low, b.close, int(b.volume))
            for b in bars
        ]
        conn.executemany(
            """INSERT INTO daily_bars (symbol, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume""",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = pull_daily_bars()
    log.info("Pulled/updated %d SPY daily bars into trading.db", n)


if __name__ == "__main__":
    main()
