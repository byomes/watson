import os
import requests
from datetime import datetime
import time

TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')
WEATHER_CITY = os.getenv('WEATHER_CITY', 'Wilmington,US')

def get_weather(city, api_key):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric&timeout=10"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def send_telegram_message(chat_id, message):
    url = "https://api.telegram.org/botYOUR_TELEGRAM_BOT_TOKEN/sendMessage"
    payload = {'chat_id': chat_id, 'text': message}
    response = requests.post(url, json=payload)
    response.raise_for_status()

def main():
    while True:
        now = datetime.now()
        if now.hour == 6 and now.minute == 30:
            weather_data = get_weather(WEATHER_CITY, OPENWEATHER_API_KEY)
            temp = int(weather_data['main']['temp'])
            feels_like = int(weather_data['main']['feels_like'])
            humidity = int(weather_data['main']['humidity'])
            message = f"Weather in {WEATHER_CITY}: Temp: {temp}°C, Feels Like: {feels_like}°C, Humidity: {humidity}%"
            send_telegram_message(TELEGRAM_CHAT_ID, message)
        time.sleep(60)

if __name__ == "__main__":
    main()