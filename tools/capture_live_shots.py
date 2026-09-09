"""Screenshot every published live screen, for the public grid on the site.

``deploy/site/live.html`` is a static menu of the fourteen screens
``webgui/live_screens.py`` publishes. Its tiles are THUMBNAILS, not live embeds:
one visitor opening the grid would otherwise spin up fourteen NiceGUI sessions
against the live process, and a page that is only ever navigation would cost
more than the screens it points at. So a timer takes a picture instead.

Headless Chrome renders each route offscreen -- no Xvfb, unlike
``tools/stream_wall.sh``, because ``--headless --screenshot`` needs no display
-- and the result is encoded to ``deploy/site/live/<slug>.webp``, inside the
directory Caddy serves.

Three decisions are worth knowing before editing:

* **The capture list IS ``live_screens.SCREENS``.** Nothing here restates a
  route, a slug or a title. A screen added to the site therefore cannot ship
  without a thumbnail, and a thumbnail cannot outlive its screen.

* **Loopback, never the public host.** Going through ``live.neuralstrike.co``
  would make the thumbnails depend on DNS, TLS and Caddy -- three things that
  say nothing about whether the screens render. The live process has no auth
  middleware, so loopback reaches it plainly.

* **Standing down is exit 0; a missing browser is exit 1.** Outside
  ``[windows.live_capture]`` there is nothing to photograph and nothing wrong,
  which is the reasoning ``stream_wall.sh`` records. A host with no browser is
  the opposite: every screen fails identically, forever, and the grid goes stale
  behind a green timer. The unit is a ``Type=oneshot`` with no ``Restart=``, so
  that exit surfaces in ``systemctl --user --failed`` rather than storming.

Run it by hand with ``--force`` to capture outside the window.
"""
import argparse
import datetime
import importlib.util
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402
from shared.market_calendar import CT, in_window  # noqa: E402

log = logging.getLogger("capture_live_shots")

# Where the grid looks. Inside SITE_ROOT because Caddy serves that tree
# literally; gitignored because it is generated state, and a tracked file the
# timer rewrites every 15 minutes would dirty prod's tree -- which
# tools/promote.sh refuses.
OUT_DIR = pathlib.Path(repo_paths.SITE_ROOT) / "live"

# The rendered viewport, and the box the grid reserves for it. Held as two pairs
# with the same aspect rather than one: the shot wants to be big enough that the
# app's small text survives, the tile wants to be small enough to be a menu. A
# test pins the ratio, because a mismatch letterboxes every tile the moment the
# first capture lands and looks like a CSS bug.
VIEWPORT_WIDTH, VIEWPORT_HEIGHT = 1600, 1000
TILE_WIDTH, TILE_HEIGHT = 640, 400

# How long Chrome is told to let the page settle before it shoots. These screens
# paint from Redis on a watcher tick -- a screenshot taken at load is a grid of
# skeletons, which reads as a rendering bug and is a timing one.
#
# ⚠ VIRTUAL TIME IS NOT WALL TIME, and that is this flag's known limit. Chrome
# advances its own clock as fast as the page allows, pausing for pending
# NETWORK fetches -- so it reliably waits for the document and its assets, and
# does NOT reliably wait for a value that arrives later over the page's
# WebSocket. If captures come back showing skeletons, raising this number is
# the cheap thing to try and is not guaranteed to fix it; the real fix is a
# CDP-driven capture that waits on a selector, which is a puppeteer/playwright
# dependency this box does not have and does not need for anything else.
SETTLE_MS = 8000

# Wall-clock ceiling per screen, in case Chrome hangs instead of exiting. Fourteen
# of these bound the unit's TimeoutStartSec in deploy/systemd/generate_units.py.
SCREEN_TIMEOUT_SEC = 45

# WebP, not the PNG Chrome writes: this is a public page loading fourteen images
# at once, and the encode is the cheapest place to pay for that. 80 is visually
# lossless on flat UI colour and roughly a fifth of the PNG.
WEBP_QUALITY = 80


