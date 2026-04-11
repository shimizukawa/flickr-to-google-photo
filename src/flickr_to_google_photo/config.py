"""
Configuration management for flickr-to-google-photo.

Reads credentials from environment variables or a .env file.

Required environment variables:
    FLICKR_API_KEY          - Flickr API key
    FLICKR_API_SECRET       - Flickr API secret

Optional environment variables:
    FLICKR_ACCESS_TOKEN     - OAuth access token (stored after first auth)
    FLICKR_ACCESS_TOKEN_SECRET - OAuth access token secret
    GOOGLE_CLIENT_SECRETS_FILE - Path to Google OAuth client secrets JSON
                                 (default: client_secrets.json)
    DATA_DIR                - Directory for local data/metadata
                              (default: ./data)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self) -> None:
        self.flickr_api_key: str = self._require("FLICKR_API_KEY")
        self.flickr_api_secret: str = self._require("FLICKR_API_SECRET")
        self.flickr_access_token: str | None = os.environ.get("FLICKR_ACCESS_TOKEN")
        self.flickr_access_token_secret: str | None = os.environ.get(
            "FLICKR_ACCESS_TOKEN_SECRET"
        )
        self.google_client_secrets_file: Path = Path(
            os.environ.get("GOOGLE_CLIENT_SECRETS_FILE", "client_secrets.json")
        )
        self.data_dir: Path = Path(os.environ.get("DATA_DIR", "data"))

    @staticmethod
    def _require(key: str) -> str:
        value = os.environ.get(key)
        if not value:
            raise ValueError(
                f"Required environment variable '{key}' is not set. "
                "Please set it in your .env file or environment."
            )
        return value

    def ensure_data_dir(self) -> Path:
        """Create the data directory if it does not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir
