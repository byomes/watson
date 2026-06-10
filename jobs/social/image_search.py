import os
import re
import httpx

def extract_keywords(text: str) -> list[str]:
    if not text:
        return []
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    stopwords = {
        "the", "and", "a", "an", "of", "to", "in", "is", "you", "that", "it", "he", "was",
        "for", "on", "are", "as", "with", "his", "they", "i", "at", "be", "this", "have",
        "from", "or", "one", "had", "by", "word", "but", "not", "what", "all", "were", "we",
        "when", "your", "can", "said", "there", "use", "an", "each", "which", "she", "do",
        "how", "their", "if", "will", "up", "other", "about", "out", "many", "then", "them",
        "these", "so", "some", "her", "would", "make", "like", "him", "into", "time", "has",
        "look", "two", "more", "write", "go", "see", "number", "no", "way", "could", "people",
        "my", "than", "first", "water", "been", "call", "who", "oil", "its", "now", "find"
    }
    filtered = [w for w in words if w not in stopwords]
    seen = set()
    unique = []
    for w in filtered:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:5]

def find_image(keywords: list[str]) -> dict | None:
    if not keywords:
        return None
    query = " ".join(keywords)

    unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if unsplash_key:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    "https://api.unsplash.com/search/photos",
                    headers={"Authorization": f"Client-ID {unsplash_key}"},
                    params={"query": query, "per_page": 1}
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("results"):
                        first = data["results"][0]
                        return {
                            "url": first["urls"]["regular"],
                            "photographer": first["user"]["name"],
                            "source": "Unsplash"
                        }
        except Exception:
            pass

    pexels_key = os.environ.get("PEXELS_API_KEY")
    if pexels_key:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    "https://api.pexels.com/v1/search",
                    headers={"Authorization": pexels_key},
                    params={"query": query, "per_page": 1}
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("photos"):
                        first = data["photos"][0]
                        return {
                            "url": first["src"]["large"],
                            "photographer": first["photographer"],
                            "source": "Pexels"
                        }
        except Exception:
            pass

    return None
