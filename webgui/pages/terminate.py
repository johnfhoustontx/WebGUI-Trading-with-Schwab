"""Stop All Services page (``/terminate``) — stop the whole local stack from the web GUI.

A deliberately guarded action: a single red button behind a confirm dialog that
spawns ``stop_all.bat`` (proxy + the six domain services + this web app; Memurai
is left running). Because it kills the web app too, the page goes unresponsive
right after you confirm — by design.

Honors the 3-tier rule: imports only ``nicegui`` + stdlib + ``repo_paths`` (host
process control, not an app engine). The ``stop_command`` builder is pure and
unit-tested; ``render`` is thin wiring.
"""
import subprocess

from nicegui import ui

from pages.options.theme import BTN_DANGER_SOLID
from repo_paths import REPO_ROOT

STOP_BAT = REPO_ROOT / "stop_all.bat"


def stop_command():
    """argv that spawns ``stop_all.bat`` fully detached from this app.

    Uses ``cmd /c start`` so the batch runs in its own independent process and
    console — NOT inside this web app's process tree. That matters because the
    batch taskkills this very app; if it were a child it could kill itself
    mid-run before terminating the other services.
    """
    return ["cmd", "/c", "start", "Schwab stop_all", str(STOP_BAT)]


def _spawn_stop():
    subprocess.Popen(stop_command(), cwd=str(REPO_ROOT))


def render():
    ui.label("Stop All Services").classes("text-h5")

    with ui.card().classes("w-full max-w-2xl"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("warning").classes("text-orange text-2xl")
            ui.label("Stop all local services").classes("text-subtitle1 font-bold")
        ui.label(
            "Stops the schwab-proxy, all six domain services, and this web app "
            "by killing whatever is listening on their ports. Memurai (the Redis "
            "backbone) keeps running.").classes("opacity-80")
        ui.label(
            "⚠ This also stops THIS web app — the page will stop responding right "
            "after you confirm. That's expected. Re-launch with start_all_wt.bat "
            "(or start_all.bat).").classes("text-orange text-sm")

        if not STOP_BAT.is_file():
            ui.label(f"stop_all.bat not found at {STOP_BAT}").classes("text-red")
            return

        with ui.dialog() as dlg, ui.card():
            ui.label("Stop all services now?").classes("text-subtitle1 font-bold")
            ui.label("The proxy, all services, and this web app will be "
                     "terminated. Memurai stays up.").classes("opacity-80")
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
