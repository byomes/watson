# Telegram Bot — Handler Patterns

## Service
watson-bot.service — systemd on Beelink

## Key Files
bot/bot.py — main bot file

## Patterns
- CommandHandler for slash commands
- MessageHandler with filters for natural language
- CallbackQueryHandler for inline buttons
- Always answer callback queries to clear loading state
- Telegram is away interface — not primary at home

## Approval Flow Pattern
Watson proposes via Telegram message with APPROVE/REJECT inline buttons.
Handler catches callback, checks data prefix, executes or logs rejection.


## Pattern: build a skill that can both read PDF files and extract their (2026-06-06)
The key coding pattern demonstrated in this code is the Observer pattern. This pattern is observed when a `log_error` function is used to notify other parts of the program (specifically, the logging system) that an error has occurred. The observer pattern allows objects (in this case, the logger and the log file) to be notified of changes or events in another object's state, without having a direct reference to each other. This decoupling enables loose coupling between classes, making it easier to modify or replace components of the system independently.
```python
import os
import sqlite3
from io import BytesIO
from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams
from requests import post
from python_dotenv import load_dotenv

load_dotenv()

DATABASE = os.path.expanduser("~/watson/data/watson.db")
LOG_FILE = os.path.expanduser("~/watson/logs/pdf_skill.log")

def log_error(message):
    with open(LOG_FILE, "a") as f:
        f.write(f"{message}\n")

def extract_text_from_pdf(pdf_path):
    text = ""
    with open(pdf_path, 'rb') as file:
```

## Pattern: Watson will be able to send SMS messages using the send libr (2026-06-06)
The key coding pattern demonstrated in this code is the Service-Oriented Architecture (SOA) approach, where each function or method performs a specific task that can be reused across different parts of the application. In this case, the `send_sms` function is a self-contained service that takes in input parameters (`to_number` and `message`) and returns a response, while the `log_error` function serves as a utility function for logging errors. This modular approach allows for easy maintenance, testing, and reuse of individual components without affecting the overall functionality of the application.
```python
import os
import requests
from datetime import datetime
from sqlite3 import connect, Error

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Database path
DB_PATH = os.path.expanduser("~/watson/data/watson.db")

# SMS API endpoint
SMS_API_URL = "https://api.smsprovider.com/send"

def send_sms(to_number, message):
    """
    Send an SMS message using the provided phone number and message.
    
    Args:
```