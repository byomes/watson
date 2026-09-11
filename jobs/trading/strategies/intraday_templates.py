"""jobs/trading/strategies/intraday_templates.py — Day-trading strategy
templates. Separate from templates.py (the daily pipeline) because the
mechanics genuinely differ — different data source (intraday_bars, minute
bars), different backtest engine (intraday_backtest.py, one cerebro per
session), and a hard flatten-by-close requirement daily strategies don't
have. Reuses templates.py's _TrackedStrategy for rejected-order tracking
and the _enter_full_position() 95%-of-portfolio sizing helper — no reason
to duplicate logic that isn't intraday-specific.

No grid/DB-strategy-row machinery exists yet for this family (unlike
templates.py's TEMPLATES dict + iteration_loop.py's grid-walk pipeline) —
this is infrastructure-validation scope: one well-known, textbook strategy
(Opening Range Breakout) to prove the data + engine actually work end to
end, not a fully built-out pipeline. That's a deliberate, separate
decision for later.
"""
import datetime as dt

from jobs.trading.strategies.templates import _TrackedStrategy


class _IntradayStrategy(_TrackedStrategy):
    """Base for every day-trading strategy — adds the flatten-by-close
    rule real day trading requires. intraday_backtest.py's
    run_intraday_backtest() runs one cerebro per session and RAISES if a
    position is still open at the end — this is what keeps that check from
    ever firing. Every strategy's next() should call
    `if self._flatten_if_near_close(): return` before any entry logic."""

    FLATTEN_AFTER = dt.time(15, 55)  # 5 min before the 16:00 ET close

    def _flatten_if_near_close(self) -> bool:
        """True means "stop, don't run any entry/exit logic this bar" —
        returned whenever we're at/past FLATTEN_AFTER, REGARDLESS of
        whether a position is currently open. Closing an existing position
        isn't enough on its own: a strategy that only closes-if-position
        but still evaluates entry logic in the closing window can open a
        BRAND NEW position on the session's last bar or two, with no bar
        left afterward for this check to ever fire again. Confirmed live
        2026-09-11 — that exact bug tripped intraday_backtest.py's
        end-of-session open-position guard on the very first validation
        run (OpeningRangeBreakoutStrategy entered at 15:59, no 16:00+ bar
        arrived to close it)."""
        bar_time = self.data.datetime.time(0)
        if bar_time < self.FLATTEN_AFTER:
            return False
        if self.position:
            self.close()
        return True


class OpeningRangeBreakoutStrategy(_IntradayStrategy):
    """Classic, textbook day-trading strategy — not KB-sourced (unlike
    donchian_breakout/time_series_momentum), this one's standard enough
    not to need a citation: define the day's opening range as the high/low
    of the first `range_minutes`, buy on a close above the range high,
    exit on a close back below the range low (stop) or the flatten-by-
    close rule, whichever comes first. Long-only, no shorting — same
    simplification as every other template in this pipeline.

    Chosen specifically as the first intraday template because it's simple
    enough to be an infrastructure-validation vehicle (does the data +
    engine actually produce sane results end to end?) rather than a
    serious edge candidate in its own right."""
    params = (("range_minutes", 15),)

    def __init__(self):
        super().__init__()
        self._range_high = None
        self._range_low = None
        self._bars_seen = 0

    def next(self):
        self._bars_seen += 1
        if self._bars_seen <= self.p.range_minutes:
            h, l = self.data.high[0], self.data.low[0]
            self._range_high = h if self._range_high is None else max(self._range_high, h)
            self._range_low = l if self._range_low is None else min(self._range_low, l)
            return

        if self._flatten_if_near_close():
            return

        if not self.position and self._range_high is not None and self.data.close[0] > self._range_high:
            self._enter_full_position()
        elif self.position and self.data.close[0] < self._range_low:
            self.close()


INTRADAY_TEMPLATES = {
    "opening_range_breakout": {
        "cls": OpeningRangeBreakoutStrategy,
        "label": "Opening range breakout",
    },
}
