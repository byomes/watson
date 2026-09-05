# "Sell in May" — A Cautionary Tale on Seasonal Anomalies

Source: https://quantpedia.com/strategies/market-seasonality-effect-in-world-equity-indexes
Fetched: 2026-08-21

Filed as a case study, not a strategy recommendation — this effect's own
research now questions whether it was ever real. Directly relevant to
this pipeline's own findings this session (a large parameter search
producing benchmark-beating results that turned out to be structural
artifacts, not real edge).

## The Effect

"Sell in May and Go Away" (the Halloween indicator): global equities historically returned close to zero from May-October and delivered nearly all their risk premium November-April. A study of 108 markets over 319 years found winter returns 4.52% higher than summer on average, rising to 6.25% over the past 50 years — one of the most-replicated seasonal patterns in finance, present in 36 of 37 markets studied in one landmark paper. Reportedly beat the market over 80% of the time on 5-year horizons, historically.

## Proposed Explanations

- **Seasonal affective disorder**: shorter fall/winter days linked to depression → heightened risk aversion → reduced equity appetite.
- **Optimism-cycle theory**: year-end optimism about economic prospects drives appreciation; several months in, that optimism becomes hard to sustain, producing a summer lull.
- **Regulatory-disclosure seasonality**: SEC filing volumes 17% higher in winter, insider trading up 22%, activist activity up 12% in the same months — information flow itself may be seasonal.

## The Actual Point of Including This

Despite centuries of apparently robust historical data and multiple plausible causal stories, **recent research finds the effect has "strongly weakened or even diminished in recent years,"** with out-of-sample backtests showing "significantly negative performance" — leading researchers to question whether the original findings reflected genuine exploitable structure or **data mining** across a long enough history that *something* was bound to look statistically significant.

This is the same failure mode this pipeline hit directly this session: a wide-enough search (here, centuries of calendar data; there, 447 parameter combinations) will reliably surface patterns that look statistically real and then evaporate out of sample. A long, robust-looking historical track record is not immunity from this — "Sell in May" had centuries of data and multiple behavioral theories behind it, and current research still can't confirm it's exploitable going forward. Any future strategy for this pipeline should be judged by the same standard this anomaly is now failing: does it hold up in genuinely new, unseen data, or only in the data that was searched to find it.
