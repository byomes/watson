import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv('/home/billyomes/watson/.env')

log = logging.getLogger(__name__)

_STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'that', 'this', 'it', 'its', 'we', 'our',
    'you', 'your', 'he', 'she', 'they', 'their', 'what', 'which', 'who',
    'how', 'when', 'where', 'why', 'not', 'no', 'so', 'as', 'if', 'than',
}


def extract_keywords(text: str) -> list:
    words = text.lower().replace(',', ' ').replace('.', ' ').split()
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 3]
    seen = []
    for k in keywords:
        if k not in seen:
            seen.append(k)
    return seen[:5]


def find_images(keywords: list[str], count: int = 5) -> list[dict]:
    query = ' '.join(keywords)
    results = []

    unsplash_key = os.environ.get('UNSPLASH_ACCESS_KEY')
    if unsplash_key:
        try:
            resp = requests.get(
                'https://api.unsplash.com/search/photos',
                params={'query': query, 'per_page': count},
                headers={'Authorization': f'Client-ID {unsplash_key}'},
                timeout=10,
            )
            if resp.status_code == 200:
                for photo in resp.json().get('results', []):
                    results.append({
                        'url': photo['urls']['regular'],
                        'photographer': photo['user']['name'],
                        'source': 'unsplash',
                    })
            else:
                log.warning('Unsplash error %s: %s', resp.status_code, resp.text[:200])
        except Exception as exc:
            log.error('Unsplash request failed: %s', exc)

    remaining = count - len(results)
    if remaining > 0:
        pexels_key = os.environ.get('PEXELS_API_KEY')
        if pexels_key:
            try:
                resp = requests.get(
                    'https://api.pexels.com/v1/search',
                    params={'query': query, 'per_page': remaining},
                    headers={'Authorization': pexels_key},
                    timeout=10,
                )
                if resp.status_code == 200:
                    for photo in resp.json().get('photos', []):
                        results.append({
                            'url': photo['src']['large'],
                            'photographer': photo['photographer'],
                            'source': 'pexels',
                        })
                else:
                    log.warning('Pexels error %s: %s', resp.status_code, resp.text[:200])
            except Exception as exc:
                log.error('Pexels request failed: %s', exc)

    if not results:
        log.warning('No images found for keywords: %s', keywords)
    return results


def run(message: str = None) -> str:
    if not message:
        return 'Provide a topic to find images for.'
    keywords = extract_keywords(message)
    images = find_images(keywords, count=3)
    if not images:
        return 'No images found.'

    return '\n'.join(f"[IMAGE_URL] {img['url']}" for img in images)
