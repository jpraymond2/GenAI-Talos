"""Shared Google OAuth for Gmail and Calendar connectors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from recall.config import Settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]


def credentials_path(settings: Settings | None = None) -> Path:
    """Return secrets/credentials.json (gitignored)."""
    _ = settings
    return Path("secrets/credentials.json")


def token_path(settings: Settings | None = None) -> Path:
    """Return secrets/token.json (gitignored)."""
    _ = settings
    return Path("secrets/token.json")


def load_google_credentials(settings: Settings | None = None) -> Any:
    """Load or refresh InstalledAppFlow credentials; raise FileNotFoundError if missing."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_file = credentials_path(settings)
    token_file = token_path(settings)
    if not creds_file.is_file():
        raise FileNotFoundError(f"missing {creds_file}")

    creds: Credentials | None = None
    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
        creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds
