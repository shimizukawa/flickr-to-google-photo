"""Tests for GooglePhotoClient.find_duplicate_media_item."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flickr_to_google_photo.google_photo_client import GooglePhotoClient, _timestamps_match

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


class TestFindDuplicateMediaItem:
    def test_returns_none_when_no_date_taken(self, tmp_path):
        """Should return None immediately when date_taken is not provided."""
        client = _make_client(tmp_path)
        result = client.find_duplicate_media_item("photo.jpg", date_taken=None)
        assert result is None
        # Session should not have been called
        client._session.post.assert_not_called()

    def test_returns_none_for_unparseable_date(self, tmp_path):
        """Should return None when date_taken cannot be parsed."""
        client = _make_client(tmp_path)
        result = client.find_duplicate_media_item("photo.jpg", date_taken="not-a-date")
        assert result is None
        client._session.post.assert_not_called()

    def test_returns_none_on_403(self, tmp_path):
        """Should return None gracefully when the API returns 403 (readonly scope not available)."""
        client = _make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        client._session.post.return_value = mock_resp

        result = client.find_duplicate_media_item("photo.jpg", date_taken="2023-06-15")
        assert result is None
        # raise_for_status should NOT have been called (we handled 403 ourselves)
        mock_resp.raise_for_status.assert_not_called()

    def test_returns_id_when_filename_matches(self, tmp_path):
        """Should return media item ID when filename matches a search result."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {"id": "item_aaa", "filename": "other.jpg", "mediaMetadata": {}},
                {"id": "item_bbb", "filename": "12345_o.jpg", "mediaMetadata": {}},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        result = client.find_duplicate_media_item("12345_o.jpg", date_taken="2023-06-15")
        assert result == "item_bbb"

    def test_returns_none_when_no_match(self, tmp_path):
        """Should return None when no item matches by filename or dimensions."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {
                    "id": "item_aaa",
                    "filename": "other.jpg",
                    "mediaMetadata": {"width": "1920", "height": "1080"},
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        result = client.find_duplicate_media_item(
            "missing.jpg", date_taken="2023-06-15", width=3024, height=4032
        )
        assert result is None

    def test_returns_id_when_dimensions_match(self, tmp_path):
        """Should return ID when filename differs but dimensions match (smartphone case)."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {
                    "id": "item_smartphone",
                    "filename": "IMG_1234.jpg",
                    "mediaMetadata": {"width": "3024", "height": "4032"},
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        # Flickr filename is different, but dimensions match
        result = client.find_duplicate_media_item(
            "98765_abc_o.jpg", date_taken="2023-06-15", width=3024, height=4032
        )
        assert result == "item_smartphone"

    def test_dimensions_not_checked_when_not_provided(self, tmp_path):
        """Dimension matching is skipped when width/height are None."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {
                    "id": "item_smartphone",
                    "filename": "IMG_1234.jpg",
                    "mediaMetadata": {"width": "3024", "height": "4032"},
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        # No dimensions provided → should not match by dimensions
        result = client.find_duplicate_media_item(
            "98765_abc_o.jpg", date_taken="2023-06-15"
        )
        assert result is None

    def test_paginates_until_match_found(self, tmp_path):
        """Should follow nextPageToken until a match is found."""
        client = _make_client(tmp_path)

        page1 = {
            "mediaItems": [{"id": "item_aaa", "filename": "other.jpg", "mediaMetadata": {}}],
            "nextPageToken": "tok123",
        }
        page2 = {
            "mediaItems": [{"id": "item_bbb", "filename": "target.jpg", "mediaMetadata": {}}],
        }

        resp1 = MagicMock()
        resp1.raise_for_status = MagicMock()
        resp1.json.return_value = page1

        resp2 = MagicMock()
        resp2.raise_for_status = MagicMock()
        resp2.json.return_value = page2

        client._session.post.side_effect = [resp1, resp2]

        result = client.find_duplicate_media_item("target.jpg", date_taken="2023-06-15")
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

        client.find_duplicate_media_item("photo.jpg", date_taken="2023-06-15 10:30:00")

        _, kwargs = client._session.post.call_args
        body = json.loads(kwargs["data"])
        date_filter = body["filters"]["dateFilter"]["dates"][0]
        assert date_filter == {"year": 2023, "month": 6, "day": 15}

    def test_returns_id_when_timestamp_matches(self, tmp_path):
        """Should return ID when minute:second of date_taken matches creationTime."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {
                    "id": "item_ts_match",
                    "filename": "IMG_1234.jpg",  # filename differs
                    "mediaMetadata": {
                        "width": "1920",
                        "height": "1080",  # dimensions differ
                        # UTC time: hour differs (timezone), but minute=30, second=45 matches
                        "creationTime": "2023-06-15T01:30:45Z",
                    },
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        # Flickr local time minute=30, second=45 → matches Google's 01:30:45 UTC
        result = client.find_duplicate_media_item(
            "98765_abc_o.jpg",
            date_taken="2023-06-15 10:30:45",
            width=3024,
            height=4032,
        )
        assert result == "item_ts_match"

    def test_timestamp_mismatch_does_not_match(self, tmp_path):
        """Should not match by timestamp when seconds differ."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {
                    "id": "item_ts_no_match",
                    "filename": "IMG_1234.jpg",
                    "mediaMetadata": {
                        "width": "1920",
                        "height": "1080",
                        "creationTime": "2023-06-15T01:30:46Z",  # second=46, not 45
                    },
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        result = client.find_duplicate_media_item(
            "98765_abc_o.jpg",
            date_taken="2023-06-15 10:30:45",
        )
        assert result is None

    def test_timestamp_checked_before_dimensions(self, tmp_path):
        """Timestamp match should be found even when dimensions also differ."""
        client = _make_client(tmp_path)

        search_response = {
            "mediaItems": [
                {
                    "id": "item_only_ts",
                    "filename": "OTHER.jpg",
                    "mediaMetadata": {
                        # Different dimensions — would NOT match by dimension alone
                        "width": "1280",
                        "height": "720",
                        "creationTime": "2023-06-15T01:30:45Z",  # minute=30, second=45
                    },
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = search_response
        client._session.post.return_value = mock_resp

        result = client.find_duplicate_media_item(
            "flickr_name.jpg",
            date_taken="2023-06-15 10:30:45",
            width=3024,
            height=4032,
        )
        assert result == "item_only_ts"


class TestTimestampsMatch:
    def test_matching_minute_and_second(self):
        """Same minute and second regardless of hour (timezone offset)."""
        assert _timestamps_match("2023-06-15 10:30:45", "2023-06-15T01:30:45Z") is True

    def test_different_minute(self):
        assert _timestamps_match("2023-06-15 10:30:45", "2023-06-15T01:31:45Z") is False

    def test_different_second(self):
        assert _timestamps_match("2023-06-15 10:30:45", "2023-06-15T01:30:46Z") is False

    def test_timezone_independence_jst(self):
        """JST (UTC+9): Flickr hour=10, Google UTC hour=01 → same minute:second."""
        assert _timestamps_match("2023-06-15 10:30:45", "2023-06-15T01:30:45Z") is True

    def test_timezone_independence_est(self):
        """EST (UTC-5): Flickr hour=10, Google UTC hour=15 → same minute:second."""
        assert _timestamps_match("2023-06-15 10:30:45", "2023-06-15T15:30:45Z") is True

    def test_none_flickr_date(self):
        assert _timestamps_match(None, "2023-06-15T01:30:45Z") is False

    def test_empty_google_time(self):
        assert _timestamps_match("2023-06-15 10:30:45", "") is False

    def test_invalid_flickr_date(self):
        assert _timestamps_match("not-a-date", "2023-06-15T01:30:45Z") is False

    def test_invalid_google_time(self):
        assert _timestamps_match("2023-06-15 10:30:45", "not-a-time") is False

    def test_iso8601_with_offset(self):
        """Google may return non-Z offset format."""
        assert _timestamps_match("2023-06-15 10:30:45", "2023-06-15T01:30:45+00:00") is True
