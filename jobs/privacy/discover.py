"""jobs/privacy/discover.py — Privacy Guard weekly discovery pass: find NEW
data-broker sites Privacy Guard doesn't already know about, distinct from
jobs/privacy/scan.py which only ever checks the 10 brokers already in
privacy_brokers.

Runs a real (non-site-restricted) Serper search per active family member —
"<name>" <city> <state> — the same shape a person-search indexer would rank
for. Any result domain not already in privacy_brokers gets judged by the
local Ollama model against a genuine people-search/data-broker page vs. news,
social media, business listings, or a public-figure mention — same
never-guess judgment pattern as jobs/curator/research.py's judge_spice_rating.
Only domains the model is confident about get stored, in
privacy_broker_candidates — a proposal queue, not privacy_brokers itself.

This job NEVER adds a row to privacy_brokers, never fetches/investigates a
candidate's opt-out flow, and never changes `active`. A candidate only moves
forward when Bill taps "Investigate" on the weekly Telegram digest, which
files a project_backlog item — the same manual, per-broker investigation
every one of the current 10 brokers went through before being trusted (see
project_backlog id=37/38, jobs/privacy/schema.py's _SEED_BROKERS notes).

No browser/Playwright involved — pure Serper + Ollama — so this doesn't hit
the goto_safe()/networkidle timeout issues jobs/privacy/scan.py sees.

Cron: 45 5 * * 0 (Sunday, after scan.py's daily 5:30am slot). On-demand:
`python -m jobs.privacy.discover`.
"""
import argparse
import json
import logging
import re
from urllib.parse import urlparse

import requests

from core.database import get_connection
from jobs.privacy import send_telegram
from jobs.privacy.schema import create_tables
from jobs.research.web_search import search as serper_search
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"  # accuracy-sensitive background judgment — see LLM Stack in WATSON_ARCHITECTURE.md

CONFIDENCE_THRESHOLD = 0.6
RESULTS_PER_PERSON = 10

# Skipped without spending an Ollama call — obviously not a data broker.
# Optimization only, not a safety mechanism: everything else still goes
# through real classification, nothing is assumed a broker off a denylist
# miss.
_PLATFORM_DENYLIST = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "wikipedia.org", "google.com", "maps.google.com",
    "amazon.com", "reddit.com", "pinterest.com", "tiktok.com", "yelp.com",
    "github.com", "apple.com", "microsoft.com",
}

_SYSTEM_PROMPT = (
    "You judge whether a web search result is a people-search / data-broker / "
    "background-check page about a private individual — the kind of site that "
    "aggregates public records into a personal profile (possible addresses, "
    "phone numbers, relatives, age) and typically offers a 'full report' or "
    "'view profile' action. You are NOT judging a news article, a social media "
    "profile, a business/professional listing, an obituary, a genealogy/family-"
    "tree research site, or a mention of a public figure — none of those count, "
    "even if they contain the person's name and some personal detail. You NEVER "
    "guess: if the title/snippet doesn't clearly show broker-page characteristics, "
    "you say so. Return only valid JSON, no other text."
)


