"""jobs/privacy/scan.py — Privacy Guard weekly scan: find new listings for
family_profiles on active privacy_brokers, and rescan brokers where a prior
removal was submitted to see if the listing has come back.

Cron: 30 5 * * * (nightly). On-demand: `python -m jobs.privacy.scan`.

Never submits anything to a broker — this job is read-only. Actual removal
submission only ever happens through jobs/privacy/remove.py, gated by a
Telegram approve tap on a match this job surfaces.
"""
import argparse
import asyncio
import json
import logging
import re
from datetime import date

from bs4 import BeautifulSoup

from core.database import get_connection
from jobs.browser.browser_service import get_page, goto_safe, log_browser_failure
from jobs.privacy import send_telegram
from jobs.privacy.schema import create_tables

log = logging.getLogger(__name__)

MATCH_CONFIDENCE_THRESHOLD = 0.7
# Above this many new pending matches in one run, batch into a single
# Telegram message with one button-row per match instead of flooding the
# chat with individual messages — mirrors the spec's stated fallback.
_DIGEST_BATCH_THRESHOLD = 5

_CITY_STATE_RE = re.compile(r"([A-Z][A-Za-z.\-' ]{1,40}),\s*([A-Z]{2})\b")
_AGE_RE = re.compile(r"\bage[s]?\.?\s*:?\s*(\d{2})\s*[-–]\s*(\d{2})\b", re.I)


def _name_parts(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) < 2:
        return parts[0], ""
    return parts[0], parts[-1]


def _build_search_url(pattern: str, first: str, last: str, state: str) -> str:
    return pattern.format(first=first.lower(), last=last.lower(), state=(state or "").lower())


def _find_listing_candidates(html: str, page_url: str, full_name: str) -> list[dict]:
    """Broker-agnostic heuristic: unlike the opt-out FORM (which gets real,
    live-verified CSS selectors per broker in privacy_brokers.form_selectors
    — see schema.py), search-RESULTS pages aren't given per-broker selectors
    here. Result-page markup varies a lot between brokers and changes often
    on redesigns; parsing visible text around each literal occurrence of the
    person's name is slower to break than a brittle per-broker CSS selector
    would be, at the cost of being a cruder signal — acceptable since
    scan.py only ever proposes a match for a human (Bill) to approve, it
    never acts on one itself.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    matches = list(re.finditer(re.escape(full_name), text, re.IGNORECASE))
    candidates = []
    for i, m in enumerate(matches):
        # Block starts at the match itself (no backward lookback) and runs
        # forward to the next match, if any. Broker result cards put
        # age/city AFTER the name ("Name, Age XX-XX, City, ST"), so a
        # forward-only window can't leak the previous listing's trailing
        # fields into this one — an earlier version that looked backward
        # past matches[i-1].end() did exactly that on compact/packed result
        # layouts, silently giving two different listings identical (and
        # for one of them, wrong) extracted city/state/age.
        start = m.start()
        end = min(len(text), m.end() + 300)
        if i + 1 < len(matches):
            end = min(end, matches[i + 1].start())
        block = text[start:end]
        city_state = _CITY_STATE_RE.search(block)
        age_match = _AGE_RE.search(block)
        candidates.append({
            "name": full_name,
            "city": city_state.group(1).strip() if city_state else None,
            "state": city_state.group(2) if city_state else None,
            "age_min": int(age_match.group(1)) if age_match else None,
            "age_max": int(age_match.group(2)) if age_match else None,
            # Fragment-disambiguated so two different matching listings on
            # the same search-results page don't collide on the
            # UNIQUE(person_id, broker_id, matched_url) constraint.
            "url": f"{page_url}#match-{i}",
            "snippet": block.strip()[:300],
        })
    return candidates


def _score_match(person_name: str, cities: list[dict], birth_year, listing: dict) -> float:
    """Name match is required just to reach this function (candidates are
    only ever produced around a literal name occurrence) — base score
    reflects that, then city/state overlap and birth-year-range
    plausibility add confidence on top."""
    score = 0.5
    if listing.get("city") and listing.get("state"):
        for c in cities:
            if (c.get("city") or "").strip().lower() == listing["city"].strip().lower() \
               and (c.get("state") or "").strip().upper() == listing["state"].strip().upper():
                score += 0.3
                break
    if birth_year and listing.get("age_min") is not None and listing.get("age_max") is not None:
        implied_age = date.today().year - birth_year
        if listing["age_min"] - 1 <= implied_age <= listing["age_max"] + 1:
            score += 0.2
    return min(score, 1.0)


async def _fetch_listings_for(person: dict, broker: dict) -> list[dict] | None:
    """Returns None on fetch failure (robots.txt disallow, navigation error,
    etc.) so callers can distinguish "couldn't check" from "checked, no
    match" — a real [] result. Never raises."""
    first, last = _name_parts(person["name"])
    cities = json.loads(person["cities"]) if isinstance(person["cities"], str) else person["cities"]
    primary_state = (cities[0].get("state") if cities else "") or ""
    url = _build_search_url(broker["search_url_pattern"], first, last, primary_state)
    async with get_page() as page:
        ok = await goto_safe(page, url, wait_until="networkidle")
        if not ok:
            return None
        try:
            html = await page.content()
        except Exception as exc:
            log_browser_failure(f"privacy.scan fetch content ({broker['name']})", url, exc)
            return None
    return _find_listing_candidates(html, url, person["name"])


