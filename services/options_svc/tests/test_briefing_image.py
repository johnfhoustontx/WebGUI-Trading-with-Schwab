"""Tests for the headless-browser HTML→PNG briefing renderer.

NOTHING here launches a real browser: `find_browser` and `subprocess.run` are
monkeypatched, and the fake run writes a real (tiny) PNG with PIL so the crop
path is exercised on genuine image bytes rather than a mock.
"""
import io
import subprocess

import pytest
from PIL import Image

from services.options_svc import briefing_image as bi

BG = (12, 15, 21)          # the briefing doc's page background (#0c0f15)
FG = (240, 240, 240)


def _png(width: int, height: int, content_rows: int) -> bytes:
    """A PNG whose top `content_rows` rows carry content; the rest is pure BG."""
    im = Image.new("RGB", (width, height), BG)
    for y in range(content_rows):
        for x in range(width):
            im.putpixel((x, y), FG)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _size(png: bytes):
    with Image.open(io.BytesIO(png)) as im:
        return im.size


def _screenshot_path(cmd) -> str:
    for a in cmd:
        if a.startswith("--screenshot="):
            return a.split("=", 1)[1]
    raise AssertionError(f"no --screenshot= in {cmd}")


def _html_path(cmd) -> str:
    return cmd[-1]


@pytest.fixture
def fake_browser(monkeypatch):
    """Pretend a browser exists; record every subprocess.run call."""
    monkeypatch.setattr(bi, "find_browser", lambda: r"C:\fake\chrome.exe")
    calls = []
    monkeypatch.setattr(bi.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))
    return calls


def _writing_run(png: bytes, rc: int = 0, calls=None):
    """A subprocess.run stand-in that writes `png` to the --screenshot path."""
    class _P:
        returncode = rc
        stdout = b""
        stderr = b""

    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        if rc == 0:
            with open(_screenshot_path(cmd), "wb") as fh:
                fh.write(png)
        return _P()
    return run


# ── happy path ───────────────────────────────────────────────────────────────

