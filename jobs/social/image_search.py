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


def find_image(keywords: list) -> dict:
    query = ' '.join(keywords)

    # Try Unsplash first
    unsplash_key = os.environ.get('UNSPLASH_ACCESS_KEY')
    if unsplash_key:
        try:
            resp = requests.get(
                'https://api.unsplash.com/search/photos',
                params={'query': query, 'per_page': 1},
                headers={'Authorization': f'Client-ID {unsplash_key}'},
                timeout=10,
            )
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    photo = results[0]
                    return {
                        'url': photo['urls']['regular'],
                        'photographer': photo['user']['name'],
                        'source': 'unsplash',
                    }
            else:
                log.warning('Unsplash error %s: %s', resp.status_code, resp.text[:200])
        except Exception as exc:
            log.error('Unsplash request failed: %s', exc)

    # Fall back to Pexels
    pexels_key = os.environ.get('PEXELS_API_KEY')
    if pexels_key:
        try:
            resp = requests.get(
                'https://api.pexels.com/v1/search',
                params={'query': query, 'per_page': 1},
                headers={'Authorization': pexels_key},
                timeout=10,
            )
            if resp.status_code == 200:
                photos = resp.json().get('photos', [])
                if photos:
                    photo = photos[0]
                    return {
                        'url': photo['src']['large'],
                        'photographer': photo['photographer'],
                        'source': 'pexels',
                    }
            else:
                log.warning('Pexels error %s: %s', resp.status_code, resp.text[:200])
        except Exception as exc:
            log.error('Pexels request failed: %s', exc)

    log.warning('No image found for keywords: %s', keywords)
    return None


def run(message: str = None) -> str:
    if not message:
        return 'Provide a topic to find an image for.'
    keywords = extract_keywords(message)
    image = find_image(keywords)
    if not image:
        return 'No image found.'

    try:
        import base64
        resp = requests.get(image['url'], timeout=15)
        resp.raise_for_status()
        b64 = base64.b64encode(resp.content).decode()
        content_type = resp.headers.get('content-type', 'image/jpeg').split(';')[0]
        data_url = f"data:{content_type};base64,{b64}"
        return f"{data_url}\n{image['url']}\nPhoto by {image['photographer']} on {image['source'].title()}"
    except Exception as exc:
        log.error('Image fetch failed: %s', exc)
        return f"{image['url']}\nPhoto by {image['photographer']} on {image['source'].title()}"
