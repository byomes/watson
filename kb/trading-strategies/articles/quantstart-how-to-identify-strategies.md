# How to Identify Algorithmic Trading Strategies

Source: https://www.quantstart.com/articles/How-to-Identify-Algorithmic-Trading-Strategies/
Fetched: 2026-08-21

## Understanding Your Personal Trading Profile

Being aware of your own personality is the most critical consideration before seeking strategies — trading requires discipline, patience, and emotional detachment.

Key personal factors: **time availability** (employment situation determines appropriate strategy frequency), **ongoing research commitment** (strategies eventually become widely known and lose their edge), **trading capital** (recommended minimum $50K, $100K more realistic for absorbing transaction costs during drawdowns; under $10K restricts you to low-frequency strategies in limited assets), **programming ability** (own infrastructure awareness enables higher-frequency approaches), **financial goals** (income dependence requires higher-frequency, lower-volatility strategies with better Sharpe ratios), and **realistic expectations** ("algo trading is NOT a get-rich-quick scheme").

## Building a Strategy Pipeline

Establish "a strategy pipeline that will provide us with a stream of ongoing trading ideas" rather than randomly pursuing ideas — this generates consistent ideas while providing a framework for rejecting unsuitable strategies with minimal emotional bias.

### Sources for Trading Ideas

- **Textbooks**: foundational concepts and simpler strategies.
- **Trading communities**: blogs/forums often rely on technical analysis, which quants should evaluate rigorously with statistics rather than accepting at face value.
- **Academic sources**: pre-print servers (arXiv, SSRN) and journals contain sophisticated strategies — but academic strategies often ignore realistic transaction costs, slippage, and spreads, requiring independent replication and backtesting before validation.
- **Custom development**: requires expertise in market microstructure/order book dynamics, fund-structure behavioral constraints, and ML/AI applications.

## Evaluating Strategy Viability

### Understanding the strategy

Ask: can you explain it concisely without endless caveats? Does it have a rational behavioral or structural basis that could withstand regulatory changes? Complex statistical rules that work only on specific asset classes raise overfitting concerns.

### Performance characteristics

- **Sharpe ratio** — reward/risk ratio; higher-frequency strategies typically achieve better Sharpe ratios but require more sophistication.
- **Win/loss ratios and average profit/loss** — momentum strategies may win less often but generate outsized gains from major moves; mean-reversion strategies win more often with smaller average profits but potentially severe losses.
- **Maximum drawdown** — momentum strategies are well known to suffer extended drawdowns; honestly assess what duration/magnitude you can tolerate psychologically.
- **Leverage requirements** — margin call risk and heightened volatility.
- **Frequency and volatility** — impacts capital requirements, technology complexity, and achievable Sharpe.
- **Parameters and optimization bias** — "every extra parameter that a strategy requires leaves it more vulnerable to optimisation bias." Target minimal parameters, or ensure sufficient data for rigorous testing.

### Rejection criteria

Filter out incompatibility with capital constraints, leverage limits, drawdown tolerance, or volatility preferences *before* expensive backtesting.

## Assessing Available Historical Data

Data type (fundamental, news/sentiment, price), frequency requirements (daily vs. tick-level dramatically raises cost/complexity), cost/storage, data quality ("once accuracy and cleanliness are included and statistical biases removed, the data can become expensive"), and technology stack all factor into whether a strategy is even feasible to properly test.

## Final Assessment

"In isolation, the returns actually provide us with limited information as to the effectiveness of the strategy." Evaluate risk-adjusted metrics, leverage, benchmarks, and capital efficiency comprehensively — not raw returns alone.