def _live_screens():
    """``webgui/live_screens.py``, loaded BY PATH rather than by import.

    ``webgui/`` holds top-level modules named ``main``, ``proxy``, ``auth`` and
    ``wall``; putting that directory on ``sys.path`` would let any of them
    shadow a same-named module elsewhere in this process. One file loaded by its
    path reaches nothing else -- and it is still a real import, so a broken
    table fails here rather than producing an empty capture list.
    """
    path = pathlib.Path(repo_paths.WEBGUI) / "live_screens.py"
    spec = importlib.util.spec_from_file_location("_live_screens", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def targets():
    """``[(url, output path), ...]`` for every published screen. PURE.

    The whole seam between this script and the site is here, which is why it is
    a function with no side effects: everything a test needs to know about what
    gets captured, and from where, is in its return value.
    """
    return [(f"{repo_paths.NICEGUI_LIVE_URL}{s.route}", OUT_DIR / f"{s.slug}.webp")
            for s in _live_screens().SCREENS]


def _in_window():
    """Is now inside ``[windows.live_capture]`` on a trading day?

    ONE call answers both: ``in_window`` gates on ``is_trading_day`` internally,
    so weekends and every NYSE holiday fall out of it without a second copy of
    the calendar living here.
    """
    return in_window("live_capture", datetime.datetime.now(CT))


def find_chrome():
    """The browser binary, or ``None``.

    Three names because the same browser answers to all of them depending on how
    it was installed (.deb, distro package, snap) -- the resolution
    ``tools/stream_wall.sh`` already does. ``chrome`` is appended for a
    non-Linux host, where this script is only ever run by hand.
    """
    for name in ("google-chrome", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _chrome_argv(chrome, url, png, profile):
    """The command line, split out so a test can read it without running it."""
    return [
        chrome,
        # Plain --headless, not --headless=new. Chrome 132 REMOVED the old
        # headless mode, so on anything current the plain flag already IS the
        # new one -- while an older packaged chromium understands only the plain
        # form and refuses to start on the suffix, with a message that names
        # neither the flag nor the reason.
        "--headless",
        "--disable-gpu",
        # Otherwise the shot carries a scrollbar down its right edge.
        "--hide-scrollbars",
        f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}",
        "--force-device-scale-factor=1",
        # A FRESH profile per run, for stream_wall.sh's reason: a --user-data-dir
        # that was not shut down cleanly makes Chrome open a restore-pages
        # bubble, and here that bubble would be IN the picture.
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--virtual-time-budget={SETTLE_MS}",
        f"--screenshot={png}",
        url,
    ]


def _render_png(chrome, url, png, *, timeout=SCREEN_TIMEOUT_SEC):
    """Drive the browser. The ONE part of this file no test can execute."""
    with tempfile.TemporaryDirectory(prefix="live-shot-profile-") as profile:
        subprocess.run(_chrome_argv(chrome, url, png, profile),
                       check=True, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def capture_one(chrome, url, out):
    """Render ``url`` and publish it at ``out`` as WebP. Raises on failure.

    Published by RENAME. Caddy serves this directory literally, so a file being
    written is a file being served -- a visitor who arrives mid-encode would get
    a truncated image. The temp file is a SIBLING of the destination rather than
    a system temp path, because ``os.replace`` is only atomic within one
    filesystem.
    """
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="live-shot-") as tmp:
        png = pathlib.Path(tmp) / "shot.png"
        _render_png(chrome, url, png)
        if not png.is_file() or png.stat().st_size == 0:
            # Chrome can exit 0 having written nothing. Encoding that would
            # replace a good thumbnail with a broken one, which is worse than
            # the stale tile it was.
            raise RuntimeError(f"{url}: chrome wrote no screenshot")

        part = out.with_name(out.name + ".part")
        try:
            with Image.open(png) as im:
                im.convert("RGB").save(part, "WEBP", quality=WEBP_QUALITY, method=6)
            os.replace(part, out)
        finally:
            part.unlink(missing_ok=True)


def main(argv=None):
    """``argv=None`` means NO ARGUMENTS, not ``sys.argv``.

    ``__main__`` passes ``sys.argv[1:]`` explicitly. Letting argparse fall back
    to ``sys.argv`` would make ``main()`` inside a test parse pytest's own
    command line and exit 2.
    """
    ap = argparse.ArgumentParser(description="Capture the public live screens.")
    ap.add_argument("--force", action="store_true",
                    help="capture even outside [windows.live_capture]")
    args = ap.parse_args([] if argv is None else argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.force and not _in_window():
        log.info("outside the live-capture window (weekend, holiday, or "
                 "off-hours) - standing down")
        return 0

    chrome = find_chrome()
    if chrome is None:
        log.error("no chrome/chromium on PATH - cannot capture the live screens")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    shots = targets()
    failed = []
    for url, out in shots:
        try:
            capture_one(chrome, url, out)
        except Exception as exc:  # noqa: BLE001 - one bad screen is not the run
            # A missing tile is a gap in a menu; a run that aborts on the second
            # screen is twelve stale ones. Logged so the gap is explainable.
            log.warning("capture failed for %s (%s): %s", out.name, url, exc)
            failed.append(out.name)

    if failed:
        log.warning("%d of %d screens did not capture: %s",
                    len(failed), len(shots), ", ".join(failed))
    else:
        log.info("captured %d screens into %s", len(shots), OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
