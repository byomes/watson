import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests
from ollama import log_message
from python_dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Constants
DATABASE_PATH = os.path.expanduser("~/watson/data/watson.db")
LOG_PATH = os.path.expanduser("~/watson/logs/")
PDF_FILE = "sciencespeaks.pdf"


def log_error(message):
    log_message("ERROR: %s", message)


def get_html(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        log_error(f"Failed to fetch {url}: {e}")
        return None


def save_pdf(html, filename=PDF_FILE):
    from weasyprint import HTML

    try:
        HTML(string=html).write_pdf(filename)
        log_message("Saved PDF: %s", filename)
    except Exception as e:
        log_error(f"Failed to create PDF: {e}")


def scrape_book(url):
    html = get_html(url)
    if not html:
        return

    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("div", {"id": "content"})
    if not body:
        log_error("Content section not found")
        return

    full_html = f"<html><head></head><body>{body}</body></html>"
    save_pdf(full_html)


if __name__ == "__main__":
    book_url = "https://sciencespeaks.dstoner.net/index.html#c0"
    scrape_book(book_url)


def run(message: str = None) -> str:
    return "here is a link to a book that is free and hosted online in HTML format. I would "
