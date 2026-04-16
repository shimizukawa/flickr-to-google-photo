"""
Flickr API client.

Handles:
- OAuth authentication (3-legged flow via browser or PIN)
- Fetching photo lists, metadata, albums, comments
- Downloading the highest-resolution version of a photo
- Deleting a photo from Flickr
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, TypeVar

import flickrapi
import requests

from .metadata import GpsInfo, PhotoComment, PhotoMetadata

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limit retry settings
# ---------------------------------------------------------------------------

# Flickr API error codes that indicate a transient / rate-limit condition
_RETRYABLE_FLICKR_ERROR_CODES = {
    105,  # Service currently unavailable
    10,   # Rate limit exceeded (observed in some Flickr responses)
}

_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.0  # seconds; actual delay = base * 2^attempt

_T = TypeVar("_T")

# Flickr size labels in descending order of resolution
_SIZE_PRIORITY = [
    "Original",
    "Large 2048",
    "Large 1600",
    "Large",
    "Medium 800",
    "Medium 640",
    "Medium",
    "Small 320",
    "Small",
    "Thumbnail",
    "Square",
    "Large Square",
]


class FlickrClient:
    """Thin wrapper around flickrapi.FlickrAPI."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str | None = None,
        access_token_secret: str | None = None,
        request_delay: float = 0.5,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token = access_token
        self._access_token_secret = access_token_secret
        self._flickr: flickrapi.FlickrAPI | None = None
        self._request_delay = request_delay  # seconds between consecutive API calls

    # ------------------------------------------------------------------
    # Rate-limit helpers
    # ------------------------------------------------------------------

    def _call_with_retry(self, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        """
        Invoke ``fn(*args, **kwargs)`` and retry with exponential back-off on
        transient Flickr errors (e.g. code 105 – service unavailable) or HTTP 429.

        A configurable inter-call delay (``request_delay``) is applied before
        every call to reduce the risk of hitting the rate limit in the first place.
        """
        for attempt in range(_MAX_RETRIES + 1):
            if self._request_delay > 0:
                time.sleep(self._request_delay)
            try:
                return fn(*args, **kwargs)
            except flickrapi.exceptions.FlickrError as exc:
                code = _flickr_error_code(exc)
                if code in _RETRYABLE_FLICKR_ERROR_CODES and attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "Flickr API error %s on attempt %d/%d. Retrying in %.1fs…",
                        code,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    raise
            except requests.exceptions.HTTPError as exc:
                if (
                    exc.response is not None
                    and exc.response.status_code == 429
                    and attempt < _MAX_RETRIES
                ):
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "HTTP 429 rate-limit on attempt %d/%d. Retrying in %.1fs…",
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    raise

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self, token_dir: "Path | None" = None) -> flickrapi.FlickrAPI:
        """
        Authenticate with Flickr using OAuth.

        If access token / secret are already available (env vars),
        they are used directly.  Otherwise, a browser-based PIN flow is started
        and the obtained token is cached by flickrapi in `token_dir`
        (default: the platform cache directory used by flickrapi, typically
        ``~/.flickr/``).

        Returns the authenticated FlickrAPI instance.
        """
        cache_location = str(token_dir) if token_dir else None
        flickr = flickrapi.FlickrAPI(
            self._api_key,
            self._api_secret,
            format="parsed-json",
            store_token=True,
            cache=True,
            token_cache_location=cache_location,
        )

        if self._access_token and self._access_token_secret:
            flickr.set_current_token(
                flickrapi.auth.FlickrAccessToken(
                    self._access_token,
                    self._access_token_secret,
                    "delete",
                )
            )
        elif not flickr.token_valid(perms="delete"):
            # Perform 3-legged OAuth PIN flow
            flickr.get_request_token(oauth_callback="oob")
            authorize_url = flickr.auth_url(perms="delete")
            print(f"\nPlease open the following URL in your browser:\n  {authorize_url}")
            pin = input("Enter the PIN from Flickr: ").strip()
            flickr.get_access_token(pin)
            print("\nFlickr authentication successful. Token cached for future use.")

        self._flickr = flickr
        return flickr

    @property
    def api(self) -> flickrapi.FlickrAPI:
        if self._flickr is None:
            raise RuntimeError("Not authenticated. Call authenticate() first.")
        return self._flickr

    # ------------------------------------------------------------------
    # Photo listing
    # ------------------------------------------------------------------

    def get_all_photo_ids(self, user_id: str = "me") -> list[str]:
        """
        Return all photo IDs owned by the authenticated user.

        Uses photos.search with per_page=500 and iterates pages.
        """
        ids: list[str] = []
        page = 1
        while True:
            result = self._call_with_retry(
                self.api.photos.search,
                user_id=user_id,
                per_page=500,
                page=page,
                extras="date_upload,date_taken,geo,tags,url_o",
            )
            photos = result["photos"]
            for p in photos["photo"]:
                ids.append(p["id"])
            if page >= int(photos["pages"]):
                break
            page += 1
        logger.info("Found %d photos on Flickr.", len(ids))
        return ids

    # ------------------------------------------------------------------
    # Photo metadata
    # ------------------------------------------------------------------

    def get_photo_info(self, photo_id: str) -> dict[str, Any]:
        """Return the raw info dict for a single photo."""
        result = self._call_with_retry(self.api.photos.getInfo, photo_id=photo_id)
        return result["photo"]

    def get_photo_sizes(self, photo_id: str) -> list[dict[str, Any]]:
        """Return the list of available sizes for a photo."""
        result = self._call_with_retry(self.api.photos.getSizes, photo_id=photo_id)
        return result["sizes"]["size"]

    def get_best_download_url(self, photo_id: str) -> tuple[str, str]:
        """
        Return (url, label) of the highest-resolution available download URL.
        """
        sizes = self.get_photo_sizes(photo_id)
        size_map = {s["label"]: s["source"] for s in sizes}
        for label in _SIZE_PRIORITY:
            if label in size_map:
                return size_map[label], label
        # Fallback: take the last (largest) entry in the list
        last = sizes[-1]
        return last["source"], last["label"]

    def get_albums_for_photo(self, photo_id: str, user_id: str = "me") -> list[dict[str, Any]]:
        """Return albums (photosets) that contain this photo."""
        result = self._call_with_retry(self.api.photos.getAllContexts, photo_id=photo_id)
        sets = result.get("set", [])
        return sets  # Each has 'id', 'title'

    def get_comments(self, photo_id: str) -> list[dict[str, Any]]:
        """Return comments for a photo."""
        result = self._call_with_retry(
            self.api.photos.comments.getList, photo_id=photo_id
        )
        comments_data = result.get("comments", {})
        return comments_data.get("comment", [])

    # ------------------------------------------------------------------
    # Full metadata assembly
    # ------------------------------------------------------------------

    def build_photo_metadata(self, photo_id: str) -> PhotoMetadata:
        """
        Fetch all available metadata from Flickr and return a PhotoMetadata object.
        """
        info = self.get_photo_info(photo_id)
        owner = info["owner"]

        # Title / description
        title = info.get("title", {}).get("_content", "")
        description = info.get("description", {}).get("_content", "")

        # Dates
        dates = info.get("dates", {})
        date_taken = dates.get("taken") or None
        date_upload_ts = dates.get("posted") or None
        last_update_ts = dates.get("lastupdate") or None

        # Tags
        tags_data = info.get("tags", {}).get("tag", [])
        tags = [t["raw"] for t in tags_data]

        # URLs
        flickr_url = info.get("urls", {}).get("url", [{}])[0].get("_content", "")
        if not flickr_url:
            flickr_url = f"https://www.flickr.com/photos/{owner['nsid']}/{photo_id}/"

        # GPS
        gps: GpsInfo | None = None
        location = info.get("location")
        if location:
            try:
                lat = float(location.get("latitude", 0))
                lon = float(location.get("longitude", 0))
                alt_str = location.get("altitude")
                alt = float(alt_str) if alt_str else None
                gps = GpsInfo(latitude=lat, longitude=lon, altitude=alt)
            except (TypeError, ValueError):
                pass

        # Albums
        album_contexts = self.get_albums_for_photo(photo_id)
        album_titles = [a["title"] for a in album_contexts]
        album_ids = [a["id"] for a in album_contexts]

        # Comments
        raw_comments = self.get_comments(photo_id)
        comments = [
            PhotoComment(
                author=c.get("author", ""),
                author_name=c.get("authorname", ""),
                date_create=c.get("datecreate", ""),
                content=c.get("_content", ""),
            )
            for c in raw_comments
        ]

        return PhotoMetadata(
            flickr_id=photo_id,
            flickr_url=flickr_url,
            title=title,
            description=description,
            date_taken=date_taken,
            date_upload=date_upload_ts,
            last_update=last_update_ts,
            tags=tags,
            albums=album_titles,
            album_ids=album_ids,
            gps=gps,
            comments=comments,
            original_format=info.get("originalformat", "jpg"),
            original_secret=info.get("originalsecret", ""),
            owner_nsid=owner.get("nsid", ""),
            owner_realname=owner.get("realname", ""),
            owner_username=owner.get("username", ""),
        )

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_photo(self, photo_id: str, dest_dir: Path) -> Path:
        """
        Download the highest-resolution version of a photo to `dest_dir`.

        Returns the path to the saved file.
        """
        url, label = self.get_best_download_url(photo_id)
        logger.debug("Downloading photo %s (%s) from %s", photo_id, label, url)

        # Derive a safe filename from the URL
        parsed = urllib.parse.urlparse(url)
        filename = os.path.basename(parsed.path) or f"{photo_id}.jpg"
        dest_path = dest_dir / filename

        if dest_path.exists():
            logger.debug("Photo %s already downloaded at %s", photo_id, dest_path)
            return dest_path

        dest_dir.mkdir(parents=True, exist_ok=True)
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        with dest_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)

        logger.info("Downloaded photo %s → %s", photo_id, dest_path)
        return dest_path

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete_photo(self, photo_id: str) -> None:
        """Delete a photo from Flickr (requires 'delete' permission)."""
        self._call_with_retry(self.api.photos.delete, photo_id=photo_id)
        logger.info("Deleted photo %s from Flickr.", photo_id)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _flickr_error_code(exc: flickrapi.exceptions.FlickrError) -> int | None:
    """
    Extract the numeric error code from a ``FlickrError`` exception.

    flickrapi formats the error message as ``"Error: <code>: <message>"``.
    Some versions of the library also expose a ``code`` attribute directly.
    Returns ``None`` if the code cannot be determined.
    """
    code = getattr(exc, "code", None)
    if code is not None:
        try:
            return int(code)
        except (TypeError, ValueError):
            pass
    # Parse from the string representation, e.g. "Error: 105: Service unavailable"
    msg = str(exc)
    parts = msg.split(":")
    if len(parts) >= 2:
        try:
            return int(parts[1].strip())
        except (TypeError, ValueError):
            pass
    return None
