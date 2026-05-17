"""
reading_list.py — Watson reading list manager.
Storage: ~/watson/data/reading_list.json
"""

import json
import os
import re
import requests
from datetime import date
from pathlib import Path

LIST_PATH = Path(os.path.expanduser("~/watson/data/reading_list.json"))

def _load():
    if not LIST_PATH.exists():
        return []
    with open(LIST_PATH) as f:
        return json.load(f)

def _save(books):
    LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LIST_PATH, "w") as f:
        json.dump(books, f, indent=2)

def add_book(title, author="Unknown", link="", status="queued"):
    books = _load()
    book = {
        "id": max((b["id"] for b in books), default=0) + 1,
        "title": title,
        "author": author,
        "link": link,
        "status": status,
        "added": date.today().isoformat()
    }
    books.append(book)
    _save(books)
    return book

def remove_book(title):
    books = _load()
    match = next((b for b in books if b["title"].lower() == title.lower()), None)
    if match:
        books = [b for b in books if b["id"] != match["id"]]
        _save(books)
    return match

def update_status(title, status):
    books = _load()
    match = next((b for b in books if b["title"].lower() == title.lower()), None)
    if match:
        match["status"] = status
        _save(books)
    return match

def list_books():
    return _load()

def parse_text_input(text):
    """Parse 'Title by Author — https://link' or variations."""
    link = ""
    if " — " in text:
        text, link = text.rsplit(" — ", 1)
        link = link.strip()
    elif "http" in text:
        parts = text.rsplit(" ", 1)
        if parts[-1].startswith("http"):
            text, link = parts[0], parts[1]
    if " by " in text.lower():
        idx = text.lower().rfind(" by ")
        title = text[:idx].strip()
        author = text[idx+4:].strip()
    else:
        title = text.strip()
        author = "Unknown"
    return title, author, link.strip()

def extract_from_url(url):
    """Fetch URL and extract title/author from meta tags or Amazon URL structure."""
    # Amazon: extract title from URL path
    if "amazon.com" in url:
        import re
        match = re.search(r'/dp/[A-Z0-9]+|/([A-Za-z0-9-]+)/dp/', url)
        path_match = re.search(r'amazon\.com/([^/]+)/(?:dp|product)/', url)
        if path_match:
            raw = path_match.group(1)
            title = raw.replace("-", " ").title()
            return title, "Unknown", url
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text
        og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html)
        if og:
            title = og.group(1).strip()
        else:
            t = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
            title = t.group(1).strip() if t else url
        auth = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\'](.*?)["\']', html)
        author = auth.group(1).strip() if auth else "Unknown"
        return title, author, url
    except Exception:
        return None, None, url
