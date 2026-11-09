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

The card is 1200 x 630 layout px and is built in three columns. Instagram's profile
grid crops a post to its centre strip (``card_draw.GRID_STRIP``), so the MIDDLE
column holds what must read on its own there -- ticker, strategy, payoff, grade --
and the two WINGS hold the figures, which the feed, the site, X, Discord and
Telegram show whole.

The face is Inter, the public site's own font (``deploy/site/assets``), a variable
font whose weights are selected by name. A host without it falls back to
Pillow's built-in default font (``shared.card_draw.font``) -- an ugly card beats no post.

CONTRACT: **never raises.** A card that cannot be drawn returns None, and the push
falls back to its text caption.
"""
import datetime as _dt
import logging

from services.options_svc import card_kit as K
from services.options_svc import trade_idea as T
from shared import card_draw as D
from shared.card_draw import SCALE, Canvas, _box, _s  # noqa: F401 -- SCALE is read by report_card and the tests

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1200, 630          # 40:21; the middle 472 px survive Instagram's 3:4 grid crop
PAD = 44

BRAND_TITLE = (238, 241, 246)      # [brand].a_from, the mark's chevrons
BRAND_ACCENT = (107, 134, 255)     # [brand].b_from, the mark's rule
POS = K.TONES["pos"]
NEG = K.TONES["neg"]
FLAT = K.TONES["flat"]
GRADE_TONE = {"Strong": POS, "Good": BRAND_ACCENT, "Marginal": (224, 183, 78)}
BIAS_TONE = {"bullish": POS, "bearish": NEG}

# What the card draws and nothing else: the site stores exactly these beside the JPEG.
CARD_FIELDS = ("symbol", "type", "label", "bias", "legs", "expiration", "dte", "spot",
               "grade", "score", "pop_pct", "entry_cash", "max_profit", "max_loss",
               "breakevens", "em")
_LEG_FIELDS = ("kind", "side", "strike", "expiration", "qty")

# Layout px of the three columns; the middle one sits inside the grid strip.
MID = (384, 816)
LEFT = (PAD, 344)
RIGHT = (856, WIDTH - PAD)
CX = WIDTH / 2


def card_facts(idea):
    """The fields the card draws, as plain data. ``render_trade_idea_png`` of this is
    byte-identical to its render of ``idea`` itself."""
    out = {k: idea.get(k) for k in CARD_FIELDS}
    out["legs"] = [{k: lg[k] for k in _LEG_FIELDS if k in lg}
                   for lg in (idea.get("legs") or [])]
    return out


def _header(c, now, label="TRADE IDEA"):
    """The lockup at the left, ``label`` centred in brand blue and the date at the
    right. ``label`` lets the market report card share it."""
    D.mark(c, PAD, 34, 30, rule=BRAND_ACCENT, chevrons=BRAND_TITLE)
    x = PAD + 42
    x += c.text(x, 40, "NEURAL", 17, "semibold", BRAND_TITLE, spacing=3.4) + 3.4
    c.text(x, 40, "STRIKE", 17, "bold", BRAND_ACCENT, spacing=3.4)
    lab = label.upper()
    c.text(CX - c.width(lab, 13, "bold", 2.0) / 2, 40, lab, 13, "bold", BRAND_ACCENT,
           spacing=2.0)
    when = f"{now:%a %b} {now.day}  ·  {now:%H:%M} CT".upper()
    c.text(WIDTH - PAD - c.width(when, 12, "semibold", 1.6), 42, when, 12, "semibold",
           K.EYEBROW, spacing=1.6)


def _fit_size(c, s, size, weight, max_w, floor=12):
    while size > floor and c.width(s, size, weight) > max_w:
        size -= 1
    return size


def _tile(c, x0, y0, x1, y1, label, value, tone, size=44):
    """A labelled figure tile: the label small and tracked, the value large."""
    c.rrect(x0, y0, x1, y1, 10, fill=K.CARD, outline=K.EDGE)
    c.text(x0 + 18, y0 + 20, label, 11, "bold", K.EYEBROW, spacing=1.8)
    size = _fit_size(c, value, size, "bold", (x1 - x0) - 36)
    c.text(x0 + 18, y0 + (y1 - y0) * 0.5 + size * 0.62 + 4, value, size, "bold", tone,
           anchor="ls")


def _middle_head(c, idea):
    """Ticker, ``<label> · $<spot>`` and the bias pill, centred in the strip."""
    width = MID[1] - MID[0]
    sym = str(idea["symbol"])
    size = _fit_size(c, sym, 96, "extrabold", width)
    c.text(CX, 82 + size * 0.86, sym, size, "extrabold", K.TITLE, anchor="ms")

    label = str(idea["label"])
    spot = f"${idea['spot']:,.2f}" if idea.get("spot") is not None else ""
    sep = "  ·  " if spot else ""
    size = 26
    while size > 14 and c.width(label + sep + spot, size, "medium") > width:
        size -= 1
    x = CX - c.width(label + sep + spot, size, "medium") / 2
    base = 186 + size * 0.97
    x += c.text(x, base, label, size, "medium", K.TEXT, anchor="ls")
    if spot:
        x += c.text(x, base, sep, size, "medium", K.MUTED, anchor="ls")
        c.text(x, base, spot, size, "medium", K.MUTED, anchor="ls")

    bias = str(idea.get("bias") or "neutral").upper()
    pw = c.width(bias, 12, "bold", 1.8) + 28
    c.pill(CX - pw / 2, 226, bias, 12, BIAS_TONE.get(bias.lower(), FLAT), K.BG, pad_x=14,
           spacing=1.8)


def _left(c, idea):
    """The legs, the expiry line under them, and the breakeven tile."""
    x0, x1 = LEFT
    c.text(x0, 104, "LEGS", 11, "bold", K.EYEBROW, spacing=1.8)
    y = 132
    for lg in idea["legs"][:4]:
        c.rrect(x0, y, x1, y + 38, 8, fill=K.CARD, outline=K.EDGE)
        long_ = lg["side"] == "long"
        tone = POS if long_ else NEG
        pw = c.pill(x0 + 10, y + 7, "BUY" if long_ else "SELL", 10, tone, K.CARD, pad_x=9,
                    h=24, spacing=1.0)
        sx = x0 + 10 + pw + 12
        sx += c.text(sx, y + 25.5, f"{lg['qty']}×", 16, "medium", K.MUTED, anchor="ls") + 10
        c.text(sx, y + 26, T.strike_text(lg["strike"]), 19, "bold", K.TITLE, anchor="ls")
        kind = lg["kind"].upper()
        c.text(x1 - 12 - c.width(kind, 15, "semibold", 1.0), y + 12, kind, 15, "semibold",
               K.TEXT, spacing=1.0)
        y += 46
    dte = idea.get("dte")
    note = T.expiry_text(idea["expiration"]) + (f" · {dte} DTE" if dte is not None else "")
    c.text(x0, y + 4, note, 14, "medium", K.MUTED)

    be = idea.get("breakevens") or []
    value = " / ".join(T.strike_text(b) for b in be[:2]) or "-"
    c.rrect(x0, 440, x1, 532, 10, fill=K.CARD, outline=K.EDGE)
    c.text(x0 + 16, 456, "BREAKEVEN", 11, "bold", K.EYEBROW, spacing=1.8)
    size = _fit_size(c, value, 26, "bold", (x1 - x0) - 32)
    c.text(x0 + 16, 512, value, size, "bold", K.TITLE, anchor="ls")


def _stats(c, idea):
    """The right wing: max risk, max profit, probability of profit."""
    x0, x1 = RIGHT
    pop = idea.get("pop_pct")
    _tile(c, x0, 104, x1, 234, "MAX RISK", T.money(idea["max_loss"]), NEG)
    _tile(c, x0, 250, x1, 380, "MAX PROFIT", T.money(idea["max_profit"]), POS)
    _tile(c, x0, 396, x1, 532, "PROBABILITY OF PROFIT",
          f"{pop:.0f}%" if pop is not None else "-", K.TITLE)


def _grade(c, idea):
    x0, x1 = MID
    y0, y1 = 460, 544
    tone = GRADE_TONE.get(idea.get("grade"), FLAT)
    c.rrect(x0, y0, x1, y1, 10, fill=D.mix(tone, K.CARD, 0.10),
            outline=D.mix(tone, K.CARD, 0.5))
    c.text(x0 + 18, y0 + 14, "GRADE", 11, "bold", K.EYEBROW, spacing=1.8)
    c.text(x0 + 18, y0 + 66, str(idea.get("grade") or "-"), 38, "extrabold", tone,
           anchor="ls")
    if idea.get("score") is not None:
        s = f"{idea['score']:.0f}"
        tail = " / 100"
        tw = c.width(tail, 15, "medium")
        c.text(x1 - 18 - tw, y0 + 66, tail, 15, "medium", K.MUTED, anchor="ls")
        c.text(x1 - 18 - tw - c.width(s, 38, "extrabold"), y0 + 66, s, 38, "extrabold",
               K.TITLE, anchor="ls")


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

    c.rrect(x0, y0, x1, y1, 10, fill=K.CARD, outline=K.EDGE)
    c.text(x0 + 16, y0 + 12, "PAYOFF AT EXPIRATION", 11, "bold", K.EYEBROW, spacing=1.8)

    px0, px1, py0, py1 = x0 + 20, x1 - 20, y0 + 40, y1 - 34
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
        lw = c.width(label, 11, "semibold")
        lx = min(max(sx - lw / 2, px0), px1 - lw)
        c.text(lx, y1 - 26, label, 11, "semibold", K.TEXT)

    for b in idea.get("breakevens") or []:
        if lo <= b <= hi:
            bx = X(b)
            c.d.ellipse(_box(bx - 4.5, zero - 4.5, bx + 4.5, zero + 4.5), fill=K.TITLE,
                        outline=K.CARD, width=_s(1.5))

    # Only the zero line is labelled. The drawn window's own extremes are NOT the
    # trade's max profit or loss (a long put's floor sits far off the left edge),
    # and a corner label reading +$10,758 beside a MAX PROFIT tile reading $91,484
    # looks like the card contradicting itself.
    c.text(px0 + 2, zero - 16, "$0", 10, "semibold", K.MUTED)


def layout(idea, now, footer="neuralstrike.co"):
    """Draw the card and return the canvas, so a test reads ``drawn``."""
    c = Canvas(D.new_image(K.BG, WIDTH, HEIGHT))
    _header(c, now)
    _middle_head(c, idea)
    _chart(c, idea, MID[0], 268, MID[1], 448)
    _grade(c, idea)
    _left(c, idea)
    _stats(c, idea)
    c.text(PAD, 572, "Per contract, at expiration, after commissions.", 13, "regular",
           K.MUTED)
    if footer:
        c.text(WIDTH - PAD - c.width(footer, 13, "semibold", 0.6), 572, footer, 13,
               "semibold", K.MUTED, spacing=0.6)
    return c


def _render(idea, now, footer):
    return D.png_bytes(layout(idea, now, footer).im)


def render_trade_idea_png(idea, *, now=None, footer="neuralstrike.co"):
    """PNG bytes for one normalized idea, or None. Never raises."""
    try:
        now = now or _dt.datetime.now()
        return _render(idea, now, footer)
    except Exception as exc:  # noqa: BLE001 -- best-effort on the hourly tick
        log.warning("trade idea card render failed: %s", exc, exc_info=True)
        return None
