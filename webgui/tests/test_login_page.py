"""The login form and the attempt handler -- above all, the ORDER inside ``attempt``.

Everything Tasks 3-6 built is inert until this module calls it in the right
order, and the failure mode is silent: get the order wrong and the login still
works, the suite still passes, and the protections are decoration. So most of
this file is ordering tests, and each was mutation-checked against the specific
edit it is meant to catch (move ``locked_until`` after the hash; delete a
``record_failure``; drop the counter persist; drop the form-token check).

Like the sibling auth suites, every test passes ``now=`` explicitly and never
patches a clock -- so nothing here depends on which side of a 30 s TOTP window
the run happens to land on.
"""
import json
import logging

import pyotp
import pytest

import auth
import auth_store
import login_page

T0 = 1_757_000_000.0
PASSWORD = "hunter2"
SECRET = "JBSWY3DPEHPK3PXP" * 2      # 32 chars, comfortably over MIN_TOTP_SECRET_LEN
KEY = "k" * 43


@pytest.fixture(autouse=True)
def _fresh_lockout():
    """The module holds ONE ``LockoutState``, so tests share it.

    Without this the flood in the lockout-ordering test leaks into every test
    that runs after it, and the global counter (50 failures in 15 minutes)
    would eventually lock this file out of its own fixtures. It is the same
    discipline the fake bus needed once it stopped inventing a fresh store per
    instance: a test that wants clean state has to say so.
    """
    login_page.reset_lockout()
    yield
    login_page.reset_lockout()


def _creds(**over):
    base = dict(password_hash=auth.hash_password(PASSWORD), totp_secret=SECRET,
                session_secret=KEY, epoch=1, last_totp_counter=0)
    base.update(over)
    return auth_store.Credentials(**base)


@pytest.fixture
def creds(tmp_path, monkeypatch):
    c = _creds()
    path = tmp_path / "webgui_auth.json"
    auth_store.save(c, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    return c


def _attempt(**kw):
    """``login_page.attempt`` with an EMPTY trusted-device slot by default.

    Every test in this file predates the remember-device cookie and is about
    something else -- the ordering, the lockout, the generic message -- so
    each would otherwise carry a ``remember_token=None`` that says nothing.
    The route's own call site passes it explicitly, and the two tests directly
    above ORDERING 3 pin that ``attempt`` still refuses to run without it, so
    this convenience cannot hide the omission it exists to shorten.

    The remember-device behaviour itself is tested in ``test_login_routes.py``,
    end to end through the real POST handler.
    """
    kw.setdefault("remember_token", None)
    return login_page.attempt(**kw)


def _token(creds, *, now=T0):
    return login_page.mint_form_token(creds.session_secret, epoch=creds.epoch,
                                      now=now)


def _good_code(creds, *, now=T0):
    return pyotp.TOTP(creds.totp_secret).at(now)


def _unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", tmp_path / "absent.json")


def _corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "corrupt.json"
    path.write_text('{"password_hash": "x", "totp', encoding="utf-8")
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)


def _unusable_secret(tmp_path, monkeypatch):
    path = tmp_path / "badsecret.json"
    auth_store.save(_creds(totp_secret="not-base32!"), path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)


# ---------------------------------------------------------------------------
# The rendered form.

def test_the_form_carries_no_nicegui_runtime(creds):
    page = login_page.render_form(next_path="/desk", error=None, form_token="t")
    assert "_nicegui" not in page, "the login page must not need the NiceGUI bundle"
    assert "socket.io" not in page
    assert "<form" in page and 'method="post"' in page


def test_the_form_is_a_standalone_document_in_the_app_palette(creds):
    page = login_page.render_form(next_path="/desk", error=None, form_token="t")
    assert page.lstrip().lower().startswith("<!doctype html>")
    assert "<style>" in page
    for colour in ("#0c1424", "#101a30", "#213152", "#cdd8ee", "#2563eb"):
        assert colour in page


