"""
Publish the briefing:
  1. Push docs/briefing.html to GitHub via the Contents API.
  2. Fire the Vercel deploy hook.
  3. Send Bill a Telegram message with the live URL.
"""
import base64
import logging
from datetime import datetime

import requests

from config.settings import (
    DOCS_DIR,
    GITHUB_REPO,
    GITHUB_TOKEN,
    VERCEL_DEPLOY_HOOK,
    WATSON_BOT_TOKEN,
    WATSON_CHAT_ID,
)

log = logging.getLogger(__name__)

_GITHUB_API   = "https://api.github.com"
_DOCS_PATH    = "docs/briefing.html"       # path inside the repo
_BRIEFING_URL = "https://williamckyomes.com/dashboard"


# ── GitHub Contents API ────────────────────────────────────────────────────

def _gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
        "User-Agent":    "watson-bot/1.0",
    }


def _get_sha() -> str | None:
    """Return the blob SHA of the current file in the repo, or None if absent."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None
    resp = requests.get(
        f"{_GITHUB_API}/repos/{GITHUB_REPO}/contents/{_DOCS_PATH}",
        headers=_gh_headers(),
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json().get("sha")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()


def _push_to_github(html_bytes: bytes) -> bool:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.warning("GITHUB_TOKEN or GITHUB_REPO not set — skipping GitHub push")
        return False

    today   = datetime.now()
    message = f"Watson briefing — {today.strftime('%B')} {today.day}, {today.year}"
    sha     = _get_sha()

    body = {
        "message": message,
        "content": base64.b64encode(html_bytes).decode(),
    }
    if sha:
        body["sha"] = sha   # required when updating an existing file

    resp = requests.put(
        f"{_GITHUB_API}/repos/{GITHUB_REPO}/contents/{_DOCS_PATH}",
        headers=_gh_headers(),
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    log.info("Pushed %s to GitHub (%s)", _DOCS_PATH, message)
    return True


# ── Vercel ─────────────────────────────────────────────────────────────────

def _trigger_vercel():
    if not VERCEL_DEPLOY_HOOK:
        log.warning("VERCEL_DEPLOY_HOOK not set — skipping deploy trigger")
        return
    try:
        resp = requests.post(VERCEL_DEPLOY_HOOK, timeout=15)
        resp.raise_for_status()
        log.info("Vercel deploy hook triggered (HTTP %s)", resp.status_code)
    except requests.RequestException as exc:
        log.warning("Vercel deploy hook failed: %s", exc)


# ── Telegram notification to Bill ──────────────────────────────────────────

def _notify_bill():
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.warning("WATSON_BOT_TOKEN or WATSON_CHAT_ID not set — skipping Bill notification")
        return
    text = f"📋 Your briefing is ready: {_BRIEFING_URL}"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
            json={"chat_id": WATSON_CHAT_ID, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Notified Bill via Telegram")
    except requests.RequestException as exc:
        log.warning("Bill notification failed: %s", exc)


# ── Public API ─────────────────────────────────────────────────────────────

def publish() -> bool:
    """
    Push docs/briefing.html to GitHub, trigger Vercel, notify Bill.
    Returns True if the file was pushed, False if skipped (credentials missing).
    """
    briefing_path = DOCS_DIR / "briefing.html"
    if not briefing_path.exists():
        raise FileNotFoundError(
            f"{briefing_path} not found — run build_briefing() before publish()"
        )

    html_bytes = briefing_path.read_bytes()
    pushed     = _push_to_github(html_bytes)

    if pushed:
        _trigger_vercel()
        _notify_bill()

    return pushed


def publish_briefing():
    """Convenience: build + publish in one call (used by manual / cron triggers)."""
    log.info("=== Watson publish starting ===")
    from briefing.builder import build_briefing
    build_briefing()
    pushed = publish()
    log.info("=== %s ===", "Briefing published" if pushed else "Built but not pushed")
    return pushed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    publish_briefing()
