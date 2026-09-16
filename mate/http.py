"""The only place that talks to the network.

Every open-data source here is volunteer-run and free. Politeness (identifying
User-Agent, per-host pacing, bounded retries) is a condition of use, so it lives
in one chokepoint rather than in five call sites that can each drift.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .settings import Settings

RETRIES = 2
BACKOFF_S = 1.5
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# Seconds between two requests to the same source. Nominatim's usage policy is
# an absolute maximum of 1 req/s; the rest are courtesy limits on donated CPUs.
RATE_LIMITS: dict[str, float] = {
    "nominatim": 1.1,
    "photon": 0.5,
    "overpass": 2.0,
    "wikidata": 0.3,
}
DEFAULT_RATE_LIMIT_S = 0.5


class SourceError(Exception):
    """A source did not answer usefully. Never swallowed into an empty result."""

    def __init__(self, source: str, message: str, status: int | None = None) -> None:
        tail = "" if status is None else f" [HTTP {status}]"
        super().__init__(f"{source}: {message}{tail}")
        self.source = source
        self.message = message
        self.status = status


_gate = threading.Lock()
_next_allowed: dict[str, float] = {}


def _throttle(key: str, interval: float) -> None:
    """Process-wide next-allowed time per source, on the monotonic clock, so two
    call sites cannot each believe they are the only one talking to Nominatim."""
    while True:
        with _gate:
            now = time.monotonic()
            ready = _next_allowed.get(key, 0.0)
            if now >= ready:
                _next_allowed[key] = now + interval
                return
            wait = ready - now
        time.sleep(wait)


def _read(resp: Any) -> str:
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode(resp.headers.get_content_charset() or "utf-8", "replace")


def _reason(e: urllib.error.HTTPError) -> str:
    """Overpass puts the real cause in the body; error pages of HTML are noise."""
    raw = e.read()
    if e.headers.get("Content-Encoding") == "gzip":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError):
            pass
    body = raw[:300].decode("utf-8", "replace").strip()
    return " ".join(body.split()) if body and not body.lstrip().startswith("<") \
        else (e.reason or "request rejected")


def _fetch(url: str, body: bytes | None, settings: Settings, source: str,
           timeout: float | None = None) -> Any:
    interval = RATE_LIMITS.get(source, DEFAULT_RATE_LIMIT_S)
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    for attempt in range(RETRIES + 1):
        _throttle(source, interval)
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or settings.http_timeout_s) as resp:
                text = _read(resp)
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise SourceError(source, f"malformed JSON: {e}") from e
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == RETRIES:
                raise SourceError(source, _reason(e), e.code) from e
        except OSError as e:  # URLError, timeouts, connection resets
            if attempt == RETRIES:
                raise SourceError(source, f"unreachable: {e}") from e
        time.sleep(BACKOFF_S * 2**attempt)

    raise SourceError(source, "retries exhausted")


def _source_of(url: str, rate_limit_key: str | None) -> str:
    """One name is both the error label and the rate-limit bucket: two sources
    behind one hostname would otherwise share a budget they do not share."""
    return rate_limit_key or urllib.parse.urlsplit(url).hostname or url


def get_json(
    url: str,
    params: dict[str, Any] | None,
    settings: Settings,
    *,
    rate_limit_key: str | None = None,
) -> Any:
    full = f"{url}?{urllib.parse.urlencode(params, doseq=True)}" if params else url
    return _fetch(full, None, settings, _source_of(url, rate_limit_key))


def post_form_json(
    url: str,
    data: dict[str, Any],
    settings: Settings,
    *,
    rate_limit_key: str | None = None,
    timeout: float | None = None,
) -> Any:
    body = urllib.parse.urlencode(data).encode("utf-8")
    return _fetch(url, body, settings, _source_of(url, rate_limit_key), timeout)
