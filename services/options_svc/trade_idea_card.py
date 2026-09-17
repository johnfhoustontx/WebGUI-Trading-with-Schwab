"""Draw the hourly trade idea as a branded PNG, with Pillow.

The third consumer of :mod:`card_kit`, and the first meant for a PUBLIC feed, so
two things differ from the snapshot and briefing cards:

* **It is drawn at 2x natively** rather than drawn small and upscaled. Those cards
  are read once on a phone; this one is reposted, and an upscaled LANCZOS image
  goes soft exactly where a social feed zooms in -- the payoff curve and the
  strike figures.
* **It carries the brand lockup**: THE FLIP mark and the NEURAL / STRIKE wordmark,
  drawn from the same geometry and the same two hexes as
  ``webgui/static/img/neuralstrike-mark.svg`` and ``config/theme.toml`` [brand].
  They are literals here for the reason card_kit gives: Tier 2 does not read the
  web GUI's theme, and a post that fails because a colour file moved would be a
  poor trade. ⚠ If the mark or the brand hexes change, this copy does not follow.

The face is Inter, the public site's own font (``deploy/site/assets``), a variable
font whose weights are selected by name. A host without it falls back to
card_kit's system fonts -- an ugly card beats no post.

CONTRACT: **never raises.** A card that cannot be drawn returns None, and the push
falls back to its text caption.
"""
import datetime as _dt
import functools
import io
import logging
import pathlib

from services.options_svc import card_kit as K
from services.options_svc import trade_idea as T

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1200, 675          # 16:9 -- what Discord, Telegram and X preview uncropped
SCALE = 2
PAD = 44

BRAND_TITLE = (238, 241, 246)      # [brand].a_from, the mark's chevrons
BRAND_ACCENT = (107, 134, 255)     # [brand].b_from, the mark's rule
POS = K.TONES["pos"]
NEG = K.TONES["neg"]
FLAT = K.TONES["flat"]
GRADE_TONE = {"Strong": POS, "Good": BRAND_ACCENT, "Marginal": (224, 183, 78)}
BIAS_TONE = {"bullish": POS, "bearish": NEG}

_INTER = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "site" / "assets" / "inter-latin.woff2"
_WEIGHT_NAMES = {"regular": b"Regular", "medium": b"Medium", "semibold": b"SemiBold",
                 "bold": b"Bold", "extrabold": b"ExtraBold"}


@functools.lru_cache(maxsize=64)
def font(size, weight="regular"):
    """Inter at ``size`` layout px (scaled), or card_kit's fallback."""
    px = int(round(size * SCALE))
    try:
        from PIL import ImageFont
        if _INTER.exists():
            f = ImageFont.truetype(str(_INTER), px)
            try:
                f.set_variation_by_name(_WEIGHT_NAMES.get(weight, b"Regular"))
            except Exception:  # noqa: BLE001 -- a static build: one weight will do
                pass
            return f
    except Exception:  # noqa: BLE001 -- fall through to the system face
        log.debug("Inter load failed", exc_info=True)
    return K._font(px, "bold" if weight in ("semibold", "bold", "extrabold") else "regular")


def _s(v):
    return int(round(v * SCALE))


def _box(*xy):
    return tuple(_s(v) for v in xy)


class _Canvas:
    """Layout-px drawing over a SCALE-x image, so every call reads in the 1200 grid."""

    def __init__(self, im):
        from PIL import ImageDraw
        self.im = im
        self.d = ImageDraw.Draw(im)

    def text(self, x, y, s, size, weight="regular", fill=K.TEXT, anchor="la", spacing=0.0):
        f = font(size, weight)
        if not spacing:
            self.d.text((_s(x), _s(y)), s, font=f, fill=fill, anchor=anchor)
            return self.width(s, size, weight)
        # Tracked text: draw per character so capitals breathe like the app's lockup.
        cx = x
        for ch in s:
            self.d.text((_s(cx), _s(y)), ch, font=f, fill=fill, anchor="la")
            cx += self.width(ch, size, weight) + spacing
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

    def pill(self, x, y, s, size, fg, weight="bold", pad_x=9, h=24, spacing=1.2):
        w = self.width(s, size, weight, spacing) + 2 * pad_x
        bg = tuple(int(c * 0.18 + b * 0.82) for c, b in zip(fg, K.CARD))
        self.rrect(x, y, x + w, y + h, h / 2, fill=bg, outline=_mix(fg, K.CARD, 0.45))
        self.text(x + pad_x, y + (h - size) / 2 - size * 0.12, s, size, weight, fg,
                  spacing=spacing)
        return w


