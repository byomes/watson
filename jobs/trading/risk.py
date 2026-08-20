"""jobs/trading/risk.py — Hard-coded risk limits. These are module-level
constants, not config — they are not meant to be tunable via .env or the
dashboard, on purpose. Any code (backtest engine now, a future live-forward
loop later) that sizes an order or tracks equity must call through these
functions rather than reimplementing the checks.
"""
from jobs.trading.db import get_connection

MAX_POSITION_PCT = 0.02      # Max 2% of account equity in a single position
MAX_DAILY_LOSS_PCT = 0.03    # Max 3% daily loss before halting for the day
MAX_DRAWDOWN_PCT = 0.15      # Max 15% drawdown from peak before a full stop


def check_position_size(order_value: float, account_equity: float) -> bool:
    """True if a position of this dollar value is within the 2%-per-position cap."""
    if account_equity <= 0:
        return False
    return (order_value / account_equity) <= MAX_POSITION_PCT


def max_position_value(account_equity: float) -> float:
    """The largest single-position dollar value allowed right now."""
    return account_equity * MAX_POSITION_PCT


def check_daily_loss(day_start_equity: float, current_equity: float) -> bool:
    """True if today's loss is still within the 3% daily cap (True = OK to keep
    trading; False = must halt for the day)."""
    if day_start_equity <= 0:
        return True
    loss_pct = (day_start_equity - current_equity) / day_start_equity
    return loss_pct < MAX_DAILY_LOSS_PCT


def check_drawdown(peak_equity: float, current_equity: float) -> bool:
    """True if drawdown from peak is still within the 15% cap (True = OK;
    False = full stop, requires manual dashboard re-approval to resume)."""
    if peak_equity <= 0:
        return True
    dd_pct = (peak_equity - current_equity) / peak_equity
    return dd_pct < MAX_DRAWDOWN_PCT


def get_risk_state() -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def update_equity(current_equity: float) -> dict:
    """Call once per trading day (or per backtest bar) with the latest equity.
    Updates peak_equity and evaluates the daily-loss / drawdown limits, moving
    risk_state.status to 'daily_halt' or 'drawdown_stop' as needed. Returns the
    resulting risk_state row. A 'drawdown_stop' never clears itself — see
    resume_from_drawdown_stop()."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
        state = dict(row)

        peak_equity = max(state["peak_equity"] or current_equity, current_equity)
        day_start_equity = state["day_start_equity"] or current_equity

        new_status = state["status"]
        requires_approval = state["requires_manual_approval"]

        if state["status"] != "drawdown_stop":
            if not check_drawdown(peak_equity, current_equity):
                new_status = "drawdown_stop"
                requires_approval = 1
            elif not check_daily_loss(day_start_equity, current_equity):
                new_status = "daily_halt"

        conn.execute(
            """UPDATE risk_state SET peak_equity=?, day_start_equity=?, status=?,
               requires_manual_approval=?, halted_at=CASE WHEN ? != status THEN datetime('now') ELSE halted_at END,
               updated_at=datetime('now') WHERE id=1""",
            (peak_equity, day_start_equity, new_status, requires_approval, new_status, ),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone())
    finally:
        conn.close()


def start_new_day(equity: float) -> None:
    """Reset the daily-loss tracker at the start of a new trading day. A
    'daily_halt' clears automatically on a new day; a 'drawdown_stop' does
    not — that one requires resume_from_drawdown_stop()."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM risk_state WHERE id = 1").fetchone()
        new_status = "active" if row["status"] == "daily_halt" else row["status"]
        conn.execute(
            "UPDATE risk_state SET day_start_equity=?, status=?, updated_at=datetime('now') WHERE id=1",
            (equity, new_status),
        )
        conn.commit()
    finally:
        conn.close()


def resume_from_drawdown_stop() -> None:
    """The only way a 'drawdown_stop' clears — explicit, manual, dashboard-only
    (see jobs/trading/routes.py). Never called automatically."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE risk_state SET status='active', requires_manual_approval=0,
               resumed_at=datetime('now'), updated_at=datetime('now') WHERE id=1"""
        )
        conn.commit()
    finally:
        conn.close()
