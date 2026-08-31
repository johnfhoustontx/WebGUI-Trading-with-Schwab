"""Draw the Gamma-briefing card as a PNG, with Pillow, from the STRUCTURED data.

WHY NOT A BROWSER. ``briefing_image.render_html_png`` screenshots the briefing
HTML with headless Chrome. That worked on the Windows box, which had one. The
Linux host does not, and putting one there is the wrong trade:

  * ~350 MB of browser on a machine holding brokerage credentials;
  * on Ubuntu 24.04 ``chromium-browser`` is a SNAP whose confinement cannot read
    the temp ``file://`` the renderer hands it, so the obvious install fails in a
    way that reads as a rendering bug;
  * a Chrome launch costs seconds and screenshots an 1800x10400 window that then
    has to be cropped, versus tens of milliseconds to draw what we already know.

And the HTML is an unnecessary intermediate anyway: the briefing is STORED
structured (``gamma_briefings.analysis_json``), so the model's output can be
drawn directly. The HTML doc is still what the in-app /options/analyze page
serves -- this module does not replace it, it replaces the screenshot of it.

CONTRACT: **never raises.** This runs inside the always-on options_svc scheduler.
Any failure returns ``None`` and the caller pushes text instead, which is what
happened for free while there was no browser at all.

DESIGN: phone-first. The reader is looking at a chat client, so the card leads
with the headline and the levels that drive a decision, and puts prose last. It
is deliberately NOT a reproduction of the web infographic -- that document is
built to be scrolled, this one to be glanced at.
"""
import functools
import io
import logging
import math
import os

log = logging.getLogger(__name__)

# ── geometry ─────────────────────────────────────────────────────────────────
# WIDTH is layout px; SCALE is the device-pixel ratio, so the chat client's
# downscale lands on crisp text. Everything below is expressed in layout px and
# multiplied once, at the end.
WIDTH = 900
SCALE = 2
PAD = 32                    # page margin
GAP = 20                    # between blocks
RULE = 1                    # hairline

# ── palette ──────────────────────────────────────────────────────────────────
# Lifted from the app's dark-navy theme so the push looks like the product.
# Kept as literals rather than read from theme.toml: this is Tier 2, which must
# not import the webgui, and a push that fails because a colour file moved would
# be a poor trade for a shade of blue.
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
# A bias inside this band is not a direction. Matches the spirit of the regime
# module's deadband: never assert a lean the number does not support.
BIAS_DEADBAND = 5.0

_FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ),
}

SLOT_LABELS = {"premarket": "Premarket", "open": "After open",
               "midday": "Midday", "close": "EOD recap"}


# ── pure helpers ─────────────────────────────────────────────────────────────
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


def bias_tone(bias):
    """'pos' | 'neg' | 'flat' for a BIAS SCORE on the -100..+100 scale.

    A finite SET, chosen the way the web tier maps a state to a palette class,
    so no colour is ever computed from a number. Absence and a value inside the
    deadband are both 'flat': neither is evidence of a direction."""
    f = _finite(bias)
    if f is None or abs(f) <= BIAS_DEADBAND:
        return "flat"
    return "pos" if f > 0 else "neg"


def move_tone(pct):
    """'pos' | 'neg' | 'flat' for a PERCENTAGE DAY MOVE.

    Separate from :func:`bias_tone` on purpose, and the first draft proved why:
    it reused the bias deadband, and +-5 on a -100..+100 bias scale is a
    reasonable "no direction" band while 5% on a day move is enormous. Every
    normal mover -- +3.27%, -1.84% -- came out grey.

    A day move has no deadband beyond zero itself. The sign IS the information,
    at any magnitude the model bothered to list."""
    f = _finite(pct)
    if f is None or f == 0:
        return "flat"
    return "pos" if f > 0 else "neg"


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


def scope_from(payload):
    """The symbol scope, derived from the indices actually being drawn.

    Derived rather than passed in, so the header cannot claim a scope the card
    does not show -- if the model returns two indices instead of three, the
    caption follows. Falls back to the standing trio only when there is nothing
    to derive from."""
    syms = [str(r.get("symbol")).strip() for r in _rows(payload, "indices")
            if r.get("symbol")]
    return " / ".join(syms) if syms else "$SPX / SPY / QQQ"


