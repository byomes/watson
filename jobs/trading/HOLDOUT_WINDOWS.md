# Holdout Windows — Verified Against Real SPY Data

Computed by `jobs/trading/verify_holdout_windows.py`, pulled live from Alpaca's
historical data API (SPY daily bars) on 2026-08-20 — not estimated from memory.

## Raw results

| Window | Dates | Days | Total return | Max drawdown | Annualized vol |
|---|---|---|---|---|---|
| Crash/recovery | 2020-02-01 → 2020-04-30 | 61 | -9.56% | **-34.18%** | 57.75% |
| Grinding bear | 2022-01-01 → 2022-12-31 | 251 | -19.95% | -25.36% | 24.31% |
| Calm climb (2019) | 2019-01-01 → 2019-12-31 | 251 | +28.33% | -6.59% | 12.44% |
| Calm climb (2017) | 2017-01-01 → 2017-12-31 | 251 | +18.57% | **-3.00%** | **6.59%** |

## Verdict on each proposed window

**1. Feb–Apr 2020 — confirmed as proposed.** A genuine sharp-crash regime: -34.18%
max drawdown inside 61 trading days, with annualized volatility (57.75%) more than
double every other candidate — no ambiguity here. The window captures both the
crash and the beginning of the recovery (net -9.56% by Apr 30, well off the
~-34% trough), matching the "crash/recovery" framing, not just "crash."

**2. Full year 2022 — confirmed as proposed, and genuinely distinct from window 1.**
-25.36% max drawdown is comparable in *depth* to 2020's crash, but the shape is
completely different: annualized volatility of 24.31% is less than half of 2020's,
spread across a full year instead of two months. This is the "grinding" bear market
the spec asked for, not a second crash — good, the three windows aren't
accidentally testing the same regime twice.

**3. 2019 vs. 2017 — challenged; 2017 is the better fit and is what I'm using.**
The spec proposed 2019 as primary with 2017 as an explicit alternative, framed as
"calm, low-volatility climb." The real numbers say 2017 fits that description
much better than 2019 does:
- 2017 annualized volatility (6.59%) is roughly **half** of 2019's (12.44%).
- 2017 max drawdown (-3.00%) is roughly **half** of 2019's (-6.59%).

2019 wasn't actually calm by the numbers — it included the August 2019 trade-war
selloff, which is exactly the -6.59% drawdown showing up above. 2017 is one of the
lowest-realized-volatility years in S&P 500 history (this is well-documented
market history, and the pulled numbers confirm it directly): a genuinely steady,
low-drama grind upward with no real intra-year selloff. Since the whole point of
this third window is to test a strategy against a *low-volatility* regime as
distinct from the other two (crash-shaped and grind-down-shaped), 2017 is the
window that actually delivers that contrast. **Using 2017, not 2019.**

## Final agreed windows (hard-coded in `jobs/trading/holdout.py`)

1. `crash_2020` — 2020-02-01 to 2020-04-30
2. `bear_2022` — 2022-01-01 to 2022-12-31
3. `calm_2017` — 2017-01-01 to 2017-12-31

Three genuinely distinct regimes by the actual data: a fast panic (57.75% ann.
vol), a slow grind-down (24.31% ann. vol, similar depth to the crash but 1/12th
the speed), and a calm, low-drama climb (6.59% ann. vol) — not three variations on
the same shape.
