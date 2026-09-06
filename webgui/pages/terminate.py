"""Stop All Services page (``/terminate``) — stop the whole local stack from the web GUI.

A deliberately guarded action: a single red button behind a confirm dialog that
stops this environment's systemd target (the six domain services + this web app,
and the schwab-proxy only in the environment that owns it; Redis is left
running). Because it stops the web app too, the page goes unresponsive right
after you confirm — by design.

Since 2026-09-06 the confirm dialog also demands a fresh TOTP code. The app is
served on the public internet, so this control sits behind exactly one session
cookie — and a stolen cookie or an unlocked phone would cost the rest of the
trading day: the GEX slots for the session, a live stream dropped mid-broadcast,
the driver stood down. The step-up is deliberately ONLY here. Adding it to the
driver's arm switch or to rescue-apply would train the code out of meaning
anything; one destructive control, one prompt.

The proxy caveat is not cosmetic, and systemd expresses it better than the old
batch did: dev borrows prod's proxy on :8100, so a dev checkout simply has no
proxy UNIT for its target to pull in. Ownership is encoded in which units exist
rather than in a kill-list filter, but the copy still has to say so, or a dev
operator either avoids a button they are entitled to press or mistrusts the
result when the proxy survives.

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
from pages.options.theme import BTN_DANGER_SOLID, MUTED, TXT_NEG
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
    ui.label("Stop All Services").classes("text-h5")

    with ui.card().classes("w-full max-w-2xl"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("warning").classes("text-orange text-2xl")
            ui.label("Stop all local services").classes("text-subtitle1 font-bold")
        ui.label(
            "Stops all six domain services and this web app by killing whatever "
            "is listening on their ports. Redis (the bus backbone) keeps "
            "running — it is a system service, not part of this target.").classes(
                "opacity-80")
        ui.label(
            "The schwab-proxy is stopped only in the environment that owns it — "
            "a dev checkout borrows prod's and leaves it up.").classes(
                "opacity-80")
        ui.label(
            "⚠ This also stops THIS web app — the page will stop responding right "
            "after you confirm. That's expected. Re-launch with "
            f"`systemctl --user start {STOP_TARGET}`.").classes("text-orange text-sm")

        with ui.dialog() as dlg, ui.card():
            ui.label("Stop all services now?").classes("text-subtitle1 font-bold")
            ui.label("All six domain services and this web app will be "
                     "terminated. The schwab-proxy stops only in the "
                     "environment that owns it; Redis stays up.").classes(
                         "opacity-80")
            ui.label("Confirm with the 6-digit code from your authenticator "
                     "app.").classes(f"text-sm {MUTED}")
            # autocomplete=one-time-code is what makes a phone offer the code
            # from its own notification; inputmode=numeric gets the digit pad.
            code_input = (ui.input("Authenticator code")
                          .props("autofocus inputmode=numeric maxlength=6 "
                                 "autocomplete=one-time-code")
                          .classes("w-48"))
            problem = ui.label("").classes(f"text-sm {TXT_NEG}")
            problem.set_visibility(False)

            with ui.row().classes("justify-end gap-2 w-full"):
                ui.button("Cancel", on_click=dlg.close).props("flat")

                @guard
                def _go():
                    ok, message = verify_stop_code(code_input.value)
                    if not ok:
                        # The dialog STAYS OPEN: closing it on a refusal would
                        # read as "done", which is the one thing that must never
                        # be ambiguous about a control like this.
                        problem.set_text(message)
                        problem.set_visibility(True)
                        code_input.value = ""
                        return
                    dlg.close()
                    _spawn_stop()
                    ui.notify("Terminating all services… this page will stop "
                              "responding shortly.", type="warning", timeout=10000)

                ui.button("Stop everything", color=None, on_click=_go).props("no-caps").classes(BTN_DANGER_SOLID)

        ui.button("Stop all services", icon="power_settings_new", color=None,
                  on_click=dlg.open).props("no-caps").classes(BTN_DANGER_SOLID)
