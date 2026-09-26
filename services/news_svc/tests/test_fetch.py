"""fetch.http_fetch: the one network call, driven through a fake ``requests.get``."""
import pytest
import requests

from services.news_svc import fetch


class FakeResponse:
    def __init__(self, status=200, chunks=(b"<rss/>",), headers=None, raise_after=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = list(chunks)
        self._raise_after = raise_after
        self.closed = False
        self.read = 0

    def iter_content(self, chunk_size=1):
        for i, c in enumerate(self._chunks):
            if self._raise_after is not None and i >= self._raise_after:
                raise requests.exceptions.ChunkedEncodingError("cut off")
            self.read += len(c)
            yield c

    def close(self):
        self.closed = True


@pytest.fixture
def fake_get(monkeypatch):
    state = {"calls": [], "response": FakeResponse()}

    def _get(url, **kw):
        state["calls"].append((url, kw))
        if isinstance(state["response"], Exception):
            raise state["response"]
        return state["response"]

    monkeypatch.setattr(fetch.requests, "get", _get)
    return state


def test_a_200_returns_the_body_and_the_validators(fake_get):
    fake_get["response"] = FakeResponse(chunks=(b"<rss>", b"</rss>"),
                                        headers={"ETag": "e1", "Last-Modified": "lm"})
    got = fetch.http_fetch("https://x", user_agent="UA", timeout=7, max_bytes=100)
    assert (got.status, got.body, got.etag, got.last_modified) == (200, b"<rss></rss>", "e1", "lm")
    url, kw = fake_get["calls"][0]
    assert url == "https://x" and kw["timeout"] == 7 and kw["stream"] is True
    assert kw["headers"]["User-Agent"] == "UA"
    assert fake_get["response"].closed


def test_conditional_headers_are_sent_and_a_304_is_not_an_error(fake_get):
    fake_get["response"] = FakeResponse(status=304, chunks=())
    got = fetch.http_fetch("https://x", etag="e0", last_modified="lm0", max_bytes=100)
    headers = fake_get["calls"][0][1]["headers"]
    assert headers["If-None-Match"] == "e0" and headers["If-Modified-Since"] == "lm0"
    assert (got.status, got.body, got.etag, got.last_modified) == (304, b"", "e0", "lm0")


def test_no_user_agent_falls_back_to_the_browser_style_default(fake_get):
    from shared import news_config as nc
    fetch.http_fetch("https://x", max_bytes=100)
    ua = fake_get["calls"][0][1]["headers"]["User-Agent"]
    assert ua == nc.DEFAULTS["collector"]["feed_user_agent"]


def test_a_non_200_raises_with_its_status(fake_get):
    fake_get["response"] = FakeResponse(status=404)
    with pytest.raises(fetch.FetchError) as err:
        fetch.http_fetch("https://x", max_bytes=100)
    assert err.value.status == 404 and "404" in str(err.value)
    assert fake_get["response"].closed


def test_a_network_error_is_a_fetch_error_with_no_status(fake_get):
    fake_get["response"] = requests.ConnectionError("down")
    with pytest.raises(fetch.FetchError) as err:
        fetch.http_fetch("https://x", max_bytes=100)
    assert err.value.status is None


def test_a_body_over_the_cap_is_refused_while_streaming(fake_get):
    """No Content-Length: the cap is enforced on the bytes as they arrive, and
    reading stops at the first chunk past it."""
    fake_get["response"] = FakeResponse(chunks=[b"x" * 40] * 10)
    with pytest.raises(fetch.FetchError, match="too large"):
        fetch.http_fetch("https://x", max_bytes=100)
    assert fake_get["response"].read <= 160
    assert fake_get["response"].closed


def test_a_declared_length_over_the_cap_is_refused_before_reading(fake_get):
    fake_get["response"] = FakeResponse(chunks=[b"x" * 10], headers={"Content-Length": "500"})
    with pytest.raises(fetch.FetchError, match="too large"):
        fetch.http_fetch("https://x", max_bytes=100)
    assert fake_get["response"].read == 0


def test_a_body_exactly_at_the_cap_is_kept(fake_get):
    fake_get["response"] = FakeResponse(chunks=[b"x" * 50, b"y" * 50])
    assert len(fetch.http_fetch("https://x", max_bytes=100).body) == 100


def test_the_default_cap_is_the_configured_one(fake_get, monkeypatch):
    monkeypatch.setattr(fetch, "_default_max_bytes", lambda: 10)
    fake_get["response"] = FakeResponse(chunks=[b"x" * 11])
    with pytest.raises(fetch.FetchError, match="too large"):
        fetch.http_fetch("https://x")


def test_the_shipped_default_cap_is_five_megabytes():
    assert fetch._default_max_bytes() == 5_000_000


def test_a_stream_cut_off_mid_body_is_a_fetch_error(fake_get):
    fake_get["response"] = FakeResponse(chunks=[b"a", b"b"], raise_after=1)
    with pytest.raises(fetch.FetchError):
        fetch.http_fetch("https://x", max_bytes=100)
    assert fake_get["response"].closed
