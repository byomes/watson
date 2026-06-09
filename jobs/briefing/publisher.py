import os
import httpx
import base64

from config.settings import GITHUB_REPO
from core.database import get_connection # Example import, might not be used here

async def _get_github_file_sha(file_path: str, access_token: str) -> str | None:
    """Retrieves the current SHA of a file from GitHub."""
    owner, repo = GITHUB_REPO.split('/')
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get('sha')
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print(f"File {file_path} not found on GitHub.")
                return None
            print(f"Failed to get SHA for {file_path}: {e.response.status_code} {e.response.text}")
            return None
        except httpx.RequestError as e:
            print(f"An error occurred while requesting GitHub API for SHA: {e}")
            return None

async def _push_to_github(file_path: str, content: str, commit_message: str, access_token: str) -> bool:
    """Pushes the given content to a file on GitHub."""
    owner, repo = GITHUB_REPO.split('/')
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json"
    }

    current_sha = await _get_github_file_sha(file_path, access_token)
    if not current_sha:
        print(f"Could not retrieve SHA for {file_path}. Aborting push.")
        return False

    # Encode content to base64 as required by GitHub API
    encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')

    payload = {
        "message": commit_message,
        "content": encoded_content,
        "sha": current_sha,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.put(url, headers=headers, json=payload)
            response.raise_for_status()
            print(f"Successfully updated {file_path} on GitHub.")
            return True
        except httpx.HTTPStatusError as e:
            print(f"Failed to push to GitHub: {e.response.status_code} {e.response.text}")
            print(f"Response body: {e.response.text}") # Added for better debugging
            return False
        except httpx.RequestError as e:
            print(f"An error occurred while requesting GitHub API: {e}")
            return False

async def publish_briefing(html_content: str, commit_msg: str = "Update briefing document") -> bool:
    """High-level function to publish briefing HTML to GitHub."""
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        print("GitHub token not found in environment variables. Cannot publish briefing.")
        return False

    briefing_file_path = "docs/briefing.html"
    success = await _push_to_github(briefing_file_path, html_content, commit_msg, github_token)
    return success
