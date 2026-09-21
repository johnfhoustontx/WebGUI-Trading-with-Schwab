"""Render ONE page module on a local NiceGUI server, with the app's page CSS.

For checking a page in a real browser without the login or a dev stack (there
is no dev environment - CLAUDE.md "Environments"). A fake Bus stands in for
Redis; ``--seed file.json`` fills it with ``{view: payload}`` first. The page
is drawn in the same ``ns-app`` content column, with the same CSS, that both
entrypoints give it - but with no nav rail or header.

    python tools/ui_harness.py settings --port 9591
    python tools/ui_harness.py options.paper --seed seed.json
    python tools/ui_harness.py symbol --seed seed.json --kwargs '{"symbol": "SPY"}'

Not a test and never for prod: with a fake Bus no command is executed.

It serves LOOPBACK only, and refuses a port something already answers on - a
taken port otherwise fails to bind silently and you read the other server's
page. Stop a harness when you are done with it; they do not stop themselves.
"""
import argparse
import importlib
import json
import pathlib
import socket
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Loopback, never 0.0.0.0 (CLAUDE.md). ``ui.run`` with no host resolves to EVERY
# interface outside native mode - which is what each leftover harness bound.
HOST = "127.0.0.1"

# How long to wait for an answer. A real loopback listener accepts in ~1 ms, so
# this is a ~300x margin. It is bounded at all because on Windows a REFUSED
# connect is not immediate (measured 2.05 s unbounded), and refused is the
# common case - the port is free. On Linux a refusal returns at once.
PROBE_TIMEOUT_SEC = 0.3


def port_taken(port, host=HOST, timeout=PROBE_TIMEOUT_SEC):
    """Whether something already ANSWERS on ``host:port``.

    A connect, not a bind - and that difference is the whole point. Every
    harness before this bound ``0.0.0.0``, and on Windows binding ``127.0.0.1``
    on a port a wildcard listener holds SUCCEEDS (measured 2026-09-20). So a
    bind-probe reports a leftover's port as FREE, ``ui.run``'s own bind then
    fails quietly, the old server keeps answering, and you read its page.
    Asking whether anything answers cannot be fooled that way.

    Only a completed connect means taken. A refusal, a timeout or any other
    socket error means nothing is serving there.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def preflight_port(port):
    """Refuse, loudly, a port something already answers on.

    The alternative is silent: NiceGUI's bind fails, the OLD server keeps
    serving, and the page you open is that one. Measured 2026-09-20 - four
    leftover harnesses held 9593-9596 for two hours, serving code from before
    four later commits, and an agent measured one of them as its own page.
    """
    if port_taken(port):
        raise SystemExit(
            f"ui_harness: port {port} is already answering on {HOST}. Something "
            f"is serving there - often a harness left running earlier - and "
            f"starting here would fail to bind SILENTLY, so you would be reading "
            f"that server's page, not this one. Stop it, or pass another --port.")


def run_kwargs(args):
    """``ui.run``'s arguments. Split out so the bind address is testable
    without starting a server."""
    return {"host": HOST, "port": args.port, "dark": True, "reload": False,
            "show": False, "title": "ui harness"}


def parse_args(argv=None):
    """The command line. Split out so the argument handling is testable without
    starting a server."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("page", help="module under webgui/pages, e.g. settings or options.paper")
    ap.add_argument("--port", type=int, default=9591)
    ap.add_argument("--seed", help="JSON file of {view: payload} for the fake bus")
    ap.add_argument("--kwargs", default=None,
                    help="JSON object passed to render(). Some pages take "
                         "their subject as an argument rather than reading it "
                         "from the cache: symbol takes symbol, momentum level.")
    args = ap.parse_args(argv)
    args.render_kwargs = {}
    if args.kwargs:
        try:
            parsed = json.loads(args.kwargs)
        except ValueError as exc:
            ap.error(f"--kwargs is not JSON: {exc}")
        # render(**x) needs a mapping; anything else fails much later, inside a
        # page build, with a traceback that says nothing about the command line.
        if not isinstance(parsed, dict):
            ap.error("--kwargs must be a JSON object, e.g. '{\"symbol\": \"SPY\"}'")
        args.render_kwargs = parsed
    return args


def main(argv=None):
    args = parse_args(argv)
    preflight_port(args.port)
    # The benign "parent slot of the element has been deleted" race - a timer
    # meeting a disconnect - that main.py filters (CLAUDE.md, ``ui_guard``). A
    # separate entrypoint must install it itself, or the harness prints a
    # traceback the running app deliberately swallows.
    from pages.ui_guard import install_deleted_slot_log_filter
    install_deleted_slot_log_filter()

    import bus_client
    from shared.bus import Bus
    bus_client._bus = Bus(fake=True)
    if args.seed:
        seed = json.loads(pathlib.Path(args.seed).read_text(encoding="utf-8"))
        for view, payload in seed.items():
            bus_client.bus().cache_set(f"cache:{view}", payload)

    from nicegui import ui

    import shell
    from pages.options import theme

    @ui.page("/")
    def _index():
        # The page-level CSS both entrypoints inject, in their order: the app
        # surface and fields BEFORE the subtab row, whose own rules must win.
        for css in (shell.TABLE_CSS, theme.SURFACE_CSS, theme.APP_FIELD_CSS,
                    shell.SUBTAB_CSS, shell.PANEL_SCROLL_CSS, theme.TYPOGRAPHY_CSS):
            if css:
                ui.add_css(css)
        if theme.FONT_HEAD_HTML:
            ui.add_head_html(theme.FONT_HEAD_HTML)
        ui.colors(**theme.QUASAR_COLORS)
        with ui.column().classes("ns-app w-full p-4 gap-3 pb-10"):
            importlib.import_module(f"pages.{args.page}").render(**args.render_kwargs)

    ui.run(**run_kwargs(args))


if __name__ == "__main__":
    main()