def _mix(a, b, t):
    """``t`` of colour ``a`` over ``b``."""
    return tuple(int(round(x * t + y * (1 - t))) for x, y in zip(a, b))


def _mark(c, x, y, size):
    """THE FLIP, from neuralstrike-mark.svg's 64-unit geometry."""
    k = size / 64.0
    c.rrect(x + 5 * k, y + 30.75 * k, x + 59 * k, y + 33.25 * k, 1.25 * k, fill=BRAND_ACCENT)
    c.line([(x + 20 * k, y + 11 * k), (x + 32 * k, y + 23 * k), (x + 44 * k, y + 11 * k)],
           BRAND_TITLE, 4.6 * k)
    c.line([(x + 20 * k, y + 53 * k), (x + 32 * k, y + 41 * k), (x + 44 * k, y + 53 * k)],
           BRAND_TITLE, 6.2 * k)


def _header(c, now):
    _mark(c, PAD, 30, 34)
    x = PAD + 46
    x += c.text(x, 38, "NEURAL", 17, "extrabold", BRAND_TITLE, spacing=2.4) + 2.4
    c.text(x, 38, "STRIKE", 17, "extrabold", BRAND_ACCENT, spacing=2.4)
    right = f"TRADE IDEA  ·  {now:%a %b} {now.day}  ·  {now:%H:%M} CT".upper()
    c.text(WIDTH - PAD - c.width(right, 13, "semibold", 1.6), 41, right, 13, "semibold",
           K.EYEBROW, spacing=1.6)
    c.line([(PAD, 86), (WIDTH - PAD, 86)], K.EDGE, 1)


def _left(c, idea):
    x, y = PAD, 110
    sym_w = c.text(x, y - 6, str(idea["symbol"]), 60, "extrabold", K.TITLE)
    if idea.get("spot") is not None:
        c.text(x + sym_w + 16, y + 34, f"${idea['spot']:,.2f}", 20, "medium", K.MUTED)
    y += 76
    lw = c.text(x, y, idea["label"], 24, "semibold", K.TEXT)
    bias = str(idea.get("bias") or "neutral")
    c.pill(x + lw + 14, y + 3, bias.upper(), 11, BIAS_TONE.get(bias, FLAT))

    # ── legs ────────────────────────────────────────────────────────────────
    y += 50
    c.text(x, y, "LEGS", 12, "bold", K.EYEBROW, spacing=1.8)
    dte = idea.get("dte")
    exp_note = T.expiry_text(idea["expiration"]) + (f" · {dte} DTE" if dte is not None else "")
    c.text(x + 470 - c.width(exp_note, 13, "medium"), y, exp_note, 13, "medium", K.MUTED)
    y += 24
    for lg in idea["legs"][:4]:
        c.rrect(x, y, x + 470, y + 38, 8, fill=K.CARD, outline=K.EDGE)
        long_ = lg["side"] == "long"
        tag = "BUY" if long_ else "SELL"
        tone = POS if long_ else NEG
        c.rrect(x + 10, y + 8, x + 62, y + 30, 11, fill=_mix(tone, K.CARD, 0.18),
                outline=_mix(tone, K.CARD, 0.45))
        c.text(x + 36, y + 19, tag, 11, "bold", tone, anchor="mm")
        c.text(x + 76, y + 9, f"{lg['qty']}×", 17, "medium", K.MUTED)
        c.text(x + 108, y + 8, T.strike_text(lg["strike"]), 19, "bold", K.TITLE)
        c.text(x + 210, y + 9, lg["kind"].upper(), 17, "semibold", K.TEXT)
        exp = T.expiry_text(lg["expiration"])
        c.text(x + 460 - c.width(exp, 15), y + 11, exp, 15, "regular", K.MUTED)
        y += 44
    return y


