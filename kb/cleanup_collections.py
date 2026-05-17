"""
cleanup_collections.py — One-time script to delete duplicate "Personal Library"
collections in OpenWebUI, keeping the one whose ID is stored in the cache file
(or the oldest one if no cache exists).

Run once:
    python kb/cleanup_collections.py
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(r"D:\OneDrive\Claude\agents\watson\.env")

OPENWEBUI_URL = os.getenv("OPENWEBUI_URL", "http://localhost:3000")
OPENWEBUI_API_KEY = os.getenv("OPENWEBUI_API_KEY")
KNOWLEDGE_COLLECTION = "Personal Library"
CACHE_FILE = Path(__file__).parent / ".collection_id_cache.json"

if not OPENWEBUI_API_KEY:
    sys.exit("OPENWEBUI_API_KEY not set — aborting")

headers = {"Authorization": f"Bearer {OPENWEBUI_API_KEY}"}


def fetch_all_collections() -> list[dict]:
    """Fetch all collections, handling paginated {items, total} responses."""
    all_items = []
    page = 1
    limit = 100
    while True:
        resp = requests.get(
            f"{OPENWEBUI_URL}/api/v1/knowledge/",
            headers=headers,
            params={"page": page, "limit": limit},
            timeout=15
        )
        if resp.status_code != 200:
            sys.exit(f"Failed to list collections: {resp.status_code} {resp.text}")
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            all_items.extend(data["items"])
            if len(all_items) >= data.get("total", len(all_items)):
                break
            page += 1
        else:
            sys.exit(f"Unexpected response shape: {data}")
    return all_items


all_cols = fetch_all_collections()
duplicates = [c for c in all_cols if isinstance(c, dict) and c.get("name") == KNOWLEDGE_COLLECTION]

if len(duplicates) <= 1:
    print(f"Only {len(duplicates)} '{KNOWLEDGE_COLLECTION}' collection(s) found — nothing to clean up.")
    if duplicates:
        keep = duplicates[0]
        print(f"Saving id={keep['id']} to cache.")
        CACHE_FILE.write_text(
            json.dumps({"collection_id": keep["id"], "name": KNOWLEDGE_COLLECTION}),
            encoding="utf-8"
        )
    sys.exit(0)

print(f"Found {len(duplicates)} '{KNOWLEDGE_COLLECTION}' collections.")

# Sort ascending by created_at so index 0 is the oldest
duplicates.sort(key=lambda c: c.get("created_at", 0))

# Prefer the cached ID if it's still in the list; otherwise keep the oldest
cached_id = None
if CACHE_FILE.exists():
    try:
        cached_id = json.loads(CACHE_FILE.read_text(encoding="utf-8")).get("collection_id")
    except Exception:
        pass

keep_id = cached_id if cached_id and any(c["id"] == cached_id for c in duplicates) else duplicates[0]["id"]
print(f"Keeping id={keep_id}")

to_delete = [c for c in duplicates if c["id"] != keep_id]
print(f"Deleting {len(to_delete)} duplicates...\n")

failed = 0
for c in to_delete:
    del_resp = requests.delete(
        f"{OPENWEBUI_URL}/api/v1/knowledge/{c['id']}/delete",
        headers=headers,
        timeout=15
    )
    if del_resp.status_code in (200, 204):
        print(f"  Deleted {c['id']}")
    else:
        # Try without /delete suffix (plain RESTful DELETE)
        del_resp2 = requests.delete(
            f"{OPENWEBUI_URL}/api/v1/knowledge/{c['id']}",
            headers=headers,
            timeout=15
        )
        if del_resp2.status_code in (200, 204):
            print(f"  Deleted {c['id']}")
        else:
            print(f"  FAILED {c['id']}: {del_resp2.status_code} {del_resp2.text}")
            failed += 1

# Update cache with the surviving ID
CACHE_FILE.write_text(
    json.dumps({"collection_id": keep_id, "name": KNOWLEDGE_COLLECTION}),
    encoding="utf-8"
)
print(f"\nDone. {len(to_delete) - failed} deleted, {failed} failed.")
print(f"Cache written: {CACHE_FILE}")
