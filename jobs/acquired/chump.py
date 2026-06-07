import os
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

# Constants
DB_PATH = '~/watson/data/watson.db'
LOG_PATH = '~/watson/logs/'
PUSHOVER_API_URL = 'https://api.pushover.net/1/messages.json'

def send_pushover_notification(message):
    token = os.getenv('PUSHOVER_APP_TOKEN')
    user_key = os.getenv('PUSHOVER_USER_KEY')

    if not token or not user_key:
        raise ValueError("Pushover API token and user key must be set in environment variables.")

    payload = {
        'token': token,
        'user': user_key,
        'message': message
    }

    response = requests.post(PUSHOVER_API_URL, data=payload)

    if response.status_code != 200:
        with open(LOG_PATH + 'pushover_errors.log', 'a') as log_file:
            log_file.write(f"Failed to send Pushover notification: {response.text}\n")

if __name__ == "__main__":
    try:
        # Example usage
        send_pushover_notification("Hello, this is a test notification from Watson!")
    except Exception as e:
        with open(LOG_PATH + 'pushover_errors.log', 'a') as log_file:
            log_file.write(f"Error sending Pushover notification: {str(e)}\n")

def run(message: str = None) -> str:
    return "Watson will be able to send push notifications using the Pushover API"
