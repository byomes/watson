# Long-Term Capital Management Collapse: The 1998 LTCM Disaster

Source: https://www.quantt.co.uk/resources/long-term-capital-management-collapse
Fetched: 2026-08-20

Note: an academic paper on the same collapse also exists at
https://eml.berkeley.edu/~webfac/craine/e137_f03/137lessons.pdf, located
via search but not machine-readable through the available fetch tooling
(PDF text extraction failed) — not ingested. This entry is sourced from the
Quantt.co.uk explainer only.

## Overview

In September 1998, Long-Term Capital Management (Greenwich, CT) lost $4.6 billion in under four months. The fund's positions were large enough to threaten systemic financial collapse, prompting a Federal-Reserve-organized $3.6 billion bailout. LTCM is the canonical example of quantitative sophistication combined with extreme leverage and underestimated correlation risk producing catastrophic failure.

## The All-Star Team

Founded 1994 by John Meriwether (former head of bond arbitrage, Salomon Brothers). Partners included Myron Scholes and Robert Merton (1997 Nobel laureates in economics), David Mullins (former Federal Reserve Vice Chairman), and star traders from Salomon's bond arbitrage desk. Launched with $1.25 billion — the largest hedge fund debut at the time, with $10 million minimum investments and three-year lockups.

## Core Strategy: Convergence Trading

Identified pairs of related securities that had diverged in price, betting on reconvergence: on-the-run vs. off-the-run Treasury spreads, European sovereign bond spread convergence, mortgage-backed security relative value, short long-dated S&P 500 index option volatility, merger-arbitrage spreads. Each individual trade generated only basis-point returns — the fund used massive leverage to make the overall return attractive.

## The Leverage Trap

Roughly 25:1 balance-sheet leverage. With $4.7 billion equity, the fund controlled ~$125 billion in assets; including off-balance-sheet derivatives, notional exposure exceeded $1 trillion (~5% of global GDP at the time). Counterparties extended this leverage because the partners' credibility (Nobel laureates, ex-Fed leadership) reassured banks into generous financing terms.

## Early Success, Then the Fatal Mistake

Returns: 1994 (partial) 20%, 1995 43%, 1996 41%, 1997 17%. By 1997 the fund had $7 billion in equity. The partners returned $2.7 billion to outside investors to capture more upside for themselves — maintaining the same leverage level on reduced equity, which significantly increased the effective leverage ratio.

## The Collapse: August–September 1998

**Trigger**: Russia defaulted on sovereign debt and devalued the ruble, August 1998 — markets had assumed such a default was impossible.

**Mechanism**: A global "flight to quality" panic — investors dumped riskier/illiquid assets (emerging-market debt, off-the-run Treasuries, corporate bonds) and bought safe/liquid ones (on-the-run Treasuries, German bunds).

**Why LTCM suffered**: nearly every position was structured long the less-liquid/riskier asset, short the more-liquid/safer asset — the flight to quality moved both sides of every trade against the fund simultaneously.

**Critical failure**: historical correlation models had under-estimated *tail* correlation. In normal times many positions showed low correlation; during stress, assets correlated toward 1.0 as markets faced a synchronized liquidity shock. By late August the fund was losing $100M+/day; any attempt to sell depressed prices further; counterparties demanded more collateral. By mid-September, ~$4 billion lost in three months, leaving $600 million equity against $125+ billion in positions.

## The Bailout

Unrestricted liquidation threatened a systemic crisis — Goldman Sachs estimated potential counterparty losses at $200 billion. On September 23, 1998, the NY Fed convened LTCM's largest counterparties (also the major investment banks) and compelled a $3.6 billion injection for 90% ownership. The fund was wound down over subsequent years.

## Core Lessons

1. **Leverage multiplies hidden risks** — volatility scales linearly with leverage, but tail risk scales non-linearly.
2. **Stress correlation differs from normal correlation** — historical matrices underestimate tail correlation; supposedly uncorrelated assets move together during liquidity crises.
3. **Liquidity is conditional** — positions liquid in normal markets become illiquid in stress; models assuming continuous liquidity fail during crises.
4. **Capacity constraints bind in crises** — massive positions can't be unwound without moving markets against you.
5. **Prestige provides no protection** — Nobel laureates and ex-Fed leadership did not prevent systematic risk blindness.
6. **Counterparty risk concentrates silently** — banks holding LTCM collateral didn't know each other held similar exposure.

## Aftermath

Post-LTCM reforms: improved hedge-fund disclosure, enhanced counterparty risk management, more rigorous stress testing, eventually Dodd-Frank and the Volcker Rule. John Meriwether founded JWM Associates (1999), shut down in 2009 after 2008-crisis losses — the same failure pattern repeated.

## Lessons for Modern Quants

Stress testing is mandatory (explicitly model correlations approaching 1, liquidity disappearing, credit withdrawing); position sizing trumps alpha (a flawed strategy at proper scale outperforms a brilliant strategy at destructive leverage); leverage multiplies concealed risk non-linearly; models are simplifications that break when reality diverges from their assumptions; overconfidence is the occupational hazard for highly intelligent practitioners who trust their models excessively.
