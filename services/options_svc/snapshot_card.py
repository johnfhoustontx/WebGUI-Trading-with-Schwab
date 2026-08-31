"""Draw the 30-minute Market Snapshot as a PNG, with Pillow, from the payloads.

The sibling of :mod:`briefing_card`, for the same reason and against the same
gap: the snapshot shipped as a headless-Chrome screenshot of a 1450px HTML
infographic, there is no browser on the Linux host, and it has therefore been
pushing a bare text caption every half hour since the migration.

This one needed no data archaeology at all. ``push_notify.send_market_snapshot``
is already handed the structured payloads the handler read out of Redis --
``dashboard`` (14 categories of tiles), ``trend``, ``sentiment``, ``regime`` --
and ``market_snapshot`` only turns them into HTML. The HTML was the intermediate,
not the source.

TWO THINGS ARE BORROWED RATHER THAN RESTATED, both deliberately:

* the tile colours come from ``market_snapshot._TILE_BG``/``_TILE_FG``, the same
  vocabulary the HTML renderer uses. A second hand-written copy in RGB is how
  two images pushed to the same chat drift apart.
* the regime words come from ``market_console.REGIME_LABELS``. Those five
  display names are already mirrored across four tiers with a test guarding a
  fifth copy; this card must not become one.

CONTRACT: **never raises.** It runs on the 30-minute tick; a card that cannot be
drawn degrades to the text caption that was already the fallback.
"""
import io
import logging

from services.options_svc import market_console as MC
from services.options_svc import market_snapshot as MS
from services.options_svc.card_kit import (  # noqa: F401 -- re-exported
    BG, CARD, EDGE, EYEBROW, MUTED, TEXT, TITLE, TONES,
    _finite, _font, _rounded, _text_w, fmt, fmt_pct, hex_to_rgb, wrap,
)

log = logging.getLogger(__name__)

# Wider than the briefing card: the macro board is a GRID and squeezing 69 tiles
# into 900px would either shrink the type below reading size on a phone or push
# the card to twice the height. 1100 fits six chips per row at 15px type.
WIDTH = 1100
SCALE = 2
PAD = 30
GAP = 18

CHIP_W = 168
CHIP_H = 46
CHIP_GAP = 8

_UNKNOWN_TILE = "no_data"


def tile_bg(state):
    """Tile background for a ``color_state``, from the HTML renderer's palette."""
    key = state if state in MS._TILE_BG else _UNKNOWN_TILE
    return hex_to_rgb(MS._TILE_BG[key])


def tile_fg(state):
    key = state if state in MS._TILE_FG else _UNKNOWN_TILE
    return hex_to_rgb(MS._TILE_FG[key])


def chip_change(tile):
    """The change string for a tile, mirroring ``market_snapshot._fmt_change``.

    A ``value_only`` tile -- $TICK, $ADVN, Put/Call -- has a level but no
    meaningful day change, and the HTML renderer prints nothing there. A dash
    would claim "we could not get it", which is a different and wrong statement
    about a number that does not exist in the first place."""
    if not isinstance(tile, dict) or tile.get("value_only"):
        return ""
    return fmt_pct(tile.get("change_pct"))


def score_of(sentiment):
    """The composite score as a float, or None.

    ``sentiment_svc`` publishes ``total_score`` as a FORMATTED STRING ("3.87") --
    the reason ``market_snapshot._num`` exists. Treating it as a float would
    print a dash on every card, which is a silent failure of the kind this
    codebase keeps finding."""
    if not isinstance(sentiment, dict):
        return None
    return _finite(sentiment.get("total_score"))


def dominant_regime(regime):
    """``(key, display_label, share)`` for the heaviest membership.

    ``(None, "-", 0.0)`` when there is nothing to name -- absence must render as
    a dash, never as the first key in a dict."""
    memb = (regime or {}).get("memberships") if isinstance(regime, dict) else None
    if not isinstance(memb, dict) or not memb:
        return (None, "-", 0.0)
    best_k, best_v = None, None
    for k, v in memb.items():
        f = _finite(v)
        if f is None:
            continue
        if best_v is None or f > best_v:
            best_k, best_v = k, f
    if best_k is None:
        return (None, "-", 0.0)
    return (best_k, MC.REGIME_LABELS.get(best_k, str(best_k)), best_v)


def _categories(dashboard):
    cats = (dashboard or {}).get("categories") if isinstance(dashboard, dict) else None
    if not isinstance(cats, list):
        return []
    out = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        tiles = c.get("tiles")
        tiles = [t for t in tiles if isinstance(t, dict)] if isinstance(tiles, list) else []
        if tiles:
            out.append((str(c.get("category") or "-"), tiles))
    return out


def _headline_block(d, x, y, w, eyebrow, value, sub, note, tone=TEXT):
    """One of the three top stats. Returns the y the block ends at."""
    f_eyebrow = _font(14, "bold")
    f_val = _font(27, "bold")
    f_sub = _font(16)
    f_note = _font(13)
    top = y
    _rounded(d, (x, y, x + w, y + 118), 10, CARD, EDGE)
    d.text((x + 16, y + 14), eyebrow, font=f_eyebrow, fill=EYEBROW)
    d.text((x + 16, y + 36), value, font=f_val, fill=tone)
    if sub:
        d.text((x + 16, y + 70), sub, font=f_sub, fill=TEXT)
    if note:
        for line in wrap(note, f_note, w - 32, d)[:1]:
            d.text((x + 16, y + 94), line, font=f_note, fill=MUTED)
    return top + 118


