import httpx
import os
import base64

class Publisher:
    def __init__(self):
        self.github_token = os.environ.get("GITHUB_TOKEN")
        self.github_repo = os.environ.get("GITHUB_REPO")
        self.github_owner = os.environ.get("GITHUB_OWNER")
        self.github_branch = os.environ.get("GITHUB_BRANCH", "main")

        if not all([self.github_token, self.github_repo, self.github_owner]):
            raise ValueError("Missing GitHub credentials. Ensure GITHUB_TOKEN, GITHUB_REPO, GITHUB_OWNER are set in .env")

        # Initialize httpx client. httpx is pinned at 0.25.2 as per system instructions.
        self.httpx_client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
        )

    def _get_file_sha(self, path: str) -> str | None:
        """
        Retrieves the SHA of a file from the GitHub repository.
        Returns None if the file does not exist.
        """
        url = f"/repos/{self.github_owner}/{self.github_repo}/contents/{path}?ref={self.github_branch}"
        try:
            response = self.httpx_client.get(url)
            response.raise_for_status() # Raise for HTTP errors (4xx or 5xx)
            sha = response.json().get("sha")
            return sha
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None # File does not exist, so no SHA
            raise # Re-raise other HTTP errors
        except httpx.RequestError as e:
            raise # Re-raise network/request errors

    def _push_to_github(self, file_path: str, new_content: str, commit_message: str) -> str:
        """
        Pushes new content to a specified file in the GitHub repository.
        The file_path argument is the path within the GitHub repository (e.g., 'docs/briefing.html').
        Returns a string indicating success or failure.
        """
        target_github_path = file_path # Assuming this will be 'docs/briefing.html' from the caller

        # 1. Get the current SHA of the file (if it exists)
        # This is crucial for updating existing files. The user confirmed SHA is retrieved correctly.
        current_sha = self._get_file_sha(target_github_path)

        # 2. Base64 encode the new content
        # The GitHub Contents API expects the 'content' field to be a Base64 encoded string.
        # It's important to first encode the string content to bytes (e.8., utf-8),
        # then Base64 encode the bytes, and finally decode the Base64 bytes to a string
        # so it can be correctly included in the JSON payload.
        try:
            encoded_content = base64.b64encode(new_content.encode('utf-8')).decode('utf-8')
        except Exception as e:
            return f"Failed to encode content: {e}"

        # 3. Construct the request body payload
        payload = {
            "message": commit_message,
            "content": encoded_content,
            "branch": self.github_branch
        }

        # If we are updating an existing file, the SHA must be included in the payload.
        # If current_sha is None, it means the file doesn't exist, and GitHub will create it.
        if current_sha:
            payload["sha"] = current_sha

        # 4. Make the PUT request to GitHub API
        url = f"/repos/{self.github_owner}/{self.github_repo}/contents/{target_github_path}"

        try:
            response = self.httpx_client.put(url, json=payload)
            response.raise_for_status() # Will raise HTTPStatusError for 4xx/5xx responses

            return f"Successfully pushed to GitHub: {target_github_path}"

        except httpx.HTTPStatusError as e:
            # A 400 Bad Request often indicates issues with the payload structure or values.
            # Extract more details from the response to aid diagnosis.
            error_message = f"HTTP {e.response.status_code}"
            try:
                error_details = e.response.json()
                error_message += f": {error_details.get('message', e.response.text)}"
                if 'errors' in error_details:
                    error_message += f" Details: {error_details['errors']}"
            except Exception:
                error_message += f": {e.response.text}"
            
            return f"Failed to push to GitHub: {error_message}"

        except httpx.RequestError as e:
            # This covers network errors, DNS issues, etc.
            return f"Failed to push to GitHub (Network/Request Error): {e}"
