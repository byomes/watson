"""
Data audit: duplicate member detection and field inconsistency correction.
"""

import difflib
import os
import re
import sqlite3
from collections import Counter

from bs4 import BeautifulSoup

from jobs.connect_cards.reports import _conn

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

_ALLOWED_FIELDS = {"name", "email", "phone", "campus_preference"}


def _ensure_audit_schema():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_exemptions (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              member_a_id INTEGER NOT NULL,
              member_b_id INTEGER NOT NULL,
              created_at  TEXT DEFAULT (datetime('now')),
              UNIQUE(member_a_id, member_b_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_ensure_audit_schema()


def _strip_phone(phone) -> str:
    return re.sub(r"\D", "", phone or "")


def _member_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.id, m.name, m.email, m.phone, m.campus_preference,
               (SELECT COUNT(*) FROM connect_cards WHERE member_id = m.id) AS card_count,
               MAX(
                 COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), ''),
                 COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '')
               ) AS last_seen
        FROM members m
        WHERE m.status != 'inactive'
        ORDER BY m.id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def find_likely_duplicates() -> list[dict]:
    """
    Find pairs of member records that are probably the same person.
    """
    with _conn() as conn:
        members = _member_rows(conn)

    pairs: dict[tuple, dict] = {}

    def _add(a: dict, b: dict, reason: str, confidence: str):
        lo, hi = (a, b) if a["id"] < b["id"] else (b, a)
        key = (lo["id"], hi["id"])
        if key in pairs:
            existing = pairs[key]["match_reason"]
            if reason not in existing:
                pairs[key]["match_reason"] = existing + "+" + reason
            if confidence == "high":
                pairs[key]["confidence"] = "high"
        else:
            pairs[key] = {"a": lo, "b": hi, "match_reason": reason, "confidence": confidence}

    # 1. Same email (case-insensitive)
    email_map: dict[str, list] = {}
    email_ids: set[int] = set()
    for m in members:
        e = (m["email"] or "").strip().lower()
        if e:
            email_map.setdefault(e, []).append(m)
    for grp in email_map.values():
        if len(grp) >= 2:
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    _add(grp[i], grp[j], "email", "high")
                    email_ids |= {grp[i]["id"], grp[j]["id"]}

    # 2. Same phone (digits only)
    phone_map: dict[str, list] = {}
    phone_ids: set[int] = set()
    for m in members:
        p = _strip_phone(m["phone"])
        if len(p) >= 7:
            phone_map.setdefault(p, []).append(m)
    for grp in phone_map.values():
        if len(grp) >= 2:
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    _add(grp[i], grp[j], "phone", "high")
                    phone_ids |= {grp[i]["id"], grp[j]["id"]}

    # 3. Fuzzy name (only candidates not already matched by email/phone)
    matched = email_ids | phone_ids
    candidates = [m for m in members if m["id"] not in matched and m["name"]]
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            ratio = difflib.SequenceMatcher(
                None,
                candidates[i]["name"].lower().strip(),
                candidates[j]["name"].lower().strip(),
            ).ratio()
            if ratio >= 0.82:
                conf = "high" if ratio >= 0.92 else "medium"
                _add(candidates[i], candidates[j], "name", conf)

    # Filter pairs exempted by "Keep Separate"
    conn = sqlite3.connect(DB_PATH)
    try:
        exempt = {
            (r[0], r[1])
            for r in conn.execute("SELECT member_a_id, member_b_id FROM audit_exemptions").fetchall()
        }
    finally:
        conn.close()

    result = [p for p in pairs.values() if (p["a"]["id"], p["b"]["id"]) not in exempt]
    result.sort(key=lambda p: (0 if p["confidence"] == "high" else 1, p["match_reason"]))
    return result


def _parse_card_fields(raw_text: str) -> dict:
    """Extract name, email, phone from connect card raw HTML."""
    if not raw_text:
        return {}
    try:
        soup = BeautifulSoup(raw_text, "html.parser")
        div = soup.find("div", attrs={"role": "module-content", "bgcolor": "#ffffff"}) or soup

        raw: dict[str, list[str]] = {}
        for b_tag in div.find_all("b"):
            label = b_tag.get_text(strip=True)
            if not label:
                continue
            values = []
            for sib in b_tag.next_siblings:
                if getattr(sib, "name", None) == "b":
                    break
                text = sib.get_text(strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                if text:
                    values.append(text)
            raw[label] = values

        def get_one(sub: str) -> str:
            sub = sub.lower()
            for lbl, vals in raw.items():
                if sub in lbl.lower():
                    return vals[0] if vals else ""
            return ""

        first = get_one("first name")
        last  = get_one("last name")
        return {
            "name":  f"{first} {last}".strip(),
            "email": get_one("email").strip().lower(),
            "phone": get_one("phone number").strip(),
        }
    except Exception:
        return {}


def find_data_inconsistencies() -> list[dict]:
    """
    Find members whose connect cards contain different values for
    name, email, or phone across submissions.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    results = []
    try:
        members = [
            dict(r) for r in conn.execute(
                """
                SELECT m.id, m.name, m.email, m.phone,
                       (SELECT COUNT(*) FROM connect_cards WHERE member_id = m.id) AS card_count
                FROM members m
                WHERE (SELECT COUNT(*) FROM connect_cards WHERE member_id = m.id) >= 2
                ORDER BY card_count DESC
                """
            ).fetchall()
        ]

        for m in members:
            cards = conn.execute(
                "SELECT raw_text FROM connect_cards WHERE member_id = ? AND raw_text IS NOT NULL",
                (m["id"],),
            ).fetchall()

            name_vals, email_vals, phone_vals = [], [], []
            for card in cards:
                p = _parse_card_fields(card["raw_text"])
                if p.get("name"):
                    name_vals.append(p["name"])
                if p.get("email"):
                    email_vals.append(p["email"])
                if p.get("phone"):
                    phone_vals.append(p["phone"])

            inconsistencies = []
            for field, vals in [("name", name_vals), ("email", email_vals), ("phone", phone_vals)]:
                if len(set(vals)) < 2:
                    continue
                counts = Counter(vals)
                variations = [
                    {"value": v, "count": c}
                    for v, c in sorted(counts.items(), key=lambda x: -x[1])
                ]
                inconsistencies.append({
                    "field":         field,
                    "current_value": m[field] or "",
                    "variations":    variations,
                    "suggested":     variations[0]["value"],
                })

            if inconsistencies:
                results.append({
                    "member_id":       m["id"],
                    "member_name":     m["name"],
                    "member_email":    m["email"],
                    "member_phone":    m["phone"],
                    "card_count":      m["card_count"],
                    "inconsistencies": inconsistencies,
                })
    finally:
        conn.close()

    return results


def merge_members(winner_id: int, loser_id: int) -> dict:
    """Merge loser into winner. Winner survives, loser is deleted. Transactional."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        for table in ("connect_cards", "attendance", "next_steps", "prayer_requests", "follow_ups"):
            conn.execute(
                f"UPDATE {table} SET member_id = ? WHERE member_id = ?",
                (winner_id, loser_id),
            )
        conn.execute("DELETE FROM members WHERE id = ?", (loser_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return {"ok": True, "winner_id": winner_id, "loser_id": loser_id}


def update_member_field(member_id: int, field: str, value: str) -> dict:
    """Update one allowed field on a member record."""
    if field not in _ALLOWED_FIELDS:
        raise ValueError(f"Field '{field}' not allowed. Allowed: {sorted(_ALLOWED_FIELDS)}")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(f"UPDATE members SET {field} = ? WHERE id = ?", (value, member_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "member_id": member_id, "field": field, "value": value}
