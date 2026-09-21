"""Tests for the Terminate page pure builder (webgui/pages/terminate.py)."""
import inspect

from pages import terminate


def test_stop_command_stops_this_environments_target():
    from repo_paths import ENV_NAME
    assert terminate.stop_command() == [
        "systemctl", "--user", "--no-block", "stop", f"trading-{ENV_NAME}.target"]


def test_stop_command_is_no_block_and_that_is_load_bearing():
    """`--no-block` registers the stop job with the systemd MANAGER and returns.

    This page kills the web app that issued the command. With --no-block the job
    is owned by systemd, so this process dying partway cannot orphan the
    shutdown -- which is strictly safer than the old `cmd /c start` detachment
    trick it replaces, where an independent console was the only thing keeping
    the batch alive."""
    assert "--no-block" in terminate.stop_command()


def test_stop_command_is_user_scoped():
    """A system-scoped stop would need root. The whole supervision design rests
    on `systemctl --user`."""
    assert terminate.stop_command()[:2] == ["systemctl", "--user"]


def test_no_windows_machinery_survives_anywhere_in_the_module():
    src = inspect.getsource(terminate)
    for banned in ("stop_all.bat", "STOP_BAT", "cmd /c", "taskkill",
                   "start_all_wt.bat", "start_all.bat"):
        assert banned not in src, banned


# --- copy honesty -------------------------------------------------------------
# The page's copy lives inline in render(), so these read the source — the same
# idiom test_shell.py and test_status.py use for wiring that can't be called
# under pytest. The BEHAVIOUR is already right: tools/stop_all.py drops the proxy
# unless OWNS_PROXY. Only the promise was wrong.
def test_copy_does_not_promise_to_stop_a_proxy_this_checkout_may_not_own():
    """Dev borrows prod's proxy on :8100 and stop_all leaves it alone. Copy that
    flatly promises to kill it is wrong there — and wrong copy about a
    destructive action either scares an operator off a button they're entitled
    to press, or makes them mistrust the result when the proxy survives."""
    src = inspect.getsource(terminate)
    assert "Stops the schwab-proxy" not in src
    assert "The proxy, all services" not in src
    assert "proxy + the six domain services" not in src   # the module docstring


def test_copy_states_that_stopping_the_proxy_is_ownership_conditional():
    """One wording that is true in BOTH environments, not dev-specific prose."""
    src = inspect.getsource(terminate)
    assert src.count("only in the environment that owns it") >= 2
    # The bus's "left running" note is unrelated to ownership and must survive.
    # It is REDIS on Linux, not Memurai -- the Windows port is gone, and the
    # reason it survives a stop is now structural: it is a SYSTEM unit, so a
    # `systemctl --user` target stop cannot reach it even in principle.
    assert "Redis" in src
    assert "Memurai" not in src


# --- TOTP step-up ------------------------------------------------------------
# Stopping the stack is reachable from the public internet behind ONE session
# cookie, and it costs a trading day: the GEX slots for the rest of the session,
# a live YouTube stream mid-broadcast, the driver stood down. So the confirm
# dialog asks for a fresh authenticator code as well.
#
# Like the sibling auth suites, every test here passes ``now=`` explicitly and
# never patches a clock, so nothing depends on which side of a 30 s TOTP window
# the run happens to land on.
import logging

import pyotp
import pytest

import auth
import auth_store
import login_page

T0 = 1_757_000_000.0
PASSWORD = "hunter2"
SECRET = "JBSWY3DPEHPK3PXP" * 2      # 32 chars, comfortably over the 16 floor
KEY = "k" * 43


def _code(at=T0):
    return pyotp.TOTP(SECRET, interval=auth.TOTP_PERIOD_SEC).at(at)