def _grade(c, idea, x, y, w):
    tone = GRADE_TONE.get(idea.get("grade"), FLAT)
    c.rrect(x, y, x + w, y + 86, 12, fill=_mix(tone, K.CARD, 0.10), outline=_mix(tone, K.CARD, 0.5))
    c.text(x + 18, y + 14, "GRADE", 12, "bold", K.EYEBROW, spacing=1.8)
    c.text(x + 18, y + 34, str(idea.get("grade") or "-"), 34, "extrabold", tone)
    if idea.get("score") is not None:
        s = f"{idea['score']:.0f}"
        sw = c.width(s, 34, "extrabold")
        c.text(x + w - 18 - sw - c.width(" / 100", 15, "medium"), y + 34, s, 34, "extrabold", K.TITLE)
        c.text(x + w - 18 - c.width(" / 100", 15, "medium"), y + 51, " / 100", 15, "medium", K.MUTED)


def _stats(c, idea, y):
    be = idea.get("breakevens") or []
    cells = [
        ("MAX RISK", T.money(idea["max_loss"]), NEG),
        ("MAX PROFIT", T.money(idea["max_profit"]), POS),
        ("PROBABILITY OF PROFIT", f"{idea['pop_pct']:.0f}%", K.TITLE),
        ("BREAKEVEN", " / ".join(T.strike_text(b) for b in be[:2]) or "-", K.TITLE),
    ]
    gap = 14
    w = (WIDTH - 2 * PAD - gap * 3) / 4
    for i, (label, value, tone) in enumerate(cells):
        x = PAD + i * (w + gap)
        c.rrect(x, y, x + w, y + 84, 12, fill=K.CARD, outline=K.EDGE)
        c.text(x + 18, y + 15, label, 11, "bold", K.EYEBROW, spacing=1.6)
        size = 30 if c.width(value, 30, "bold") < w - 36 else 22
        c.text(x + 18, y + 38 + (30 - size) * 0.5, value, size, "bold", tone)
    c.text(PAD, y + 92, "Per contract, at expiration, after commissions.", 12, "regular", K.MUTED)


def chart_window(idea):
    """``(lo, hi)`` price range for the payoff chart: every strike, the spot and the
    breakevens in view, with room either side so the shape reads."""
    marks = [lg["strike"] for lg in idea["legs"]] + list(idea.get("breakevens") or [])
    if idea.get("spot") is not None:
        marks.append(idea["spot"])
    lo, hi = min(marks), max(marks)
    centre = idea["spot"] if idea.get("spot") is not None else (lo + hi) / 2
    half = max(centre - lo, hi - centre, (idea.get("em") or 0.0) * 1.5, centre * 0.01)
    half *= 1.35
    return max(0.0, centre - half), centre + half


