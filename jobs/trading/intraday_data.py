"""jobs/trading/intraday_data.py — Session-level access to intraday_bars.

Raw intraday_bars includes pre-market/after-hours; everything here filters
to the regular 9:30-16:00 ET session, since that's what a day-trading
strategy operates within (extended-hours bars are thin and wide-spread,
and the free-tier IEX live feed this would eventually execute against
isn't reliable there anyway).

Returned DataFrames are indexed by TIME-ZONE-NAIVE Eastern wall-clock
datetimes (converted from the stored UTC timestamps, then the tz dropped)
— backtrader's PandasData feed expects naive datetimes and doesn't do tz
conversion itself, so this is what intraday_backtest.py needs to see
correct 9:30/9:31/.../15:59 values, not UTC-offset ones.

Known simplification: session boundaries are a fixed 9:30-16:00 ET, not
calendar-aware. Early-close days (day after Thanksgiving, Dec 24, some
July 3rds) actually close at 13:00 ET — this filter would incorrectly
include their 13:00-16:00 bars as "regular session" when they're really
post-close/thin activity. Rare (~5-6 days/year out of ~2400 in the 2016-
present history) and doesn't affect data correctness, only session-
boundary precision on those specific days. Flagged, not fixed, this pass.

No training/holdout split exists yet for intraday data (unlike
data.py/holdout.py's daily equivalent) — that's a deliberate, separate
design decision (which historical days/regimes to seal, mirroring the
verification rigor in HOLDOUT_WINDOWS.md) not made in this first pass.
Every function here can see every session; nothing is sealed yet.
"""
import datetime as dt

import pandas as pd

from jobs.trading.db import get_connection

SESSION_OPEN = dt.time(9, 30)
SESSION_CLOSE = dt.time(16, 0)


def _rows_to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(
        [dict(r) for r in rows],
        columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"],
    )
    if df.empty:
        df.index = pd.DatetimeIndex([], name="timestamp")
        return df[["open", "high", "low", "close", "volume"]]
    ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    df = df.set_index(ts.rename("timestamp")).sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def regular_session_bars(symbol: str = "SPY", start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """All intraday_bars for `symbol` with UTC timestamp in [start, end]
    (ISO strings, either bound optional), filtered to the regular
    9:30-16:00 ET session."""
    conn = get_connection()
    try:
        clauses = ["symbol = ?"]
        params = [symbol]
        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)
        query = f"SELECT * FROM intraday_bars WHERE {' AND '.join(clauses)} ORDER BY timestamp"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    df = _rows_to_df(rows)
    if df.empty:
        return df
    times = df.index.time
    mask = (times >= SESSION_OPEN) & (times <= SESSION_CLOSE)
    return df[mask]


def list_session_dates(symbol: str = "SPY") -> list[str]:
    """Distinct trading-session dates (ET) with regular-session data,
    ascending ISO date strings."""
    df = regular_session_bars(symbol)
    if df.empty:
        return []
    return sorted({d.isoformat() for d in df.index.date})


def session_bars(session_date: str, symbol: str = "SPY") -> pd.DataFrame:
    """One trading session's regular-hours bars. session_date: 'YYYY-MM-DD'
    (ET calendar date — safe to query by matching UTC calendar date since
    the US regular session never crosses UTC midnight: 9:30 ET is always
    afternoon UTC same day, 16:00 ET is always evening UTC same day)."""
    return regular_session_bars(symbol, start=f"{session_date}T00:00:00+00:00", end=f"{session_date}T23:59:59+00:00")
