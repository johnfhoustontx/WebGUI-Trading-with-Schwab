"""Which addresses are ever asked for, and the one function that asks.

``localize`` decides what to request; ``http_fetch`` decides again, on the
text, before it connects. No test here reaches the network: ``localize`` gets
a fake ``fetch`` and ``http_fetch`` gets a replaced ``requests.get``. The last
test is of the guard that makes forgetting either one fail.
"""
import concurrent.futures
import gzip
import io
import inspect
import re
import threading
from urllib.parse import urlsplit

import pytest
import requests
import urllib3

from services.blog_svc import clean, fonts
from services.blog_svc.tests._fonts_kit import (A, B, BACKSLASH, CYRILLIC_A, DELETE, E_ACUTE, G,
                                                LINK, LINK_2, NONE, ONE_DOT_LEADER, Requests,
                                                Response, SHIPPED_AGENT, Web, audit, face,
                                                fetched, name_of, rule, sheet, woff2)
from services.blog_svc.tests.conftest import NetworkReached, guard_the_network
from shared import blog_inbox


def test_every_request_is_sent_as_a_desktop_chrome_and_capped():
    """Google serves woff2, split by unicode-range, only to a browser it knows
    can take it. The stylesheet and the files have different size limits."""
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    audit(fonts.localize([LINK], fetch=web))
    assert re.fullmatch(r"Mozilla/5\.0 \(Windows NT 10\.0; Win64; x64\) AppleWebKit/537\.36 "
                        r"\(KHTML, like Gecko\) Chrome/\d+\.0\.0\.0 Safari/537\.36",
                        SHIPPED_AGENT)
    cfg = blog_inbox.fonts()
    assert [call["headers"]["User-Agent"] for call in web.calls] == [SHIPPED_AGENT] * 2
    assert [call["max_bytes"] for call in web.calls] == [cfg["max_css_kb"] * 1024,
                                                         cfg["max_file_kb"] * 1024]
    assert all(0 < call["timeout"] <= cfg["timeout_sec"] for call in web.calls)


def test_the_browser_named_to_google_is_a_setting(monkeypatch):
    """It ages, and the cure used to be a code change and a promote. What is
    sent is the SETTING; nothing in ``fonts.py`` holds a string of its own."""
    assert not hasattr(fonts, "USER_AGENT")
    newer = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/160.0.0.0 Safari/537.36")
    pages = {LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A}

    web = Web(pages)
    audit(fonts.localize([LINK], fetch=web, cfg={"user_agent": newer}))
    assert [call["headers"] for call in web.calls] == [{"User-Agent": newer}] * 2

    web = Web(pages)                                # ... and from the file, with no cfg
    with monkeypatch.context() as patch:
        patch.setattr(blog_inbox, "load", lambda: {"fonts": {"user_agent": newer}})
        audit(fonts.localize([LINK], fetch=web))
    assert [call["headers"] for call in web.calls] == [{"User-Agent": newer}] * 2

    web = Web(pages)                                # one that could not be a header
    audit(fonts.localize([LINK], fetch=web, cfg={"user_agent": newer + chr(0x0A) + "X: 1"}))
    assert [call["headers"] for call in web.calls] == [{"User-Agent": SHIPPED_AGENT}] * 2


def test_the_real_fetch_is_the_default_and_the_shipped_config_is_read(monkeypatch):
    assert inspect.signature(fonts.localize).parameters["fetch"].default is fonts.http_fetch
    assert (fonts.CSS_HOST, fonts.FILE_HOST) == ("fonts.googleapis.com", "fonts.gstatic.com")
    web = Web({LINK: sheet(face("greek", G + "g.woff2"), face("latin", G + "l.woff2")),
               G + "g.woff2": A, G + "l.woff2": B})
    monkeypatch.setattr(blog_inbox, "load", lambda: {"fonts": {"subsets": ["greek"]}})
    assert audit(fonts.localize([LINK], fetch=web)).files == {name_of(A): A}


