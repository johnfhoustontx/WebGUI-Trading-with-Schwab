"""``tools/capture_live_shots.py`` -- the thumbnails behind the public grid.

The same problem ``test_stream_wall.py`` has: the part that actually renders is
a headless Chrome subprocess pointed at a running live process, and there is
nothing there a unit test can execute. So the script is written as a PURE target
list, a PURE window gate and a thin subprocess call, and what is pinned here is
every decision that would otherwise fail silently and only in public --

* a capture list that drifts from what the site publishes (a screen with no
  tile, or a tile with no screen),
* a capture taken over the public origin, which would make the thumbnails
  depend on DNS, TLS and Caddy rather than on whether the screens render,
* a holiday exiting non-zero and leaving the unit ``failed``,
* one bad screen aborting the other thirteen,
* a half-written file being served, because it was written in place.
"""
import importlib.util
import pathlib

import pytest

import repo_paths
from tools import capture_live_shots as c

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "tools" / "capture_live_shots.py"


def _live_screens():
    """``webgui/live_screens.py``, loaded BY PATH rather than by import.

    ``webgui/`` holds top-level modules named ``main``, ``proxy``, ``auth`` and
    ``wall``; putting that directory on ``sys.path`` would do it for the whole
    pytest session (``tests deploy tools/tests`` run in one command), where it
    can shadow another module of the same name. Loading one file by its path
    costs nothing and reaches nothing else -- and it is a REAL import, so a
    syntax error in the table still fails here.
    """
    path = pathlib.Path(repo_paths.WEBGUI) / "live_screens.py"
    spec = importlib.util.spec_from_file_location("_live_screens_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the list is not a second copy of the published table --------------------

def test_it_captures_every_published_screen():
    """One source: the capture list IS live_screens.SCREENS, so a screen added
    to the site cannot be missing a thumbnail."""
    screens = _live_screens().SCREENS
    targets = c.targets()
    assert len(targets) == len(screens)
    for screen, (url, out) in zip(screens, targets):
        assert url.endswith(screen.route)
        assert out.name == f"{screen.slug}.webp"


def test_it_captures_from_loopback_never_the_public_host():
    """Capturing over the public origin would make the thumbnails depend on
    DNS, TLS and Caddy being healthy -- three things that have nothing to do
    with whether the screens render."""
    for url, _out in c.targets():
        assert url.startswith(f"http://127.0.0.1:{repo_paths.NICEGUI_LIVE_PORT}")
        assert repo_paths.LIVE_HOST not in url


def test_it_writes_into_the_directory_the_grid_reads():
    """``deploy/site/live/<slug>.webp`` -- inside the SERVED root, which is what
    makes a capture reach a visitor at all, and the reason the directory is
    gitignored rather than committed."""
    live_dir = pathlib.Path(repo_paths.SITE_ROOT) / "live"
    for _url, out in c.targets():
        assert out.parent == live_dir
        assert out.suffix == ".webp"


# --- the window gate ---------------------------------------------------------

def test_outside_the_window_it_stands_down_with_exit_zero(monkeypatch):
    """A holiday is a normal outcome. A non-zero exit would restart-storm into
    StartLimitBurst and leave the unit `failed`, a state someone has to clear by
    hand -- the same reasoning tools/stream_wall.sh records."""
    monkeypatch.setattr(c, "_in_window", lambda: False)
    assert c.main() == 0


def test_standing_down_captures_nothing(monkeypatch):
    """The other half of the same property, and the one a stray `return 0`
    would leave green: exit zero must mean it did not run, not that it ran and
    swallowed the result."""
    monkeypatch.setattr(c, "_in_window", lambda: False)
    monkeypatch.setattr(c, "find_chrome",
                        lambda: pytest.fail("looked for a browser after standing down"))
    assert c.main() == 0


def test_the_gate_asks_the_market_calendar_not_a_holiday_literal():
    """``in_window("live_capture", now)`` gates on ``is_trading_day``
    internally, so weekends and every NYSE holiday fall out of one call. A
    second calendar here would be a second thing to keep in step."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'in_window("live_capture"' in text
    assert "weekday()" not in text


def test_the_window_exists_in_the_config_the_gate_reads():
    """The gate names a window; a typo'd name would degrade to the built-in
    defaults and capture at hours nobody chose."""
    from shared import market_calendar as mc

    start, end = mc.window_bounds("live_capture")
    assert start < end
    assert "live_capture" in mc.load_config()["windows"], (
        "config/sessions.toml has no [windows.live_capture]; the gate would run "
        "on market_calendar's built-in fallback instead")


# --- failure is per screen, except for the one failure that is total ----------

def test_one_screen_failing_does_not_abort_the_rest(monkeypatch):
    """A missing tile is a gap in a menu. A dead timer is fourteen stale ones."""
    seen = []

    def _fake(chrome, url, out):
        seen.append(out.name)
        if len(seen) == 2:
            raise RuntimeError("chrome fell over")

    monkeypatch.setattr(c, "_in_window", lambda: True)
    monkeypatch.setattr(c, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(c, "capture_one", _fake)
    assert c.main() == 0
    assert len(seen) == len(c.targets())


def test_a_missing_browser_is_a_failure_not_a_silent_success(monkeypatch):
    """Unlike a holiday, this is not a normal outcome and does not pass on its
    own: every screen would fail identically, forever, and the grid would go
    stale behind a green timer. The capture unit is a Type=oneshot with NO
    Restart=, so a non-zero exit shows up in `systemctl --user --failed`
    instead of storming -- which is exactly the difference from stream_wall.sh.
    """
    monkeypatch.setattr(c, "_in_window", lambda: True)
    monkeypatch.setattr(c, "find_chrome", lambda: None)
    monkeypatch.setattr(c, "capture_one",
                        lambda *a, **k: pytest.fail("captured with no browser"))
    assert c.main() == 1


# --- publishing is atomic ----------------------------------------------------

def test_a_capture_is_published_by_rename_never_written_in_place(tmp_path,
                                                                monkeypatch):
    """Caddy serves this directory literally, so a file being written IS a file
    being served. The encode goes to a sibling temp name and `os.replace`s over
    the destination, which is atomic on the same filesystem -- hence the
    sibling, not a system temp dir on another one.
    """
    from PIL import Image

    src = tmp_path / "shot.png"
    Image.new("RGB", (64, 40), (12, 24, 48)).save(src)

    def _fake_render(chrome, url, png, **kw):
        png.write_bytes(src.read_bytes())

    monkeypatch.setattr(c, "_render_png", _fake_render)
    out = tmp_path / "live" / "desk.webp"
    out.parent.mkdir()
    c.capture_one("/usr/bin/google-chrome", "http://127.0.0.1:8501/desk", out)

    assert out.is_file()
    with Image.open(out) as im:
        assert im.format == "WEBP"
    assert not list(out.parent.glob("*.part")), "a temp file was left behind"


def test_an_empty_render_is_refused_rather_than_published(tmp_path, monkeypatch):
    """Chrome can exit 0 having written nothing. Encoding that would replace a
    good thumbnail with a broken one -- worse than the stale tile it was."""
    monkeypatch.setattr(c, "_render_png", lambda *a, **k: None)
    out = tmp_path / "desk.webp"
    with pytest.raises(RuntimeError):
        c.capture_one("/usr/bin/google-chrome", "http://127.0.0.1:8501/desk", out)
    assert not out.exists()


# --- the browser -------------------------------------------------------------

def test_it_looks_for_the_same_browsers_the_stream_script_does(monkeypatch):
    """One box, one browser, installed as a .deb, a distro package or a snap --
    which decides the name. `chrome` is added for a non-Linux host, where this
    script is run by hand and never by the timer."""
    names = []
    monkeypatch.setattr(c.shutil, "which", lambda n: names.append(n) or None)
    assert c.find_chrome() is None
    assert names[:2] == ["google-chrome", "chromium-browser"]
    assert "chromium" in names


def test_the_settle_delay_is_declared_not_left_to_luck():
    """These pages paint from Redis on a watcher tick, so a screenshot taken at
    load is a grid of skeletons -- which looks like a rendering bug and is a
    timing one."""
    assert c.SETTLE_MS >= 5000
    assert f"--virtual-time-budget={c.SETTLE_MS}" in " ".join(
        c._chrome_argv("/usr/bin/google-chrome", "http://127.0.0.1:8501/desk",
                       pathlib.Path("/tmp/x.png"), pathlib.Path("/tmp/profile")))


def test_each_run_gets_a_fresh_browser_profile():
    """A reused --user-data-dir that was not shut down cleanly makes Chrome
    open with a restore-pages bubble, which would be IN the screenshot."""
    argv = c._chrome_argv("/usr/bin/google-chrome", "http://127.0.0.1:8501/desk",
                          pathlib.Path("/tmp/x.png"), pathlib.Path("/tmp/profile"))
    assert any(a.startswith("--user-data-dir=") for a in argv)
    assert "--headless" in argv or any(a.startswith("--headless") for a in argv)
    assert argv[-1] == "http://127.0.0.1:8501/desk"


def test_it_does_not_import_the_app(monkeypatch):
    """It reads ONE pure table out of webgui/ and drives a browser. An import of
    the app itself would drag NiceGUI, the bus and the engines into a cron-shaped
    process for the sake of fourteen slugs."""
    text = SCRIPT.read_text(encoding="utf-8")
    for banned in ("import nicegui", "from nicegui", "import main", "shared.bus"):
        assert banned not in text, f"the capture script pulls in {banned}"
    assert "live_screens" in text


def test_the_shot_geometry_matches_the_aspect_the_grid_reserves():
    """The grid declares width/height on every tile so its boxes exist before
    the first capture does. A shot with a different aspect would letterbox into
    them the moment it arrived."""
    assert c.VIEWPORT_WIDTH / c.VIEWPORT_HEIGHT == pytest.approx(
        c.TILE_WIDTH / c.TILE_HEIGHT)
