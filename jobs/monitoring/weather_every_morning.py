import os
import time
from datetime import datetime, timedelta
from subprocess import call
from urllib.parse import urlparse
import requests
from python_telegram_bot import Updater, CommandHandler, Filters
from ollama import qwen2_5_coder
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
CHAT_ID = os.getenv("CHAT_ID")

def fetch_weather():
    url = f"http://api.openweathermap.org/data/2.5/weather?q=New+York&appid={WEATHER_API_KEY}&units=metric"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return f"Temperature: {data['main']['temp']}°C, Weather: {data['weather'][0]['description']}"
    else:
        return "Failed to fetch weather."

def send_weather(update, context):
    weather = fetch_weather()
    update.message.reply_text(weather)

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("weather", send_weather))
    
    job_queue = updater.job_queue
    job_queue.run_daily(send_weather, time=timedelta(hours=6))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()