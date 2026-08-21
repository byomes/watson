"""jobs/trading/strategies/templates.py — Fixed, vetted strategy templates
with explicit parameter grids. "Watson proposes a strategy variant" means
deterministically picking the next untried grid point for one of these three
templates — there is no code-generation path here, and there never should
be one; see jobs/trading/iteration_loop.py's module docstring for why.

Position sizing note: these use backtrader's default all-in-cash sizer
(fully invested when the signal is on, flat otherwise) to test each
template's raw edge against SPY buy-and-hold — that's standard backtesting
practice for a single-symbol strategy-quality test. jobs/trading/risk.py's
2%-per-position cap is a real-order-placement gate for live (paper) trading,
not a backtest sizing rule; it applies once a strategy is actually placing
orders, not while comparing strategy shape against the benchmark here.
"""
import backtrader as bt


class _TrackedStrategy(bt.Strategy):
    """Base class for every template here — tracks rejected/margin-failed
    orders so a bug like time_series_momentum's original sizing bug (orders
    submitted with the right signal, silently rejected on margin, producing
    a flat result indistinguishable from "no signal") can never again hide
    from a report without a deliberate check. jobs/trading/backtest.py
    reads self.rejected_orders after cerebro.run() and surfaces it in every
    run's metrics — a run with 0% return AND 0 signals attempted looks
    different from one with several rejected orders."""

    def __init__(self):
        self.rejected_orders = 0

    def notify_order(self, order):
        if order.status in (order.Margin, order.Rejected):
            self.rejected_orders += 1


class MACrossoverStrategy(_TrackedStrategy):
    params = (("fast", 20), ("slow", 50))

    def __init__(self):
        super().__init__()
        fast_sma = bt.ind.SMA(period=self.p.fast)
        slow_sma = bt.ind.SMA(period=self.p.slow)
        self.crossover = bt.ind.CrossOver(fast_sma, slow_sma)

    def next(self):
        if not self.position and self.crossover > 0:
            self.buy()
        elif self.position and self.crossover < 0:
            self.close()


class MeanReversionStrategy(_TrackedStrategy):
    params = (("period", 20), ("devfactor", 2.0))

    def __init__(self):
        super().__init__()
        self.bb = bt.ind.BollingerBands(period=self.p.period, devfactor=self.p.devfactor)

    def next(self):
        if not self.position and self.data.close[0] < self.bb.lines.bot[0]:
            self.buy()
        elif self.position and self.data.close[0] >= self.bb.lines.mid[0]:
            self.close()


class MomentumStrategy(_TrackedStrategy):
    params = (("period", 90),)

    def __init__(self):
        super().__init__()
        self.roc = bt.ind.RateOfChange(period=self.p.period)

    def next(self):
        if not self.position and self.roc[0] > 0:
            self.buy()
        elif self.position and self.roc[0] <= 0:
            self.close()


class TimeSeriesMomentumStrategy(_TrackedStrategy):
    """Time-series ("absolute") momentum, per Moskowitz/Ooi/Pedersen — see
    kb/trading-strategies/articles/time-series-momentum.md. Distinct from
    MomentumStrategy above (bare rate-of-change sign, all-in-or-flat): this
    adds the two refinements the research specifically credits with real
    performance — volatility-scaled position sizing and periodic (not
    every-bar) rebalancing.

    Long-only, capped at 1x notional exposure (no leverage, no shorting) —
    a deliberate simplification of the source research (which uses
    leverage and shorting across a multi-asset portfolio) to fit this
    pipeline's single-symbol, no-leverage design.

    Signal: `lookback`-day trailing return sign. Sizing: target_vol /
    realized_annualized_vol (over `vol_window` days), capped at 95% of
    portfolio value. Rebalance: every `rebalance_days` bars, not every bar
    — closer to the source research's monthly cadence than a same-bar
    reaction.

    Caveat (see iteration_loop/templates.py module docstring on holdout
    windows): needs `lookback + vol_window` bars of warmup. holdout_data()
    returns each sealed window in isolation with no pre-window buffer, so
    a long-lookback variant literally cannot trade within crash_2020 (62
    bars) — it will show a flat 0%-return "beat" of SPY's -10% there, for
    lack of data rather than skill. Check the per-window breakdown, not
    just the aggregate PASS/FAIL, before trusting a holdout result for
    this family."""
    params = (
        ("lookback", 252),      # ~12 months of trading days, per the source research
        ("vol_window", 20),
        ("target_vol", 0.15),   # annualized target volatility for the position
        ("rebalance_days", 21), # ~1 trading month
    )

    def __init__(self):
        super().__init__()
        self._bar_count = 0

    def next(self):
        self._bar_count += 1
        warmup = self.p.lookback + self.p.vol_window
        if self._bar_count <= warmup:
            return
        if (self._bar_count - warmup) % self.p.rebalance_days != 0:
            return

        signal = self.data.close[0] / self.data.close[-self.p.lookback] - 1

        daily_returns = [
            self.data.close[-i] / self.data.close[-i - 1] - 1
            for i in range(self.p.vol_window)
        ]
        mean_r = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_r) ** 2 for r in daily_returns) / max(len(daily_returns) - 1, 1)
        annualized_vol = (variance ** 0.5) * (252 ** 0.5)

        if signal > 0 and annualized_vol > 0:
            # Cap at 0.95, not 1.0: a market order sized at exactly 100% of
            # portfolio value against TODAY's close, submitted to fill at
            # TOMORROW's open, leaves zero room for the next bar's price
            # move or slippage — backtrader rejects the whole order on
            # margin if the fill price is even fractionally higher.
            # Confirmed live: this silently zeroed out real signals in
            # calm_2017 specifically (a low-volatility window where
            # target_vol/annualized_vol routinely exceeds 1.0, pinning the
            # cap) — every rebalance submitted a real, correctly-signed buy
            # order that then got rejected, producing a flat 0% result
            # indistinguishable from a genuine "no signal" case unless you
            # check notify_order or order status directly.
            target_fraction = min(self.p.target_vol / annualized_vol, 0.95)
            target_size = int(self.broker.getvalue() * target_fraction / self.data.close[0])
        else:
            target_size = 0

        if target_size <= 0:
            if self.position:
                self.close()
        else:
            diff = target_size - (self.position.size if self.position else 0)
            if diff > 0:
                self.buy(size=diff)
            elif diff < 0:
                self.sell(size=-diff)