NOT_A_STYLESHEET_LINK = [
    "http://fonts.googleapis.com/css2?family=Inter",
    "https://fonts.googleapis.com/css?family=Inter",                # the old endpoint
    "https://fonts.googleapis.com/css2",
    "https://fonts.googleapis.com/css2?",
    "https://fonts.googleapis.com/icon?family=Material+Icons",
    "https://fonts.googleapis.com.evil.test/css2?family=Inter",
    "https://fonts.googleapis.com@evil.test/css2?family=Inter",
    "https://evil.test/css2?family=Inter",
    "https://fonts.googleapis.com:8443/css2?family=Inter",
    "https://fonts.googleapis.com./css2?family=Inter",
    "https://FONTS.GOOGLEAPIS.COM/css2?family=Inter",
    "https://fonts.googleapis.com/css2?family=Inter#x",
    "https://fonts.googleapis.com/css2?family=Inter/../../x",
    "https://fonts.googleapis.com/css2?family=Inter\n",
    " https://fonts.googleapis.com/css2?family=Inter",
    "https://fonts.googleapis.com/css2?family=Caf" + E_ACUTE,
    "https://fonts.googleapis" + ONE_DOT_LEADER + "com/css2?family=Inter",
    "https://fonts.googleapis.com/css2?family=" + "A" * 5000,       # no real link is this long
    "//fonts.googleapis.com/css2?family=Inter",
    "", 7, None, b"https://fonts.googleapis.com/css2?family=Inter", ["nested"],
]


@pytest.mark.parametrize("link", NOT_A_STYLESHEET_LINK, ids=repr)
def test_only_a_google_fonts_stylesheet_link_is_followed(link):
    """The cleaner already matches the links it hands over. This module checks
    again rather than trust its caller: what it is given becomes a request."""
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    result = audit(fonts.localize([link, LINK], fetch=web))
    assert web.urls == [LINK, G + "a.woff2"]
    assert result.files == {name_of(A): A}                  # the good link is not lost
    assert result.note.startswith("Some typefaces were not copied")
    assert "1 link was not a Google Fonts stylesheet" in result.note


def test_a_link_is_checked_with_the_cleaners_pattern_itself(monkeypatch):
    """This module checks again what the cleaner already checked - with the
    cleaner's own ``FONT_LINK_RE``, by reference. There used to be a copy here
    and a test that the two were equal; with one pattern there is nothing to
    drift, and what is left to pin is that it IS the one consulted."""
    assert not hasattr(fonts, "_LINK_RE")
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(A)
    monkeypatch.setattr(clean, "FONT_LINK_RE", re.compile(r"^nothing matches this\Z"))
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert web.calls == [] and result.note == NONE + "1 link was not a Google Fonts stylesheet."


@pytest.mark.parametrize("links", [7, object(), {"a": 1}, b"bytes", [[LINK]], [None, 7],
                                   iter([LINK])], ids=repr)
def test_links_that_are_not_a_list_of_text_never_raise(links):
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    result = audit(fonts.localize(links, fetch=web))
    assert web.calls == [] and result.css == "" and result.note


def test_one_link_given_as_text_is_one_link_not_its_characters():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert audit(fonts.localize(LINK, fetch=web)).css == rule(A)


