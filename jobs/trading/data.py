"""jobs/trading/data.py — training_data() / holdout_data() query helpers.

training_data() is the hard code-level guard the iteration loop is built on:
it excludes every date range in HOLDOUT_WINDOWS at the SQL level, so the
holdout dates are never even returned to a caller — there is no code path
by which the iteration loop can see them, accidentally or otherwise.

holdout_data() is the deliberately separate, explicitly-named accessor for
the sealed windows. Only jobs/trading/evaluate.py should ever import it.
"""
import pandas as pd

from jobs.trading.db import get_connection
from jobs.trading.holdout import HOLDOUT_WINDOWS


def training_data(symbol: str = "SPY") -> pd.DataFrame:
    """All daily_bars rows for `symbol` OUTSIDE every holdout window."""
    conn = get_connection()
    try:
        clauses = " AND ".join("NOT (date BETWEEN ? AND ?)" for _ in HOLDOUT_WINDOWS)
        params = [symbol]
        for start, end in HOLDOUT_WINDOWS.values():
            params.extend([start, end])
        query = f"SELECT * FROM daily_bars WHERE symbol = ? AND {clauses} ORDER BY date"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return _rows_to_df(rows)


def holdout_data(window_name: str, symbol: str = "SPY") -> pd.DataFrame:
    """Bars for `symbol` inside the named sealed holdout window only."""
    if window_name not in HOLDOUT_WINDOWS:
        raise ValueError(f"Unknown holdout window: {window_name!r}. Valid: {list(HOLDOUT_WINDOWS)}")
    start, end = HOLDOUT_WINDOWS[window_name]
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM daily_bars WHERE symbol = ? AND date BETWEEN ? AND ? ORDER BY date",
            (symbol, start, end),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_df(rows)


def _rows_to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(
        [dict(r) for r in rows],
        columns=["symbol", "date", "open", "high", "low", "close", "volume"],
    )
    if df.empty:
        df.index = pd.DatetimeIndex([], name="date")
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df[["open", "high", "low", "close", "volume"]]