def test_the_form_does_not_reflect_the_next_path_unescaped(creds):
    page = login_page.render_form(next_path='"><script>alert(1)</script>',
                                  error=None, form_token="t")
    assert "<script>alert(1)</script>" not in page
    # ...and it is ESCAPED rather than dropped. Without this half the test would
    # pass just as well if render_form quietly discarded the value, which would
    # hide the fact that nothing anywhere is escaping anything.
    assert "&lt;script&gt;" in page


def test_the_form_does_not_reflect_the_error_or_the_token_unescaped(creds):
    page = login_page.render_form(next_path="/desk",
                                  error="<img src=x onerror=1>",
                                  form_token='"><script>alert(2)</script>')
    assert "<img src=x onerror=1>" not in page
    assert "<script>alert(2)</script>" not in page
    assert "&lt;img" in page


def test_the_form_carries_the_token_and_the_next_path_as_hidden_fields(creds):
    page = login_page.render_form(next_path="/options/gamma", error=None,
                                  form_token="tok-123")
    assert 'name="form_token"' in page and "tok-123" in page
    assert 'name="next"' in page and "/options/gamma" in page


def test_the_form_never_echoes_a_submitted_password(creds):
    """The password field must come back empty after a failure.

    A ``value=`` on a password input is how a mistyped password ends up in the
    browser's back-forward cache and in any log that captures the page body.
    """
    page = login_page.render_form(next_path="/desk",
                                  error=login_page.GENERIC_FAILURE,
                                  form_token="t")
    assert PASSWORD not in page
    field = page.split('type="password"')[1].split(">")[0]
    assert "value" not in field


# ---------------------------------------------------------------------------
# safe_next.

@pytest.mark.parametrize("candidate,expected", [
    ("/desk", "/desk"),
    ("/options/gamma?x=1", "/options/gamma?x=1"),
    ("/", "/"),
    ("https://evil.example/", login_page.DEFAULT_NEXT),
    ("//evil.example/", login_page.DEFAULT_NEXT),
    ("/\\evil.example", login_page.DEFAULT_NEXT),
    ("\\\\evil.example", login_page.DEFAULT_NEXT),
    ("desk", login_page.DEFAULT_NEXT),
    ("javascript:alert(1)", login_page.DEFAULT_NEXT),
    ("", login_page.DEFAULT_NEXT),
    (None, login_page.DEFAULT_NEXT),
    (b"/desk", login_page.DEFAULT_NEXT),
    (17, login_page.DEFAULT_NEXT),
])
def test_safe_next_refuses_anything_that_leaves_this_site(candidate, expected):
    assert login_page.safe_next(candidate) == expected


@pytest.mark.parametrize("candidate", [
    "/desk\nSet-Cookie: a=b",
    "/desk\r\nLocation: https://evil.example",
    "/desk\tx",
    "/desk\x00",
    "/" + "a" * 4096,
])
def test_safe_next_refuses_anything_that_could_forge_a_header(candidate):
    """``next`` is echoed into a ``Location:`` header by the caller.

    A newline there is response splitting and an unbounded one is a free
    amplifier -- both cheaper to refuse here than to remember to strip at every
    call site.
    """
    assert login_page.safe_next(candidate) == login_page.DEFAULT_NEXT


# ---------------------------------------------------------------------------
# The form token -- design mitigation 5, and the kind that must not collide.

def test_the_form_token_kind_is_neither_the_session_nor_the_remember_kind():
    """If it were, ``GET /login`` would hand an anonymous visitor a session.

    The whole reason ``auth`` carries a ``kind`` discriminator is that two
    tokens signed with the same key are otherwise interchangeable. This one is
    minted BEFORE authentication, so a collision here is not a downgrade of one
    credential into another -- it is a complete bypass.
    """
    assert login_page.FORM_TOKEN_KIND not in (auth.KIND_SESSION,
                                              auth.KIND_REMEMBER)


def test_a_session_token_is_not_accepted_as_a_form_token(creds):
    session = auth.mint_token(creds.session_secret, kind=auth.KIND_SESSION,
                              epoch=creds.epoch, now=T0)
    assert login_page.verify_form_token(session, creds, now=T0) is False