def test_only_googles_two_hosts_are_ever_fetched():
    """Whatever the links say and whatever the stylesheet says, every address
    handed to ``fetch`` is https on one of the two hosts - the stylesheet host
    for a link, the file host for a file, never the other way round."""
    hostile = [
        "https://evil.test/a.woff2", "http://fonts.gstatic.com/s/a.woff2",
        "https://fonts.gstatic.com.evil.test/s/a.woff2",
        "https://fonts.gstatic.com@evil.test/s/a.woff2",
        "https://user:pw@fonts.gstatic.com/s/a.woff2",
        "https://fonts.gstatic.com:8443/s/a.woff2", "https://fonts.gstatic.com./s/a.woff2",
        "https://FONTS.GSTATIC.COM/s/a.woff2", "//fonts.gstatic.com/s/a.woff2",
        "https://fonts.googleapis.com/s/a.woff2",           # the stylesheet host is not a file host
        "https://fonts.gstatic.com/s/a.ttf", "https://fonts.gstatic.com/s/a.woff2?x=1",
        "https://fonts.gstatic.com/s/a.woff2#x", "https://fonts.gstatic.com/s/../a.woff2",
        "https://fonts.gstatic.com/s/a%2e%2e/b.woff2", "https://fonts.gstatic.com//a.woff2",
        "https://fonts.gstatic.com/.woff2", "https://fonts.gstatic.com/s/" + "a" * 600 + ".woff2",
        "data:font/woff2;base64,d09GMg==", "../fonts/" + "0" * 20 + ".woff2",
        "/blog/fonts/" + "0" * 20 + ".woff2", "file:///etc/passwd", "a.woff2",
    ]
    css = sheet(*[face("latin", url, family=f"'F{n}'") for n, url in enumerate(hostile)],
                face("latin", G + "good.woff2"))
    web = Web({LINK: css, LINK_2: css, G + "good.woff2": A})
    result = audit(fonts.localize(
        [LINK, "https://evil.test/css2?family=X", "https://fonts.gstatic.com/css2?family=X",
         LINK_2], fetch=web))
    assert web.urls == [LINK, LINK_2, G + "good.woff2"]
    for url in web.urls:
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.netloc in (fonts.CSS_HOST, fonts.FILE_HOST)
    assert result.css == rule(A) and result.files == {name_of(A): A}
    assert f"{2 * len(hostile)} typeface rules were not usable" in result.note
    assert "2 links were not Google Fonts stylesheets" in result.note


def test_http_fetch_asks_once_follows_nothing_and_streams(monkeypatch):
    response = Response(chunks=(b"wOF2", b"", b"rest"))
    fake, body = fetched(monkeypatch, response)
    assert body == b"wOF2rest" and type(body) is bytes
    ((url, kwargs),) = fake.calls
    assert url == G + "a.woff2"
    assert kwargs == {"headers": {"User-Agent": SHIPPED_AGENT}, "timeout": 7,
                      "allow_redirects": False, "stream": True}
    assert response.closed


REFUSED_ADDRESSES = [
    "http://fonts.gstatic.com/s/a.woff2", "ftp://fonts.gstatic.com/s/a.woff2",
    "HTTPS://fonts.gstatic.com/s/a.woff2", "https:fonts.gstatic.com/s/a.woff2",
    "https:/fonts.gstatic.com/s/a.woff2", "https:///fonts.gstatic.com/s/a.woff2",
    "//fonts.gstatic.com/s/a.woff2", "fonts.gstatic.com/s/a.woff2",
    "https://evil.test/a.woff2", "https://gstatic.com/a.woff2",
    "https://www.fonts.gstatic.com/a.woff2",
    "https://fonts.gstatic.com.evil.test/a.woff2", "https://fonts.gstatic.com@evil.test/a.woff2",
    "https://evil.test@fonts.gstatic.com/a.woff2", "https://user:pw@fonts.gstatic.com/a.woff2",
    "https://@fonts.gstatic.com/a.woff2",
    "https://fonts.gstatic.com:443/a.woff2", "https://fonts.gstatic.com:8443/a.woff2",
    "https://fonts.gstatic.com:/a.woff2",
    "https://fonts.gstatic.com./a.woff2", "https://FONTS.GSTATIC.COM/a.woff2",
    "https://Fonts.Gstatic.Com/a.woff2",
    "https://142.250.72.14/a.woff2", "https://[::1]/a.woff2", "https://[fonts.gstatic.com]/a",
    "https://2398766094/a.woff2", "https://0x8efa480e/a.woff2", "https://localhost/a.woff2",
    "https://fonts.gstatic.com\\@evil.test/a.woff2", "https://evil.test\\fonts.gstatic.com/a",
    "https://fonts.gstatic.com\t/a.woff2", "https://fonts.gstatic.com /a.woff2",
    "https://fonts.gstatic.com/a.woff2\n", " https://fonts.gstatic.com/a.woff2",
    "https://fonts.gstatic.com/a b.woff2", "https://fonts.gstatic.com/a.woff2\x00",
    "https://fonts.gstatic" + ONE_DOT_LEADER + "com/a.woff2",
    "https://fonts.gst" + CYRILLIC_A + "tic.com/a.woff2",
    "https://fonts.gstatic.com/caf" + E_ACUTE + ".woff2",
    "https://xn--fonts-gstatic.com/a.woff2",
    "", None, 7, b"https://fonts.gstatic.com/a.woff2", ["https://fonts.gstatic.com/a.woff2"],
]


