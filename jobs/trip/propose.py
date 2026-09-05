"""jobs/trip/propose.py — Romantic 3-Day Trip Finder.

Proposes flight+hotel getaway options for Bill and Donna, delivered by
private Telegram DM only (never Kaci, Donna herself, or any group — this is
a personal trip, not a church matter). Watson proposes, never books:
'approved' just marks a proposal starred, there is no downstream booking
call anywhere in this module.

Two entrypoints into the same core:
  - `run(message)` — on-demand, called by the skill router from a Telegram/
    dashboard trigger phrase. Kicks off propose_trip() in a background
    thread (Amadeus + Ollama calls are slow) and returns an immediate ack.
  - `python3 jobs/trip/propose.py` — cron entrypoint (monthly "surprise us"
    run), calls propose_trip() directly/synchronously.

TUNABLE DEFAULTS — none of these are load-bearing product decisions, just
starting points. Adjust freely:
  - ORIGIN_AIRPORTS: Philly/BWI, per the original spec.
  - DESTINATIONS: a fixed shortlist rather than a computed flight-time
    radius (matches the spec's "pick a shortlist" approach). Swap/extend
    the list to taste.
  - TRIP_NIGHTS: "3-day" read as a long weekend, 2 nights (Fri depart /
    Sun return). Change to "3,3" in cheapest_dates() calls for 3 nights
    literally if that's what was meant.
  - EXCLUDED_CHAIN_CODES: approximate big-chain GDS codes. Amadeus's exact
    `chainCode` values per hotel need verifying against a real response —
    this list is a starting filter, not a verified one.
  - MIN_HOTEL_RATING / MAX_HOTEL_NIGHTLY_USD / MAX_FLIGHT_ROUNDTRIP_USD:
    placeholder budget/quality bars, tune to actual preference.
"""
import json
import logging
import threading

import requests

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate
from jobs.trip.amadeus_client import AmadeusNotConfigured, cheapest_dates, hotel_offers, hotels_by_city
from jobs.trip.schema import create_tables
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

log = logging.getLogger(__name__)

ORIGIN_AIRPORTS = ["PHL", "BWI"]

# city, airport/city code (assumed equal for these single-airport markets —
# verify against Amadeus's Airport & City Search API on first live run).
DESTINATIONS = [
    {"city": "Charleston, SC", "code": "CHS"},
    {"city": "Savannah, GA", "code": "SAV"},
    {"city": "Asheville, NC", "code": "AVL"},
    {"city": "New Orleans, LA", "code": "MSY"},
    {"city": "Nashville, TN", "code": "BNA"},
    {"city": "Quebec City, QC", "code": "YQB"},
    {"city": "Montreal, QC", "code": "YUL"},
    {"city": "Bermuda", "code": "BDA"},
    {"city": "Nassau, Bahamas", "code": "NAS"},
    {"city": "Key West, FL", "code": "EYW"},
]

TRIP_DURATION_NIGHTS = "2,3"  # passed straight to Amadeus cheapest_dates()

EXCLUDED_CHAIN_CODES = {
    "MC",  # Marriott
    "HI", "IC",  # IHG / Holiday Inn
    "BW",  # Best Western
    "WY",  # Wyndham
    "CI",  # Comfort Inn / Choice
    "RA",  # Radisson
}
MIN_HOTEL_RATING = 4
MAX_HOTEL_NIGHTLY_USD = 400
MAX_FLIGHT_ROUNDTRIP_USD = 700  # per person

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"  # accuracy-sensitive tier, off the hot Telegram path

_TASTE_SYSTEM_PROMPT = (
    "You help pick a romantic weekend-getaway hotel for a married couple. "
    "You are given a short list of hotel candidates (name, rating, nightly "
    "price, and any description/amenities text). Pick the single best one "
    "for a romantic 2-3 night trip — favor boutique character, walkable "
    "location, and an adults-oriented feel over generic business hotels. "
    "Reply with JSON only, no prose, no markdown fences: "
    '{"chosen_index": <int>, "blurb": "<one sentence, why this one>"}'
)