def test_a_form_token_is_accepted_only_within_its_short_life(creds):
    tok = _token(creds)
    assert login_page.verify_form_token(tok, creds, now=T0) is True
    assert login_page.verify_form_token(
        tok, creds, now=T0 + login_page.FORM_TOKEN_MAX_AGE_SEC - 1) is True
    assert login_page.verify_form_token(
        tok, creds, now=T0 + login_page.FORM_TOKEN_MAX_AGE_SEC + 1) is False


def test_an_epoch_bump_invalidates_an_outstanding_form_token(creds):
    tok = _token(creds)
    assert login_page.verify_form_token(tok, _creds(epoch=2), now=T0) is False


def test_issue_form_token_reads_the_store_at_call_time(creds):
    tok = login_page.issue_form_token(now=T0)
    assert tok and login_page.verify_form_token(tok, creds, now=T0) is True


def test_issue_form_token_returns_none_rather_than_raising_when_unconfigured(
        tmp_path, monkeypatch):
    _unconfigured(tmp_path, monkeypatch)
    assert login_page.issue_form_token(now=T0) is None


def test_issue_form_token_returns_none_rather_than_raising_on_a_corrupt_file(
        tmp_path, monkeypatch):
    _corrupt_file(tmp_path, monkeypatch)
    assert login_page.issue_form_token(now=T0) is None


# ---------------------------------------------------------------------------
# attempt() -- the happy path and the one generic message.

