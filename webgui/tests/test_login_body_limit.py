"""The sign-in form refuses an oversized body before it parses it.

Audit SE-03: ``POST /login`` is the one route open to the internet without a
session, and it parsed whatever body it was sent, of any size, before the
throttle was consulted. A real sign-in form is a few hundred bytes.
"""
import pytest
from fastapi.testclient import TestClient

import auth_middleware
import login_page
import main


@pytest.mark.parametrize("headers,ok", [
    ({"content-length": "300"}, True),
    ({"content-length": str(login_page.MAX_BODY_BYTES)}, True),
    ({"content-length": str(login_page.MAX_BODY_BYTES + 1)}, False),
    ({"content-length": "50000000"}, False),
    ({}, False),                                   # chunked: no length to check
    ({"content-length": "abc"}, False),
    ({"content-length": "-5"}, False),
    ({"content-length": "1e3"}, False),
])
def test_only_a_stated_small_length_is_within_the_limit(headers, ok):
    assert login_page.body_within_limit(headers) is ok


def test_the_limit_fits_a_real_form_many_times_over():
    assert 4 * 1024 <= login_page.MAX_BODY_BYTES <= 64 * 1024


@pytest.fixture
def stranger():
    return TestClient(main.app, base_url="https://testserver",
                      headers={auth_middleware.EDGE_HEADER: "1"},
                      raise_server_exceptions=False)


def test_an_oversized_post_is_never_parsed(stranger, monkeypatch):
    parsed = []
    from starlette.requests import Request
    real = Request.form

    def spy(self, *a, **k):
        parsed.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(Request, "form", spy)
    big = "password=" + "x" * (login_page.MAX_BODY_BYTES + 100)
    r = stranger.post("/login", content=big,
                      headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "<form" in r.text     # the form again: a refusal
    assert parsed == [], "the oversized body was handed to the form parser"


def test_a_normal_post_is_still_parsed(stranger, monkeypatch):
    parsed = []
    from starlette.requests import Request
    real = Request.form

    def spy(self, *a, **k):
        parsed.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(Request, "form", spy)
    r = stranger.post("/login", data={"password": "wrong", "code": "000000"})
    assert r.status_code == 200
    assert parsed == [1]
