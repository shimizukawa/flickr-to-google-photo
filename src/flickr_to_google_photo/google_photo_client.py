"""
Google Photos API client.

Handles:
- OAuth2 authentication (via local browser flow)
- Uploading photo bytes using the media upload endpoint
- Creating albums
- Adding media items to albums

Note: The Google Photos Library API does *not* allow reading or modifying
existing library items uploaded outside this application, per its terms of
service.  New uploads are fully manageable.

Scopes used:
- https://www.googleapis.com/auth/photoslibrary.appendonly
  (upload, create albums, add items to albums)
- https://www.googleapis.com/auth/photoslibrary.readonly
  (read all library items, including smartphone uploads, for duplicate detection)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
    "https://www.googleapis.com/auth/photoslibrary.readonly",
]
_API_BASE = "https://photoslibrary.googleapis.com/v1"
_UPLOAD_URL = "https://photoslibrary.googleapis.com/v1/uploads"


class GooglePhotoClient:
    """Wrapper around the Google Photos Library REST API."""

    def __init__(
        self,
        client_secrets_file: Path,
        token_file: Path,
    ) -> None:
        self._client_secrets_file = client_secrets_file
        self._token_file = token_file
        self._credentials: Credentials | None = None
        self._session: requests.Session | None = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        """
        Authenticate with Google Photos using OAuth2.

        Tries to load cached credentials from `token_file`.  If they are
        missing or expired, starts a local-server browser flow.
        """
        creds: Credentials | None = None

        if self._token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_file), _SCOPES)
            # If the cached token was issued for a different (smaller) set of scopes,
            # the new scope (e.g. photoslibrary.readonly added in a later version) would
            # cause 403 errors.  Force re-authorization in that case.
            if creds and creds.scopes and not set(_SCOPES).issubset(creds.scopes):
                logger.info(
                    "Cached token scopes %s do not cover all required scopes %s; "
                    "re-authorizing.",
                    creds.scopes,
                    _SCOPES,
                )
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._client_secrets_file), _SCOPES
                )
                creds = flow.run_local_server(port=0)
            self._token_file.write_text(creds.to_json())

        self._credentials = creds
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {creds.token}"}
        )
        logger.info("Authenticated with Google Photos.")

    def _ensure_auth(self) -> None:
        if self._session is None or self._credentials is None:
            raise RuntimeError("Not authenticated. Call authenticate() first.")
        # Refresh token if needed
        if self._credentials.expired and self._credentials.refresh_token:
            self._credentials.refresh(Request())
            assert self._session is not None
            self._session.headers.update(
                {"Authorization": f"Bearer {self._credentials.token}"}
            )

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_photo(self, file_path: Path, description: str = "") -> str:
        """
        Upload a photo file and return the upload token.

        The upload token is later used in `create_media_item`.
        """
        self._ensure_auth()
        assert self._session is not None

        mime_type = _mime_type_for(file_path)
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Goog-Upload-Content-Type": mime_type,
            "X-Goog-Upload-Protocol": "raw",
            "X-Goog-Upload-File-Name": file_path.name,
        }

        with file_path.open("rb") as f:
            data = f.read()

        resp = self._session.post(_UPLOAD_URL, headers=headers, data=data, timeout=300)
        resp.raise_for_status()
        upload_token = resp.text
        logger.debug("Uploaded %s, token length=%d", file_path.name, len(upload_token))
        return upload_token

    def create_media_item(
        self,
        upload_token: str,
        filename: str,
        description: str = "",
        album_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a media item from an upload token.

        Returns the created mediaItem dict which includes 'id' and 'productUrl'.
        """
        self._ensure_auth()
        assert self._session is not None

        new_media_item: dict[str, Any] = {
            "description": description,
            "simpleMediaItem": {
                "fileName": filename,
                "uploadToken": upload_token,
            },
        }

        body: dict[str, Any] = {"newMediaItems": [new_media_item]}
        if album_id:
            body["albumId"] = album_id

        resp = self._session.post(
            f"{_API_BASE}/mediaItems:batchCreate",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()

        new_media_item_results = result.get("newMediaItemResults", [])
        if not new_media_item_results:
            raise RuntimeError(f"No media item results returned: {result}")

        item_result = new_media_item_results[0]
        status = item_result.get("status", {})
        # Google Photos API returns status.code == 0 for success (gRPC convention)
        if status.get("code", 0) != 0:
            raise RuntimeError(f"Media item creation failed: {status}")

        media_item = item_result.get("mediaItem", {})
        logger.info("Created media item: id=%s url=%s", media_item.get("id"), media_item.get("productUrl"))
        return media_item

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def find_duplicate_media_item(
        self,
        filename: str,
        date_taken: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> str | None:
        """
        Search the Google Photos library for an item that duplicates this photo.

        The search is always narrowed to a single calendar day via ``date_taken``
        to keep the number of API results small.  When ``date_taken`` is absent
        the check is skipped entirely (full-library enumeration would be
        prohibitively expensive).

        Three matching strategies are attempted for every candidate item:

        1. **Filename match** – the item's ``filename`` equals ``filename``.
           Reliable for items previously uploaded by this app (the filename is
           preserved from the original Flickr download).

        2. **Timestamp match** – the item's ``mediaMetadata.creationTime``
           minute and second components equal those of ``date_taken``.
           Flickr stores ``date_taken`` in the photographer's local time while
           Google Photos stores ``creationTime`` in UTC.  Because timezone
           offsets are always a whole number of hours (ignoring the very few
           fractional-hour regions), the *minute* and *second* components are
           identical for the same photo regardless of timezone.  This gives
           second-level precision without requiring timezone knowledge.

        3. **Dimension match** – the item's ``mediaMetadata.width`` and
           ``mediaMetadata.height`` equal ``width`` and ``height``.
           Useful as a fallback when ``creationTime`` is absent, but note that
           all photos taken with the same camera will share dimensions so this
           strategy alone can produce false positives.

        The ``photoslibrary.readonly`` scope is required so that items not
        created by this app (e.g. smartphone uploads) are visible in search
        results.

        Returns the media item ID string if a duplicate is found, else ``None``.
        """
        if not date_taken:
            return None

        self._ensure_auth()
        assert self._session is not None

        # Parse just the date part (format may be "YYYY-MM-DD HH:MM:SS" or ISO-8601)
        try:
            dt = datetime.strptime(date_taken[:10], "%Y-%m-%d")
        except ValueError:
            logger.debug("Could not parse date_taken %r; skipping duplicate check.", date_taken)
            return None

        body: dict[str, Any] = {
            "pageSize": 100,
            "filters": {
                "dateFilter": {
                    "dates": [{"year": dt.year, "month": dt.month, "day": dt.day}]
                }
            },
        }

        check_dimensions = width is not None and height is not None

        while True:
            resp = self._session.post(
                f"{_API_BASE}/mediaItems:search",
                headers={"Content-Type": "application/json"},
                data=json.dumps(body),
                timeout=30,
            )
            if resp.status_code == 403:
                logger.warning(
                    "Duplicate detection unavailable (403 Forbidden on mediaItems:search). "
                    "The OAuth app may not have the photoslibrary.readonly scope approved. "
                    "Skipping duplicate check and proceeding with upload."
                )
                return None
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("mediaItems", []):
                item_id = item.get("id")
                meta = item.get("mediaMetadata", {})

                # Strategy 1: filename match
                if item.get("filename") == filename:
                    logger.debug(
                        "Duplicate found by filename: id=%s filename=%s",
                        item_id,
                        filename,
                    )
                    return item_id

                # Strategy 2: timestamp match (minute:second, timezone-independent)
                creation_time = meta.get("creationTime", "")
                if creation_time and _timestamps_match(date_taken, creation_time):
                    logger.debug(
                        "Duplicate found by timestamp: id=%s filename=%s creationTime=%s",
                        item_id,
                        item.get("filename"),
                        creation_time,
                    )
                    return item_id

                # Strategy 3: dimension match (fallback for smartphone originals)
                if check_dimensions:
                    try:
                        item_w = int(meta.get("width", 0))
                        item_h = int(meta.get("height", 0))
                    except (TypeError, ValueError):
                        continue
                    if item_w == width and item_h == height:
                        logger.debug(
                            "Duplicate found by dimensions (%dx%d): id=%s filename=%s",
                            width,
                            height,
                            item_id,
                            item.get("filename"),
                        )
                        return item_id

            next_token = data.get("nextPageToken")
            if not next_token:
                break
            body["pageToken"] = next_token

        return None

    # ------------------------------------------------------------------
    # Albums
    # ------------------------------------------------------------------

    def create_album(self, title: str) -> dict[str, Any]:
        """Create a new album and return the album dict (includes 'id')."""
        self._ensure_auth()
        assert self._session is not None

        body = {"album": {"title": title}}
        resp = self._session.post(
            f"{_API_BASE}/albums",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=30,
        )
        resp.raise_for_status()
        album = resp.json()
        logger.info("Created album '%s' with id=%s", title, album.get("id"))
        return album

    def get_or_create_album(self, title: str) -> str:
        """
        Return the ID of an album with the given title, creating it if needed.

        Note: Google Photos does not allow listing all albums created by other
        apps, so we list only albums created by this application.
        """
        self._ensure_auth()
        assert self._session is not None

        # List app-created albums
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": 50, "excludeNonAppCreatedData": True}
            if page_token:
                params["pageToken"] = page_token
            resp = self._session.get(f"{_API_BASE}/albums", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            for album in data.get("albums", []):
                if album.get("title") == title:
                    return album["id"]
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        # Not found → create
        album = self.create_album(title)
        return album["id"]

    def add_to_album(self, album_id: str, media_item_id: str) -> None:
        """Add an already-created media item to an album."""
        self._ensure_auth()
        assert self._session is not None

        body = {"mediaItemIds": [media_item_id]}
        resp = self._session.post(
            f"{_API_BASE}/albums/{album_id}:batchAddMediaItems",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=30,
        )
        resp.raise_for_status()
        logger.debug("Added media item %s to album %s", media_item_id, album_id)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/avi",
    ".wmv": "video/x-ms-wmv",
}


def _mime_type_for(path: Path) -> str:
    return _MIME_MAP.get(path.suffix.lower(), "application/octet-stream")


def _timestamps_match(flickr_date_taken: str | None, google_creation_time: str) -> bool:
    """
    Compare Flickr and Google Photos timestamps at minute:second precision.

    Flickr ``date_taken`` is stored in the photographer's local time
    (``YYYY-MM-DD HH:MM:SS``).  Google Photos ``creationTime`` is RFC 3339 UTC
    (e.g. ``2023-06-15T01:30:45Z``).  Because timezone offsets are always a
    whole number of hours (ignoring the very few fractional-hour regions such
    as IST +05:30), the *minute* and *second* components are identical for the
    same photo regardless of which timezone the photographer was in.

    Returns ``True`` only when both minute and second components match.
    """
    if not flickr_date_taken or not google_creation_time:
        return False
    try:
        flickr_dt = datetime.strptime(flickr_date_taken[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    try:
        # datetime.fromisoformat does not accept "Z" before Python 3.11
        google_dt = datetime.fromisoformat(google_creation_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    return flickr_dt.minute == google_dt.minute and flickr_dt.second == google_dt.second
