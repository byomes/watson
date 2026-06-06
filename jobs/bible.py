"""
jobs/bible.py — Watson Bible Lookup Job

Fetches Bible passages from api.scripture.api.bible.

Telegram usage:
  Watson bible John 3:16
  Watson bible NIV Romans 8:28
  Watson bible CSB Psalm 23
  Watson bible all Genesis 1:1     ← returns all three translations

Defaults to NIV if no translation specified.
"""

import os
import re
import requests

API_KEY = os.environ.get("BIBLE_API_KEY", "mfKPzqotDI3AE3dgUQiiA")
BASE_URL = "https://api.scripture.api.bible/v1"

TRANSLATIONS = {
    "NIV":  "78a9f6124f344018-01",
    "CSB":  "a556c5305ee15c3f-01",
    "NASB": "9879dbb7cfe39e4d-01",
}

DEFAULT_TRANSLATION = "NIV"


def parse_command(text: str):
    parts = text.strip().split(None, 2)
    if len(parts) < 3:
        return None, None
    remainder = parts[2].strip()
    words = remainder.split(None, 1)
    first = words[0].upper()
    if first == "ALL":
        reference = words[1].strip() if len(words) > 1 else None
        return "all", reference
    elif first in TRANSLATIONS:
        reference = words[1].strip() if len(words) > 1 else None
        return first, reference
    else:
        return DEFAULT_TRANSLATION, remainder


def search_passage(bible_id: str, reference: str) -> str:
    headers = {"api-key": API_KEY}
    params = {"query": reference, "limit": 1}
    url = f"{BASE_URL}/bibles/{bible_id}/search"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        passages = data.get("data", {}).get("passages", [])
        if passages:
            text = passages[0].get("content", "")
            text = re.sub(r"<[^>]+>", "", text).strip()
            ref = passages[0].get("reference", reference)
            return ref, text
        verses = data.get("data", {}).get("verses", [])
        if verses:
            combined = " ".join(
                re.sub(r"<[^>]+>", "", v.get("text", "")).strip()
                for v in verses
            )
            ref = verses[0].get("reference", reference)
            return ref, combined
        return reference, "Passage not found."
    except requests.RequestException as e:
        return reference, f"API error: {e}"


def run(message_text: str = "") -> str:
    if not message_text:
        return "Bible lookup ready. Usage: Watson, bible [reference] or Watson, bible [translation] [reference]"
    translation_key, reference = parse_command(message_text)
    if not reference:
        return "Usage: `Watson bible [NIV|CSB|NASB|all] <reference>`\nExample: `Watson bible John 3:16`"
    if translation_key == "all":
        results = []
        for key, bible_id in TRANSLATIONS.items():
            ref, text = search_passage(bible_id, reference)
            results.append(f"*{key}* — {ref}\n{text}")
        return "\n\n".join(results)
    else:
        bible_id = TRANSLATIONS[translation_key]
        ref, text = search_passage(bible_id, reference)
        return f"*{translation_key}* — {ref}\n{text}"


if __name__ == "__main__":
    import sys
    test_msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Watson bible John 3:16"
    print(run(test_msg))
