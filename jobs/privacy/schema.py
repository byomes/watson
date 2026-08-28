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
import json
import sqlite3

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
                      CHECK(status IN ('pending','approved','submitted','unconfirmed','failed','rejected')),
    failure_reason    TEXT,
    submitted_at      TEXT,
    next_rescan_at    TEXT,
    confirm_attempts  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now')),
    UNIQUE(person_id, broker_id, matched_url)
);
"""

CREATE_CANDIDATES = """
CREATE TABLE IF NOT EXISTS privacy_broker_candidates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    domain           TEXT NOT NULL UNIQUE,
    example_url      TEXT NOT NULL,
    example_snippet  TEXT,
    example_person   TEXT,
    match_count      INTEGER NOT NULL DEFAULT 1,
    confidence       REAL,
    status           TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','flagged','dismissed')),
    notified_at      TEXT,
    first_seen_at    TEXT DEFAULT (datetime('now')),
    last_seen_at     TEXT DEFAULT (datetime('now'))
);
"""

ALL_TABLES = [CREATE_BROKERS, CREATE_FAMILY_PROFILES, CREATE_REMOVALS, CREATE_CANDIDATES]

# Seed list — the 10 brokers from the spec, live-verified 2026-08-20 (Phase 2
# of this build: a read-only recon pass per broker's real opt-out page — no
# form was ever submitted, no CAPTCHA was ever solved). Only 2 of 10 came out
# active=1: Spokeo (single-step form) and BeenVerified (via its documented
# email fallback, since the form itself is CAPTCHA-gated at page load). The
# other 8 stay active=0, each for a specific, documented reason — several
# share one root cause (a multi-step opt-out wizard, which remove.py's
# current single fill+submit model can't drive) rather than remove.py being
# broken per-broker; see each row's notes for detail and matched_url_pattern
# note for a non-obvious real behavior worth reading rather than blindly
# flipping active=1 on any of them. Placeholders {first}/{last}/{state} in
# search_url_pattern are filled in by scan.py per family_profiles row.
_SEED_BROKERS = [
    dict(
        name="Spokeo",
        search_url_pattern="https://www.spokeo.com/{first}-{last}/{state}",
        opt_out_method="form",
        opt_out_target="https://www.spokeo.com/optout",
        form_selectors=json.dumps({
            "url_field": "input[name='url']",
            "email_field": "input[name='email']",
            "submit_button": "form[name='optout-form'] button[type='submit']",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive (2026-08-20, second pass). Verified live "
               "(Phase 2): single-page form (profile URL + email). An invisible reCAPTCHA key is "
               "present but did not block loading/inspecting the form — a dry-run fill-without-submit "
               "test (goto_safe + fill, stop before click) found real reCAPTCHA v2 challenge "
               "infrastructure loads on field interaction, no token auto-generated while idle; "
               "inconclusive either way on what happens at the actual click (see "
               "jobs/privacy/remove.py's dry_run mode). Separately, and more fundamentally: this "
               "page's own copy says completion requires clicking a link in a follow-up "
               "confirmation EMAIL ('To complete this process, we will send you a confirmation "
               "email. Please click the link in the email.') — so it's really a two-step flow, not "
               "single-step, and remove.py's success check ('the click didn't throw') can't verify "
               "real completion either way. See project_backlog id=37 and the comment at "
               "_submit_form()'s success return in remove.py. Kept inactive pending that decision — "
               "not a rejection of Spokeo, a paused one. Alt contact: privacy@spokeo.com."),
    ),
    dict(
        name="Whitepages",
        search_url_pattern="https://www.whitepages.com/name/{first}-{last}/{state}",
        opt_out_method="form",
        opt_out_target="https://www.whitepages.com/suppression-requests",
        form_selectors=json.dumps({
            "url_field": "#suppression-requests-person-url",
            "note": "Step 1 of 5 field only — see notes column, this is not a multi-step-support gap, it's a cannot-verify-without-a-real-submission gap.",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive, re-investigated 2026-08-20 (project_backlog "
               "id=38, feature/privacy-guard-wizard). Worse than a plain multi-step gap: static analysis "
               "of Whitepages' own loaded JS bundles (never executed, read-only) shows the VERY FIRST "
               "Next click (step 1->2) already fires a real backend GET (/api/person/details), and step "
               "2->3 actually CREATES the removal request server-side (POST /api/suppression-requests, "
               "returns a real requestId). Step 4->5 places a real outbound verification phone call. "
               "There is no harmless exploration path through this wizard at all — the point of no "
               "return is the first click, not a late final-submit button. Same category as Spokeo "
               "(project_backlog id=37): cannot verify a genuine success signal without creating a real "
               "request. _submit_wizard() (jobs/privacy/remove.py) exists but this broker was "
               "deliberately NOT configured with it — see that function's docstring for why."),
    ),
    dict(
        name="BeenVerified",
        search_url_pattern="https://www.beenverified.com/people/{first}-{last}/{state}/",
        opt_out_method="email",
        opt_out_target="privacy@beenverified.com",
        form_selectors=None,
        active=1,
        notes=("Verified live 2026-08-20 (Phase 2). Web opt-out form (beenverified.com/svc/optout/...) is fully "
               "Cloudflare CAPTCHA-gated at page load — no DOM was even reachable, "
               "so no selectors exist and the form path is not used. Activated "
               "instead via email to privacy@beenverified.com (documented contact "
               "on their site), which remove.py's existing 'email' branch already "
               "handles cleanly. /app/optout/search is also robots.txt-disallowed "
               "and was not attempted."),
    ),
    dict(
        name="MyLife",
        search_url_pattern="https://www.mylife.com/{first}-{last}/{state}",
        opt_out_method="form",
        opt_out_target="https://www.mylife.com/privacyrequest",
        form_selectors=json.dumps({
            "first_name_field": "#first_6", "last_name_field": "#last_6",
            "email_field": "#input_7", "url_field": "#input_12",
            "state_field": "#input_21", "city_field": "#input_11_city", "zip_field": "#input_11_postal",
            "birth_year_field": "#input_15", "submit_button": "#input_2",
            "note": "Selectors captured for a future manual/semi-manual path per spec "
                    "— not usable for automated submission while required reCAPTCHA "
                    "gates the actual submit.",
        }),
        active=0,
        notes=("CAPTCHA-gated per spec's explicit v1 exclusion rule (2026-08-20 Phase 2). Form itself "
               "loads/inspects fine, but submission requires a required, visible "
               "reCAPTCHA (name='recaptcha_visible') — not automatable in v1. "
               "Selectors saved above for a future manual/semi-manual path. "
               "membersupport@mylife.com is general support, not a confirmed "
               "dedicated removal channel, so not used as an email fallback."),
    ),
    dict(
        name="Radaris",
        search_url_pattern="https://radaris.com/p/{first}/{last}/",
        opt_out_method="form",
        opt_out_target="https://radaris.com/control-privacy",
        form_selectors=json.dumps({
            "_reference_only_not_wired_to_submit_wizard": True,
            "step1_intro_next": "div.js-funnel-next-btn",
            "step4_name_field": "#topsearch", "step4_city_state_field": "#name_city_state",
            "step4_url_field": "#url-input",
            "step4_url_submit_REAL_BACKEND_CALL": "button.get-url-btn",
            "step5_confirm_next": "css not captured — 'Start Removing' div, pure client-side per source read",
            "step13_email_field": "#user_email",
            "step13_submit_REAL_BACKEND_CALL_recaptcha_gated": "button.btn-sbmt",
            "step14_success_signal": "sendData()'s onload handler writes a real request_id + track_url "
                                      "from the JSON response into #step_14 .tracking — a genuine, "
                                      "verifiable completion signal, confirmed by reading source, not guessed",
            "note": "CORRECTED 2026-08-20 (project_backlog id=38): only 6 real steps (1,4,5,13,14,15) — "
                    "the JS STEPS object has keys 1-15 but 2,3,6-12 are commented-out dead code. Step 4's "
                    "profile-URL path (get-url-btn) and step 13's submit both make real backend POSTs "
                    "(/ng/control/a.check_url and /ng/control/a.remove_request respectively) — confirmed "
                    "by reading their handler source directly, neither was ever clicked. Not wired into "
                    "_submit_wizard()'s 'steps' schema: the branching at step 4 (name-search vs "
                    "profile-URL, two different backend endpoints) and the fact that step 4's own advance "
                    "is itself a real submission don't fit that function's linear "
                    "fill->next(safe)->...->submit(checked) model — see its docstring.",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive, re-investigated 2026-08-20 (project_backlog "
               "id=38, feature/privacy-guard-wizard). CORRECTED from the original Phase 2 read: this is "
               "NOT a 13-step wizard — only 6 steps are live (1,4,5,13,14,15), the rest is dead JS. "
               "Fully mapped now via reading client-side source directly (zero real-flow clicks needed) "
               "— including a genuine, verifiable success signal at step 14 (a real request_id + "
               "track_url written from the server's JSON response, not static text). This broker is "
               "blocked purely by the reCAPTCHA gating step 13's final submit — the spec's explicit v1 "
               "CAPTCHA exclusion rule, same reason as MyLife/Nuwber — NOT a cannot-verify-success gap "
               "like Whitepages/Spokeo/PeopleConnect. If the reCAPTCHA is ever handled some other way "
               "(a semi-manual path per the spec's own suggestion), the selectors and success check "
               "above are already known and verified."),
    ),
    dict(
        name="Intelius",
        search_url_pattern="https://www.intelius.com/people-search/{first}-{last}/{state}",
        opt_out_method="form",
        opt_out_target="https://suppression.peopleconnect.us/?brand=Intelius",
        form_selectors=json.dumps({
            "email_field": "input[name='login-email']", "consent_checkbox": "input[name='consent']",
            "submit_button": "form button[type='submit']",
            "note": "PeopleConnect's shared suppression tool (also covers "
                    "TruthFinder/USSearch/InstantCheckmate) — Step 1 (email+consent) "
                    "only; verification-link continuation to further name/address "
                    "fields was not reached.",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive, re-investigated 2026-08-20 (project_backlog "
               "id=38, feature/privacy-guard-wizard). Re-confirmed directly from the page's own static "
               "copy (no submission needed): \"Upon submission of your email address you will receive a "
               "verification email with a link to proceed.\" Step 1's submit already sends a real email — "
               "there is no way to safely explore step 2+ or determine a genuine success signal without "
               "triggering that send. Same category as Whitepages/Spokeo: cannot verify without a real "
               "submission, not a plain multi-step-support gap. Shared PeopleConnect infrastructure with "
               "TruthFinder and USSearch (see those rows) — this same blocker applies to all three "
               "identically."),
    ),
    dict(
        name="PeopleFinders",
        search_url_pattern="https://www.peoplefinders.com/people/{first}-{last}/{state}",
        opt_out_method="form",
        opt_out_target="https://www.peoplefinders.com/opt-out",
        form_selectors=None,
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive (2026-08-20 Phase 2). peoplefinders.com/robots.txt "
               "explicitly disallows /opt-out — goto_safe() refuses this URL "
               "automatically (Bill's standing policy: disallow rules are absolute, "
               "no working around them). The allowed /do-not-sell info page confirms "
               "the form exists and links to /opt-out, and lists two phone numbers "
               "((877) 551-9688 opt-out-specific, (800) 718-8997 general) as the only "
               "channels reachable without violating robots.txt — not automatable, "
               "same category as the spec's mail-only 'needs manual request' "
               "handling."),
    ),
    dict(
        name="TruthFinder",
        search_url_pattern="https://truthfinder.com/people-search/{first}-{last}-{state}/",
        opt_out_method="form",
        opt_out_target="https://suppression.peopleconnect.us/?brand=TruthFinder",
        form_selectors=json.dumps({
            "email_field": "input[name='login-email']", "consent_checkbox": "input[name='consent']",
            "submit_button": "form button[type='submit']",
            "note": "PeopleConnect's shared suppression tool (also covers "
                    "Intelius/USSearch/InstantCheckmate) — Step 1 (email+consent) "
                    "only; further steps not reached.",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive, re-investigated 2026-08-20 (project_backlog "
               "id=38). Same PeopleConnect suppression flow as Intelius — step 1's email submit already "
               "sends a real verification email (re-confirmed directly from the page's own copy, not "
               "guessed), so step 2+ and any genuine success signal are unreachable without triggering "
               "that. See Intelius's notes for the full finding. privacy@truthfinder.com exists but is "
               "documented as a separate 'User Data Rights/CCPA' channel, not confirmed equivalent to a "
               "public-listing removal request, so not used as an email-method substitute."),
    ),
    dict(
        name="USSearch",
        search_url_pattern="https://www.ussearch.com/people/{first}-{last}/{state}/",
        opt_out_method="form",
        opt_out_target="https://suppression.peopleconnect.us/?brand=USSearch",
        form_selectors=json.dumps({
            "email_field": "input[name='login-email']", "consent_checkbox": "input[name='consent']",
            "submit_button": "form button[type='submit']",
            "note": "PeopleConnect's shared suppression tool (also covers "
                    "Intelius/TruthFinder/InstantCheckmate) — Step 1 (email+consent) "
                    "only; further steps not reached.",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive, re-investigated 2026-08-20 (project_backlog "
               "id=38). Same PeopleConnect suppression flow as Intelius — step 1's email submit already "
               "sends a real verification email (re-confirmed directly from the page's own copy, not "
               "guessed), so step 2+ and any genuine success signal are unreachable without triggering "
               "that. See Intelius's notes for the full finding. privacy@ussearch.com exists but is "
               "documented as a separate 'User Data Rights/CCPA' channel, not confirmed equivalent to a "
               "public-listing removal request, so not used as an email-method substitute."),
    ),
    dict(
        name="Nuwber",
        search_url_pattern="https://nuwber.com/search?name={first}+{last}&state={state}",
        opt_out_method="form",
        opt_out_target="https://nuwber.com/removal/link",
        form_selectors=None,
        active=0,
        notes=("CAPTCHA-gated per spec's explicit v1 exclusion rule (2026-08-20 Phase 2). Opt-out page "
               "shows a full Cloudflare 'Verify you are human' challenge that blocks "
               "even viewing the form — no selectors obtainable. support@nuwber.com "
               "exists in the footer but is not confirmed as a designated removal "
               "channel, so not used as a fallback."),
    ),
]


def _migrate_removals_status_check(conn) -> None:
    """The fix/privacy-guard-unconfirmed-status merge (2026-08-21) added
    'unconfirmed' to privacy_removals' status CHECK constraint in
    CREATE_REMOVALS above, but CREATE TABLE IF NOT EXISTS never re-applies
    a changed CHECK to a table that already exists -- SQLite has no ALTER
    TABLE for constraints, only a full rebuild. Without this, the very
    first _mark_unconfirmed() call (jobs/privacy/remove.py) or Privacy
    Guard confirm.py attempt-tracking UPDATE would raise IntegrityError
    instead of landing, silently breaking backlog id=37's fix. Detected via
    sqlite_master.sql (not assumed), so the rebuild runs at most once, and
    only after confirm_attempts has already been added (see the ALTER
    TABLE ADD COLUMN above this call in create_tables) so the copy below
    can rely on that column existing."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='privacy_removals'"
    ).fetchone()
    if row is None or "'unconfirmed'" in row[0]:
        return  # table doesn't exist yet, or already has the fixed constraint
    conn.execute("ALTER TABLE privacy_removals RENAME TO privacy_removals_old")
    conn.execute(CREATE_REMOVALS)
    conn.execute(
        """INSERT INTO privacy_removals
           (id, person_id, broker_id, matched_url, matched_fields, confidence_score,
            status, failure_reason, submitted_at, next_rescan_at, confirm_attempts, created_at)
           SELECT id, person_id, broker_id, matched_url, matched_fields, confidence_score,
                  status, failure_reason, submitted_at, next_rescan_at, confirm_attempts, created_at
           FROM privacy_removals_old"""
    )
    conn.execute("DROP TABLE privacy_removals_old")


def seed_brokers(conn) -> None:
    for b in _SEED_BROKERS:
        conn.execute(
            """INSERT OR IGNORE INTO privacy_brokers
               (name, search_url_pattern, opt_out_method, opt_out_target, form_selectors, active, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (b["name"], b["search_url_pattern"], b["opt_out_method"], b["opt_out_target"],
             b["form_selectors"], b["active"], b["notes"]),
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
        try:
            conn.execute(
                "ALTER TABLE privacy_removals ADD COLUMN confirm_attempts INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass  # column already exists (pre-2026-08-21 databases)
        _migrate_removals_status_check(conn)
        seed_brokers(conn)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    create_tables()
    print("privacy_brokers, family_profiles, privacy_removals ready.")
