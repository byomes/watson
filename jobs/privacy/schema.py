"""jobs/privacy/schema.py — Schema for Privacy Guard
(privacy_brokers / family_profiles / privacy_removals).

Mirrors the jobs/campaigns/schema.py pattern: CREATE TABLE IF NOT EXISTS +
create_tables(conn=None), idempotent, PRAGMA table_info()-driven migrations
for any future column additions.

Deviation from the original spec (noted in the PR): `telegram_message_id`
is dropped from privacy_removals. Every approve/skip callback edits the
tapped message directly via python-telegram-bot's `query.message` (same
pattern as camp_approve / the merge-conflict resolution handlers) — no
later out-of-band lookup by message id is needed, and that column sits
unused on book_launch_sends for the same reason.
"""
from core.database import get_connection

CREATE_BROKERS = """
CREATE TABLE IF NOT EXISTS privacy_brokers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL UNIQUE,
    search_url_pattern TEXT NOT NULL,
    opt_out_method     TEXT NOT NULL CHECK(opt_out_method IN ('form','email','mail')),
    opt_out_target     TEXT,
    form_selectors     TEXT,
    active             INTEGER NOT NULL DEFAULT 1,
    notes              TEXT,
    created_at         TEXT DEFAULT (datetime('now'))
);
"""

CREATE_FAMILY_PROFILES = """
CREATE TABLE IF NOT EXISTS family_profiles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    relationship TEXT,
    birth_year   INTEGER,
    cities       TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now'))
);
"""

CREATE_REMOVALS = """
CREATE TABLE IF NOT EXISTS privacy_removals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id         INTEGER NOT NULL REFERENCES family_profiles(id),
    broker_id         INTEGER NOT NULL REFERENCES privacy_brokers(id),
    matched_url       TEXT,
    matched_fields    TEXT,
    confidence_score  REAL NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','approved','submitted','failed','rejected')),
    failure_reason    TEXT,
    submitted_at      TEXT,
    next_rescan_at    TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    UNIQUE(person_id, broker_id, matched_url)
);
"""

ALL_TABLES = [CREATE_BROKERS, CREATE_FAMILY_PROFILES, CREATE_REMOVALS]

# Seed list — the 10 brokers from the spec. Every opt_out_target/form_selectors
# value is NULL and every row starts active=0: none of this has been visited
# live yet. search_url_pattern and opt_out_method are best-effort guesses from
# public knowledge of each site's URL conventions, not confirmed — Phase 2 of
# this build (a live, read-only verification pass per broker) corrects
# whatever's wrong and only then flips active=1, broker by broker. Placeholders
# {first}/{last}/{state} are filled in by scan.py per family_profiles row.
_NEEDS_VERIFICATION = (
    "NEEDS VERIFICATION — search_url_pattern/opt_out_method are unverified "
    "guesses; opt_out_target and form_selectors are not yet captured. Do not "
    "flip active=1 until confirmed live (see Privacy Guard build plan, Phase 2)."
)

_SEED_BROKERS = [
    ("Spokeo", "https://www.spokeo.com/{first}-{last}/{state}", "form"),
    ("Whitepages", "https://www.whitepages.com/name/{first}-{last}/{state}", "form"),
    ("BeenVerified", "https://www.beenverified.com/people/{first}-{last}/{state}/", "form"),
    ("MyLife", "https://www.mylife.com/{first}-{last}/{state}", "form"),
    ("Radaris", "https://radaris.com/p/{first}/{last}/", "form"),
    ("Intelius", "https://www.intelius.com/people-search/{first}-{last}/{state}", "form"),
    ("PeopleFinders", "https://www.peoplefinders.com/people/{first}-{last}/{state}", "form"),
    ("TruthFinder", "https://truthfinder.com/people-search/{first}-{last}-{state}/", "form"),
    ("USSearch", "https://www.ussearch.com/people/{first}-{last}/{state}/", "form"),
    ("Nuwber", "https://nuwber.com/search?name={first}+{last}&state={state}", "form"),
]


def seed_brokers(conn) -> None:
    for name, url_pattern, method in _SEED_BROKERS:
        conn.execute(
            """INSERT OR IGNORE INTO privacy_brokers
               (name, search_url_pattern, opt_out_method, active, notes)
               VALUES (?, ?, ?, 0, ?)""",
            (name, url_pattern, method, _NEEDS_VERIFICATION),
        )


def create_tables(conn=None) -> None:
    """Create all three privacy_* tables in watson.db (idempotent) and seed
    the 10 broker rows (INSERT OR IGNORE, keyed on the UNIQUE name column —
    safe to call repeatedly, never overwrites a broker already verified)."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        for stmt in ALL_TABLES:
            conn.execute(stmt)
        seed_brokers(conn)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    create_tables()
    print("privacy_brokers, family_profiles, privacy_removals ready.")
