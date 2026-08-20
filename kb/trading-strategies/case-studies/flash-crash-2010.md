# The Flash Crash of 2010: Trigger, Mechanism, and Lessons

Source: https://www.quantt.co.uk/resources/flash-crash-of-2010-explained
Fetched: 2026-08-20

## What Happened

On May 6, 2010, between 2:32pm and 2:47pm, the Dow Jones Industrial Average plummeted approximately 1,000 points (9%) within minutes, then largely recovered. Individual stocks traded at absurd extremes — some as low as one penny, others as high as $100,000 — during this 15-minute window.

## The Trigger

A Kansas-based mutual fund (Waddell & Reed) placed a $4.1 billion sell order for 75,000 E-mini S&P 500 futures contracts. The algorithm executing the order was set to participate at 9% of trading volume **without price or time controls**. As volatility spiked and volume accelerated, the algorithm fed increasingly large sell orders into the market, initiating a destructive cascade.

## The Cascade Mechanism

**Phase 1 (2:32–2:41pm)**: HFT market makers absorbed the initial futures selling, then hedged by selling underlying stocks.

**Phase 2 (2:41–2:45pm)**: HFT inventory limits reached capacity — firms aggressively offloaded positions instead of absorbing more risk, creating a "hot potato" effect: heavy volume with no genuine liquidity provision.

**Phase 3 (2:45–2:47pm)**: Liquidity evaporated. Many HFT systems triggered safety protocols and withdrew from quoting entirely, leaving no buyers.

**Phase 4 (2:47–3:00pm)**: A 5-second trading halt in E-mini futures allowed participant reassessment; algorithmic systems returned and prices snapped back.

## Contributing Causes (SEC-CFTC report)

- **Market fragmentation** — liquidity scattered across 13+ exchanges and dark pools, preventing any single venue from implementing effective circuit breakers.
- **No mandatory liquidity provision** — unlike traditional specialists, HFT firms faced no obligation to maintain market presence during stress.
- **Stop-loss cascades** — retail and institutional stop orders amplified the decline.
- **Stub quotes** — market makers posted nominal quotes ($0.01 bids, $99,999 offers) that satisfied quoting obligations without providing real liquidity.
- **Cross-asset feedback** — futures and equity markets reinforced each other's decline.

## Regulatory Response

Limit Up/Limit Down single-stock circuit breakers (halt trading on 5–10% moves within defined timeframes); market-wide circuit breakers at S&P 500 declines of 7/13/20%; stub-quote prohibition (quotes must stay within a reasonable percentage of prevailing spreads); consolidated audit trail (cross-venue trade reconstruction); pre-trade risk controls at brokers for unusually large/rapid orders.

## Enduring Lessons for Quantitative Trading

1. **Liquidity is conditional** — displayed liquidity vanishes precisely when it's needed most.
2. **Hedging assumptions break** — models assuming continuous hedging access proved dangerously naive.
3. **Feedback loops drive extremes** — a single trigger doesn't cause a 9% move; amplification mechanisms do.
4. **Algorithmic controls are essential** — modern safeguards surpass 2010 standards, but similar patterns persist.
5. **Cross-asset dynamics matter** — futures-cash, ETF-NAV, and FX-equity relationships drive systemic risk.

Subsequent flash events (2014, 2015, 2024) demonstrate these mechanisms remain relevant — fragmented market microstructure combined with algorithmic trading can still produce dramatic intraday dislocations.
