# Time Series Momentum ("Absolute Momentum")

Source: https://quantpedia.com/strategies/time-series-momentum-effect
Fetched: 2026-08-21

Note: fetched a fresh SPY-only backtest is directly applicable to this — this
is a single-asset signal, unlike cross-sectional momentum which needs a
universe. Distinct from jobs/trading/strategies/templates.py's existing
MomentumStrategy (rate-of-change sign only, no volatility scaling) — this
is the academically-documented version with volatility-adjusted sizing,
a real candidate for a new template.

## Overview

Time series momentum ("absolute momentum") is a distinct anomaly from cross-sectional momentum: an asset's own past returns predict its own future performance, independent of how it performs relative to peers. "The past 12-month excess return of each instrument is a positive predictor of its future return." Cross-sectional momentum needs a full universe to rank winners/losers; time series momentum doesn't — each asset stands alone.

## The Moskowitz, Ooi, Pedersen Research (1965-2009)

58 liquid instruments across commodity futures, currencies, developed equity indices, and government bond futures. Results: annualized alpha 20.7% (Fama-French-adjusted), Sharpe ratio 1.31, volatility 15.74%, max drawdown -33.87%. "A diversified portfolio of time series momentum across all assets is remarkably stable and robust, yielding a high Sharpe ratio with little correlation to passive benchmarks."

## Strategy Mechanics

1. **Signal**: monthly evaluation of the asset's 12-month excess return.
2. **Position**: long if positive, short if negative.
3. **Volatility adjustment**: position size scaled inversely to estimated volatility (GARCH or simpler historical-vol estimates both work) — this is a real, documented refinement over a bare buy/sell-on-sign rule.
4. **Rebalancing**: monthly.

## Why It's Distinctive

**Hedge benefit**: "Time-series momentum returns appear to be largest when the stock market's returns are most extreme; hence, time-series momentum may be a hedge for extreme events" — i.e. it's specifically designed to perform well during crashes, directly relevant to a strategy that needs to handle a window like this pipeline's `crash_2020`.

**Behavioral foundation**: attributed to investors' initial under-reaction and delayed over-reaction to information.

**Consistency**: positive returns shown across multiple decades without evident capacity constraints in futures markets.

## Research Extensions Worth Noting

- Volatility scaling contributes meaningfully to performance on its own, separate from the momentum signal itself.
- Linear trend fits on the price path outperform a simple return-sign signal out of sample.
- Performance degraded somewhat post-2008; underperforms in sideways, low-volatility markets.
- Some research questions statistical robustness under corrected bootstrap methods — worth remembering given this pipeline's own multiple-comparisons findings.

## Risk Considerations

Real drawdowns (max -33.87% in the source study) despite the crisis-hedge property — this is not a low-volatility strategy, and any template built from it should still respect this pipeline's existing hard risk limits (2% max position, 3% daily halt, 15% drawdown stop).