def test_a_correct_triple_succeeds_and_advances_the_totp_counter(creds):
    res = _attempt(password=PASSWORD, code=_good_code(creds),
                             client="1.1.1.1", form_token=_token(creds), now=T0)
    assert res.ok is True
    assert res.message == ""
    stored = auth_store.load()
    assert stored.last_totp_counter > 0
    assert stored.last_totp_counter == int(T0 // auth.TOTP_PERIOD_SEC)


def test_the_persisted_counter_refuses_a_replay_of_the_same_code(creds):
    """The ONE test that fails when the counter persist is dropped.

    ``verify_totp`` already refuses ``counter <= last_counter`` -- but
    ``last_counter`` only ever moves if this module writes it back. Without the
    persist the guard reads 0 forever and a captured code stays replayable for
    its whole drift window, with every other test in this file still green.
    """
    code = _good_code(creds)
    first = _attempt(password=PASSWORD, code=code, client="1.1.1.1",
                               form_token=_token(creds), now=T0)
    assert first.ok is True
    second = _attempt(password=PASSWORD, code=code, client="1.1.1.1",
                                form_token=_token(creds), now=T0 + 1)
    assert second.ok is False
    assert second.message == login_page.GENERIC_FAILURE


def test_a_counter_that_cannot_be_persisted_refuses_the_sign_in(creds, monkeypatch,
                                                                caplog):
    """Fail CLOSED when the replay guard cannot be advanced.

    Accepting here would hand back a session while leaving the code just used
    replayable for the rest of its window -- and an unwritable store would 500
    the public page if it were left to propagate. Loud in the log, generic on
    the page.
    """
    def _boom(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(auth_store, "save", _boom)
    with caplog.at_level(logging.WARNING):
        res = _attempt(password=PASSWORD, code=_good_code(creds),
                                 client="6.6.6.6", form_token=_token(creds),
                                 now=T0)
    assert res.ok is False and res.message == login_page.GENERIC_FAILURE
    assert login_page.lockout_state().tracked_failures("6.6.6.6") == 1
    assert any("read-only file system" in r.getMessage() for r in caplog.records)


def test_a_failed_attempt_never_rewinds_the_persisted_counter(creds):
    _attempt(password=PASSWORD, code=_good_code(creds),
                       client="1.1.1.1", form_token=_token(creds), now=T0)
    high = auth_store.load().last_totp_counter
    _attempt(password="wrong", code="000000", client="1.1.1.1",
                       form_token=_token(creds), now=T0 + 1)
    assert auth_store.load().last_totp_counter == high


def test_the_same_message_is_shown_for_a_bad_password_and_a_bad_code(creds):
    a = _attempt(password="wrong", code=_good_code(creds),
                           client="1.1.1.1", form_token=_token(creds), now=T0)
    b = _attempt(password=PASSWORD, code="000000",
                           client="1.1.1.1", form_token=_token(creds), now=T0)
    assert a.ok is False and b.ok is False
    assert a.message == b.message == login_page.GENERIC_FAILURE


def test_every_refusal_carries_the_identical_message(creds, tmp_path, monkeypatch):
    """One sentence, everywhere -- including the cheap pre-hash refusals.

    A distinct "too many attempts" or "your form expired" would tell an
    attacker which of the three gates they tripped, and would tell a scanner
    that the lockout exists at all.
    """
    messages = {
        _attempt(password="wrong", code="000000", client="a",
                           form_token=_token(creds), now=T0).message,
        _attempt(password=PASSWORD, code="000000", client="a",
                           form_token=_token(creds), now=T0).message,
        _attempt(password=PASSWORD, code=_good_code(creds), client="a",
                           form_token=None, now=T0).message,
        _attempt(password=PASSWORD, code=_good_code(creds), client="a",
                           form_token="garbage", now=T0).message,
        _attempt(password=PASSWORD, code=_good_code(creds), client="a",
                           form_token=_token(creds),
                           now=T0 + login_page.FORM_TOKEN_MAX_AGE_SEC + 1).message,
    }
    # The lockout refusal. Driven from LOCKOUT_THRESHOLD rather than relying on
    # the five attempts above happening to reach it -- retuning that constant
    # must move this test with the code, not turn it red.
    for _ in range(auth.LOCKOUT_THRESHOLD):
        _attempt(password="wrong", code="000000", client="locked",
                           form_token=_token(creds), now=T0)
    locked = _attempt(password=PASSWORD, code=_good_code(creds),
                                client="locked", form_token=_token(creds), now=T0)
    assert locked.ok is False
    messages.add(locked.message)
    # ...and the three that need a different store underneath.
    for setup in (_unconfigured, _corrupt_file, _unusable_secret):
        setup(tmp_path, monkeypatch)
        messages.add(_attempt(password=PASSWORD, code="000000",
                                        client="b", form_token="t",
                                        now=T0).message)
    assert messages == {login_page.GENERIC_FAILURE}


# ---------------------------------------------------------------------------
# A broken store refuses; it never 500s on a public page.

def test_an_unconfigured_app_refuses_rather_than_raising(tmp_path, monkeypatch,
                                                         caplog):
    _unconfigured(tmp_path, monkeypatch)
    with caplog.at_level(logging.WARNING):
        res = _attempt(password=PASSWORD, code="000000",
                                 client="1.1.1.1", form_token="t", now=T0)
    assert res.ok is False and res.message == login_page.GENERIC_FAILURE
    assert any("absent.json" in r.getMessage() for r in caplog.records)


def test_a_corrupt_credentials_file_refuses_and_names_the_file(
        tmp_path, monkeypatch, caplog):
    _corrupt_file(tmp_path, monkeypatch)
    with caplog.at_level(logging.WARNING):
        res = _attempt(password=PASSWORD, code="000000",
                                 client="1.1.1.1", form_token="t", now=T0)
    assert res.ok is False and res.message == login_page.GENERIC_FAILURE
    assert any("corrupt.json" in r.getMessage() for r in caplog.records)


def test_an_unusable_totp_secret_refuses_rather_than_raising(tmp_path, monkeypatch):
    """``verify_totp`` refuses a non-base32 secret; prove the call site agrees.

    A hand-edited secret must produce the generic refusal, not a
    ``binascii.Error`` traceback on an internet-facing page.
    """
    _unusable_secret(tmp_path, monkeypatch)
    c = auth_store.load()
    res = _attempt(password=PASSWORD, code="000000", client="1.1.1.1",
                             form_token=_token(c), now=T0)
    assert res.ok is False and res.message == login_page.GENERIC_FAILURE


def test_an_unparseable_password_hash_refuses_rather_than_raising(creds):
    auth_store.save(_creds(password_hash="not-an-argon2-hash"),
                    auth_store.DEFAULT_PATH)
    res = _attempt(password=PASSWORD, code=_good_code(creds),
                             client="1.1.1.1", form_token=_token(creds), now=T0)
    assert res.ok is False and res.message == login_page.GENERIC_FAILURE


# ---------------------------------------------------------------------------
# ORDERING 1: the lockout is consulted BEFORE Argon2.

def test_lockout_is_consulted_before_the_hash_is_computed(creds, monkeypatch):
    """Mitigation 2. A throttle behind the thing it throttles is decoration.

    ``before > 0`` is load-bearing, not decoration: without it this test passes
    vacuously whenever the hash is never reached at all -- which is exactly
    what a missing form token does -- and it would then keep passing with the
    lockout check moved to the very end of the function.
    """
    calls = []
    monkeypatch.setattr(auth, "verify_password",
                        lambda *a, **k: (calls.append(1), False)[1])
    for _ in range(auth.LOCKOUT_THRESHOLD + 2):
        _attempt(password="wrong", code="000000", client="9.9.9.9",
                           form_token=_token(creds), now=T0)
    before = len(calls)
    assert before > 0, "the hash was never reached, so this test proves nothing"
    res = _attempt(password="wrong", code="000000", client="9.9.9.9",
                             form_token=_token(creds), now=T0)
    assert res.ok is False
    assert len(calls) == before, "Argon2 ran despite the client being locked out"


def test_a_locked_out_client_does_not_even_reach_the_credentials_file(
        creds, monkeypatch):
    """The lockout is FIRST -- ahead of the store read as well as the hash.

    Sharpens the test above: a lockout check placed after ``load()`` still
    passes that one, and fails this one.
    """
    for _ in range(auth.LOCKOUT_THRESHOLD):
        _attempt(password="wrong", code="000000", client="9.9.9.9",
                           form_token=_token(creds), now=T0)
    loads = []
    monkeypatch.setattr(auth_store, "load",
                        lambda *a, **k: (loads.append(1), None)[1])
    _attempt(password="wrong", code="000000", client="9.9.9.9",
                       form_token="t", now=T0)
    assert loads == []


def test_a_correct_triple_is_still_refused_while_the_client_is_locked_out(creds):
    for _ in range(auth.LOCKOUT_THRESHOLD):
        _attempt(password="wrong", code="000000", client="9.9.9.9",
                           form_token=_token(creds), now=T0)
    res = _attempt(password=PASSWORD, code=_good_code(creds),
                             client="9.9.9.9", form_token=_token(creds), now=T0)
    assert res.ok is False


# ---------------------------------------------------------------------------
# ORDERING 2: the form token is checked BEFORE Argon2.

def test_a_post_without_a_form_token_never_reaches_the_hash(creds, monkeypatch):
    """Design mitigation 5. A bot POSTing blind must cost an HMAC, not 19 MiB."""
    calls = []
    monkeypatch.setattr(auth, "verify_password",
                        lambda *a, **k: (calls.append(1), False)[1])
    res = _attempt(password=PASSWORD, code="000000", client="2.2.2.2",
                             form_token=None, now=T0)
    assert res.ok is False
    assert calls == [], "Argon2 ran on a request with no form token"


def test_a_form_token_from_another_session_key_never_reaches_the_hash(
        creds, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "verify_password",
                        lambda *a, **k: (calls.append(1), False)[1])
    forged = auth.mint_token("z" * 43, kind=login_page.FORM_TOKEN_KIND,
                             epoch=1, now=T0)
    res = _attempt(password=PASSWORD, code="000000", client="2.2.2.2",
                             form_token=forged, now=T0)
    assert res.ok is False
    assert calls == [], "Argon2 ran on a request with a forged form token"


def test_an_expired_form_token_never_reaches_the_hash(creds, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "verify_password",
                        lambda *a, **k: (calls.append(1), False)[1])
    res = _attempt(
        password=PASSWORD, code="000000", client="2.2.2.2",
        form_token=_token(creds),
        now=T0 + login_page.FORM_TOKEN_MAX_AGE_SEC + 1)
    assert res.ok is False
    assert calls == []


def test_attempt_will_not_run_without_being_told_about_the_form_token(creds):
    """``form_token`` is keyword-only with NO default, exactly as ``kind`` is.

    A default is how a future call site silently skips the check -- the same
    reasoning ``auth.mint_token`` records for its own discriminator.
    """
    with pytest.raises(TypeError):
        login_page.attempt(password=PASSWORD, code="000000", client="2.2.2.2")


def test_attempt_will_not_run_without_being_told_about_the_remembered_device(creds):
    """``remember_token`` has no default either, and for the mirror reason.

    ``form_token``'s omission would skip a check; this one's would silently
    stop honouring a trusted device -- a failure whose only symptom is that
    "trust this device" quietly does nothing, which nobody reports as a bug.
    """
    with pytest.raises(TypeError):
        login_page.attempt(password=PASSWORD, code="000000", client="2.2.2.2",
                           form_token="t")


# ---------------------------------------------------------------------------
# ORDERING 3: every failure path records a failure.

@pytest.mark.parametrize("kwargs", [
    dict(password=PASSWORD, code="000000", form_token=None),
    dict(password=PASSWORD, code="000000", form_token="garbage"),
    dict(password="wrong", code="000000", form_token=...),
    dict(password=PASSWORD, code="000000", form_token=...),
])
def test_each_failure_path_records_a_failure(creds, kwargs):
    """A path that returns without recording is a loop an attacker runs free.

    Parametrised over the cheap pre-hash refusals as well as the two expensive
    ones, because the cheap ones are precisely where a ``return`` gets added
    without a ``record_failure`` beside it.
    """
    kw = dict(kwargs)
    if kw["form_token"] is ...:
        kw["form_token"] = _token(creds)
    res = _attempt(client="3.3.3.3", now=T0, **kw)
    assert res.ok is False
    assert login_page.lockout_state().tracked_failures("3.3.3.3") == 1


def test_a_broken_store_still_records_a_failure(tmp_path, monkeypatch):
    for setup in (_unconfigured, _corrupt_file):
        login_page.reset_lockout()
        setup(tmp_path, monkeypatch)
        _attempt(password=PASSWORD, code="000000", client="3.3.3.3",
                           form_token="t", now=T0)
        assert login_page.lockout_state().tracked_failures("3.3.3.3") == 1


def test_an_attempt_while_locked_out_still_records_a_failure(creds):
    """The backoff must keep growing while it is being hammered.

    Otherwise a locked-out client's requests are free of accounting, and it
    emerges from the window sitting exactly at the threshold rather than deeper
    into the exponential.
    """
    st = login_page.lockout_state()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        _attempt(password="wrong", code="000000", client="9.9.9.9",
                           form_token=_token(creds), now=T0)
    before = st.tracked_failures("9.9.9.9")
    _attempt(password="wrong", code="000000", client="9.9.9.9",
                       form_token=_token(creds), now=T0)
    assert st.tracked_failures("9.9.9.9") == before + 1


# ---------------------------------------------------------------------------
# ORDERING 4: record_success only once everything has passed.

def test_a_success_clears_that_clients_backoff(creds):
    st = login_page.lockout_state()
    for _ in range(auth.LOCKOUT_THRESHOLD - 1):
        _attempt(password="wrong", code="000000", client="4.4.4.4",
                           form_token=_token(creds), now=T0)
    assert st.tracked_failures("4.4.4.4") == auth.LOCKOUT_THRESHOLD - 1
    res = _attempt(password=PASSWORD, code=_good_code(creds),
                             client="4.4.4.4", form_token=_token(creds), now=T0)
    assert res.ok is True
    assert st.tracked_failures("4.4.4.4") == 0


@pytest.mark.parametrize("password,code", [
    ("wrong", "good"),      # right code, wrong password
    (PASSWORD, "000000"),   # right password, wrong code
])
def test_a_half_correct_attempt_does_not_clear_the_backoff(creds, password, code):
    """``record_success`` placed after the password check clears it on a bad code."""
    st = login_page.lockout_state()
    _attempt(password="wrong", code="000000", client="5.5.5.5",
                       form_token=_token(creds), now=T0)
    _attempt(password=password,
                       code=_good_code(creds) if code == "good" else code,
                       client="5.5.5.5", form_token=_token(creds), now=T0)
    assert st.tracked_failures("5.5.5.5") == 2


# ---------------------------------------------------------------------------
# Logging.

def test_every_failure_is_logged_at_warning_with_the_client_address(creds, caplog):
    with caplog.at_level(logging.WARNING):
        _attempt(password="wrong", code="000000", client="7.7.7.7",
                           form_token=_token(creds), now=T0)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings
    assert any("7.7.7.7" in r.getMessage() for r in warnings)


def test_a_failure_log_line_never_carries_the_password_or_the_code(creds, caplog):
    with caplog.at_level(logging.DEBUG):
        _attempt(password=PASSWORD, code="123456", client="7.7.7.7",
                           form_token=_token(creds), now=T0)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert PASSWORD not in joined
    assert "123456" not in joined


def test_the_login_logger_sits_under_the_webgui_logger():
    assert login_page.log.name.split(".")[0] == "webgui"


# ---------------------------------------------------------------------------
# The store is read through DEFAULT_PATH at CALL time, not bound at import.

def test_the_store_is_resolved_at_call_time(creds, tmp_path, monkeypatch):
    """Python binds default arguments at ``def`` time.

    This repo has a documented incident where exactly that made a "redirected"
    fixture write into the live database for six weeks. ``attempt`` must follow
    the monkeypatched ``DEFAULT_PATH``, never one captured at import.
    """
    other = tmp_path / "elsewhere.json"
    auth_store.save(_creds(password_hash=auth.hash_password("different")), other)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", other)
    assert _attempt(password=PASSWORD, code=_good_code(creds),
                              client="8.8.8.8", form_token=_token(creds),
                              now=T0).ok is False
    assert _attempt(password="different", code=_good_code(creds),
                              client="8.8.8.8", form_token=_token(creds),
                              now=T0).ok is True
    assert json.loads(other.read_text(encoding="utf-8"))["last_totp_counter"] > 0

def test_the_login_page_carries_the_mark():
    """THE FIRST PAGE ANYONE SEES WAS THE ONE STILL BRANDED AS THE FRAMEWORK.

    This page is hand-written HTML, not a NiceGUI page, so it gets none of
    ``main._page``'s favicon wiring. With no icon link at all a browser falls
    back to requesting ``/favicon.ico`` -- which ``auth_middleware.OPEN_PATHS``
    deliberately leaves open and NiceGUI answers with its own logo.

    The mark is inlined as a data URI rather than served from ``/static``: every
    static path is closed to unauthenticated requests, and opening one to
    decorate the login screen would trade a real control for a picture.
    """
    import login_page

    html_out = login_page.render_form(next_path="/desk", form_token="t", error=None)
    assert 'rel="icon"' in html_out, "the login page declares no favicon"
    assert "data:image/svg+xml," in html_out, "the icon is not inlined"
    assert "/static/" not in login_page._FAVICON_TAG, (
        "the login favicon must not depend on an authenticated static path")


def test_the_login_mark_is_the_same_drawing_as_everywhere_else():
    """A different shape here means the tab icon changes as you sign in."""
    import pathlib
    from urllib.parse import quote

    import login_page

    for path_d in ("M22 12 L32 23.5 L42 12", "M22 52 L32 41 L42 52"):
        assert path_d in login_page._FAVICON_SVG, f"login mark lost {path_d!r}"
        assert quote(path_d) in login_page._FAVICON_TAG

    app_mark = (pathlib.Path(__file__).resolve().parents[1]
                / "static" / "img" / "neuralstrike-mark.svg").read_text(encoding="utf-8")
    # The app header uses the LARGE drawing at 44px; this is the small one, as
    # the favicon should be -- so they share the accent, not the geometry.
    assert "#6b86ff" in app_mark and "#6b86ff" in login_page._FAVICON_SVG
