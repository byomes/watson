"""jobs/trading/schema.py — Schema for the paper-trading strategy development
pipeline (trading.db — separate from watson.db/congregation.db/donors.db/curator.db).
"""
from jobs.trading.db import get_connection

CREATE_DAILY_BARS = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL NOT NULL,
    high   REAL NOT NULL,
    low    REAL NOT NULL,
    close  REAL NOT NULL,
    volume INTEGER NOT NULL,
    PRIMARY KEY (symbol, date)
);
"""

CREATE_RISK_STATE = """
CREATE TABLE IF NOT EXISTS risk_state (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
    status                 TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'daily_halt', 'drawdown_stop')),
    peak_equity            REAL,
    day_start_equity       REAL,
    halted_at              TEXT,
    resumed_at             TEXT,
    requires_manual_approval INTEGER NOT NULL DEFAULT 0,
    updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Keep this family list in sync with jobs/trading/strategies/templates.py's
# TEMPLATES dict. SQLite can't ALTER a CHECK constraint in place — adding a
# new family value to a live DB needs the table-recreate migration in
# _migrate_strategies_family_check() below, not just editing this string.
CREATE_STRATEGIES = """
CREATE TABLE IF NOT EXISTS strategies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    family     TEXT NOT NULL CHECK (family IN ('ma_crossover', 'mean_reversion', 'momentum', 'time_series_momentum')),
    params_json TEXT NOT NULL,
    rationale  TEXT,
    status     TEXT NOT NULL DEFAULT 'proposed'
               CHECK (status IN ('proposed', 'training_tested', 'holdout_tested', 'passed', 'failed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_BACKTEST_RUNS = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id        INTEGER REFERENCES strategies(id),
    symbol             TEXT NOT NULL DEFAULT 'SPY',
    window_label       TEXT NOT NULL,
    start_date         TEXT NOT NULL,
    end_date           TEXT NOT NULL,
    return_pct         REAL NOT NULL,
    max_drawdown_pct   REAL NOT NULL,
    sharpe             REAL,
    win_rate           REAL,
    benchmark_return_pct REAL NOT NULL,
    rationale          TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Sealed by construction: strategy_id UNIQUE means a strategy can be tested
# against the three holdout windows at most once, ever — see evaluate.py.
CREATE_HOLDOUT_TESTS = """
CREATE TABLE IF NOT EXISTS holdout_tests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id      INTEGER NOT NULL UNIQUE REFERENCES strategies(id),
    window_results_json TEXT NOT NULL,
    windows_beaten   INTEGER NOT NULL,
    any_outright_loss INTEGER NOT NULL,
    overall_pass     INTEGER NOT NULL,
    tested_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

ALL_TABLES = [
    CREATE_DAILY_BARS,
    CREATE_RISK_STATE,
    CREATE_STRATEGIES,
    CREATE_BACKTEST_RUNS,
    CREATE_HOLDOUT_TESTS,
]


def _migrate_strategies_family_check(conn) -> None:
    """SQLite can't ALTER a CHECK constraint in place — recreate the table
    with the wider CHECK, copy every row across (ids preserved, so
    backtest_runs/holdout_tests foreign keys still resolve — FK constraints
    aren't enforced by this connection anyway, but keeping ids stable is
    still the correct thing to do), drop the old, rename the new one in.
    Guarded by inspecting the live CHECK text so this only runs once ever,
    and is a safe no-op if the table doesn't exist yet (fresh installs get
    the right CHECK straight from CREATE_STRATEGIES above)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='strategies'"
    ).fetchone()
    if not row or "time_series_momentum" in row[0]:
        return
    conn.execute("""
        CREATE TABLE strategies_new (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            family     TEXT NOT NULL CHECK (family IN ('ma_crossover', 'mean_reversion', 'momentum', 'time_series_momentum')),
            params_json TEXT NOT NULL,
            rationale  TEXT,
            status     TEXT NOT NULL DEFAULT 'proposed'
                       CHECK (status IN ('proposed', 'training_tested', 'holdout_tested', 'passed', 'failed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("INSERT INTO strategies_new SELECT * FROM strategies")
    conn.execute("DROP TABLE strategies")
    conn.execute("ALTER TABLE strategies_new RENAME TO strategies")


def create_tables(conn=None) -> None:
    """Create all trading.db tables (idempotent — CREATE TABLE IF NOT EXISTS)."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        for stmt in ALL_TABLES:
            conn.execute(stmt)
        _migrate_strategies_family_check(conn)

        # rejected_orders: how many buy/sell orders backtrader rejected on
        # margin during this run — added after a real bug (time_series_momentum's
        # original 100%-of-portfolio sizing left no room for next-bar price
        # movement/slippage, silently zeroing every order and producing a
        # flat 0% result indistinguishable from a genuine "no signal" case).
        # A plain additive column, not a CHECK-constraint change, so a
        # simple guarded ALTER TABLE is enough — no table recreation needed.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()}
        if "rejected_orders" not in cols:
            conn.execute("ALTER TABLE backtest_runs ADD COLUMN rejected_orders INTEGER NOT NULL DEFAULT 0")

        conn.execute(
            "INSERT OR IGNORE INTO risk_state (id, status) VALUES (1, 'active')"
        )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    create_tables()
    print(f"trading.db schema ready.")
