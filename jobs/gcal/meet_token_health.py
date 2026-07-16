import requests

from jobs.gcal.token_health import _send_telegram

MEET_AVAILABILITY_URL = "https://www.williamckyomes.com/api/meet/availability?duration=30"
REQUEST_TIMEOUT_SECONDS = 15

ALERT_MESSAGE = (
    "⚠️ /meet availability endpoint is down or returning no slots.\n\n"
    "Checked: {url}\n"
    "Result: {detail}\n\n"
    "Likely cause: the wcky Google Calendar refresh token (Watson-Web's "
    "OAuth client, shared with Watson's own calendar integration) has "
    "been revoked or expired.\n\n"
    "Fix: SSH to Beelink and run:\n"
    "cd ~/watson && source venv/bin/activate && python scripts/wcky_meet_reauth.py\n"
    "then copy the printed refresh token into Vercel (wcky project, "
    "GOOGLE_REFRESH_TOKEN, Production + Preview) and redeploy."
)


def _check() -> str | None:
    """Returns None if healthy, or a failure detail string if not."""
    try:
        resp = requests.get(MEET_AVAILABILITY_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        return f"request failed: {e}"

    if resp.status_code != 200:
        return f"HTTP {resp.status_code}"

    try:
        data = resp.json()
    except ValueError:
        return "200 but response body is not valid JSON"

    slots = data.get("slots")
    if not isinstance(slots, list) or len(slots) == 0:
        return "200 but no slot data in response"

    return None


def main():
    detail = _check()
    if detail is None:
        print("Meet availability endpoint OK")
        return
    _send_telegram(ALERT_MESSAGE.format(url=MEET_AVAILABILITY_URL, detail=detail))


if __name__ == "__main__":
    main()

# Cron: 0 7 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/gcal/meet_token_health.py