async def _new_match_pass(conn) -> list[int]:
    new_ids = []
    people = conn.execute("SELECT * FROM family_profiles WHERE active=1").fetchall()
    brokers = conn.execute("SELECT * FROM privacy_brokers WHERE active=1").fetchall()

    attempts = 0
    failures = 0
    for person in people:
        person = dict(person)
        cities = json.loads(person["cities"])
        for broker in brokers:
            broker = dict(broker)
            attempts += 1
            listings = await _fetch_listings_for(person, broker)
            if listings is None:
                failures += 1
                continue
            for listing in listings:
                score = _score_match(person["name"], cities, person["birth_year"], listing)
                if score < MATCH_CONFIDENCE_THRESHOLD:
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO privacy_removals
                       (person_id, broker_id, matched_url, matched_fields, confidence_score, status)
                       VALUES (?, ?, ?, ?, ?, 'pending')""",
                    (person["id"], broker["id"], listing["url"], json.dumps(listing), score),
                )
                if cur.rowcount:
                    new_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            # Commit after each broker attempt rather than once at the end of
            # the whole run — this loop awaits a real network fetch per
            # iteration, so leaving a write transaction open across all of
            # them held a lock for the entire multi-minute run and starved
            # every other writer (bug_tracker: Privacy Guard scan locking).
            conn.commit()

    if attempts and failures == attempts:
        send_telegram(
            f"⚠️ Privacy Guard: every broker fetch failed this run ({failures}/{attempts}) — "
            "possible outage, check logs/privacy_scan.log.",
            priority="system_failure",
        )
    return new_ids


async def _rescan_pass(conn) -> list[int]:
    new_ids = []
    due = conn.execute(
        """SELECT r.*, p.name AS person_name, p.cities AS person_cities, p.birth_year AS person_birth_year,
                  b.name AS broker_name, b.search_url_pattern, b.active AS broker_active
           FROM privacy_removals r
           JOIN family_profiles p ON p.id = r.person_id
           JOIN privacy_brokers b ON b.id = r.broker_id
           WHERE r.status IN ('submitted','unconfirmed') AND r.next_rescan_at <= datetime('now')"""
    ).fetchall()

    # Commit after every row (including the early-continue branches below)
    # rather than once at the end — each iteration awaits a real network
    # fetch, so leaving one write transaction open across the whole pass
    # held a lock for the entire run and starved every other writer
    # (bug_tracker: Privacy Guard scan locking).
    for row in due:
        row = dict(row)
        if not row["broker_active"]:
            # Broker deactivated since this was submitted (e.g. flagged
            # CAPTCHA-gated later) — push the check out rather than fetch.
            conn.execute(
                "UPDATE privacy_removals SET next_rescan_at=datetime('now','+7 days') WHERE id=?",
                (row["id"],),
            )
            conn.commit()
            continue

        person = {"name": row["person_name"], "cities": row["person_cities"], "birth_year": row["person_birth_year"]}
        broker = {"name": row["broker_name"], "search_url_pattern": row["search_url_pattern"]}
        listings = await _fetch_listings_for(person, broker)
        if listings is None:
            continue  # transient fetch failure — retried on next week's run

        cities = json.loads(person["cities"])
        scored = [
            (listing, _score_match(person["name"], cities, person["birth_year"], listing))
            for listing in listings
        ]
        best_listing, best_score = max(scored, key=lambda t: t[1], default=(None, 0.0))

        if best_score < MATCH_CONFIDENCE_THRESHOLD:
            # Still gone — leave status as-is (submitted or unconfirmed), push the next check out.
            conn.execute(
                "UPDATE privacy_removals SET next_rescan_at=datetime('now','+7 days') WHERE id=?",
                (row["id"],),
            )
        else:
            # Back — a new pending row, history on the old submitted row stays intact.
            cur = conn.execute(
                """INSERT OR IGNORE INTO privacy_removals
                   (person_id, broker_id, matched_url, matched_fields, confidence_score, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (row["person_id"], row["broker_id"], best_listing["url"], json.dumps(best_listing), best_score),
            )
            if cur.rowcount:
                new_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        # Same reasoning as _new_match_pass: commit per row, not once at the
        # end, so this loop's per-row network fetch doesn't hold a write
        # lock across the whole rescan pass.
        conn.commit()
    return new_ids


