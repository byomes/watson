# An Introduction to Volatility Targeting

Source: https://quantpedia.com/an-introduction-to-volatility-targeting/
Fetched: 2026-08-21

A position-sizing technique, not a standalone entry signal — relevant as an
enhancement to any of this pipeline's templates, and to jobs/trading/risk.py's
existing fixed 2%-per-position cap: volatility targeting sizes dynamically
instead of using one static percentage regardless of market conditions.

## Core Concept

Adjusts leverage/position size based on realized market volatility to hold risk exposure roughly constant. When volatility rises, reduce size (de-risk); when volatility falls, increase size. Keeps dollar risk stable despite changing market conditions.

## Why It Works

**Improved Sharpe ratios**: risk-adjusted returns improve for equities/credit when volatility targeting uses accurate volatility forecasts — negligible impact for bonds/currencies/commodities, so the benefit is asset-class-dependent (SPY, an equity, is squarely in the category where it helps).

**Smoother risk profile**: scaling positions inversely to volatility produces "smooth volatility" that's easier to predict, reducing the variance of volatility itself.

## Implementation Methods

- **Portfolio volatility targeting**: adjust total exposure to one target volatility level (simplest).
- **Dynamic volatility scaling**: scale individual instruments by their own volatility/correlation before portfolio-level adjustment.
- **Volatility switching**: a faster volatility measure activates only during real market stress, balancing responsiveness against whipsaw.
- **Momentum filtering**: reduce leverage during falling markets specifically, exploiting the tendency of declines to persist.

## Volatility Estimation Methods

**Simple volatility**: equal-weighted standard deviation of past returns. **EWMA**: exponentially weighted, more weight on recent returns, responds faster. **GARCH**: models variance as dependent on past squared returns and past variances — captures volatility clustering.

## Empirical Results (60/40 equity-bond portfolio, Mar 2006 - Nov 2020)

- Simple vol targeting: return 8.86%→10.76%, volatility 11.27%→10.59%, Sharpe 0.79→1.02.
- EWMA variant: return to 10.85%, volatility down to 8.09%, Sharpe up to 1.34.
- Momentum-based (tactical): return to 10.07%, minimal volatility change — effectiveness depends on the market's trending character.

All variants reduced max drawdown and tail-risk (95% drawdown metric) — the practical payoff is downside protection, not just a headline return bump.

## Key Trade-off

Delayed volatility measurement avoids overreacting to temporary stress, but risks being too slow during a genuine regime shift. Momentum filters partially address this by adding directional sensitivity on top of the pure volatility measure.
