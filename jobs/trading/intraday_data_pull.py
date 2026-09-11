"""jobs/trading/intraday_data_pull.py — Pull SPY minute bars (real SIP
consolidated-tape data, not the free-tier IEX-only live feed) into
trading.db's intraday_bars table, for backtesting a day-trading strategy.

Free-tier Alpaca blocks SIP data less than ~15-20 minutes old ("subscription
does not permit querying recent SIP data") — confirmed live 2026-09-11.
Everything older than that is fully available, back to 2016-01-01 (2015
returns nothing; this is Alpaca's documented historical data start, not an
account-specific limit). This puller stays a fixed RECENCY_BUFFER_MINUTES
behind "now" specifically to stay clear of that boundary — it is a
historical/backtesting puller, not a live feed, and never tries to be one.

Bars include pre-market/after-hours — this pulls the raw feed as-is and
does NOT filter to the regular 9:30-16:00 ET session; that filtering
belongs in the data-access layer (jobs/trading/intraday_data.py), same
separation of concerns as daily_bars (raw) vs training_data()/holdout_data()
(filtered) in data.py/holdout.py.

Usage: PYTHONPATH=<repo> venv/bin/python -m jobs.trading.intraday_data_pull
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone

from jobs.trading.alpaca_client import get_data_client
from jobs.trading.db import get_connection
from jobs.trading.schema import create_tables

log = logging.getLogger(__name__)

HISTORY_START = date(2016, 1, 1)
RECENCY_BUFFER_MINUTES = 25  # stays clear of the ~15-20 min free-tier SIP cutoff


def pull_intraday_bars(symbol: str = "SPY", start: date = HISTORY_START) -> int:
    """Pull minute bars for `symbol` from `start` through (now - buffer),
    one calendar year per request (Alpaca auto-paginates within a request;
    chunking by year just keeps each call's memory/time bounded and makes
    the pull resumable/inspectable). Upserts into intraday_bars. Returns
    total rows written."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    end_cutoff = datetime.now(timezone.utc) - timedelta(minutes=RECENCY_BUFFER_MINUTES)
    client = get_data_client()
    create_tables()
    conn = get_connection()
    total = 0
    try:
        year = start.year
        while True:
            chunk_start = datetime(year, 1, 1, tzinfo=timezone.utc)
            if chunk_start > end_cutoff:
                break
            chunk_end = min(datetime(year + 1, 1, 1, tzinfo=timezone.utc), end_cutoff)

            req = StockBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                start=chunk_start, end=chunk_end, feed=DataFeed.SIP,
            )
            t0 = time.time()
            bars = client.get_stock_bars(req).data.get(symbol, [])
            rows = [
                (symbol, b.timestamp.isoformat(), b.open, b.high, b.low, b.close, int(b.volume))
                for b in bars
            ]
            conn.executemany(
                """INSERT INTO intraday_bars (symbol, timestamp, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, timestamp) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume""",
                rows,
            )
            conn.commit()
            total += len(rows)
            log.info("%s %d: %d bars (%.1fs)", symbol, year, len(rows), time.time() - t0)
            year += 1
        return total
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = pull_intraday_bars()
    log.info("Pulled/updated %d SPY minute bars into trading.db", n)


if __name__ == "__main__":
    main()