@pytest.mark.parametrize("url", REFUSED_ADDRESSES, ids=repr)
def test_http_fetch_refuses_an_address_before_it_connects(monkeypatch, url):
    """https, and a host that is EXACTLY one of the two: no userinfo, no port,
    no trailing dot, no other case, no number, nothing that is not ASCII. The
    refusal is made on the text - ``requests`` is never called."""
    fake, outcome = fetched(monkeypatch, Response(), url=url)
    assert isinstance(outcome, fonts.FetchRefused), outcome
    assert fake.calls == []


def test_a_backslash_in_the_path_of_a_file_address_is_refused(monkeypatch):
    """In the PATH, not only in the host: a browser reads a backslash as a
    slash, and nothing else does. Refused where the stylesheet is read, and
    again where the request is made."""
    odd = G + "a" + BACKSLASH + "b.woff2"
    # In a quoted url() a backslash is written twice to mean itself.
    quoted = 'url("' + G + "a" + BACKSLASH * 2 + 'b.woff2") format("woff2")'
    web = Web({LINK: sheet(face("latin", None, src=quoted)), odd: A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == "" and web.urls == [LINK]
    assert result.note == NONE + "1 typeface rule was not usable."
    for address in (odd, G + "a" + DELETE + "b.woff2", G + "a" + BACKSLASH,
                    "https://fonts.googleapis.com/css2?family=a" + BACKSLASH + "b"):
        fake = Requests(Response())
        monkeypatch.setattr(fonts.requests, "get", fake.get)
        with pytest.raises(fonts.FetchRefused):
            fonts.http_fetch(address, headers={}, timeout=7, max_bytes=1000)
        assert fake.calls == []


@pytest.mark.parametrize("url", [
    "https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;1,700&display=swap",
    "https://fonts.gstatic.com/s/inter/v13/UcC73FwrK3iLTeHuS_fvQtMwCp50KnMa1ZL7W0Q5nw.woff2",
])
def test_http_fetch_takes_both_of_googles_hosts(monkeypatch, url):
    fake, body = fetched(monkeypatch, Response(), url=url)
    assert body == b"body" and fake.calls[0][0] == url


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 204, 206, 304, 400, 403, 404,
                                    429, 500, 503, 0, None, "200"])
def test_http_fetch_refuses_another_host_and_a_redirect(monkeypatch, status):
    """A redirect is an address this module did not check, so none is followed:
    ``allow_redirects=False`` and then anything but a plain 200 is a failure -
    whatever the body, which is never read."""
    response = Response(status=status, headers={"Location": "https://evil.test/a.woff2"},
                        chunks=(woff2("x"),))
    fake, outcome = fetched(monkeypatch, response)
    assert isinstance(outcome, fonts.FetchError) and not isinstance(outcome, fonts.FetchRefused)
    # Spelled from a number only: this text is let into the log.
    assert str(outcome) == (f"HTTP {status}" if type(status) is int else "no HTTP status")
    assert fake.calls[0][1]["allow_redirects"] is False
    assert response.read == 0 and response.closed
    # ... and the other host, as the plan's test names it.
    fake, outcome = fetched(monkeypatch, Response(), url="https://evil.test/a.woff2")
    assert isinstance(outcome, fonts.FetchRefused) and fake.calls == []


