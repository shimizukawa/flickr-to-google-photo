"""Tests for GooglePhotoClient.find_media_item_by_filename."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

from flickr_to_google_photo.google_photo_client import GooglePhotoClient

_API_BASE = "https://photoslibrary.googleapis.com/v1"


def _make_client(tmp_path: Path) -> GooglePhotoClient:
    """Return a GooglePhotoClient with a pre-set fake session."""
    client = GooglePhotoClient(
        client_secrets_file=tmp_path / "secrets.json",
        token_file=tmp_path / "token.json",
    )
    # Inject a fake session so _ensure_auth doesn't raise
    session = MagicMock()
    client._session = session
    creds = MagicMock()
    creds.expired = False
    client._credentials = creds
    return client


class TestFindMediaItemByFilename:
    def test_returns_none_when_no_date_taken(self, tmp_path):
        """Should return None immediately when date_taken is not provided."""
        client = _make_client(tmp_path)
        result = client.find_media_item_by_filename("photo.jpg", date_taken=None)
        assert result is None
        # Session should not have been called
        client._session.post.assert_not_called()

    def test_returns_none_for_unparseable_date(self, tmp_path):
        """Should return None when date_taken cannot be parsed."""
        client = _make_client(tmp_path)
        result = client.find_media_item_by_filename("photo.jpg", date_taken="not-a-date")
        assert result is None
        client._session.post.assert_not_called()

    def test_returns_id_when_filename_matches(self, tmp_path):
        """Should return media item ID when filename matches a search result."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {"id": "item_aaa", "filename": "other.jpg"},
                {"id": "item_bbb", "filename": "12345_o.jpg"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        result = client.find_media_item_by_filename("12345_o.jpg", date_taken="2023-06-15")
        assert result == "item_bbb"

    def test_returns_none_when_no_match(self, tmp_path):
        """Should return None when no item in the results matches the filename."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {"id": "item_aaa", "filename": "other.jpg"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        result = client.find_media_item_by_filename("missing.jpg", date_taken="2023-06-15")
        assert result is None

    def test_paginates_until_match_found(self, tmp_path):
        """Should follow nextPageToken until a match is found."""
        client = _make_client(tmp_path)

        page1 = {
            "mediaItems": [{"id": "item_aaa", "filename": "other.jpg"}],
            "nextPageToken": "tok123",
        }
        page2 = {
            "mediaItems": [{"id": "item_bbb", "filename": "target.jpg"}],
        }

        resp1 = MagicMock()
        resp1.raise_for_status = MagicMock()
        resp1.json.return_value = page1

        resp2 = MagicMock()
        resp2.raise_for_status = MagicMock()
        resp2.json.return_value = page2

        client._session.post.side_effect = [resp1, resp2]

        result = client.find_media_item_by_filename("target.jpg", date_taken="2023-06-15")
        assert result == "item_bbb"
        assert client._session.post.call_count == 2

        # Second call must include the pageToken
        _, kwargs = client._session.post.call_args_list[1]
        body = json.loads(kwargs["data"])
        assert body["pageToken"] == "tok123"

    def test_uses_date_filter_in_request(self, tmp_path):
        """Search request must include the correct dateFilter."""
        client = _make_client(tmp_path)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}
        client._session.post.return_value = mock_resp

        client.find_media_item_by_filename("photo.jpg", date_taken="2023-06-15 10:30:00")

        _, kwargs = client._session.post.call_args
        body = json.loads(kwargs["data"])
        date_filter = body["filters"]["dateFilter"]["dates"][0]
        assert date_filter == {"year": 2023, "month": 6, "day": 15}
