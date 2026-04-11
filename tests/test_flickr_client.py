"""Tests for FlickrClient rate-limit retry logic."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import flickrapi
import pytest
import requests

from flickr_to_google_photo.flickr_client import (
    FlickrClient,
    _flickr_error_code,
    _MAX_RETRIES,
    _RETRY_BASE_DELAY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(request_delay: float = 0.0) -> FlickrClient:
    """Return a FlickrClient with a pre-set mock FlickrAPI."""
    client = FlickrClient(
        api_key="key",
        api_secret="secret",
        request_delay=request_delay,
    )
    client._flickr = MagicMock()
    return client


def _flickr_error(code: int) -> flickrapi.exceptions.FlickrError:
    return flickrapi.exceptions.FlickrError(f"Error: {code}: some message")


# ---------------------------------------------------------------------------
# _flickr_error_code
# ---------------------------------------------------------------------------

class TestFlickrErrorCode:
    def test_parses_code_from_string(self):
        exc = flickrapi.exceptions.FlickrError("Error: 105: Service unavailable")
        assert _flickr_error_code(exc) == 105

    def test_parses_code_10(self):
        exc = flickrapi.exceptions.FlickrError("Error: 10: Rate limit exceeded")
        assert _flickr_error_code(exc) == 10

    def test_uses_code_attribute_if_present(self):
        exc = flickrapi.exceptions.FlickrError("some message")
        exc.code = 105  # type: ignore[attr-defined]
        assert _flickr_error_code(exc) == 105

    def test_returns_none_for_unparseable(self):
        exc = flickrapi.exceptions.FlickrError("not a structured error")
        assert _flickr_error_code(exc) is None


# ---------------------------------------------------------------------------
# _call_with_retry – success on first attempt
# ---------------------------------------------------------------------------

class TestCallWithRetrySuccess:
    def test_returns_result_immediately(self):
        client = _make_client()
        fn = MagicMock(return_value={"ok": True})

        with patch("flickr_to_google_photo.flickr_client.time.sleep"):
            result = client._call_with_retry(fn, a=1)

        assert result == {"ok": True}
        fn.assert_called_once_with(a=1)

    def test_request_delay_applied(self):
        client = _make_client(request_delay=0.3)
        fn = MagicMock(return_value="done")

        with patch("flickr_to_google_photo.flickr_client.time.sleep") as mock_sleep:
            client._call_with_retry(fn)

        # The inter-call delay must be the first sleep call.
        mock_sleep.assert_called_with(0.3)

    def test_no_sleep_when_delay_is_zero(self):
        client = _make_client(request_delay=0.0)
        fn = MagicMock(return_value="ok")

        with patch("flickr_to_google_photo.flickr_client.time.sleep") as mock_sleep:
            client._call_with_retry(fn)

        # No sleep call at all (neither inter-call nor retry)
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# _call_with_retry – retryable Flickr errors (codes 105 / 10)
# ---------------------------------------------------------------------------

class TestCallWithRetryFlickrError:
    @pytest.mark.parametrize("error_code", [105, 10])
    def test_retries_on_retryable_code(self, error_code):
        client = _make_client()
        err = _flickr_error(error_code)
        fn = MagicMock(side_effect=[err, err, "success"])

        with patch("flickr_to_google_photo.flickr_client.time.sleep"):
            result = client._call_with_retry(fn)

        assert result == "success"
        assert fn.call_count == 3

    def test_raises_immediately_on_non_retryable_code(self):
        client = _make_client()
        err = _flickr_error(1)  # code 1 = unknown user, not retryable
        fn = MagicMock(side_effect=err)

        with patch("flickr_to_google_photo.flickr_client.time.sleep"):
            with pytest.raises(flickrapi.exceptions.FlickrError):
                client._call_with_retry(fn)

        fn.assert_called_once()

    def test_raises_after_max_retries(self):
        client = _make_client()
        err = _flickr_error(105)
        fn = MagicMock(side_effect=err)

        with patch("flickr_to_google_photo.flickr_client.time.sleep"):
            with pytest.raises(flickrapi.exceptions.FlickrError):
                client._call_with_retry(fn)

        assert fn.call_count == _MAX_RETRIES + 1

    def test_exponential_backoff_delays(self):
        client = _make_client(request_delay=0.0)
        err = _flickr_error(105)
        fn = MagicMock(side_effect=[err, err, "ok"])

        sleep_calls = []
        with patch(
            "flickr_to_google_photo.flickr_client.time.sleep",
            side_effect=lambda s: sleep_calls.append(s),
        ):
            client._call_with_retry(fn)

        # Expect two retry delays: 1.0 * 2^0 = 1.0 and 1.0 * 2^1 = 2.0
        assert sleep_calls == [_RETRY_BASE_DELAY * (2**0), _RETRY_BASE_DELAY * (2**1)]


# ---------------------------------------------------------------------------
# _call_with_retry – HTTP 429 rate-limit
# ---------------------------------------------------------------------------

class TestCallWithRetryHttp429:
    def _make_429_error(self) -> requests.exceptions.HTTPError:
        resp = MagicMock()
        resp.status_code = 429
        exc = requests.exceptions.HTTPError(response=resp)
        return exc

    def test_retries_on_429(self):
        client = _make_client()
        err = self._make_429_error()
        fn = MagicMock(side_effect=[err, "success"])

        with patch("flickr_to_google_photo.flickr_client.time.sleep"):
            result = client._call_with_retry(fn)

        assert result == "success"
        assert fn.call_count == 2

    def test_raises_immediately_on_non_429_http_error(self):
        client = _make_client()
        resp = MagicMock()
        resp.status_code = 500
        err = requests.exceptions.HTTPError(response=resp)
        fn = MagicMock(side_effect=err)

        with patch("flickr_to_google_photo.flickr_client.time.sleep"):
            with pytest.raises(requests.exceptions.HTTPError):
                client._call_with_retry(fn)

        fn.assert_called_once()

    def test_raises_after_max_retries(self):
        client = _make_client()
        err = self._make_429_error()
        fn = MagicMock(side_effect=err)

        with patch("flickr_to_google_photo.flickr_client.time.sleep"):
            with pytest.raises(requests.exceptions.HTTPError):
                client._call_with_retry(fn)

        assert fn.call_count == _MAX_RETRIES + 1


# ---------------------------------------------------------------------------
# High-level API methods use _call_with_retry
# ---------------------------------------------------------------------------

class TestApiMethodsUseRetry:
    """Smoke-test that public methods delegate to _call_with_retry."""

    def test_get_photo_info_uses_retry(self):
        client = _make_client()
        client._flickr.photos.getInfo.return_value = {"photo": {"id": "1"}}  # type: ignore

        with patch.object(client, "_call_with_retry", wraps=client._call_with_retry) as spy:
            with patch("flickr_to_google_photo.flickr_client.time.sleep"):
                client.get_photo_info("1")

        spy.assert_called_once()

    def test_get_photo_sizes_uses_retry(self):
        client = _make_client()
        client._flickr.photos.getSizes.return_value = {"sizes": {"size": []}}  # type: ignore

        with patch.object(client, "_call_with_retry", wraps=client._call_with_retry) as spy:
            with patch("flickr_to_google_photo.flickr_client.time.sleep"):
                client.get_photo_sizes("1")

        spy.assert_called_once()

    def test_delete_photo_uses_retry(self):
        client = _make_client()
        client._flickr.photos.delete.return_value = {}  # type: ignore

        with patch.object(client, "_call_with_retry", wraps=client._call_with_retry) as spy:
            with patch("flickr_to_google_photo.flickr_client.time.sleep"):
                client.delete_photo("1")

        spy.assert_called_once()
