"""jobs/research/gemini_fetch.py — Fetch a webpage and process its content with Gemini 2.0 Flash."""
import os

import requests
from bs4 import BeautifulSoup
try:
    import google.genai as _genai
except ImportError:
    _genai = None


def run(message: str = None) -> str:
    if _genai is None:
        return "Gemini API not available."
    return "Gemini Fetch Skill: Send a URL and instruction to fetch page content and process with Gemini 2.0 Flash"


def fetch(url, instruction):
    if _genai is None:
        return "Gemini API not available."
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    _genai.configure(api_key=GEMINI_API_KEY)
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        content = soup.get_text()
        message = f"Instruction: {instruction}\n\nPage Content:\n{content}"
        model = _genai.GenerativeModel('gemini-2.0-flash')
        result = model.generate_content(message)
        return str(result.text)
    except requests.RequestException as error:
        return f"Error fetching URL: {error}"
    except Exception as error:
        return f"Gemini API error: {error}"