def call_ollama(system: str, prompt: str, timeout: int = 60) -> str:
    payload = {
        "model": MODEL, "system": system, "prompt": prompt,
        "stream": False, "options": {"temperature": 0},
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def _parse_json(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _known_broker_domains(conn) -> set[str]:
    rows = conn.execute("SELECT search_url_pattern FROM privacy_brokers").fetchall()
    return {_domain(r["search_url_pattern"]) for r in rows}


def _existing_candidate_status(conn, domain: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM privacy_broker_candidates WHERE domain=?", (domain,)
    ).fetchone()
    return row["status"] if row else None


def classify_candidate(person_name: str, domain: str, title: str, snippet: str) -> dict:
    """Never guesses: a failed/unparseable model call or a low-confidence
    judgment both return is_broker=False rather than surfacing an unverified
    guess to Bill."""
    prompt = f"""Person being searched: {person_name}
Search result domain: {domain}
Result title: {title}
Result snippet: {snippet}

Does this result look like a people-search / data-broker / background-check
page about this specific private individual?

Return JSON exactly in this shape:
{{
  "is_broker": true or false,
  "confidence": a number from 0.0 to 1.0,
  "reason": "one short sentence"
}}"""
    try:
        raw = call_ollama(_SYSTEM_PROMPT, prompt)
        parsed = _parse_json(raw)
    except Exception as exc:
        log.warning("classify_candidate Ollama call failed for %s: %s", domain, exc)
        parsed = None

    if not parsed or "is_broker" not in parsed:
        return {"is_broker": False, "confidence": 0.0, "reason": "classification failed"}
    return parsed


def _name_parts(full_name: str) -> tuple[str, str]:
    """First + last only, same as jobs/privacy/scan.py's _name_parts() — a
    broker listing is never going to contain someone's full legal name with
    2-3 middle names as a literal phrase, so quoting the raw family_profiles
    `name` field (confirmed live: zero Serper results for a 4-part name)
    would silently make this job find nothing."""
    parts = full_name.strip().split()
    if len(parts) < 2:
        return parts[0], ""
    return parts[0], parts[-1]


def _search_for_person(person: dict) -> list[dict]:
    cities = json.loads(person["cities"]) if isinstance(person["cities"], str) else person["cities"]
    city = (cities[0].get("city") if cities else "") or ""
    state = (cities[0].get("state") if cities else "") or ""
    first, last = _name_parts(person["name"])
    query = f'"{first} {last}" {city} {state}'.strip()
    return serper_search(query, max_results=RESULTS_PER_PERSON)


def _upsert_candidate(conn, domain: str, url: str, snippet: str, person_name: str, confidence: float) -> bool:
    """Returns True if this is a brand-new candidate row (worth digesting)."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO privacy_broker_candidates
           (domain, example_url, example_snippet, example_person, confidence)
           VALUES (?, ?, ?, ?, ?)""",
        (domain, url, snippet, person_name, confidence),
    )
    if cur.rowcount:
        return True
    conn.execute(
        """UPDATE privacy_broker_candidates
           SET match_count = match_count + 1, last_seen_at = datetime('now')
           WHERE domain=?""",
        (domain,),
    )
    return False


def run() -> int:
    create_tables()
    conn = get_connection()
    new_ids = []
    try:
        known_domains = _known_broker_domains(conn)
        people = conn.execute("SELECT * FROM family_profiles WHERE active=1").fetchall()

        for person in people:
            person = dict(person)
            seen_this_person = set()
            for result in _search_for_person(person):
                url = result.get("url", "")
                domain = _domain(url)
                if not domain or domain in known_domains or domain in _PLATFORM_DENYLIST:
                    continue
                if domain in seen_this_person:
                    continue
                seen_this_person.add(domain)

                status = _existing_candidate_status(conn, domain)
                if status == "dismissed":
                    continue  # Bill already said no — never resurfaced
                if status is not None:
                    # Already a known candidate (new/flagged) — just bump
                    # match_count/last_seen, no need to re-classify.
                    _upsert_candidate(conn, domain, url, result.get("snippet", ""), person["name"], None)
                    continue

                verdict = classify_candidate(
                    person["name"], domain, result.get("title", ""), result.get("snippet", "")
                )
                if not verdict.get("is_broker") or verdict.get("confidence", 0) < CONFIDENCE_THRESHOLD:
                    continue

                is_new = _upsert_candidate(
                    conn, domain, url, result.get("snippet", ""), person["name"], verdict.get("confidence")
                )
                if is_new:
                    row = conn.execute(
                        "SELECT id FROM privacy_broker_candidates WHERE domain=?", (domain,)
                    ).fetchone()
                    new_ids.append(row["id"])
            conn.commit()  # per-person, not once at the end — same reasoning as scan.py

        _send_digest(conn, new_ids)
    finally:
        conn.close()

    print(f"Privacy Guard discover: {len(new_ids)} new candidate broker(s).")
    return len(new_ids)


def _send_digest(conn, new_ids: list[int]) -> None:
    to_notify = conn.execute(
        "SELECT * FROM privacy_broker_candidates WHERE status='new' AND notified_at IS NULL ORDER BY id"
    ).fetchall()
    if not to_notify:
        return

    lines = [f"🔎 Privacy Guard — {len(to_notify)} possible new broker site(s) found this week"]
    keyboard = []
    for row in to_notify:
        lines.append(
            f"  • {row['domain']} — found via {row['example_person']}'s search "
            f"({int((row['confidence'] or 0) * 100)}% confidence)\n    {row['example_url']}"
        )
        keyboard.append([
            {"text": f"🔎 Investigate {row['domain']}", "callback_data": f"pgcand_flag:{row['id']}"},
            {"text": "🚫 Not relevant", "callback_data": f"pgcand_skip:{row['id']}"},
        ])
    send_telegram("\n".join(lines), reply_markup={"inline_keyboard": keyboard})

    ids = [row["id"] for row in to_notify]
    conn.executemany(
        "UPDATE privacy_broker_candidates SET notified_at=datetime('now') WHERE id=?",
        [(i,) for i in ids],
    )
    conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    argparse.ArgumentParser(description="Privacy Guard weekly new-broker discovery pass.").parse_args()
    run()
