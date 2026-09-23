"""Stop All Services page (``/terminate``) — stop the whole local stack from the web GUI.

A deliberately guarded action: a single red button behind a confirm dialog that
stops this environment's systemd target (the five domain services, this web app
and the PUBLIC live screens beside it, and the schwab-proxy only in the
environment that owns it; Redis is left running). Because it stops the web app
too, the page goes unresponsive right after you confirm — by design.

⚠ The live screens are a SECOND web app on this target, so confirming here also
takes the public site dark. Worth saying on the page: someone stopping the
trading stack for five minutes is not necessarily expecting to unpublish
anything.

Since 2026-09-06 the confirm dialog also demands a fresh TOTP code. The app is
served on the public internet, so this control sits behind exactly one session
cookie — and a stolen cookie or an unlocked phone would cost the rest of the
trading day: the GEX slots for the session and a live stream dropped
mid-broadcast. The step-up is deliberately ONLY here. Adding it to rescue-apply
would train the code out of meaning anything; one destructive control, one
prompt.

The proxy caveat is not cosmetic, and systemd expresses it better than the old
batch did: dev borrows prod's proxy on :8100, so a dev checkout simply has no
proxy UNIT for its target to pull in. Ownership is encoded in which units exist
rather than in a kill-list filter, but the copy still has to say so, or a dev
operator either avoids a button they are entitled to press or mistrusts the
result when the proxy survives.

Since the Phase 6 kit migration the confirm is one ``kit.confirm(danger=True)``.
The 6-digit input, its instruction line and the refusal message live in the
kit's own ``handle.content`` column, and ``_go`` returns ``False`` on a refusal
— which is exactly how ``kit.confirm`` is told to keep the dialog open.

Honors the 3-tier rule: imports only ``nicegui`` + stdlib + ``repo_paths`` (host
process control, not an app engine) + the webgui's own ``auth``/``auth_store``.
``stop_command`` and ``verify_stop_code`` are the testable surface; ``render`` is
thin wiring.
"""
import dataclasses
import logging
import subprocess
import time as _time

from nicegui import ui

import auth
import auth_store
from pages import ui_kit as kit
from pages.options import theme
from pages.ui_guard import guard
from repo_paths import ENV_NAME, REPO_ROOT

log = logging.getLogger("webgui.terminate")

# The step-up's own backoff, DELIBERATELY SEPARATE from the login's.
#
# Without one, a stolen session cookie can grind the six-digit space unbounded --
# ~3 valid codes per window out of 10**6, which is hours of scripted attempts, not
# a wall. With one it is 5 tries and then a wait.
#
# Separate, because sharing `login_page`'s counter would mean fumbling a stop code
# locks you out of the LOGIN FORM -- coupling an everyday screen to a rare one.
#
# And a single GLOBAL key rather than a per-client one, which would be wrong on the
# login form and is right here: `verify_stop_code` takes no request context (that is
# what keeps it testable), the action it guards is global, and the worst an attacker
# achieves by grinding it is locking the owner out of the STOP BUTTON -- who still
# has SSH. On the login form that same reasoning fails, which is why `main._client_ip`
# exists.
_LOCKOUT = auth.LockoutState()
_STOP_CLIENT = "stop-all-services"


def reset_stop_lockout():
    """Test hook: forget every recorded attempt."""
    global _LOCKOUT
    _LOCKOUT = auth.LockoutState()


STOP_TARGET = f"trading-{ENV_NAME}.target"

# The three refusals, as sentences the operator can act on.
#
# ⚠ These are deliberately NOT ``login_page.GENERIC_FAILURE``. That sentence is
# vague on purpose because the login form answers strangers; whoever reaches this
# dialog has already passed the password AND a code, so withholding which gate
# they hit protects nobody and just leaves them retyping a code that was never
# going to work.
CODE_REQUIRED = "Enter the 6-digit code from your authenticator app to confirm."
CODE_REJECTED = ("That code was not accepted — it is wrong, expired, or has "
                 "already been used. Nothing has been stopped. Wait for the next "
                 "code and try again.")
