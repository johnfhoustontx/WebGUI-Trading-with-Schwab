"""Approving a sign-in from the phone, end to end through the real ``main.app``.

Two browsers: the one signing in (``browser``) and the owner's phone
(``phone``), which already holds a signed-in session. The push itself is
stubbed; everything else is the shipped routes. Same conventions as
``test_login_routes.py``: https base URL (the cookies are Secure), the edge
header, a real credentials file. Design:
docs/plans/2026-10-17-login-push-approval-design.md.
"""
import itsdangerous
import pytest
from starlette.testclient import TestClient

import auth
import auth_middleware
import auth_store
import login_page
import login_push
import main

PASSWORD = "hunter2"
SECRET = "JBSWY3DPEHPK3PXP" * 2
KEY = "k" * 43
EPOCH = 1
BASE = "https://testserver"
EDGE = {auth_middleware.EDGE_HEADER: "1"}
WAITING_COOKIE = "ns_pending"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    login_page.reset_lockout()
    login_push.reset_store()
    monkeypatch.setenv("NTFY_TOPIC", "t" * 32)
    yield
    login_page.reset_lockout()
    login_push.reset_store()


@pytest.fixture
def creds(tmp_path, monkeypatch):
    c = auth_store.Credentials(password_hash=auth.hash_password(PASSWORD),
                               totp_secret=SECRET, session_secret=KEY,
                               epoch=EPOCH, last_totp_counter=0)
    path = tmp_path / "webgui_auth.json"
    auth_store.save(c, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    return c


@pytest.fixture
def pushes(monkeypatch):
    sent = []

    def fake(p, *, app_url, post=None):
        sent.append((p, app_url))
        return True

    monkeypatch.setattr(login_push, "publish", fake)
    return sent


def _client(headers=None):
    h = dict(EDGE)
    h.update(headers or {})
    return TestClient(main.app, base_url=BASE, headers=h)


@pytest.fixture
def browser(creds):
    return _client({"x-forwarded-for": "198.51.100.7"})


@pytest.fixture
def phone(creds):
    c = _client({"x-forwarded-for": "198.51.100.99"})
    c.cookies.set(auth_middleware.SESSION_COOKIE,
                  auth.mint_token(KEY, kind=auth.KIND_SESSION, epoch=EPOCH))
    return c


def _sign_in(client, *, keep=False, next_path=None):
    data = {login_page.FIELD_PASSWORD: PASSWORD, login_page.FIELD_CODE: "",
            login_page.FIELD_FORM_TOKEN: login_page.issue_form_token()}
    if keep:
        data[login_page.FIELD_REMEMBER] = login_page.REMEMBER_ON
    if next_path is not None:
        data[login_page.FIELD_NEXT] = next_path
    return client.post(login_page.ROUTE, data=data, follow_redirects=False)


def _waiting(pushes):
    return pushes[-1][0]


def _approve(phone, p, number=None, decision="approve", token=None):
    n = p.number if number is None else number
    page = phone.get(f"/approve-sign-in?a={p.approval_id}&n={n}",
                     follow_redirects=False)
    assert page.status_code == 200, page.status_code
    data = {"a": p.approval_id, "n": str(n), "decision": decision,
            login_page.FIELD_FORM_TOKEN: (login_page.issue_form_token()
                                          if token is None else token)}
    return phone.post("/approve-sign-in", data=data, follow_redirects=False)


def _kind(token):
    return itsdangerous.URLSafeSerializer(KEY, salt=auth._SALT).loads(token)["kind"]


# ── the happy path ───────────────────────────────────────────────────────────
def test_the_form_offers_the_phone_when_configured(browser):
    page = browser.get(login_page.ROUTE).text
    assert "Leave empty to approve on your phone" in page


def test_empty_code_sends_one_push_and_shows_the_number(browser, pushes):
    r = _sign_in(browser)
    assert r.status_code == 303 and r.headers["location"] == login_page.ROUTE
    assert browser.cookies.get(WAITING_COOKIE)
    assert len(pushes) == 1
    p, app_url = pushes[0]
    assert app_url.startswith("https://")
    page = browser.get(login_page.ROUTE)
    assert page.status_code == 200
    assert str(p.number) in page.text
    assert 'http-equiv="refresh"' in page.text
    assert browser.cookies.get(auth_middleware.SESSION_COOKIE) is None


def test_the_waiting_cookie_never_carries_the_approval_id(browser, pushes):
    _sign_in(browser)
    p = _waiting(pushes)
    assert browser.cookies.get(WAITING_COOKIE) == p.pending_id
    assert p.approval_id not in browser.get(login_page.ROUTE).text


def test_phone_approves_and_the_browser_signs_in(browser, phone, pushes):
    _sign_in(browser)
    p = _waiting(pushes)
    page = phone.get(f"/approve-sign-in?a={p.approval_id}&n={p.number}")
    assert str(p.number) in page.text and "Approve" in page.text
    r = _approve(phone, p)
    assert r.status_code == 200 and "Approved" in r.text
    done = browser.get(login_page.ROUTE, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/desk"
    assert _kind(browser.cookies.get(auth_middleware.SESSION_COOKIE)) == auth.KIND_SESSION
    assert browser.cookies.get(WAITING_COOKIE) is None
    assert browser.cookies.get(auth_middleware.REMEMBER_COOKIE) is None


def test_keep_ticked_gives_the_30_day_session_and_remember_cookie(browser, phone, pushes):
    _sign_in(browser, keep=True)
    _approve(phone, _waiting(pushes))
    browser.get(login_page.ROUTE, follow_redirects=False)
    assert _kind(browser.cookies.get(auth_middleware.SESSION_COOKIE)) == \
        auth.KIND_TRUSTED_SESSION
    assert browser.cookies.get(auth_middleware.REMEMBER_COOKIE)


def test_a_collected_approval_clears_the_backoff(browser, phone, pushes):
    client = "198.51.100.7"
    login_page.lockout_state().record_failure(client, now=0, hashed=True)
    _sign_in(browser)
    _approve(phone, _waiting(pushes))
    browser.get(login_page.ROUTE, follow_redirects=False)
    assert not login_page.lockout_state()._per_client.get(client)


# ── refusals ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("how", ["wrong number", "not me"])
def test_wrong_number_or_not_me_refuses_and_the_browser_sees_the_form(
        browser, phone, pushes, how):
    _sign_in(browser)
    p = _waiting(pushes)
    if how == "wrong number":
        wrong = next(n for n in p.offered if n != p.number)
        r = _approve(phone, p, number=wrong)
    else:
        r = _approve(phone, p, decision="deny")
    assert "Not approved" in r.text
    page = browser.get(login_page.ROUTE, follow_redirects=False)
    assert page.status_code == 200
    assert "Not approved. Sign in again, or use your authenticator code." in page.text
    assert browser.cookies.get(WAITING_COOKIE) is None
    assert browser.cookies.get(auth_middleware.SESSION_COOKIE) is None


def test_the_approval_link_without_a_session_approves_nothing(browser, creds, pushes):
    """The stolen-topic case: someone who read the push, with no session."""
    _sign_in(browser)
    p = _waiting(pushes)
    stranger = _client()
    got = stranger.get(f"/approve-sign-in?a={p.approval_id}&n={p.number}",
                       follow_redirects=False)
    assert got.status_code == 303 and got.headers["location"].startswith("/login?next=")
    posted = stranger.post("/approve-sign-in", follow_redirects=False, data={
        "a": p.approval_id, "n": str(p.number), "decision": "approve",
        login_page.FIELD_FORM_TOKEN: login_page.issue_form_token()})
    assert posted.status_code == 303
    assert login_push.STORE.by_pending(p.pending_id).state == "waiting"


def test_the_pending_cookie_alone_collects_nothing_while_waiting(browser, pushes):
    _sign_in(browser)
    for _ in range(3):
        r = browser.get(login_page.ROUTE, follow_redirects=False)
        assert r.status_code == 200
    assert browser.cookies.get(auth_middleware.SESSION_COOKIE) is None


@pytest.mark.parametrize("token", ["", "not-a-token"])
def test_approve_post_needs_a_form_token(browser, phone, pushes, token):
    _sign_in(browser)
    p = _waiting(pushes)
    r = _approve(phone, p, token=token)
    assert "Approved" not in r.text
    assert login_push.STORE.by_pending(p.pending_id).state == "waiting"


def test_answers_are_not_counted_by_the_lockout(browser, phone, pushes):
    _sign_in(browser)
    p = _waiting(pushes)
    _approve(phone, p, number=next(n for n in p.offered if n != p.number))
    assert not login_page.lockout_state()._per_client.get("198.51.100.99")
    assert not login_page.lockout_state()._per_client.get("198.51.100.7")


def test_a_wrong_password_sends_no_push(browser, pushes):
    data = {login_page.FIELD_PASSWORD: "wrong", login_page.FIELD_CODE: "",
            login_page.FIELD_FORM_TOKEN: login_page.issue_form_token()}
    r = browser.post(login_page.ROUTE, data=data, follow_redirects=False)
    assert r.status_code == 200 and pushes == []
    assert browser.cookies.get(WAITING_COOKIE) is None


# ── the review focus ─────────────────────────────────────────────────────────
def test_two_waiting_browsers_collect_only_their_own(creds, phone, pushes):
    a = _client({"x-forwarded-for": "198.51.100.1"})
    b = _client({"x-forwarded-for": "198.51.100.2"})
    _sign_in(a)
    pa = _waiting(pushes)
    _sign_in(b)
    _approve(phone, pa)
    assert b.get(login_page.ROUTE, follow_redirects=False).status_code == 200
    assert b.cookies.get(auth_middleware.SESSION_COOKIE) is None
    assert a.get(login_page.ROUTE, follow_redirects=False).status_code == 303
    assert a.cookies.get(auth_middleware.SESSION_COOKIE)


def test_approve_after_cancel_has_ended(browser, phone, pushes):
    _sign_in(browser)
    p = _waiting(pushes)
    form = browser.get(f"{login_page.ROUTE}?cancel=1", follow_redirects=False)
    assert form.status_code == 200 and "<form" in form.text
    assert browser.cookies.get(WAITING_COOKIE) is None
    page = phone.get(f"/approve-sign-in?a={p.approval_id}&n={p.number}")
    assert "This sign-in request has ended." in page.text


def test_unknown_pending_cookie_shows_the_form_and_clears_it(browser, pushes):
    _sign_in(browser)
    login_push.reset_store()                 # the web app restarted
    r = browser.get(login_page.ROUTE, follow_redirects=False)
    assert r.status_code == 200 and "<form" in r.text
    assert "Not approved. Sign in again, or use your authenticator code." in r.text
    assert browser.cookies.get(WAITING_COOKIE) is None


def test_publish_failure_falls_back_to_the_code(browser, monkeypatch):
    monkeypatch.setattr(login_push, "publish", lambda p, **k: False)
    r = _sign_in(browser)
    assert r.status_code == 200
    assert "The push could not be sent; use your authenticator code." in r.text
    assert browser.cookies.get(WAITING_COOKIE) is None
    assert not login_push.STORE._by_pending


def test_collect_redirects_to_the_validated_next(browser, phone, pushes):
    _sign_in(browser, next_path="//evil.example/x")
    _approve(phone, _waiting(pushes))
    r = browser.get(login_page.ROUTE, follow_redirects=False)
    assert r.headers["location"] == "/desk"


def test_open_paths_are_unchanged():
    assert auth_middleware.OPEN_PATHS == frozenset({"/login", "/favicon.ico"})


def test_without_a_topic_an_empty_code_fails_as_before(browser, pushes, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC")
    r = _sign_in(browser)
    assert r.status_code == 200 and login_page.GENERIC_FAILURE in r.text
    assert pushes == []
