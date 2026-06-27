import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from telegram import Bot

# Replace with your actual API keys and chat ID
WEATHER_API_KEY = 'your_weather_api_key'
TELEGRAM_API_TOKEN = 'your_telegram_bot_token'
CHAT_ID = 'your_chat_id'

def get_weather_data():
    url = f'http://api.openweathermap.org/data/2.5/weather?q=YourHomeAddress&appid={WEATHER_API_KEY}&units=metric'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        main = data['main']
        weather = data['weather'][0]
        return {
            'temperature': round(main['temp']),
            'feels_like': round(main['feels_like']),
            'description': weather['description'].capitalize()
        }
    else:
        raise Exception('Failed to fetch weather data')

def send_telegram_message(message):
    bot = Bot(token=TELEGRAM_API_TOKEN)
    bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')

def main():
    now = datetime.now()
    if now.hour == 6 and now.minute == 30:
        weather_data = get_weather_data()
        message = f"🌡️ Temperature: {weather_data['temperature']}°C\n"
        message += f"Feels Like: {weather_data['feels_like']}°C\n"
        message += f"Weather: {weather_data['description']}"
        send_telegram_message(message)

if __name__ == '__main__':
    main()