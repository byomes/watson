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
    of the first `range_minutes`, buy on a close above the range high.
    Long-only, no shorting — same simplification as every other template
    in this pipeline.

    Exit: whichever comes first — a real stop_loss_pct below entry (not
    just "back below the range low", which on a wide opening range can be
    a much bigger loss than a day-trading stop should ever allow), an
    optional profit_target_pct above entry, or the flatten-by-close rule.
    The original infra-validation version of this template (2026-09-11)
    only had the range-low exit — real, but not a REAL day-trading risk
    control; this version adds one."""
    params = (
        ("range_minutes", 15),
        ("stop_loss_pct", 0.005),
        ("profit_target_pct", None),  # None = no target, ride to stop/close
    )

    def __init__(self):
        super().__init__()
        self._range_high = None
        self._range_low = None
        self._bars_seen = 0
        self._entry_price = None

    def next(self):
        self._bars_seen += 1
        if self._bars_seen <= self.p.range_minutes:
            h, l = self.data.high[0], self.data.low[0]
            self._range_high = h if self._range_high is None else max(self._range_high, h)
            self._range_low = l if self._range_low is None else min(self._range_low, l)
            return

        if self._flatten_if_near_close():
            self._entry_price = None
            return

        price = self.data.close[0]
        if self.position:
            stop_hit = price <= self._entry_price * (1 - self.p.stop_loss_pct)
            target_hit = self.p.profit_target_pct is not None and price >= self._entry_price * (1 + self.p.profit_target_pct)
            if stop_hit or target_hit or price < self._range_low:
                self.close()
                self._entry_price = None
        elif self._range_high is not None and price > self._range_high:
            self._enter_full_position()
            self._entry_price = price


class VWAPMeanReversionStrategy(_IntradayStrategy):
    """Classic day-trading mean-reversion: track the session's running
    volume-weighted average price (VWAP) as a fair-value anchor, buy when
    price dips `entry_deviation_pct` below it (a common institutional/
    retail day-trading pattern — VWAP is what a large share of intraday
    execution algos actually benchmark against, so price deviating far
    from it during the session is a real, widely-watched signal, not an
    arbitrary threshold), exit when price reverts back to VWAP, a real
    stop_loss_pct below entry, or the flatten-by-close rule.

    `warmup_minutes` bars must elapse before trading starts — VWAP on the
    first few bars of a session is noisy (computed from almost no volume),
    same reasoning as templates.py's TimeSeriesMomentumStrategy warmup.
    Long-only, no shorting — same simplification as every other template."""
    params = (
        ("entry_deviation_pct", 0.003),
        ("stop_loss_pct", 0.005),
        ("warmup_minutes", 10),
    )

    def __init__(self):
        super().__init__()
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self._bars_seen = 0
        self._entry_price = None

    def _vwap(self) -> float | None:
        if self._cum_vol <= 0:
            return None
        return self._cum_pv / self._cum_vol

    def next(self):
        self._bars_seen += 1
        typical_price = (self.data.high[0] + self.data.low[0] + self.data.close[0]) / 3.0
        volume = self.data.volume[0]
        self._cum_pv += typical_price * volume
        self._cum_vol += volume

        if self._flatten_if_near_close():
            self._entry_price = None
            return
        if self._bars_seen <= self.p.warmup_minutes:
            return

        vwap = self._vwap()
        if vwap is None:
            return
        price = self.data.close[0]

        if self.position:
            stop_hit = price <= self._entry_price * (1 - self.p.stop_loss_pct)
            reverted = price >= vwap
            if stop_hit or reverted:
                self.close()
                self._entry_price = None
        elif price <= vwap * (1 - self.p.entry_deviation_pct):
            self._enter_full_position()
            self._entry_price = price


INTRADAY_TEMPLATES = {
    "opening_range_breakout": {
        "cls": OpeningRangeBreakoutStrategy,
        "label": "Opening range breakout",
        "grid": [
            {"range_minutes": rm, "stop_loss_pct": sl, "profit_target_pct": pt}
            for rm in (5, 15, 30)
            for sl in (0.003, 0.005, 0.01)
            for pt in (None, 0.01, 0.02)
        ],
    },
    "vwap_mean_reversion": {
        "cls": VWAPMeanReversionStrategy,
        "label": "VWAP mean reversion",
        "grid": (
            [
                {"entry_deviation_pct": dv, "stop_loss_pct": sl, "warmup_minutes": wm}
                for dv in (0.002, 0.003, 0.005, 0.0075)
                for sl in (0.003, 0.005, 0.0075)
                for wm in (10, 30)
            ]
            # Round 2 (2026-09-11): pushed wider, not exhaustive — Round 1's
            # 2019-2020 screen showed a consistent trend toward wider
            # thresholds performing better (best result was at the grid's
            # own upper edge, dv=sl=0.0075, warmup=10 -> -1.86%, the best
            # of 51 combos tried that round including opening_range_breakout)
            # and warmup=10 beating warmup=30 at every matched (dv, sl)
            # pair. This round extends dv/sl out to 1.5% and drops warmup=30
            # (consistently the worse half of every pair) in favor of
            # trying an even shorter warmup=5 alongside 10.
            + [
                {"entry_deviation_pct": dv, "stop_loss_pct": sl, "warmup_minutes": wm}
                for dv in (0.0075, 0.01, 0.0125, 0.015)
                for sl in (0.0075, 0.01, 0.0125, 0.015)
                for wm in (5, 10)
                if (dv, sl, wm) != (0.0075, 0.0075, 10)  # already in Round 1
            ]
        ),
    },
}
