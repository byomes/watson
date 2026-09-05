"""jobs/trading/alpaca_client.py — Alpaca client factory, paper trading only.

Hard safety guard: every client built here refuses to run at all unless
ALPACA_BASE_URL still names the paper-trading host. `paper=True` is also
passed as a hardcoded literal to TradingClient below — never sourced from
config — so no .env edit anywhere can flip this job into live trading.
"""
from config.settings import ALPACA_API_KEY_ID, ALPACA_SECRET_KEY, ALPACA_BASE_URL

PAPER_HOST = "paper-api.alpaca.markets"


def _assert_paper_only() -> None:
    if PAPER_HOST not in (ALPACA_BASE_URL or ""):
        raise RuntimeError(
            f"jobs/trading refuses to run: ALPACA_BASE_URL={ALPACA_BASE_URL!r} "
            f"does not point at {PAPER_HOST}. This job is paper-trading only, "
            f"by design, always — fix the .env value, do not bypass this check."
        )
    if not ALPACA_API_KEY_ID or not ALPACA_SECRET_KEY:
        raise RuntimeError(
            "jobs/trading refuses to run: ALPACA_API_KEY_ID/ALPACA_SECRET_KEY missing from .env."
        )


def get_trading_client():
    """Alpaca TradingClient — account/order operations. paper=True is a
    hardcoded literal, not read from config, on purpose."""
    _assert_paper_only()
    from alpaca.trading.client import TradingClient

    return TradingClient(ALPACA_API_KEY_ID, ALPACA_SECRET_KEY, paper=True)


def get_data_client():
    """Alpaca StockHistoricalDataClient — historical bar data. Market data
    itself isn't paper/live-scoped, but this still refuses to run unless the
    paper-only guard passes, so a live-account credential swap in .env can't
    silently start pulling data under this job's identity either."""
    _assert_paper_only()
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(ALPACA_API_KEY_ID, ALPACA_SECRET_KEY)


if __name__ == "__main__":
    client = get_trading_client()
    account = client.get_account()
    print(f"Paper account {account.account_number}: equity=${account.equity}, status={account.status}")
