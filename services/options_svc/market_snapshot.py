"""Pure builders for the 30-min Market Snapshot infographic (options_svc).

Reads NOTHING — every function takes plain dicts/lists (the Redis payloads the
handler already fetched) and returns HTML/SVG strings. Deterministic + unit-tested.
The handler renders the doc to PNG via ``briefing_image.render_html_png`` and pushes
it via ``push_notify.send_market_snapshot``.

Two sections, built here and assembled by :func:`market_snapshot_doc`:

* the **Macro Board** grid (``dashboard_grid_html`` and friends), a mirror of
  ``webgui/pages/market.py``;
* the **Market Read**, which is the ``/sentiment`` **Market Regime Console**,
  mirrored in :mod:`services.options_svc.market_console`.

This file is an INDEPENDENT RENDERER — not a screenshot — so a page redesign
never breaks it and never updates it either. That is exactly how the Market Read
fell a design behind. ``market_console`` carries the whole mirroring contract and
names the Tier-1 function behind every decision; read its module docstring before
touching either side.
"""
import html as _html

from services.options_svc import market_console as MC


def _num(x):
    """``float(x)`` for an int/float OR a numeric string, else None (never raises).

    ``bool`` is excluded — ``True``/``False`` are ints but never a real score.
    (sentiment_svc publishes ``total_score`` as a formatted string, e.g. "7.80").
    """
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


_TILE_BG = {"risk_on_strong": "#12331f", "risk_on_mild": "#16261c",
            "risk_off_strong": "#331213", "risk_off_mild": "#2a1618",
            "flat": "#141b2c", "no_data": "#10141f"}
_TILE_FG = {"risk_on_strong": "#4ad07f", "risk_on_mild": "#4ad07f",
            "risk_off_strong": "#f07171", "risk_off_mild": "#f07171",
            "flat": "#cdd8ee", "no_data": "#7f8db0"}


def _fmt_change(tile):
    if tile.get("value_only"):
        return ""
    pct = tile.get("change_pct")
    if pct is None:
        return ""
    try:
        return f"{float(pct):+.2f}%"
    except (TypeError, ValueError):
        return ""


# ── Day / Week / Month horizon values ───────────────────────────────────────
# These feed the console's Sentiment and Trend METER STACKS (they fed the
# concentric rings the console replaced; the numbers are identical, only the
# drawing changed). MIRRORS webgui/pages/sentiment.trend_arcs / sentiment_arcs.
def _trend_arc_value(t):
    """A trend horizon's 0-100 value, or None when it carries no reading.

    Keys on CONFIDENCE, not on key presence: the service publishes a fully
    shaped ``score 50.0 / confidence 0.0`` dict after a fetch failure, so an
    absent-key guard misses it and paints a confident neutral 50. A horizon with
    no reading must render NO READ, never a fabricated number."""
    if not isinstance(t, dict):
        return None
    conf = _num(t.get("confidence"))
    if conf is not None and conf <= 0:
        return None
    return _num(t.get("score"))


def trend_arcs(derived):
    d = derived if isinstance(derived, dict) else {}
    return [{"value": _trend_arc_value(d.get("trend")), "caption": "DAY"},
            {"value": _trend_arc_value(d.get("trend_7d")), "caption": "WEEK"},
            {"value": _trend_arc_value(d.get("trend_30d_ago")), "caption": "MONTH"}]


WEEK_SNAPS = 5


def _snap_composite(s):
    if not isinstance(s, dict):
        return None
    return _num((s.get("composite") or {}).get("total_score"))


