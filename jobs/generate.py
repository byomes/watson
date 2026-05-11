"""
generate.py — Claude API: clean transcript → blog draft + social seeds.
Pushes drafts to Vercel KV. Sends Telegram notification when ready.

Usage:
  python jobs/generate.py <clean_transcript_path> <sermon_slug>

  sermon_slug: used for the blog filename and KV key, e.g. "2026-05-11-kingdom-citizenship"
"""

import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

MODEL      = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

BLOG_PROMPT_FILE   = REPO_ROOT / "prompts" / "generate_blog.md"
SOCIAL_PROMPT_FILE = REPO_ROOT / "prompts" / "generate_social.md"

DRAFTS_BLOG_DIR   = REPO_ROOT / "outputs" / "drafts" / "blog"
DRAFTS_SOCIAL_DIR = REPO_ROOT / "outputs" / "drafts" / "social"

# Vercel KV
KV_URL   = os.getenv("VERCEL_KV_REST_API_URL")
KV_TOKEN = os.getenv("VERCEL_KV_REST_API_TOKEN")

# Watson Telegram bot
WATSON_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
WATSON_CHAT_ID   = os.getenv("WATSON_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID")

# Review app URL
REVIEW_APP_URL = os.getenv("REVIEW_APP_URL", "https://watson-review.vercel.app")


# --- Prompt loaders ---------------------------------------------------

def _load_blog_prompt() -> str:
    if BLOG_PROMPT_FILE.exists():
        return BLOG_PROMPT_FILE.read_text(encoding="utf-8")
    return (
        "You are a writing assistant for a pastor and author. "
        "Given a cleaned sermon transcript, write a blog article.\n\n"
        "Requirements:\n"
        "- 800–1200 words\n"
        "- Title that works as a standalone article (not 'Sermon on X')\n"
        "- Written in first person plural (we/us/our)\n"
        "- Theological depth without academic jargon\n"
        "- One clear takeaway\n"
        "- No bullet points in body; flowing prose\n\n"
        "Return ONLY valid JSON with these fields:\n"
        "  title: string\n"
        "  description: string (one sentence, under 160 chars)\n"
        "  slug: string (url-friendly, no date prefix)\n"
        "  body: string (the full article in markdown, no frontmatter)\n"
        "No preamble, no explanation — JSON only."
    )


def _load_social_prompt() -> str:
    if SOCIAL_PROMPT_FILE.exists():
        return SOCIAL_PROMPT_FILE.read_text(encoding="utf-8")
    return (
        "You are a social media strategist for a pastor and author. "
        "Given a cleaned sermon transcript, generate social media seed ideas.\n\n"
        "Requirements:\n"
        "- 5 seed ideas\n"
        "- Each seed: one compelling hook sentence or question (under 280 chars)\n"
        "- Varied angles: challenge, question, quote, stat, story\n"
        "- Theology-forward but accessible\n\n"
        "Return ONLY valid JSON: an array of 5 strings.\n"
        "No preamble, no explanation — JSON only."
    )


# --- Claude calls -----------------------------------------------------

def _call_claude(system: str, user: str) -> str:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text.strip()


def _parse_json(raw: str) -> dict | list:
    # Strip markdown fences if Claude adds them despite instructions
    clean = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE)
    return json.loads(clean.strip())


# --- Vercel KV --------------------------------------------------------

def _kv_set(key: str, value: dict) -> None:
    if not KV_URL or not KV_TOKEN:
        log.warning("Vercel KV not configured — skipping push")
        return
    url = f"{KV_URL}/set/{key}"
    headers = {"Authorization": f"Bearer {KV_TOKEN}"}
    resp = requests.post(url, headers=headers, json=value, timeout=15)
    resp.raise_for_status()
    log.info("Pushed to Vercel KV: %s", key)


# --- Telegram ---------------------------------------------------------

def _telegram_notify(text: str) -> None:
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.warning("Telegram not configured — skipping notification")
        return
    url = f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": WATSON_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()
    log.info("Telegram notification sent")


# --- Main job ---------------------------------------------------------

def generate(clean_path: Path, sermon_slug: str) -> None:
    clean_text = clean_path.read_text(encoding="utf-8")
    today = date.today().strftime("%Y-%m-%d")

    # Generate blog post
    log.info("Generating blog post...")
    blog_raw  = _call_claude(_load_blog_prompt(), clean_text)
    blog_data = _parse_json(blog_raw)

    slug          = blog_data.get("slug", sermon_slug)
    dated_slug    = f"{today}-{slug}"
    title         = blog_data.get("title", "Untitled")
    description   = blog_data.get("description", "")
    body          = blog_data.get("body", "")

    # Build frontmatter + body as .md
    md_content = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"date: \"{today}\"\n"
        f"description: \"{description}\"\n"
        f"slug: \"{slug}\"\n"
        f"---\n\n"
        f"{body}\n"
    )

    # Save draft locally
    DRAFTS_BLOG_DIR.mkdir(parents=True, exist_ok=True)
    draft_path = DRAFTS_BLOG_DIR / f"{dated_slug}.md"
    draft_path.write_text(md_content, encoding="utf-8")
    log.info("Blog draft saved: %s", draft_path)

    # Generate social seeds
    log.info("Generating social seeds...")
    social_raw   = _call_claude(_load_social_prompt(), clean_text)
    social_seeds = _parse_json(social_raw)

    DRAFTS_SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
    social_path = DRAFTS_SOCIAL_DIR / f"{dated_slug}-seeds.json"
    social_path.write_text(json.dumps(social_seeds, indent=2), encoding="utf-8")
    log.info("Social seeds saved: %s", social_path)

    # Push to Vercel KV so review app can read without calling home
    kv_payload = {
        "dated_slug":   dated_slug,
        "title":        title,
        "description":  description,
        "blog_md":      md_content,
        "social_seeds": social_seeds,
        "status":       "pending",
        "generated_at": today,
    }
    _kv_set("sermon:current", kv_payload)

    # Telegram notification
    review_url = f"{REVIEW_APP_URL}?key={dated_slug}"
    _telegram_notify(
        f"✅ <b>Sermon pipeline complete</b>\n\n"
        f"<b>{title}</b>\n\n"
        f"Blog draft + social seeds ready for review.\n"
        f"<a href='{review_url}'>Open review app →</a>"
    )

    log.info("Generate job complete: %s", dated_slug)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 3:
        print("Usage: python jobs/generate.py <clean_transcript_path> <sermon_slug>")
        sys.exit(1)

    clean_path  = Path(sys.argv[1])
    sermon_slug = sys.argv[2]

    if not clean_path.exists():
        log.error("Clean transcript not found: %s", clean_path)
        sys.exit(1)

    generate(clean_path, sermon_slug)
    sys.exit(0)


if __name__ == "__main__":
    main()
