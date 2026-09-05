"""jobs/trading/holdout.py — Single source of truth for the three sealed
holdout windows. Every other module (training/holdout data split, evaluation,
dashboard) imports HOLDOUT_WINDOWS from here — never redefines its own copy.

See HOLDOUT_WINDOWS.md for how these were verified against real SPY data
and why calm_2017 was used instead of the originally-proposed calm_2019.
"""

# name -> (start_date, end_date), both inclusive, ISO format.
HOLDOUT_WINDOWS = {
    "crash_2020": ("2020-02-01", "2020-04-30"),
    "bear_2022": ("2022-01-01", "2022-12-31"),
    "calm_2017": ("2017-01-01", "2017-12-31"),
}