def test_render_returns_cropped_png_bytes(monkeypatch):
    monkeypatch.setattr(bi, "find_browser", lambda: r"C:\fake\chrome.exe")
    monkeypatch.setattr(bi.subprocess, "run", _writing_run(_png(40, 400, 100)))
    out = bi.render_html_png("<html>hi</html>")
    assert isinstance(out, bytes) and out[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = _size(out)
    # width untouched; height cropped to the last content row + the pad margin
    assert w == 40
    assert h == 100 + bi._CROP_PAD
    assert h < 400


def test_render_command_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(bi, "find_browser", lambda: r"C:\fake\chrome.exe")
    monkeypatch.setattr(bi.subprocess, "run", _writing_run(_png(20, 200, 50), calls=calls))
    bi.render_html_png("<html>x</html>", width=900, scale=2, max_height=5200, timeout=90)
    cmd, kw = calls[0]
    assert cmd[0] == r"C:\fake\chrome.exe"
    assert "--headless=new" in cmd
    assert "--disable-gpu" in cmd and "--hide-scrollbars" in cmd
    assert "--force-device-scale-factor=2" in cmd
    assert "--window-size=900,5200" in cmd
    assert _screenshot_path(cmd).endswith(".png")
    assert _html_path(cmd).startswith("file:")
    assert kw["timeout"] == 90


def test_render_writes_html_as_utf8(monkeypatch):
    """The briefing caption/doc carries '·' and '→'; a cp1252 write would explode."""
    seen = {}

    def run(cmd, **kw):
        from urllib.parse import unquote, urlparse
        path = unquote(urlparse(_html_path(cmd)).path).lstrip("/")
        seen["html"] = open(path, encoding="utf-8").read()
        with open(_screenshot_path(cmd), "wb") as fh:
            fh.write(_png(10, 100, 20))

        class _P:
            returncode = 0
            stdout = stderr = b""
        return _P()

    monkeypatch.setattr(bi, "find_browser", lambda: r"C:\fake\chrome.exe")
    monkeypatch.setattr(bi.subprocess, "run", run)
    bi.render_html_png("<html>Gamma · bias → up</html>")
    assert "Gamma · bias → up" in seen["html"]


def test_render_cleans_up_its_temp_dir(monkeypatch):
    import os
    seen = {}

    def run(cmd, **kw):
        seen["dir"] = os.path.dirname(_screenshot_path(cmd))
        with open(_screenshot_path(cmd), "wb") as fh:
            fh.write(_png(10, 100, 20))

        class _P:
            returncode = 0
            stdout = stderr = b""
        return _P()

    monkeypatch.setattr(bi, "find_browser", lambda: r"C:\fake\chrome.exe")
    monkeypatch.setattr(bi.subprocess, "run", run)
    assert bi.render_html_png("<html>x</html>") is not None
    assert not os.path.exists(seen["dir"])


# ── failure paths — all must degrade to None, never raise ────────────────────

def test_render_returns_none_without_a_browser(monkeypatch):
    monkeypatch.setattr(bi, "find_browser", lambda: None)

    def boom(*a, **k):
        raise AssertionError("must not shell out without a browser")
    monkeypatch.setattr(bi.subprocess, "run", boom)
    assert bi.render_html_png("<html>x</html>") is None


def test_render_returns_none_on_nonzero_rc(fake_browser, monkeypatch):
    monkeypatch.setattr(bi.subprocess, "run", _writing_run(b"", rc=3))
    assert bi.render_html_png("<html>x</html>") is None


def test_render_returns_none_on_timeout(fake_browser, monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
    monkeypatch.setattr(bi.subprocess, "run", boom)
    assert bi.render_html_png("<html>x</html>") is None


def test_render_returns_none_when_output_missing(fake_browser, monkeypatch):
    class _P:
        returncode = 0
        stdout = stderr = b""
    monkeypatch.setattr(bi.subprocess, "run", lambda cmd, **kw: _P())  # writes nothing
    assert bi.render_html_png("<html>x</html>") is None


def test_render_never_raises(fake_browser, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("OSError-ish")
    monkeypatch.setattr(bi.subprocess, "run", boom)
    assert bi.render_html_png("<html>x</html>") is None


def test_render_returns_uncropped_png_when_crop_fails(monkeypatch):
    """A crop failure must not lose the screenshot — an uncropped image still reads."""
    raw = _png(20, 300, 80)
    monkeypatch.setattr(bi, "find_browser", lambda: r"C:\fake\chrome.exe")
    monkeypatch.setattr(bi.subprocess, "run", _writing_run(raw))
    monkeypatch.setattr(bi, "_crop_uniform_bottom", lambda *a, **k: None)
    assert bi.render_html_png("<html>x</html>") == raw


# ── browser discovery ────────────────────────────────────────────────────────

def test_find_browser_prefers_env_override(tmp_path, monkeypatch):
    exe = tmp_path / "my-chrome.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv(bi._BROWSER_ENV, str(exe))
    assert bi.find_browser() == str(exe)


def test_find_browser_falls_back_to_known_path(tmp_path, monkeypatch):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    monkeypatch.delenv(bi._BROWSER_ENV, raising=False)
    monkeypatch.setattr(bi, "_KNOWN_BROWSERS", (str(tmp_path / "nope.exe"), str(exe)))
    assert bi.find_browser() == str(exe)


def test_find_browser_falls_back_to_path_lookup(monkeypatch):
    monkeypatch.delenv(bi._BROWSER_ENV, raising=False)
    monkeypatch.setattr(bi, "_KNOWN_BROWSERS", ())
    monkeypatch.setattr(bi.shutil, "which",
                        lambda n: "/usr/bin/msedge" if n == "msedge" else None)
    assert bi.find_browser() == "/usr/bin/msedge"


def test_find_browser_none_when_nothing_installed(monkeypatch):
    monkeypatch.delenv(bi._BROWSER_ENV, raising=False)
    monkeypatch.setattr(bi, "_KNOWN_BROWSERS", ())
    monkeypatch.setattr(bi.shutil, "which", lambda n: None)
    assert bi.find_browser() is None


# ── crop ─────────────────────────────────────────────────────────────────────

def test_crop_trims_the_uniform_tail(monkeypatch):
    out = bi._crop_uniform_bottom(_png(30, 500, 120))
    assert _size(out) == (30, 120 + bi._CROP_PAD)


def test_crop_returns_none_for_an_all_background_image():
    assert bi._crop_uniform_bottom(_png(30, 300, 0)) is None


def test_crop_returns_none_when_content_reaches_the_bottom():
    """Nothing to trim -> None, so the caller keeps the original bytes."""
    assert bi._crop_uniform_bottom(_png(30, 200, 200)) is None


def test_crop_returns_none_on_garbage_input():
    assert bi._crop_uniform_bottom(b"not a png") is None
