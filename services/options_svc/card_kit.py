"""Shared drawing primitives for the pushed PNG cards.

Extracted the moment a SECOND card needed them. This repo has scars from the
other choice -- ``_clamp`` duplicated nine times across scoring/, ``num`` seven
-- and a palette that drifts between two images pushed to the same chat is the
visual version of the same bug.

Everything here is generic: colour, fonts, wrapping, number formatting, a
rounded box. What a particular card MEANS -- what a bias is, what counts as a
mover -- stays with that card.
"""
import functools
import logging
import math
import os

log = logging.getLogger(__name__)

# ── palette ──────────────────────────────────────────────────────────────────
# Lifted from the app's dark-navy theme so a push looks like the product. Kept
# as literals rather than read from theme.toml: this is Tier 2, which must not
# import the webgui, and a push that fails because a colour file moved would be
# a poor trade for a shade of blue.
BG = (12, 20, 36)
CARD = (16, 26, 48)
EDGE = (33, 49, 82)
TITLE = (234, 240, 251)
TEXT = (205, 216, 238)
MUTED = (127, 141, 176)
EYEBROW = (135, 148, 180)

TONES = {
    "pos": (52, 211, 153),
    "neg": (248, 113, 113),
    "flat": (148, 163, 184),
}

_FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fontsrial.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fontsrialbd.ttf",
    ),
}


def hex_to_rgb(value, fallback=(0, 0, 0)):
    """'#12331f' -> (18, 51, 31). Never raises.

    Exists so the cards can reuse a palette that is already written as CSS hex
    in the HTML renderers -- market_snapshot's tile colours -- instead of a
    second copy of the same six shades in RGB."""
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return fallback
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _finite(v):
    """The value as a float, or None. Rejects NaN/inf and bool.

    bool is excluded deliberately: ``float(True)`` is 1.0, and a True that
    reaches a price field should read as absent, not as $1."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def fmt(v, places=2):
    """A price-ish number, thousands-separated, with trailing zeros dropped."""
    f = _finite(v)
    if f is None:
        return "-"
    if abs(f - round(f)) < 1e-9:
        return f"{int(round(f)):,}"
    return f"{f:,.{places}f}"


def fmt_pct(v):
    """A day move. Always signed -- the direction is the whole point."""
    f = _finite(v)
    if f is None:
        return "-"
    if abs(f) < 1e-9:
        return "0.00%"
    return f"{f:+.2f}%"


def _text_w(draw, s, font):
    return draw.textlength(s, font=font)


def wrap(text, font, max_w, draw=None):
    """Greedy word wrap to `max_w` layout px. Returns [] for empty input.

    A word wider than the box is emitted on its own line rather than dropped or
    looped over -- there is no break to find, and hunting for one is how a
    renderer hangs on a long ticker or a URL.
    """
    s = (text or "").strip()
    if not s:
        return []
    if draw is None:
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and _text_w(draw, trial, font) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


@functools.lru_cache(maxsize=64)
def _font(size, weight="regular"):
    """A TrueType font at `size`, or Pillow's bitmap default.

    Memoised because layout asks for the same handful of sizes dozens of times
    and each miss is a file open. Never returns None: an ugly card beats a
    crashed scheduler, and a host with no DejaVu is still a host that must push.
    """
    from PIL import ImageFont
    for path in _FONT_CANDIDATES.get(weight, ()):
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 -- a broken font file is not fatal
            log.debug("font load failed: %s", path, exc_info=True)
    return ImageFont.load_default()


def _rounded(draw, box, radius, fill, outline=None):
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)
    except Exception:  # noqa: BLE001 -- very old Pillow: square corners will do
        draw.rectangle(box, fill=fill, outline=outline)
