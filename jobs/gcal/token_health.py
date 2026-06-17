import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

REAUTH_MESSAGE = (
    "⚠️ Google Calendar token has expired and needs reauthorization.\n\n"
    "SSH to Beelink and run:\n"
    "cd ~/watson && source venv/bin/activate && python jobs/gcal/reauth.py"
)


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def main():
    try:
        from jobs.gcal.gcal_service import get_service
        get_service()
        print("Calendar token OK")
    except Exception:
        _send_telegram(REAUTH_MESSAGE)


if __name__ == "__main__":
    main()

# Cron: 0 7 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/gcal/token_health.py