# Grids intentionally start with the original small combinations (already
# tested live) as a prefix, with more combinations appended after — grid
# indexing in iteration_loop.propose_next_variant() is positional
# (grid[count of existing strategies rows for this family]), so extending a
# grid must never reorder or remove earlier entries, only append.
TEMPLATES = {
    "ma_crossover": {
        "cls": MACrossoverStrategy,
        "label": "MA crossover",
        "grid": (
            [{"fast": f, "slow": s} for f in (10, 20) for s in (30, 50, 100)]
            + [{"fast": f, "slow": s} for f in (5, 15, 25, 30) for s in (40, 60, 70, 80, 90, 110, 120, 130, 140, 150)]
            # Round 2: fresh fast values (never used above) crossed with a
            # wider slow range, filtered to fast < slow so the crossover
            # semantics stay meaningful.
            + [
                {"fast": f, "slow": s}
                for f in (35, 40, 45, 55, 60)
                for s in (70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 180, 200, 220, 250, 280)
                if f < s
            ]
            # Round 3: fresh fast values, wider slow range still. fast max
            # (90) < slow min (100), so no explicit filter needed.
            + [
                {"fast": f, "slow": s}
                for f in (65, 70, 75, 80, 90)
                for s in (100, 120, 140, 160, 180, 200, 220, 250, 280, 300, 320, 350, 380, 400, 450, 500, 550, 600)
            ]
        ),
    },
    "mean_reversion": {
        "cls": MeanReversionStrategy,
        "label": "Mean reversion (Bollinger)",
        "grid": (
            [{"period": p, "devfactor": d} for p in (10, 20, 30) for d in (1.5, 2.0, 2.5)]
            + [{"period": p, "devfactor": d} for p in (15, 25, 40, 50) for d in (1.0, 1.25, 1.75, 2.25, 2.75, 3.0)]
            # Round 2: fresh period values (never used above), any devfactor.
            + [
                {"period": p, "devfactor": d}
                for p in (12, 18, 22, 28, 35, 45, 60, 70, 80)
                for d in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
            ]
            # Round 3: fresh period values, any devfactor.
            + [
                {"period": p, "devfactor": d}
                for p in (85, 90, 95, 100, 110, 120)
                for d in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0)
            ]
        ),
    },
    "momentum": {
        "cls": MomentumStrategy,
        "label": "Momentum (rate of change)",
        "grid": (
            [{"period": p} for p in (30, 60, 90, 120)]
            + [{"period": p} for p in (10, 20, 40, 50, 70, 80, 100, 110, 140, 150, 160, 180, 200, 220, 250)]
            # Round 2: fresh period values (the "5 mod 10" gaps left by the
            # two segments above, which only used multiples of 10).
            + [{"period": p} for p in (15, 25, 35, 45, 55, 65, 75, 85, 95, 105, 115, 125, 135, 145, 155, 165, 175, 185, 195, 205)]
            # Round 3: fresh period values, all > 250 (the highest used so
            # far), so no overlap check needed against the "5 mod 10"
            # segment above.
            + [{"period": p} for p in range(255, 505, 5)]
        ),
    },
    "time_series_momentum": {
        "cls": TimeSeriesMomentumStrategy,
        "label": "Time-series momentum (vol-scaled)",
        "grid": [
            {"lookback": lb, "target_vol": tv, "vol_window": vw}
            for lb in (126, 189, 252)   # ~6, 9, 12 months
            for tv in (0.10, 0.15, 0.20)
            for vw in (20, 60)
        ],
    },
}
