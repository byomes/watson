# Market Regime Detection: Methods and Strategy Adaptation

Source: https://www.tradewink.com/learn/ai-market-regime-detection-guide
Fetched: 2026-08-20 (source located via search — "source at build time," per the trading-KB spec)

## What Is a Market Regime?

The characteristic pattern of price movement during a given period. Professional traders recognize "one strategy for all conditions" typically fails — adaptive systems detect the current regime and adjust position sizing, strategy selection, and entry criteria accordingly.

## The Three Primary Market Regimes

### 1. Trending (Momentum) Regime
- Consistent higher highs/higher lows (uptrend) or lower lows/lower highs (downtrend)
- Volume increases on dominant moves, decreases on pullbacks
- Moving averages fan out in the trend direction; RSI stays elevated (>60) or depressed (<40) for extended periods
- **Effective**: momentum trading, gap-and-go, trend following, breakout entries, VWAP bounces
- **Fails**: fade strategies and mean reversion underperform significantly

### 2. Mean-Reverting (Ranging) Regime
- Price oscillates between defined support/resistance; moving averages converge and flatten
- RSI oscillates cleanly between overbought (>70) and oversold (<30); breakout attempts frequently fail and reverse
- **Effective**: mean reversion setups, support/resistance fades, iron condors, short-volatility options
- **Fails**: momentum and breakout strategies consistently underperform

### 3. Choppy (High-Noise) Regime — closest analogue to "crashing"/crisis conditions
- No clear directional bias; high volatility relative to trend; whipsaws in both directions
- Every breakout and fade fails; inconsistent volume, erratic price action; often follows major events/transitions
- **Best approach**: reduce position sizes dramatically or avoid trading entirely — "this regime destroys retail traders who repeat previously successful strategies without recognizing environmental changes"

## Hidden Markov Models for Regime Detection

HMMs assume returns stem from an underlying system switching between hidden states, inferred only from observable data (returns, volatility). Components: transition matrix (probability of shifting regimes), emission distributions (return characteristics per regime), initial state probabilities. Baum-Welch estimates parameters from historical returns; Viterbi determines the most probable hidden-state sequence, yielding probabilistic regime assignments.

Why HMMs suit market regimes: regimes persist for days-to-weeks (transition probability back to choppy stays low), and probabilistic (not binary) assignment avoids whipsaw from a single anomalous day.

## The Efficiency Ratio — a fast, simple alternative

**Formula**: ER = |Net price change over N periods| / Sum of |individual period changes|. ER near 1.0 = efficient directional movement (trending); near 0 = erratic movement without net progress (choppy).

**Practical thresholds**: ER > 0.40 → trending, full strategy activation. ER 0.20–0.40 → neutral, standard sizing. ER < 0.20 → choppy, reduce sizing 50% or skip setups. Cheap enough to run every cycle.

## How Regime Changes Every Trading Parameter

- **Position sizing**: full allocation in trending regimes for momentum strategies; 30–50% cuts in choppy regimes; 70% cuts in transitioning regimes.
- **Strategy activation**: momentum/breakout signals suppress in choppy regimes; mean-reversion signals suppress in trending regimes.
- **Stop placement**: choppy regimes need wider stops to avoid whipsaw; trending regimes allow tighter stops.
- **Target selection**: trending regimes favor letting winners run (3x+ risk); ranging regimes prioritize quick profit-taking at known levels.

## Manual Regime Checks (no automated system required)

1. Slope check: 20-period EMA on a chart pointing up/flat/down
2. ADX evaluation: ADX >25 signals a trend; ADX <20 indicates choppy/ranging
3. Recent breakout follow-through: did the last 3–5 breakout attempts follow through?
4. Volatility index level: elevated volatility typically signals a regime where risk management should outweigh signal selection