THROTTLED = ("Too many rejected codes. Wait a minute and try again — or, if this "
             "is urgent, stop the stack from a terminal with "
             f"`systemctl --user stop {STOP_TARGET}`.")
CANNOT_VERIFY = ("The stored sign-in credentials could not be read, so the code "
                 "cannot be checked. Refusing to stop the stack.")
CANNOT_RECORD = ("The code was valid but could not be recorded as used, so it is "
                 "refused rather than left replayable. Nothing has been stopped.")


def stop_command():
    """argv that stops this environment's whole systemd target.

    ``--no-block`` registers the stop job with the systemd MANAGER and returns
    immediately. That is what makes this safe despite the target including THIS
    web app: the job is owned by systemd, so this process being stopped partway
    through cannot orphan the shutdown. The batch version needed a detached
    console for the same reason and got a weaker guarantee for it — an
    independent OS process was the only thing keeping the script alive.
    """
    return ["systemctl", "--user", "--no-block", "stop", STOP_TARGET]


def verify_stop_code(code, *, now=None):
    """``(ok, message)`` — may this stop proceed? Never raises.

    Refusing is the safe answer to every failure here, including our own: if the
    code cannot be CHECKED (nothing configured, corrupt file) or cannot be
    RECORDED as spent, the destructive action does not happen. A page that 500s
    would be the same refusal with a traceback instead of a sentence.

    ⚠ The accepted counter is PERSISTED, exactly as ``login_page.attempt``
    persists it, and against the SAME file. That is what makes a code spent here
    unusable at the login form, and one spent there unusable here — skipping the
    write leaves this function looking correct while the code stays live for the
    rest of its window (90 s, with drift).
    """
    now_ts = _time.time() if now is None else now
    locked = _LOCKOUT.locked_until(_STOP_CLIENT, now=now_ts)
    if locked:
        log.warning("Stop All Services REFUSED: throttled for another %.0f s.",
                    locked - now_ts)
        return False, THROTTLED

    try:
        creds = auth_store.load()
    except auth_store.CredentialsError as exc:
        log.warning("Stop All Services REFUSED: %s", exc)
        return False, CANNOT_VERIFY
    if creds is None:
        log.warning("Stop All Services REFUSED: no credentials configured at "
                    "%s, so the code cannot be checked.", auth_store.DEFAULT_PATH)
        return False, CANNOT_VERIFY

    # Blank is worth its own sentence: "wrong code" would be a lie, and the
    # answer ("type one") is different. The credentials read comes first so a
    # broken store is reported as a broken store whatever was typed.
    if not (code or "").strip():
        log.warning("Stop All Services REFUSED: no code was entered.")
        return False, CODE_REQUIRED

    ok, counter = auth.verify_totp(creds.totp_secret, code, now=now,
                                   last_counter=creds.last_totp_counter)
    if not ok:
        log.warning("Stop All Services REFUSED: the code was wrong, expired or "
                    "already used.")
        _LOCKOUT.record_failure(_STOP_CLIENT, now=now_ts)
        return False, CODE_REJECTED

    try:
        auth_store.save(dataclasses.replace(creds, last_totp_counter=counter))
    except OSError as exc:
        log.warning("Stop All Services REFUSED: the code verified but the "
                    "counter could not be written to %s, so it would stay "
                    "replayable: %s", auth_store.DEFAULT_PATH, exc)
        return False, CANNOT_RECORD

    log.warning("Stop All Services AUTHORIZED by a valid authenticator code — "
                "stopping %s", STOP_TARGET)
    _LOCKOUT.record_success(_STOP_CLIENT)
    return True, ""


def _spawn_stop():
    subprocess.Popen(stop_command(), cwd=str(REPO_ROOT))