def _digest_row(conn, removal_id: int):
    return conn.execute(
        """SELECT r.id, r.confidence_score, r.matched_url, p.name AS person_name, b.name AS broker_name
           FROM privacy_removals r
           JOIN family_profiles p ON p.id = r.person_id
           JOIN privacy_brokers b ON b.id = r.broker_id
           WHERE r.id=?""",
        (removal_id,),
    ).fetchone()


def _send_digest(removal_ids: list[int]) -> None:
    if not removal_ids:
        return
    conn = get_connection()
    try:
        if len(removal_ids) > _DIGEST_BATCH_THRESHOLD:
            lines = [f"🔍 Privacy Guard — {len(removal_ids)} new matches"]
            keyboard = []
            for rid in removal_ids:
                row = _digest_row(conn, rid)
                lines.append(f"  • {row['person_name']} on {row['broker_name']} ({int(row['confidence_score']*100)}%)")
                keyboard.append([
                    {"text": f"✅ Approve #{rid}", "callback_data": f"priv_approve:{rid}"},
                    {"text": f"⏭ Skip #{rid}", "callback_data": f"priv_skip:{rid}"},
                ])
            send_telegram("\n".join(lines), reply_markup={"inline_keyboard": keyboard})
        else:
            for rid in removal_ids:
                row = _digest_row(conn, rid)
                text = (
                    f"🔍 Privacy Guard match\n"
                    f"{row['person_name']} found on {row['broker_name']} "
                    f"({int(row['confidence_score']*100)}% confidence)\n{row['matched_url']}"
                )
                keyboard = [[
                    {"text": "✅ Approve removal", "callback_data": f"priv_approve:{rid}"},
                    {"text": "⏭ Skip", "callback_data": f"priv_skip:{rid}"},
                ]]
                send_telegram(text, reply_markup={"inline_keyboard": keyboard})
    finally:
        conn.close()


async def run() -> int:
    create_tables()
    conn = get_connection()
    try:
        new_ids = await _new_match_pass(conn)
        rescan_ids = await _rescan_pass(conn)
    finally:
        conn.close()

    all_new = new_ids + rescan_ids
    if all_new:
        _send_digest(all_new)
        print(f"Privacy Guard: {len(all_new)} new pending match(es) — digest sent.")
    else:
        print("Privacy Guard: nothing new this run.")
    return len(all_new)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    argparse.ArgumentParser(description="Privacy Guard weekly scan (new-match + rescan passes).").parse_args()
    asyncio.run(run())
