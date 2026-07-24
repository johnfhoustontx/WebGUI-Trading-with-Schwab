"""Render the Gamma-briefing HTML document to a PNG via headless Chrome/Edge.

WHY an image at all: the briefing used to be pushed as a `.html` file attachment,
but **Discord auto-previews an .html attachment as syntax-highlighted raw source**
— the reader gets a wall of CSS above the file card, and the suppress-embeds flag
(`flags: 4`) does not suppress ATTACHMENT previews. A PNG renders inline in both
Telegram and Discord with no tap and no preview problem.

The HTML generation is unchanged (``compute._analyze_doc`` still builds the doc
and still serves the in-app ``/options/analyze`` page) — this module just turns
that same doc into the channel payload.

ZERO new dependencies, deliberately: Chrome (or Edge) is already installed on
this Windows box and PIL is already in the venv, so no playwright/imgkit/selenium.

Everything here is best-effort — it runs inside the always-on options_svc
scheduler, so ANY failure (no browser, non-zero rc, timeout, missing output,
PIL error) logs a warning and returns ``None`` rather than raising.
"""
import io
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)

# ── browser discovery ────────────────────────────────────────────────────────
_BROWSER_ENV = "BRIEFING_CHROME"        # explicit override wins over discovery
_KNOWN_BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)
_PATH_NAMES = ("chrome", "msedge")      # last resort: whatever is on PATH

# ── render geometry ──────────────────────────────────────────────────────────
# The doc is a fixed-width dark infographic. `width` is CSS px; `scale` is the
# device-pixel ratio (2 = retina-sharp text once the chat client downscales).
# `max_height` is the headless WINDOW height — it must exceed the tallest
# briefing, because the screenshot is the window, not the document. The excess is
# uniform background and gets cropped off below.
_WIDTH = 900
_SCALE = 2
_MAX_HEIGHT = 5200
_TIMEOUT_SEC = 90          # measured ~1.5s; the ceiling only guards a wedged run

# ── crop ─────────────────────────────────────────────────────────────────────
_CROP_TOLERANCE = 8        # per-channel delta from the page bg that counts as content
_CROP_PAD = 24             # device px of background kept below the last content row
_MIN_CROP_HEIGHT = 50      # anything shorter than this is degenerate -> don't crop


def find_browser() -> str | None:
    """Path to a headless-capable Chromium browser, or None if none is installed.

    Order: ``BRIEFING_CHROME`` env override → the two known Windows install paths
    → ``PATH``. A non-existent env path falls THROUGH to discovery (a typo should
    degrade to the working default, not to a confusing subprocess failure)."""
    env = os.environ.get(_BROWSER_ENV)
    if env:
        if os.path.exists(env):
            return env
        log.warning("%s=%s does not exist; falling back to browser discovery",
                    _BROWSER_ENV, env)
    for path in _KNOWN_BROWSERS:
        if os.path.exists(path):
            return path
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _last_content_row(im, bg, tolerance: int) -> int | None:
    """Bottom-most row index containing a pixel that differs from `bg`.

    Scans upward a ROW at a time via ``getextrema()`` (a C-level min/max per band)
    rather than per pixel — an 1800x10400 screenshot is ~19M pixels, which a
    Python-level loop would take minutes to walk."""
    w, h = im.size
    for y in range(h - 1, -1, -1):
        for band, (lo, hi) in enumerate(im.crop((0, y, w, y + 1)).getextrema()):
            if lo < bg[band] - tolerance or hi > bg[band] + tolerance:
                return y
    return None


def _crop_uniform_bottom(png: bytes, *, tolerance: int = _CROP_TOLERANCE,
                         pad: int = _CROP_PAD) -> bytes | None:
    """Trim the uniform page background off the bottom of a screenshot.

    Returns the cropped PNG, or ``None`` when there is nothing to gain (image is
    all background / content already reaches the bottom / the crop would be
    degenerate) or the math failed. The caller keeps the ORIGINAL bytes on None —
    an uncropped briefing still reads fine, a lost one does not."""
    try:
        # Imported lazily (inside the guard) so a missing/broken Pillow degrades
        # to "crop failed -> ship uncropped" rather than an ImportError that would
        # break push_notify's import of this module and with it the whole briefing.
        from PIL import Image
        with Image.open(io.BytesIO(png)) as opened:
            im = opened.convert("RGB")
            w, h = im.size
            bg = im.getpixel((0, h - 1))         # background sample: bottom-left
            last = _last_content_row(im, bg, tolerance)
            if last is None:                      # entirely background
                return None
            new_h = min(h, last + 1 + pad)
            if new_h >= h or new_h < _MIN_CROP_HEIGHT:
                return None
            buf = io.BytesIO()
            im.crop((0, 0, w, new_h)).save(buf, format="PNG")
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 — best-effort; caller falls back to raw
        log.warning("briefing image crop failed: %s", exc)
        return None


def render_html_png(html: str, *, width: int = _WIDTH, scale: int = _SCALE,
                    max_height: int = _MAX_HEIGHT,
                    timeout: int = _TIMEOUT_SEC) -> bytes | None:
    """Render an HTML document to PNG bytes with headless Chrome. Never raises.

    Writes `html` to a temp file, screenshots it into a temp PNG, reads the bytes
    back, and always removes the temp directory. The window-height slack is
    auto-cropped off the bottom. Returns ``None`` on any failure (logged)."""
    browser = find_browser()
    if not browser:
        log.warning("no Chrome/Edge found (set %s) — cannot render briefing image",
                    _BROWSER_ENV)
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="gamma-briefing-") as tmp:
            src = pathlib.Path(tmp) / "briefing.html"
            out = pathlib.Path(tmp) / "briefing.png"
            src.write_text(html, encoding="utf-8")
            proc = subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                 f"--force-device-scale-factor={scale}",
                 f"--window-size={width},{max_height}",
                 f"--screenshot={out}", src.as_uri()],
                capture_output=True, timeout=timeout,
            )
            if proc.returncode != 0:
                log.warning("briefing screenshot failed (rc=%s): %s",
                            proc.returncode, (proc.stderr or b"")[:300])
                return None
            if not out.exists():
                log.warning("briefing screenshot produced no output file")
                return None
            raw = out.read_bytes()
    except Exception as exc:  # noqa: BLE001 — best-effort inside the scheduler
        log.warning("briefing image render failed: %s", exc)
        return None
    return _crop_uniform_bottom(raw) or raw
