"""Signing in to Schwab again from the Status page, with no tailnet.

Until 2026-10-11 the Authorize button only opened the proxy's own ``/auth``
page at its Tailscale address. On the evening the sign-in lapsed, the one PC on
the tailnet could not open a TCP connection to any tailnet address (a VPN's
filter on that PC refused them), the phone had been off the tailnet for 25
days, and there was no way left to sign in short of an SSH tunnel.

The button now opens a dialog in the app: a link to Schwab's own login, a box
for the address Schwab redirects to, and the web app hands that address to the
proxy over loopback.
"""
import inspect

import pytest
import requests

import proxy as proxy_mod
from pages import status


class _Resp:
    def __init__(self, status_code=200, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload


# ── proxy.auth_info ──────────────────────────────────────────────────────────
def test_auth_info_returns_the_schwab_login_link(monkeypatch):
    seen = {}

    def get(url, timeout):
        seen["url"] = url
        return _Resp(200, {"auth_url": "https://api.schwabapi.com/v1/oauth/authorize?x=1",
                           "callback_url": "https://127.0.0.1:8182"})

    monkeypatch.setattr(proxy_mod.requests, "get", get)
    info = proxy_mod.auth_info()
    assert seen["url"] == f"{proxy_mod.PROXY_URL}/auth/info"
    assert info["auth_url"].startswith("https://api.schwabapi.com/")


@pytest.mark.parametrize("resp", [
    _Resp(404, {"detail": "Not Found"}),                 # a proxy that predates it
    _Resp(200, None, raises=True),
    _Resp(200, ["not", "a", "dict"]),
    _Resp(200, {"callback_url": "x"}),                   # no link
    _Resp(200, {"auth_url": 7}),
    _Resp(200, {"auth_url": "javascript:alert(1)"}),     # never a script URL
    _Resp(200, {"auth_url": "http://api.schwabapi.com/x"}),   # https only
])
def test_auth_info_is_none_for_anything_but_an_https_link(monkeypatch, resp):
    monkeypatch.setattr(proxy_mod.requests, "get", lambda url, timeout: resp)
    assert proxy_mod.auth_info() is None


def test_auth_info_is_none_when_the_proxy_is_down(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(proxy_mod.requests, "get", boom)
    assert proxy_mod.auth_info() is None


# ── proxy.auth_exchange ──────────────────────────────────────────────────────
def test_auth_exchange_posts_the_pasted_address_in_the_body(monkeypatch):
    seen = {}

    def post(url, json, timeout):
        seen.update(url=url, json=json, timeout=timeout)
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(proxy_mod.requests, "post", post)
    pasted = "https://127.0.0.1:8182/?code=C0.abc%40&session=s"
    assert proxy_mod.auth_exchange(pasted) == (True, "")
    assert seen["url"] == f"{proxy_mod.PROXY_URL}/auth/exchange"
    assert seen["json"] == {"url": pasted}
    # The code rides in the BODY: a query string would put it in the proxy's
    # access log.
    assert "code" not in seen["url"]
    # Longer than the 30 s the proxy itself gives Schwab.
    assert seen["timeout"] > 30


@pytest.mark.parametrize("resp, expected", [
    (_Resp(502, {"ok": False, "error": "Auth code exchange failed (400): invalid_grant"}),
     "Auth code exchange failed (400): invalid_grant"),
    (_Resp(400, {"ok": False, "error": "No authorization code in that address."}),
     "No authorization code in that address."),
    (_Resp(500, None, raises=True), "The proxy answered 500."),
    (_Resp(404, {"detail": "Not Found"}), "The proxy answered 404."),
    # A 200 that does not say ok is NOT a sign-in.
    (_Resp(200, {"ok": False}), "The proxy answered 200."),
    (_Resp(200, {}), "The proxy answered 200."),
    (_Resp(200, "ok"), "The proxy answered 200."),
])
def test_auth_exchange_reports_a_refusal_as_text_and_never_as_success(
        monkeypatch, resp, expected):
    monkeypatch.setattr(proxy_mod.requests, "post", lambda url, json, timeout: resp)
    assert proxy_mod.auth_exchange("https://x/?code=1") == (False, expected)


def test_auth_exchange_never_raises_when_the_proxy_is_down(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(proxy_mod.requests, "post", boom)
    ok, message = proxy_mod.auth_exchange("https://x/?code=1")
    assert ok is False and "did not answer" in message


def test_the_helper_still_holds_no_schwab_client_and_talks_only_to_the_proxy():
    src = inspect.getsource(proxy_mod)
    assert "schwabapi.com" not in src
    assert src.count("requests.post(") == 1          # the sign-in, and only it
    for line in src.splitlines():
        if "requests.get(" in line or "requests.post(" in line:
            assert 'f"{base_url}/' in line, line


# ── the dialog's own checks ──────────────────────────────────────────────────
@pytest.mark.parametrize("pasted", [
    "https://127.0.0.1:8182/?code=C0.abc%40&session=s",
    "  https://127.0.0.1:8182?code=x  ",
    "https://127.0.0.1:8182/?session=s&code=x",
])
def test_a_pasted_address_with_a_code_is_accepted(pasted):
    assert status.sign_in_paste_problem(pasted) is None


@pytest.mark.parametrize("pasted", ["", "   ", None])
def test_an_empty_box_asks_for_the_address(pasted):
    assert status.sign_in_paste_problem(pasted) == status.SIGN_IN_PASTE_EMPTY


@pytest.mark.parametrize("pasted", [
    "https://127.0.0.1:8182/",                           # the redirect, cut short
    "https://api.schwabapi.com/v1/oauth/authorize?client_id=k",   # the LOGIN link
    "C0.abc@",                                           # a bare code
    "https://127.0.0.1:8182/?code=",
    "https://127.0.0.1:8182/?barcode=1",
])
def test_an_address_with_no_code_is_refused_before_the_proxy_is_asked(pasted):
    assert status.sign_in_paste_problem(pasted) == status.SIGN_IN_PASTE_NO_CODE


def test_a_refusal_says_what_to_do_next():
    said = status.sign_in_failure_text("Auth code exchange failed (400): invalid_grant")
    assert "invalid_grant" in said
    assert "30 seconds" in said and "log in again" in said.lower()


# ── how long the sign-in has left ────────────────────────────────────────────
@pytest.mark.parametrize("hours, text", [
    (168.0, "7 days"),
    (166.4, "6 days 22 hours"),
    (49.0, "2 days 1 hour"),
    (48.0, "2 days"),
    (25.2, "1 day 1 hour"),
    (23.9, "23 hours"),
    (1.0, "1 hour"),
    (0.6, "under an hour"),
    (0.0, "under an hour"),
])
def test_time_left_in_words(hours, text):
    assert status.sign_in_left_text(hours) == text


@pytest.mark.parametrize("junk", [None, "12", True, float("nan"), float("inf"), -3])
def test_time_left_is_none_for_a_value_that_is_not_a_reading(junk):
    assert status.sign_in_left_text(junk) is None


def test_the_auth_line_says_how_long_the_sign_in_has_left():
    up, detail = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": False,
        "refresh_token_rejected": False, "token_expired": False,
        "refresh_token_hours_left": 166.4})
    assert up is True
    assert detail == "authorized — sign-in lasts another 6 days 22 hours"


def test_the_auth_line_is_unchanged_when_the_proxy_does_not_say():
    up, detail = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": False,
        "token_expired": False})
    assert (up, detail) == (True, "authorized — token valid")


def test_a_rejected_sign_in_never_shows_time_left():
    """The stamp said 165 hours while Schwab was rejecting the token."""
    up, detail = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": False,
        "refresh_token_rejected": True, "token_expired": True,
        "refresh_token_hours_left": 164.9})
    assert up is False and "lasts another" not in detail


# ── the page wires the dialog, not the tailnet link ──────────────────────────
def test_the_authorize_button_opens_the_dialog_and_not_a_tailnet_tab():
    src = inspect.getsource(status.render)
    assert "_authorize_clicked" in src
    assert "ui.navigate.to(AUTH_URL" not in src
    # The exchange is blocking (the proxy calls Schwab): never on the event loop.
    assert "run.io_bound(proxy.auth_exchange" in src
    assert "run.io_bound(proxy.auth_info" in src


def test_the_sign_in_is_refused_in_the_public_process():
    """The Status page is not published, and this is the second lock: the
    handlers read the process, not a flag a caller could leave out."""
    src = inspect.getsource(status.render)
    assert src.count("_shell.is_public()") >= 2
