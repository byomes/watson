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


class MACrossoverStrategy(bt.Strategy):
    params = (("fast", 20), ("slow", 50))

    def __init__(self):
        fast_sma = bt.ind.SMA(period=self.p.fast)
        slow_sma = bt.ind.SMA(period=self.p.slow)
        self.crossover = bt.ind.CrossOver(fast_sma, slow_sma)

    def next(self):
        if not self.position and self.crossover > 0:
            self.buy()
        elif self.position and self.crossover < 0:
            self.close()


class MeanReversionStrategy(bt.Strategy):
    params = (("period", 20), ("devfactor", 2.0))

    def __init__(self):
        self.bb = bt.ind.BollingerBands(period=self.p.period, devfactor=self.p.devfactor)

    def next(self):
        if not self.position and self.data.close[0] < self.bb.lines.bot[0]:
            self.buy()
        elif self.position and self.data.close[0] >= self.bb.lines.mid[0]:
            self.close()


class MomentumStrategy(bt.Strategy):
    params = (("period", 90),)

    def __init__(self):
        self.roc = bt.ind.RateOfChange(period=self.p.period)

    def next(self):
        if not self.position and self.roc[0] > 0:
            self.buy()
        elif self.position and self.roc[0] <= 0:
            self.close()


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
        ),
    },
    "mean_reversion": {
        "cls": MeanReversionStrategy,
        "label": "Mean reversion (Bollinger)",
        "grid": (
            [{"period": p, "devfactor": d} for p in (10, 20, 30) for d in (1.5, 2.0, 2.5)]
            + [{"period": p, "devfactor": d} for p in (15, 25, 40, 50) for d in (1.0, 1.25, 1.75, 2.25, 2.75, 3.0)]
        ),
    },
    "momentum": {
        "cls": MomentumStrategy,
        "label": "Momentum (rate of change)",
        "grid": (
            [{"period": p} for p in (30, 60, 90, 120)]
            + [{"period": p} for p in (10, 20, 40, 50, 70, 80, 100, 110, 140, 150, 160, 180, 200, 220, 250)]
        ),
    },
}
