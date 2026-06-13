"""jobs/research/gemini_fetch.py — Fetch a webpage and process its content with Gemini 2.0 Flash."""
import os

import requests
from bs4 import BeautifulSoup
import google.generativeai


def run():
    return "Gemini Fetch Skill: Send a URL and instruction to fetch page content and process with Gemini 2.0 Flash"


def fetch(url, instruction):
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    google.generativeai.configure(api_key=GEMINI_API_KEY)
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        content = soup.get_text()
        message = f"Instruction: {instruction}\n\nPage Content:\n{content}"
        model = google.generativeai.GenerativeModel('gemini-2.0-flash')
        result = model.generate_content(message)
        return str(result.text)
    except requests.RequestException as error:
        return f"Error fetching URL: {error}"
    except google.generativeai.APIError as error:
        return f"Gemini API error: {error}"