def render():
    """The page: what a stop costs, then one danger button behind one
    ``kit.confirm`` carrying the TOTP step-up.

    ⚠ The page button is the danger OUTLINE, not the solid red it used to be.
    Solid red appears only INSIDE a confirm dialog (the app's standard), and the
    rail already draws this route that way (``main._nav_danger_link``).
    """
    with kit.page(width="form"):
        kit.header("Stop All Services")
        with ui.column().classes(f"{theme.CARD} w-full gap-2"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("warning").classes(f"text-2xl {theme.TXT_WARN}")
                ui.label("Stop all local services").classes(
                    f"text-subtitle1 font-semibold {theme.LABEL}")
            ui.label(
                "Stops all six domain services, this web app and the public live "
                "screens beside it. Redis (the bus backbone) keeps "
                "running — it is a system service, not part of this target."
            ).classes(f"text-sm {theme.MUTED}")
            ui.label(
                "The schwab-proxy is stopped only in the environment that owns it — "
                "a dev checkout borrows prod's and leaves it up."
            ).classes(f"text-sm {theme.MUTED}")
            ui.label(
                "⚠ This also stops THIS web app — the page will stop responding right "
                "after you confirm. That's expected. Re-launch with "
                f"`systemctl --user start {STOP_TARGET}`."
            ).classes(f"text-sm {theme.TXT_WARN}")
            kit.button("Stop all services", kind="danger",
                       icon="power_settings_new",
                       on_click=lambda: _open()).classes("self-start mt-1")

    def _go():
        """The confirm's action. ``False`` keeps the dialog OPEN.

        Closing it on a refusal would read as "done", which is the one thing
        that must never be ambiguous about a control like this — and returning
        ``False`` is precisely how ``kit.confirm`` is asked for that. The kit
        closes the dialog itself on anything else, so there is no ``close()``
        here; a page that closed it as well would race the kit's own.
        """
        ok, message = verify_stop_code(code_input.value)
        if not ok:
            problem.set_text(message)
            problem.set_visibility(True)
            code_input.value = ""
            return False
        _spawn_stop()
        kit.toast("warn", "Terminating all services… this page will stop "
                          "responding shortly.")
        return True

    # Built ONCE at render()'s own level, and deliberately NOT ephemeral: a
    # refused code has to leave this dialog standing and reopenable.
    # ⚠ AFTER ``_go``: test_terminate.py splits render()'s source on
    # ``verify_stop_code(`` and asserts nothing reaches ``_spawn_stop`` before
    # it, which is the wiring guard that the code check cannot be skipped.
    #
    # ⚠ The body is the old dialog's, VERBATIM - two sentences where the kit's
    # confirms are usually one, and it stays that way. It carries the only
    # ownership statement the operator sees at the MOMENT of confirming, and
    # test_copy_states_that_stopping_the_proxy_is_ownership_conditional counts
    # that promise across the module: it finds TWO sites, not three, because the
    # docstring's own copy is wrapped across a newline and the literal has never
    # matched it. This is one of the two. That test reads the SOURCE, so the
    # phrase has to sit on ONE source line - a wrap between "owns" and "it"
    # breaks it exactly as the docstring's does.
    stop_dlg = kit.confirm(
        "Stop all services now?",
        "All six domain services, this web app and the public live screens will "
        "be terminated. The schwab-proxy stops "
        "only in the environment that owns it; Redis stays up.",
        confirm_text="Stop everything", danger=True, on_confirm=_go)
    with stop_dlg.content:
        ui.label("Confirm with the 6-digit code from your authenticator "
                 "app.").classes(f"text-sm {theme.MUTED}")
        # autocomplete=one-time-code is what makes a phone offer the code
        # from its own notification; inputmode=numeric gets the digit pad.
        code_input = (ui.input("Authenticator code")
                      .props("autofocus inputmode=numeric maxlength=6 "
                             "autocomplete=one-time-code")
                      .classes("w-48"))
        problem = ui.label("").classes(f"text-sm {theme.TXT_NEG}")
        problem.set_visibility(False)

    @guard
    def _open():
        """Open it CLEAN. The kit resets its own re-entrancy latch on each open,
        but the refusal message and the emptied field are this page's, and a
        stale red sentence over a blank box reads as a refusal of the code you
        have not typed yet."""
        problem.set_visibility(False)
        code_input.value = ""
        stop_dlg.open()