# ── Amadeus search + rules prefilter ──────────────────────────────────────

def _cheapest_flight(destination_code: str) -> dict | None:
    best = None
    for origin in ORIGIN_AIRPORTS:
        try:
            offers = cheapest_dates(origin, destination_code, TRIP_DURATION_NIGHTS)
        except Exception as exc:
            log.warning("cheapest_dates(%s -> %s) failed: %s", origin, destination_code, exc)
            continue
        for offer in offers:
            price = float(offer.get("price", {}).get("total", "inf"))
            if best is None or price < best["price"]:
                best = {
                    "origin": origin,
                    "depart_date": offer.get("departureDate"),
                    "return_date": offer.get("returnDate"),
                    "price": price,
                }
    return best


def _candidate_hotels(city_code: str, check_in: str, check_out: str) -> list:
    try:
        hotel_list = hotels_by_city(city_code)
    except Exception as exc:
        log.warning("hotels_by_city(%s) failed: %s", city_code, exc)
        return []
    hotel_ids = [h["hotelId"] for h in hotel_list if h.get("hotelId")][:50]
    if not hotel_ids:
        return []
    try:
        offers = hotel_offers(hotel_ids, check_in, check_out)
    except Exception as exc:
        log.warning("hotel_offers(%s) failed: %s", city_code, exc)
        return []

    candidates = []
    for entry in offers:
        hotel = entry.get("hotel", {})
        offer = (entry.get("offers") or [{}])[0]
        chain_code = hotel.get("chainCode")
        rating = hotel.get("rating")
        price = offer.get("price", {}).get("total")
        if chain_code and chain_code in EXCLUDED_CHAIN_CODES:
            continue
        if rating is not None and float(rating) < MIN_HOTEL_RATING:
            continue
        if price is not None and float(price) > MAX_HOTEL_NIGHTLY_USD:
            continue
        candidates.append({
            "name": hotel.get("name"),
            "chain_code": chain_code,
            "rating": rating,
            "price": price,
            "currency": offer.get("price", {}).get("currency"),
            "description": (hotel.get("description") or {}).get("text", ""),
        })
    return candidates


# ── Ollama taste pass ──────────────────────────────────────────────────────

def _call_ollama(system: str, prompt: str, timeout: int = 300) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "system": system, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def _parse_json(raw: str):
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _pick_romantic_hotel(candidates: list) -> dict | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        chosen = candidates[0]
        chosen["blurb"] = ""
        return chosen

    listing = "\n".join(
        f'{i}. "{c["name"]}" — rating {c["rating"]}, ${c["price"]}/night. {c["description"][:200]}'
        for i, c in enumerate(candidates)
    )
    raw = _call_ollama(_TASTE_SYSTEM_PROMPT, listing)
    parsed = _parse_json(raw)
    if not parsed or "chosen_index" not in parsed:
        chosen = candidates[0]
        chosen["blurb"] = ""
        return chosen

    idx = parsed["chosen_index"]
    if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
        chosen = candidates[0]
        chosen["blurb"] = ""
        return chosen

    chosen = candidates[idx]
    chosen["blurb"] = parsed.get("blurb", "")
    return chosen


# ── Proposal build + persist + send ────────────────────────────────────────

def propose_trip() -> list:
    """Core flow: search every destination in the shortlist, build one
    proposal per destination that clears the rules filter, insert as
    'pending', and send each as its own Telegram card. Returns the list of
    inserted proposal rows (dicts)."""
    create_tables()

    proposals = []
    for dest in DESTINATIONS:
        flight = _cheapest_flight(dest["code"])
        if not flight or flight["price"] > MAX_FLIGHT_ROUNDTRIP_USD:
            continue

        candidates = _candidate_hotels(dest["code"], flight["depart_date"], flight["return_date"])
        hotel = _pick_romantic_hotel(candidates)
        if not hotel:
            continue

        row = {
            "destination_city": dest["city"],
            "destination_airport": dest["code"],
            "origin_airport": flight["origin"],
            "depart_date": flight["depart_date"],
            "return_date": flight["return_date"],
            "flight_price": flight["price"],
            "flight_currency": "USD",
            "hotel_name": hotel.get("name"),
            "hotel_chain_code": hotel.get("chain_code"),
            "hotel_rating": hotel.get("rating"),
            "hotel_price_per_night": hotel.get("price"),
            "hotel_currency": hotel.get("currency"),
            "blurb": hotel.get("blurb", ""),
        }
        proposal_id = _insert_proposal(row)
        row["id"] = proposal_id
        proposals.append(row)
        _send_for_review(row)

    if not proposals:
        _send_no_results()

    return proposals


