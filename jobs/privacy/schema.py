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
            "note": "Step 1 of 5 wizard — steps 2-5 selectors not captured, form shape doesn't fit remove.py's single fill+submit assumption.",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive (2026-08-20 Phase 2). Real opt-out is a 5-step "
               "wizard, not a single form — remove.py's _submit_form() only fills "
               "fields then clicks one submit button. Needs multi-step submission "
               "support before activating. No CAPTCHA seen on step 1."),
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
            "name_field": "#topsearch", "city_state_field": "#name_city_state",
            "url_field": "#url-input", "url_step_submit": "button.get-url-btn",
            "email_field": "#user_email", "submit_button": "button.btn-sbmt",
            "note": "13-step wizard, all steps present in page HTML at once (JS "
                    "shows/hides) — selectors read from source without stepping "
                    "through the UI.",
        }),
        active=0,
        notes=("BLOCKING DECISION -> defaulted inactive (2026-08-20 Phase 2). 13-step wizard, far beyond "
               "remove.py's single fill+submit model. reCAPTCHA also gates the final "
               "(13th) step's submission specifically, alongside the step-count "
               "mismatch. Selectors saved for a future multi-step handler."),
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
        notes=("BLOCKING DECISION -> defaulted inactive (2026-08-20 Phase 2). Real flow is multi-step: "
               "submit email -> confirm via emailed link -> further fields on a "
               "later page not yet captured. Doesn't fit remove.py's single-step "
               "model. Shared PeopleConnect infrastructure with TruthFinder and "
               "USSearch (see those rows) — a single multi-step PeopleConnect "
               "handler would unlock all three at once, worth prioritizing as a "
               "fast-follow."),
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
        notes=("BLOCKING DECISION -> defaulted inactive (2026-08-20 Phase 2). Same multi-step "
               "PeopleConnect suppression flow as Intelius/USSearch — see "
               "Intelius's notes. privacy@truthfinder.com exists but is documented "
               "as a separate 'User Data Rights/CCPA' channel, not confirmed "
               "equivalent to a public-listing removal request, so not used as an "
               "email-method substitute."),
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
        notes=("BLOCKING DECISION -> defaulted inactive (2026-08-20 Phase 2). Same multi-step "
               "PeopleConnect suppression flow as Intelius/TruthFinder — see "
               "Intelius's notes. privacy@ussearch.com exists but is documented as "
               "a separate 'User Data Rights/CCPA' channel, not confirmed "
               "equivalent to a public-listing removal request, so not used as an "
               "email-method substitute."),
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
        seed_brokers(conn)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    create_tables()
    print("privacy_brokers, family_profiles, privacy_removals ready.")