def _chart(c, idea, x0, y0, x1, y1):
    from PIL import Image, ImageDraw

    c.rrect(x0, y0, x1, y1, 14, fill=K.CARD, outline=K.EDGE)
    c.text(x0 + 20, y0 + 16, "PAYOFF AT EXPIRATION", 12, "bold", K.EYEBROW, spacing=1.8)

    px0, px1, py0, py1 = x0 + 24, x1 - 24, y0 + 60, y1 - 44
    lo, hi = chart_window(idea)
    n = 240
    prices = [lo + (hi - lo) * i / n for i in range(n + 1)]
    vals = [T.payoff(idea["legs"], idea["entry_cash"], p) for p in prices]
    vmax, vmin = max(vals + [0.0]), min(vals + [0.0])
    span = (vmax - vmin) or 1.0
    vmax += span * 0.12
    vmin -= span * 0.12

    def X(p):
        return px0 + (px1 - px0) * (p - lo) / ((hi - lo) or 1.0)

    def Y(v):
        return py0 + (py1 - py0) * (vmax - v) / (vmax - vmin)

    zero = Y(0.0)
    pts = [(X(p), Y(v)) for p, v in zip(prices, vals)]

    # Fills: an RGBA overlay per sign, clipped at the zero line, composited once.
    overlay = Image.new("RGBA", c.im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for tone, keep in ((POS, lambda v: v > 0), (NEG, lambda v: v < 0)):
        poly = []
        for (p, v), (xx, yy) in zip(zip(prices, vals), pts):
            poly.append((xx, yy if keep(v) else zero))
        poly = [(px0, zero)] + poly + [(px1, zero)]
        od.polygon([(_s(a), _s(b)) for a, b in poly], fill=tone + (46,))
    c.im.paste(Image.alpha_composite(c.im.convert("RGBA"), overlay).convert("RGB"))
    c.d = ImageDraw.Draw(c.im)

    c.line([(px0, zero), (px1, zero)], K.EDGE, 1.2)
    # Line coloured by sign, split at each crossing so no segment straddles zero.
    for (p0, v0), (p1, v1) in zip(zip(prices, vals), zip(prices[1:], vals[1:])):
        if v0 * v1 < 0:
            pm = p0 + (p1 - p0) * (-v0) / (v1 - v0)
            c.line([(X(p0), Y(v0)), (X(pm), zero)], POS if v0 > 0 else NEG, 3)
            c.line([(X(pm), zero), (X(p1), Y(v1))], POS if v1 > 0 else NEG, 3)
        else:
            tone = POS if (v0 + v1) > 0 else NEG if (v0 + v1) < 0 else FLAT
            c.line([(X(p0), Y(v0)), (X(p1), Y(v1))], tone, 3)

    spot = idea.get("spot")
    if spot is not None and lo <= spot <= hi:
        sx = X(spot)
        yy = py0 - 6
        while yy < py1:
            c.line([(sx, yy), (sx, min(yy + 6, py1))], K.MUTED, 1.2)
            yy += 11
        label = f"Spot {T.strike_text(spot)}"
        lw = c.width(label, 12, "semibold")
        lx = min(max(sx - lw / 2, px0), px1 - lw)
        c.text(lx, y1 - 34, label, 12, "semibold", K.TEXT)

    for b in idea.get("breakevens") or []:
        if lo <= b <= hi:
            bx = X(b)
            c.d.ellipse(_box(bx - 4.5, zero - 4.5, bx + 4.5, zero + 4.5), fill=K.TITLE,
                        outline=K.CARD, width=_s(1.5))

    # Only the zero line is labelled. The drawn window's own extremes are NOT the
    # trade's max profit or loss (a long put's floor sits far off the left edge),
    # and a corner label reading +$10,758 beside a MAX PROFIT tile reading $91,484
    # looks like the card contradicting itself.
    c.text(px0 + 2, zero - 18, "$0", 11, "semibold", K.MUTED)


def _render(idea, now, footer):
    from PIL import Image

    im = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), K.BG)
    c = _Canvas(im)
    _header(c, now)
    _left(c, idea)
    _chart(c, idea, 560, 106, WIDTH - PAD, 420)
    _stats(c, idea, 518)
    _grade(c, idea, 560, 426, WIDTH - PAD - 560)
    if footer:
        c.text(PAD, HEIGHT - 34, footer, 13, "semibold", K.MUTED, spacing=0.6)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_trade_idea_png(idea, *, now=None, footer="neuralstrike.co"):
    """PNG bytes for one normalized idea, or None. Never raises."""
    try:
        now = now or _dt.datetime.now()
        return _render(idea, now, footer)
    except Exception as exc:  # noqa: BLE001 -- best-effort on the hourly tick
        log.warning("trade idea card render failed: %s", exc, exc_info=True)
        return None
