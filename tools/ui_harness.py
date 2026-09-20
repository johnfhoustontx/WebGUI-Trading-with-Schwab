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
"""
import argparse
import importlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


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

    ui.run(port=args.port, dark=True, reload=False, show=False, title="ui harness")


if __name__ == "__main__":
    main()
