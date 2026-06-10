import os
import httpx
from dotenv import load_dotenv
import logging

# Load environment variables at the top of the script
load_dotenv()

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

def find_image(query: str) -> str | None:
    """
    Searches for an image using SerpAPI's Google Images API.
    Returns the URL of the first image found or None if no image is found or an error occurs.
    """
    api_key = os.environ.get('SERPAPI_API_KEY')
    if not api_key:
        logging.error("SERPAPI_API_KEY not found in environment variables.")
        return None

    params = {
        "engine": "google_images",
        "q": query,
        """tbm": "isch", # Image search
        "api_key": api_key
    }

    try:
        # httpx MUST stay pinned at 0.25.2
        response = httpx.get("https://serpapi.com/search", params=params, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()

        if "images_results" in data and data["images_results"]:
            # Return the URL of the first image found
            return data["images_results"][0].get("original")
        else:
            logging.info(f"No image results found for query: {query}")
            return None

    except httpx.RequestError as e:
        logging.error(f"An error occurred while requesting SerpAPI for query '{query}': {e}")
        return None
    except httpx.HTTPStatusError as e:
        logging.error(f"SerpAPI returned an HTTP error for query '{query}': {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred in find_image for query '{query}': {e}")
        return None

if __name__ == '__main__':
    # Example usage:
    image_url = find_image("cat meme")
    if image_url:
        print(f"Found image: {image_url}")
    else:
        print("No image found.")

    image_url_fail = find_image("a very specific non-existent image that should fail")
    if image_url_fail:
        print(f"Found image: {image_url_fail}")
    else:
        print("No image found for failure test.")