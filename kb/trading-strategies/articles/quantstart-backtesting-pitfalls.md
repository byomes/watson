# Successful Backtesting of Algorithmic Trading Strategies — Backtesting Pitfalls

Source: https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/
Fetched: 2026-08-20

## What is Backtesting?

Backtesting involves exposing a strategy algorithm to historical financial data to generate trading signals. Each completed trade produces a profit or loss, and the accumulated P&L represents the strategy's total performance.

Four primary purposes for backtesting: **Filtration** (eliminate strategies that fail historical thresholds), **Modeling** (safely test transaction costs, order routing, latency, liquidity effects), **Optimization** (improve performance by adjusting parameters), **Verification** (compare against external implementations).

## Major Biases Affecting Backtests

### Optimisation Bias (Curve Fitting)

"This is probably the most insidious of all backtest biases. It involves adjusting or introducing additional trading parameters until the strategy performance on the backtest data set is very attractive." Live performance often diverges significantly from backtest results.

Mitigation:
- Minimize the total number of parameters
- Expand training datasets while remaining cautious about outdated regime data
- Conduct sensitivity analysis by varying parameters incrementally to visualize performance surfaces — a smooth surface suggests sound reasoning; an erratic surface suggests parameters are artifacts of the test data.

### Look-Ahead Bias

Occurs "when future data is accidentally included at a point in the simulation where that data would not have actually been available." Three common sources:

- **Technical Bugs**: Incorrect array indexing or iterator offsets incorporating future-period data.
- **Parameter Calculation**: Using an entire dataset (including future values) to calculate regression coefficients, then retroactively applying them to historical periods.
- **Maxima/Minima**: Trading strategies using high/low prices from OHLC data must lag these values by at least one period, since extreme values can only be determined after a period concludes.

### Survivorship Bias

Occurs when strategies are tested exclusively on assets that "survived" to the current time, excluding assets that failed or were delisted. Example: testing an equity strategy only on stocks that survived the 2001 market crash creates an artificial performance boost.

Mitigation: survivorship-bias-free datasets (institutional-grade, expensive) — "Yahoo Finance data is NOT survivorship bias free" — or building forward from the current date (becomes survivorship-bias-free after 3-4 years).

### Psychological Tolerance Bias

The difficulty of enduring actual drawdowns that backtests suggest will occur. A strategy showing "a maximum relative drawdown of 25% and a maximum drawdown duration of 4 months" appears tolerable in theory, but experiencing it live creates pressure that often leads traders to abandon otherwise sound strategies during drawdown periods. Expect actual drawdowns to match historical patterns and prepare to persist through them.

## Software Environment Considerations

Maintaining control over the full technology stack typically yields superior long-term returns versus vendor software with potential bugs beyond your control. Python offers an optimal balance of customization, development speed, testing capabilities, and execution performance for most retail traders; a common professional approach is prototyping in Python then converting performance-critical sections to C++ iteratively.
