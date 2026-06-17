from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = Path.home() / "watson" / "config" / "credentials.json"
TOKEN_FILE = Path.home() / "watson" / "config" / "token.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
    )
    auth_url, _ = flow.authorization_url(prompt="consent")
    print("\nOpen this URL in your browser to authorize Watson:\n")
    print(auth_url)
    print()
    code = input("Paste the authorization code here: ").strip()
    flow.fetch_token(code=code)
    TOKEN_FILE.write_text(flow.credentials.to_json())
    print("Done — token saved.")


if __name__ == "__main__":
    main()
