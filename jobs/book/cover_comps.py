"""jobs/book/cover_comps.py — Cover Comp Idea Generator core logic.

Idea generation only, not execution: takes a book's theme/argument and
proposes 3-5 concrete cover concepts (symbol idea, generation-ready image
prompt, font pairing), validated deterministically, sent to Bill on Telegram
for Approve/Regenerate/Reject. Assembly and final production stay manual.

Model: qwen2.5:7b on the Beelink's own Ollama (localhost:11434) — the
existing "accuracy-sensitive background job" bucket. Never qwen2.5:14b or
FMSPC — see memory/WATSON_ARCHITECTURE.md's FMSPC note.
"""
import difflib
import json
import logging
import os
import re
import urllib.parse
import urllib.request

import requests

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate
from jobs.book.schema import create_tables

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"

PREVIEW_DIR = os.path.expanduser("~/watson/data/cover_images")

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_MAX_PROMPT_WORDS = 40
_SYMBOL_DUP_THRESHOLD = 0.82
_PACKETS_PER_RUN = (3, 5)


# ── Ollama call ───────────────────────────────────────────────────────────

def _call_ollama(system: str, prompt: str, timeout: int = 120) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "system": system, "prompt": prompt, "stream": False},
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


# ── Prompt construction ──────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a cover-design idea generator for a nonfiction/theology publishing "
    "house. You propose SINGLE-SYMBOL cover concepts, never full layouts. "
    "You output JSON only, no prose, no markdown fences."
)


def _build_prompt(title, subtitle, theme, key_concepts, palette, used_symbols, font_library, count):
    font_lines = "\n".join(
        f'  id={f["id"]}: display="{f["display_face"]}", body="{f["body_face"]}"' for f in font_library
    )
    used_lines = "\n".join(f"  - {s}" for s in used_symbols) or "  (none yet)"
    prompt = f"""Book title: {title}
Subtitle/working title: {subtitle or "(none)"}
Core argument/theme: {theme}
Key concepts/images already in the source material: {key_concepts or "(none given)"}

Series palette (locked, hex values you MUST use verbatim in generation_prompt): {json.dumps(palette)}

Available font pairings (choose font_pairing_id FROM this list only, never invent):
{font_lines}

Symbols already used in this series (do NOT repeat or closely paraphrase any of these):
{used_lines}

Generate {count} distinct cover concepts as a JSON array. Each element:
{{
  "symbol_concept": "one sentence describing a SINGLE symbol/image",
  "generation_prompt": "under 40 words, vector-flat linework, explicit hex palette values, explicit negative constraints (no gradients, no photorealism, no people/scenes), written for a vector-style image generator",
  "font_pairing_id": <one of the ids above>,
  "layout_note": "one short sentence on title/subtitle hierarchy — title carries stance, subtitle carries information"
}}

Hard rules, all mandatory:
- Single symbol only, no scenes, no people, no verse overlays, no warm gradients, no seasonal decor
- Vector-flat linework only, no photorealistic or painterly rendering
- Must survive a thumbnail/silhouette legibility test
- Must not repeat any symbol listed above
- generation_prompt must be under 40 words and must contain at least one of the locked palette hex values verbatim
- font_pairing_id must be one of the ids listed above

Return ONLY the JSON array, nothing else."""
    return prompt


def _build_retry_prompt(base_prompt, failed_packet, reason):
    return (
        base_prompt
        + f"\n\nYour previous suggestion failed validation because: {reason}\n"
        + f"Previous attempt: {json.dumps(failed_packet)}\n"
        + "Provide ONE corrected replacement packet (a single JSON object, not an array) "
        + "following all the rules above."
    )


# ── Validator ─────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _symbol_is_duplicate(symbol: str, used_symbols) -> bool:
    norm = _normalize(symbol)
    for used in used_symbols:
        ratio = difflib.SequenceMatcher(None, norm, _normalize(used)).ratio()
        if ratio >= _SYMBOL_DUP_THRESHOLD:
            return True
    return False


def validate_packet(packet: dict, palette: list, font_library: list, used_symbols: list) -> str | None:
    """Returns None if valid, else a short human-readable failure reason."""
    if not isinstance(packet, dict):
        return "not a JSON object"

    symbol = packet.get("symbol_concept")
    prompt = packet.get("generation_prompt")
    font_id = packet.get("font_pairing_id")

    if not symbol or not isinstance(symbol, str):
        return "missing symbol_concept"
    if not prompt or not isinstance(prompt, str):
        return "missing generation_prompt"

    if len(prompt.split()) > _MAX_PROMPT_WORDS:
        return f"generation_prompt exceeds {_MAX_PROMPT_WORDS} words"

    found_hex = {h.lower() for h in _HEX_RE.findall(prompt)}
    palette_lower = {h.lower() for h in palette}
    if not found_hex:
        return "generation_prompt contains no hex palette values"
    if not found_hex.issubset(palette_lower):
        return "generation_prompt uses hex values outside the locked series palette"

    if _symbol_is_duplicate(symbol, used_symbols):
        return "symbol_concept fuzzy-matches a symbol already used in this series"

    active_ids = {f["id"] for f in font_library if f.get("active")}
    try:
        font_id = int(font_id)
    except (TypeError, ValueError):
        return "font_pairing_id missing or not an integer"
    if font_id not in active_ids:
        return "font_pairing_id is not an active entry in the supplied font library"

    return None


