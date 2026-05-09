import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_TOKEN = os.getenv("JENNY_BOT_TOKEN")
_CHAT_ID = os.getenv("JENNY_CHAT_ID")


def _send(text):
    if not _TOKEN or not _CHAT_ID:
        log.warning("JENNY_BOT_TOKEN or JENNY_CHAT_ID not set — message not sent")
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
        json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()
    log.info("Sent to Jenny's bot (chat_id=%s)", _CHAT_ID)


def send_to_email(title, summary, url):
    text = f"📧 <b>TO EMAIL</b>\n\n<b>{title}</b>\n\n{summary}\n\n{url}"
    _send(text)


def send_to_facebook(title, summary, url):
    text = f"📘 <b>TO FACEBOOK</b>\n\n<b>{title}</b>\n\n{summary}\n\n{url}"
    _send(text)
