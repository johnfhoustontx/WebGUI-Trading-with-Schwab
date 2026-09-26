"""fetch.http_fetch: the one network call, driven through a fake ``fetch._open``
(the ``requests.get``-shaped seam) - plus a loopback server for the deadline,
which is about real sockets and cannot be proven with a fake clock alone."""
import socket
import threading
import time

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

    monkeypatch.setattr(fetch, "_open", _get)
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


# ── the overall deadline: requests' timeout is per READ, not per request ────

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class DripResponse(FakeResponse):
    """A server that sends one chunk every ``step`` seconds - never idle long
    enough for requests' per-read timeout, so only a total deadline stops it."""

    def __init__(self, clock, step, chunks):
        super().__init__(chunks=chunks)
        self._clock, self._step = clock, step

    def iter_content(self, chunk_size=1):
        for c in self._chunks:
            self._clock.t += self._step
            self.read += len(c)
            yield c


def test_a_drip_fed_body_is_stopped_by_the_total_deadline(fake_get, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(fetch.time, "monotonic", clock)
    fake_get["response"] = DripResponse(clock, 19, [b"x"] * 50)
    with pytest.raises(fetch.FetchError, match="deadline") as err:
        fetch.http_fetch("https://x", timeout=20, max_bytes=1000)   # default 3 x 20 s
    assert err.value.status is None                  # an outage, not the server's answer
    assert fake_get["response"].read == 4            # 76 s > 60 s: stopped at once
    assert fake_get["response"].closed


def test_the_deadline_is_checked_before_reading_the_body(fake_get, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(fetch.time, "monotonic", clock)
    resp = DripResponse(clock, 0, [b"x"])

    def slow_get(url, **kw):
        clock.t += 61                                 # headers took longer than 3 x 20 s
        return resp

    monkeypatch.setattr(fetch, "_open", slow_get)
    with pytest.raises(fetch.FetchError, match="deadline"):
        fetch.http_fetch("https://x", timeout=20, max_bytes=1000)
    assert resp.read == 0 and resp.closed


def test_an_explicit_deadline_overrides_the_default(fake_get, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(fetch.time, "monotonic", clock)
    fake_get["response"] = DripResponse(clock, 3, [b"x"] * 10)
    with pytest.raises(fetch.FetchError, match="deadline"):
        fetch.http_fetch("https://x", timeout=20, deadline_s=5, max_bytes=1000)
    assert fake_get["response"].read == 2


def test_a_body_inside_the_deadline_is_kept(fake_get, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(fetch.time, "monotonic", clock)
    fake_get["response"] = DripResponse(clock, 19, [b"a", b"b", b"c"])     # 57 s < 60 s
    assert fetch.http_fetch("https://x", timeout=20, max_bytes=1000).body == b"abc"


# ── "too large" is its own kind: the response's answer, not an outage ───────

def test_an_oversized_body_is_a_too_large_error(fake_get):
    fake_get["response"] = FakeResponse(chunks=[b"x" * 40] * 10)
    with pytest.raises(fetch.TooLarge):
        fetch.http_fetch("https://x", max_bytes=100)
    fake_get["response"] = FakeResponse(chunks=[b"x"], headers={"Content-Length": "500"})
    with pytest.raises(fetch.TooLarge) as err:
        fetch.http_fetch("https://x", max_bytes=100)
    assert isinstance(err.value, fetch.FetchError)


# ── a 304 nobody asked for ───────────────────────────────────────────────────

def test_a_304_without_validators_is_an_error_not_an_empty_poll(fake_get):
    fake_get["response"] = FakeResponse(status=304, chunks=())
    with pytest.raises(fetch.FetchError, match="304 without validators") as err:
        fetch.http_fetch("https://x", max_bytes=100)
    assert err.value.status == 304
    assert fake_get["response"].closed


# ── the deadline on a REAL socket: a watchdog, not a check between chunks ───
#
# urllib3 fills a whole chunk before ``iter_content`` yields, and headers are
# read before ``requests.get`` returns, so a check between chunks never runs
# against a server that drips. Only a watchdog that cuts the socket stops it.

class _DripServer:
    """One-connection loopback server: ``head`` is sent at once, then ``drip``
    one byte every ``step`` seconds until the client goes away or the test
    ends."""

    def __init__(self, head, drip, step=0.3):
        self.head, self.drip, self.step = head, drip, step
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(5)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/feed"
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            try:
                conn.recv(65536)                       # the request; content irrelevant
                if self.head:
                    conn.sendall(self.head)
                for b in self.drip:
                    if self.stop.wait(self.step):
                        return
                    conn.sendall(bytes([b]))
            except OSError:
                return                                 # the client cut us off: good

    def close(self):
        self.stop.set()
        self.sock.close()
        self.thread.join(5)


@pytest.fixture
def drip_server(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    servers = []

    def _make(head, drip, step=0.3):
        s = _DripServer(head, drip, step)
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.close()


def _timed_fetch(url, **kw):
    t0 = time.monotonic()
    with pytest.raises(fetch.FetchError, match="deadline") as err:
        fetch.http_fetch(url, max_bytes=10_000, **kw)
    return time.monotonic() - t0, err.value


def test_a_content_length_body_dripped_a_byte_at_a_time_is_cut_at_the_deadline(drip_server):
    srv = drip_server(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n", b"x" * 1000)
    elapsed, err = _timed_fetch(srv.url, timeout=1, deadline_s=1.5)
    assert elapsed < 2.0
    assert err.status is None


def test_headers_dripped_a_byte_at_a_time_are_cut_at_the_deadline(drip_server):
    srv = drip_server(b"HTTP/1.1 200 OK\r\n", b"X-Slow: " + b"a" * 1000)
    elapsed, err = _timed_fetch(srv.url, timeout=1, deadline_s=1.5)
    assert elapsed < 2.0
    assert err.status is None


def test_a_close_delimited_body_cut_by_the_deadline_is_an_error_not_a_short_body(drip_server):
    """Without a Content-Length the body ends at EOF - and cutting the socket IS
    an EOF, so a truncated body must not come back as a successful fetch."""
    srv = drip_server(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n", b"y" * 1000)
    elapsed, _ = _timed_fetch(srv.url, timeout=1, deadline_s=1.5)
    assert elapsed < 2.0


def test_a_fast_loopback_body_is_read_whole_and_the_watchdog_is_disarmed(drip_server):
    body = b"<rss>" + b"z" * 200 + b"</rss>"
    srv = drip_server(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nETag: e9\r\n\r\n" % len(body)
                      + body, b"", step=0)
    before = {t.name for t in threading.enumerate()}
    got = fetch.http_fetch(srv.url, timeout=1, deadline_s=1.5, max_bytes=10_000)
    assert (got.status, got.body, got.etag) == (200, body, "e9")
    time.sleep(0.05)
    leftover = {t.name for t in threading.enumerate()} - before
    assert not [n for n in leftover if n.startswith("news-fetch-deadline")]


# ── caller-supplied headers ─────────────────────────────────────────────────

def test_extra_headers_are_sent_and_cannot_replace_the_user_agent(fake_get):
    fetch.http_fetch("https://x", user_agent="UA", max_bytes=100,
                     headers={"Accept": "application/json", "User-Agent": "evil"})
    sent = fake_get["calls"][-1][1]["headers"]
    assert sent["Accept"] == "application/json" and sent["User-Agent"] == "UA"


def test_a_differently_cased_reserved_header_is_dropped_not_sent_beside_ours(fake_get):
    # HTTP names are case-insensitive: {"user-agent": ...} beside "User-Agent"
    # would put two User-Agent lines on the wire (or let requests pick one).
    fetch.http_fetch("https://x", user_agent="UA", etag="e0", last_modified="lm0",
                     max_bytes=100, headers={"user-agent": "evil",
                                             "if-none-match": "x", "IF-MODIFIED-SINCE": "y"})
    sent = fake_get["calls"][-1][1]["headers"]
    lowered = {k.lower(): v for k, v in sent.items()}
    assert len(lowered) == len(sent)
    assert lowered["user-agent"] == "UA"
    assert lowered["if-none-match"] == "e0" and lowered["if-modified-since"] == "lm0"


def test_a_caller_cannot_make_a_request_conditional_behind_the_validators_back(fake_get):
    # With no etag/last_modified the request must stay unconditional, so a 304
    # is still the "no validators" error rather than a silent empty poll.
    fake_get["response"] = FakeResponse(status=304, chunks=())
    with pytest.raises(fetch.FetchError):
        fetch.http_fetch("https://x", max_bytes=100,
                         headers={"If-None-Match": "x", "If-Modified-Since": "y"})
    sent = {k.lower() for k in fake_get["calls"][-1][1]["headers"]}
    assert "if-none-match" not in sent and "if-modified-since" not in sent


def test_no_extra_headers_sends_exactly_the_old_set(fake_get):
    fetch.http_fetch("https://x", user_agent="UA", max_bytes=100)
    assert fake_get["calls"][-1][1]["headers"] == {"User-Agent": "UA"}