def _render(dashboard, trend, sentiment, regime, slot, as_of):
    from PIL import Image, ImageDraw

    inner = WIDTH - 2 * PAD
    im = Image.new("RGB", (WIDTH, 4200), BG)
    d = ImageDraw.Draw(im)

    f_eyebrow = _font(15, "bold")
    f_cat = _font(15, "bold")
    f_sym = _font(15, "bold")
    f_pct = _font(16, "bold")
    f_tiny = _font(12)

    y = PAD
    d.text((PAD, y), "MARKET SNAPSHOT", font=f_eyebrow, fill=EYEBROW)
    right = " - ".join(x for x in (slot, as_of) if x)
    if right:
        d.text((WIDTH - PAD - _text_w(d, right, f_eyebrow), y), right,
               font=f_eyebrow, fill=EYEBROW)
    y += 26
    d.line((PAD, y, WIDTH - PAD, y), fill=EDGE, width=1)
    y += GAP

    # ── the three headline reads ─────────────────────────────────────────────
    trend = trend if isinstance(trend, dict) else {}
    tscore = _finite(trend.get("smoothed_score"))
    if tscore is None:
        tscore = _finite(trend.get("score"))
    # 50 is the neutral midpoint of the trend scale, not zero.
    ttone = TONES["flat"] if tscore is None else (
        TONES["pos"] if tscore > 53 else TONES["neg"] if tscore < 47 else TONES["flat"])

    sscore = score_of(sentiment)
    sentiment = sentiment if isinstance(sentiment, dict) else {}
    # The composite runs 0-10 and 5 is the middle.
    stone = TONES["flat"] if sscore is None else (
        TONES["pos"] if sscore > 5.5 else TONES["neg"] if sscore < 4.5 else TONES["flat"])

    _key, rlabel, rshare = dominant_regime(regime)
    col_w = (inner - 2 * GAP) / 3
    _headline_block(d, PAD, y, col_w, "TREND",
                    "-" if tscore is None else f"{tscore:.1f}",
                    str(trend.get("label") or "-"),
                    str(trend.get("description") or ""), ttone)
    _headline_block(d, PAD + col_w + GAP, y, col_w, "SENTIMENT",
                    "-" if sscore is None else f"{sscore:.2f} / 10",
                    str(sentiment.get("bias") or "-"),
                    f"size {sentiment.get('size_modifier') or '-'}", stone)
    y = _headline_block(d, PAD + 2 * (col_w + GAP), y, col_w, "REGIME",
                        rlabel,
                        f"{rshare * 100:.0f}% of the mix" if rshare else "",
                        MC.REGIME_NOTES.get(_key, "") if _key else "")
    y += GAP

    # ── macro board ──────────────────────────────────────────────────────────
    cats = _categories(dashboard)
    if cats:
        d.text((PAD, y), "MACRO BOARD", font=f_eyebrow, fill=EYEBROW)
        y += 24
    per_row = max(1, int((inner + CHIP_GAP) // (CHIP_W + CHIP_GAP)))
    for name, tiles in cats:
        d.text((PAD, y), name.upper(), font=f_cat, fill=MUTED)
        y += 20
        for i, t in enumerate(tiles):
            col = i % per_row
            if col == 0 and i:
                y += CHIP_H + CHIP_GAP
            x = PAD + col * (CHIP_W + CHIP_GAP)
            state = t.get("color_state")
            _rounded(d, (x, y, x + CHIP_W, y + CHIP_H), 7,
                     tile_bg(state), EDGE)
            d.text((x + 10, y + 6), str(t.get("display") or "-")[:12],
                   font=f_sym, fill=TEXT)
            pct = chip_change(t)
            if pct:
                d.text((x + CHIP_W - 10 - _text_w(d, pct, f_pct), y + 5), pct,
                       font=f_pct, fill=tile_fg(state))
            last = fmt(t.get("last"))
            d.text((x + 10, y + 26), last, font=f_tiny, fill=MUTED)
        y += CHIP_H + GAP

    d.line((PAD, y, WIDTH - PAD, y), fill=EDGE, width=1)
    y += 12
    if isinstance(dashboard, dict) and dashboard.get("proxy_up") is False:
        d.text((PAD, y), "proxy down - values may be stale", font=f_tiny,
               fill=TONES["neg"])
        y += 18
    y += PAD - 14

    im = im.crop((0, 0, WIDTH, min(y, im.height)))
    if SCALE != 1:
        im = im.resize((WIDTH * SCALE, im.height * SCALE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def render_snapshot_png(dashboard=None, trend=None, sentiment=None, regime=None,
                        *, slot="", as_of=""):
    """PNG bytes for the market snapshot, or ``None``. Never raises."""
    try:
        return _render(dashboard, trend, sentiment, regime, slot, as_of)
    except Exception as exc:  # noqa: BLE001 -- best-effort on the 30-min tick
        log.warning("snapshot card render failed: %s", exc, exc_info=True)
        return None
