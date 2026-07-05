"""jobs/skills/image_gen_skill.py — On-demand AI image generation.
Triggered from Telegram or the dashboard; result is always delivered
back to Dr. Bill via Telegram sendPhoto, regardless of which interface
the request came from.
"""
import os
import re
import time
import logging
import urllib.request
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

IMAGE_DIR = BASE_DIR / "data" / "generated_images"
TELEGRAM_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

_PREFIXES = (
    "generate image:",
    "generate an image:",
    "create an image:",
    "make an image:",
    "imagegen:",
    "imgen:",
)


def _extract_prompt(message: str) -> str:
    msg = message.strip()
    low = msg.lower()
    for prefix in _PREFIXES:
        if low.startswith(prefix):
            return msg[len(prefix):].strip()
    return msg


def generate_image(prompt: str, width: int = 1080, height: int = 1080) -> str:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true"

    req = urllib.request.Request(url, headers={"User-Agent": "Watson/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    slug = re.sub(r"[^\w]+", "_", prompt.lower())[:40].strip("_")
    filepath = str(IMAGE_DIR / f"img_{slug}_{int(time.time())}.jpg")
    with open(filepath, "wb") as f:
        f.write(data)

    return filepath


def send_image_telegram(filepath: str, caption: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Missing Telegram credentials for image delivery.")
        return False
    try:
        with open(filepath, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
                files={"photo": f},
                timeout=30,
            )
        return resp.ok
    except Exception as exc:
        log.error("send_image_telegram failed: %s", exc)
        return False


def run(message: str = None) -> str:
    if not message or not message.strip():
        return "Give me something to generate — e.g. 'image: a lighthouse at sunset'."

    prompt = _extract_prompt(message)
    if not prompt:
        return "What should the image show?"

    try:
        filepath = generate_image(prompt)
    except Exception as exc:
        log.error("Image generation failed: %s", exc)
        return f"Image generation failed: {exc}"

    sent = send_image_telegram(filepath, caption=f"\U0001F3A8 {prompt}")
    if sent:
        return f"Image generated and sent to Telegram: {prompt}"
    return f"Image generated at {filepath}, but Telegram delivery failed — check bot token/chat ID."
