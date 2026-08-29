"""Stop All Services page (``/terminate``) — stop the whole local stack from the web GUI.

A deliberately guarded action: a single red button behind a confirm dialog that
stops this environment's systemd target (the six domain services + this web app,
and the schwab-proxy only in the environment that owns it; Redis is left
running). Because it stops the web app too, the page goes unresponsive right
after you confirm — by design.

The proxy caveat is not cosmetic, and systemd expresses it better than the old
batch did: dev borrows prod's proxy on :8100, so a dev checkout simply has no
proxy UNIT for its target to pull in. Ownership is encoded in which units exist
rather than in a kill-list filter, but the copy still has to say so, or a dev
operator either avoids a button they are entitled to press or mistrusts the
result when the proxy survives.

Honors the 3-tier rule: imports only ``nicegui`` + stdlib + ``repo_paths`` (host
process control, not an app engine). The ``stop_command`` builder is pure and
unit-tested; ``render`` is thin wiring.
"""
import subprocess

from nicegui import ui

from pages.options.theme import BTN_DANGER_SOLID
from repo_paths import ENV_NAME, REPO_ROOT

STOP_TARGET = f"trading-{ENV_NAME}.target"


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
            with ui.row().classes("justify-end gap-2 w-full"):
                ui.button("Cancel", on_click=dlg.close).props("flat")

                def _go():
                    dlg.close()
                    _spawn_stop()
                    ui.notify("Terminating all services… this page will stop "
                              "responding shortly.", type="warning", timeout=10000)

                ui.button("Stop everything", color=None, on_click=_go).props("no-caps").classes(BTN_DANGER_SOLID)

        ui.button("Stop all services", icon="power_settings_new", color=None,
                  on_click=dlg.open).props("no-caps").classes(BTN_DANGER_SOLID)
