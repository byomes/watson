"""jobs/marketing/social_poster.py — Post content to Facebook page."""
import logging
import os
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

FB_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID")
FB_GRAPH_URL = "https://graph.facebook.com/v19.0"


def post_to_facebook(message: str, image_path: str = None) -> bool:
    import requests
    if not FB_PAGE_ACCESS_TOKEN or not FB_PAGE_ID:
        log.error("FACEBOOK_PAGE_ACCESS_TOKEN or FACEBOOK_PAGE_ID not set")
        return False
    try:
        if image_path:
            with open(image_path, "rb") as f:
                resp = requests.post(
                    f"{FB_GRAPH_URL}/{FB_PAGE_ID}/photos",
                    data={"caption": message, "access_token": FB_PAGE_ACCESS_TOKEN},
                    files={"source": f},
                    timeout=30,
                )
        else:
            resp = requests.post(
                f"{FB_GRAPH_URL}/{FB_PAGE_ID}/feed",
                data={"message": message, "access_token": FB_PAGE_ACCESS_TOKEN},
                timeout=30,
            )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("post_to_facebook failed: %s", exc)
        return False


def schedule_facebook_post(message: str, scheduled_time: str, image_path: str = None) -> bool:
    import requests
    import arrow
    if not FB_PAGE_ACCESS_TOKEN or not FB_PAGE_ID:
        log.error("Facebook credentials not set")
        return False
    try:
        ts = int(arrow.get(scheduled_time).timestamp())
        data = {
            "message": message,
            "published": "false",
            "scheduled_publish_time": ts,
            "access_token": FB_PAGE_ACCESS_TOKEN,
        }
        resp = requests.post(f"{FB_GRAPH_URL}/{FB_PAGE_ID}/feed", data=data, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("schedule_facebook_post failed: %s", exc)
        return False


def run(message: str = None) -> str:
    if not message:
        return "Social poster ready. Provide post content."
    if not FB_PAGE_ACCESS_TOKEN:
        return "Facebook credentials not configured (FACEBOOK_PAGE_ACCESS_TOKEN missing)."
    success = post_to_facebook(message)
    return "Posted to Facebook." if success else "Facebook post failed — check logs."
