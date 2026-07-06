"""jobs/design/screenshot.py — Take webpage screenshots using Playwright."""
import logging
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
SCREENSHOTS_DIR = REPO / "data" / "exports" / "screenshots"
TELEGRAM_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

log = logging.getLogger(__name__)
_URL_RE = re.compile(r'https?://[^\s]+')


def take_screenshot(url: str, output_path: str = None) -> str:
    from playwright.sync_api import sync_playwright
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    if not output_path:
        slug = re.sub(r'[^\w]', '_', url.split("//")[-1])[:40]
        output_path = str(SCREENSHOTS_DIR / f"screenshot_{slug}_{int(time.time())}.png")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.screenshot(path=output_path, full_page=True)
            browser.close()
        return output_path
    except Exception as exc:
        log.error("take_screenshot failed: %s", exc)
        return f"Error: {exc}"


def screenshot_element(url: str, selector: str, output_path: str = None) -> str:
    from playwright.sync_api import sync_playwright
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    if not output_path:
        output_path = str(SCREENSHOTS_DIR / f"element_{int(time.time())}.png")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, wait_until="networkidle", timeout=30000)
            element = page.query_selector(selector)
            if element:
                element.screenshot(path=output_path)
            else:
                browser.close()
                return f"Element not found: {selector}"
            browser.close()
        return output_path
    except Exception as exc:
        log.error("screenshot_element failed: %s", exc)
        return f"Error: {exc}"


def send_screenshot_telegram(url: str) -> bool:
    import requests
    if vacation_gate("normal", "jobs.design.screenshot", url):
        return False
    path = take_screenshot(url)
    if path.startswith("Error"):
        return False
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": url},
                files={"photo": f},
                timeout=30,
            )
        return resp.ok
    except Exception as exc:
        log.error("send_screenshot_telegram failed: %s", exc)
        return False


def run(message: str = None) -> str:
    if not message:
        return "Screenshot tool ready. Provide a URL."
    match = _URL_RE.search(message)
    if not match:
        return "No URL found in message."
    url = match.group(0)
    path = take_screenshot(url)
    if path.startswith("Error"):
        return path
    sent = send_screenshot_telegram(url)
    return f"Screenshot saved: {path}" + (" — sent via Telegram." if sent else "")