def test_http_fetch_stops_reading_at_the_size_limit(monkeypatch):
    """Counted as the body arrives, because Content-Length is the other end's
    word: absent, wrong or a lie. An endless body costs one chunk past the cap."""
    def endless():
        while True:
            yield b"x" * 400

    response = Response(chunks=endless())
    _fake, outcome = fetched(monkeypatch, response, max_bytes=1000)
    assert isinstance(outcome, fonts.FetchError)
    assert response.read == 1200 and response.closed

    response = Response(chunks=(b"x" * 600, b"x" * 400))
    _fake, body = fetched(monkeypatch, response, max_bytes=1000)
    assert body == b"x" * 1000                              # exactly at the limit is inside it

    lying = Response(chunks=(b"x" * 600, b"x" * 401), headers={"Content-Length": "10"})
    _fake, outcome = fetched(monkeypatch, lying, max_bytes=1000)
    assert isinstance(outcome, fonts.FetchError) and lying.closed


def test_http_fetch_refuses_a_declared_size_over_the_limit_unread(monkeypatch):
    response = Response(chunks=(b"x",), headers={"Content-Length": "1001"})
    _fake, outcome = fetched(monkeypatch, response, max_bytes=1000)
    assert isinstance(outcome, fonts.FetchError)
    assert response.read == 0 and response.closed
    for odd in ("", "abc", "-1", "1e3", None):              # an odd header is just not a promise
        _fake, body = fetched(monkeypatch, Response(headers={"Content-Length": odd}))
        assert body == b"body"