@pytest.fixture
def creds(tmp_path, monkeypatch):
    """A real credentials file on a tmp path.

    ``auth_store.load()`` is called with NO path so it resolves DEFAULT_PATH at
    CALL time -- which is what makes patching the module attribute enough, and is
    the shape this repo prefers after a fixture that patched a captured default
    wrote into the live database for six weeks.
    """
    c = auth_store.Credentials(password_hash=auth.hash_password(PASSWORD),
                               totp_secret=SECRET, session_secret=KEY,
                               epoch=1, last_totp_counter=0)
    path = tmp_path / "webgui_auth.json"
    auth_store.save(c, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    return c


@pytest.fixture(autouse=True)
def _fresh_lockout():
    """``login_page`` and ``terminate`` each hold ONE LockoutState, shared by
    every test in the run. The cross-path tests below drive real sign-ins and
    real rejected stop codes through them, so they clean up after themselves
    rather than leaking failures into whatever runs next.

    ⚠ The stop's counter matters here even more than the login's: several tests
    below deliberately reject a code, and five rejections is the throttle
    threshold. Without this reset the sixth test in the file starts throttled and
    fails for a reason that has nothing to do with what it asserts."""
    login_page.reset_lockout()
    terminate.reset_stop_lockout()
    yield
    login_page.reset_lockout()
    terminate.reset_stop_lockout()


def test_a_valid_code_authorizes_the_stop(creds):
    ok, msg = terminate.verify_stop_code(_code(), now=T0)
    assert ok is True
    assert msg == ""


def test_a_wrong_code_refuses_and_says_so_plainly(creds):
    """NOT the login form's generic sentence. The visitor here has already
    authenticated, so withholding which gate they hit protects nobody and just
    leaves them retyping a code that was never going to work."""
    ok, msg = terminate.verify_stop_code("000000", now=T0)
    assert ok is False
    assert msg == terminate.CODE_REJECTED
    assert msg != login_page.GENERIC_FAILURE
    assert "code" in msg.lower()


def test_a_missing_or_blank_code_refuses(creds):
    for empty in (None, "", "   "):
        ok, msg = terminate.verify_stop_code(empty, now=T0)
        assert ok is False, empty
        assert msg == terminate.CODE_REQUIRED, empty


def test_a_short_or_non_numeric_code_refuses(creds):
    for bad in ("12345", "1234567", "12345a", "12 456"):
        ok, _msg = terminate.verify_stop_code(bad, now=T0)
        assert ok is False, bad


def test_a_code_spent_on_the_stop_cannot_then_sign_in(creds):
    """The whole reason the accepted counter is PERSISTED.

    Verifying without saving looks identical from inside this module -- the stop
    is authorized either way -- and silently leaves the code usable at the login
    form for the rest of its window (90 s, with drift). This is the test that
    fails when the save is skipped.
    """
    code = _code()
    assert terminate.verify_stop_code(code, now=T0)[0] is True

    token = login_page.mint_form_token(creds.session_secret, epoch=creds.epoch,
                                       now=T0)
    result = login_page.attempt(password=PASSWORD, code=code, client="1.2.3.4",
                                form_token=token, remember_token=None, now=T0)
    assert result.ok is False


def test_a_code_spent_on_a_sign_in_cannot_then_stop_the_stack(creds):
    """The converse, and it is not implied by the first: the stop path has to
    READ ``last_totp_counter`` from the store on every call, not from a value it
    captured at import."""
    code = _code()
    token = login_page.mint_form_token(creds.session_secret, epoch=creds.epoch,
                                       now=T0)
    assert login_page.attempt(password=PASSWORD, code=code, client="1.2.3.4",
                              form_token=token, remember_token=None,
                              now=T0).ok is True

    ok, msg = terminate.verify_stop_code(code, now=T0)
    assert ok is False
    assert msg == terminate.CODE_REJECTED


def test_the_same_code_cannot_stop_the_stack_twice(creds):
    code = _code()
    assert terminate.verify_stop_code(code, now=T0)[0] is True
    assert terminate.verify_stop_code(code, now=T0)[0] is False


def test_an_unconfigured_credentials_file_refuses_rather_than_raising(
        tmp_path, monkeypatch):
    """``auth_store.load()`` returns None when nothing is configured. Refusing is
    the right call: if the code cannot be checked, the destructive action does
    not happen -- and it must not 500 the page either."""
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", tmp_path / "nothing.json")
    ok, msg = terminate.verify_stop_code(_code(), now=T0)
    assert ok is False
    assert msg == terminate.CANNOT_VERIFY


def test_a_corrupt_credentials_file_refuses_rather_than_raising(
        tmp_path, monkeypatch):
    """``auth_store.load()`` RAISES CredentialsError on a corrupt file -- by
    design, since a config-style "fall back to defaults" would mean "no
    password". That exception must not reach the page."""
    path = tmp_path / "webgui_auth.json"
    path.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    ok, msg = terminate.verify_stop_code(_code(), now=T0)
    assert ok is False
    assert msg == terminate.CANNOT_VERIFY


def test_an_unusable_secret_refuses_rather_than_waving_the_stop_through(
        tmp_path, monkeypatch):
    """An empty TOTP secret does not disable the second factor -- ``auth`` makes
    every code fail rather than pass, and the stop must inherit that."""
    c = auth_store.Credentials(password_hash=auth.hash_password(PASSWORD),
                               totp_secret="", session_secret=KEY)
    path = tmp_path / "webgui_auth.json"
    auth_store.save(c, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    ok, msg = terminate.verify_stop_code(_code(), now=T0)
    assert ok is False
    assert msg == terminate.CODE_REJECTED


def test_a_counter_that_cannot_be_recorded_refuses_the_stop(creds, monkeypatch):
    """Same call the login route makes: a code we cannot record is a code we
    cannot stop being replayed, so the otherwise-valid attempt is refused."""
    def _boom(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(auth_store, "save", _boom)
    ok, msg = terminate.verify_stop_code(_code(), now=T0)
    assert ok is False
    assert msg == terminate.CANNOT_RECORD


def test_both_outcomes_leave_a_warning_in_the_log(creds, caplog):
    """Stopping a trading stack should leave a trace, and so should a failed try
    at it -- WARNING either way, because "someone tried to stop the stack" is the
    line an operator wants to find after the fact."""
    with caplog.at_level(logging.WARNING, logger="webgui.terminate"):
        terminate.verify_stop_code("000000", now=T0)
        assert [r for r in caplog.records if r.levelno == logging.WARNING], \
            "a refused stop attempt logged nothing"

        caplog.clear()
        terminate.verify_stop_code(_code(), now=T0)
        allowed = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert allowed, "an authorized stop logged nothing"
    assert any(terminate.STOP_TARGET in r.getMessage() for r in allowed), \
        "the authorized-stop line must name what is being stopped"


def test_a_refusal_never_rewinds_the_replay_guard(creds):
    """A rejected code hands ``last_counter`` straight back, so a refusal must
    never write a LOWER counter than the one already stored."""
    assert terminate.verify_stop_code(_code(), now=T0)[0] is True
    after = auth_store.load()
    terminate.verify_stop_code("000000", now=T0)
    assert auth_store.load().last_totp_counter == after.last_totp_counter


def test_the_stop_is_reachable_only_through_the_code_check():
    """Wiring guard: the dialog must not reach ``_spawn_stop`` without going
    through ``verify_stop_code`` first. The handler cannot be driven under pytest
    (no client), so this reads the source -- the idiom test_shell.py uses."""
    src = inspect.getsource(terminate.render)
    assert "verify_stop_code(" in src
    before = src.split("verify_stop_code(", 1)[0]
    assert "_spawn_stop()" not in before, \
        "render() reaches _spawn_stop() before the code is verified"


def test_the_dialog_still_carries_its_warnings_and_a_way_out():
    """The step-up is an ADDITION. Everything the page already said about what a
    stop costs -- including that it kills the page you are looking at -- and the
    way to back out of it both survive.

    ⚠ RE-AIMED for the Phase 6 kit migration, NOT weakened. The ``"Cancel"``
    half used to read this module's source, and ``kit.confirm`` owns that
    literal now: it builds Cancel and then the confirm, in that order, for every
    dialog in the app. So the way out is asserted where it now lives -- the
    handle the page holds -- and ``test_the_dialogs_way_out_comes_before_the_stop``
    below checks the rendered order as well, which the source grep never did."""
    src = inspect.getsource(terminate.render)
    assert "This also stops THIS web app" in src
    assert "kit.confirm(" in src
    from nicegui import ui
    with ui.card():
        handle = kit.confirm("t", confirm_text="go", on_confirm=lambda: None)
    assert hasattr(handle, "cancel") and handle.cancel.text == "Cancel"


# --- the step-up's own throttle (2026-09-06) ---------------------------------
#
# Without it a stolen session cookie can grind the six-digit space unbounded.
# It is a SEPARATE counter from the login's on purpose: sharing one would mean a
# fumbled stop code locking you out of the sign-in form.

def test_grinding_rejected_codes_eventually_throttles(creds):
    for _ in range(auth.LOCKOUT_THRESHOLD):
        ok, msg = terminate.verify_stop_code("000000", now=T0)
        assert ok is False and msg is terminate.CODE_REJECTED
    ok, msg = terminate.verify_stop_code("000000", now=T0)
    assert ok is False
    assert msg is terminate.THROTTLED


def test_a_correct_code_is_refused_while_throttled(creds):
    """The throttle outranks a valid code, or it is not a throttle."""
    for _ in range(auth.LOCKOUT_THRESHOLD):
        terminate.verify_stop_code("000000", now=T0)
    ok, msg = terminate.verify_stop_code(_code(), now=T0)
    assert ok is False
    assert msg is terminate.THROTTLED


def test_the_throttle_lets_go_once_the_backoff_expires(creds):
    for _ in range(auth.LOCKOUT_THRESHOLD):
        terminate.verify_stop_code("000000", now=T0)
    later = T0 + auth.LOCKOUT_MAX_SEC + 1
    ok, _msg = terminate.verify_stop_code(_code(at=later), now=later)
    assert ok is True, "a lockout that never expires locks the owner out for good"


def test_the_stop_throttle_does_not_lock_the_sign_in_form(creds):
    """The whole reason the counters are separate."""
    for _ in range(auth.LOCKOUT_THRESHOLD + 2):
        terminate.verify_stop_code("000000", now=T0)
    assert login_page.lockout_state().locked_until("1.1.1.1", now=T0) == 0


def test_a_broken_store_is_not_counted_as_an_attempt(creds, monkeypatch):
    """A corrupt credentials file is OUR fault, not an attacker's.

    Counting it would let one bug of ours lock the owner out of the control they
    would reach for precisely when something is wrong.
    """
    real_load = auth_store.load
    broken = {"yes": True}

    def _load(*a, **k):
        if broken["yes"]:
            raise auth_store.CredentialsError("corrupt")
        return real_load(*a, **k)

    # NOT monkeypatch.undo() -- that would also revert the `creds` fixture's
    # DEFAULT_PATH patch, so the final call would read the real (empty) store and
    # the test would pass or fail for a reason unrelated to throttling.
    monkeypatch.setattr(auth_store, "load", _load)
    for _ in range(auth.LOCKOUT_THRESHOLD + 2):
        ok, msg = terminate.verify_stop_code("000000", now=T0)
        assert msg is terminate.CANNOT_VERIFY

    broken["yes"] = False
    ok, msg = terminate.verify_stop_code(_code(), now=T0)
    assert ok is True, "our own failure must not throttle the operator"


# --- the Phase 6 kit migration (2026-09-20) ----------------------------------
#
# The page is the kit's form column, and the hand-built dialog is one
# ``kit.confirm(danger=True)``: the 6-digit input, its instruction line and the
# refusal message live in ``handle.content``, and ``_go`` returns ``False`` on a
# refusal, which is what keeps the dialog OPEN.
#
# ⚠ Every test above this line drives ``verify_stop_code`` directly and touches
# no widget. None of them changed, and none of them may: they are the step-up's
# behaviour, and this migration is presentation.
from pages import ui_kit as kit
from pages.options import theme


def _dialogs():
    """Dialogs live on the client LAYOUT (NiceGUI 3.x), not in the page slot."""
    from nicegui import context, ui
    return [e for e in context.client.layout.descendants() if isinstance(e, ui.dialog)]


def _render():
    """Render the page and hand back ``(host, dialog)`` -- the dialog THIS render
    built, not whichever one another test module left on the shared auto-index
    client (the Phase 3 rule-10 lesson)."""
    from nicegui import ui
    before = {id(d) for d in _dialogs()}
    with ui.card() as host:
        terminate.render()
    new = [d for d in _dialogs() if id(d) not in before]
    assert len(new) == 1, f"render() built {len(new)} dialogs, expected 1"
    return host, new[0]


def _buttons(el):
    from nicegui import ui
    return [b for b in el.descendants() if isinstance(b, ui.button)]


def _labels(el):
    from nicegui import ui
    return [lbl for lbl in el.descendants() if isinstance(lbl, ui.label)]


def _fire(el, kind, args=None):
    from nicegui.events import GenericEventArguments
    fired = [li.handler(GenericEventArguments(sender=el, client=el.client, args=args))
             for li in list(el._event_listeners.values())
             if li.type.split(".")[0] == kind and li.handler is not None]
    assert fired, f"no {kind} listener to fire"


def _confirm(dlg):
    """Run a confirm dialog's action -- the recipe from test_appearance.py. Its
    ``run`` is a COROUTINE function and nicegui would only DEFER it here (no
    running app loop), so drive the dialog's own keydown.enter listener, which
    ``kit.confirm`` registers as ``run`` itself, inside a slot context (a slot
    stack is per asyncio TASK)."""
    import asyncio
    (run_,) = [li.handler for li in dlg._event_listeners.values()
               if li.type == "keydown.enter"]

    async def _drive(slot):
        with slot:
            await run_(None)

    asyncio.run(_drive(dlg.parent_slot))


def test_the_frame_is_the_kit_and_carries_no_surface_of_its_own():
    src = inspect.getsource(terminate.render)
    assert 'kit.page(width="form")' in src
    assert 'kit.header("Stop All Services")' in src
    for token in ("text-h5", "max-w-2xl", "opacity-", "ui.dialog(", "ui.notify(",
                  "ui.button("):
        assert token not in src, f"{token} is the page building its own chrome"


def test_the_page_button_is_a_danger_OUTLINE_and_the_confirm_is_the_solid_one():
    """The standard: solid red appears ONLY inside a confirm dialog. The page
    button was solid red and is now the outline -- which is also what the rail
    already draws for this route (``_nav_danger_link``)."""
    host, dlg = _render()
    (page_btn,) = [b for b in _buttons(host) if b.text == "Stop all services"]
    assert set(theme.BTN_DANGER.split()) <= set(page_btn.classes)
    assert set(theme.BTN_DANGER_SOLID.split()) - set(page_btn.classes), \
        "the page button is still the solid red one"
    (confirm_btn,) = [b for b in _buttons(dlg) if b.text == "Stop everything"]
    assert set(theme.BTN_DANGER_SOLID.split()) <= set(confirm_btn.classes)


def test_the_dialogs_way_out_comes_before_the_stop():
    """Cancel then confirm, the app's one order. The old dialog already had it;
    this pins it now that the kit owns the row."""
    _host, dlg = _render()
    texts = [b.text for b in _buttons(dlg)]
    assert texts == ["Cancel", "Stop everything"], texts


def test_the_dialog_wears_the_kits_confirm_card():
    from nicegui import ui
    _host, dlg = _render()
    (card,) = [e for e in dlg.descendants() if isinstance(e, ui.card)]
    assert set(kit.CONFIRM_CARD.split()) <= set(card.classes)


def test_the_code_field_and_its_message_live_in_the_dialog():
    """``handle.content`` is the kit's own column between the body and the
    buttons -- the documented place for a confirm's inputs.

    ⚠ A must-not-change guard: green on both sides of the migration. The four
    props were already right, and each is load-bearing -- ``one-time-code`` is
    what makes a phone offer the code from its own notification, and
    ``inputmode=numeric`` gets the digit pad. Moving the field into a kit
    dialog must not drop any of them."""
    from nicegui import ui
    _host, dlg = _render()
    (code,) = [e for e in dlg.descendants() if isinstance(e, ui.input)]
    for prop in ("autofocus", "inputmode", "maxlength", "autocomplete"):
        assert prop in code._props, prop
    assert str(code._props["maxlength"]) == "6"
    assert code._props["autocomplete"] == "one-time-code"
    texts = [lbl.text for lbl in _labels(dlg)]
    assert any("6-digit code" in (t or "") for t in texts)


def test_enter_confirms_from_inside_the_dialog():
    """``CONFIRM_ENTER_JS`` excludes only TEXTAREA, so Enter in the 6-digit
    field submits. It did nothing at all on the hand-built dialog."""
    _host, dlg = _render()
    assert [li for li in dlg._event_listeners.values()
            if li.type == "keydown.enter"], "Enter does not reach the confirm"


def test_a_refused_code_keeps_the_dialog_open_and_stops_nothing(monkeypatch):
    """The load-bearing half of the migration. Closing on a refusal would read
    as "done", which is the one thing that must never be ambiguous here -- and
    ``kit.confirm`` keeps the dialog open exactly when ``on_confirm`` returns
    ``False``."""
    from nicegui import ui
    spawned = []
    monkeypatch.setattr(terminate, "_spawn_stop", lambda: spawned.append(1))
    monkeypatch.setattr(terminate, "verify_stop_code",
                        lambda code, **kw: (False, terminate.CODE_REJECTED))
    _host, dlg = _render()
    (code,) = [e for e in dlg.descendants() if isinstance(e, ui.input)]
    code.value = "000000"
    dlg.open()
    _confirm(dlg)

    assert not spawned, "a refused code reached the stop"
    assert dlg.value is True, "the dialog closed on a refusal"
    (problem,) = [lbl for lbl in _labels(dlg)
                  if lbl.text == terminate.CODE_REJECTED]
    assert problem.visible
    assert code.value == "", "the refused code was left in the field"


def test_a_refused_code_can_be_retried_without_reloading_the_page(monkeypatch):
    """``kit.confirm``'s re-entrancy guard resets ``st["done"]`` on each open, so
    a second attempt inside one opening is not swallowed. The hand-built dialog
    had no guard at all, so this is new behaviour and had to be checked rather
    than assumed."""
    from nicegui import ui
    tries = []
    monkeypatch.setattr(terminate, "_spawn_stop", lambda: None)

    def _verify(code, **_kw):
        tries.append(code)
        return False, terminate.CODE_REJECTED

    monkeypatch.setattr(terminate, "verify_stop_code", _verify)
    _host, dlg = _render()
    (code,) = [e for e in dlg.descendants() if isinstance(e, ui.input)]
    dlg.open()
    code.value = "111111"
    _confirm(dlg)
    code.value = "222222"
    _confirm(dlg)
    assert tries == ["111111", "222222"], tries


def test_reopening_clears_the_last_refusal(monkeypatch):
    """A stale red sentence over a fresh empty field reads as a refusal of the
    code you have not typed yet."""
    from nicegui import ui
    monkeypatch.setattr(terminate, "_spawn_stop", lambda: None)
    monkeypatch.setattr(terminate, "verify_stop_code",
                        lambda code, **kw: (False, terminate.CODE_REJECTED))
    host, dlg = _render()
    (code,) = [e for e in dlg.descendants() if isinstance(e, ui.input)]
    code.value = "000000"
    dlg.open()
    _confirm(dlg)
    (problem,) = [lbl for lbl in _labels(dlg)
                  if lbl.text == terminate.CODE_REJECTED]
    assert problem.visible
    _fire([b for b in _buttons(host) if b.text == "Stop all services"][0], "click")
    assert not problem.visible, "the previous refusal was still on screen"


def test_a_valid_code_stops_the_stack_and_says_the_page_is_about_to_die(monkeypatch):
    from nicegui import ui
    spawned, said = [], []
    monkeypatch.setattr(terminate, "_spawn_stop", lambda: spawned.append(1))
    monkeypatch.setattr(terminate, "verify_stop_code", lambda code, **kw: (True, ""))
    monkeypatch.setattr(terminate.kit, "toast",
                        lambda kind, text: said.append((kind, text)))
    _host, dlg = _render()
    (code,) = [e for e in dlg.descendants() if isinstance(e, ui.input)]
    code.value = "123456"
    dlg.open()
    _confirm(dlg)

    assert spawned == [1]
    assert dlg.value is False, "the dialog stayed open after an authorized stop"
    assert said and said[-1][0] == "warn"
    assert "stop responding" in said[-1][1]


def test_the_page_builds_no_control_of_its_own():
    """The guard's ``terminate.py`` entry ({"button": 3, "dialog": 1,
    "notify": 1}) is deleted in the same commit; this is its page-level half.
    No written exception was needed: the two things that looked missing from
    ``kit.confirm`` -- an input field and a second factor that can refuse --
    are both already in it (``handle.content`` and a ``False`` return)."""
    src = inspect.getsource(terminate)
    for banned in ("ui.button(", "ui.dialog(", "ui.notify("):
        assert banned not in src, banned