def _avg_or_none(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def sentiment_arcs(sentiment, snaps):
    """Day/Week/Month values for sentiment, on the 0-100 SCORE SCALE.

    The 0-10 composite is multiplied by 10 here and NOWHERE else, which is the
    fix for the scale bug the old push shipped: its ring centre read "5.0" (the
    0-10 composite) while its own WEEK/MONTH legend read 57 / 53 (0-100) —
    three numbers on one dial on two different scales. Every number the
    Sentiment card draws is now 0-100; the 0-10 composite survives only inside
    the bias pill, where it is labelled by the word beside it.

    Week is the last 5 scored sessions, Month the full stored history — the same
    two horizons ``/sentiment`` shows, and the reason the handler also reads
    ``cache:sentiment:history``."""
    snaps = [s for s in (snaps or []) if isinstance(s, dict)]
    day = _num((sentiment or {}).get("total_score"))
    if day is None and snaps:
        day = _snap_composite(snaps[-1])
    week = _avg_or_none([_snap_composite(s) for s in snaps[-WEEK_SNAPS:]])
    month = _avg_or_none([_snap_composite(s) for s in snaps])
    to100 = lambda v: None if v is None else max(0.0, min(100.0, v * 10.0))
    return [{"value": to100(day), "caption": "DAY"},
            {"value": to100(week), "caption": "WEEK"},
            {"value": to100(month), "caption": "MONTH"}]


def prev_total(snaps):
    """The prior scored session's 0-10 composite, for YESTERDAY / CHANGE.

    MIRRORS ``sentiment._apply``: the push always shows the LIVE composite as
    today, so the prior series is the whole backfill (every completed session),
    and "yesterday" is its last scored entry. ``> 0`` filters the unscored rows
    exactly as ``composite_series`` does. None when there is no prior session —
    which the Signals card renders as an em-dash rather than inventing a band."""
    scored = [v for v in (_snap_composite(s) for s in (snaps or []))
              if v is not None and v > 0]
    return scored[-1] if scored else None


# Short state words, one per trend horizon. MIRRORS sentiment._TREND_SHORT —
# covers BOTH the five-state intraday vocabulary and the older trend-band words
# the 30-day structural read still publishes.
_TREND_SHORT = {
    "bullish": "Bull", "lack_of_bullishness": "Weak Bull", "neutral": "Neutral",
    "lack_of_bearishness": "Resilient", "bearish": "Bear",
    "bull_trend": "BULL", "pullback_in_bull": "PULLBACK", "range": "RANGE",
    "bear_rally": "BEAR RALLY", "bear_trend": "BEAR"}


def console_context(trend, sentiment, regime, regime_hist, derived, snaps, *,
                    composite_at=None, now_utc=None, now_ct=None):
    """The ``ctx`` dict :func:`market_console.console_html` consumes.

    Deliberately the SAME SHAPE ``console_page.apply`` takes on Tier 1 (see the
    call in ``webgui/pages/sentiment._apply``), so the two assemblies stay
    comparable key-for-key and a field added to one has an obvious home in the
    other. Everything is derived from payloads the handler already reads."""
    d = derived if isinstance(derived, dict) else {}
    t = d.get("trend") or (trend if isinstance(trend, dict) else {}) or {}
    s = sentiment if isinstance(sentiment, dict) else {}
    total = _num(s.get("total_score"))
    # size/bias/signal arrive together or not at all — MIRRORS the page's
    # ``band_labels`` gate, so a cold cache shows three em-dashes rather than a
    # half-populated row.
    if d.get("size") is not None:
        bias_word, signal_word = d.get("bias") or "—", d.get("signal") or "—"
    else:
        bias_word, signal_word = "—", "—"
    return {
        "sent_arcs": sentiment_arcs(s, snaps),
        "trend_arcs": trend_arcs(d if d else {"trend": t}),
        "bias": s.get("bias"),
        "total": "—" if total is None else f"{total:.2f}",
        "confidence": _num(s.get("aggregate_confidence")),
        "trend_short": _TREND_SHORT.get(t.get("state"), ""),
        "trend_verdict": t.get("label"),
        "trend_guidance": t.get("description"),
        "signal_rows": MC.signal_rows(bias_word, signal_word, total,
                                      prev_total(snaps)),
        "velocity_values": (d.get("velocity") or {}).get("values"),
        "divergence_detail": d.get("divergence_detail"),
        "regime": regime or {},
        "regime_points": (regime_hist or {}).get("points") or [],
        "as_of": composite_at,
        "now_utc": now_utc,
        "now_ct": now_ct,
    }


# ── Macro Board dashboard grid ──────────────────────────────────────────────
def _wash(tile):
    """Magnitude-scaled background wash, matching the board's colour language:
    intensity tracks |%change| while the HUE tracks the polarity-aware
    ``color_state`` — so VIX up is red even though the number rose."""
    cs = tile.get("color_state", "no_data")
    base = {"up": "63,179,107", "down": "224,82,82"}.get(cs)
    if not base:
        return "#111a2c"
    pct = _num(tile.get("change_pct")) or 0.0
    a = min(0.30, abs(pct) / 3.0 * 0.30 + 0.05)
    return f"rgba({base},{a:.3f})"


def _skew_word(pct):
    """Signed skew % -> 'Call 31%' / 'Put 22%' / 'Even' / '—'. Mirrors
    ``webgui/pages/market._skew_word``."""
    p = _num(pct)
    if p is None:
        return "—"
    if abs(p) < 1:
        return "Even"
    return f"{'Call' if p > 0 else 'Put'} {abs(p):.0f}%"


def _tile_lines(t):
    """(value, change, subline) for one tile — mirrors the board's ``tile_text``
    + ``descriptor_line`` so the pushed image says the same thing the screen
    does. The special tiles matter: Net Prem and BIG10 carry NO ``last`` at all,
    so a naive read renders them blank (which is what the old snapshot did)."""
    if t.get("net_prem"):
        pct = t.get("skew_pct")
        net = _num(t.get("net_m"))
        chg = "" if net is None else f"{'+' if net >= 0 else '-'}${abs(net) / 1000.0:.2f}B"
        return (_skew_word(pct), chg, "DOLLAR-WEIGHTED CALL/PUT")
    if t.get("basket"):
        avg = _num(t.get("avg_pct"))
        head = "—" if avg is None else f"{avg:+.2f}%"
        return (head, str(t.get("breadth_text") or ""), _skew_word(t.get("prem_skew_pct")))
    last = t.get("last")
    if last is None:
        return ("—", "", _descriptor(t))
    last_s = f"{last:g}" if isinstance(last, (int, float)) else str(last)
    if t.get("value_only"):
        return (last_s, "", _descriptor(t))
    return (last_s, _fmt_change(t), _descriptor(t))


_DESC_MAX = 26


def _descriptor(t):
    if "prem_skew_pct" in t:
        return _skew_word(t.get("prem_skew_pct"))
    d = (t.get("description") or "").strip().upper()
    return d if len(d) <= _DESC_MAX else d[:_DESC_MAX - 1] + "…"


def dashboard_grid_html(categories):
    cats = [c for c in (categories or []) if isinstance(c, dict) and c.get("tiles")]
    if not cats:
        return '<div class="ms-empty">no data</div>'
    frames = []
    for c in cats:
        name = str(c.get("category", ""))
        tiles = []
        for t in c["tiles"]:
            if not isinstance(t, dict):
                continue
            fg = _TILE_FG.get(t.get("color_state", "no_data"), _TILE_FG["no_data"])
            last_s, chg_s, sub = _tile_lines(t)
            tiles.append(
                f'<div class="ms-tile" style="background:{_wash(t)}">'
                f'<div class="ms-sym">{_html.escape(str(t.get("display", "")))}</div>'
                f'<div class="ms-last">{_html.escape(last_s)}</div>'
                f'<div class="ms-chg" style="color:{fg}">{_html.escape(chg_s)}</div>'
                f'<div class="ms-sub">{_html.escape(str(sub))}</div></div>')
        # Width the frame to a whole number of tiles (max 4 across) instead of
        # letting it stretch. A stretched frame left dead space to the right of
        # its tiles and forced one frame per row, which nearly doubled the
        # image height — a real cost on a phone push.
        cols = max(1, min(len(tiles), _MS_MAX_COLS))
        w = cols * _MS_TILE_W + (cols - 1) * _MS_TILE_GAP + _MS_FRAME_PAD
        frames.append(
            f'<div class="ms-frame" style="--acc:{_accent(name)};width:{w}px">'
            f'<div class="ms-frame-h">{_html.escape(name)}</div>'
            f'<div class="ms-tiles">{"".join(tiles)}</div></div>')
    return f'<div class="ms-grid">{"".join(frames)}</div>'


_MS_TILE_W = 152      # must match .ms-tile width in _MS_CSS
_MS_TILE_GAP = 7      # .ms-tiles gap
_MS_FRAME_PAD = 24    # .ms-frame left+right padding + borders
_MS_MAX_COLS = 4      # frames wrap internally past this, so several fit per row


_ACCENTS = ("#35e0ff", "#00e5a0", "#e0c452", "#b98cff", "#ff7ad9", "#4f9cf0")


def _accent(name):
    """Stable per-category accent — deterministic on the NAME, so a category
    keeps its colour between snapshots even if the board's order changes."""
    return _ACCENTS[sum(map(ord, str(name))) % len(_ACCENTS)]


# The doc reuses the gamma-briefing CSS, whose `.ga` is capped at max-width:860px
# — sized for that report's single column. The board needs room for two 4-tile
# frames side by side, so the snapshot widens its own wrapper. _MS_CSS is emitted
# in the BODY, after the head stylesheet, so it wins on source order.
DOC_WIDTH = 1450          # CSS px handed to briefing_image.render_html_png
_MS_CSS = """
.ga{max-width:1380px}
.ms-grid{display:flex;flex-wrap:wrap;gap:11px;align-items:flex-start}
.ms-frame{position:relative;flex:0 0 auto;background:#101a30;border:1px solid #213152;
  padding:10px 11px 11px;
  clip-path:polygon(14px 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%,0 14px)}
.ms-frame::before{content:"";position:absolute;left:0;top:0;width:2px;height:100%;
  background:linear-gradient(180deg,var(--acc,#35e0ff),transparent 78%);opacity:.9}
.ms-frame-h{color:#8794b4;font-size:9.5px;text-transform:uppercase;
  letter-spacing:.24em;margin:0 0 9px 9px}
.ms-tiles{display:flex;flex-wrap:wrap;gap:7px}
/* every tile identical, matching the board's fixed 152x94 */
.ms-tile{position:relative;overflow:hidden;width:152px;height:94px;flex:0 0 152px;
  background:#111a2c;border:1px solid #1d2942;padding:9px 11px 8px;
  clip-path:polygon(0 0,100% 0,100% calc(100% - 8px),calc(100% - 8px) 100%,0 100%)}
.ms-sym{color:#8794b4;font-size:11px;font-weight:700;letter-spacing:.13em}
.ms-last{color:#e7edf8;font-size:17px;font-weight:500;margin-top:3px}
.ms-chg{font-size:10.5px;margin-top:2px}
.ms-sub{color:#7f8db0;font-size:9px;letter-spacing:.05em;margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ms-empty{color:#7f8db0;font-size:12px}
"""


def market_snapshot_doc(dashboard, trend, sentiment, regime, intraday, regime_hist,
                        *, subtitle="", derived=None, snaps=None,
                        composite_at=None, now_utc=None, now_ct=None):
    """The whole pushed document: Macro Board, then the Market Read console.

    ``intraday`` is accepted (and cached by the handler) but the console does not
    draw it: on ``/sentiment`` the intraday series lives BELOW the console in its
    own charts, and the Day/Week/Month meters plus the vs-WEEK / vs-MONTH deltas
    carry that story in the image. Kept in the signature because the caller
    passes it positionally and it is the natural home for it if the console ever
    grows one.

    ``composite_at`` feeds the DATA AS OF chip; ``now_utc`` / ``now_ct`` exist so
    tests can pin the chips without freezing the clock.
    """
    # Reuse the gamma-briefing dark aesthetic (_ANALYZE_CSS + .ga* structure) but
    # wrap it locally so the doc is titled "Market Snapshot", NOT "Gamma Analysis"
    # (compute._analyze_doc hardcodes the gamma title). Zero blast radius on gamma.
    from services.options_svc import compute  # lazy: reuse the dark doc CSS
    ctx = console_context(trend, sentiment, regime, regime_hist, derived, snaps,
                          composite_at=composite_at, now_utc=now_utc,
                          now_ct=now_ct)
    body = (
        f'<style>{_MS_CSS}{MC.CONSOLE_CSS}</style>'
        + dashboard_grid_html((dashboard or {}).get("categories") or [])
        # No separate <h2> here any more: the console carries its OWN header
        # (title + eyebrow + the SESSION / DATA AS OF chips), exactly as it does
        # on /sentiment, and stacking a gamma-style heading above it would give
        # the section two competing titles.
        + MC.console_html(ctx)
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Market Snapshot</title>'
        f'<style>{compute._ANALYZE_CSS}</style></head><body><div class="ga">'
        '<div class="ga-title">Market Snapshot</div>'
        f'<div class="ga-sub">{_html.escape(subtitle)}</div>'
        f'<div class="ga-body">{body}</div></div></body></html>'
    )
