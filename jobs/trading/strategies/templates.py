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
}
