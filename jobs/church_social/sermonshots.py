"""jobs/church_social/sermonshots.py — thin client for the real Sermon Shots
API, reverse-engineered from their public OpenAPI spec
(https://api.sermonshots.com/api/v1/public/swagger.yaml) plus live testing
against Bill's own account (2026-09-14), since the spec disagrees with
actual behavior in two places documented below.

Auth: `auth-token: <key>` header (not `Authorization: Bearer`, despite that
being the more common convention) — SERMONSHOTS_API_KEY in .env.

Known spec-vs-reality gaps:
- GET /video/{id} returns a JSON ARRAY with one element in practice, not a
  bare object as the spec's `Video` schema implies. get_video() below
  unwraps this.
- GET /videos silently returns an EMPTY BODY (200, content-length 0) unless
  `page`, `limit`, AND `sort` are ALL three present together — any subset
  missing breaks it. The response shape is also `{"items": [...], "total"}`,
  not the spec's `{"data": [...], "total", "page", "limit"}`.
- GET /video/{id}/clips (the endpoint its own name suggests you'd want) was
  tested against a video with 4 real finished clips and came back
  `{"data": [], "count": 0}` — appears unused/dead. The clips that actually
  exist live at GET /video/{id}/downloadable/clips instead, which returns
  each clip's real GCS file record. get_clips() below hits that one.

Clip file URLs (the `file.publicUrl` on each downloadable/clips record) are
plain public GCS object URLs — confirmed no auth needed, no expiry token in
the query string beyond `generation`/`alt=media`. Files are large (one
observed at ~412MB for a single clip), so download_clip() streams to disk
rather than loading the response into memory.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

API_KEY = os.getenv("SERMONSHOTS_API_KEY")
BASE_URL = "https://api.sermonshots.com/api/v1"


def is_configured() -> bool:
    return bool(API_KEY)


def _headers() -> dict:
    return {"auth-token": API_KEY}


def list_videos(limit: int = 10) -> list[dict]:
    """Most-recent-first. `page`+`limit`+`sort` must ALL be present — see
    module docstring. Response key is `items`, not the spec's `data`."""
    resp = requests.get(
        f"{BASE_URL}/videos",
        headers=_headers(),
        params={"page": 1, "limit": limit, "sort": "DESC"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def get_video(video_id: int) -> dict | None:
    """Unwraps the array-of-one the API actually returns (spec claims a
    bare object)."""
    resp = requests.get(f"{BASE_URL}/video/{video_id}", headers=_headers(), timeout=20)
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, list):
        return body[0] if body else None
    return body


def get_clips(video_id: int) -> list[dict]:
    """Finished clip renders for a video — NOT /video/{id}/clips (dead
    endpoint, always empty in testing), but /downloadable/clips. Each
    record has an `id` (stable clip identifier), `video` (parent video
    summary), and `file.publicUrl` (direct downloadable GCS URL)."""
    resp = requests.get(
        f"{BASE_URL}/video/{video_id}/downloadable/clips",
        headers=_headers(),
        params={"withProjects": "true"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def download_clip(url: str, dest_path: str) -> None:
    """Streams to disk — clip files have been observed at several hundred
    MB, too large to hold in memory."""
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
