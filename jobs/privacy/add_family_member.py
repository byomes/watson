"""jobs/privacy/add_family_member.py — One-time CLI for Bill to populate
family_profiles. Per the Privacy Guard build spec this data is never
scraped or inferred — Bill runs this himself with real names/years/cities.

Usage:
    python -m jobs.privacy.add_family_member \\
        --name "Jane Yomes" --relationship spouse --birth-year 1980 \\
        --city "Wilmington,DE" --city "Newark,DE,past"

--city is repeatable: "City,ST" for a current city, "City,ST,past" for a
former one.
"""
import argparse
import json

from core.database import get_connection
from jobs.privacy.schema import create_tables


def _parse_city(raw: str) -> dict:
    parts = [p.strip() for p in raw.split(",")]
    city, state = parts[0], parts[1] if len(parts) > 1 else ""
    current = len(parts) < 3 or parts[2].lower() != "past"
    return {"city": city, "state": state, "current": current}


def add_family_member(name: str, relationship: str, birth_year, cities: list[dict]) -> int:
    create_tables()
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO family_profiles (name, relationship, birth_year, cities) VALUES (?, ?, ?, ?)",
            (name, relationship, birth_year, json.dumps(cities)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add one family member to Privacy Guard's family_profiles.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--relationship", choices=["self", "spouse", "child"], required=True)
    parser.add_argument("--birth-year", type=int, default=None)
    parser.add_argument(
        "--city", action="append", required=True, dest="cities",
        help='Repeatable: "City,ST" (current) or "City,ST,past" (former).',
    )
    args = parser.parse_args()
    parsed_cities = [_parse_city(c) for c in args.cities]
    new_id = add_family_member(args.name, args.relationship, args.birth_year, parsed_cities)
    print(f"Added family_profiles id={new_id}: {args.name}")
