"""
Shared carrier/SMS-gateway cache — jobs.sms.carrier_lookup

Turns a phone number into number@carrier-gateway.com, caching the carrier once
resolved (manually or via NumVerify) so it's never looked up twice. Any Watson
job/skill can call get_sms_address() and get either a working address or None
(meaning: trigger a manual-confirm flow with the caller's own UI).
"""
import os
import re

import requests
from dotenv import load_dotenv

from core.database import get_connection

load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

NUMVERIFY_URL = "http://apilayer.net/api/validate"

CARRIER_GATEWAY_MAP = {
    "AT&T": "txt.att.net",
    "Verizon": "vtext.com",
    "T-Mobile": "tmomail.net",
    "Sprint": "messaging.sprintpcs.com",
    "Boost Mobile": "sms.myboostmobile.com",
    "Cricket": "sms.cricketwireless.net",
    "MetroPCS": "mymetropcs.com",
    "US Cellular": "email.uscc.net",
    "Google Fi": "msg.fi.google.com",
    "Xfinity Mobile": "vtext.com",  # rides Verizon network
}

# Substring aliases for free-text/NumVerify carrier names -> canonical map keys.
_CARRIER_ALIASES = {
    "AT&T": ("at&t", "att"),
    "Verizon": ("verizon", "cellco"),
    "T-Mobile": ("t-mobile", "tmobile", "t mobile"),
    "Sprint": ("sprint",),
    "Boost Mobile": ("boost",),
    "Cricket": ("cricket",),
    "MetroPCS": ("metropcs", "metro pcs", "metro by t-mobile", "metro"),
    "US Cellular": ("us cellular", "uscellular", "united states cellular"),
    "Google Fi": ("google fi", "project fi"),
    "Xfinity Mobile": ("xfinity",),
}


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS phone_carriers (
                phone_number TEXT PRIMARY KEY,
                carrier      TEXT,
                sms_gateway  TEXT,
                source       TEXT,
                confirmed    INTEGER DEFAULT 0,
                updated_at   TEXT
            )
        """)


_bootstrap()


def normalize_phone(raw: str) -> str | None:
    """Strip to 10 digits, drop leading 1/country code. None if not a valid US number."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits


def normalize_carrier_name(raw: str) -> str | None:
    """Map a free-text/API carrier name to a CARRIER_GATEWAY_MAP key, or None if unrecognized."""
    if not raw:
        return None
    if raw in CARRIER_GATEWAY_MAP:
        return raw
    low = raw.lower().strip()
    for canonical, aliases in _CARRIER_ALIASES.items():
        if any(alias in low for alias in aliases):
            return canonical
    return None


def save_carrier(phone_number: str, carrier: str, source: str, confirmed: bool) -> None:
    """Upsert into phone_carriers, resolving the gateway from the canonical carrier name."""
    digits = normalize_phone(phone_number) or phone_number
    canonical = normalize_carrier_name(carrier) or carrier
    gateway = CARRIER_GATEWAY_MAP.get(canonical)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO phone_carriers (phone_number, carrier, sms_gateway, source, confirmed, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(phone_number) DO UPDATE SET
                   carrier=excluded.carrier,
                   sms_gateway=excluded.sms_gateway,
                   source=excluded.source,
                   confirmed=excluded.confirmed,
                   updated_at=excluded.updated_at""",
            (digits, canonical, gateway, source, 1 if confirmed else 0),
        )


def _numverify_lookup(phone_digits: str, api_key: str) -> str | None:
    try:
        resp = requests.get(
            NUMVERIFY_URL,
            params={
                "access_key": api_key,
                "number": phone_digits,
                "country_code": "US",
                "format": 1,
            },
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if not data.get("valid"):
        return None
    return data.get("carrier") or None


def get_sms_address(phone_number: str) -> str | None:
    """
    1. Normalize the number.
    2. Reuse a cached row if one exists (regardless of confirmed status) — never
       burns NumVerify quota twice on the same number.
    3. If NUMVERIFY_KEY is set and nothing is cached, call NumVerify, cache the
       result with source='numverify', confirmed=0, and return the address.
    4. Otherwise None — caller must trigger a manual-confirm flow.
    """
    digits = normalize_phone(phone_number)
    if not digits:
        return None

    with get_connection() as conn:
        row = conn.execute(
            "SELECT sms_gateway FROM phone_carriers WHERE phone_number = ?",
            (digits,),
        ).fetchone()

    if row is not None:
        return f"{digits}@{row['sms_gateway']}" if row["sms_gateway"] else None

    api_key = os.getenv("NUMVERIFY_KEY")
    if not api_key:
        return None

    carrier = _numverify_lookup(digits, api_key)
    if not carrier:
        return None

    save_carrier(digits, carrier, source="numverify", confirmed=False)
    canonical = normalize_carrier_name(carrier)
    gateway = CARRIER_GATEWAY_MAP.get(canonical) if canonical else None
    return f"{digits}@{gateway}" if gateway else None
