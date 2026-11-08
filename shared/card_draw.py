"""The drawing kit the card renderers share: Inter, a layout-px canvas, THE FLIP.

Cards are laid out on a ``WIDTH`` x ``HEIGHT`` grid (1200 x 630) and drawn at
``SCALE`` x, so every call reads in layout px and the file comes out 2400 x 1260.
Drawn natively at 2x, not upscaled: a social feed zooms in exactly where an
upscaled image goes soft.

Moved out of ``services/options_svc/trade_idea_card.py`` so the Social Cards
renderer (blog and idea previews) and that card draw with ONE copy. Colours are
the caller's: nothing here reads a theme.

The face is Inter, the public site's own variable font, selected by weight
name. A host without it falls back to Pillow's built-in font -- an ugly card
beats no post, and ``font`` never raises.

Stdlib, ``repo_paths`` and (lazily, inside functions) Pillow only.
"""
import functools
import io
import logging

from repo_paths import REPO_ROOT

log = logging.getLogger(__name__)

SCALE = 2
WIDTH, HEIGHT = 1200, 630
# Instagram's profile grid crops a post to a centre strip; on the 1200-wide
# layout it keeps these two x values (layout px). Words that must survive the
# grid thumbnail sit inside it (``in_strip``).
GRID_STRIP = (363.75, 836.25)

_INTER = REPO_ROOT / "deploy" / "site" / "assets" / "inter-latin.woff2"
_WEIGHT_NAMES = {"regular": b"Regular", "medium": b"Medium", "semibold": b"SemiBold",
                 "bold": b"Bold", "extrabold": b"ExtraBold"}


def _s(v):
    return int(round(v * SCALE))


def _box(*xy):
    return tuple(_s(v) for v in xy)


@functools.lru_cache(maxsize=64)
def font(size, weight="regular"):
    """Inter at ``size`` layout px (scaled), or Pillow's default at that size."""
    from PIL import ImageFont
    px = int(round(size * SCALE))
    try:
        if _INTER.exists():
            f = ImageFont.truetype(str(_INTER), px)
            try:
                f.set_variation_by_name(_WEIGHT_NAMES.get(weight, b"Regular"))
            except Exception:  # noqa: BLE001 -- a static build: one weight will do
                pass
            return f
    except Exception:  # noqa: BLE001 -- fall through to the default face
        log.debug("Inter load failed", exc_info=True)
    return ImageFont.load_default(size=px)


def mix(a, b, t):
    """``t`` of colour ``a`` over ``b``."""
    return tuple(int(round(x * t + y * (1 - t))) for x, y in zip(a, b))


class Canvas:
    """Layout-px drawing over a SCALE-x image, so every call reads in the 1200 grid.

    ``drawn`` lists every ``text`` call as ``(s, x0, y0, x1, y1)`` in layout px,
    so a caller (and a test) can see where the words landed."""

    def __init__(self, im):
        from PIL import ImageDraw
        self.im = im
        self.d = ImageDraw.Draw(im)
        self.drawn = []

    def text(self, x, y, s, size, weight="regular", fill=(255, 255, 255), anchor="la",
             spacing=0.0):
        f = font(size, weight)
        if not spacing:
            self.d.text((_s(x), _s(y)), s, font=f, fill=fill, anchor=anchor)
            w = self.width(s, size, weight)
            try:
                l, t, r, b = self.d.textbbox((_s(x), _s(y)), s, font=f, anchor=anchor)
                self.drawn.append((s, l / SCALE, t / SCALE, r / SCALE, b / SCALE))
            except Exception:  # noqa: BLE001 -- a record is never worth a card
                self.drawn.append((s, x, y, x + w, y + size))
            return w
        # Tracked text: draw per character so capitals breathe like the app's lockup.
        cx = x
        for ch in s:
            self.d.text((_s(cx), _s(y)), ch, font=f, fill=fill, anchor="la")
            cx += self.width(ch, size, weight) + spacing
        self.drawn.append((s, x, y, cx - spacing, y + size))
        return cx - x - spacing

    def width(self, s, size, weight="regular", spacing=0.0):
        w = self.d.textlength(s, font=font(size, weight)) / SCALE
        return w + spacing * max(0, len(s) - 1)

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(_box(x0, y0, x1, y1), radius=_s(r), fill=fill,
                                 outline=outline, width=_s(width))

    def line(self, pts, fill, width=1):
        self.d.line([(_s(x), _s(y)) for x, y in pts], fill=fill, width=max(1, _s(width)),
                    joint="curve")

    def pill(self, x, y, s, size, fg, ground, weight="bold", pad_x=9, h=24, spacing=1.2):
        """A tinted capsule of ``s`` in ``fg`` over a ``ground`` colour; returns its width."""
        w = self.width(s, size, weight, spacing) + 2 * pad_x
        bg = tuple(int(c * 0.18 + b * 0.82) for c, b in zip(fg, ground))
        self.rrect(x, y, x + w, y + h, h / 2, fill=bg, outline=mix(fg, ground, 0.45))
        self.text(x + pad_x, y + (h - size) / 2 - size * 0.12, s, size, weight, fg,
                  spacing=spacing)
        return w


def mark(c, x, y, size, *, rule, chevrons):
    """THE FLIP, from neuralstrike-mark.svg's 64-unit geometry."""
    k = size / 64.0
    c.rrect(x + 5 * k, y + 30.75 * k, x + 59 * k, y + 33.25 * k, 1.25 * k, fill=rule)
    c.line([(x + 20 * k, y + 11 * k), (x + 32 * k, y + 23 * k), (x + 44 * k, y + 11 * k)],
           chevrons, 4.6 * k)
    c.line([(x + 20 * k, y + 53 * k), (x + 32 * k, y + 41 * k), (x + 44 * k, y + 53 * k)],
           chevrons, 6.2 * k)


def new_image(ground, width=WIDTH, height=HEIGHT):
    """A blank RGB image of ``width`` x ``height`` layout px, at SCALE x."""
    from PIL import Image
    return Image.new("RGB", (width * SCALE, height * SCALE), ground)


def png_bytes(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def jpeg_from_png(png, quality):
    """A progressive JPEG of ``png``'s bytes, or None when they will not decode."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            out = io.BytesIO()
            im.convert("RGB").save(out, format="JPEG", quality=int(quality),
                                   progressive=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001 -- a missing JPEG is the caller's fallback
        log.debug("jpeg conversion failed", exc_info=True)
        return None


def in_strip(x0, x1):
    """True when layout span ``x0``..``x1`` sits inside the Instagram grid strip."""
    return x0 >= GRID_STRIP[0] and x1 <= GRID_STRIP[1]
