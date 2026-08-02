"""jobs/book/font_finder.py — "Suggest Fonts": populates a cover_series'
(or Standalone's) font library from the Google Fonts catalog.

Google Fonts metadata narrows a large catalog to a sane shortlist;
qwen2.5:7b reasons about weight range/italic availability to propose
pairings; a deterministic validator confirms every family name actually
exists in the fetched catalog (LLMs occasionally invent/misspell font
names); PIL renders an actual preview so Bill's visual judgment — not
the model's — is what approves a pairing. Reusable across any bucket,
not tied to Standalone specifically.
"""
import json
import logging
import os
import re
import urllib.request

import requests
from PIL import Image, ImageDraw, ImageFont

from config.settings import GOOGLE_FONTS_API_KEY, WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate
from jobs.book.schema import create_tables

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"

GOOGLE_FONTS_URL = "https://www.googleapis.com/webfonts/v1/webfonts"
_ALLOWED_CATEGORIES = ("serif", "sans-serif")
_SHORTLIST_SIZE = 50
_REQUEST_COUNT = 12  # ask for more than the 8+ minimum to buffer against hallucination-drops
_MIN_RESULT = 8

FONT_CACHE_DIR = os.path.expanduser("~/watson/data/font_cache")
PREVIEW_DIR = os.path.expanduser("~/watson/data/font_previews")

_FONT_SYSTEM_PROMPT = (
    "You are a typography assistant choosing display/body font pairings for "
    "nonfiction/theology book covers, from a fixed Google Fonts catalog only. "
    "You output JSON only, no prose, no markdown fences."
)


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


# ── Google Fonts catalog ──────────────────────────────────────────────────