def test_the_size_limit_holds_against_a_compressed_body(monkeypatch):
    """A body that is small on the wire and huge once inflated. The limit is on
    what is READ, and it holds only because the library hands over a compressed
    body a chunk of DECODED bytes at a time - which is its behaviour, not this
    module's. So this goes through the real ``requests.Response`` and the real
    urllib3 decoder (no socket: the "wire" is a buffer), and would fail on a
    library that inflated the whole body before handing over its first chunk."""
    wire = gzip.compress(b"x" * 5_000_000)
    assert len(wire) < 8 * 1024
    handed_over = []

    class Counting(bytearray):
        def __iadd__(self, chunk):
            handed_over.append(len(chunk))
            return super().__iadd__(chunk)

    def get(url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.raw = urllib3.response.HTTPResponse(
            body=io.BytesIO(wire), headers={"content-encoding": "gzip"}, status=200,
            preload_content=False)
        return response

    monkeypatch.setattr(fonts.requests, "get", get)
    monkeypatch.setattr(fonts, "bytearray", Counting, raising=False)
    with pytest.raises(fonts.FetchError, match="larger than 100000 bytes"):
        fonts.http_fetch(G + "a.woff2", headers={}, timeout=7, max_bytes=100_000)
    # Seven chunks of 16 KiB pass 100,000 bytes; the other 4.9 MB were never inflated.
    assert handed_over == [16 * 1024] * 7


@pytest.mark.parametrize("max_bytes", [0, -1, None, "1000", float("nan"), True])
def test_http_fetch_needs_a_real_size_limit(monkeypatch, max_bytes):
    fake, outcome = fetched(monkeypatch, Response(), max_bytes=max_bytes)
    assert isinstance(outcome, fonts.FetchRefused) and fake.calls == []


@pytest.mark.parametrize("timeout", [0, -1, None, "7", float("nan"), float("inf"), True,
                                     60.5, 61, 3600])
def test_http_fetch_needs_a_real_time_limit(monkeypatch, timeout):
    """``timeout=None`` is how ``requests`` spells "wait for ever". The most one
    request may be given is the most ``[fonts] timeout_sec`` may be set to."""
    assert blog_inbox.BOUNDS[("fonts", "timeout_sec")][1] == 60
    fake = Requests(Response())
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    with pytest.raises(fonts.FetchRefused):
        fonts.http_fetch(G + "a.woff2", headers={}, timeout=timeout, max_bytes=1000)
    assert fake.calls == []
    for longest_allowed in (60, 60.0, 0.5):
        assert fonts.http_fetch(G + "a.woff2", headers={}, timeout=longest_allowed,
                                max_bytes=1000) == b"body"


def test_http_fetch_sends_a_copy_of_the_headers_it_was_given(monkeypatch):
    given = {"User-Agent": SHIPPED_AGENT}
    fake = Requests(Response())
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    fonts.http_fetch(G + "a.woff2", headers=given, timeout=7, max_bytes=1000)
    sent = fake.calls[0][1]["headers"]
    assert sent == given and sent is not given
    for not_headers in (None, "User-Agent: x", [("User-Agent", "x")], 7):
        with pytest.raises(fonts.FetchRefused):
            fonts.http_fetch(G + "a.woff2", headers=not_headers, timeout=7, max_bytes=1000)
    assert len(fake.calls) == 1


def test_http_fetch_lets_a_network_failure_through_and_closes(monkeypatch):
    """``localize`` catches whatever this raises; what matters here is that the
    connection is given back whichever way the read ends."""
    _fake, outcome = fetched(monkeypatch, ConnectionError("refused"))
    assert isinstance(outcome, ConnectionError)
    response = Response(chunks=(b"half",), error=OSError("reset"))
    _fake, outcome = fetched(monkeypatch, response)
    assert isinstance(outcome, OSError) and response.closed


@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit, MemoryError])
def test_http_fetch_gives_the_connection_back_even_to_a_shutdown(monkeypatch, stop):
    response = Response(chunks=(b"half",), error=stop())
    fake = Requests(response)
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    with pytest.raises(stop):
        fonts.http_fetch(G + "a.woff2", headers={}, timeout=7, max_bytes=1000)
    assert response.closed


def test_http_fetch_gives_one_request_no_longer_than_its_timeout(monkeypatch):
    """``requests``' timeout bounds each read, not the request: a body arriving
    a chunk at a time, each just inside it, would otherwise never end."""
    now = [0.0]

    def dripping():
        while True:
            now[0] += 3
            yield b"x"

    monkeypatch.setattr(fonts, "monotonic", lambda: now[0])
    response = Response(chunks=dripping())
    _fake, outcome = fetched(monkeypatch, response, max_bytes=10 ** 6)
    assert isinstance(outcome, fonts.FetchError)
    assert response.read == 3 and response.closed           # 3 s, 6 s, 9 s: past 7 on the third


def test_localize_through_the_real_fetch_end_to_end(monkeypatch):
    """The two halves together, with only ``requests.get`` replaced: the
    default ``fetch`` really is called with what ``localize`` builds."""
    pages = {LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A}
    asked = []

    def get(url, **kwargs):
        asked.append((url, kwargs))
        return Response(chunks=(pages[url],))

    monkeypatch.setattr(fonts.requests, "get", get)
    result = audit(fonts.localize([LINK]))
    assert result.css == rule(A) and result.note == ""
    assert [url for url, _kw in asked] == [LINK, G + "a.woff2"]
    for _url, kwargs in asked:
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["headers"] == {"User-Agent": SHIPPED_AGENT}


