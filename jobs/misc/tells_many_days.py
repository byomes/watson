"""
jobs/christmas_count.py — Notify how many days until Christmas.
"""

import datetime
from datetime import timedelta
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
DB_PATH = str(REPO / "data" / "watson.db")
LOG_PATH = str(REPO / "logs" / "christmas_count.log")

def _log(message):
    with open(LOG_PATH, "a") as log_file:
        log_file.write(f"{datetime.datetime.now()} - {message}\n")

def days_until_christmas():
    today = datetime.date.today()
    christmas = datetime.date(today.year, 12, 25)
    if today > christmas:
        christmas = datetime.date(today.year + 1, 12, 25)
    return (christmas - today).days

def main():
    load_dotenv()
    days_left = days_until_christmas()
    message = f"There are {days_left} days until Christmas."
    _log(message)

if __name__ == "__main__":
    main()