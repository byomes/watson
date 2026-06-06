"""jobs/weather/notify.py — send daily weather update via Telegram at 6am."""
import os
import time
from datetime import datetime, timezone, timedelta
import requests
from telegram.ext import Updater, Job, CommandHandler, run_jobs
from python_dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
DB_PATH = os.getenv("WATSON_DB", str(REPO / "data" / "watson.db"))
LOG_PATH = REPO / "logs"
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            with open(LOG_PATH / "error.log", "a") as f:
                f.write(f"{datetime.now(timezone.utc)} - {e}\n")
            raise
    return wrapper

@log_error
def get_weather():
    url = f"http://api.openweathermap.org/data/2.5/weather?q=your_city&appid={WEATHER_API_KEY}&units=metric"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return f"Weather in your city: {data['weather'][0]['description']}, Temp: {data['main']['temp']}°C"
    else:
        return "Failed to fetch weather"

@log_error
def send_weather(update, context):
    message = get_weather()
    context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

def main():
    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    job_queue = updater.job_queue
    job_queue.run_daily(send_weather, time(6, 0), name="daily_weather")

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()