def _rows(payload, key):
    """A list of dicts under `key`, defensively. Never raises on a foreign shape."""
    v = (payload or {}).get(key) if isinstance(payload, dict) else None
    if not isinstance(v, list):
        return []
    return [r for r in v if isinstance(r, dict)]


def _strs(payload, key, limit):
    v = (payload or {}).get(key) if isinstance(payload, dict) else None
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if isinstance(x, (str, int, float))][:limit]


# ── drawing ──────────────────────────────────────────────────────────────────
def _rounded(draw, box, radius, fill, outline=None):
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)
    except Exception:  # noqa: BLE001 -- very old Pillow: square corners will do
        draw.rectangle(box, fill=fill, outline=outline)


def _render(payload, slot, meta):
    """Two-pass draw: measure into a tall scratch, then crop to the ink.

    Measuring first would mean duplicating every layout rule in a sizing pass
    that could silently disagree with the drawing pass -- the classic way a card
    clips its last row. Drawing into slack and cropping to the recorded cursor
    keeps ONE source of truth for the geometry.
    """
    from PIL import Image, ImageDraw

    inner = WIDTH - 2 * PAD
    im = Image.new("RGB", (WIDTH, 4000), BG)
    d = ImageDraw.Draw(im)

    f_eyebrow = _font(15, "bold")
    f_h1 = _font(31, "bold")
    f_h2 = _font(19, "bold")
    f_body = _font(17)
    f_small = _font(15)
    f_num = _font(23, "bold")
    f_tiny = _font(13)

    y = PAD

    # ── header ───────────────────────────────────────────────────────────────
    slot_label = SLOT_LABELS.get(slot, (slot or "Briefing").title())
    scope = meta.get("symbol_scope") or scope_from(payload)
    d.text((PAD, y), "GAMMA ANALYSIS", font=f_eyebrow, fill=EYEBROW)
    right = f"{slot_label}  -  {scope}"
    d.text((WIDTH - PAD - _text_w(d, right, f_eyebrow), y), right,
           font=f_eyebrow, fill=EYEBROW)
    y += 26
    d.line((PAD, y, WIDTH - PAD, y), fill=EDGE, width=RULE)
    y += GAP

    # ── headline ─────────────────────────────────────────────────────────────
    for line in wrap((payload.get("headline") or "").strip(), f_h1, inner, d)[:3]:
        d.text((PAD, y), line, font=f_h1, fill=TITLE)
        y += 40
    y += 6

    # ── bias + regime ────────────────────────────────────────────────────────
    tone = TONES[bias_tone(payload.get("bias"))]
    label = str(payload.get("bias_label") or "").strip()
    bias_f = _finite(payload.get("bias"))
    chip = f"{bias_f:+.0f}" if bias_f is not None else "n/a"
    chip_w = _text_w(d, chip, f_h2) + 26
    _rounded(d, (PAD, y, PAD + chip_w, y + 34), 8, CARD, tone)
    d.text((PAD + 13, y + 7), chip, font=f_h2, fill=tone)
    x = PAD + chip_w + 14
    if label:
        d.text((x, y + 8), label, font=f_body, fill=tone)
        x += _text_w(d, label, f_body) + 14
    regime = str(payload.get("regime") or "").strip()
    if regime:
        for line in wrap(regime, f_small, WIDTH - PAD - x, d)[:2]:
            d.text((x, y + 10), line, font=f_small, fill=MUTED)
            break
    y += 34 + GAP

    # ── indices ──────────────────────────────────────────────────────────────
    METRICS = (("gamma_flip", "FLIP"), ("call_wall", "CALL WALL"),
               ("put_wall", "PUT WALL"), ("max_pain", "MAX PAIN"),
               ("expected_move", "EXP MOVE"), ("pc_ratio", "P/C"))
    for row in _rows(payload, "indices")[:4]:
        top = y
        y += 14
        sym = str(row.get("symbol") or "-")
        d.text((PAD + 16, y), sym, font=f_h2, fill=TITLE)
        spot = fmt(row.get("spot"))
        d.text((PAD + 16 + _text_w(d, sym, f_h2) + 14, y + 2), spot,
               font=f_body, fill=TEXT)
        y += 30

        col_w = (inner - 32) / len(METRICS)
        for i, (key, cap) in enumerate(METRICS):
            cx = PAD + 16 + i * col_w
            d.text((cx, y), cap, font=f_tiny, fill=EYEBROW)
            d.text((cx, y + 17), fmt(row.get(key)), font=f_num, fill=TEXT)
        y += 50

        note = str(row.get("note") or "").strip()
        for line in wrap(note, f_small, inner - 32, d)[:2]:
            d.text((PAD + 16, y), line, font=f_small, fill=MUTED)
            y += 20
        y += 12
        # Drawn last so the fill does not cover the text: outline only.
        _rounded(d, (PAD, top, WIDTH - PAD, y), 10, None, EDGE)
        y += GAP - 6

    # ── movers ───────────────────────────────────────────────────────────────
    movers = _rows(payload, "movers")[:6]
    if movers:
        d.text((PAD, y), "MOVERS", font=f_eyebrow, fill=EYEBROW)
        y += 22
        x = PAD
        for m in movers:
            sym = str(m.get("symbol") or "-")
            pct = fmt_pct(m.get("day_pct"))
            tone_m = TONES[move_tone(m.get("day_pct"))]
            txt = f"{sym}  {pct}"
            w = _text_w(d, txt, f_small) + 22
            if x + w > WIDTH - PAD:
                break
            _rounded(d, (x, y, x + w, y + 30), 8, CARD, EDGE)
            d.text((x + 11, y + 7), sym, font=f_small, fill=TEXT)
            d.text((x + 11 + _text_w(d, sym + "  ", f_small), y + 7), pct,
                   font=f_small, fill=tone_m)
            x += w + 8
        y += 30 + GAP

    # ── macro drivers ────────────────────────────────────────────────────────
    drivers = _strs(payload, "macro_drivers", 5)
    if drivers:
        d.text((PAD, y), "WHAT IS DRIVING IT", font=f_eyebrow, fill=EYEBROW)
        y += 24
        for item in drivers:
            d.ellipse((PAD + 3, y + 7, PAD + 9, y + 13), fill=EYEBROW)
            for j, line in enumerate(wrap(item, f_body, inner - 22, d)[:2]):
                d.text((PAD + 20, y), line, font=f_body, fill=TEXT)
                y += 23
            y += 5
        y += GAP - 10

    # ── narrative ────────────────────────────────────────────────────────────
    narrative = str(payload.get("narrative") or "").strip()
    if narrative:
        d.text((PAD, y), "THE TRADE", font=f_eyebrow, fill=EYEBROW)
        y += 24
        for line in wrap(narrative, f_body, inner, d)[:6]:
            d.text((PAD, y), line, font=f_body, fill=TEXT)
            y += 23
        y += GAP - 8

    # ── footer ───────────────────────────────────────────────────────────────
    d.line((PAD, y, WIDTH - PAD, y), fill=EDGE, width=RULE)
    y += 12
    stamp = " - ".join(x for x in (meta.get("generated_at_label"),
                                   meta.get("model")) if x)
    if stamp:
        d.text((PAD, y), stamp, font=f_tiny, fill=MUTED)
        y += 18
    y += PAD - 12

    im = im.crop((0, 0, WIDTH, min(y, im.height)))
    if SCALE != 1:
        im = im.resize((WIDTH * SCALE, im.height * SCALE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def render_briefing_png(payload, *, slot=None, meta=None):
    """PNG bytes for a structured briefing, or ``None``. Never raises.

    `payload` is ``gamma_briefings.analysis_json`` as a dict -- the same object
    the model produced. `meta` carries presentation-only extras (model name,
    generated-at label, symbol scope) that are not part of the analysis.
    """
    try:
        if not isinstance(payload, dict) or not payload:
            return None
        return _render(payload, slot, meta or payload.get("_meta") or {})
    except Exception as exc:  # noqa: BLE001 -- best-effort inside the scheduler
        log.warning("briefing card render failed: %s", exc, exc_info=True)
        return None
