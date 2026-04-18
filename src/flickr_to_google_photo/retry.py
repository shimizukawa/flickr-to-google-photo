"""Shared retry / exponential-backoff utilities."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 8
BACKOFF_BASE = 2.0  # delay = BACKOFF_BASE ** attempt  (1s, 2s, 4s, …)

_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

_T = TypeVar("_T")


def backoff_delay(attempt: int, retry_after_header: str | None = None) -> float:
    """Return how many seconds to sleep before the next attempt."""
    wait = BACKOFF_BASE**attempt
    if retry_after_header:
        try:
            wait = max(wait, float(retry_after_header))
        except ValueError:
            pass
    return wait


def http_request_with_backoff(
    fn: Callable[..., requests.Response], *args: Any, **kwargs: Any
) -> requests.Response:
    """
    Call ``fn(*args, **kwargs)`` and retry on transient HTTP errors (429, 5xx).

    Raises the underlying ``HTTPError`` once all retries are exhausted.
    """
    for attempt in range(MAX_RETRIES + 1):
        resp: requests.Response = fn(*args, **kwargs)
        if resp.status_code not in _RETRYABLE_HTTP_CODES:
            resp.raise_for_status()
            return resp
        if attempt == MAX_RETRIES:
            resp.raise_for_status()
        wait = backoff_delay(attempt, resp.headers.get("Retry-After"))
        logger.warning(
            "HTTP %d – retrying in %.1fs (attempt %d/%d)",
            resp.status_code,
            wait,
            attempt + 1,
            MAX_RETRIES,
        )
        time.sleep(wait)
    raise AssertionError("unreachable")


def call_with_backoff(
    fn: Callable[..., _T],
    *args: Any,
    is_retryable: Callable[[Exception], bool],
    request_delay: float = 0.0,
    **kwargs: Any,
) -> _T:
    """
    Call ``fn(*args, **kwargs)`` and retry with exponential back-off whenever
    ``is_retryable(exc)`` returns True.

    ``request_delay`` is an optional fixed sleep applied *before every* call
    to avoid hitting rate limits proactively.
    """
    for attempt in range(MAX_RETRIES + 1):
        if request_delay > 0:
            time.sleep(request_delay)
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if is_retryable(exc) and attempt < MAX_RETRIES:
                wait = backoff_delay(attempt)
                logger.warning(
                    "%s on attempt %d/%d. Retrying in %.1fs…",
                    exc,
                    attempt + 1,
                    MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
            else:
                raise
    raise AssertionError("unreachable")
