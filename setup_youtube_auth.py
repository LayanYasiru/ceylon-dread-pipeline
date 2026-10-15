"""Run ONCE on your PC to authorise uploads. Needs client_secret.json (Google Cloud Console -> OAuth client, type 'Desktop app').
Creates token.json (refresh token) and prints it so you can paste it into the GitHub secret YOUTUBE_TOKEN_JSON.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
secrets = Path(__file__).parent / "client_secret.json"
if not secrets.exists():
    raise SystemExit("client_secret.json not found. See SETUP.md step 3.")
flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
(Path(__file__).parent / "token.json").write_text(creds.to_json(), encoding="utf-8")
print("\nSaved token.json. For GitHub Actions, copy the content of token.json into the secret YOUTUBE_TOKEN_JSON.")
print("Never share or commit token.json.")