def _insert_proposal(row: dict) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO trip_proposals
               (destination_city, destination_airport, origin_airport,
                depart_date, return_date, flight_price, flight_currency,
                hotel_name, hotel_chain_code, hotel_rating,
                hotel_price_per_night, hotel_currency, blurb)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["destination_city"], row["destination_airport"], row["origin_airport"],
                row["depart_date"], row["return_date"], row["flight_price"], row["flight_currency"],
                row["hotel_name"], row["hotel_chain_code"], row["hotel_rating"],
                row["hotel_price_per_night"], row["hotel_currency"], row["blurb"],
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _proposal_card_text(row: dict) -> str:
    lines = [
        f"✈️ Romantic getaway idea: {row['destination_city']}",
        f"{row['origin_airport']} → {row['destination_airport']}, "
        f"{row['depart_date']} to {row['return_date']}",
        f"Flight: ~${row['flight_price']:.0f} {row['flight_currency']}",
    ]
    if row.get("hotel_name"):
        lines.append(
            f"Hotel: {row['hotel_name']} — ${row['hotel_price_per_night']:.0f}/night, "
            f"rating {row.get('hotel_rating', '?')}"
        )
    if row.get("blurb"):
        lines.append(row["blurb"])
    return "\n".join(lines)


def _proposal_keyboard(proposal_id: int) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Like it", "callback_data": f"trip_approve:{proposal_id}"},
            {"text": "❌ Pass", "callback_data": f"trip_reject:{proposal_id}"},
        ]]
    }


def _send_for_review(row: dict) -> None:
    text = _proposal_card_text(row)
    if vacation_gate("normal", "jobs.trip.propose._send_for_review", text):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.warning("WATSON_BOT_TOKEN / WATSON_CHAT_ID not set — cannot send trip proposal.")
        return
    requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": WATSON_CHAT_ID,
            "text": text,
            "reply_markup": _proposal_keyboard(row["id"]),
        },
        timeout=10,
    )


def _send_no_results() -> None:
    text = "Checked the usual romantic-getaway spots — nothing cleared the price/quality bar this time."
    if vacation_gate("normal", "jobs.trip.propose._send_no_results", text):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json={"chat_id": WATSON_CHAT_ID, "text": text},
        timeout=10,
    )


# ── Approve / reject (called from bot.py callback handler) ────────────────

def approve_trip(proposal_id: int) -> dict | None:
    """Marks a proposal 'approved' — a starred pick, nothing more. Watson
    never books, so there is no downstream action here."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM trip_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE trip_proposals SET status='approved', decided_at=datetime('now') WHERE id=?",
            (proposal_id,),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def reject_trip(proposal_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM trip_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE trip_proposals SET status='rejected', decided_at=datetime('now') WHERE id=?",
            (proposal_id,),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


# ── Skill-router entrypoint ────────────────────────────────────────────────

def run(message: str = "") -> str:
    """Called by jobs/skillbuilder/router.py on trigger-phrase match. The
    actual search is slow (multiple Amadeus + one Ollama call per
    destination), so it runs in a background thread and this returns
    immediately; results land as separate Telegram DM cards."""
    try:
        from jobs.trip.amadeus_client import get_access_token
        get_access_token()
    except AmadeusNotConfigured as exc:
        return str(exc)

    threading.Thread(target=propose_trip, daemon=True).start()
    return "On it — checking flights and hotels for a romantic getaway. I'll send you options here shortly."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = propose_trip()
    print(f"Sent {len(results)} trip proposal(s).")
