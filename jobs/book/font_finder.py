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

CANVAS_W, CANVAS_H = 1000, 600
PADDING = 60
TITLE_REGION_H = 220
SUB_REGION_H = 140
LABEL_REGION_H = 60
TITLE_MAX_SIZE, TITLE_MIN_SIZE = 64, 26
SUB_MAX_SIZE, SUB_MIN_SIZE = 26, 15
LABEL_MAX_SIZE, LABEL_MIN_SIZE = 16, 11


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


def _truncate_to_width(draw, text: str, font, max_width: int) -> str:
    """Character-level fallback for a single word wider than max_width
    even alone — binary-searches the longest prefix (plus an ellipsis)
    that still fits, rather than letting it run off the canvas edge."""
    if (draw.textbbox((0, 0), text, font=font)[2]) <= max_width:
        return text
    lo, hi, best = 0, len(text), "…"
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + "…"
        if (draw.textbbox((0, 0), candidate, font=font)[2]) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def fit_text(draw, text: str, font_path: str, max_width: int, max_height: int, start_size: int, min_size: int):
    """Measure -> shrink -> wrap, in that order. Tries start_size on one
    line first; if it doesn't fit, shrinks in steps down to min_size; if
    it still doesn't fit at the floor size, wraps onto additional lines
    (greedy word wrap) instead of shrinking past legibility. Returns
    (lines, font, line_height) truncated to whatever fits in max_height,
    ellipsizing the last visible line if content had to be cut."""
    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width and (bbox[3] - bbox[1]) <= max_height:
            return [text], font, bbox[3] - bbox[1]
        size -= 2

    font = ImageFont.truetype(font_path, min_size)
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = trial
        elif not current:
            # a single word wider than max_width even alone (no spaces to
            # break on) — truncate the word itself rather than letting it
            # run off the canvas edge unbroken
            lines.append(_truncate_to_width(draw, word, font, max_width))
        else:
            lines.append(current)
            word_bbox = draw.textbbox((0, 0), word, font=font)
            if (word_bbox[2] - word_bbox[0]) <= max_width:
                current = word
            else:
                lines.append(_truncate_to_width(draw, word, font, max_width))
                current = ""
    if current:
        lines.append(current)
    if not lines:
        lines = [text]

    line_bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_height = int((line_bbox[3] - line_bbox[1]) * 1.15)
    max_lines = max(1, max_height // line_height)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines, font, line_height


def _draw_block(draw, lines, font, line_height, x, y, fill):
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + 6
    return y


def _render_preview(suggestion_id: int, title: str, subtitle: str, display_meta: dict, body_meta: dict) -> str:
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    display_path = _download_font(display_meta)
    body_path = _download_font(body_meta)

    img = Image.new("RGB", (CANVAS_W, CANVAS_H), "#ffffff")
    draw = ImageDraw.Draw(img)
    max_w = CANVAS_W - 2 * PADDING

    title_lines, title_font, title_line_h = fit_text(
        draw, title, display_path, max_w, TITLE_REGION_H, TITLE_MAX_SIZE, TITLE_MIN_SIZE
    )
    _draw_block(draw, title_lines, title_font, title_line_h, PADDING, 80, "#1a1a1a")

    if subtitle:
        sub_lines, sub_font, sub_line_h = fit_text(
            draw, subtitle, body_path, max_w, SUB_REGION_H, SUB_MAX_SIZE, SUB_MIN_SIZE
        )
        _draw_block(draw, sub_lines, sub_font, sub_line_h, PADDING, 80 + TITLE_REGION_H + 20, "#444444")

    label_text = f'{display_meta["family"]} / {body_meta["family"]}'
    label_lines, label_font, label_line_h = fit_text(
        draw, label_text, body_path, max_w, LABEL_REGION_H, LABEL_MAX_SIZE, LABEL_MIN_SIZE
    )
    _draw_block(draw, label_lines, label_font, label_line_h, PADDING, CANVAS_H - LABEL_REGION_H, "#888888")

    out_path = os.path.join(PREVIEW_DIR, f"suggestion_{suggestion_id}.jpg")
    img.save(out_path, "JPEG", quality=90)
    return out_path


# ── Generation flow: narrow (slow, once) + render (fast, repeatable) ─────

def narrow_fonts(series_id: int) -> int:
    """Stage 1 — Fonts API fetch + qwen2.5:7b selection + validator. The
    expensive/slow part (Ollama call alone runs ~47s). Caches the
    resolved candidate list (family names + full Google Fonts metadata,
    so render_batch never needs to hit the Fonts API again) in a
    cover_font_suggestion_batches row. Returns the batch id."""
    create_tables()
    if not GOOGLE_FONTS_API_KEY:
        raise RuntimeError("GOOGLE_FONTS_API_KEY not set in ~/watson/.env — cannot fetch Google Fonts catalog.")

    conn = get_connection()
    try:
        series = conn.execute("SELECT * FROM cover_series WHERE id=?", (series_id,)).fetchone()
        if not series:
            raise ValueError(f"cover_series id={series_id} not found")

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

        candidates = [
            {
                "display_family": v["display_family"],
                "body_family": v["body_family"],
                "rationale": v["rationale"],
                "display_meta": catalog_by_name[v["display_family"]],
                "body_meta": catalog_by_name[v["body_family"]],
            }
            for v in valid
        ]

        cur = conn.execute(
            "INSERT INTO cover_font_suggestion_batches (series_id, candidates_json) VALUES (?, ?)",
            (series_id, json.dumps(candidates)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _get_or_create_suggestion_row(conn, batch_id: int, series_id: int, candidate: dict) -> dict:
    """First render of a batch creates the cover_font_suggestions row;
    every later re-render (same batch, new title/subtitle) reuses the
    same row/id so Telegram's Approve/Reject buttons keep referencing
    the one canonical suggestion, not a duplicate."""
    row = conn.execute(
        "SELECT * FROM cover_font_suggestions WHERE batch_id=? AND display_family=? AND body_family=?",
        (batch_id, candidate["display_family"], candidate["body_family"]),
    ).fetchone()
    if row:
        return dict(row)
    cur = conn.execute(
        "INSERT INTO cover_font_suggestions (series_id, batch_id, display_family, body_family, rationale) "
        "VALUES (?, ?, ?, ?, ?)",
        (series_id, batch_id, candidate["display_family"], candidate["body_family"], candidate["rationale"]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM cover_font_suggestions WHERE id=?", (cur.lastrowid,)).fetchone())


def render_batch(batch_id: int, title: str, subtitle: str) -> list:
    """Stage 2 — PIL-only, no Fonts API call, no Ollama call. Renders
    every candidate in the batch against the given title/subtitle and
    (re)sends each to Telegram for review. This is what fires on every
    title/subtitle edit, so it should feel close to instant compared to
    stage 1's ~47s+ narrowing pass (font files are cached locally after
    their first download)."""
    conn = get_connection()
    try:
        batch = conn.execute("SELECT * FROM cover_font_suggestion_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError(f"cover_font_suggestion_batch id={batch_id} not found")
        series_id = batch["series_id"]
        candidates = json.loads(batch["candidates_json"])

        suggestion_ids = []
        for candidate in candidates:
            row = _get_or_create_suggestion_row(conn, batch_id, series_id, candidate)
            suggestion_id = row["id"]

            try:
                preview_path = _render_preview(
                    suggestion_id, title, subtitle, candidate["display_meta"], candidate["body_meta"]
                )
                conn.execute(
                    "UPDATE cover_font_suggestions SET preview_image_path=? WHERE id=?", (preview_path, suggestion_id)
                )
                conn.commit()
            except Exception as exc:
                log.error("Font preview render failed for suggestion %s: %s", suggestion_id, exc)
                continue

            suggestion_ids.append(suggestion_id)
            row = conn.execute("SELECT * FROM cover_font_suggestions WHERE id=?", (suggestion_id,)).fetchone()
            _send_for_review(dict(row))

        return suggestion_ids
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
