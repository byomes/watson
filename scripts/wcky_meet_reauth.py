"""
One-time OAuth consent script — mints an independent refresh token for
wcky's /meet Google Calendar integration (freebusy + event create/patch),
using Watson-Web's existing OAuth client instead of a separate one.

Does NOT touch ~/watson/config/token.json (Watson's own operational
credential is left untouched). Does NOT print the client secret — only
the resulting refresh token, for manual copy into Vercel.

Prerequisite: http://localhost:8765/ must be added as an Authorized
redirect URI on the Watson-Web OAuth client in Google Cloud Console
(APIs & Services > Credentials > OAuth 2.0 Client IDs > client ending
...fjmm14rb3asfutnppql2dldftpd1djc5, project watson-498401).

Usage:
    cd ~/watson && PYTHONPATH=/home/billyomes/watson venv/bin/python scripts/wcky_meet_reauth.py

If running over SSH with no local browser, tunnel the port first:
    ssh -L 8765:localhost:8765 billyomes@watson
then open the printed URL in a browser on your own machine.
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

CREDENTIALS_FILE = Path.home() / "watson" / "config" / "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
PORT = 8765


def main():
    client_config = json.loads(CREDENTIALS_FILE.read_text())

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(
        host="localhost",
        port=PORT,
        open_browser=False,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        print("\nNo refresh_token returned — Google may have withheld it because this")
        print("account already has an active grant for this client+scope. Revoke the")
        print("existing grant at myaccount.google.com/permissions and re-run.\n")
        return

    print("\nConsent complete. Copy this into Vercel as GOOGLE_REFRESH_TOKEN:\n")
    print(creds.refresh_token)
    print()


if __name__ == "__main__":
    main()
