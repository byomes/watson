"""jobs/trading/verify_holdout_windows.py — One-off script (run once, by hand)
that pulls real SPY daily bars via Alpaca and computes actual metrics for the
three proposed holdout windows, instead of trusting assumption/memory about
what each period looked like. Output feeds jobs/trading/HOLDOUT_WINDOWS.md.

Usage: PYTHONPATH=<repo> venv/bin/python -m jobs.trading.verify_holdout_windows
"""
from datetime import date

from jobs.trading.alpaca_client import get_data_client

CANDIDATE_WINDOWS = {
    "crash_2020": ("2020-02-01", "2020-04-30"),
    "bear_2022": ("2022-01-01", "2022-12-31"),
    "calm_2019": ("2019-01-01", "2019-12-31"),
    "calm_2017_alt": ("2017-01-01", "2017-12-31"),
}


def fetch_spy_bars(start: str, end: str):
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = get_data_client()
    req = StockBarsRequest(
        symbol_or_symbols="SPY",
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(req).data["SPY"]
    return [
        {"date": b.timestamp.date().isoformat(), "close": b.close, "high": b.high, "low": b.low}
        for b in bars
    ]


def compute_metrics(bars: list[dict]) -> dict:
    if len(bars) < 2:
        return {"n_days": len(bars), "total_return_pct": None, "max_drawdown_pct": None, "ann_vol_pct": None}

    closes = [b["close"] for b in bars]
    total_return_pct = (closes[-1] / closes[0] - 1) * 100

    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        dd = (c - peak) / peak
        max_dd = min(max_dd, dd)

    daily_returns = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    mean_r = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    daily_vol = variance ** 0.5
    ann_vol_pct = daily_vol * (252 ** 0.5) * 100

    return {
        "n_days": len(bars),
        "start": bars[0]["date"],
        "end": bars[-1]["date"],
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "ann_vol_pct": round(ann_vol_pct, 2),
    }


def main():
    print(f"Fetching SPY bars for each candidate window as of {date.today().isoformat()}...\n")
    results = {}
    for name, (start, end) in CANDIDATE_WINDOWS.items():
        bars = fetch_spy_bars(start, end)
        metrics = compute_metrics(bars)
        results[name] = metrics
        print(f"{name:16s} {start} → {end}: {metrics}")
    return results


if __name__ == "__main__":
    main()