def _fetch_font_catalog(api_key: str) -> list:
    resp = requests.get(GOOGLE_FONTS_URL, params={"key": api_key, "sort": "popularity"}, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [f for f in items if f.get("category") in _ALLOWED_CATEGORIES]


def _catalog_summary(items: list) -> str:
    lines = []
    for f in items:
        variants = ", ".join(f.get("variants", []))
        lines.append(f'- "{f["family"]}" ({f["category"]}): variants=[{variants}]')
    return "\n".join(lines)


# ── Prompt + validator ───────────────────────────────────────────────────

def _build_font_prompt(catalog_items: list, count: int) -> str:
    return f"""Available fonts (choose ONLY from this list, family names must match exactly):
{_catalog_summary(catalog_items)}

Propose {count} distinct display/body font pairings for book covers. Rules:
- Restrained weight — no overly bold/heavy default-only faces
- Prefer families with a real type scale (multiple weights available)
- Avoid overly geometric or playful sans faces for body text
- Prefer a body face with an italic variant available
- Display and body face in a pairing must be different families
- Family names must exactly match one of the families listed above, spelled exactly

Return a JSON array, each element:
{{"display_family": "...", "body_family": "...", "rationale": "one short sentence"}}

Return ONLY the JSON array, nothing else."""


def _validate_suggestions(packets, catalog_by_name: dict) -> list:
    valid = []
    for p in packets or []:
        if not isinstance(p, dict):
            continue
        display = p.get("display_family")
        body = p.get("body_family")
        if not display or not body or display not in catalog_by_name or body not in catalog_by_name:
            continue
        if display == body:
            continue
        valid.append({"display_family": display, "body_family": body, "rationale": p.get("rationale") or ""})
    return valid


# ── PIL preview rendering ─────────────────────────────────────────────────

def _download_font(font_meta: dict) -> str:
    os.makedirs(FONT_CACHE_DIR, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", font_meta["family"])
    out_path = os.path.join(FONT_CACHE_DIR, f"{safe_name}.ttf")
    if os.path.exists(out_path):
        return out_path
    files = font_meta.get("files") or {}
    url = files.get("regular") or next(iter(files.values()))
    url = url.replace("http://", "https://")
    urllib.request.urlretrieve(url, out_path)
    return out_path


def _render_preview(suggestion_id: int, sample_title: str, display_meta: dict, body_meta: dict) -> str:
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    display_path = _download_font(display_meta)
    body_path = _download_font(body_meta)

    img = Image.new("RGB", (900, 500), "#ffffff")
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(display_path, 56)
    sub_font = ImageFont.truetype(body_path, 24)
    label_font = ImageFont.truetype(body_path, 18)

    draw.text((50, 160), sample_title, font=title_font, fill="#1a1a1a")
    draw.text((50, 260), "A sample subtitle line for preview purposes", font=sub_font, fill="#444444")
    draw.text(
        (50, 440),
        f'{display_meta["family"]} / {body_meta["family"]}',
        font=label_font,
        fill="#888888",
    )

    out_path = os.path.join(PREVIEW_DIR, f"suggestion_{suggestion_id}.jpg")
    img.save(out_path, "JPEG", quality=90)
    return out_path


# ── Generation flow ───────────────────────────────────────────────────────

def suggest_fonts(series_id: int) -> list:
    """Fetch the Google Fonts catalog, propose 8+ validated pairings for
    the given bucket, render a preview for each, send to Telegram for
    Approve/Reject. Returns the list of inserted suggestion ids."""
    create_tables()
    if not GOOGLE_FONTS_API_KEY:
        log.error("GOOGLE_FONTS_API_KEY not set in ~/watson/.env — cannot fetch Google Fonts catalog.")
        return []

    conn = get_connection()
    try:
        series = conn.execute("SELECT * FROM cover_series WHERE id=?", (series_id,)).fetchone()
        if not series:
            raise ValueError(f"cover_series id={series_id} not found")
        series = dict(series)

        catalog = _fetch_font_catalog(GOOGLE_FONTS_API_KEY)
        shortlist = catalog[:_SHORTLIST_SIZE]
        catalog_by_name = {f["family"]: f for f in shortlist}

        prompt = _build_font_prompt(shortlist, _REQUEST_COUNT)
        raw = _call_ollama(_FONT_SYSTEM_PROMPT, prompt)
        packets = _parse_json(raw)
        if not isinstance(packets, list):
            packets = [packets] if packets else []
        valid = _validate_suggestions(packets, catalog_by_name)

        if len(valid) < _MIN_RESULT:
            log.warning(
                "Only %d/%d font suggestions validated for series_id=%s — proceeding with what validated.",
                len(valid), _MIN_RESULT, series_id,
            )

        inserted_ids = []
        for v in valid:
            cur = conn.execute(
                "INSERT INTO cover_font_suggestions (series_id, display_family, body_family, rationale) "
                "VALUES (?, ?, ?, ?)",
                (series_id, v["display_family"], v["body_family"], v["rationale"]),
            )
            conn.commit()
            suggestion_id = cur.lastrowid

            try:
                preview_path = _render_preview(
                    suggestion_id, series["name"], catalog_by_name[v["display_family"]], catalog_by_name[v["body_family"]]
                )
                conn.execute(
                    "UPDATE cover_font_suggestions SET preview_image_path=? WHERE id=?", (preview_path, suggestion_id)
                )
                conn.commit()
            except Exception as exc:
                log.error("Font preview render failed for suggestion %s: %s", suggestion_id, exc)

            inserted_ids.append(suggestion_id)
            row = conn.execute("SELECT * FROM cover_font_suggestions WHERE id=?", (suggestion_id,)).fetchone()
            _send_for_review(dict(row))

        return inserted_ids
    finally:
        conn.close()


def approve_font_suggestion(suggestion_id: int) -> dict | None:
    """Writes the pairing into cover_font_library (active=1) and appends
    its id to the bucket's font_library_ids so it's actually usable by
    that bucket's concept generation, not just sitting in the library."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM cover_font_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        if not row:
            return None
        row = dict(row)

        existing = conn.execute(
            "SELECT id FROM cover_font_library WHERE display_face=? AND body_face=?",
            (row["display_family"], row["body_family"]),
        ).fetchone()
        if existing:
            font_id = existing["id"]
            conn.execute(
                "UPDATE cover_font_library SET active=1, rationale_tag=? WHERE id=?", (row["rationale"], font_id)
            )
        else:
            cur = conn.execute(
                "INSERT INTO cover_font_library (display_face, body_face, rationale_tag, active) VALUES (?, ?, ?, 1)",
                (row["display_family"], row["body_family"], row["rationale"]),
            )
            font_id = cur.lastrowid

        series_row = conn.execute("SELECT font_library_ids FROM cover_series WHERE id=?", (row["series_id"],)).fetchone()
        font_ids = json.loads(series_row["font_library_ids"]) if series_row else []
        if font_id not in font_ids:
            font_ids.append(font_id)
            conn.execute(
                "UPDATE cover_series SET font_library_ids=? WHERE id=?", (json.dumps(font_ids), row["series_id"])
            )

        conn.execute("UPDATE cover_font_suggestions SET status='approved' WHERE id=?", (suggestion_id,))
        conn.commit()
        return row
    finally:
        conn.close()


def reject_font_suggestion(suggestion_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM cover_font_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE cover_font_suggestions SET status='rejected' WHERE id=?", (suggestion_id,))
        conn.commit()
        return dict(row)
    finally:
        conn.close()


# ── Telegram review ───────────────────────────────────────────────────────

def _font_review_keyboard(suggestion_id: int) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"fsg_approve:{suggestion_id}"},
            {"text": "❌ Reject", "callback_data": f"fsg_reject:{suggestion_id}"},
        ]]
    }


def _send_for_review(suggestion: dict) -> None:
    caption = f"Font pairing: {suggestion['display_family']} / {suggestion['body_family']}\n{suggestion.get('rationale') or ''}"
    if vacation_gate("normal", "jobs.book.font_finder._send_for_review", caption):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID or not suggestion.get("preview_image_path"):
        return
    try:
        with open(suggestion["preview_image_path"], "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id": WATSON_CHAT_ID,
                    "caption": caption[:1024],
                    "reply_markup": json.dumps(_font_review_keyboard(suggestion["id"])),
                },
                files={"photo": f},
                timeout=30,
            )
    except Exception as exc:
        log.error("Failed to send font suggestion %s for review: %s", suggestion["id"], exc)
