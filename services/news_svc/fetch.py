"""One conditional HTTP GET - the only network call in the service; injectable.

The body is STREAMED and refused once it passes ``max_bytes``
(``[collector] max_body_bytes``): a feed is a few hundred KB, so anything far
larger is a runaway or hostile response and must not be read into memory. The
cap counts DECODED bytes (``iter_content`` inflates gzip), so a compressed bomb
is stopped too. An oversized body raises ``TooLarge`` - the response's own
answer, which a retry cannot change - never a plain outage.

``requests``' ``timeout`` bounds each READ, not the request: a server sending
one byte every ``timeout - 1`` seconds never trips it, and holds the poll lock
(and the SEC pacing lock) for as long as it likes. So the whole request also
has a total ``deadline_s`` (default ``3 x timeout``), checked once the headers
are in and after every chunk. A single blocked read can still overrun it by at
most one ``timeout``.
"""
import time

import requests

from shared import news_config as _nc

_CHUNK = 64 * 1024
_DEADLINE_FACTOR = 3


class FetchError(Exception):
    """A fetch that produced no usable body. ``status`` is the HTTP status when
    the server answered (a 404 is the feed's answer, not an outage), ``None``
    for a network failure or a missed deadline."""

    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


class TooLarge(FetchError):
    """The body is over ``max_bytes``. ``status`` is None (the server answered
    200), but this is NOT an outage: fetching it again gets the same body."""


class Fetched:
    __slots__ = ("status", "body", "etag", "last_modified")

    def __init__(self, status, body, etag, last_modified):
        self.status, self.body, self.etag, self.last_modified = status, body, etag, last_modified


def _default_max_bytes() -> int:
    return int(_nc.load().get("collector", {}).get(
        "max_body_bytes", _nc.DEFAULTS["collector"]["max_body_bytes"]))


def _declared_length(headers):
    try:
        return int(headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None


def http_fetch(url, *, etag=None, last_modified=None, user_agent=None, timeout=20,
               max_bytes=None, deadline_s=None) -> Fetched:
    """GET ``url``. A 304 to a conditional request comes back as
    ``Fetched(304, b"", ...)``; anything else that is not a 200 with a body of
    at most ``max_bytes``, read within ``deadline_s``, raises FetchError."""
    cap = _default_max_bytes() if max_bytes is None else int(max_bytes)
    limit = float(timeout) * _DEADLINE_FACTOR if deadline_s is None else float(deadline_s)
    started = time.monotonic()

    def _check_deadline():
        if time.monotonic() - started > limit:
            raise FetchError(f"deadline of {limit:g}s exceeded {url}")

    headers = {"User-Agent": user_agent or _nc.DEFAULTS["collector"]["feed_user_agent"]}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        r = requests.get(url, headers=headers, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc
    try:
        if r.status_code == 304:
            if not (etag or last_modified):
                # Nothing was asked conditionally, so "not modified" answers no
                # question: an empty healthy poll would hide a broken server.
                raise FetchError(f"HTTP 304 without validators {url}", status=304)
            return Fetched(304, b"", etag, last_modified)
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code} {url}", status=r.status_code)
        declared = _declared_length(r.headers)
        if declared is not None and declared > cap:
            raise TooLarge(f"response too large ({declared} > {cap} bytes) {url}")
        _check_deadline()
        body = bytearray()
        for chunk in r.iter_content(chunk_size=_CHUNK):
            body += chunk
            if len(body) > cap:
                raise TooLarge(f"response too large (over {cap} bytes) {url}")
            _check_deadline()
        return Fetched(200, bytes(body), r.headers.get("ETag"), r.headers.get("Last-Modified"))
    except requests.RequestException as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        r.close()