def _font_pairing_text(font_id: int, font_library: list) -> str:
    for f in font_library:
        if f["id"] == font_id:
            return f'{f["display_face"]} / {f["body_face"]}'
    return ""


# ── Data access ───────────────────────────────────────────────────────────

def _get_series(conn, series_id: int) -> dict:
    row = conn.execute("SELECT * FROM cover_series WHERE id=?", (series_id,)).fetchone()
    if not row:
        raise ValueError(f"cover_series id={series_id} not found")
    return dict(row)


def _get_font_library(conn, font_library_ids: list) -> list:
    if not font_library_ids:
        return []
    placeholders = ",".join("?" * len(font_library_ids))
    rows = conn.execute(
        f"SELECT * FROM cover_font_library WHERE id IN ({placeholders})", font_library_ids
    ).fetchall()
    return [dict(r) for r in rows]


def _get_used_symbols(conn, series_id: int) -> list:
    rows = conn.execute(
        "SELECT symbol_description FROM cover_symbols_used WHERE series_id=?", (series_id,)
    ).fetchall()
    return [r["symbol_description"] for r in rows]


def _insert_concept(conn, series_id, book_title, theme, key_concepts, packet, font_library) -> int:
    font_id = int(packet["font_pairing_id"])
    cur = conn.execute(
        """INSERT INTO cover_concepts
           (series_id, book_title, theme, key_concepts, symbol_concept, generation_prompt, font_pairing, layout_note, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed')""",
        (
            series_id,
            book_title,
            theme,
            key_concepts,
            packet["symbol_concept"],
            packet["generation_prompt"],
            _font_pairing_text(font_id, font_library),
            packet.get("layout_note"),
        ),
    )
    conn.commit()
    return cur.lastrowid


# ── Generation flow ───────────────────────────────────────────────────────

def _validate_or_retry(base_prompt: str, packet, palette, font_library, used_symbols) -> dict | None:
    """Validate one packet; on failure, re-prompt once with the specific
    failure reason attached. Drops the packet if the retry also fails."""
    reason = validate_packet(packet, palette, font_library, used_symbols) if packet else "empty/unparseable response"
    if reason is None:
        return packet

    retry_raw = _call_ollama(_SYSTEM_PROMPT, _build_retry_prompt(base_prompt, packet or {}, reason))
    retry_packet = _parse_json(retry_raw)
    if isinstance(retry_packet, list):
        retry_packet = retry_packet[0] if retry_packet else None
    reason2 = validate_packet(retry_packet, palette, font_library, used_symbols) if retry_packet else "empty/unparseable retry response"
    if reason2 is None:
        return retry_packet

    log.info("Dropping packet after two failed validations: %s / %s", reason, reason2)
    return None


def _generate_one_packet(base_prompt: str, palette, font_library, used_symbols) -> dict | None:
    raw = _call_ollama(_SYSTEM_PROMPT, base_prompt)
    packet = _parse_json(raw)
    if isinstance(packet, list):
        packet = packet[0] if packet else None
    return _validate_or_retry(base_prompt, packet, palette, font_library, used_symbols)


def generate(series_id: int, title: str, subtitle: str, theme: str, key_concepts: str) -> list:
    """Main entry point: dashboard form -> 3-5 validated concepts, sent to
    Telegram for review. Returns the list of inserted concept ids."""
    create_tables()
    conn = get_connection()
    try:
        series = _get_series(conn, series_id)
        palette = json.loads(series["house_palette"])
        font_library = _get_font_library(conn, json.loads(series["font_library_ids"]))
        used_symbols = _get_used_symbols(conn, series_id)

        count = _PACKETS_PER_RUN[1]
        base_prompt = _build_prompt(title, subtitle, theme, key_concepts, palette, used_symbols, font_library, count)
        raw = _call_ollama(_SYSTEM_PROMPT, base_prompt)
        packets = _parse_json(raw)
        if not isinstance(packets, list):
            packets = [packets] if packets else []

        inserted_ids = []
        running_used = list(used_symbols)
        for packet in packets:
            validated = _validate_or_retry(base_prompt, packet, palette, font_library, running_used)
            if validated is None:
                continue
            concept_id = _insert_concept(conn, series_id, title, theme, key_concepts, validated, font_library)
            inserted_ids.append(concept_id)
            running_used.append(validated["symbol_concept"])
            row = conn.execute("SELECT * FROM cover_concepts WHERE id=?", (concept_id,)).fetchone()
            _send_for_review(dict(row))

        return inserted_ids
    finally:
        conn.close()


