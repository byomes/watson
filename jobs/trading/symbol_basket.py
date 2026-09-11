"""jobs/trading/symbol_basket.py — Multi-symbol robustness test for the
mean-reversion signal, and (later) the basket the live-multi loop trades.

Why this exists: everything backtested this session (5 templates, hundreds
of variants) was tested on SPY alone. A strategy that only "works" on one
price series is much weaker evidence of real edge than one that holds up
independently across many unrelated instruments — testing across a basket
is a genuinely different, arguably stronger form of out-of-sample check
than the sealed-holdout-windows-on-SPY approach (which is still testing
the same one series, just different date ranges). If mean-reversion's
sealed-test performance was curve-fit to SPY's specific price history,
it should look inconsistent or random across other symbols; if it's a
real, generalizable effect, it should show up repeatedly.

BASKET: liquid sector/broad-market ETFs, chosen for (a) enough history
(2016+, matching this pipeline's daily_bars convention) and (b) genuine
diversity — different sectors move somewhat independently, unlike e.g.
XLK vs QQQ which are both tech-heavy and would just be a correlated
re-test of the same thing under a different ticker.
"""
BASKET = [
    "XLK",   # Technology
    "XLF",   # Financials
    "XLE",   # Energy
    "XLV",   # Health Care
    "XLY",   # Consumer Discretionary
    "XLP",   # Consumer Staples
    "XLI",   # Industrials
    "XLB",   # Materials
    "XLU",   # Utilities
    "XLRE",  # Real Estate
    "IWM",   # Small-cap (Russell 2000) — different market-cap regime than the sector ETFs
    "DIA",   # Large-cap value-ish (Dow 30) — different construction than SPY/QQQ
]

# The mean-reversion parameter neighborhood already vetted this session —
# not re-invented, reused as-is (period~30-35, devfactor~2.0; see #18/#499/
# #525's holdout results in evaluate.py's history).
TEST_PARAMS = {"period": 30, "devfactor": 2.0}