def test_a_refusal_before_connecting_is_told_from_an_answer_that_was_no_good(monkeypatch):
    """Two kinds of ``FetchError``. ``FetchRefused``: this function turned down
    what it was HANDED - an address, headers, a limit - and never connected;
    that is a caller's mistake, and ``localize`` counts it as a fault. Plain
    ``FetchError``: it connected and the answer would not do - a status, a
    size, the time it took - which is the other end's."""
    assert issubclass(fonts.FetchRefused, fonts.FetchError)
    for response in (Response(status=503), Response(chunks=(b"x" * 2000,)),
                     Response(headers={"Content-Length": "5000"})):
        fake, outcome = fetched(monkeypatch, response)
        assert type(outcome) is fonts.FetchError and len(fake.calls) == 1
    for refused in ({"url": "https://evil.test/a.woff2"}, {"max_bytes": 0}):
        fake, outcome = fetched(monkeypatch, Response(), **refused)
        assert type(outcome) is fonts.FetchRefused and fake.calls == []


# ── the suite's own network guard ────────────────────────────────────────────
#
# ``conftest._no_real_network`` replaces what ``requests`` sends with. These
# are tests of THAT, against the code it guards: each reaches the guard on
# purpose, checks that the test doing so would have failed, and then says so
# (``guard.expected()``) so that it does not.

def test_a_test_that_forgets_its_fetch_fails_loudly(_no_real_network):
    """``localize`` catches ``Exception`` around every fetch, so a guard that
    raised one was turned into "1 stylesheet could not be fetched" and the
    forgetful test passed. The guard raises a ``BaseException`` for that
    reason, and it gets out."""
    guard = _no_real_network
    assert issubclass(NetworkReached, BaseException)
    assert not issubclass(NetworkReached, Exception)
    with pytest.raises(NetworkReached):
        fonts.localize([LINK])                      # no fetch=: the real one
    with pytest.raises(NetworkReached):
        fonts.http_fetch(G + "a.woff2", headers={}, timeout=1, max_bytes=10)
    with pytest.raises(AssertionError, match="reach the network"):
        guard.check()                               # what the fixture does when the test ends
    assert guard.expected() == ["MainThread", "MainThread"]
    guard.check()


def test_the_guard_fails_a_test_when_that_test_ends(_no_real_network):
    """The fixture, driven by hand with a monkeypatch of its own: on, reached,
    and then its last step - the check nothing else would notice missing."""
    with pytest.MonkeyPatch.context() as patch:
        quiet = guard_the_network(patch)
        next(quiet)
        with pytest.raises(StopIteration):          # nothing reached: the test passes
            next(quiet)
        running = guard_the_network(patch)
        inner = next(running)
        with pytest.raises(NetworkReached):
            requests.get(G + "a.woff2")
        assert inner.reached == ["MainThread"]
        with pytest.raises(AssertionError, match="reach the network"):
            next(running)
    assert _no_real_network.reached == []           # this test's own guard was never the one


def test_a_request_from_a_worker_thread_fails_the_test_too(_no_real_network):
    """Raising is not enough off the main thread. An exception that ends a
    worker thread is only a warning to pytest, and one raised inside a pool is
    not even that: the pool keeps it on a future nobody may ever look at. In
    both cases the test would PASS. So the guard also RECORDS every refusal,
    whichever thread made it, and the fixture fails the test at the end."""
    guard = _no_real_network
    seen = []

    def copies_with_no_fetch():
        try:
            fonts.localize([LINK])
        except BaseException as exc:                # what a supervisor might do: swallow it
            seen.append(type(exc))

    worker = threading.Thread(target=copies_with_no_fetch, name="copy-worker")
    worker.start()
    worker.join()
    assert seen == [NetworkReached] and guard.reached == ["copy-worker"]
    with pytest.raises(AssertionError, match="copy-worker"):
        guard.check()
    guard.expected()

    with concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="pool") as pool:
        future = pool.submit(fonts.localize, [LINK])
    assert isinstance(future.exception(), NetworkReached)     # kept here, raised nowhere
    assert [name.split("_")[0] for name in guard.reached] == ["pool"]
    with pytest.raises(AssertionError, match="pool"):
        guard.check()
    guard.expected()
