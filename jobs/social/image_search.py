import os
import httpx
import base64
import logging

from core.database import get_connection

logger = logging.getLogger(__name__)

"""
This module implements an image search skill for Watson.
It leverages the Google Custom Search API to find and retrieve images.
The search query is passed to the API, and the first result's image content
is fetched and returned as a base64 encoded string.
This makes it suitable for direct embedding in HTML img tags
on the dashboard, for example.
Credentials (GOOGLE_CUSTOM_SEARCH_API_KEY and GOOGLE_CUSTOM_SEARCH_ENGINE_ID)
must be set in the environment.
The `run` function is the primary entry point for skill execution.
"""
def image_search(query: str) -> str:
    """
    Searches for an image using a web service and returns its base64 encoded string.
    """
    API_KEY = os.environ.get("GOOGLE_CUSTOM_SEARCH_API_KEY")
    CSE_ID = os.environ.get("GOOGLE_CUSTOM_SEARCH_ENGINE_ID")

    if not API_KEY or not CSE_ID:
        logger.error("Google Custom Search API key or CSE ID not found in environment variables.")
        return "Error: Google Custom Search API key or CSE ID not configured."

    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "q": query,
        "cx": CSE_ID,
        "key": API_KEY,
        "searchType": "image",
        "num": 1
    }

    try:
        response = httpx.get(search_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "items" in data and data["items"]:
            image_url = data["items"][0]["link"]
            logger.info(f"Found image URL: {image_url}")

            image_response = httpx.get(image_url, timeout=10)
            image_response.raise_for_status()
            image_content = image_response.content
            base64_image = base64.b64encode(image_content).decode('utf-8')
            mime_type = image_response.headers.get("Content-Type", "image/jpeg")

            return f"data:{mime_type};base64,{base64_image}"
        else:
            return "No image found for the given query."

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error searching for image: {e.response.status_code} - {e.response.text}")
        return f"Error: Failed to search for image (HTTP status {e.response.status_code})."
    except httpx.RequestError as e:
        logger.error(f"Request error searching for image: {e}")
        return "Error: Failed to connect to image search service."
    except Exception as e:
        logger.error(f"An unexpected error occurred during image search: {e}", exc_info=True)
        return "Error: An unexpected error occurred."


def run(query: str) -> str:
    """
    Skill entry point for image search.
    """
    return image_search(query)