def regenerate_slot(concept_id: int) -> int | None:
    """Regenerate: mark the old concept superseded, generate one fresh
    packet for the same book/series with the old symbol excluded, send a
    new Telegram review message. Returns the new concept id, or None."""
    conn = get_connection()
    try:
        old = conn.execute("SELECT * FROM cover_concepts WHERE id=?", (concept_id,)).fetchone()
        if not old:
            return None
        old = dict(old)

        series = _get_series(conn, old["series_id"])
        palette = json.loads(series["house_palette"])
        font_library = _get_font_library(conn, json.loads(series["font_library_ids"]))
        used_symbols = _get_used_symbols(conn, old["series_id"])
        used_symbols.append(old["symbol_concept"])

        base_prompt = _build_prompt(
            old["book_title"], None, old["theme"] or "", old["key_concepts"] or "",
            palette, used_symbols, font_library, 1,
        )
        packet = _generate_one_packet(base_prompt, palette, font_library, used_symbols)

        conn.execute("UPDATE cover_concepts SET status='superseded' WHERE id=?", (concept_id,))
        conn.commit()

        if packet is None:
            return None

        new_id = _insert_concept(
            conn, old["series_id"], old["book_title"], old["theme"], old["key_concepts"], packet, font_library
        )
        row = conn.execute("SELECT * FROM cover_concepts WHERE id=?", (new_id,)).fetchone()
        _send_for_review(dict(row))
        return new_id
    finally:
        conn.close()


def approve_concept(concept_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM cover_concepts WHERE id=?", (concept_id,)).fetchone()
        if not row:
            return None
        row = dict(row)
        conn.execute(
            "UPDATE cover_concepts SET status='accepted', accepted_at=datetime('now') WHERE id=?",
            (concept_id,),
        )
        conn.execute(
            "INSERT INTO cover_symbols_used (series_id, book_title, symbol_description) VALUES (?, ?, ?)",
            (row["series_id"], row["book_title"], row["symbol_concept"]),
        )
        conn.commit()
        return row
    finally:
        conn.close()


def reject_concept(concept_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM cover_concepts WHERE id=?", (concept_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE cover_concepts SET status='rejected' WHERE id=?", (concept_id,))
        conn.commit()
        return dict(row)
    finally:
        conn.close()


# ── Preview on demand (Pollinations.ai, raster approximation) ───────────

def generate_preview(concept_id: int) -> str | None:
    """Rough raster approximation via Pollinations.ai — same call shape as
    jobs/facebook/image_gen.py's generate_image(), separate output dir.
    Sanity-check thumbnail only; real vectorization stays a manual
    Illustrator step, per the spec's own non-goals."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM cover_concepts WHERE id=?", (concept_id,)).fetchone()
        if not row:
            return None
        prompt = row["generation_prompt"]

        os.makedirs(PREVIEW_DIR, exist_ok=True)
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=800&height=1200&nologo=true"
        req = urllib.request.Request(url, headers={"User-Agent": "Watson/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()

        out_path = os.path.join(PREVIEW_DIR, f"cover_{concept_id}.jpg")
        with open(out_path, "wb") as f:
            f.write(data)

        conn.execute("UPDATE cover_concepts SET preview_image_path=? WHERE id=?", (out_path, concept_id))
        conn.commit()
        return out_path
    finally:
        conn.close()


# ── Telegram review ───────────────────────────────────────────────────────

def _review_keyboard(concept_id: int) -> list:
    return [[
        {"text": "✅ Approve", "callback_data": f"cvr_approve:{concept_id}"},
        {"text": "\U0001F504 Regenerate", "callback_data": f"cvr_regen:{concept_id}"},
        {"text": "❌ Reject", "callback_data": f"cvr_reject:{concept_id}"},
    ]]


def _send_for_review(concept: dict) -> None:
    text = (
        f"\U0001F4D6 {concept['book_title']} — cover concept\n\n"
        f"Symbol: {concept['symbol_concept']}\n"
        f"Fonts: {concept['font_pairing']}\n"
        f"Layout: {concept.get('layout_note') or '(none)'}\n\n"
        f"Prompt: {concept['generation_prompt']}"
    )
    if vacation_gate("normal", "jobs.book.cover_comps._send_for_review", text):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": WATSON_CHAT_ID,
                "text": text,
                "reply_markup": {"inline_keyboard": _review_keyboard(concept["id"])},
            },
            timeout=15,
        )
    except Exception as exc:
        log.error("Failed to send cover concept %s for review: %s", concept["id"], exc)
