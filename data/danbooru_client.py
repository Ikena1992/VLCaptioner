"""Shared authenticated Danbooru request helpers."""

from __future__ import annotations

import requests
import time
import math
from threading import Lock
from contextlib import contextmanager
from contextvars import ContextVar

from runtime_config import CONFIG_FILE, read_settings

DEFAULT_USER_AGENT = "VLCaptioner/1.0 (personal dataset captioner)"
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.35
DEFAULT_RATE_LIMIT_RETRIES = 4
_request_lock = Lock()
_last_request_time = 0.0
_request_context = ContextVar('danbooru_request_context', default=(None, None))
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class RequestCancelled(requests.RequestException):
    """An API operation was cancelled before its next request."""


@contextmanager
def request_context(cancel_event=None, on_retry=None):
    token = _request_context.set((cancel_event, on_retry))
    try:
        yield
    finally:
        _request_context.reset(token)


def _wait(seconds, cancel_event):
    if cancel_event is None:
        time.sleep(seconds)
    elif cancel_event.wait(seconds):
        raise RequestCancelled('Danbooru request cancelled')


def read_danbooru_config() -> dict[str, str]:
    """Read Danbooru settings from the shared private configuration file."""
    if not CONFIG_FILE.is_file():
        raise RuntimeError(
            "Configuration is required. Copy config.example.txt to config.txt "
            "in the repository root and add your settings."
        )
    return {
        key: value.strip().strip('"\'')
        for key, value in read_settings(CONFIG_FILE).items()
    }


def get_danbooru_auth() -> tuple[str, str]:
    """Return credentials from the config file or fail before a request."""
    settings = read_danbooru_config()
    login = settings.get("DANBOORU_LOGIN", "")
    api_key = settings.get("DANBOORU_API_KEY", "")

    if not login or not api_key:
        raise RuntimeError(
            f"DANBOORU_LOGIN and DANBOORU_API_KEY are required in {CONFIG_FILE}."
        )

    return login, api_key


def get_danbooru_headers() -> dict[str, str]:
    """Return configured request headers without exposing credentials."""
    settings = read_danbooru_config()
    return {
        "User-Agent": settings.get("DANBOORU_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "application/json",
    }


def configure_danbooru_session(session: requests.Session) -> requests.Session:
    """Configure an existing requests session for authenticated Danbooru calls."""
    session.auth = get_danbooru_auth()
    session.headers.update(get_danbooru_headers())
    return session


def danbooru_get(url: str, **kwargs) -> requests.Response:
    """Make a rate-limited, retrying authenticated GET request to Danbooru."""
    global _last_request_time
    retries = kwargs.pop("rate_limit_retries", DEFAULT_RATE_LIMIT_RETRIES)
    interval = kwargs.pop("request_interval", DEFAULT_REQUEST_INTERVAL_SECONDS)
    cancel_event, on_retry = _request_context.get()

    for attempt in range(retries + 1):
        with _request_lock:
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled('Danbooru request cancelled')
            delay = interval - (time.monotonic() - _last_request_time)
            if delay > 0:
                _wait(delay, cancel_event)
            response = requests.get(
                url,
                auth=get_danbooru_auth(),
                headers=get_danbooru_headers(),
                **kwargs,
            )
            _last_request_time = time.monotonic()

        if response.status_code not in RETRYABLE_STATUSES or attempt >= retries:
            return response

        retry_after = response.headers.get("Retry-After", "")
        try:
            retry_delay = float(retry_after)
            if not math.isfinite(retry_delay):
                raise ValueError("Non-finite Retry-After")
            retry_delay = max(retry_delay, 2 ** (attempt + 1))
        except (TypeError, ValueError):
            retry_delay = 2 ** (attempt + 1)
        response.close()
        if on_retry is not None:
            kind = 'rate limit' if response.status_code == 429 else 'temporary server error'
            on_retry(f'Danbooru HTTP {response.status_code} ({kind}); retry {attempt + 1}/{retries} in {retry_delay:g}s…')
        _wait(retry_delay, cancel_event)

    return response
